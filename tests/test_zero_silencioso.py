"""Rede permanente contra o "zero silencioso" — a classe de bug que já apareceu
QUATRO vezes neste projeto: um filtro descarta tudo e ninguém percebe, porque
zero candidatas não é erro, é só um run vazio.

Histórico: `selection.category_ids` só reconhecia categorias da Shopee;
`validation.allowed_domains` não incluía os domínios do ML; `commission_pct`
fixo em 0.0 zerava o `ev_score` contra o piso de EV; e, por último, o portão
`discount_pct >= min_discount_pct` matava TODA oferta do ML — que nasce com
`discount_pct == 0` por construção (38 buscadas, 0 sobreviventes).

Por isso este teste NÃO usa fixtures: ele roda o `config.yaml` real e o
`data/meli_offers.json` real ponta a ponta (fetch_offers -> enrich_offers ->
filter_offers) e exige candidatas > 0. Se alguém reintroduzir um portão que
zere uma fonte inteira, a suíte quebra aqui.
"""

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from afiliado import pricing, selection
from afiliado.config import load_config
from afiliado.sources import meli as meli_mod
from afiliado.sources.meli import MeliSource
from afiliado.state import StateDB

RAIZ = Path(__file__).resolve().parents[1]
CONFIG_REAL = RAIZ / "config.yaml"
POOL_REAL = RAIZ / "data/meli_offers.json"


def _sem_rede(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"nenhuma chamada de rede é esperada aqui: {request.url}")


@pytest.fixture
def pool_no_prazo(monkeypatch) -> date:
    """Congela "hoje" na data de geração do pool real.

    `fetch_offers` descarta pool vencido; sem isso este teste passaria a
    falhar sozinho `valid_days` dias depois do último refresh do pool, e a
    rede contra o zero silencioso viraria ruído. O que ele protege é a régua,
    não a validade do arquivo."""
    gerado = date.fromisoformat(
        json.loads(POOL_REAL.read_text(encoding="utf-8"))["generated_at"])

    class _DataCongelada(date):
        @classmethod
        def today(cls) -> date:
            return gerado

    monkeypatch.setattr(meli_mod, "date", _DataCongelada)
    return gerado


def _meli_source(tmp_path) -> MeliSource:
    return MeliSource(
        "CID", "CSECRET",
        token_path=tmp_path / "meli_token.json",
        links_path=tmp_path / "meli_links.json",
        client=httpx.Client(transport=httpx.MockTransport(_sem_rede)),
    )


def test_meli_produz_candidatas_com_config_e_pool_reais(tmp_path, pool_no_prazo):
    cfg = load_config(CONFIG_REAL)
    src = _meli_source(tmp_path)

    offers = src.fetch_offers(cfg)
    assert offers, (
        f"o pool curado ({POOL_REAL}) não produziu nenhuma oferta — "
        f"aviso: {src.pool_warning}")

    db = StateDB(tmp_path / "s.db")
    pricing.record_observations(db, offers)
    offers = pricing.enrich_offers(offers, db, None, cfg)
    candidatas = selection.filter_offers(offers, db, cfg)

    assert len(candidatas) > 0, (
        f"{len(offers)} ofertas do ML entraram e ZERO sobreviveram ao filtro — "
        "é o zero silencioso de novo")
    db.close()


def test_toda_oferta_do_meli_nasce_com_referencia_e_piso(tmp_path, pool_no_prazo):
    cfg = load_config(CONFIG_REAL)
    offers = _meli_source(tmp_path).fetch_offers(cfg)
    assert all(o.price_ref_cents > 0 for o in offers)
    assert all(o.price_floor_cents > 0 for o in offers)


def test_desconto_do_vendedor_zerado_nao_derruba_mais_ninguem(tmp_path, pool_no_prazo):
    """O sintoma exato do bug: no ML `discount_pct` é 0 para todas as ofertas."""
    cfg = load_config(CONFIG_REAL)
    db = StateDB(tmp_path / "s.db")
    offers = _meli_source(tmp_path).fetch_offers(cfg)
    assert all(o.discount_pct == 0 for o in offers)
    offers = pricing.enrich_offers(offers, db, None, cfg)
    assert len(selection.filter_offers(offers, db, cfg)) > 0
    db.close()


def test_config_real_nao_tem_mais_portao_de_desconto():
    cfg = load_config(CONFIG_REAL)
    assert "min_discount_pct" not in cfg["selection"]
    assert "max_above_historic_min" not in (cfg.get("meli") or {})
    assert cfg["selection"]["max_above_ref"] >= 1.0


def test_oferta_sem_referencia_e_publicavel_e_o_texto_nao_alega_desconto(tmp_path):
    """Teste obrigatório 3, ponta a ponta com o config real: sem referência a
    oferta PASSA no filtro e na validação (é a decisão de volume máximo) e o
    texto não alega desconto nenhum."""
    from afiliado import message, validate
    from afiliado.models import CopyParts
    from tests.test_models import make_offer

    cfg = load_config(CONFIG_REAL)
    db = StateDB(tmp_path / "s.db")
    offer = make_offer(category="100630", price_original_cents=49999,
                       price_current_cents=24999, rating=4.8, sales=12000)
    assert offer.price_ref_cents == 0
    assert offer.discount_pct == 50          # o "de" do vendedor diz 50%...

    assert selection.filter_offers([offer], db, cfg) == [offer]
    validate.check_price(offer, cfg)         # não levanta

    copy = CopyParts(headline="Achado do dia", description="d", cta="c")
    texto = message.build_message(offer, copy, "https://shope.ee/x")
    assert "OFF" not in texto                # ...e o post não repete nada disso
    assert "<s>" not in texto
    assert "R$ 499,99" not in texto
    assert "R$ 249,99" in texto
    db.close()

"""Rede permanente contra o "zero silencioso" — a classe de bug que já apareceu
QUATRO vezes neste projeto: um filtro descarta tudo e ninguém percebe, porque
zero candidatas não é erro, é só um run vazio.

Histórico: `selection.category_ids` só reconhecia categorias da Shopee;
`validation.allowed_domains` não incluía os domínios do ML; `commission_pct`
fixo em 0.0 zerava o `ev_score` contra o piso de EV; e, por último, o portão
`discount_pct >= min_discount_pct` matava TODA oferta do ML — que nasce com
`discount_pct == 0` por construção (38 buscadas, 0 sobreviventes).

Por isso este teste roda o `config.yaml` REAL ponta a ponta (fetch_offers ->
enrich_offers -> filter_offers) e exige candidatas > 0. Se alguém
reintroduzir um portão que zere uma fonte inteira, a suíte quebra aqui.

Pool: as asserções da régua rodam sobre `tests/fixtures/meli_offers_v2.json`
(3 entradas no formato da fase 5B, com ids/títulos/buy boxes reais e números
de preço sintéticos), para que a suíte não dependa do conteúdo do pool de
produção. O pool REAL (`data/meli_offers.json`) tem o seu próprio teste
ponta a ponta mais abaixo — é ele que quebra se um refresh gerar um arquivo
que o leitor rejeita em silêncio.
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
POOL = RAIZ / "tests/fixtures/meli_offers_v2.json"


def _sem_rede(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"nenhuma chamada de rede é esperada aqui: {request.url}")


def _dia_do_pool(raw: dict) -> date:
    """O "hoje" em que este pool é contemporâneo: a MAIS RECENTE entre a
    geração e as datas de verificação do buy box.

    Congelar só em `generated_at` parecia bastar até o passo semanal do buy box
    rodar: ele carimba `buy_box_checked_at` de hoje sem regerar o arquivo (é o
    procedimento documentado — o vencedor do buy box muda mais rápido que
    título e histórico). Com o relógio parado na geração, essas datas ficam no
    FUTURO e o leitor rejeita a entrada por data inválida — a suíte acusava um
    pool saudável. A régua é o que este teste protege; a idade do arquivo,
    não."""
    datas = [date.fromisoformat(raw["generated_at"])]
    datas += [date.fromisoformat(o["buy_box_checked_at"]) for o in raw["offers"]
              if o.get("buy_box_checked_at")]
    return max(datas)


def test_o_dia_do_pool_acompanha_a_checagem_semanal_do_buy_box():
    """O passo semanal carimba `buy_box_checked_at` sem regerar o arquivo.

    Aconteceu de verdade em 2026-08-28: 31 entradas renovadas contra um
    `generated_at` de 2026-08-26 fizeram o leitor recusar TODAS por "data do
    buy box inválida", e a suíte apontou para o dado quando o errado era o
    relógio do teste."""
    raw = {"generated_at": "2026-08-26",
           "offers": [{"buy_box_checked_at": "2026-08-28"},
                      {"buy_box_checked_at": "2026-08-27"},
                      {}]}
    assert _dia_do_pool(raw) == date(2026, 8, 28)
    # Sem checagem posterior, o dia continua sendo o da geração.
    assert _dia_do_pool({"generated_at": "2026-08-26", "offers": [{}]}) == date(2026, 8, 26)


def _congela(monkeypatch, dia: date) -> date:
    class _DataCongelada(date):
        @classmethod
        def today(cls) -> date:
            return dia

    monkeypatch.setattr(meli_mod, "date", _DataCongelada)
    return dia


@pytest.fixture
def pool_no_prazo(monkeypatch) -> date:
    """Congela "hoje" no dia em que o pool de fixture é contemporâneo.

    `fetch_offers` descarta pool vencido; sem isso este teste passaria a
    falhar sozinho `valid_days` dias depois do último refresh do pool, e a
    rede contra o zero silencioso viraria ruído. O que ele protege é a régua,
    não a validade do arquivo."""
    return _congela(monkeypatch,
                    _dia_do_pool(json.loads(POOL.read_text(encoding="utf-8"))))


def _cfg() -> dict:
    cfg = load_config(CONFIG_REAL)
    cfg["meli"]["offers_path"] = str(POOL)
    return cfg


def _meli_source(tmp_path) -> MeliSource:
    return MeliSource(
        "CID", "CSECRET",
        token_path=tmp_path / "meli_token.json",
        links_path=tmp_path / "meli_links.json",
        client=httpx.Client(transport=httpx.MockTransport(_sem_rede)),
    )


def test_meli_produz_candidatas_com_config_real_e_pool_no_formato_novo(tmp_path, pool_no_prazo):
    cfg = _cfg()
    src = _meli_source(tmp_path)

    offers = src.fetch_offers(cfg)
    assert offers, (
        f"o pool ({POOL}) não produziu nenhuma oferta — aviso: {src.pool_warning}")
    assert src.pool_warning is None, src.pool_warning     # as 3 entradas passam na validação

    db = StateDB(tmp_path / "s.db")
    offers = pricing.enrich_offers(offers, db, None, cfg)
    candidatas = selection.filter_offers(offers, db, cfg)

    assert len(candidatas) > 0, (
        f"{len(offers)} ofertas do ML entraram e ZERO sobreviveram ao filtro — "
        "é o zero silencioso de novo")
    db.close()


def test_toda_oferta_do_meli_nasce_com_referencia_p25_janelas_e_piso(tmp_path, pool_no_prazo):
    offers = _meli_source(tmp_path).fetch_offers(_cfg())
    assert all(o.price_ref_cents > 0 for o in offers)
    assert all(o.price_p25_cents > 0 for o in offers)
    assert all(o.price_window_days >= pricing.MIN_WINDOW_DAYS for o in offers)
    assert all(o.price_floor_cents > 0 and o.price_floor_window_days > 0 for o in offers)


def test_desconto_do_vendedor_zerado_nao_derruba_mais_ninguem(tmp_path, pool_no_prazo):
    """O sintoma exato do bug: no ML `discount_pct` é 0 para todas as ofertas."""
    cfg = _cfg()
    db = StateDB(tmp_path / "s.db")
    offers = _meli_source(tmp_path).fetch_offers(cfg)
    assert all(o.discount_pct == 0 for o in offers)
    offers = pricing.enrich_offers(offers, db, None, cfg)
    assert len(selection.filter_offers(offers, db, cfg)) > 0
    db.close()


def test_pool_real_produz_candidatas_com_o_config_real(tmp_path, monkeypatch):
    """A rede que importa: o pool de PRODUÇÃO, lido pelo leitor de produção,
    com o `config.yaml` de produção, tem de virar candidatas.

    É aqui que um refresh malfeito aparece: entrada sem p25, mínima acima do
    p25, preço fora da faixa, buy box não verificado — tudo isso faz o leitor
    ignorar a entrada COM MOTIVO, e se ele ignorar todas o ML publica zero.
    Sem este teste, esse zero seria indistinguível de "não havia oferta boa"
    (foi assim nas quatro vezes anteriores).

    O tempo é congelado na geração do pool: o teste protege a régua, não a
    validade do arquivo — pool vencido é problema de operação, avisado no
    resumo, e não deve quebrar a suíte."""
    real = RAIZ / "data/meli_offers.json"
    if not real.is_file():
        pytest.skip("sem data/meli_offers.json neste checkout")
    raw = json.loads(real.read_text(encoding="utf-8"))
    _congela(monkeypatch, _dia_do_pool(raw))
    cfg = load_config(CONFIG_REAL)
    cfg["meli"]["offers_path"] = str(real)
    src = _meli_source(tmp_path)
    offers = src.fetch_offers(cfg)

    assert offers, f"pool real não produziu oferta alguma: {src.pool_warning}"
    assert src.pool_warning is None, f"entradas ignoradas no pool real: {src.pool_warning}"
    assert len(offers) == len(raw["offers"])

    db = StateDB(tmp_path / "s.db")
    candidatas = selection.filter_offers(
        pricing.enrich_offers(offers, db, None, cfg), db, cfg)
    assert candidatas, "pool real carregou mas nenhuma oferta virou candidata"
    db.close()


def test_config_real_nao_tem_mais_portao_de_desconto():
    cfg = load_config(CONFIG_REAL)
    assert "min_discount_pct" not in cfg["selection"]
    assert "max_above_historic_min" not in (cfg.get("meli") or {})
    assert cfg["selection"]["max_above_ref"] >= 1.0
    assert cfg["selection"]["ref_min_observations"] == pricing.MIN_WINDOW_DAYS


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
    texto = message.build_message(offer, copy, "https://shope.ee/x",
                                  pricing.verdict(offer, cfg["selection"]["min_real_discount_pct"]))
    assert "OFF" not in texto                # ...e o post não repete nada disso
    assert "<s>" not in texto
    assert "R$ 499,99" not in texto
    assert "R$ 249,99" in texto
    db.close()

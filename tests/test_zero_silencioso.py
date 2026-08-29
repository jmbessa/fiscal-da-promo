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
    """O "hoje" em que este pool é contemporâneo — a data de geração.

    A única validade que resta é a do ARQUIVO (`generated_at` + `valid_days`).
    Até a fase 5M havia uma segunda, de 7 dias, sobre o `buy_box_checked_at`
    de cada entrada, e este helper tinha de acompanhar a MAIS RECENTE das
    duas; o buy box saiu do leitor junto com a premissa de que o preço vinha
    do anúncio dele."""
    return date.fromisoformat(raw["generated_at"])


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


def test_toda_oferta_do_meli_nasce_SEM_regua_e_com_preco(tmp_path, pool_no_prazo):
    """Invertido na fase 5M, e de propósito: a régua curada do pool é do
    anúncio que vencia o buy box, e o preço publicado passou a ser o do
    anúncio LINKADO mais barato — outro vendedor. Levar a régua junto faria o
    selo comparar o preço de A com a mínima de B.

    O que NÃO pode zerar junto é o preço: sem ele o `ev_score` é 0, a oferta
    cai para o fim da fila e o `min_ev_brl` a mata — o zero silencioso de novo,
    agora pela porta do ranking. A mediana do pool continua sendo a estimativa
    com que a oferta entra na fila."""
    offers = _meli_source(tmp_path).fetch_offers(_cfg())
    assert offers
    assert all(o.price_ref_cents == 0 and o.price_p25_cents == 0 for o in offers)
    assert all(o.price_window_days == 0 for o in offers)
    assert all(o.price_floor_cents == 0 and o.price_floor_window_days == 0 for o in offers)
    assert all(o.price_current_cents > 0 for o in offers)
    assert all(pricing.verdict(o, 10).mode == "B" for o in offers)
    assert all(pricing.verdict(o, 10).seal == "" for o in offers)


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
    p25, preço fora da faixa — tudo isso faz o leitor ignorar a entrada COM
    MOTIVO, e se ele ignorar todas o ML publica zero.
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


def test_pool_so_de_entradas_sem_historico_produz_candidatas(tmp_path):
    """Fase 5J, e é o zero silencioso de novo: o leitor passou a aceitar a
    entrada sem histórico, mas ela chega com `price_current_cents == 0` (o
    preço só existe depois do `refresh_price`, que roda DEPOIS do filtro). Com
    o `config.yaml` real, a faixa de preço e o piso de `min_ev_brl` matavam
    100% delas — a fase inteira seria um no-op, sem nada falhar."""
    from tests.test_meli import SEM_HISTORICO, write_pool

    pool = write_pool(tmp_path / "pool.json", [
        {"product_id": f"MLB{i}", "title": f"Produto {i}", **SEM_HISTORICO,
         "image_url": "https://http2.mlstatic.com/x.jpg", "category": "MLB264586",
         "sales": 13337, "rating": 4.8}
        for i in range(3)])
    cfg = load_config(CONFIG_REAL)
    cfg["meli"]["offers_path"] = str(pool)
    src = _meli_source(tmp_path)
    offers = src.fetch_offers(cfg)
    assert len(offers) == 3, src.pool_warning

    db = StateDB(tmp_path / "s.db")
    candidatas = selection.filter_offers(pricing.enrich_offers(offers, db, None, cfg), db, cfg)
    assert len(candidatas) == 3, (
        "3 entradas sem histórico entraram e nem todas sobraram — é o zero "
        "silencioso de novo, agora no preço desconhecido")
    db.close()


def test_a_entrada_sem_historico_nao_entra_por_uma_porta_que_a_de_30_reais_nao_usa(tmp_path):
    """A checagem de faixa foi PULADA na carga, não removida: ela volta no
    preço VIVO, depois do `refresh_price`. Uma oferta de R$ 3.000 (e uma sem
    preço nenhum) é barrada lá — pelo mesmo `price_min_brl..price_max_brl`."""
    from afiliado import validate
    from afiliado.errors import ValidationError
    from tests.test_models import make_offer

    cfg = load_config(CONFIG_REAL)
    sem_regua = dict(source="meli", price_ref_cents=0, price_p25_cents=0,
                     price_window_days=0, price_original_cents=0)
    validate.check_price(make_offer(**sem_regua, price_current_cents=3390), cfg)
    for centavos in (300_000, 1999, 0):
        with pytest.raises(ValidationError, match="fora da faixa"):
            validate.check_price(make_offer(**sem_regua, price_current_cents=centavos), cfg)


# -- fase 5L: o lote do data feed da Shopee ----------------------------------

def _lote_de_feed(n: int = 500) -> list[dict]:
    """Um lote INTEIRO do feed, na proporção medida ao vivo em 2026-08-28
    (`getItemFeedData`, 3 janelas de 500 do "Shopee Oficial BR"):

    - 32% das linhas caem nas cinco raízes que a conta varre; o resto é
      autopeças (102187, a maior categoria do feed), pets, papelaria...;
    - 86% dos preços caem na faixa de R$ 20 a R$ 1.000;
    - `like` vai de 0 a dezenas de milhares (mediana 70);
    - e NENHUMA traz `commission` ou `sales` — é isso que esta rede protege.
    """
    nossas = ["100630", "100636", "100001", "100637", "100632"]
    outras = ["102187", "100643", "100638", "100629", "100010"]
    linhas = []
    for i in range(n):
        nossa = i % 100 < 32
        cat = nossas[i % 5] if nossa else outras[i % 5]
        # 14% fora da faixa: metade barata demais, metade cara demais
        preco = {0: "9.90", 1: "1499.00"}.get(i % 14, f"{20 + (i % 900)}.90")
        linhas.append({"columns": json.dumps({
            "itemid": str(9_000_000 + i), "title": f"Produto do feed {i}",
            "price": f"{40 + (i % 900)}.90", "sale_price": preco,
            "discount_percentage": str(i % 60), "item_rating": "4.9",
            "image_link": f"https://cf.shopee.com.br/file/{i}",
            "product_link": f"https://shopee.com.br/product/7/{9_000_000 + i}",
            "product_short link": f"https://shopee.com.br/universal-link/product/7/{i}"
                                  "?utm_medium=affiliates&utm_source=an_18313221156",
            "global_catid1": cat, "global_category1": "x", "like": str(i * 7)},
            ensure_ascii=False), "updateType": None})
    return linhas


def _shopee_so_com_feed(db, linhas: list[dict]):
    """`ShopeeSource` real cuja BUSCA não devolve nada: o que sobrar no fim do
    filtro veio do feed, e só dele."""
    from afiliado.sources.shopee import ShopeeSource

    def handler(request):
        corpo = json.loads(request.content.decode())
        if "listItemFeeds" in corpo["query"]:
            return httpx.Response(200, json={"data": {"listItemFeeds": {"feeds": [
                {"datafeedId": "1_FULL_2026-08-27", "datafeedName": "Shopee Oficial BR",
                 "totalCount": 100_000, "date": "2026-08-27", "feedMode": "FULL"}]}}})
        if "getItemFeedData" in corpo["query"]:
            return httpx.Response(200, json={"data": {"getItemFeedData": {
                "rows": linhas,
                "pageInfo": {"offset": 0, "limit": len(linhas),
                             "totalCount": 100_000, "hasMore": True}}}})
        return httpx.Response(200, json={"data": {"productOfferV2": {
            "nodes": [], "pageInfo": {"hasNextPage": False}}}})

    return ShopeeSource("APPID", "SECRET",
                        client=httpx.Client(transport=httpx.MockTransport(handler)),
                        db=db)


def test_um_lote_inteiro_do_feed_produz_candidatas_com_o_config_real(tmp_path):
    """Fase 5L, e é o zero silencioso pela SEXTA vez: a linha do data feed
    chega sem `commission` e sem `sales` (o feed não tem os campos), e com o
    `min_ev_brl: 0.50` do config real o piso de EV as leria como "valem zero" e
    mataria 100% delas — a fase inteira seria um no-op, sem nada falhar."""
    cfg = load_config(CONFIG_REAL)
    db = StateDB(tmp_path / "s.db")
    src = _shopee_so_com_feed(db, _lote_de_feed())

    offers = src.fetch_offers(cfg)
    assert not src.discovery_stats.feed_warning, src.discovery_stats.feed_warning
    assert len(offers) == cfg["shopee"]["feed_keep_per_run"] == 10, src.discovery_stats.feed
    assert all(o.commission_pct == 0.0 and o.sales == 0 for o in offers)

    offers = pricing.enrich_offers(offers, db, None, cfg)
    candidatas, cortes = selection.filter_offers_with_stats(offers, db, cfg)
    assert len(candidatas) > 0, (
        f"{len(offers)} linhas do feed entraram e ZERO sobraram — {cortes.resumo()}")
    assert cortes.ev == 0, "o piso de EV matou candidata de comissão desconhecida"
    assert cortes.categoria == 0, "o feed entregou categoria fora do allowlist"
    db.close()


def test_o_feed_sem_a_isencao_do_piso_de_ev_morreria_inteiro(tmp_path):
    """A prova de que a rede acima é a que segura o zero: com o piso julgando a
    comissão desconhecida (o comportamento anterior à 5L), sobra ZERO."""
    cfg = load_config(CONFIG_REAL)
    db = StateDB(tmp_path / "s.db")
    offers = _shopee_so_com_feed(db, _lote_de_feed()).fetch_offers(cfg)
    offers = pricing.enrich_offers(offers, db, None, cfg)
    piso = float(cfg["selection"]["min_ev_brl"])
    assert piso > 0
    assert [o for o in offers if selection.ev_score(o, cfg) >= piso] == []
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

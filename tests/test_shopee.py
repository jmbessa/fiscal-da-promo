import hashlib
import json
from pathlib import Path

import httpx
import pytest

from afiliado.errors import SourceError
from afiliado.sources.shopee import ShopeeSource

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "shopee_product_offer.json")
    .read_text(encoding="utf-8"))

CFG = {"shopee": {"sort_types": [5], "list_type": 0, "pages": 1, "page_size": 50}}


def source_with(handler, sleep=None) -> ShopeeSource:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    if sleep is None:
        return ShopeeSource("APPID", "SECRET", client=client)
    return ShopeeSource("APPID", "SECRET", client=client, sleep=sleep)


# -- Fase 5A (A4): retry com backoff em 429, 5xx e erro de conexão --------------

def test_post_repete_em_429_e_5xx_com_backoff():
    calls, sleeps = [], []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, json={"message": "rate limited"})
        if len(calls) == 2:
            return httpx.Response(503, text="Service Unavailable")
        return httpx.Response(200, json=FIXTURE)

    offers = source_with(handler, sleep=sleeps.append).fetch_offers(CFG)
    assert len(offers) == 1
    assert len(calls) == 3
    assert sleeps == [0.5, 1.5]


def test_post_repete_em_erro_de_conexao_com_backoff():
    calls, sleeps = [], []

    def handler(request):
        calls.append(1)
        if len(calls) < 3:
            raise httpx.ConnectError("down")
        return httpx.Response(200, json=FIXTURE)

    offers = source_with(handler, sleep=sleeps.append).fetch_offers(CFG)
    assert len(offers) == 1 and len(calls) == 3
    assert sleeps == [0.5, 1.5]


def test_post_esgota_as_tentativas_e_levanta_source_error():
    calls, sleeps = [], []

    def handler(request):
        calls.append(1)
        return httpx.Response(502, text="Bad Gateway")

    with pytest.raises(SourceError, match="502"):
        source_with(handler, sleep=sleeps.append).fetch_offers(CFG)
    assert len(calls) == 4                    # 1 tentativa + 3 repetições
    assert sleeps == [0.5, 1.5, 4.0]


def test_post_nao_repete_em_4xx_que_nao_e_429():
    calls, sleeps = [], []

    def handler(request):
        calls.append(1)
        return httpx.Response(401, text="Unauthorized")

    with pytest.raises(SourceError, match="401"):
        source_with(handler, sleep=sleeps.append).fetch_offers(CFG)
    assert len(calls) == 1 and sleeps == []


def test_post_assina_cada_tentativa_de_novo():
    # A assinatura carrega o timestamp: uma repetição com a assinatura velha
    # poderia ser recusada. Cada tentativa recalcula o header.
    auths, sleeps = [], []

    def handler(request):
        auths.append(request.headers["authorization"])
        if len(auths) == 1:
            return httpx.Response(503, text="down")
        return httpx.Response(200, json=FIXTURE)

    source_with(handler, sleep=sleeps.append).fetch_offers(CFG)
    assert len(auths) == 2 and all(a.startswith("SHA256 Credential=APPID") for a in auths)


def test_signature_header_matches_formula():
    captured = {}

    def handler(request):
        captured["auth"] = request.headers["authorization"]
        captured["body"] = request.content.decode()
        return httpx.Response(200, json=FIXTURE)

    source_with(handler).fetch_offers(CFG)
    # Formato: SHA256 Credential=<id>, Timestamp=<ts>, Signature=<sig>
    parts = dict(p.strip().split("=", 1)
                 for p in captured["auth"].removeprefix("SHA256 ").split(","))
    esperado = hashlib.sha256(
        f"APPID{parts['Timestamp']}{captured['body']}SECRET".encode()).hexdigest()
    assert parts["Signature"] == esperado


def test_fetch_offers_parses_and_skips_bad_nodes():
    offers = source_with(lambda r: httpx.Response(200, json=FIXTURE)).fetch_offers(CFG)
    assert len(offers) == 1  # nó sem preço é ignorado
    o = offers[0]
    assert o.item_id == "123456"
    assert o.price_current_cents == 24999
    assert o.price_original_cents == 49998  # derivado de price e priceDiscountRate
    assert o.commission_pct == 12.0
    assert o.category == "100636"
    assert o.offer_link == "https://s.shopee.com.br/xyz"
    assert o.rating == 4.9


def test_resolve_affiliate_link_short_link():
    def handler(request):
        return httpx.Response(200, json={
            "data": {"generateShortLink": {"shortLink": "https://shope.ee/abc"}}})
    src = source_with(handler)
    offers = [o for o in FIXTURE["data"]["productOfferV2"]["nodes"]]
    from tests.test_models import make_offer
    assert src.resolve_affiliate_link(make_offer()) == "https://shope.ee/abc"


def test_resolve_affiliate_link_falls_back_to_offer_link():
    def handler(request):
        return httpx.Response(200, json={"errors": [{"message": "quota"}]})
    from tests.test_models import make_offer
    offer = make_offer(offer_link="https://s.shopee.com.br/xyz")
    assert source_with(handler).resolve_affiliate_link(offer) == "https://s.shopee.com.br/xyz"


def test_resolve_affiliate_link_raises_without_fallback():
    def handler(request):
        return httpx.Response(200, json={"errors": [{"message": "quota"}]})
    from tests.test_models import make_offer
    with pytest.raises(SourceError):
        source_with(handler).resolve_affiliate_link(make_offer(offer_link=""))


def test_fetch_offers_skips_node_missing_item_id():
    payload = {
        "data": {
            "productOfferV2": {
                "nodes": [
                    {
                        "productName": "Sem itemId (deve ser ignorado)",
                        "price": "99.90",
                        "priceDiscountRate": 10,
                        "commissionRate": "0.05",
                        "sales": 1,
                        "imageUrl": "https://cf.shopee.com.br/file/no-id.jpg",
                        "productLink": "https://shopee.com.br/product/1/000",
                        "offerLink": "",
                        "productCatIds": [],
                    },
                    {
                        "itemId": 999,
                        "productName": "Válido",
                        "price": "49.90",
                        "priceDiscountRate": 20,
                        "commissionRate": "0.10",
                        "sales": 10,
                        "imageUrl": "https://cf.shopee.com.br/file/ok.jpg",
                        "productLink": "https://shopee.com.br/product/1/999",
                        "offerLink": "https://s.shopee.com.br/ok",
                        "productCatIds": [123],
                    },
                ]
            }
        }
    }
    offers = source_with(lambda r: httpx.Response(200, json=payload)).fetch_offers(CFG)
    assert len(offers) == 1
    assert offers[0].item_id == "999"


def test_post_raises_source_error_on_non_json_response():
    def handler(request):
        return httpx.Response(200, text="<html>not json</html>")
    with pytest.raises(SourceError):
        source_with(handler).fetch_offers(CFG)


def test_fetch_offers_merges_sort_types_and_dedupes():
    cfg = {"shopee": {"sort_types": [5, 2], "list_type": 0, "pages": 1, "page_size": 50}}
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=FIXTURE)

    offers = source_with(handler).fetch_offers(cfg)
    assert len(offers) == 1  # mesma oferta nas duas ordenações -> dedupe por item_id
    assert len(calls) == 2


def test_fetch_offers_busca_por_categoria():
    cfg = {"shopee": {"sort_types": [2], "list_type": 0, "pages": 1, "page_size": 50,
                       "category_ids": ["100630", "100636"]}}
    calls = []

    def handler(request):
        calls.append(json.loads(request.content.decode()))
        return httpx.Response(200, json=FIXTURE)

    offers = source_with(handler).fetch_offers(cfg)
    assert len(calls) == 2
    assert calls[0]["variables"]["productCatId"] == 100630
    assert calls[1]["variables"]["productCatId"] == 100636
    assert len(offers) == 1  # mesma oferta nas duas categorias -> dedupe por item_id


def test_fetch_offers_sem_category_ids_faz_busca_unica():
    calls = []

    def handler(request):
        calls.append(json.loads(request.content.decode()))
        return httpx.Response(200, json=FIXTURE)

    offers = source_with(handler).fetch_offers(CFG)  # CFG não tem category_ids
    assert len(calls) == 1
    assert "productCatId" not in calls[0]["variables"]
    assert len(offers) == 1


def test_parse_le_campos_novos():
    offers = source_with(lambda r: httpx.Response(200, json=FIXTURE)).fetch_offers(CFG)
    o = offers[0]
    assert o.price_min_cents == 19999
    assert o.price_max_cents == 24999
    assert o.commission_brl == pytest.approx(29.9988)
    assert o.rating == 4.9


def test_oferta_expirada_e_descartada():
    payload = {
        "data": {
            "productOfferV2": {
                "nodes": [
                    {
                        "itemId": 1,
                        "productName": "Expirada",
                        "price": "49.90",
                        "priceDiscountRate": 20,
                        "commissionRate": "0.10",
                        "sales": 10,
                        "imageUrl": "https://cf.shopee.com.br/file/expired.jpg",
                        "productLink": "https://shopee.com.br/product/1/1",
                        "offerLink": "https://s.shopee.com.br/expired",
                        "productCatIds": [123],
                        "periodEndTime": 1000000000,  # 2001 -> já passou
                    },
                    {
                        "itemId": 2,
                        "productName": "Válida",
                        "price": "59.90",
                        "priceDiscountRate": 20,
                        "commissionRate": "0.10",
                        "sales": 10,
                        "imageUrl": "https://cf.shopee.com.br/file/ok.jpg",
                        "productLink": "https://shopee.com.br/product/1/2",
                        "offerLink": "https://s.shopee.com.br/ok",
                        "productCatIds": [123],
                        "periodEndTime": 32503651199,  # ano 3000 -> nunca expira
                    },
                ]
            }
        }
    }
    offers = source_with(lambda r: httpx.Response(200, json=payload)).fetch_offers(CFG)
    assert [o.item_id for o in offers] == ["2"]


def test_uma_fatia_por_run_nunca_pagina_dentro_do_run():
    """Cada run lê UMA página por fatia — quem avança é o cursor, não um laço
    interno (fase 5C, M1): sem isso o run relia sempre as mesmas p1–2."""
    chamadas = []

    def handler(request):
        corpo = json.loads(request.content.decode())
        chamadas.append(corpo["variables"].get("page"))
        return httpx.Response(200, json={"data": {"productOfferV2": {
            "nodes": [
                {"itemId": 1, "productName": "x", "price": "10.00", "priceDiscountRate": 50,
                 "commissionRate": "0.1", "sales": 5, "imageUrl": "i", "productLink": "l",
                 "offerLink": "o", "productCatIds": [100630]}],
            "pageInfo": {"hasNextPage": True}}}})

    cfg = {"shopee": {"sort_types": [2], "list_type": 0, "pages": 3, "page_size": 3,
                      "category_ids": ["100630"]}}
    source_with(handler).fetch_offers(cfg)
    assert chamadas == [1]


def test_period_end_zero_nao_e_expirado():
    """periodEndTime 0 = validade desconhecida, não 1970."""
    def handler(request):
        return httpx.Response(200, json={"data": {"productOfferV2": {"nodes": [
            {"itemId": 42, "productName": "vale", "price": "10.00", "priceDiscountRate": 50,
             "commissionRate": "0.1", "sales": 5, "imageUrl": "i", "productLink": "l",
             "offerLink": "o", "productCatIds": [100630], "periodEndTime": 0}]}}})

    cfg = {"shopee": {"sort_types": [2], "list_type": 0, "pages": 1, "page_size": 50,
                      "category_ids": ["100630"]}}
    ofertas = source_with(handler).fetch_offers(cfg)
    assert [o.item_id for o in ofertas] == ["42"]


# =============================================================================
# Fase 5C (M1/C1) — varredura rotativa: cada run lê uma FATIA do espaço
# =============================================================================

def _no(item_id: int, cat: int = 100630) -> dict:
    return {"itemId": item_id, "productName": f"item {item_id}", "price": "49.90",
            "priceDiscountRate": 20, "commissionRate": "0.10", "sales": 500,
            "imageUrl": "https://cf.shopee.com.br/i.jpg",
            "productLink": f"https://shopee.com.br/product/1/{item_id}",
            "offerLink": f"https://s.shopee.com.br/{item_id}", "productCatIds": [cat],
            "priceMin": "49.90", "priceMax": "49.90", "commission": "4.99",
            "ratingStar": "4.8"}


def _api_falsa(chamadas: list, por_pagina: int = 3, ultima_pagina: int = 40):
    """API de mentira com uma janela de `ultima_pagina` páginas por listagem:
    cada (categoria|keyword, página) devolve itens distintos e `hasNextPage`
    fica falso na última página — como a API real (calls 75/126)."""
    listagens: dict[str, int] = {}

    def handler(request):
        v = json.loads(request.content.decode())["variables"]
        chamadas.append(v)
        page = int(v.get("page") or 1)
        chave = f"{v.get('keyword') or ''}|{v.get('productCatId') or ''}"
        base = listagens.setdefault(chave, len(listagens)) * 10_000_000 + page * 1000
        nodes = [_no(base + i, int(v.get("productCatId") or 100630))
                 for i in range(por_pagina if page <= ultima_pagina else 0)]
        return httpx.Response(200, json={"data": {"productOfferV2": {
            "nodes": nodes, "pageInfo": {"hasNextPage": page < ultima_pagina}}}})
    return handler


CFG_5C = {"shopee": {
    "sort_types": [2], "list_type": 0, "pages": 40, "page_size": 50,
    "calls_per_run": 8,
    "category_ids": ["100630", "100636", "100001", "100637", "100632"],
    "subcategory_ids": ["100663", "100659", "100662", "100664"],
    "keywords": {"100630": ["sérum", "shampoo"], "100001": ["creatina", "whey"]},
}}


def _fonte_com_cursor(handler, tmp_path):
    from afiliado.state import StateDB
    db = StateDB(tmp_path / "s.db")
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return ShopeeSource("APPID", "SECRET", client=client, db=db), db


def test_a_fatia_do_run_e_raizes_mais_subcategorias_mais_keyword(tmp_path):
    chamadas = []
    src, db = _fonte_com_cursor(_api_falsa(chamadas), tmp_path)
    src.fetch_offers(CFG_5C)
    assert len(chamadas) == 8
    raizes = [c for c in chamadas if not c.get("keyword")
              and str(c.get("productCatId")) in CFG_5C["shopee"]["category_ids"]]
    subs = [c for c in chamadas if not c.get("keyword")
            and str(c.get("productCatId")) in CFG_5C["shopee"]["subcategory_ids"]]
    kws = [c for c in chamadas if c.get("keyword")]
    assert len(raizes) == 5 and len(subs) == 2 and len(kws) == 1
    assert [str(c["productCatId"]) for c in raizes] == CFG_5C["shopee"]["category_ids"]
    assert all(c["page"] == 1 for c in raizes)             # 1º run: p1 das raízes
    assert [str(c["productCatId"]) for c in subs] == ["100663", "100659"]
    assert kws[0]["keyword"] == "sérum" and kws[0]["productCatId"] == 100630
    db.close()


def test_cursor_avanca_e_a_fatia_seguinte_e_outra(tmp_path):
    chamadas = []
    src, db = _fonte_com_cursor(_api_falsa(chamadas), tmp_path)
    src.fetch_offers(CFG_5C)
    n = len(chamadas)
    src.fetch_offers(CFG_5C)
    run2 = chamadas[n:]
    raizes = [c for c in run2 if str(c.get("productCatId")) in CFG_5C["shopee"]["category_ids"]
              and not c.get("keyword")]
    subs = [c for c in run2 if str(c.get("productCatId")) in CFG_5C["shopee"]["subcategory_ids"]]
    kws = [c for c in run2 if c.get("keyword")]
    assert all(c["page"] == 2 for c in raizes)                        # avançou 1 página
    assert [str(c["productCatId"]) for c in subs] == ["100662", "100664"]  # avançou 2 ids
    assert kws[0]["keyword"] == "shampoo"                             # avançou 1 termo
    # nenhuma fatia (categoria, keyword, página) se repete entre os dois runs
    def chave(c):
        return (c.get("productCatId"), c.get("keyword"), c.get("page"))
    assert not {chave(c) for c in chamadas[:n]} & {chave(c) for c in run2}
    db.close()


def test_has_next_page_falso_reinicia_a_categoria(tmp_path):
    chamadas = []
    # janela de 2 páginas: o 2º run bate no fim e o 3º volta à p1
    src, db = _fonte_com_cursor(_api_falsa(chamadas, ultima_pagina=2), tmp_path)
    for _ in range(3):
        src.fetch_offers(CFG_5C)
    raizes = [c["page"] for c in chamadas
              if str(c.get("productCatId")) == "100630" and not c.get("keyword")]
    assert raizes == [1, 2, 1]
    db.close()


def test_o_cursor_da_a_volta_no_fim_da_janela(tmp_path):
    chamadas = []
    cfg = {"shopee": {**CFG_5C["shopee"], "pages": 3, "subcategory_ids": [],
                      "keywords": {}, "category_ids": ["100630"]}}
    src, db = _fonte_com_cursor(_api_falsa(chamadas, ultima_pagina=99), tmp_path)
    for _ in range(4):
        src.fetch_offers(cfg)
    assert [c["page"] for c in chamadas] == [1, 2, 3, 1]   # `pages` é o teto
    db.close()


def test_quarenta_runs_cobrem_as_raizes_sem_redescobrir(tmp_path):
    """Teste obrigatório (1): 40 runs contra uma API de 2.000 itens por
    categoria — o estoque cobre as 5 raízes inteiras e nenhum item aparece
    duas vezes no mesmo ciclo."""
    chamadas = []
    cfg = {"shopee": {**CFG_5C["shopee"], "subcategory_ids": [], "keywords": {},
                      "calls_per_run": 5}}
    src, db = _fonte_com_cursor(_api_falsa(chamadas, por_pagina=50, ultima_pagina=40),
                                tmp_path)
    vistos: list[str] = []
    for _ in range(40):
        vistos += [o.item_id for o in src.fetch_offers(cfg)]
    assert len(chamadas) == 200                       # 5 categorias × 40 páginas
    assert {(c["productCatId"], c["page"]) for c in chamadas} == {
        (int(cat), pag) for cat in cfg["shopee"]["category_ids"] for pag in range(1, 41)}
    assert len(vistos) == 200 * 50 == len(set(vistos))   # nada redescoberto no ciclo
    db.close()


def test_keyword_percorre_termos_e_paginas(tmp_path):
    chamadas = []
    cfg = {"shopee": {**CFG_5C["shopee"], "category_ids": [], "subcategory_ids": [],
                      "calls_per_run": 1}}
    src, db = _fonte_com_cursor(_api_falsa(chamadas), tmp_path)
    for _ in range(9):
        src.fetch_offers(cfg)
    assert [(c["keyword"], c["page"]) for c in chamadas] == [
        ("sérum", 1), ("shampoo", 1), ("creatina", 1), ("whey", 1),
        ("sérum", 2), ("shampoo", 2), ("creatina", 2), ("whey", 2),
        ("sérum", 1)]                                  # 8 fatias, ciclo fechado
    db.close()


def test_calls_per_run_e_o_teto_de_chamadas(tmp_path):
    chamadas = []
    cfg = {"shopee": {**CFG_5C["shopee"], "calls_per_run": 3}}
    src, db = _fonte_com_cursor(_api_falsa(chamadas), tmp_path)
    src.fetch_offers(cfg)
    assert len(chamadas) == 3
    # o cursor das keywords não avança por uma fatia que o teto cortou
    src.fetch_offers(cfg)
    assert not any(c.get("keyword") for c in chamadas)
    db.close()


def test_estatisticas_da_descoberta(tmp_path):
    chamadas = []
    cfg = {"shopee": {**CFG_5C["shopee"], "subcategory_ids": [], "keywords": {}}}
    src, db = _fonte_com_cursor(_api_falsa(chamadas, por_pagina=4), tmp_path)
    ofertas = src.fetch_offers(cfg)
    assert src.discovery_stats.calls == 5
    assert src.discovery_stats.nodes == 20
    assert src.discovery_stats.eligible == len(ofertas) == 20
    db.close()


def test_sem_db_a_varredura_ainda_roda(tmp_path):
    """`doctor` monta a fonte sem StateDB: cursor em memória, sem persistir."""
    chamadas = []
    cfg = {"shopee": {**CFG_5C["shopee"], "calls_per_run": 1, "subcategory_ids": [],
                      "keywords": {}, "category_ids": ["100630"]}}
    src = source_with(_api_falsa(chamadas))
    src.fetch_offers(cfg)
    src.fetch_offers(cfg)
    assert [c["page"] for c in chamadas] == [1, 2]


# -- preço ao vivo da Shopee (M1) ---------------------------------------------

def test_refresh_price_atualiza_preco_e_comissao():
    from tests.test_models import make_offer
    pedidos = []

    def handler(request):
        corpo = json.loads(request.content.decode())
        pedidos.append(corpo["variables"])
        return httpx.Response(200, json={"data": {"productOfferV2": {"nodes": [
            {**_no(123456), "price": "19.90", "priceDiscountRate": 0,
             "commissionRate": "0.15", "commission": "2.985"}]}}})

    offer = make_offer(source="shopee", item_id="123456", price_current_cents=24999)
    novo = source_with(handler).refresh_price(offer)
    assert pedidos == [{"itemId": 123456}]
    assert novo.price_current_cents == 1990
    assert novo.commission_pct == 15.0
    assert novo.commission_brl == pytest.approx(2.985)
    assert novo.title == offer.title and novo is not offer


def test_refresh_price_descarta_item_que_sumiu_da_listagem():
    from tests.test_models import make_offer
    handler = lambda r: httpx.Response(200, json={"data": {"productOfferV2": {"nodes": []}}})
    with pytest.raises(SourceError, match="saiu da listagem"):
        source_with(handler).refresh_price(make_offer(source="shopee", item_id="123456"))


def test_refresh_price_descarta_item_expirado():
    from tests.test_models import make_offer
    def handler(request):
        return httpx.Response(200, json={"data": {"productOfferV2": {"nodes": [
            {**_no(123456), "periodEndTime": 1000000000}]}}})   # 2001
    with pytest.raises(SourceError, match="saiu da listagem"):
        source_with(handler).refresh_price(make_offer(source="shopee", item_id="123456"))


def test_refresh_price_ignora_no_de_outro_item():
    from tests.test_models import make_offer
    def handler(request):
        return httpx.Response(200, json={"data": {"productOfferV2": {"nodes": [_no(999)]}}})
    with pytest.raises(SourceError, match="saiu da listagem"):
        source_with(handler).refresh_price(make_offer(source="shopee", item_id="123456"))

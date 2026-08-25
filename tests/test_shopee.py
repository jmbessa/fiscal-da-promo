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


def source_with(handler) -> ShopeeSource:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return ShopeeSource("APPID", "SECRET", client=client)


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


def test_pagina_incompleta_interrompe_paginacao():
    """Página menor que page_size = fim do estoque: não pedir a próxima."""
    chamadas = []

    def handler(request):
        corpo = json.loads(request.content.decode())
        chamadas.append(corpo["variables"].get("page"))
        # devolve 1 nó (page_size é 3) -> deve parar na primeira página
        return httpx.Response(200, json={"data": {"productOfferV2": {"nodes": [
            {"itemId": 1, "productName": "x", "price": "10.00", "priceDiscountRate": 50,
             "commissionRate": "0.1", "sales": 5, "imageUrl": "i", "productLink": "l",
             "offerLink": "o", "productCatIds": [100630]}]}}})

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

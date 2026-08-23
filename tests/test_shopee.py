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

CFG = {"shopee": {"sort_type": 5, "list_type": 0, "pages": 1, "page_size": 50}}


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

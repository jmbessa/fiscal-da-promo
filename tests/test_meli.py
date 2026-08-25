import json
from pathlib import Path

import httpx
import pytest

from afiliado.errors import SourceError
from afiliado.sources.meli import MeliSource

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "meli_search.json")
    .read_text(encoding="utf-8"))

CFG = {"meli": {"category_ids": ["MLB1000"], "per_category": 50, "min_sold": 5}}


def source_with(handler, tmp_path, refresh_token="", token_path=None, links_path=None) -> MeliSource:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return MeliSource(
        "CID", "CSECRET",
        refresh_token=refresh_token,
        token_path=token_path or (tmp_path / "meli_token.json"),
        links_path=links_path or (tmp_path / "meli_links.json"),
        client=client,
    )


def _authed_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/oauth/token":
        return httpx.Response(200, json={"access_token": "TOK", "expires_in": 21600})
    if request.url.path == "/sites/MLB/search":
        return httpx.Response(200, json=FIXTURE)
    raise AssertionError(f"caminho inesperado: {request.url.path}")


def test_client_credentials_preferido(tmp_path):
    calls = []

    def handler(request: httpx.Request):
        calls.append(request)
        if request.url.path == "/oauth/token":
            body = json.loads(request.content)
            assert body["grant_type"] == "client_credentials"
            assert body["client_id"] == "CID"
            assert body["client_secret"] == "CSECRET"
            return httpx.Response(200, json={"access_token": "TOK-CC", "expires_in": 21600})
        if request.url.path == "/sites/MLB/search":
            assert request.headers["authorization"] == "Bearer TOK-CC"
            return httpx.Response(200, json=FIXTURE)
        raise AssertionError(f"caminho inesperado: {request.url.path}")

    offers = source_with(handler, tmp_path).fetch_offers(CFG)
    assert len(offers) == 2
    token_calls = [c for c in calls if c.url.path == "/oauth/token"]
    assert len(token_calls) == 1  # refresh_token nunca foi chamado


def test_fallback_refresh_token_persiste_rotacao(tmp_path):
    token_path = tmp_path / "meli_token.json"

    def handler(request: httpx.Request):
        if request.url.path == "/oauth/token":
            body = json.loads(request.content)
            if body["grant_type"] == "client_credentials":
                return httpx.Response(400, json={"error": "invalid_client"})
            assert body["grant_type"] == "refresh_token"
            assert body["refresh_token"] == "OLD-REFRESH"
            return httpx.Response(200, json={
                "access_token": "TOK-NEW",
                "refresh_token": "NEW-REFRESH",
                "expires_in": 21600,
            })
        if request.url.path == "/sites/MLB/search":
            assert request.headers["authorization"] == "Bearer TOK-NEW"
            return httpx.Response(200, json=FIXTURE)
        raise AssertionError(f"caminho inesperado: {request.url.path}")

    src = source_with(handler, tmp_path, refresh_token="OLD-REFRESH", token_path=token_path)
    src.fetch_offers(CFG)
    saved = json.loads(token_path.read_text(encoding="utf-8"))
    assert saved["refresh_token"] == "NEW-REFRESH"
    assert saved["access_token"] == "TOK-NEW"


def test_token_do_arquivo_tem_precedencia_sobre_env(tmp_path):
    token_path = tmp_path / "meli_token.json"
    token_path.write_text(json.dumps({"refresh_token": "do-arquivo"}), encoding="utf-8")
    captured = {}

    def handler(request: httpx.Request):
        body = json.loads(request.content)
        if body["grant_type"] == "client_credentials":
            return httpx.Response(400, json={"error": "invalid_client"})
        captured["refresh_token"] = body["refresh_token"]
        return httpx.Response(200, json={
            "access_token": "TOK", "refresh_token": "ROT", "expires_in": 21600})

    src = source_with(handler, tmp_path, refresh_token="da-env", token_path=token_path)
    src.ensure_token()
    assert captured["refresh_token"] == "do-arquivo"


def test_fetch_offers_mapeia_e_filtra(tmp_path):
    offers = source_with(_authed_handler, tmp_path).fetch_offers(CFG)
    assert len(offers) == 2  # item de baixa venda (sold_quantity=1 < min_sold=5) sai
    by_id = {o.item_id: o for o in offers}
    assert "MLB999999" not in by_id

    completo = by_id["MLB123456"]
    assert completo.source == "meli"
    assert completo.price_current_cents == 14990
    assert completo.price_original_cents == 19990
    assert completo.product_url == "https://produto.mercadolivre.com.br/MLB-123456-fone-de-ouvido"
    assert completo.category == "MLB1000"
    assert completo.sales == 120
    assert completo.commission_pct == 0.0
    assert completo.rating == 0.0
    assert completo.image_url == "http://http2.mlstatic.com/D_123456-W.jpg"

    sem_original = by_id["MLB777777"]
    assert sem_original.price_original_cents == sem_original.price_current_cents == 5990


def test_fetch_offers_erro_http_vira_source_error(tmp_path):
    def handler(request: httpx.Request):
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "TOK", "expires_in": 21600})
        return httpx.Response(500, text="erro interno")

    with pytest.raises(SourceError):
        source_with(handler, tmp_path).fetch_offers(CFG)


def test_resolve_affiliate_link_usa_pool(tmp_path):
    from tests.test_models import make_offer
    links_path = tmp_path / "meli_links.json"
    links_path.write_text(
        json.dumps({"MLB123456": "https://mercadolivre.com/sec/abc"}), encoding="utf-8")
    src = source_with(_authed_handler, tmp_path, links_path=links_path)
    offer = make_offer(source="meli", item_id="MLB123456")
    assert src.resolve_affiliate_link(offer) == "https://mercadolivre.com/sec/abc"


def test_resolve_affiliate_link_sem_pool_levanta_source_error(tmp_path):
    from tests.test_models import make_offer
    links_path = tmp_path / "meli_links.json"
    links_path.write_text(json.dumps({"MLB1": "https://mercadolivre.com/sec/x"}), encoding="utf-8")
    src = source_with(_authed_handler, tmp_path, links_path=links_path)
    offer = make_offer(source="meli", item_id="MLB999")
    with pytest.raises(SourceError):
        src.resolve_affiliate_link(offer)


def test_pool_ausente_nao_levanta_na_carga(tmp_path):
    from tests.test_models import make_offer
    src = source_with(_authed_handler, tmp_path, links_path=tmp_path / "nao-existe.json")
    offer = make_offer(source="meli", item_id="MLB1")
    with pytest.raises(SourceError, match="sem link de afiliado"):
        src.resolve_affiliate_link(offer)

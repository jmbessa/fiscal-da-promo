import json
from datetime import date, timedelta

import httpx
import pytest

from afiliado.errors import SourceError
from afiliado.sources.meli import MeliSource


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
    raise AssertionError(f"caminho inesperado: {request.url.path}")


def write_pool(path, offers, generated_at=None, valid_days=30):
    """`price_historic_min_cents` é obrigatório em cada entrada (fase 4: sem
    ele a entrada é pulada). Para não repetir a chave nos testes que não se
    importam com o piso, o padrão aqui é o próprio price_ref_cents; passe a
    chave explicitamente (inclusive ausente/inválida) para exercitar a
    rejeição."""
    generated_at = generated_at or date.today()
    entradas = []
    for offer in offers:
        item = dict(offer)
        if "price_ref_cents" in item:
            item.setdefault("price_historic_min_cents", item["price_ref_cents"])
        entradas.append(item)
    path.write_text(json.dumps({
        "generated_at": generated_at.isoformat(),
        "valid_days": valid_days,
        "offers": entradas,
    }), encoding="utf-8")
    return path


# -- autenticação (OAuth já funcionava antes da fase 3B; só muda COMO o teste
# dispara ensure_token, já que fetch_offers não faz mais nenhuma chamada de
# rede — ver testes de fetch_offers abaixo) --------------------------------

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
        raise AssertionError(f"caminho inesperado: {request.url.path}")

    token = source_with(handler, tmp_path).ensure_token()
    assert token == "TOK-CC"
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
        raise AssertionError(f"caminho inesperado: {request.url.path}")

    src = source_with(handler, tmp_path, refresh_token="OLD-REFRESH", token_path=token_path)
    src.ensure_token()
    saved = json.loads(token_path.read_text(encoding="utf-8"))
    assert saved["refresh_token"] == "NEW-REFRESH"
    assert saved["access_token"] == "TOK-NEW"


def _refresh_only_handler(request: httpx.Request) -> httpx.Response:
    """client_credentials sempre recusado -> força o caminho refresh_token,
    que é o único que grava em disco (usado pelos testes de persistência)."""
    if request.url.path == "/oauth/token":
        body = json.loads(request.content)
        if body["grant_type"] == "client_credentials":
            return httpx.Response(400, json={"error": "invalid_client"})
        return httpx.Response(200, json={
            "access_token": "TOK-NEW", "refresh_token": "NEW-REFRESH", "expires_in": 21600})
    raise AssertionError(f"caminho inesperado: {request.url.path}")


def test_persist_token_falha_vira_source_error(tmp_path, monkeypatch):
    import os

    def boom(*a, **k):
        raise OSError("disco cheio")

    monkeypatch.setattr(os, "replace", boom)
    src = source_with(_refresh_only_handler, tmp_path, refresh_token="OLD-REFRESH")
    with pytest.raises(SourceError, match="persistir"):
        src.ensure_token()


def test_persist_token_e_atomico(tmp_path):
    token_path = tmp_path / "meli_token.json"
    src = source_with(_refresh_only_handler, tmp_path, refresh_token="OLD-REFRESH",
                      token_path=token_path)
    src.ensure_token()
    # só o arquivo final sobrevive: nenhum .tmp deixado para trás pela troca atômica
    assert list(tmp_path.iterdir()) == [token_path]


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


# -- fetch_offers (pool curado, fase 3B) -----------------------------------

def _no_network_handler(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"fetch_offers não deveria chamar a rede: {request.url}")


def test_fetch_offers_le_pool_e_mapeia_campos(tmp_path):
    pool_path = tmp_path / "meli_offers.json"
    write_pool(pool_path, [
        {"product_id": "MLB18725310", "title": "Creatina 1kg Growth",
         "image_url": "https://http2.mlstatic.com/D_creatina.jpg", "category": "MLB264586",
         "price_ref_cents": 6890, "price_historic_min_cents": 4792,
         "sales": 13337, "rating": 4.8},
    ])
    cfg = {"meli": {"offers_path": str(pool_path), "commission_pct": 4.0}}
    src = source_with(_no_network_handler, tmp_path)
    offers = src.fetch_offers(cfg)
    assert len(offers) == 1
    o = offers[0]
    assert o.source == "meli"
    assert o.item_id == "MLB18725310"
    assert o.title == "Creatina 1kg Growth"
    assert o.image_url == "https://http2.mlstatic.com/D_creatina.jpg"
    assert o.category == "MLB264586"
    assert o.price_current_cents == 6890
    assert o.price_original_cents == 6890  # sem desconto inflado (ver Mudança 3)
    assert o.price_ref_cents == 6890       # a NOSSA referência vem do pool curado
    assert o.price_floor_cents == 4792     # mínima histórica -> selo de menor preço
    assert o.real_discount_pct == 0        # no preço típico: nada a alegar
    assert o.sales == 13337
    assert o.rating == 4.8
    assert o.commission_pct == 4.0
    assert o.product_url == "https://www.mercadolivre.com.br/p/MLB18725310"


def test_fetch_offers_nao_chama_rede(tmp_path):
    pool_path = tmp_path / "meli_offers.json"
    write_pool(pool_path, [
        {"product_id": "MLB1", "title": "X", "price_ref_cents": 1000},
    ])
    cfg = {"meli": {"offers_path": str(pool_path)}}
    # _no_network_handler levanta AssertionError se QUALQUER requisição sair;
    # o próprio teste passar já prova a ausência de chamada de rede.
    offers = source_with(_no_network_handler, tmp_path).fetch_offers(cfg)
    assert len(offers) == 1


def test_fetch_offers_pool_ausente_nao_levanta(tmp_path):
    cfg = {"meli": {"offers_path": str(tmp_path / "nao-existe.json")}}
    src = source_with(_no_network_handler, tmp_path)
    assert src.fetch_offers(cfg) == []
    assert src.pool_warning is not None


def test_fetch_offers_pool_json_invalido_nao_levanta(tmp_path):
    pool_path = tmp_path / "meli_offers.json"
    pool_path.write_text("{not valid json", encoding="utf-8")
    cfg = {"meli": {"offers_path": str(pool_path)}}
    assert source_with(_no_network_handler, tmp_path).fetch_offers(cfg) == []


def test_fetch_offers_pool_vencido_devolve_vazio(tmp_path):
    pool_path = tmp_path / "meli_offers.json"
    write_pool(pool_path, [
        {"product_id": "MLB1", "title": "X", "price_ref_cents": 1000},
    ], generated_at=date.today() - timedelta(days=40), valid_days=30)
    cfg = {"meli": {"offers_path": str(pool_path)}}
    src = source_with(_no_network_handler, tmp_path)
    assert src.fetch_offers(cfg) == []
    assert "vencido" in src.pool_warning


def test_fetch_offers_pool_dentro_da_validade(tmp_path):
    pool_path = tmp_path / "meli_offers.json"
    write_pool(pool_path, [
        {"product_id": "MLB1", "title": "X", "price_ref_cents": 1000},
    ], generated_at=date.today() - timedelta(days=29), valid_days=30)
    cfg = {"meli": {"offers_path": str(pool_path)}}
    src = source_with(_no_network_handler, tmp_path)
    offers = src.fetch_offers(cfg)
    assert len(offers) == 1
    assert src.pool_warning is None


def test_fetch_offers_item_sem_price_ref_e_ignorado(tmp_path):
    pool_path = tmp_path / "meli_offers.json"
    write_pool(pool_path, [
        {"product_id": "MLB1", "title": "Sem preço"},
        {"product_id": "MLB2", "title": "Com preço", "price_ref_cents": 500},
    ])
    cfg = {"meli": {"offers_path": str(pool_path)}}
    offers = source_with(_no_network_handler, tmp_path).fetch_offers(cfg)
    assert [o.item_id for o in offers] == ["MLB2"]


def test_fetch_offers_usa_commission_pct_do_config(tmp_path):
    pool_path = tmp_path / "meli_offers.json"
    write_pool(pool_path, [
        {"product_id": "MLB1", "title": "X", "price_ref_cents": 1000},
    ])
    src = source_with(_no_network_handler, tmp_path)
    offers = src.fetch_offers({"meli": {"offers_path": str(pool_path), "commission_pct": 4.0}})
    assert offers[0].commission_pct == 4.0

    src2 = source_with(_no_network_handler, tmp_path)
    offers2 = src2.fetch_offers({"meli": {"offers_path": str(pool_path)}})
    assert offers2[0].commission_pct == 0.0


def test_fetch_offers_offers_path_default(tmp_path, monkeypatch):
    # Sem offers_path em cfg["meli"], usa data/meli_offers.json relativo ao
    # cwd — aqui trocamos o cwd para tmp_path para não tocar o repo real.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    write_pool(tmp_path / "data" / "meli_offers.json", [
        {"product_id": "MLB1", "title": "X", "price_ref_cents": 1000},
    ])
    offers = source_with(_no_network_handler, tmp_path).fetch_offers({"meli": {}})
    assert len(offers) == 1


# -- refresh_price (preço ao vivo, imediatamente antes de publicar) --------

def _make_offer_from_pool(tmp_path, product_id="MLB18725310", price_ref_cents=6890,
                          price_historic_min_cents=4792, handler=None):
    pool_path = tmp_path / "meli_offers.json"
    write_pool(pool_path, [
        {"product_id": product_id, "title": "Creatina 1kg", "price_ref_cents": price_ref_cents,
         "price_historic_min_cents": price_historic_min_cents},
    ])
    src = source_with(handler or _authed_handler, tmp_path)
    cfg = {"meli": {"offers_path": str(pool_path)}}
    offers = src.fetch_offers(cfg)
    return src, offers[0]


def test_refresh_price_usa_menor_preco_condicao_new(tmp_path):
    def handler(request: httpx.Request):
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "TOK", "expires_in": 21600})
        if request.url.path == "/products/MLB18725310/items":
            assert request.headers["authorization"] == "Bearer TOK"
            return httpx.Response(200, json={"results": [
                {"item_id": "A", "price": 68.90, "original_price": None, "condition": "new"},
                {"item_id": "B", "price": 62.50, "original_price": None, "condition": "new"},
                {"item_id": "C", "price": 10.00, "original_price": None, "condition": "used"},
            ]})
        raise AssertionError(f"caminho inesperado: {request.url.path}")

    src, offer = _make_offer_from_pool(tmp_path, price_historic_min_cents=100000, handler=handler)
    updated = src.refresh_price(offer)
    assert updated.price_current_cents == 6250  # menor entre os "new" (62.50), ignora "used"
    assert updated is not offer  # dataclass frozen -> nova instância
    assert offer.price_current_cents == 6890  # original não é mutado


def test_refresh_price_sem_preco_novo_levanta_source_error(tmp_path):
    def handler(request: httpx.Request):
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "TOK", "expires_in": 21600})
        if request.url.path.endswith("/items"):
            return httpx.Response(200, json={"results": [
                {"item_id": "A", "price": 10.00, "condition": "used"},
                {"item_id": "B", "price": None, "condition": "new"},
            ]})
        raise AssertionError(f"caminho inesperado: {request.url.path}")

    src, offer = _make_offer_from_pool(tmp_path, handler=handler)
    with pytest.raises(SourceError, match="sem preço ao vivo"):
        src.refresh_price(offer)


def test_refresh_price_nao_tem_mais_teto_proprio(tmp_path):
    # Fase 4: o teto do ML (max_above_historic_min) saiu — era ele que segurava
    # o volume do ML em ~11 de 38. Quem decide publicabilidade agora é
    # selection.max_above_ref + validate.check_price, igual para as duas lojas.
    def handler(request: httpx.Request):
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "TOK", "expires_in": 21600})
        if request.url.path.endswith("/items"):
            # piso 4792; antes 68.90 estourava o teto de 1.10 e virava SourceError
            return httpx.Response(200, json={"results": [
                {"item_id": "A", "price": 68.90, "condition": "new"},
            ]})
        raise AssertionError(f"caminho inesperado: {request.url.path}")

    src, offer = _make_offer_from_pool(
        tmp_path, price_ref_cents=6890, price_historic_min_cents=4792, handler=handler)
    updated = src.refresh_price(offer)
    assert updated.price_current_cents == 6890


def test_refresh_price_so_atualiza_o_preco_atual(tmp_path):
    def handler(request: httpx.Request):
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "TOK", "expires_in": 21600})
        if request.url.path.endswith("/items"):
            return httpx.Response(200, json={"results": [
                {"item_id": "A", "price": 59.90, "condition": "new"},
            ]})
        raise AssertionError(f"caminho inesperado: {request.url.path}")

    src, offer = _make_offer_from_pool(
        tmp_path, price_ref_cents=6890, price_historic_min_cents=4792, handler=handler)
    updated = src.refresh_price(offer)
    assert updated.price_current_cents == 5990
    assert updated.price_ref_cents == 6890     # a referência do pool sobrevive
    assert updated.price_floor_cents == 4792   # e o piso também
    assert updated.real_discount_pct == 13     # 68,90 -> 59,90


def test_refresh_price_preco_acima_da_referencia_e_barrado_depois(tmp_path):
    # refresh_price deixa passar; quem barra é a validação (a rede que pega a
    # oferta que encareceu entre a busca e a publicação).
    from afiliado import validate
    from afiliado.errors import ValidationError

    def handler(request: httpx.Request):
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "TOK", "expires_in": 21600})
        if request.url.path.endswith("/items"):
            return httpx.Response(200, json={"results": [
                {"item_id": "A", "price": 99.90, "condition": "new"},
            ]})
        raise AssertionError(f"caminho inesperado: {request.url.path}")

    src, offer = _make_offer_from_pool(
        tmp_path, price_ref_cents=6890, price_historic_min_cents=4792, handler=handler)
    updated = src.refresh_price(offer)
    cfg = {"selection": {"max_above_ref": 1.00, "price_min_brl": 20, "price_max_brl": 1000}}
    with pytest.raises(ValidationError, match="acima da referência"):
        validate.check_price(updated, cfg)


def test_fetch_offers_pula_entrada_sem_minima_historica(tmp_path):
    # Antes essas entradas eram aceitas e desligavam o piso em silêncio.
    pool_path = tmp_path / "meli_offers.json"
    write_pool(pool_path, [
        {"product_id": "MLB1", "title": "Sem piso", "price_ref_cents": 6890,
         "price_historic_min_cents": None},
        {"product_id": "MLB2", "title": "Piso zero", "price_ref_cents": 6890,
         "price_historic_min_cents": 0},
        {"product_id": "MLB3", "title": "Piso float", "price_ref_cents": 6890,
         "price_historic_min_cents": 4792.5},
        {"product_id": "MLB4", "title": "Piso ok", "price_ref_cents": 6890,
         "price_historic_min_cents": 4792},
    ])
    src = source_with(_no_network_handler, tmp_path)
    offers = src.fetch_offers({"meli": {"offers_path": str(pool_path)}})
    assert [o.item_id for o in offers] == ["MLB4"]
    assert "3 entrada(s) do pool ignorada(s)" in src.pool_warning


def test_refresh_price_erro_http_vira_source_error(tmp_path):
    def handler(request: httpx.Request):
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "TOK", "expires_in": 21600})
        return httpx.Response(500, text="erro interno")

    src, offer = _make_offer_from_pool(tmp_path, handler=handler)
    with pytest.raises(SourceError):
        src.refresh_price(offer)


# -- resolve_affiliate_link (pool pré-gerado, inalterado) -------------------

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

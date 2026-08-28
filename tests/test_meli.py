import json
from datetime import date, timedelta

import httpx
import pytest

from afiliado.errors import SourceError
from afiliado.sources.meli import MeliSource

SEL = {"price_min_brl": 20, "price_max_brl": 1000}


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
    """Formato novo do pool (fase 5B). Cada entrada precisa de
    `buy_box_item_id` e dos cinco campos de preço inteiros >= 0; para não
    repetir tudo nos testes que não se importam, o padrão aqui preenche o
    que falta a partir de `price_ref_cents` (p25 = mínima = ref, janelas 91 e
    365, buy box "BB-<id>"). Passe a chave explicitamente (inclusive
    ausente/inválida) para exercitar a rejeição.

    `price_ref_cents: 0` é a entrada SEM HISTÓRICO da fase 5J, e aí as janelas
    default também são 0 — os cinco campos vêm do mesmo degrau, e um default de
    91 dias produziria justamente a régua PARCIAL que o leitor rejeita."""
    generated_at = generated_at or date.today()
    entradas = []
    for offer in offers:
        if not isinstance(offer, dict):          # entrada malformada de propósito
            entradas.append(offer)
            continue
        item = dict(offer)
        if "price_ref_cents" in item:
            sem_historico = item["price_ref_cents"] == 0
            item.setdefault("price_p25_cents", item["price_ref_cents"])
            item.setdefault("price_historic_min_cents", item["price_ref_cents"])
            item.setdefault("price_window_days", 0 if sem_historico else 91)
            item.setdefault("price_min_window_days", 0 if sem_historico else 365)
            item.setdefault("buy_box_item_id", f"BB-{item.get('product_id')}")
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


# -- fetch_offers (pool curado, formato novo da fase 5B) --------------------

def _no_network_handler(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"fetch_offers não deveria chamar a rede: {request.url}")


def test_fetch_offers_le_pool_e_mapeia_campos(tmp_path):
    pool_path = tmp_path / "meli_offers.json"
    write_pool(pool_path, [
        {"product_id": "MLB18725310", "title": "Creatina 1kg Growth",
         "image_url": "https://http2.mlstatic.com/D_creatina.jpg", "category": "MLB264586",
         "buy_box_item_id": "MLB3928374651",
         "price_ref_cents": 2590, "price_p25_cents": 2428, "price_window_days": 91,
         "price_historic_min_cents": 1699, "price_min_window_days": 365,
         "sales": 13337, "rating": 4.8},
    ])
    cfg = {"meli": {"offers_path": str(pool_path), "commission_pct": 4.0}, "selection": SEL}
    src = source_with(_no_network_handler, tmp_path)
    offers = src.fetch_offers(cfg)
    assert len(offers) == 1 and src.pool_warning is None
    o = offers[0]
    assert o.source == "meli"
    assert o.item_id == "MLB18725310"
    assert o.title == "Creatina 1kg Growth"
    assert o.image_url == "https://http2.mlstatic.com/D_creatina.jpg"
    assert o.category == "MLB264586"
    assert o.price_current_cents == 2590
    assert o.price_original_cents == 2590  # sem desconto inflado (ver Mudança 3)
    assert o.price_ref_cents == 2590       # mediana da janela, do pool curado
    assert o.price_p25_cents == 2428
    assert o.price_window_days == 91
    assert o.price_floor_cents == 1699     # mínima histórica -> selo de menor preço
    assert o.price_floor_window_days == 365
    assert o.real_discount_pct == 0        # no preço típico: nada a alegar
    assert o.sales == 13337
    assert o.rating == 4.8
    assert o.commission_pct == 4.0
    assert o.product_url == "https://www.mercadolivre.com.br/p/MLB18725310"
    assert src._buy_box_ids == {"MLB18725310": "MLB3928374651"}


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
    # Pool de 29 dias (validade 30) com o buy box re-verificado esta semana:
    # as duas validades são independentes (a do buy box é de 7 dias).
    pool_path = tmp_path / "meli_offers.json"
    write_pool(pool_path, [
        {"product_id": "MLB1", "title": "X", "price_ref_cents": 1000,
         "buy_box_checked_at": date.today().isoformat()},
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
    src = source_with(_no_network_handler, tmp_path)
    offers = src.fetch_offers(cfg)
    assert [o.item_id for o in offers] == ["MLB2"]
    assert src.pool_warning == "1 entrada(s) do pool ignorada(s) (1 sem referência)"


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


# -- validação do pool na carga (C7d): cada regra pula a entrada certa -------

def _pool_com(tmp_path, entradas):
    pool_path = tmp_path / "meli_offers.json"
    write_pool(pool_path, entradas)
    src = source_with(_no_network_handler, tmp_path)
    offers = src.fetch_offers({"meli": {"offers_path": str(pool_path)}, "selection": SEL})
    return [o.item_id for o in offers], src.pool_warning


def _pool_com_nota(tmp_path, entradas):
    """Como `_pool_com`, mas devolve também a `pool_note` — o canal
    INFORMATIVO, separado do aviso desde a fase 5J."""
    pool_path = tmp_path / "meli_offers.json"
    write_pool(pool_path, entradas)
    src = source_with(_no_network_handler, tmp_path)
    offers = src.fetch_offers({"meli": {"offers_path": str(pool_path)}, "selection": SEL})
    return [o.item_id for o in offers], src.pool_warning, src.pool_note


def test_pool_pula_cada_campo_de_preco_ausente_ou_invalido(tmp_path):
    # Fase 5J: o ZERO deixou de ser "campo ausente" e passou a ter motivo
    # próprio quando é PARCIAL (P2, M2, J1 abaixo) — o resto (nulo, texto,
    # bool, negativo) continua caindo pelo motivo do campo.
    ok = {"product_id": "OK", "title": "ok", "price_ref_cents": 5000}
    ids, aviso = _pool_com(tmp_path, [
        ok,
        {**ok, "product_id": "P1", "price_p25_cents": None},
        {**ok, "product_id": "P2", "price_p25_cents": 0},
        {**ok, "product_id": "P3", "price_p25_cents": "R$ 45"},
        {**ok, "product_id": "P5", "price_p25_cents": True},
        {**ok, "product_id": "P6", "price_p25_cents": -1},
        {**ok, "product_id": "M1", "price_historic_min_cents": None},
        {**ok, "product_id": "M2", "price_historic_min_cents": 0},
        {**ok, "product_id": "J1", "price_window_days": 0},
        {**ok, "product_id": "J2", "price_min_window_days": None},
        {**ok, "product_id": "R1", "price_ref_cents": "R$ 50"},
    ])
    assert ids == ["OK"]
    assert aviso == ("10 entrada(s) do pool ignorada(s) "
                     "(4 sem p25, 3 régua parcial (uns campos de régua zerados, outros não), "
                     "1 sem janela da mínima, 1 sem mínima histórica, 1 sem referência)")


def test_pool_aceita_centavos_em_float_integral(tmp_path):
    # JSON não distingue 2590 de 2590.0 (planilha, dump de pandas, divisão em
    # Python): o float INTEGRAL É o inteiro e a entrada não pode morrer por
    # causa do ponto — antes ela caía como "sem referência", motivo que mandava
    # a curadoria procurar um campo que estava lá.
    pool_path = tmp_path / "meli_offers.json"
    write_pool(pool_path, [{"product_id": "F", "title": "t", "price_ref_cents": 5000.0,
                            "price_p25_cents": 4500.0, "price_historic_min_cents": 4000.0,
                            "price_window_days": 91.0, "price_min_window_days": 365.0}])
    src = source_with(_no_network_handler, tmp_path)
    (o,) = src.fetch_offers({"meli": {"offers_path": str(pool_path)}, "selection": SEL})
    assert src.pool_warning is None
    assert (o.price_ref_cents, o.price_p25_cents, o.price_window_days) == (5000, 4500, 91)
    assert (o.price_floor_cents, o.price_floor_window_days) == (4000, 365)
    # inteiros de verdade: o resto do código faz aritmética de centavos
    assert all(isinstance(v, int) for v in (o.price_ref_cents, o.price_p25_cents,
                                            o.price_current_cents, o.price_window_days,
                                            o.price_floor_cents, o.price_floor_window_days))


def test_pool_centavos_fracionados_dizem_nao_inteiro(tmp_path):
    # 4500,5 centavos não existe. O motivo tem de dizer isso — "sem p25" numa
    # entrada que TEM p25 manda a curadoria caçar o campo errado.
    ids, aviso = _pool_com(tmp_path, [
        {"product_id": "OK", "title": "t", "price_ref_cents": 5000},
        {"product_id": "P4", "title": "t", "price_ref_cents": 5000, "price_p25_cents": 4500.5},
        {"product_id": "M3", "title": "t", "price_ref_cents": 5000,
         "price_historic_min_cents": 4792.5},
        {"product_id": "R2", "title": "t", "price_ref_cents": 5000.7},
    ])
    assert ids == ["OK"]
    assert aviso == "3 entrada(s) do pool ignorada(s) (3 não inteiro)"


def test_pool_pula_referencia_fora_da_faixa_de_preco(tmp_path):
    # O item a R$ 19,90 do pool antigo era descartado em silêncio em TODO run
    # (price_min_brl: 20) — morto por construção. Agora a curadoria é
    # validada contra o config na carga, com o motivo no aviso.
    ids, aviso = _pool_com(tmp_path, [
        {"product_id": "BARATO", "title": "t", "price_ref_cents": 1990},
        {"product_id": "CARO", "title": "t", "price_ref_cents": 100001},
        {"product_id": "MIN", "title": "t", "price_ref_cents": 2000},
        {"product_id": "MAX", "title": "t", "price_ref_cents": 100000},
    ])
    assert ids == ["MIN", "MAX"]
    assert aviso == "2 entrada(s) do pool ignorada(s) (2 fora da faixa de preço)"


def test_pool_sem_selection_nao_checa_a_faixa(tmp_path):
    pool_path = tmp_path / "meli_offers.json"
    write_pool(pool_path, [{"product_id": "B", "title": "t", "price_ref_cents": 500}])
    src = source_with(_no_network_handler, tmp_path)
    assert len(src.fetch_offers({"meli": {"offers_path": str(pool_path)}})) == 1


def test_pool_pula_p25_acima_da_referencia_e_minima_acima_do_p25(tmp_path):
    ids, aviso = _pool_com(tmp_path, [
        {"product_id": "P25", "title": "t", "price_ref_cents": 5000, "price_p25_cents": 5001,
         "price_historic_min_cents": 4000},
        {"product_id": "MIN", "title": "t", "price_ref_cents": 5000, "price_p25_cents": 4500,
         "price_historic_min_cents": 4501},
        {"product_id": "IGUAIS", "title": "t", "price_ref_cents": 5000, "price_p25_cents": 5000,
         "price_historic_min_cents": 5000},
    ])
    assert ids == ["IGUAIS"]
    assert aviso == ("2 entrada(s) do pool ignorada(s) (1 mínima acima do p25, "
                     "1 p25 acima da referência)")


def test_pool_pula_entrada_sem_buy_box_e_id_repetido(tmp_path):
    ids, aviso = _pool_com(tmp_path, [
        {"product_id": "A", "title": "t", "price_ref_cents": 5000, "buy_box_item_id": ""},
        {"product_id": "B", "title": "t", "price_ref_cents": 5000, "buy_box_item_id": None},
        {"product_id": "C", "title": "t", "price_ref_cents": 5000},
        {"product_id": "C", "title": "t", "price_ref_cents": 5000},
        {"product_id": "", "title": "t", "price_ref_cents": 5000},
        "não é objeto",
    ])
    assert ids == ["C"]
    assert aviso == ("5 entrada(s) do pool ignorada(s) (2 sem buy box, 1 entrada não é objeto, "
                     "1 id repetido, 1 sem id ou título)")


def test_pool_aviso_no_formato_do_brief(tmp_path):
    ids, aviso = _pool_com(tmp_path, [
        {"product_id": "A", "title": "t", "price_ref_cents": 1000},
        {"product_id": "B", "title": "t", "price_ref_cents": 999999},
        {"product_id": "C", "title": "t", "price_ref_cents": 5000, "price_p25_cents": None},
        {"product_id": "D", "title": "t", "price_ref_cents": 5000},
    ])
    assert ids == ["D"]
    assert aviso == "3 entrada(s) do pool ignorada(s) (2 fora da faixa de preço, 1 sem p25)"


def test_pool_antigo_sem_p25_e_rejeitado_inteiro(tmp_path):
    # O formato anterior (foto de um dia, sem p25/janela/buy box) não passa.
    ids, aviso = _pool_com(tmp_path, [
        {"product_id": "MLB66637233", "title": "Creatina", "price_ref_cents": 7890,
         "price_historic_min_cents": 3051, "price_p25_cents": None,
         "price_window_days": None, "price_min_window_days": None, "buy_box_item_id": None},
    ])
    assert ids == []
    assert aviso == "1 entrada(s) do pool ignorada(s) (1 sem p25)"


# -- buy box que envelhece (rodada de correção da 5B, Fix 1 — caminho B) ----
# Verificado ao vivo em 2026-08-26, 3 produtos: a ordem de /products/{id}/items
# bateu com a página em 2 de 3 (no 3º a página mostrava results[1]) e o
# anúncio do pool de um deles já tinha sumido da lista. Nem a API nem o pool
# reproduzem a página com certeza; o que o loader garante é a IDADE da
# verificação do buy box — 7 dias.

def test_pool_buy_box_verificado_ha_mais_de_7_dias_e_ignorado_com_os_dias_no_motivo(tmp_path):
    hoje = date.today()
    ids, aviso = _pool_com(tmp_path, [
        {"product_id": "FRESCO", "title": "t", "price_ref_cents": 5000,
         "buy_box_checked_at": (hoje - timedelta(days=7)).isoformat()},
        {"product_id": "HOJE", "title": "t", "price_ref_cents": 5000,
         "buy_box_checked_at": hoje.isoformat()},
        {"product_id": "VELHO", "title": "t", "price_ref_cents": 5000,
         "buy_box_checked_at": (hoje - timedelta(days=8)).isoformat()},
        {"product_id": "MUITO-VELHO", "title": "t", "price_ref_cents": 5000,
         "buy_box_checked_at": (hoje - timedelta(days=30)).isoformat()},
    ])
    assert ids == ["FRESCO", "HOJE"]
    assert aviso == ("2 entrada(s) do pool ignorada(s) (1 buy box não verificado há 30 dias, "
                     "1 buy box não verificado há 8 dias)")


def test_pool_sem_buy_box_checked_at_usa_a_data_de_geracao(tmp_path):
    # Gerar o pool É uma verificação (o Passo 1 do skill devolve o buyBoxId):
    # sem o campo, vale a data de geração — e envelhece junto com ela, mesmo
    # com o pool dentro dos 30 dias de validade.
    pool_path = tmp_path / "meli_offers.json"
    cfg = {"meli": {"offers_path": str(pool_path)}, "selection": SEL}
    write_pool(pool_path, [{"product_id": "A", "title": "t", "price_ref_cents": 5000}],
               generated_at=date.today() - timedelta(days=8))
    src = source_with(_no_network_handler, tmp_path)
    assert src.fetch_offers(cfg) == []
    assert src.pool_warning == "1 entrada(s) do pool ignorada(s) (1 buy box não verificado há 8 dias)"

    write_pool(pool_path, [{"product_id": "A", "title": "t", "price_ref_cents": 5000}],
               generated_at=date.today() - timedelta(days=7))
    src = source_with(_no_network_handler, tmp_path)
    assert len(src.fetch_offers(cfg)) == 1
    assert src.pool_warning is None


def test_pool_buy_box_checked_at_invalido_ou_no_futuro_e_ignorado(tmp_path):
    ids, aviso = _pool_com(tmp_path, [
        {"product_id": "A", "title": "t", "price_ref_cents": 5000, "buy_box_checked_at": "ontem"},
        {"product_id": "B", "title": "t", "price_ref_cents": 5000, "buy_box_checked_at": 20260826},
        {"product_id": "C", "title": "t", "price_ref_cents": 5000, "buy_box_checked_at": ""},
        {"product_id": "D", "title": "t", "price_ref_cents": 5000,
         "buy_box_checked_at": (date.today() + timedelta(days=1)).isoformat()},
    ])
    assert ids == []
    assert aviso == "4 entrada(s) do pool ignorada(s) (4 data do buy box inválida)"


def test_pool_validade_do_buy_box_e_de_7_dias():
    from afiliado.sources.meli import BUY_BOX_MAX_AGE_DAYS
    assert BUY_BOX_MAX_AGE_DAYS == 7


# -- fase 5J: a entrada SEM HISTÓRICO (os cinco campos de régua zerados) -----
# O histórico de preço custa 4 consultas do JoomPulse a cada 28 produtos contra
# 1 a cada 50 do resto: é 15x todo o resto junto, e é ele que impedia o pool de
# crescer. Ele não é necessário para PUBLICAR — `pricing.enrich_offers` já
# prevê referência 0 ("a oferta continua publicável, sem alegar desconto") e
# `pricing.verdict` só dá modo A com janela >= 14 dias. Quem barrava era só o
# leitor.

SEM_HISTORICO = {"price_ref_cents": 0, "price_p25_cents": 0, "price_window_days": 0,
                 "price_historic_min_cents": 0, "price_min_window_days": 0}


def test_pool_aceita_a_entrada_com_a_regua_toda_zerada(tmp_path):
    pool_path = tmp_path / "meli_offers.json"
    write_pool(pool_path, [{"product_id": "NOVO", "title": "Creatina", **SEM_HISTORICO,
                            "image_url": "https://x/i.jpg", "category": "MLB264586",
                            "sales": 13337, "rating": 4.8}])
    src = source_with(_no_network_handler, tmp_path)
    (o,) = src.fetch_offers({"meli": {"offers_path": str(pool_path)}, "selection": SEL})
    # A régua fica zerada — é o que `enrich_offers` chama de "desconhecida".
    assert (o.price_ref_cents, o.price_p25_cents, o.price_window_days) == (0, 0, 0)
    assert (o.price_floor_cents, o.price_floor_window_days) == (0, 0)
    # ...e tudo que NÃO é régua continua chegando: é isso que o Passo 2 do
    # skill compra por 1 consulta a cada 50 produtos.
    assert (o.title, o.category, o.sales, o.rating) == ("Creatina", "MLB264586", 13337, 4.8)
    assert src._buy_box_ids == {"NOVO": "BB-NOVO"}


def test_pool_campo_de_regua_AUSENTE_continua_sendo_erro(tmp_path):
    """O que se aceita é o zero EXPLÍCITO. Chave faltando é typo de curadoria:
    foi assim que as cinco variantes de zero silencioso foram pegas, e um pool
    com typo não pode passar a valer só porque o resto veio zerado."""
    from afiliado.sources.meli import CAMPOS_DE_PRECO
    pool_path = tmp_path / "meli_offers.json"
    cfg = {"meli": {"offers_path": str(pool_path)}, "selection": SEL}
    for campo, motivo in CAMPOS_DE_PRECO:
        entrada = {"product_id": "P", "title": "t", **SEM_HISTORICO,
                   "buy_box_item_id": "BB"}
        del entrada[campo]
        # JSON cru, sem os defaults de `write_pool`: o que se testa aqui é a
        # chave FALTANDO, e o helper a preencheria de volta.
        pool_path.write_text(json.dumps({"generated_at": date.today().isoformat(),
                                         "valid_days": 30, "offers": [entrada]}),
                             encoding="utf-8")
        src = source_with(_no_network_handler, tmp_path)
        assert src.fetch_offers(cfg) == [], f"{campo} ausente passou"
        assert src.pool_warning == f"1 entrada(s) do pool ignorada(s) (1 {motivo})"


def test_pool_regua_PARCIALMENTE_zerada_e_erro_com_motivo_proprio(tmp_path):
    """`ref > 0` com `p25 = 0` é curadoria quebrada, não "sem histórico": o
    trio ref/p25/janela sai sempre do mesmo degrau. Motivo próprio para não
    confundir com o caso novo — e para a curadoria saber o que consertar."""
    ids, aviso = _pool_com(tmp_path, [
        {"product_id": "P25", "title": "t", **SEM_HISTORICO, "price_ref_cents": 5000},
        {"product_id": "JANELA", "title": "t", "price_ref_cents": 5000,
         "price_p25_cents": 4500, "price_historic_min_cents": 4000,
         "price_window_days": 0, "price_min_window_days": 365},
        {"product_id": "PISO", "title": "t", "price_ref_cents": 5000,
         "price_p25_cents": 4500, "price_historic_min_cents": 0,
         "price_window_days": 91, "price_min_window_days": 0},
        {"product_id": "ZERADA", "title": "t", **SEM_HISTORICO},
    ])
    assert ids == ["ZERADA"]
    assert aviso.startswith("3 entrada(s) do pool ignorada(s) (3 régua parcial")
    assert "régua parcial" in aviso


def test_pool_sem_historico_nao_e_barrado_pela_faixa_de_preco(tmp_path):
    """A faixa é `price_ref_cents / 100` e não tem como rodar sem referência.
    Verificado: quem barra por preço VIVO é `validate.check_price`, DEPOIS do
    `refresh_price` — a mesma faixa, sobre o preço que vai ao post. Então aqui
    a checagem é pulada, e o aviso diz isso."""
    ids, aviso, nota = _pool_com_nota(tmp_path, [
        {"product_id": "SEM", "title": "t", **SEM_HISTORICO},
        {"product_id": "CARO", "title": "t", "price_ref_cents": 100001},
    ])
    assert ids == ["SEM"]                      # a de R$ 3.000 COM régua cai
    assert aviso == "1 entrada(s) do pool ignorada(s) (1 fora da faixa de preço)"
    assert "1 entrada(s) sem histórico" in nota
    assert "preço VIVO" in nota


def test_pool_so_com_entradas_sem_historico_nao_gera_aviso_nenhum(tmp_path):
    """A separação entre `pool_warning` e `pool_note`: pool inteiro sem
    histórico é estado SAUDÁVEL — nada foi ignorado, e o doctor tem de mostrar
    ✅. Um ⚠️ aceso todo dia deixa de ser lido, e a entrada silenciosamente
    ignorada se esconderia atrás dele."""
    ids, aviso, nota = _pool_com_nota(tmp_path, [
        {"product_id": "A", "title": "t", **SEM_HISTORICO},
        {"product_id": "B", "title": "t", **SEM_HISTORICO},
    ])
    assert ids == ["A", "B"]
    assert aviso is None
    assert nota.startswith("2 entrada(s) sem histórico")
    assert "modo B" in nota


def test_pool_note_nao_sobrevive_a_uma_leitura_que_nem_chegou_ao_pool(tmp_path):
    """Retorno antecipado (pool ausente) tem de limpar a nota da leitura
    anterior — senão ela descreve um pool que não foi lido."""
    src = source_with(_no_network_handler, tmp_path)
    pool_path = tmp_path / "meli_offers.json"
    write_pool(pool_path, [{"product_id": "A", "title": "t", **SEM_HISTORICO}])
    cfg = {"meli": {"offers_path": str(pool_path)}, "selection": SEL}
    assert src.fetch_offers(cfg) and src.pool_note

    cfg["meli"]["offers_path"] = str(tmp_path / "nao_existe.json")
    assert src.fetch_offers(cfg) == []
    assert src.pool_note is None
    assert "pool ausente ou inválido" in src.pool_warning


def test_ruler_coverage_conta_quantas_entradas_tem_regua_curada(tmp_path):
    """J4: sem este número, "o ML só publica modo B" vira descoberta de semanas
    depois — e o ponto da fase é que a proporção mude sozinha com o tempo."""
    pool_path = tmp_path / "meli_offers.json"
    write_pool(pool_path, [
        {"product_id": "A", "title": "t", "price_ref_cents": 5000},
        {"product_id": "B", "title": "t", **SEM_HISTORICO},
        {"product_id": "C", "title": "t", **SEM_HISTORICO},
    ])
    src = source_with(_no_network_handler, tmp_path)
    offers = src.fetch_offers({"meli": {"offers_path": str(pool_path)}, "selection": SEL})
    assert src.ruler_coverage(offers) == (1, 3)
    assert src.ruler_coverage([]) == (0, 0)


# -- refresh_price: preço vivo = buy box (C7b) ------------------------------

ITENS = [
    {"item_id": "MLB7210468412", "price": 58.90, "original_price": None, "condition": "new"},
    {"item_id": "MLB4555189589", "price": 78.90, "original_price": 104.9, "condition": "new"},
    {"item_id": "MLB7125449388", "price": 104.90, "original_price": None, "condition": "new"},
]


def _make_offer_from_pool(tmp_path, product_id="MLB66637233", price_ref_cents=10490,
                          price_historic_min_cents=9990, buy_box_item_id="MLB7125449388",
                          handler=None):
    pool_path = tmp_path / "meli_offers.json"
    write_pool(pool_path, [
        {"product_id": product_id, "title": "Creatina 500g", "price_ref_cents": price_ref_cents,
         "price_p25_cents": min(price_ref_cents, 9990),
         "price_historic_min_cents": price_historic_min_cents,
         "buy_box_item_id": buy_box_item_id},
    ])
    src = source_with(handler or _authed_handler, tmp_path)
    cfg = {"meli": {"offers_path": str(pool_path)}}
    offers = src.fetch_offers(cfg)
    assert offers, src.pool_warning
    return src, offers[0]


def _items_handler(results, paging=None):
    def handler(request: httpx.Request):
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "TOK", "expires_in": 21600})
        if request.url.path == "/products/MLB66637233/items":
            assert request.headers["authorization"] == "Bearer TOK"
            return httpx.Response(200, json={"results": results, "paging": paging or {
                "total": len(results), "offset": 0, "limit": 100}})
        raise AssertionError(f"caminho inesperado: {request.url.path}")
    return handler


def test_refresh_price_usa_o_buy_box_nunca_o_menor_entre_vendedores(tmp_path):
    # Teste obrigatório 9. Caso real (2026-08-26): 37 vendedores, o mais
    # barato a R$ 58,90 e o buy box (MLB7125449388) a R$ 104,90 — o post
    # dizia 58,90 e o clique mostrava 104,90.
    src, offer = _make_offer_from_pool(tmp_path, handler=_items_handler(ITENS))
    updated = src.refresh_price(offer)
    assert updated.price_current_cents == 10490
    assert updated is not offer  # dataclass frozen -> nova instância
    assert offer.price_current_cents == 10490  # original não é mutado
    assert updated.price_ref_cents == 10490 and updated.price_floor_cents == 9990


def test_refresh_price_sem_buy_box_na_lista_levanta_source_error(tmp_path):
    # O anúncio vencedor do pool não está entre os vendedores: NUNCA cair
    # para o mínimo (58,90) — a oferta é descartada.
    sem_vencedor = [r for r in ITENS if r["item_id"] != "MLB7125449388"]
    src, offer = _make_offer_from_pool(tmp_path, handler=_items_handler(sem_vencedor))
    with pytest.raises(SourceError, match="sem buy box"):
        src.refresh_price(offer)


def test_refresh_price_buy_box_sem_preco_levanta_source_error(tmp_path):
    itens = [{"item_id": "MLB7125449388", "price": None, "condition": "new"}, ITENS[0]]
    src, offer = _make_offer_from_pool(tmp_path, handler=_items_handler(itens))
    with pytest.raises(SourceError, match="sem preço"):
        src.refresh_price(offer)


def test_refresh_price_lista_vazia_levanta_source_error(tmp_path):
    src, offer = _make_offer_from_pool(tmp_path, handler=_items_handler([]))
    with pytest.raises(SourceError, match="sem buy box"):
        src.refresh_price(offer)


def test_refresh_price_oferta_desconhecida_da_fonte_levanta_source_error(tmp_path):
    from tests.test_models import make_offer
    src = source_with(_authed_handler, tmp_path)
    with pytest.raises(SourceError, match="sem buy box conhecido"):
        src.refresh_price(make_offer(source="meli", item_id="MLB999"))


def test_refresh_price_pagina_ate_achar_o_buy_box(tmp_path):
    paginas = {0: [{"item_id": f"X{i}", "price": 10.0, "condition": "new"} for i in range(100)],
               100: [ITENS[0], ITENS[2]]}
    chamadas = []

    def handler(request: httpx.Request):
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "TOK", "expires_in": 21600})
        offset = int(request.url.params.get("offset", 0))
        chamadas.append(offset)
        return httpx.Response(200, json={"results": paginas[offset],
                                         "paging": {"total": 102, "offset": offset, "limit": 100}})

    src, offer = _make_offer_from_pool(tmp_path, handler=handler)
    assert src.refresh_price(offer).price_current_cents == 10490
    assert chamadas == [0, 100]


def test_refresh_price_preco_acima_da_referencia_e_barrado_depois(tmp_path):
    # refresh_price deixa passar; quem barra é a validação (a rede que pega a
    # oferta que encareceu entre a busca e a publicação).
    from afiliado import validate
    from afiliado.errors import ValidationError

    itens = [{"item_id": "MLB7125449388", "price": 129.90, "condition": "new"}]
    src, offer = _make_offer_from_pool(tmp_path, handler=_items_handler(itens))
    updated = src.refresh_price(offer)
    assert updated.price_current_cents == 12990
    cfg = {"selection": {"max_above_ref": 1.00, "price_min_brl": 20, "price_max_brl": 1000}}
    with pytest.raises(ValidationError, match="acima da referência"):
        validate.check_price(updated, cfg)


def test_refresh_price_erro_http_vira_source_error(tmp_path):
    def handler(request: httpx.Request):
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "TOK", "expires_in": 21600})
        return httpx.Response(500, text="erro interno")

    src, offer = _make_offer_from_pool(tmp_path, handler=handler)
    with pytest.raises(SourceError):
        src.refresh_price(offer)


def test_meli_nao_grava_o_preco_do_pool_como_observacao():
    # C7c: o preço com que a oferta sai do pool é a mediana, não uma
    # observação — o pipeline lê este atributo e só grava o preço vivo.
    assert MeliSource.observes_price_on_discovery is False


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


# -- Fase 5C (M5/A6): cobertura do pool de links ------------------------------

def test_link_coverage_conta_quantos_produtos_tem_link(tmp_path):
    from tests.test_models import make_offer
    links = tmp_path / "links.json"
    links.write_text(json.dumps({"MLB1": "https://meli.la/a", "MLB3": ""}),
                     encoding="utf-8")
    src = source_with(_authed_handler, tmp_path, links_path=links)
    ofertas = [make_offer(source="meli", item_id=f"MLB{n}") for n in (1, 2, 3)]
    assert src.links_file_exists
    assert src.link_coverage(ofertas) == (1, 3)      # link vazio não conta
    assert src.link_coverage([]) == (0, 0)


def test_link_coverage_com_arquivo_ausente(tmp_path):
    from tests.test_models import make_offer
    src = source_with(_authed_handler, tmp_path, links_path=tmp_path / "nao-existe.json")
    assert not src.links_file_exists
    assert src.link_coverage([make_offer(source="meli", item_id="MLB1")]) == (0, 1)

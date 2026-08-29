import dataclasses
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
    # A mediana do pool vira o preço ESTIMADO com que a oferta entra na fila
    # (o preço de verdade só existe depois do `refresh_price`).
    assert o.price_current_cents == 2590
    assert o.price_original_cents == 2590  # sem desconto inflado (ver Mudança 3)
    # ...mas a RÉGUA não viaja mais na oferta: fase 5M, ver
    # `test_a_oferta_do_meli_nasce_sem_regua` logo abaixo.
    assert o.price_ref_cents == 0
    assert o.price_p25_cents == 0
    assert o.price_window_days == 0
    assert o.price_floor_cents == 0
    assert o.price_floor_window_days == 0
    assert o.real_discount_pct == 0        # sem referência: nada a alegar
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
    assert o.price_current_cents == 5000
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


def test_pool_pula_id_repetido_e_entrada_malformada(tmp_path):
    ids, aviso = _pool_com(tmp_path, [
        {"product_id": "C", "title": "t", "price_ref_cents": 5000},
        {"product_id": "C", "title": "t", "price_ref_cents": 5000},
        {"product_id": "", "title": "t", "price_ref_cents": 5000},
        "não é objeto",
    ])
    assert ids == ["C"]
    assert aviso == ("3 entrada(s) do pool ignorada(s) (1 entrada não é objeto, "
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


# -- fase 5M: o buy box saiu do leitor --------------------------------------
# A validade de 7 dias do `buy_box_checked_at` existia para proteger UMA
# premissa: o preço publicado era o do anúncio do buy box, e esse anúncio
# envelhece. A premissa caiu — o preço agora é o do anúncio linkado mais
# barato, lido ao vivo. Manter a validade só faria o pool inteiro parar de
# publicar 7 dias depois de cada refresh, por um campo que ninguém lê.


def test_pool_nao_exige_mais_buy_box_nem_a_data_dele(tmp_path):
    ids, aviso = _pool_com(tmp_path, [
        {"product_id": "SEM-BB", "title": "t", "price_ref_cents": 5000,
         "buy_box_item_id": None},
        {"product_id": "BB-VELHO", "title": "t", "price_ref_cents": 5000,
         "buy_box_checked_at": (date.today() - timedelta(days=90)).isoformat()},
        {"product_id": "BB-TORTO", "title": "t", "price_ref_cents": 5000,
         "buy_box_checked_at": "ontem"},
    ])
    assert ids == ["SEM-BB", "BB-VELHO", "BB-TORTO"]
    assert aviso is None


def test_pool_antigo_de_30_dias_ainda_publica(tmp_path):
    """Antes da 5M um pool gerado há 8 dias vinha VAZIO (o buy box vencia em 7)
    e o ML parava sozinho entre um `/meli-pool-refresh` e outro. A validade que
    resta é a do arquivo (`valid_days`, 30 dias)."""
    pool_path = tmp_path / "meli_offers.json"
    cfg = {"meli": {"offers_path": str(pool_path)}, "selection": SEL}
    write_pool(pool_path, [{"product_id": "A", "title": "t", "price_ref_cents": 5000}],
               generated_at=date.today() - timedelta(days=29), valid_days=30)
    src = source_with(_no_network_handler, tmp_path)
    assert len(src.fetch_offers(cfg)) == 1
    assert src.pool_warning is None


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
    assert "1 oferta(s) do ML nascem SEM RÉGUA" in nota


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
    assert nota.startswith("2 oferta(s) do ML nascem SEM RÉGUA")
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


def test_ruler_coverage_e_zero_por_construcao_desde_a_5M(tmp_path):
    """J4 + M4: o número existe para que "o ML só publica modo B" não vire
    descoberta de semanas depois. Desde a 5M ele é ZERO por desenho — a régua
    curada é de OUTRO anúncio —, e a régua própria (price_log) só aparece
    depois do `enrich_offers`, que roda adiante."""
    pool_path = tmp_path / "meli_offers.json"
    write_pool(pool_path, [
        {"product_id": "A", "title": "t", "price_ref_cents": 5000},
        {"product_id": "B", "title": "t", **SEM_HISTORICO},
        {"product_id": "C", "title": "t", **SEM_HISTORICO},
    ])
    src = source_with(_no_network_handler, tmp_path)
    offers = src.fetch_offers({"meli": {"offers_path": str(pool_path)}, "selection": SEL})
    assert src.ruler_coverage(offers) == (0, 3)
    assert src.ruler_coverage([]) == (0, 0)


# -- fase 5M (M4): a régua do pool é de OUTRO anúncio -------------------------

def test_a_oferta_do_meli_nasce_sem_regua(tmp_path):
    """A régua curada (mediana/p25/janela/mínima) é do anúncio que vencia o buy
    box; o preço publicado é o do anúncio linkado mais barato — outro vendedor.
    Selar "menor preço dos últimos 12 meses (verificado)" comparando o preço de
    A com a mínima de B é mentira, então a oferta nasce sem régua e publica em
    modo B."""
    from afiliado import pricing

    pool_path = tmp_path / "meli_offers.json"
    write_pool(pool_path, [
        {"product_id": "MLB1", "title": "Creatina", "price_ref_cents": 10490,
         "price_p25_cents": 9990, "price_window_days": 91,
         "price_historic_min_cents": 8990, "price_min_window_days": 365},
    ])
    src = source_with(_no_network_handler, tmp_path)
    (o,) = src.fetch_offers({"meli": {"offers_path": str(pool_path)}, "selection": SEL})
    assert (o.price_ref_cents, o.price_p25_cents, o.price_window_days) == (0, 0, 0)
    assert (o.price_floor_cents, o.price_floor_window_days) == (0, 0)
    # ...e o veredito que sai daí não alega desconto nem sela nada.
    veredito = pricing.verdict(dataclasses.replace(o, price_current_cents=7890), 10)
    assert veredito.mode == "B"
    assert veredito.discount_pct == 0 and veredito.seal == ""


def test_a_mediana_do_pool_ainda_serve_para_ranquear(tmp_path):
    """Zerar a régua não pode zerar o preço: sem preço o `ev_score` é 0 e a
    oferta cai para o fim da fila (ou morre no `min_ev_brl`). A mediana do pool
    continua sendo a ESTIMATIVA com que a oferta entra na fila — o preço de
    verdade só existe depois do `refresh_price`."""
    pool_path = tmp_path / "meli_offers.json"
    write_pool(pool_path, [{"product_id": "MLB1", "title": "t", "price_ref_cents": 10490}])
    src = source_with(_no_network_handler, tmp_path)
    (o,) = src.fetch_offers({"meli": {"offers_path": str(pool_path)}, "selection": SEL})
    assert o.price_current_cents == 10490
    assert o.price_original_cents == 10490      # e sem "de" inventado
    assert o.discount_pct == 0


# -- fase 5M: o preço publicado é o do ANÚNCIO que o nosso link abre ---------
#
# O `buy_box_item_id` do pool não era o vencedor do buy box — era só UM
# vendedor, e nos dois stories errados de 2026-08-28 um caro (R$ 80,00 num
# produto cuja página mostrava R$ 39,90; R$ 209,87 num de R$ 113). O vencedor
# não é obtível: `/products/{id}` devolve `buy_box_winner: null` e o campo
# `tier` de `/products/{id}/items` veio vazio nos 89 anúncios sondados.
#
# A saída não é adivinhar melhor: é publicar o preço do anúncio que o NOSSO
# link abre. Aí o número do post e o número que o seguidor vê são o mesmo
# objeto, por construção.

PRODUTO = "MLB66637233"


def _anuncio(item_id: str, price, **extra) -> dict:
    """Um anúncio como `/products/{id}/items` devolve — campos e valores
    medidos ao vivo em 2026-08-28 (1717 anúncios de 53 produtos do pool).
    O default passa em qualquer piso de qualidade; os testes de piso pedem o
    contrário explicitamente."""
    item = {"item_id": item_id, "price": price, "original_price": None,
            "condition": "new", "listing_type_id": "gold_special",
            "official_store_id": None, "tier": "", "inventory_id": "",
            "tags": ["kvs_primary", "immediate_payment"],
            "shipping": {"free_shipping": True, "mode": "me2",
                         "logistic_type": "fulfillment", "cost": 0}}
    item.update(extra)
    return item


def write_links(path, produtos: dict[str, dict[str, str]], product_links=None):
    """Pool de links no formato da fase 5M: produto -> {anúncio: link}."""
    from afiliado.meli_links import escrever_pool
    escrever_pool(path, {pid: {"items": itens,
                               "product_link": (product_links or {}).get(pid, "")}
                         for pid, itens in produtos.items()}, tag="ofiscaldapromo")
    return path


def _fonte(tmp_path, handler, linkados: dict[str, str] | None = None,
           product_id=PRODUTO, price_ref_cents=10490):
    """(fonte, oferta) prontas: pool curado de um produto + pool de links."""
    pool_path = tmp_path / "meli_offers.json"
    write_pool(pool_path, [
        {"product_id": product_id, "title": "Creatina 500g",
         "price_ref_cents": price_ref_cents, "price_p25_cents": min(price_ref_cents, 9990),
         "price_historic_min_cents": min(price_ref_cents, 9990)},
    ])
    links_path = tmp_path / "meli_links.json"
    write_links(links_path, {product_id: linkados if linkados is not None
                             else {"MLB4555189589": "https://meli.la/link-do-4555",
                                   "MLB7125449388": "https://meli.la/link-do-7125"}})
    src = source_with(handler, tmp_path, links_path=links_path)
    offers = src.fetch_offers({"meli": {"offers_path": str(pool_path)}})
    assert offers, src.pool_warning
    return src, offers[0]


def _items_handler(results, paging=None):
    def handler(request: httpx.Request):
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "TOK", "expires_in": 21600})
        if request.url.path == f"/products/{PRODUTO}/items":
            assert request.headers["authorization"] == "Bearer TOK"
            return httpx.Response(200, json={"results": results, "paging": paging or {
                "total": len(results), "offset": 0, "limit": 100}})
        raise AssertionError(f"caminho inesperado: {request.url.path}")
    return handler


# Caso real (2026-08-26/28): o mais barato da lista está a R$ 58,90, o anúncio
# que o pool chamava de buy box custa R$ 104,90, e temos link para dois deles.
ITENS = [
    _anuncio("MLB7210468412", 58.90),                       # o mais barato — SEM link
    _anuncio("MLB4555189589", 78.90, original_price=104.9),  # linkado
    _anuncio("MLB7125449388", 104.90),                       # linkado (o "buy box" do pool)
]


def test_refresh_price_publica_o_mais_barato_ENTRE_OS_LINKADOS(tmp_path):
    src, offer = _fonte(tmp_path, _items_handler(ITENS))
    updated = src.refresh_price(offer)
    assert updated.price_current_cents == 7890          # não 5890 (sem link)
    assert updated.anuncio_id == "MLB4555189589"        # e o post sabe de quem é o preço
    assert updated is not offer                          # dataclass frozen -> nova instância
    assert offer.anuncio_id == ""                        # o original não é mutado


def test_refresh_price_nunca_publica_o_preco_de_um_anuncio_sem_link(tmp_path):
    """A garantia inteira da fase: publicar o preço de um anúncio que o nosso
    link NÃO abre é reintroduzir o bug — o seguidor chega em outro vendedor."""
    src, offer = _fonte(tmp_path, _items_handler(ITENS),
                        linkados={"MLB7125449388": "https://meli.la/link-do-7125"})
    assert src.refresh_price(offer).price_current_cents == 10490   # nem 5890, nem 7890


def test_refresh_price_ignora_o_buy_box_do_pool(tmp_path):
    """O `buy_box_item_id` deixou de ser lido: ele não era o vencedor, e o
    vencedor não é obtível. O pool destes testes aponta para `BB-MLB66637233`,
    que NÃO está entre os vendedores — antes da 5M isso descartava a oferta
    inteira ("sem buy box"); agora nem é consultado."""
    src, offer = _fonte(tmp_path, _items_handler(ITENS))
    publicada = src.refresh_price(offer)
    assert publicada.anuncio_id == "MLB4555189589"
    assert not hasattr(src, "_buy_box_ids")   # o mapa morreu junto com a premissa


def test_refresh_price_sem_anuncio_linkado_na_lista_viva_levanta(tmp_path):
    """Os anúncios linkados sumiram da lista (medido: 1 de 35 some em 2 dias).
    A oferta é DESCARTADA — nunca cai para o mais barato sem link."""
    sumiram = [ITENS[0], _anuncio("MLB9999999999", 61.0)]
    src, offer = _fonte(tmp_path, _items_handler(sumiram))
    with pytest.raises(SourceError, match="nenhum anúncio linkado"):
        src.refresh_price(offer)


def test_refresh_price_sem_link_nenhum_para_o_produto_levanta(tmp_path):
    src, offer = _fonte(tmp_path, _items_handler(ITENS), linkados={})
    with pytest.raises(SourceError, match="sem link de anúncio"):
        src.refresh_price(offer)


def test_refresh_price_anuncio_linkado_sem_preco_e_pulado(tmp_path):
    itens = [_anuncio("MLB4555189589", None), _anuncio("MLB7125449388", 104.90), ITENS[0]]
    src, offer = _fonte(tmp_path, _items_handler(itens))
    assert src.refresh_price(offer).price_current_cents == 10490

    src, offer = _fonte(tmp_path, _items_handler([_anuncio("MLB4555189589", None)]))
    with pytest.raises(SourceError, match="nenhum anúncio linkado"):
        src.refresh_price(offer)


def test_refresh_price_lista_vazia_levanta_source_error(tmp_path):
    src, offer = _fonte(tmp_path, _items_handler([]))
    with pytest.raises(SourceError, match="nenhum anúncio linkado"):
        src.refresh_price(offer)


def test_refresh_price_zera_o_de_do_vendedor(tmp_path):
    """`price_original_cents` continua sendo o preço vivo: o ML não expõe "de"
    de vendedor (`original_price` é nulo em 1502 dos 1717 anúncios medidos) e
    deixar lá a mediana do pool inventaria um desconto — o `flagrante` lê esse
    campo como acusação."""
    src, offer = _fonte(tmp_path, _items_handler(ITENS))
    updated = src.refresh_price(offer)
    assert updated.price_original_cents == updated.price_current_cents == 7890
    assert updated.discount_pct == 0


def test_refresh_price_pagina_ate_achar_os_linkados(tmp_path):
    paginas = {0: [_anuncio(f"X{i}", 10.0) for i in range(100)],
               100: [ITENS[0], ITENS[2]]}
    chamadas = []

    def handler(request: httpx.Request):
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "TOK", "expires_in": 21600})
        offset = int(request.url.params.get("offset", 0))
        chamadas.append(offset)
        return httpx.Response(200, json={"results": paginas[offset],
                                         "paging": {"total": 102, "offset": offset, "limit": 100}})

    src, offer = _fonte(tmp_path, handler,
                        linkados={"MLB7125449388": "https://meli.la/link-do-7125"})
    assert src.refresh_price(offer).price_current_cents == 10490
    assert chamadas == [0, 100]


def test_refresh_price_para_de_paginar_quando_ja_achou_todos_os_linkados(tmp_path):
    """O maior produto do pool tem 277 anúncios (3 páginas). Achados os
    linkados, não há por que pedir o resto: as páginas seguintes não podem
    mudar a escolha."""
    chamadas = []

    def handler(request: httpx.Request):
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "TOK", "expires_in": 21600})
        chamadas.append(int(request.url.params.get("offset", 0)))
        return httpx.Response(200, json={
            "results": ITENS + [_anuncio(f"X{i}", 9.0) for i in range(97)],
            "paging": {"total": 277, "offset": 0, "limit": 100}})

    src, offer = _fonte(tmp_path, handler)
    assert src.refresh_price(offer).price_current_cents == 7890
    assert chamadas == [0]


def test_refresh_price_preco_acima_da_referencia_e_barrado_depois(tmp_path):
    # refresh_price deixa passar; quem barra é a validação (a rede que pega a
    # oferta que encareceu entre a busca e a publicação). Com a régua do ML
    # zerada (fase 5M), quem sustenta a referência é o nosso price_log.
    from afiliado import validate
    from afiliado.errors import ValidationError
    from tests.test_models import make_offer

    src, offer = _fonte(tmp_path, _items_handler([_anuncio("MLB7125449388", 129.90)]),
                        linkados={"MLB7125449388": "https://meli.la/x"})
    updated = src.refresh_price(offer)
    assert updated.price_current_cents == 12990
    cfg = {"selection": {"max_above_ref": 1.00, "price_min_brl": 20, "price_max_brl": 1000}}
    com_regua = make_offer(source="meli", item_id=PRODUTO, price_ref_cents=10490,
                           price_current_cents=updated.price_current_cents)
    with pytest.raises(ValidationError, match="acima da referência"):
        validate.check_price(com_regua, cfg)


def test_refresh_price_erro_http_vira_source_error(tmp_path):
    def handler(request: httpx.Request):
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "TOK", "expires_in": 21600})
        return httpx.Response(500, text="erro interno")

    src, offer = _fonte(tmp_path, handler)
    with pytest.raises(SourceError):
        src.refresh_price(offer)


def test_meli_nao_grava_o_preco_do_pool_como_observacao():
    # C7c: o preço com que a oferta sai do pool é a mediana, não uma
    # observação — o pipeline lê este atributo e só grava o preço vivo. Desde
    # a 5M o que entra no price_log é o preço do anúncio ESCOLHIDO.
    assert MeliSource.observes_price_on_discovery is False


# -- fase 5M (M3): o mais barato não pode ser QUALQUER um ---------------------
#
# Medido em 2026-08-28 sobre 1717 anúncios de 53 produtos do pool
# (`/products/{id}/items` paginado inteiro):
#
# - `condition` é "new" em 1717/1717 — o piso não exclui ninguém HOJE, e é
#   justamente por isso que ele fica: o dia em que um usado entrar na lista
#   não pode ser o dia em que a gente descobre que não olhava;
# - em 12 dos 53 produtos o mais barato é um item barato com FRETE caro pago
#   pelo comprador (`free_shipping: false`, `shipping.cost` 44,62): R$ 8,00 +
#   R$ 44,62 de frete, R$ 17,99 + R$ 44,62... publicar "R$ 8,00" ali é dizer
#   um número que ninguém paga;
# - exigir Full OU loja oficial OU frete grátis derruba esses 12 para ZERO,
#   custa 1 produto de 53 (fica sem anúncio elegível) e encarece o preço
#   publicado em +0,0% na mediana (p75 +8,6%): em 38 dos 52 produtos o mais
#   barato JÁ passa no piso;
# - exigir só frete grátis custaria 15 produtos de 53 e +8,4% na mediana;
#   exigir `gold_pro`, 21 produtos e +10,6%. Nenhum dos dois se paga.


def test_o_piso_de_qualidade_e_novo_E_um_sinal_de_entrega():
    from afiliado.sources.meli import anuncio_passa_no_piso

    full = _anuncio("MLB1", 10.0)
    assert anuncio_passa_no_piso(full)
    oficial = _anuncio("MLB2", 10.0, official_store_id=1234,
                       shipping={"free_shipping": False, "logistic_type": "drop_off"})
    assert anuncio_passa_no_piso(oficial)
    frete_gratis = _anuncio("MLB3", 10.0,
                            shipping={"free_shipping": True, "logistic_type": "cross_docking"})
    assert anuncio_passa_no_piso(frete_gratis)


def test_o_piso_barra_o_item_barato_de_frete_caro():
    """O caso real: R$ 8,00 com R$ 44,62 de frete pago pelo comprador — 558%
    do preço. Sem loja oficial, sem Full, sem frete grátis."""
    from afiliado.sources.meli import anuncio_passa_no_piso

    assert not anuncio_passa_no_piso(_anuncio(
        "MLB5097654932", 8.0, official_store_id=None,
        shipping={"free_shipping": False, "logistic_type": "drop_off", "cost": 44.62}))


def test_o_piso_barra_o_anuncio_usado_ou_recondicionado():
    from afiliado.sources.meli import anuncio_passa_no_piso

    for condicao in ("used", "refurbished", "", None):
        assert not anuncio_passa_no_piso(_anuncio("MLB1", 10.0, condition=condicao))


def test_o_piso_aguenta_payload_torto():
    from afiliado.sources.meli import anuncio_passa_no_piso

    assert not anuncio_passa_no_piso({})
    assert not anuncio_passa_no_piso({"condition": "new", "shipping": "não é dict"})
    assert not anuncio_passa_no_piso({"condition": "new"})


def test_refresh_price_pula_o_linkado_que_nao_passa_no_piso(tmp_path):
    """Ter link não basta: mandar o seguidor para um vendedor ruim é outro
    tipo de dano, e a conta se chama Fiscal."""
    itens = [_anuncio("MLB4555189589", 78.90, official_store_id=None,
                      shipping={"free_shipping": False, "logistic_type": "drop_off",
                                "cost": 44.62}),
             _anuncio("MLB7125449388", 104.90)]
    src, offer = _fonte(tmp_path, _items_handler(itens))
    publicada = src.refresh_price(offer)
    assert publicada.anuncio_id == "MLB7125449388"
    assert publicada.price_current_cents == 10490


def test_refresh_price_com_todos_os_linkados_fora_do_piso_descarta(tmp_path):
    ruim = {"free_shipping": False, "logistic_type": "drop_off", "cost": 44.62}
    itens = [_anuncio("MLB4555189589", 78.90, shipping=ruim),
             _anuncio("MLB7125449388", 104.90, condition="used")]
    src, offer = _fonte(tmp_path, _items_handler(itens))
    with pytest.raises(SourceError, match="nenhum anúncio linkado"):
        src.refresh_price(offer)


def test_anuncios_para_linkar_devolve_os_N_mais_baratos_que_passam_no_piso():
    """O que o `/meli-links-refresh` manda para o painel. N = 3: medido, 34 de
    35 anúncios sobrevivem a 2 dias (~90% a 7), então a chance de os TRÊS
    sumirem numa semana é 0,09%; e em 27 dos 52 produtos os 3 já são a lista
    elegível INTEIRA."""
    from afiliado.sources.meli import LINKS_POR_PRODUTO, anuncios_para_linkar

    ruim = {"free_shipping": False, "logistic_type": "drop_off", "cost": 44.62}
    results = [_anuncio("MLB-caro", 300.0), _anuncio("MLB-barato-ruim", 8.0, shipping=ruim),
               _anuncio("MLB-b", 50.0), _anuncio("MLB-a", 20.0), _anuncio("MLB-c", 90.0),
               _anuncio("MLB-sem-preco", None)]
    assert LINKS_POR_PRODUTO == 3
    assert anuncios_para_linkar(results) == ["MLB-a", "MLB-b", "MLB-c"]
    assert anuncios_para_linkar(results, n=1) == ["MLB-a"]
    assert anuncios_para_linkar([], n=3) == []
    assert anuncios_para_linkar([_anuncio("MLB-x", 8.0, shipping=ruim)]) == []


# -- resolve_affiliate_link: o link é o do anúncio publicado ------------------

def test_resolve_affiliate_link_devolve_o_link_do_anuncio_escolhido(tmp_path):
    src, offer = _fonte(tmp_path, _items_handler(ITENS))
    publicada = src.refresh_price(offer)
    assert src.resolve_affiliate_link(publicada) == "https://meli.la/link-do-4555"


def test_resolve_affiliate_link_sem_o_anuncio_escolhido_levanta(tmp_path):
    """Sem passar pelo `refresh_price` não há anúncio escolhido — e devolver
    "algum" link do produto é publicar preço de um anúncio e link de outro."""
    from tests.test_models import make_offer
    links_path = write_links(tmp_path / "links.json",
                             {"MLB123456": {"MLB99": "https://meli.la/x"}})
    src = source_with(_authed_handler, tmp_path, links_path=links_path)
    with pytest.raises(SourceError, match="sem anúncio escolhido"):
        src.resolve_affiliate_link(make_offer(source="meli", item_id="MLB123456"))


def test_resolve_affiliate_link_produto_sem_link_levanta(tmp_path):
    from tests.test_models import make_offer
    links_path = write_links(tmp_path / "links.json",
                             {"MLB1": {"MLB11": "https://meli.la/x"}})
    src = source_with(_authed_handler, tmp_path, links_path=links_path)
    offer = make_offer(source="meli", item_id="MLB999", anuncio_id="MLB11")
    with pytest.raises(SourceError, match="sem link de afiliado"):
        src.resolve_affiliate_link(offer)


def test_o_formato_antigo_por_produto_nao_publica_nada(tmp_path):
    """Os 55 links da fase 5C abrem a página de catálogo: eles continuam
    guardados (`product_link`), mas não podem virar preço publicado — o pool
    antigo lido pela fonte nova tem cobertura ZERO, e é o doctor que avisa."""
    from tests.test_models import make_offer
    links_path = tmp_path / "links.json"
    links_path.write_text(json.dumps({"MLB1": "https://meli.la/velho"}), encoding="utf-8")
    src = source_with(_authed_handler, tmp_path, links_path=links_path)
    offer = make_offer(source="meli", item_id="MLB1", anuncio_id="MLB11")
    with pytest.raises(SourceError, match="sem link de afiliado"):
        src.resolve_affiliate_link(offer)
    assert src.link_coverage([offer]) == (0, 1)


def test_pool_de_links_ausente_nao_levanta_na_carga(tmp_path):
    from tests.test_models import make_offer
    src = source_with(_authed_handler, tmp_path, links_path=tmp_path / "nao-existe.json")
    offer = make_offer(source="meli", item_id="MLB1", anuncio_id="MLB11")
    with pytest.raises(SourceError, match="sem link de afiliado"):
        src.resolve_affiliate_link(offer)


# -- Fase 5C (M5/A6): cobertura do pool de links ------------------------------

def test_link_coverage_conta_produtos_com_pelo_menos_um_anuncio_linkado(tmp_path):
    from tests.test_models import make_offer
    links = write_links(tmp_path / "links.json",
                        {"MLB1": {"MLB11": "https://meli.la/a"},
                         "MLB3": {}},                      # produto sem anúncio linkado
                        product_links={"MLB3": "https://meli.la/velho"})
    src = source_with(_authed_handler, tmp_path, links_path=links)
    ofertas = [make_offer(source="meli", item_id=f"MLB{n}") for n in (1, 2, 3)]
    assert src.links_file_exists
    assert src.link_coverage(ofertas) == (1, 3)
    assert src.link_coverage([]) == (0, 0)


def test_link_coverage_com_arquivo_ausente(tmp_path):
    from tests.test_models import make_offer
    src = source_with(_authed_handler, tmp_path, links_path=tmp_path / "nao-existe.json")
    assert not src.links_file_exists
    assert src.link_coverage([make_offer(source="meli", item_id="MLB1")]) == (0, 1)

import json

import httpx

from afiliado.meli_links import gerar_links


def client_with(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _success_response(product_ids, tag):
    urls = []
    for pid in product_ids:
        urls.append({
            "origin_url": f"https://www.mercadolivre.com.br/p/{pid}",
            "short_url": f"https://meli.la/{pid[-6:]}",
            "created": True,
            "tag": tag,
            "type_url": "SOCIAL_PROFILE_ENCRYPTED",
        })
    return {"status": 200, "total_items": len(urls), "total_success": len(urls),
            "total_error": 0, "urls": urls}


def test_gerar_links_lote_unico_sucesso_total():
    captured = {}

    def handler(request: httpx.Request):
        assert request.url == "https://www.mercadolivre.com.br/affiliate-program/api/v2/affiliates/createLink"
        body = json.loads(request.content)
        captured["body"] = body
        captured["headers"] = request.headers
        return httpx.Response(200, json=_success_response(body["urls"], body["tag"]))

    links, erro = gerar_links(
        ["MLB18725310", "MLB111", "MLB222", "MLB333"], tag="jmbessa",
        cookies="sess=abc", csrf="tok123", client=client_with(handler))

    assert erro is None
    assert links == {
        "MLB18725310": "https://meli.la/725310",
        "MLB111": "https://meli.la/MLB111",
        "MLB222": "https://meli.la/MLB222",
        "MLB333": "https://meli.la/MLB333",
    }
    assert captured["body"]["tag"] == "jmbessa"
    assert captured["body"]["urls"] == [
        "https://www.mercadolivre.com.br/p/MLB18725310",
        "https://www.mercadolivre.com.br/p/MLB111",
        "https://www.mercadolivre.com.br/p/MLB222",
        "https://www.mercadolivre.com.br/p/MLB333",
    ]
    assert captured["headers"]["cookie"] == "sess=abc"
    assert captured["headers"]["x-csrf-token"] == "tok123"
    assert captured["headers"]["content-type"] == "application/json"
    assert captured["headers"]["origin"] == "https://www.mercadolivre.com.br"
    assert captured["headers"]["referer"] == "https://www.mercadolivre.com.br/afiliados/linkbuilder"


def test_gerar_links_divide_em_lotes():
    chamadas = []

    def handler(request: httpx.Request):
        body = json.loads(request.content)
        chamadas.append(body["urls"])
        return httpx.Response(200, json=_success_response(body["urls"], body["tag"]))

    product_ids = [f"MLB{i}" for i in range(5)]
    links, erro = gerar_links(product_ids, tag="jmbessa", cookies="c", csrf="t",
                              client=client_with(handler), lote=2)

    assert erro is None
    assert len(chamadas) == 3  # 2 + 2 + 1
    assert len(links) == 5


def test_gerar_links_ignora_entradas_sem_created_ou_sem_short_url():
    def handler(request: httpx.Request):
        body = json.loads(request.content)
        return httpx.Response(200, json={
            "status": 200, "total_items": 3, "total_success": 1, "total_error": 2,
            "urls": [
                {"origin_url": "https://www.mercadolivre.com.br/p/MLB1",
                 "short_url": "https://meli.la/ok1", "created": True},
                {"origin_url": "https://www.mercadolivre.com.br/p/MLB2",
                 "short_url": "https://meli.la/nope", "created": False},
                {"origin_url": "https://www.mercadolivre.com.br/p/MLB3",
                 "created": True},  # sem short_url
            ],
        })

    links, erro = gerar_links(["MLB1", "MLB2", "MLB3"], tag="jmbessa", cookies="c", csrf="t",
                              client=client_with(handler))
    assert erro is None
    assert links == {"MLB1": "https://meli.la/ok1"}


def test_gerar_links_sessao_expirada_401_devolve_vazio_com_mensagem():
    def handler(request: httpx.Request):
        return httpx.Response(401, text="unauthorized")

    links, erro = gerar_links(["MLB1"], tag="jmbessa", cookies="expirado", csrf="t",
                              client=client_with(handler))
    assert links == {}
    assert erro is not None
    assert "expirad" in erro or "inválid" in erro


def test_gerar_links_sessao_expirada_403_devolve_vazio_com_mensagem():
    def handler(request: httpx.Request):
        return httpx.Response(403, text="forbidden")

    links, erro = gerar_links(["MLB1"], tag="jmbessa", cookies="ruim", csrf="t",
                              client=client_with(handler))
    assert links == {}
    assert erro is not None


def test_gerar_links_lote_inteiro_sem_sucesso_para_e_nao_chama_os_seguintes():
    chamadas = []

    def handler(request: httpx.Request):
        chamadas.append(request)
        return httpx.Response(200, json={
            "status": 200, "total_items": 1, "total_success": 0, "total_error": 1,
            "urls": [{"origin_url": "https://www.mercadolivre.com.br/p/MLB1",
                      "created": False}],
        })

    links, erro = gerar_links(["MLB1", "MLB2"], tag="tag-inexistente", cookies="c", csrf="t",
                              client=client_with(handler), lote=1)
    assert links == {}
    assert erro is not None
    assert len(chamadas) == 1  # para no primeiro lote sem sucesso; o 2º nem é chamado


def test_gerar_links_sessao_expirada_no_meio_preserva_lotes_ja_coletados():
    """Regressão: sessão cair no meio do lote 2/3 não pode jogar fora os
    links do lote 1 já coletados com sucesso — só o processamento dos lotes
    restantes é interrompido."""
    chamadas = []

    def handler(request: httpx.Request):
        body = json.loads(request.content)
        chamadas.append(body["urls"])
        if len(chamadas) == 2:
            return httpx.Response(401, text="unauthorized")
        return httpx.Response(200, json=_success_response(body["urls"], body["tag"]))

    links, erro = gerar_links(["MLB1", "MLB2", "MLB3"], tag="jmbessa", cookies="c", csrf="t",
                              client=client_with(handler), lote=1)

    assert len(chamadas) == 2               # o 3º lote não é chamado
    assert links == {"MLB1": "https://meli.la/p/MLB1"}  # lote 1 preservado
    assert erro is not None


def test_gerar_links_sucesso_parcial_nao_e_erro():
    def handler(request: httpx.Request):
        return httpx.Response(200, json={
            "status": 200, "total_items": 2, "total_success": 1, "total_error": 1,
            "urls": [
                {"origin_url": "https://www.mercadolivre.com.br/p/MLB1",
                 "short_url": "https://meli.la/ok1", "created": True},
                {"origin_url": "https://www.mercadolivre.com.br/p/MLB2", "created": False},
            ],
        })

    links, erro = gerar_links(["MLB1", "MLB2"], tag="jmbessa", cookies="c", csrf="t",
                              client=client_with(handler))
    assert erro is None
    assert links == {"MLB1": "https://meli.la/ok1"}


def test_gerar_links_lista_vazia_nao_chama_rede():
    def handler(request: httpx.Request):
        raise AssertionError("não deveria chamar a rede com lista vazia")

    links, erro = gerar_links([], tag="jmbessa", cookies="c", csrf="t",
                              client=client_with(handler))
    assert links == {}
    assert erro is None


def test_gerar_links_dedupe_ids_repetidos():
    chamadas = []

    def handler(request: httpx.Request):
        body = json.loads(request.content)
        chamadas.append(body["urls"])
        return httpx.Response(200, json=_success_response(body["urls"], body["tag"]))

    links, erro = gerar_links(["MLB1", "MLB1", "MLB2"], tag="jmbessa", cookies="c", csrf="t",
                              client=client_with(handler))
    assert erro is None
    assert len(chamadas[0]) == 2  # MLB1 duplicado colapsa para uma URL
    assert set(links) == {"MLB1", "MLB2"}

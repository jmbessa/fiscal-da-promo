import json
from pathlib import Path

import httpx
import pytest

from afiliado.meli_links import escrever_pool, gerar_links, item_url, ler_pool

RAIZ = Path(__file__).resolve().parents[1]


def client_with(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _success_response(item_ids, tag):
    """Resposta de sucesso total do createLink para `item_ids`: origin_url e
    short_url são derivados do ID de forma óbvia (`MLB1` ->
    `https://produto.mercadolivre.com.br/MLB-1-_JM` e `https://meli.la/MLB1`),
    nunca por fatiamento."""
    urls = [{
        "origin_url": item_url(iid),
        "short_url": f"https://meli.la/{iid}",
        "created": True,
        "tag": tag,
        "type_url": "SOCIAL_PROFILE_ENCRYPTED",
    } for iid in item_ids]
    return {"status": 200, "total_items": len(urls), "total_success": len(urls),
            "total_error": 0, "urls": urls}


def _ids_do_corpo(body: dict) -> list[str]:
    """IDs de anúncio que a requisição carrega — o createLink recebe URLs
    completas (`.../MLB-123-_JM`), e o helper de resposta trabalha com IDs."""
    return ["MLB" + url.split("MLB-", 1)[1].split("-", 1)[0] for url in body["urls"]]


def test_gerar_links_lote_unico_sucesso_total():
    captured = {}

    def handler(request: httpx.Request):
        assert request.url == "https://www.mercadolivre.com.br/affiliate-program/api/v2/affiliates/createLink"
        body = json.loads(request.content)
        captured["body"] = body
        captured["headers"] = request.headers
        return httpx.Response(200, json=_success_response(_ids_do_corpo(body), body["tag"]))

    links, erro = gerar_links(
        ["MLB18725310", "MLB111", "MLB222", "MLB333"], tag="ofiscaldapromo",
        cookies="sess=abc", csrf="tok123", client=client_with(handler))

    assert erro is None
    assert links == {
        "MLB18725310": "https://meli.la/MLB18725310",
        "MLB111": "https://meli.la/MLB111",
        "MLB222": "https://meli.la/MLB222",
        "MLB333": "https://meli.la/MLB333",
    }
    assert captured["body"]["tag"] == "ofiscaldapromo"
    # Fase 5M: a URL pedida é a do ANÚNCIO, não a da página de catálogo — é o
    # que faz o link abrir o vendedor cujo preço o post publica.
    assert captured["body"]["urls"] == [
        "https://produto.mercadolivre.com.br/MLB-18725310-_JM",
        "https://produto.mercadolivre.com.br/MLB-111-_JM",
        "https://produto.mercadolivre.com.br/MLB-222-_JM",
        "https://produto.mercadolivre.com.br/MLB-333-_JM",
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
        return httpx.Response(200, json=_success_response(_ids_do_corpo(body), body["tag"]))

    product_ids = [f"MLB{i}" for i in range(5)]
    links, erro = gerar_links(product_ids, tag="jmbessa", cookies="c", csrf="t",
                              client=client_with(handler), lote=2)

    assert erro is None
    assert len(chamadas) == 3  # 2 + 2 + 1
    assert links == {pid: f"https://meli.la/{pid}" for pid in product_ids}


def test_gerar_links_ignora_entradas_sem_created_ou_sem_short_url():
    def handler(request: httpx.Request):
        body = json.loads(request.content)
        return httpx.Response(200, json={
            "status": 200, "total_items": 3, "total_success": 1, "total_error": 2,
            "urls": [
                {"origin_url": "https://produto.mercadolivre.com.br/MLB-1-_JM",
                 "short_url": "https://meli.la/ok1", "created": True},
                {"origin_url": "https://produto.mercadolivre.com.br/MLB-2-_JM",
                 "short_url": "https://meli.la/nope", "created": False},
                {"origin_url": "https://produto.mercadolivre.com.br/MLB-3-_JM",
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
            "urls": [{"origin_url": "https://produto.mercadolivre.com.br/MLB-1-_JM",
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
        return httpx.Response(200, json=_success_response(_ids_do_corpo(body), body["tag"]))

    links, erro = gerar_links(["MLB1", "MLB2", "MLB3"], tag="jmbessa", cookies="c", csrf="t",
                              client=client_with(handler), lote=1)

    assert len(chamadas) == 2               # o 3º lote não é chamado
    assert links == {"MLB1": "https://meli.la/MLB1"}  # lote 1 preservado
    assert erro is not None


def test_gerar_links_sucesso_parcial_nao_e_erro():
    def handler(request: httpx.Request):
        return httpx.Response(200, json={
            "status": 200, "total_items": 2, "total_success": 1, "total_error": 1,
            "urls": [
                {"origin_url": "https://produto.mercadolivre.com.br/MLB-1-_JM",
                 "short_url": "https://meli.la/ok1", "created": True},
                {"origin_url": "https://produto.mercadolivre.com.br/MLB-2-_JM",
                 "created": False},
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
        return httpx.Response(200, json=_success_response(_ids_do_corpo(body), body["tag"]))

    links, erro = gerar_links(["MLB1", "MLB1", "MLB2"], tag="jmbessa", cookies="c", csrf="t",
                              client=client_with(handler))
    assert erro is None
    assert len(chamadas[0]) == 2  # MLB1 duplicado colapsa para uma URL
    assert links == {"MLB1": "https://meli.la/MLB1", "MLB2": "https://meli.la/MLB2"}


def test_gerar_links_json_valido_nao_dict_nao_levanta():
    """Contrato: gerar_links nunca levanta. Um JSON válido mas não-dict (uma
    lista, uma string) chegava direto no data.get() e virava AttributeError."""
    for corpo in ([], ["MLB1"], "erro", 42):
        def handler(request: httpx.Request, corpo=corpo):
            return httpx.Response(200, json=corpo)

        links, erro = gerar_links(["MLB1"], tag="jmbessa", cookies="c", csrf="t",
                                  client=client_with(handler))
        assert links == {}
        assert erro is not None            # o lote inteiro ficou sem resposta válida


# -- fase 5M: a URL do anúncio e o pool de links POR ANÚNCIO ------------------


def test_item_url_monta_a_url_do_anuncio():
    """O painel de afiliados aceita URL de anúncio (medido em 2026-08-28:
    `MLB-7080290072-_JM` cunhou `https://meli.la/2WFwu8s`), e o
    `permalink` não vem em `/products/{id}/items` — a URL é montada."""
    assert item_url("MLB7080290072") == "https://produto.mercadolivre.com.br/MLB-7080290072-_JM"
    assert item_url("7080290072") == "https://produto.mercadolivre.com.br/MLB-7080290072-_JM"


def test_gerar_links_casa_a_resposta_pelo_id_do_anuncio_com_hifen():
    """Regressão da 5M: o `origin_url` do anúncio traz `MLB-7080290072`, com
    hífen. O casamento antigo era `MLB\\d+` — não casava com o hífen, e TODO
    link cunhado seria descartado em silêncio, com `erro is None`."""
    def handler(request: httpx.Request):
        return httpx.Response(200, json={
            "status": 200, "total_items": 1, "total_success": 1, "total_error": 0,
            "urls": [{"origin_url": "https://produto.mercadolivre.com.br/"
                                    "MLB-7080290072-creatina-500g-_JM",
                      "short_url": "https://meli.la/2WFwu8s", "created": True}],
        })

    links, erro = gerar_links(["MLB7080290072"], tag="ofiscaldapromo", cookies="c", csrf="t",
                              client=client_with(handler))
    assert erro is None
    assert links == {"MLB7080290072": "https://meli.la/2WFwu8s"}


def test_ler_pool_le_o_formato_por_anuncio(tmp_path):
    caminho = tmp_path / "meli_links.json"
    caminho.write_text(json.dumps({
        "version": 2, "generated_at": "2026-08-28", "tag": "ofiscaldapromo",
        "products": {
            "MLB18725310": {
                "items": {"MLB7381404798": "https://meli.la/a",
                          "MLB4555189589": "https://meli.la/b"},
                "product_link": "https://meli.la/1ULuAEY"},
            "MLB26796581": {"items": {"MLB4812143184": "https://meli.la/c"}},
        }}), encoding="utf-8")
    pool = ler_pool(caminho)
    assert pool["MLB18725310"]["items"] == {"MLB7381404798": "https://meli.la/a",
                                            "MLB4555189589": "https://meli.la/b"}
    assert pool["MLB18725310"]["product_link"] == "https://meli.la/1ULuAEY"
    assert pool["MLB26796581"] == {"items": {"MLB4812143184": "https://meli.la/c"},
                                   "product_link": ""}


def test_ler_pool_migra_o_formato_antigo_sem_perder_link(tmp_path):
    """Os 55 links da fase 5C são por PRODUTO (gerados de `/p/MLB...`): eles
    continuam válidos como link, mas não servem para publicar preço — o preço
    é de um anúncio, e um link de catálogo abre o vendedor que o ML escolher.
    O formato antigo é lido como "produto sem nenhum anúncio linkado", e o
    link fica guardado em `product_link`."""
    caminho = tmp_path / "meli_links.json"
    caminho.write_text(json.dumps({"MLB18725310": "https://meli.la/1ULuAEY",
                                   "MLB26796581": "https://meli.la/19JLMU4"}),
                       encoding="utf-8")
    pool = ler_pool(caminho)
    assert pool == {
        "MLB18725310": {"items": {}, "product_link": "https://meli.la/1ULuAEY"},
        "MLB26796581": {"items": {}, "product_link": "https://meli.la/19JLMU4"},
    }


@pytest.mark.parametrize("conteudo", ["{não é json", "[]", '"texto"', "42"])
def test_ler_pool_arquivo_invalido_e_vazio(tmp_path, conteudo):
    caminho = tmp_path / "meli_links.json"
    caminho.write_text(conteudo, encoding="utf-8")
    assert ler_pool(caminho) == {}


def test_ler_pool_arquivo_ausente_e_vazio(tmp_path):
    assert ler_pool(tmp_path / "nao-existe.json") == {}


def test_escrever_pool_e_ler_de_volta(tmp_path):
    caminho = tmp_path / "meli_links.json"
    escrever_pool(caminho, {
        "MLB2": {"items": {"MLB22": "https://meli.la/b"}, "product_link": ""},
        "MLB1": {"items": {"MLB11": "https://meli.la/a"},
                 "product_link": "https://meli.la/velho"},
    }, tag="ofiscaldapromo")
    raw = json.loads(caminho.read_text(encoding="utf-8"))
    assert raw["version"] == 2 and raw["tag"] == "ofiscaldapromo"
    # ordenado: o diff de um refresh mostra o que mudou, não o que reordenou
    assert list(raw["products"]) == ["MLB1", "MLB2"]
    assert ler_pool(caminho)["MLB1"]["items"] == {"MLB11": "https://meli.la/a"}
    assert ler_pool(caminho)["MLB1"]["product_link"] == "https://meli.la/velho"


def test_o_pool_de_links_do_repo_esta_no_formato_novo_e_guardou_os_55(tmp_path):
    """A migração é dado, não código: o arquivo commitado tem de estar no
    formato por anúncio E ainda trazer os 55 links por produto da fase 5C
    (`jmbessa`) — perdê-los seria jogar fora trabalho de painel já feito."""
    caminho = RAIZ / "data/meli_links.json"
    if not caminho.is_file():
        pytest.skip("sem data/meli_links.json neste checkout")
    raw = json.loads(caminho.read_text(encoding="utf-8"))
    assert raw["version"] == 2
    pool = ler_pool(caminho)
    assert sum(1 for e in pool.values() if e["product_link"]) == 55

"""Fase 5E — canal `instagram_story`: o story vai ao ar pela API oficial.

Os fatos que este canal codifica foram medidos AO VIVO na conta do dono em
2026-08-27 (ver `docs/runbooks/meta-setup.md`): `POST /{ig_user_id}/media` com
`media_type=STORIES` e **sem** `caption`, container que nasce `IN_PROGRESS`, e
`media_publish` com o `creation_id`. Nenhum teste toca a rede — tudo por
`httpx.MockTransport`.
"""

import io
from urllib.parse import parse_qs

import httpx
import pytest
from PIL import Image

from afiliado.channels.instagram_story import AVISO_POLLING_CEGO, InstagramStoryChannel
from tests.test_state import make_post


def _product_png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (800, 800), (10, 20, 30)).save(buf, "PNG")
    return buf.getvalue()


def _graph_handler(request, status_codes, corpos, publish_id="story789",
                   creation_id="creation123"):
    """Rotas da Graph API: cria container, devolve `status_codes` em sequência
    no polling (o último se repete) e publica."""
    path = request.url.path
    if path.endswith("/media_publish"):
        corpos.append(("publish", request.content))
        return httpx.Response(200, json={"id": publish_id})
    if path.endswith("/media"):
        corpos.append(("media", request.content))
        return httpx.Response(200, json={"id": creation_id})
    # GET /{creation_id}?fields=status_code,status
    corpos.append(("status", str(request.url)))
    idx = min(len([c for c in corpos if c[0] == "status"]) - 1, len(status_codes) - 1)
    code = status_codes[idx]
    return httpx.Response(200, json={
        "status_code": code, "status": _STATUS_TEXTO[code], "id": creation_id})


_STATUS_TEXTO = {
    "IN_PROGRESS": "Media is still being processed.",
    "FINISHED": "Finished",
    "ERROR": "The media you are trying to publish is not valid.",
    "EXPIRED": "The container has expired.",
}


def _handler(status_codes=("FINISHED",), corpos=None, **kw):
    corpos = corpos if corpos is not None else []

    def handler(request):
        host, path = request.url.host, request.url.path
        if host == "cf.shopee.com.br":
            return httpx.Response(200, content=_product_png(),
                                  headers={"content-type": "image/png"})
        if host == "api.telegram.org":
            if path.endswith("/sendPhoto"):
                corpos.append(("sendPhoto", path))
                return httpx.Response(200, json={"ok": True, "result": {
                    "message_id": 5, "photo": [{"file_id": "small"}, {"file_id": "big"}]}})
            if path.endswith("/getFile"):
                return httpx.Response(200, json={
                    "ok": True, "result": {"file_path": "photos/file_2.jpg"}})
        if host == "graph.facebook.com":
            return _graph_handler(request, list(status_codes), corpos, **kw)
        return httpx.Response(404)

    return handler


class _Relogio:
    """`sleep` injetado: registra as pausas e não dorme de verdade."""

    def __init__(self):
        self.pausas = []

    def __call__(self, segundos):
        self.pausas.append(segundos)


def _canal(handler, sleep=None, **kw) -> InstagramStoryChannel:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return InstagramStoryChannel("IGUSER", "IGTOKEN", "BOTTOKEN", "OPSCHAT",
                                 client=client, sleep=sleep or _Relogio(), **kw)


def _corpo(corpos, etapa) -> dict:
    # keep_blank_values=True NÃO é detalhe: com o padrão do `parse_qs`, um
    # `caption=` no fio (legenda vazia, mas PRESENTE) simplesmente sumia do
    # dicionário, e o `"caption" not in corpo` do teste do container passava
    # sem prender nada. Ver `test_o_guarda_do_caption_guarda_valor_vazio`.
    return parse_qs(next(c for k, c in corpos if k == etapa).decode(),
                    keep_blank_values=True)


def _handler_status(resposta, corpos=None):
    """Base normal, mas o GET do container devolve `resposta` (ou levanta, se
    `resposta` for uma exceção). É por aqui que entram as formas que a Meta
    pode devolver e o canal nunca viu ao vivo."""
    base = _handler(corpos=corpos)

    def handler(request):
        if request.url.host == "graph.facebook.com" and "fields=" in str(request.url):
            if corpos is not None:
                corpos.append(("status", str(request.url)))
            if isinstance(resposta, Exception):
                raise resposta
            return httpx.Response(200, json=resposta)
        return base(request)

    return handler


# -- 1. o container do story ---------------------------------------------------

def test_container_do_story_manda_media_type_stories_e_nenhuma_caption():
    """Fato 1: `media_type=STORIES` é o que separa um story de um post de feed,
    e a API não aceita legenda em story. Inspeciona o CORPO da requisição."""
    corpos = []
    res = _canal(_handler(corpos=corpos)).publish(make_post())
    assert res.ok and res.message_id == "story789"
    corpo = _corpo(corpos, "media")
    assert corpo["media_type"] == ["STORIES"]
    assert "caption" not in corpo
    assert corpo["image_url"][0].startswith("https://api.telegram.org/file/bot")
    assert corpo["access_token"] == ["IGTOKEN"]
    # ...e o publish leva o creation_id devolvido pelo container.
    assert _corpo(corpos, "publish")["creation_id"] == ["creation123"]


def test_max_per_run_e_nome_do_canal():
    assert InstagramStoryChannel.name == "instagram_story"
    assert InstagramStoryChannel.max_per_run == 1


# -- 2. polling do container ---------------------------------------------------

def test_polling_espera_o_container_terminar_e_so_entao_publica():
    """Fato 2/3: o container nasce IN_PROGRESS. Publicar assim funcionou ao
    vivo, mas com imagem maior é sorte — o canal espera o FINISHED."""
    corpos, relogio = [], _Relogio()
    res = _canal(_handler(("IN_PROGRESS", "IN_PROGRESS", "FINISHED"), corpos=corpos),
                 sleep=relogio).publish(make_post())
    assert res.ok and res.message_id == "story789"
    assert len([k for k, _ in corpos if k == "status"]) == 3
    assert relogio.pausas == [1.0, 1.0]        # 3 tentativas, 2 pausas; nada dormiu
    # o publish veio DEPOIS do último status (ordem das chamadas)
    assert [k for k, _ in corpos] == ["sendPhoto", "media", "status", "status", "status",
                                      "publish"]


def test_polling_le_status_code_e_status_do_container():
    corpos = []
    _canal(_handler(("FINISHED",), corpos=corpos)).publish(make_post())
    url = next(c for k, c in corpos if k == "status")
    assert "/creation123" in url
    assert "fields=status_code%2Cstatus" in url or "fields=status_code,status" in url


def test_container_finished_de_primeira_nao_dorme():
    relogio = _Relogio()
    assert _canal(_handler(("FINISHED",)), sleep=relogio).publish(make_post()).ok
    assert relogio.pausas == []


def test_container_ainda_em_progresso_no_fim_das_tentativas_publica_assim_mesmo():
    """Foi o que funcionou ao vivo: `media_publish` aceitou um container
    IN_PROGRESS. Esgotadas as 5 tentativas, o canal tenta publicar."""
    corpos, relogio = [], _Relogio()
    res = _canal(_handler(("IN_PROGRESS",), corpos=corpos), sleep=relogio).publish(make_post())
    assert res.ok and res.message_id == "story789"
    assert len([k for k, _ in corpos if k == "status"]) == InstagramStoryChannel.max_polls == 5
    assert relogio.pausas == [1.0] * 4


# -- 3. container com erro -----------------------------------------------------

@pytest.mark.parametrize("code", ["ERROR", "EXPIRED"])
def test_container_com_erro_vira_resultado_falho_com_o_status_devolvido(code):
    relogio = _Relogio()
    res = _canal(_handler((code,)), sleep=relogio).publish(make_post())
    assert not res.ok
    assert code in res.error
    assert _STATUS_TEXTO[code] in res.error
    assert relogio.pausas == []              # desiste na hora, não fica tentando


def test_container_com_erro_nao_chega_a_publicar():
    corpos = []
    _canal(_handler(("ERROR",), corpos=corpos)).publish(make_post())
    assert not any(k == "publish" for k, _ in corpos)


# -- 4. resposta sem `id` ------------------------------------------------------

def test_container_sem_id_falha_sem_excecao():
    def handler(request):
        if request.url.host == "graph.facebook.com" and request.url.path.endswith("/media"):
            return httpx.Response(400, json={"error": {"message": "bad image_url"}})
        return _handler()(request)

    res = _canal(handler).publish(make_post())
    assert not res.ok and res.error == "bad image_url"


def test_publish_sem_id_falha_sem_excecao():
    def handler(request):
        if request.url.host == "graph.facebook.com" and request.url.path.endswith("/media_publish"):
            return httpx.Response(400, json={"error": {"message": "sem permissão"}})
        return _handler()(request)

    res = _canal(handler).publish(make_post())
    assert not res.ok and "sem permissão" in res.error


def test_publish_falho_com_container_em_progresso_diz_isso_no_erro():
    """Quando o publish falha DEPOIS de o polling esgotar, o erro precisa dizer
    que o container nunca terminou — senão a causa provável some do resumo."""
    def handler(request):
        if request.url.host == "graph.facebook.com" and request.url.path.endswith("/media_publish"):
            return httpx.Response(400, json={"error": {"message": "sem permissão"}})
        return _handler(("IN_PROGRESS",))(request)

    res = _canal(handler).publish(make_post())
    assert not res.ok
    assert "sem permissão" in res.error and "IN_PROGRESS" in res.error


# -- 5. falha de rede em qualquer etapa ----------------------------------------

@pytest.mark.parametrize("rota", ["cf.shopee.com.br", "sendPhoto", "getFile",
                                  "media", "status", "media_publish"])
def test_falha_de_rede_em_qualquer_etapa_nunca_levanta(rota):
    base = _handler(("IN_PROGRESS", "FINISHED"))

    def handler(request):
        host, path, url = request.url.host, request.url.path, str(request.url)
        if rota == "cf.shopee.com.br" and host == rota:
            raise httpx.ConnectError("sem rede")
        if rota in ("sendPhoto", "getFile") and path.endswith("/" + rota):
            raise httpx.ConnectError("sem rede")
        if rota == "media_publish" and path.endswith("/media_publish"):
            raise httpx.ConnectError("sem rede")
        if rota == "media" and path.endswith("/media"):
            raise httpx.ConnectError("sem rede")
        if rota == "status" and host == "graph.facebook.com" and "fields=" in url:
            raise httpx.ConnectError("sem rede")
        return base(request)

    res = _canal(handler).publish(make_post())
    if rota == "status":
        # O polling é o único passo cuja falha NÃO condena o story: o container
        # existe, e publicar com ele IN_PROGRESS foi o que funcionou ao vivo.
        assert res.ok
    else:
        assert not res.ok and res.error


def test_resposta_nao_json_do_container_falha_sem_excecao():
    def handler(request):
        if request.url.host == "graph.facebook.com" and request.url.path.endswith("/media"):
            return httpx.Response(500, content=b"<html>gateway</html>")
        return _handler()(request)

    res = _canal(handler).publish(make_post())
    assert not res.ok and "JSON" in res.error


def test_ig_user_id_com_caractere_de_controle_nunca_levanta():
    client = httpx.Client(transport=httpx.MockTransport(_handler()))
    ch = InstagramStoryChannel("12\n34", "IGTOKEN", "BOTTOKEN", "OPSCHAT",
                               client=client, sleep=_Relogio())
    res = ch.publish(make_post())
    assert not res.ok and res.error


# -- 6. hospedagem da arte (A5) ------------------------------------------------

def test_a_arte_sai_pelo_bot_secundario_quando_existe():
    corpos = []
    client = httpx.Client(transport=httpx.MockTransport(_handler(corpos=corpos)))
    ch = InstagramStoryChannel("IGUSER", "IGTOKEN", "BOTDOCANAL", "OPSCHAT", client=client,
                               sleep=_Relogio(), art_host_bot_token="BOTDEARTE")
    assert ch.publish(make_post()).ok
    image_url = _corpo(corpos, "media")["image_url"][0]
    assert "botBOTDEARTE" in image_url
    assert "BOTDOCANAL" not in image_url
    assert all("BOTDEARTE" in p for k, p in corpos if k == "sendPhoto")


def test_sem_bot_secundario_a_arte_sai_pelo_bot_do_canal():
    corpos = []
    client = httpx.Client(transport=httpx.MockTransport(_handler(corpos=corpos)))
    ch = InstagramStoryChannel("IGUSER", "IGTOKEN", "BOTDOCANAL", "OPSCHAT",
                               client=client, sleep=_Relogio())
    assert ch.publish(make_post()).ok
    assert "botBOTDOCANAL" in _corpo(corpos, "media")["image_url"][0]


def test_a_arte_hospedada_e_jpeg():
    corpos = []
    enviados = []

    def handler(request):
        if request.url.path.endswith("/sendPhoto"):
            enviados.append(request.content)
        return _handler(corpos=corpos)(request)

    assert _canal(handler).publish(make_post()).ok
    assert b"art.jpg" in enviados[0] and b"image/jpeg" in enviados[0]
    assert bytes.fromhex("ffd8ff") in enviados[0]


def test_hospedagem_falha_sem_publicar():
    corpos = []

    def handler(request):
        if request.url.path.endswith("/sendPhoto"):
            return httpx.Response(200, json={"ok": False, "description": "bot fora do chat"})
        return _handler(corpos=corpos)(request)

    res = _canal(handler).publish(make_post())
    assert not res.ok and "hospedar" in res.error
    assert corpos == []                     # nada chegou à Graph API


# -- variante da API -----------------------------------------------------------

def test_o_story_conta_como_publicacao_e_nao_como_despacho(tmp_path, monkeypatch):
    """A12 ao contrário. O `story_dispatch` é `manual = True`: a arte fica no
    chat de ops esperando o dono, entra em `summary.dispatched` e NÃO conta em
    `day_stats().published`. Este canal publica de verdade — não declara
    `manual`, e o pipeline o trata como o Telegram e o feed."""
    from afiliado import llm, pipeline
    from afiliado.state import StateDB
    from tests.test_models import make_offer
    from tests.test_pipeline import CFG, FakeSource, no_network_validator

    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    ch = _canal(_handler())
    summary = pipeline.run(CFG, [FakeSource([make_offer(item_id="a")])], [ch], db,
                           validator=no_network_validator)

    assert summary.published == ["Tênis Nike SB"]
    assert summary.dispatched == []
    assert pipeline.DESPACHO_MANUAL not in summary.text()
    assert db.day_stats(db.local_today()).published == 1
    assert db.day_stats(db.local_today()).dispatched == 0
    assert db.count_posts_today("instagram_story") == 1
    # `posted.manual` é a coluna que separa as duas trilhas; o story novo é 0.
    assert db.conn.execute(
        "SELECT manual FROM posted WHERE channel='instagram_story'").fetchall() == [(0,)]
    db.close()


def test_o_canal_nao_declara_manual():
    assert not getattr(InstagramStoryChannel, "manual", False)


def test_o_guarda_do_caption_guarda_valor_vazio():
    """Guarda do guarda (revisão da 5E). Com o padrão do `parse_qs`
    (`keep_blank_values=False`), `caption=` no fio desaparecia do dicionário e
    o `"caption" not in corpo` do teste do container passava mesmo com a
    legenda sendo enviada. A implementação estava certa; a asserção é que não
    prendia nada."""
    corpos = [("media", b"image_url=x&caption=&media_type=STORIES")]
    assert _corpo(corpos, "media")["caption"] == [""]


# -- 7. o polling só relata o que observou (revisão da 5E) ---------------------

def test_polling_que_nunca_chegou_a_meta_nao_inventa_in_progress():
    """Todos os GETs morrem na rede e o publish falha depois. O erro precisa
    dizer que o status nunca foi LIDO: antes ele afirmava "container ainda
    IN_PROGRESS após 5 tentativas" — um status que o canal nunca observou — e
    a causa real ("rede: sem rede") morria numa variável local."""
    def handler(request):
        if request.url.path.endswith("/media_publish"):
            return httpx.Response(400, json={"error": {"message": "sem permissão"}})
        return _handler_status(httpx.ConnectError("sem rede"))(request)

    res = _canal(handler).publish(make_post())
    assert not res.ok
    assert "sem permissão" in res.error          # o que a Meta respondeu
    assert "não consegui ler o status do container" in res.error
    assert "sem rede" in res.error               # a causa que estava sendo jogada fora
    assert "IN_PROGRESS" not in res.error        # status inventado


def test_container_sem_status_code_nao_vira_in_progress_no_erro():
    """O suspeito nº 1 do primeiro run real: 200 com `{"id": ...}` e nenhum
    `status_code`. Não ler é diferente de ler IN_PROGRESS."""
    def handler(request):
        if request.url.path.endswith("/media_publish"):
            return httpx.Response(400, json={"error": {"message": "sem permissão"}})
        return _handler_status({"id": "creation123"})(request)

    res = _canal(handler).publish(make_post())
    assert not res.ok and "sem permissão" in res.error
    assert "não consegui ler o status do container" in res.error
    assert "IN_PROGRESS" not in res.error


def test_status_code_ausente_publica_assim_mesmo_e_avisa_que_o_polling_e_cego():
    """O ramo mais provável de quebrar no primeiro run: sem `status_code` o
    canal gasta 5 GETs e 4 s de espera em TODO story, publica assim mesmo e
    não dizia nada. O custo agora tem um aviso com nome."""
    corpos, relogio = [], _Relogio()
    ch = _canal(_handler_status({"id": "creation123"}, corpos=corpos), sleep=relogio)
    res = ch.publish(make_post())
    assert res.ok and res.message_id == "story789"      # o story vai ao ar
    assert len([k for k, _ in corpos if k == "status"]) == 5
    assert relogio.pausas == [1.0] * 4                  # 4 s por story, todo dia
    assert ch.warnings == [AVISO_POLLING_CEGO]
    assert "status_code" in AVISO_POLLING_CEGO and "instagram_story" in AVISO_POLLING_CEGO


def test_status_code_desconhecido_espera_publica_e_nao_e_polling_cego():
    """"PUBLISHED" não é FINISHED nem terminal: o canal espera as 5 leituras e
    publica assim mesmo. Mas LEU o status — não é polling cego, e o erro (se o
    publish falhar) diz o status de verdade."""
    corpos, relogio = [], _Relogio()
    ch = _canal(_handler_status({"status_code": "PUBLISHED", "status": "Published"},
                                corpos=corpos), sleep=relogio)
    assert ch.publish(make_post()).ok
    assert len([k for k, _ in corpos if k == "status"]) == 5
    assert relogio.pausas == [1.0] * 4
    assert ch.warnings == []


def test_publish_falho_com_status_desconhecido_diz_o_status_lido():
    def handler(request):
        if request.url.path.endswith("/media_publish"):
            return httpx.Response(400, json={"error": {"message": "sem permissão"}})
        return _handler_status({"status_code": "PUBLISHED", "status": "Published"})(request)

    res = _canal(handler).publish(make_post())
    assert not res.ok
    assert "sem permissão" in res.error and "PUBLISHED" in res.error


def test_o_polling_cego_vira_uma_linha_por_dia_no_resumo(tmp_path, monkeypatch):
    """O aviso do canal precisa CHEGAR ao chat de operações — o pipeline
    recolhe o que o canal juntou publicando e o passa pelo mesmo `warn` do
    resto (logo: uma vez por dia local)."""
    from afiliado import llm, pipeline
    from afiliado.state import StateDB
    from tests.test_models import make_offer
    from tests.test_pipeline import CFG, FakeSource, no_network_validator

    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    ch = _canal(_handler_status({"id": "creation123"}))
    summary = pipeline.run(CFG, [FakeSource([make_offer(item_id="a")])], [ch], db,
                           validator=no_network_validator)
    assert AVISO_POLLING_CEGO in summary.warnings
    assert ch.warnings == []                 # drenado pelo pipeline
    segundo = pipeline.run(CFG, [FakeSource([make_offer(item_id="b")])], [ch], db,
                           validator=no_network_validator)
    assert AVISO_POLLING_CEGO not in segundo.warnings     # mesmo dia: uma vez só
    db.close()


def test_container_normal_nao_avisa_nada():
    ch = _canal(_handler(("IN_PROGRESS", "FINISHED")))
    assert ch.publish(make_post()).ok
    assert ch.warnings == []


def test_variante_instagram_login_usa_o_outro_host():
    vistos = []

    def handler(request):
        vistos.append(request.url.host)
        if request.url.host == "graph.instagram.com":
            return _graph_handler(request, ["FINISHED"], [])
        return _handler()(request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    ch = InstagramStoryChannel("IGUSER", "IGTOKEN", "BOTTOKEN", "OPSCHAT",
                               client=client, sleep=_Relogio(), api="instagram_login")
    assert ch.publish(make_post()).ok
    assert "graph.facebook.com" not in vistos

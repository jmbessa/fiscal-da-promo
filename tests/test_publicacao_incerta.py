"""Fase 5X — quando o `media_publish` falha, NÃO se pode afirmar que nada foi
ao ar. E afirmar isso custou a conta.

**O defeito, medido na produção em 2026-09-02.** A conta tinha 5 carrosséis
IDÊNTICOS ("COMO EU CONFIRO UM DESCONTO") e três posts de feed publicados no
MESMO run de 15 minutos — com `instagram_feed.max_per_day: 2`. O `posted` não
tinha uma linha sequer de `instagram_feed` nem de `instagram_carrossel` desde
2026-08-29, enquanto a Graph API listava dezenas.

A cadeia:

1. o `media_publish` respondeu erro (`Application request limit reached` — o
   teto de CHAMADAS do app, não o de publicação, que estava em 6 de 100);
2. o canal devolveu `ok=False` **e `publicado=False`**;
3. o pipeline só grava em `posted` quando `res.ok or res.publicado`, então não
   gravou nada;
4. sem linha em `posted`, `count_posts_today` fica em 0 — o teto do dia, o
   `max_per_run` e o dedupe todos passam a valer para um post que EXISTE;
5. o run seguinte republica a mesma peça, gasta mais chamadas, estoura mais o
   limite — e o laço se alimenta.

**A regra que fecha isso:** a partir do instante em que o `creation_id` existe
e o `media_publish` foi CHAMADO, não há como saber se o post existe. Erro de
resposta não é prova de que nada aconteceu — a Meta processa a criação e pode
devolver erro depois. Nesse ponto o resultado carrega `publicado=True`, e o
teto e o dedupe contam a peça.

O custo de errar para cada lado não é simétrico, e é isso que decide:
- errar dizendo "publicou" quando não publicou custa UMA peça naquele dia;
- errar dizendo "não publicou" quando publicou custa um laço de republicação
  sem teto, que foi o que aconteceu.

É a mesma decisão que a fase 5F tomou para o story do instagrapi — o que
faltava era ela valer também para os canais da Graph API.
"""

import io

import httpx
import pytest
from PIL import Image

from afiliado.channels.instagram_feed import InstagramFeedChannel
from afiliado.channels.instagram_reel import InstagramReelChannel
from afiliado.channels.instagram_story import InstagramStoryChannel
from tests.test_state import make_post

LIMITE = {"error": {"message": "Application request limit reached", "code": 4}}


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (400, 400), (10, 20, 30)).save(buf, "PNG")
    return buf.getvalue()


def _handler(*, media_ok: bool = True, publish_ok: bool = True):
    """A Meta dublê: o container criado (`/media`) e a publicação
    (`/media_publish`) falham de forma independente."""

    def handler(request):
        host, path = request.url.host, request.url.path
        if host in ("cf.shopee.com.br", "http2.mlstatic.com"):
            return httpx.Response(200, content=_png(),
                                  headers={"content-type": "image/png"})
        if host == "api.telegram.org":
            if path.endswith(("/sendPhoto", "/sendVideo")):
                return httpx.Response(200, json={"ok": True, "result": {
                    "message_id": 5, "photo": [{"file_id": "big"}],
                    "video": {"file_id": "big"}}})
            if path.endswith("/getFile"):
                return httpx.Response(200, json={"ok": True,
                                                 "result": {"file_path": "p/f.jpg"}})
        if host == "graph.facebook.com":
            if path.endswith("/content_publishing_limit"):
                return httpx.Response(200, json={"data": [{
                    "config": {"quota_total": 100, "quota_duration": 86400},
                    "quota_usage": 1}]})
            if path.endswith("/media_publish"):
                return (httpx.Response(200, json={"id": "post456"}) if publish_ok
                        else httpx.Response(400, json=LIMITE))
            if path.endswith("/media"):
                return (httpx.Response(200, json={"id": "creation123"}) if media_ok
                        else httpx.Response(400, json=LIMITE))
            if "creation123" in path:
                return httpx.Response(200, json={"status_code": "FINISHED"})
        return httpx.Response(404)

    return handler


def _canal(cls, handler):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return cls("IGUSER", "IGTOKEN", "BOTTOKEN", "OPSCHAT", client=client)


CANAIS = [InstagramFeedChannel, InstagramStoryChannel, InstagramReelChannel]
IDS = [c.name for c in CANAIS]


# -- a incerteza começa no `media_publish` ------------------------------------

@pytest.mark.parametrize("cls", CANAIS, ids=IDS)
def test_media_publish_que_falha_marca_a_peca_como_PUBLICADA(cls):
    """O container foi criado e a publicação foi CHAMADA: daqui em diante a
    peça pode estar na conta, e a resposta de erro não prova o contrário.

    `ok=False` continua — sucesso não foi, e a oferta vai para `discarded` com
    o motivo. O que muda é `publicado`, que é quem faz o teto do dia e o dedupe
    contarem a peça e o run seguinte NÃO republicar."""
    res = _canal(cls, _handler(publish_ok=False)).publish(make_post())
    assert res.ok is False
    assert res.publicado is True, (
        f"{cls.name}: sem isto o teto fica em 0 e o run seguinte republica")
    assert "limit" in res.error.lower()


@pytest.mark.parametrize("cls", CANAIS, ids=IDS)
def test_container_que_nem_foi_criado_NAO_marca_publicado(cls):
    """Aqui a certeza existe: sem `creation_id` o `media_publish` nunca foi
    chamado, e nada pode ter ido ao ar. Marcar `publicado` aqui gastaria uma
    vaga do dia por um post que não existe."""
    res = _canal(cls, _handler(media_ok=False)).publish(make_post())
    assert res.ok is False
    assert res.publicado is False


@pytest.mark.parametrize("cls", CANAIS, ids=IDS)
def test_o_caminho_feliz_continua_feliz(cls):
    res = _canal(cls, _handler()).publish(make_post())
    assert res.ok is True
    assert res.message_id == "post456"


# -- o carrossel, que é o que estava saindo cinco vezes ------------------------

def test_carrossel_com_publish_falho_marca_publicado():
    canal = _canal(InstagramFeedChannel, _handler(publish_ok=False))
    res = canal.publish_carrossel([_png(), _png()], "legenda")
    assert res.ok is False
    assert res.publicado is True


def test_carrossel_sem_container_pai_nao_marca_publicado():
    canal = _canal(InstagramFeedChannel, _handler(media_ok=False))
    res = canal.publish_carrossel([_png(), _png()], "legenda")
    assert res.ok is False
    assert res.publicado is False

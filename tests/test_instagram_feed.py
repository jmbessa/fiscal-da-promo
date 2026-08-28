import io
from urllib.parse import parse_qs

import httpx
from PIL import Image

from afiliado import pricing
from afiliado.channels.instagram_feed import InstagramFeedChannel
from afiliado.models import NO_CLAIM, Verdict
from tests.test_models import make_offer_ref
from tests.test_state import make_post


def _product_png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (800, 800), (10, 20, 30)).save(buf, "PNG")
    return buf.getvalue()


def _happy_handler(request):
    host = request.url.host
    path = request.url.path
    if host == "cf.shopee.com.br":
        return httpx.Response(200, content=_product_png(), headers={"content-type": "image/png"})
    if host == "api.telegram.org":
        if path.endswith("/sendPhoto"):
            return httpx.Response(200, json={"ok": True, "result": {
                "message_id": 5,
                "photo": [{"file_id": "small"}, {"file_id": "big"}],
            }})
        if path.endswith("/getFile"):
            return httpx.Response(200, json={"ok": True, "result": {"file_path": "photos/file_2.jpg"}})
    if host == "graph.facebook.com":
        if path.endswith("/media"):
            return httpx.Response(200, json={"id": "creation123"})
        if path.endswith("/media_publish"):
            return httpx.Response(200, json={"id": "post456"})
    return httpx.Response(404)


def _channel_with(handler) -> InstagramFeedChannel:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return InstagramFeedChannel("IGUSER", "IGTOKEN", "BOTTOKEN", "OPSCHAT", client=client)


def test_publish_happy_path():
    res = _channel_with(_happy_handler).publish(make_post())
    assert res.ok
    assert res.message_id == "post456"


def test_max_per_run_class_attribute():
    assert InstagramFeedChannel.max_per_run == 1


# -- Fase 5C (M4/A5): a arte não é hospedada pelo bot do CANAL -----------------

def test_arte_hospedada_pelo_bot_secundario():
    """A5: `api.telegram.org/file/bot{TOKEN}/...` vai como `image_url` para a
    Meta — o que expira é o file_path, o token é o segredo PERMANENTE do
    administrador do canal público. Com ART_HOST_BOT_TOKEN, quem aparece na
    URL é um bot sem direitos no canal."""
    urls = []

    def handler(request):
        if request.url.host == "graph.facebook.com" and request.url.path.endswith("/media"):
            urls.append(parse_qs(request.content.decode())["image_url"][0])
        return _happy_handler(request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    ch = InstagramFeedChannel("IGUSER", "IGTOKEN", "BOTDOCANAL", "OPSCHAT", client=client,
                              art_host_bot_token="BOTDEARTE")
    assert ch.publish(make_post()).ok
    assert "/bot BOTDEARTE".replace(" ", "") in urls[0]
    assert "BOTDOCANAL" not in urls[0]


def test_sem_bot_secundario_a_arte_continua_saindo_pelo_bot_do_canal():
    urls = []

    def handler(request):
        if request.url.host == "graph.facebook.com" and request.url.path.endswith("/media"):
            urls.append(parse_qs(request.content.decode())["image_url"][0])
        return _happy_handler(request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    ch = InstagramFeedChannel("IGUSER", "IGTOKEN", "BOTDOCANAL", "OPSCHAT", client=client)
    assert ch.publish(make_post()).ok
    assert "botBOTDOCANAL" in urls[0]     # comportamento atual, com aviso no cli


def test_o_bot_secundario_so_hospeda_a_arte():
    """A mensagem de hospedagem vai pelo bot secundário; o chat de operações é
    o mesmo (é lá que o bot secundário precisa estar)."""
    enviados = []

    def handler(request):
        if request.url.host == "api.telegram.org":
            enviados.append(request.url.path)
        return _happy_handler(request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    InstagramFeedChannel("IGUSER", "IGTOKEN", "BOTDOCANAL", "OPSCHAT", client=client,
                         art_host_bot_token="BOTDEARTE").publish(make_post())
    assert all("BOTDEARTE" in p for p in enviados)


def test_caption_sent_to_media_never_contains_link():
    captured = {}

    def handler(request):
        if request.url.host == "graph.facebook.com" and request.url.path.endswith("/media"):
            captured["body"] = request.content
        return _happy_handler(request)

    res = _channel_with(handler).publish(make_post())
    assert res.ok
    parsed = parse_qs(captured["body"].decode())
    caption = parsed["caption"][0]
    assert "http" not in caption.lower()


def test_media_without_id_returns_false():
    def handler(request):
        if request.url.host == "graph.facebook.com" and request.url.path.endswith("/media"):
            return httpx.Response(400, json={"error": {"message": "bad image_url"}})
        return _happy_handler(request)

    res = _channel_with(handler).publish(make_post())
    assert not res.ok
    assert res.error


def test_instagram_publish_never_raises_on_invalid_url():
    # Mid-string control char in ig_user_id (e.g. env var copied with an
    # embedded newline) makes the Graph API URL invalid; httpx.InvalidURL is
    # NOT an httpx.HTTPError subclass — publish must still never raise.
    client = httpx.Client(transport=httpx.MockTransport(_happy_handler))
    ch = InstagramFeedChannel("12\n34", "IGTOKEN", "BOTTOKEN", "OPSCHAT", client=client)
    res = ch.publish(make_post())
    assert not res.ok
    assert res.error


def test_caption_title_sanitized_when_it_smuggles_a_url():
    captured = {}

    def handler(request):
        if request.url.host == "graph.facebook.com" and request.url.path.endswith("/media"):
            captured["body"] = request.content
        return _happy_handler(request)

    post = make_post(title="Produto X http://spam.com compre já")
    res = _channel_with(handler).publish(post)
    assert res.ok
    parsed = parse_qs(captured["body"].decode())
    caption = parsed["caption"][0]
    assert "Produto X" in caption
    assert "http" not in caption.lower()


def _png_bytes():
    import io
    from PIL import Image
    buf = io.BytesIO(); Image.new("RGB", (400, 400), (90, 30, 200)).save(buf, "PNG"); return buf.getvalue()


def _full_flow_handler(graph_host, seen):
    def handler(request):
        host, path = request.url.host, request.url.path
        seen.append(host + path)
        if host.endswith("susercontent.com") or host.endswith("shopee.com.br"):
            return httpx.Response(200, headers={"content-type": "image/png"}, content=_png_bytes())
        if host == "api.telegram.org" and path.endswith("/sendPhoto"):
            seen.append(("photo_body", request.content))
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1, "photo": [{"file_id": "f1"}]}})
        if host == "api.telegram.org" and path.endswith("/getFile"):
            return httpx.Response(200, json={"ok": True, "result": {"file_path": "photos/x.jpg"}})
        if host == graph_host and path.endswith("/media_publish"):
            return httpx.Response(200, json={"id": "222"})
        if host == graph_host and path.endswith("/media"):
            return httpx.Response(200, json={"id": "111"})
        return httpx.Response(404, json={"error": f"rota inesperada {host}{path}"})
    return handler


def test_instagram_login_variant_uses_graph_instagram_host():
    seen = []
    client = httpx.Client(transport=httpx.MockTransport(_full_flow_handler("graph.instagram.com", seen)))
    ch = InstagramFeedChannel("17841400000", "tok", "bot", "999", client=client, api="instagram_login")
    res = ch.publish(make_post())
    assert res.ok and res.message_id == "222"
    assert not any(isinstance(x, str) and x.startswith("graph.facebook.com") for x in seen)


def test_art_is_hosted_as_jpeg():
    seen = []
    client = httpx.Client(transport=httpx.MockTransport(_full_flow_handler("graph.facebook.com", seen)))
    ch = InstagramFeedChannel("17841400000", "tok", "bot", "999", client=client)
    assert ch.publish(make_post()).ok
    body = next(b for k, b in (x for x in seen if isinstance(x, tuple)) if k == "photo_body")
    assert b"art.jpg" in body and b"image/jpeg" in body
    assert bytes.fromhex("ffd8ff") in body          # magic number do JPEG


def _caption_for(verdict: Verdict | None = None, minimo: int = 10, **offer_kw) -> str:
    """Legenda para um post cujo veredito é `verdict` ou, se ausente, o que
    `pricing.verdict(offer, minimo)` decide."""
    client = httpx.Client(transport=httpx.MockTransport(_happy_handler))
    canal = InstagramFeedChannel("IGUSER", "IGTOKEN", "BOTTOKEN", "OPSCHAT", client=client)
    post = make_post(**offer_kw)
    post.verdict = verdict if verdict is not None else pricing.verdict(post.offer, minimo)
    return canal._build_caption(post)


def _ref(**kw):
    return dict(price_ref_cents=2600, price_p25_cents=2600, price_window_days=90, **kw)


def test_caption_respeita_o_veredito_do_post():
    # 20% verificado (26,00 -> 20,80): com veredito A a legenda alega; com o
    # veredito B (mínimo 30 no config) sai no modo B — sem "OFF". O canal
    # não recalcula nada.
    assert "(20% OFF)" in _caption_for(**_ref(price_current_cents=2080))
    caption = _caption_for(minimo=30, **_ref(price_current_cents=2080))
    assert "OFF" not in caption
    assert "R$ 20,80" in caption
    assert "R$ 26,00" not in caption
    assert "OFF" not in _caption_for(verdict=NO_CLAIM, **_ref(price_current_cents=2080))


def test_caption_modo_a_usa_a_nossa_referencia():
    caption = _caption_for(**_ref(price_original_cents=35000, price_current_cents=1890))
    assert "De: R$ 26,00 | Por: R$ 18,90 (27% OFF)" in caption
    assert "R$ 350,00" not in caption


def test_caption_modo_b_sem_referencia_nao_alega_desconto():
    caption = _caption_for(price_original_cents=35000, price_current_cents=4900,
                           rating=4.9, sales=30000)
    assert "R$ 49,00" in caption
    assert "R$ 350,00" not in caption
    assert "OFF" not in caption
    assert "⭐ 4,9 · 30 mil vendidos" in caption


# -- Fase 5D: publicar CARROSSEL (um post, N imagens) -------------------------

def _carrossel_handler(seen, filho_falha=None, pai_falha=False, publish_falha=False,
                       status="FINISHED"):
    """Rotas da Graph API do carrossel: um container por imagem
    (`is_carousel_item`), um container CAROUSEL com `children`, e o publish."""
    filhos = {"n": 0}

    def handler(request):
        host, path = request.url.host, request.url.path
        if host == "cf.shopee.com.br":
            return httpx.Response(200, content=_product_png(),
                                  headers={"content-type": "image/png"})
        if host == "api.telegram.org":
            if path.endswith("/sendPhoto"):
                return httpx.Response(200, json={"ok": True, "result": {
                    "message_id": 5, "photo": [{"file_id": "big"}]}})
            if path.endswith("/getFile"):
                return httpx.Response(200, json={"ok": True, "result": {
                    "file_path": "photos/f.jpg"}})
        if host != "graph.facebook.com":
            return httpx.Response(404)
        if request.method == "GET":
            seen.append(("status", str(request.url)))
            return httpx.Response(200, json={"status_code": status, "status": "ok"})
        corpo = parse_qs(request.content.decode())
        if path.endswith("/media_publish"):
            seen.append(("publish", corpo))
            if publish_falha:
                return httpx.Response(400, json={"error": {"message": "publish recusado"}})
            return httpx.Response(200, json={"id": "carrossel999"})
        if path.endswith("/media"):
            if corpo.get("media_type", [""])[0] == "CAROUSEL":
                seen.append(("pai", corpo))
                if pai_falha:
                    return httpx.Response(400, json={"error": {"message": "children inválido"}})
                return httpx.Response(200, json={"id": "pai1"})
            filhos["n"] += 1
            seen.append(("filho", corpo))
            if filho_falha == filhos["n"]:
                return httpx.Response(400, json={"error": {"message": "imagem recusada"}})
            return httpx.Response(200, json={"id": f"filho{filhos['n']}"})
        return httpx.Response(404)

    return handler


def _canal_carrossel(handler) -> InstagramFeedChannel:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return InstagramFeedChannel("IGUSER", "IGTOKEN", "BOTTOKEN", "OPSCHAT",
                                client=client, sleep=lambda s: None)


def _imagens(n: int = 3) -> list[bytes]:
    return [_product_png() for _ in range(n)]


def test_publish_carrossel_manda_is_carousel_item_children_e_publica():
    seen = []
    res = _canal_carrossel(_carrossel_handler(seen)).publish_carrossel(
        _imagens(3), "legenda do carrossel")
    assert res.ok and res.message_id == "carrossel999"

    filhos = [c for k, c in seen if k == "filho"]
    assert len(filhos) == 3
    for corpo in filhos:
        assert corpo["is_carousel_item"] == ["true"]
        assert "caption" not in corpo          # a legenda é do PAI, não dos filhos
        assert "media_type" not in corpo

    pai = next(c for k, c in seen if k == "pai")
    assert pai["media_type"] == ["CAROUSEL"]
    assert pai["children"] == ["filho1,filho2,filho3"]
    assert pai["caption"] == ["legenda do carrossel"]

    # UM post: um único media_publish para as três imagens. É isso que faz o
    # formato caber na cota de 100/24 h da Meta.
    assert len([k for k, _ in seen if k == "publish"]) == 1


def test_publish_carrossel_falha_em_um_filho_nao_cria_o_pai():
    seen = []
    res = _canal_carrossel(_carrossel_handler(seen, filho_falha=2)).publish_carrossel(
        _imagens(3), "legenda")
    assert not res.ok
    assert "imagem recusada" in res.error and "2" in res.error
    assert not [k for k, _ in seen if k in ("pai", "publish")]


def test_publish_carrossel_falha_no_pai_nao_publica():
    seen = []
    res = _canal_carrossel(_carrossel_handler(seen, pai_falha=True)).publish_carrossel(
        _imagens(2), "legenda")
    assert not res.ok and "children inválido" in res.error
    assert not [k for k, _ in seen if k == "publish"]


def test_publish_carrossel_falha_no_publish_vira_resultado_falho():
    seen = []
    res = _canal_carrossel(_carrossel_handler(seen, publish_falha=True)).publish_carrossel(
        _imagens(2), "legenda")
    assert not res.ok and "publish recusado" in res.error


def test_publish_carrossel_container_em_erro_nem_tenta_publicar():
    seen = []
    res = _canal_carrossel(_carrossel_handler(seen, status="ERROR")).publish_carrossel(
        _imagens(2), "legenda")
    assert not res.ok and "ERROR" in res.error
    assert not [k for k, _ in seen if k == "publish"]


def test_publish_carrossel_sem_imagem_nao_toca_a_rede():
    seen = []
    res = _canal_carrossel(_carrossel_handler(seen)).publish_carrossel([], "legenda")
    assert not res.ok and res.error
    assert seen == []


def test_publish_carrossel_recusa_mais_que_o_teto_da_meta():
    seen = []
    res = _canal_carrossel(_carrossel_handler(seen)).publish_carrossel(
        _imagens(11), "legenda")
    assert not res.ok and "10" in res.error
    assert seen == []


def test_publish_carrossel_falha_de_hospedagem_vira_resultado_falho():
    def handler(request):
        if request.url.host == "api.telegram.org":
            return httpx.Response(200, json={"ok": False, "description": "bot removido"})
        return _carrossel_handler([])(request)

    res = _canal_carrossel(handler).publish_carrossel(_imagens(2), "legenda")
    assert not res.ok and "hospedar" in res.error


def test_publish_carrossel_nunca_levanta():
    client = httpx.Client(transport=httpx.MockTransport(_carrossel_handler([])))
    ch = InstagramFeedChannel("12\n34", "IGTOKEN", "BOTTOKEN", "OPSCHAT",
                              client=client, sleep=lambda s: None)
    res = ch.publish_carrossel(_imagens(2), "legenda")
    assert not res.ok and res.error


def test_publish_carrossel_avisa_polling_cego_uma_vez():
    def handler(request):
        if request.url.host == "graph.facebook.com" and request.method == "GET":
            return httpx.Response(200, json={})     # sem status_code
        return _carrossel_handler([])(request)

    ch = _canal_carrossel(handler)
    assert ch.publish_carrossel(_imagens(2), "legenda").ok
    assert len(ch.warnings) == 1
    assert "instagram_feed" in ch.warnings[0] and "polling cego" in ch.warnings[0]


def test_caption_traz_o_selo_do_veredito():
    # C9: a legenda do IG nunca tinha selo enquanto o Telegram tinha.
    offer_kw = _ref(price_current_cents=1890, price_floor_cents=1890, price_floor_window_days=191)
    v = pricing.verdict(make_offer_ref(2600, **{k: v for k, v in offer_kw.items()
                                                if k != "price_ref_cents"}), 10)
    assert v.seal == "🏷️ Menor preço dos últimos 6 meses (verificado)"
    caption = _caption_for(verdict=v, **offer_kw)
    assert "De: R$ 26,00 | Por: R$ 18,90 (27% OFF)\n🏷️ Menor preço dos últimos 6 meses (verificado)" in caption
    assert "Menor preço" not in _caption_for(verdict=NO_CLAIM, **offer_kw)

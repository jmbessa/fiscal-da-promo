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


def test_caption_traz_o_selo_do_veredito():
    # C9: a legenda do IG nunca tinha selo enquanto o Telegram tinha.
    offer_kw = _ref(price_current_cents=1890, price_floor_cents=1890, price_floor_window_days=191)
    v = pricing.verdict(make_offer_ref(2600, **{k: v for k, v in offer_kw.items()
                                                if k != "price_ref_cents"}), 10)
    assert v.seal == "🏷️ Menor preço dos últimos 6 meses (verificado)"
    caption = _caption_for(verdict=v, **offer_kw)
    assert "De: R$ 26,00 | Por: R$ 18,90 (27% OFF)\n🏷️ Menor preço dos últimos 6 meses (verificado)" in caption
    assert "Menor preço" not in _caption_for(verdict=NO_CLAIM, **offer_kw)

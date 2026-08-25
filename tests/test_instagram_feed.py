import io
from urllib.parse import parse_qs

import httpx
from PIL import Image

from afiliado.channels.instagram_feed import InstagramFeedChannel
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

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

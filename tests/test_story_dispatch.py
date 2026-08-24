import io

import httpx
from PIL import Image

from afiliado.channels.story_dispatch import StoryDispatchChannel
from tests.test_state import make_post


def _product_png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (800, 800), (120, 40, 200)).save(buf, "PNG")
    return buf.getvalue()


def _channel_with(handler) -> StoryDispatchChannel:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return StoryDispatchChannel("TOKEN", "OPSCHAT", client=client)


def test_publish_happy_path():
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.host == "cf.shopee.com.br":
            return httpx.Response(200, content=_product_png(), headers={"content-type": "image/png"})
        if request.url.path.endswith("/sendPhoto"):
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 99}})
        if request.url.path.endswith("/sendMessage"):
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 100}})
        return httpx.Response(404)

    post = make_post()
    res = _channel_with(handler).publish(post)

    assert res.ok
    assert res.message_id == "99"

    sendphoto_calls = [r for r in calls if r.url.path.endswith("/sendPhoto")]
    sendmessage_calls = [r for r in calls if r.url.path.endswith("/sendMessage")]
    assert len(sendphoto_calls) == 1
    assert b"art.png" in sendphoto_calls[0].content
    assert len(sendmessage_calls) == 1
    assert post.affiliate_link.encode() in sendmessage_calls[0].content


def test_publish_product_image_404_returns_false_without_raising():
    def handler(request):
        if request.url.host == "cf.shopee.com.br":
            return httpx.Response(404)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    res = _channel_with(handler).publish(make_post())
    assert not res.ok
    assert res.error


def test_publish_sendphoto_failure_returns_false():
    def handler(request):
        if request.url.host == "cf.shopee.com.br":
            return httpx.Response(200, content=_product_png(), headers={"content-type": "image/png"})
        if request.url.path.endswith("/sendPhoto"):
            return httpx.Response(400, json={"ok": False, "description": "bad photo"})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    res = _channel_with(handler).publish(make_post())
    assert not res.ok
    assert "bad photo" in res.error

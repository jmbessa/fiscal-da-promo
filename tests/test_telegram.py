import httpx

from afiliado.channels.telegram import TelegramChannel, send_text
from tests.test_state import make_post


def channel_with(handler) -> TelegramChannel:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return TelegramChannel("TOKEN", "@canal", client=client)


def test_publish_send_photo_ok():
    def handler(request):
        assert request.url.path.endswith("/sendPhoto")
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})
    res = channel_with(handler).publish(make_post())
    assert res.ok and res.message_id == "42"


def test_publish_falls_back_to_send_message():
    def handler(request):
        if request.url.path.endswith("/sendPhoto"):
            return httpx.Response(400, json={"ok": False, "description": "bad photo"})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})
    res = channel_with(handler).publish(make_post())
    assert res.ok and res.message_id == "7"


def test_publish_total_failure():
    def handler(request):
        return httpx.Response(400, json={"ok": False, "description": "nope"})
    res = channel_with(handler).publish(make_post())
    assert not res.ok and "nope" in res.error


def test_publish_retries_network_error():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("down")
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
    res = channel_with(handler).publish(make_post())
    assert res.ok and calls["n"] == 3


def test_send_text_never_raises():
    def handler(request):
        raise httpx.ConnectError("down")
    client = httpx.Client(transport=httpx.MockTransport(handler))
    send_text("TOKEN", "123", "oi", client=client)  # não deve explodir

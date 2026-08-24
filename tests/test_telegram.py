import httpx

from afiliado.channels.telegram import TelegramChannel, get_file_url, send_photo_bytes, send_text
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


def test_publish_non_json_response():
    def handler(request):
        return httpx.Response(502, content=b"<html>Bad Gateway</html>")
    res = channel_with(handler).publish(make_post())
    assert not res.ok and res.error and len(res.error) > 0


def test_send_photo_bytes_ok():
    captured = {}

    def handler(request):
        assert request.url.path.endswith("/sendPhoto")
        captured["body"] = request.content
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 5}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = send_photo_bytes("TOKEN", "123", b"PNGDATA", caption="oi", client=client)
    assert result["ok"] is True
    assert b"art.png" in captured["body"]


def test_send_photo_bytes_network_error_returns_dict():
    def handler(request):
        raise httpx.ConnectError("down")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = send_photo_bytes("TOKEN", "123", b"PNGDATA", client=client)
    assert result == {"ok": False, "description": result["description"]}
    assert result["ok"] is False
    assert "description" in result


def test_get_file_url_ok():
    def handler(request):
        assert request.url.path.endswith("/getFile")
        return httpx.Response(200, json={"ok": True, "result": {"file_path": "photos/file_1.jpg"}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    url = get_file_url("TOKEN", "ABC123", client=client)
    assert url == "https://api.telegram.org/file/botTOKEN/photos/file_1.jpg"


def test_get_file_url_failure_none():
    def handler(request):
        return httpx.Response(400, json={"ok": False, "description": "bad file id"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert get_file_url("TOKEN", "bad", client=client) is None


def test_send_photo_bytes_never_raises_on_invalid_url():
    # Mid-string control char (e.g. a token copy-pasted with an embedded
    # newline) makes httpx raise InvalidURL, which is NOT an httpx.HTTPError
    # subclass — send_photo_bytes must still never raise.
    def handler(request):
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = send_photo_bytes("ab\ncd", "123", b"PNGDATA", client=client)
    assert result["ok"] is False
    assert "description" in result


def test_get_file_url_never_raises_on_invalid_url():
    def handler(request):
        return httpx.Response(200, json={"ok": True, "result": {"file_path": "x.jpg"}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert get_file_url("ab\ncd", "file123", client=client) is None

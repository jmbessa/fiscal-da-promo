import json

import httpx

from afiliado.channels.telegram import (TelegramChannel, get_file_url, send_photo_bytes,
                                        send_text, split_message)
from tests.test_state import make_post


def channel_with(handler, sleep=None) -> TelegramChannel:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    if sleep is None:
        return TelegramChannel("TOKEN", "@canal", client=client)
    return TelegramChannel("TOKEN", "@canal", client=client, sleep=sleep)


def _429(retry_after: int) -> httpx.Response:
    return httpx.Response(429, json={
        "ok": False, "error_code": 429,
        "description": f"Too Many Requests: retry after {retry_after}",
        "parameters": {"retry_after": retry_after}})


# -- send_text: resumo de ops nunca some em silêncio (C5) ----------------------

def test_send_text_divide_em_mensagens_de_ate_4000_e_devolve_true():
    enviados = []

    def handler(request):
        enviados.append(json.loads(request.content)["text"])
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    texto = "\n".join(f"• linha {i} " + "x" * 100 for i in range(90))   # ~9.8k chars
    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert send_text("TOKEN", "123", texto, client=client) is True
    assert len(enviados) >= 3
    assert all(len(t) <= 4000 for t in enviados)
    assert "\n".join(enviados) == texto        # cortes só em quebras de linha, nada perdido


def test_send_text_curto_vai_numa_mensagem_so():
    enviados = []

    def handler(request):
        enviados.append(json.loads(request.content)["text"])
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert send_text("TOKEN", "123", "oi\ntudo bem", client=client) is True
    assert enviados == ["oi\ntudo bem"]


def test_send_text_ok_false_devolve_false_e_imprime(capsys):
    def handler(request):
        return httpx.Response(400, json={"ok": False, "error_code": 400,
                                         "description": "Bad Request: message is too long"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert send_text("TOKEN", "123", "x" * 10, client=client) is False
    assert "message is too long" in capsys.readouterr().out


def test_send_text_erro_de_rede_devolve_false_e_imprime(capsys):
    def handler(request):
        raise httpx.ConnectError("down")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert send_text("TOKEN", "123", "oi", client=client) is False
    assert "down" in capsys.readouterr().out


def test_split_message_corta_linha_gigante():
    partes = split_message("a" * 9000 + "\nfim", limit=4000)
    assert all(len(p) <= 4000 for p in partes)
    assert "".join(partes).replace("\n", "") == "a" * 9000 + "fim"


# -- 429 com retry_after (A4) ----------------------------------------------------

def test_publish_429_com_retry_after_curto_dorme_e_tenta_uma_vez():
    calls, sleeps = [], []

    def handler(request):
        calls.append(request.url.path.rsplit("/", 1)[-1])
        if len(calls) == 1:
            return _429(5)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 9}})

    res = channel_with(handler, sleep=sleeps.append).publish(make_post())
    assert res.ok and res.message_id == "9"
    assert calls == ["sendPhoto", "sendPhoto"]
    assert sleeps == [5]


def test_publish_429_com_retry_after_longo_falha_sem_dormir_e_sem_fallback():
    # Acima de 30 s não vale esperar; e sendMessage não ajuda num rate limit —
    # antes o canal fazia 2 chamadas imediatas e o pipeline seguia para a
    # PRÓXIMA oferta dentro da mesma janela de limite.
    calls, sleeps = [], []

    def handler(request):
        calls.append(request.url.path.rsplit("/", 1)[-1])
        return _429(35)

    res = channel_with(handler, sleep=sleeps.append).publish(make_post())
    assert not res.ok and "retry after 35" in res.error
    assert calls == ["sendPhoto"]
    assert sleeps == []


def test_publish_429_persistente_falha_depois_de_uma_repeticao():
    calls, sleeps = [], []

    def handler(request):
        calls.append(request.url.path.rsplit("/", 1)[-1])
        return _429(3)

    res = channel_with(handler, sleep=sleeps.append).publish(make_post())
    assert not res.ok
    assert calls == ["sendPhoto", "sendPhoto"]
    assert sleeps == [3]


def test_send_text_honra_retry_after():
    calls, sleeps = [], []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return _429(2)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert send_text("TOKEN", "123", "oi", client=client, sleep=sleeps.append) is True
    assert sleeps == [2] and len(calls) == 2


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


def test_telegram_channel_never_raises_on_invalid_url():
    # Mid-string control char in bot_token (e.g. copy-pasted with an embedded
    # newline) makes the sendPhoto/sendMessage URL invalid; httpx.InvalidURL
    # is NOT an httpx.HTTPError subclass — publish() must still never raise.
    def handler(request):
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    ch = TelegramChannel("ab\ncd", "@c", client=client)
    res = ch.publish(make_post())
    assert res.ok is False
    assert res.error

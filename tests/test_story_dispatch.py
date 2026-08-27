import io

import httpx
from PIL import Image

from afiliado import creative
from afiliado.channels.story_dispatch import StoryDispatchChannel
from tests.test_state import make_post


def _product_png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (800, 800), (120, 40, 200)).save(buf, "PNG")
    return buf.getvalue()


def _channel_with(handler) -> StoryDispatchChannel:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return StoryDispatchChannel("TOKEN", "OPSCHAT", client=client)


def test_min_real_discount_pct_do_canal_decide_o_modo_da_arte():
    # Config com mínimo 30 e oferta com 20% verificado (26,00 -> 20,80): a
    # arte enviada tem que ser a do modo B (sem selo de porcentagem) — a
    # mesma que render_story produz com esse limite — e NÃO a do padrão (10),
    # que traria o selo -20%.
    enviados = []

    def handler(request):
        if request.url.host == "cf.shopee.com.br":
            return httpx.Response(200, content=_product_png(), headers={"content-type": "image/png"})
        if request.url.path.endswith("/sendPhoto"):
            enviados.append(request.content)
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 2}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    canal = StoryDispatchChannel("TOKEN", "OPSCHAT", client=client, min_real_discount_pct=30)
    post = make_post(price_ref_cents=2600, price_current_cents=2080)
    assert post.offer.real_discount_pct == 20
    assert canal.publish(post).ok

    modo_b = creative.render_story(post.offer, post.copy, client=client, min_real_discount_pct=30)
    modo_a = creative.render_story(post.offer, post.copy, client=client, min_real_discount_pct=10)
    assert modo_a != modo_b
    assert modo_b in enviados[0]
    assert modo_a not in enviados[0]


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

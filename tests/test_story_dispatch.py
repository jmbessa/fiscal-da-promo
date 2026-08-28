import io

import httpx
from PIL import Image

from afiliado import creative, pricing
from afiliado.channels.story_dispatch import StoryDispatchChannel
from afiliado.models import NO_CLAIM, Verdict
from tests.test_state import make_post


def _product_png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (800, 800), (120, 40, 200)).save(buf, "PNG")
    return buf.getvalue()


def _channel_with(handler) -> StoryDispatchChannel:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return StoryDispatchChannel("TOKEN", "OPSCHAT", client=client)


def test_o_canal_so_declara_que_e_manual():
    """Menor da revisão da 5C: `dispatch_note` era uma cópia morta de
    `pipeline.DESPACHO_MANUAL` — dois lugares para mudar o mesmo texto, e
    ninguém lia este. Quem escreve o resumo é o pipeline; o canal só declara
    que é manual."""
    from afiliado import pipeline
    assert StoryDispatchChannel.manual is True
    assert not hasattr(StoryDispatchChannel, "dispatch_note")
    assert pipeline.DESPACHO_MANUAL


def test_veredito_do_post_decide_o_modo_da_arte():
    # Oferta com 20% verificável (26,00 -> 20,80) e veredito B (mínimo 30 no
    # config): a arte enviada é a do modo B — a mesma que render_story
    # produz com esse veredito — e NÃO a do modo A. O canal não recalcula.
    enviados = []

    def handler(request):
        if request.url.host == "cf.shopee.com.br":
            return httpx.Response(200, content=_product_png(), headers={"content-type": "image/png"})
        if request.url.path.endswith("/sendPhoto"):
            enviados.append(request.content)
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 2}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    canal = StoryDispatchChannel("TOKEN", "OPSCHAT", client=client)
    offer_kw = dict(price_ref_cents=2600, price_p25_cents=2600, price_window_days=90,
                    price_current_cents=2080)
    post = make_post(verdict=NO_CLAIM, **offer_kw)
    assert post.offer.real_discount_pct == 20
    assert pricing.verdict(post.offer, 30) == NO_CLAIM
    assert canal.publish(post).ok

    modo_b = creative.render_story(post.offer, post.copy, NO_CLAIM, client=client)
    modo_a = creative.render_story(post.offer, post.copy, pricing.verdict(post.offer, 10),
                                   client=client)
    assert modo_a != modo_b
    assert modo_b in enviados[0]
    assert modo_a not in enviados[0]


def test_caption_do_despacho_traz_preco_e_selo_do_veredito():
    v = Verdict("A", 27, "🏷️ Menor preço dos últimos 3 meses (verificado)", 90)
    post = make_post(verdict=v, price_ref_cents=2600, price_current_cents=1890)
    caption = StoryDispatchChannel._build_caption(post)
    assert "STORY PRONTO" in caption and "Tênis Nike SB" in caption
    assert "De: R$ 26,00 | Por: R$ 18,90 sem cupom (27% OFF)" in caption
    assert "🏷️ Menor preço dos últimos 3 meses (verificado)" in caption
    neutra = StoryDispatchChannel._build_caption(make_post(price_current_cents=1890, rating=4.9))
    assert "R$ 18,90" in neutra and "⭐ 4,9" in neutra
    assert "OFF" not in neutra and "Menor preço" not in neutra


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

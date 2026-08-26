"""Teste obrigatório 5 (C9): texto, arte e legendas concordam — o MESMO
`Verdict` produz selo em todos ou em nenhum, e o mesmo "(N% OFF)" (ou
nenhum) em todos. Nenhum consumidor recalcula: quem decide é
`pricing.verdict`, uma vez. A arte é verificada pelo hook `story_plan`/
`feed_plan` (o rótulo do selo que ela desenha), não por pixel."""

import httpx

from afiliado import creative, message, pricing
from afiliado.channels.instagram_feed import InstagramFeedChannel
from afiliado.channels.story_dispatch import StoryDispatchChannel
from afiliado.models import NO_CLAIM, CopyParts, Post, Verdict
from tests.test_models import make_offer, make_offer_ref

COPY = CopyParts(headline="Achado", description="Vale o clique", cta="Vai")
SELO = "🏷️ Menor preço dos últimos 3 meses (verificado)"


def _sem_rede(request):
    raise AssertionError(f"rede inesperada: {request.url}")


def _post(offer, verdict) -> Post:
    return Post(offer=offer, copy=COPY, affiliate_link="https://shope.ee/x",
                message_text=message.build_message(offer, COPY, "https://shope.ee/x", verdict),
                verdict=verdict)


def _legendas(post: Post) -> list[str]:
    ig = InstagramFeedChannel("u", "t", "b", "o",
                              client=httpx.Client(transport=httpx.MockTransport(_sem_rede)))
    return [post.message_text, ig._build_caption(post), StoryDispatchChannel._build_caption(post)]


def test_mesmo_veredito_selo_em_todos_ou_em_nenhum():
    offer = make_offer_ref(2600, price_current_cents=1890, rating=4.9, sales=30000,
                           price_floor_cents=1890, price_floor_window_days=90)
    v = pricing.verdict(offer, 10)
    assert v == Verdict("A", 27, SELO, 90)

    post = _post(offer, v)
    for texto in _legendas(post):
        assert SELO in texto
        assert "(27% OFF)" in texto and "R$ 26,00" in texto
    for plan in (creative.story_plan(offer, v), creative.feed_plan(offer, v)):
        assert plan["selo"] == "MENOR PREÇO VERIFICADO · 3 MESES"
        assert plan["badge_pct"] == 27 and plan["riscado"] == "R$ 26,00"

    # Mesma oferta, veredito sem nada: nenhum consumidor inventa selo ou OFF.
    post_b = _post(offer, NO_CLAIM)
    for texto in _legendas(post_b):
        assert "Menor preço" not in texto and "🏷️" not in texto
        assert "OFF" not in texto and "R$ 26,00" not in texto and "R$ 18,90" in texto
    for plan in (creative.story_plan(offer, NO_CLAIM), creative.feed_plan(offer, NO_CLAIM)):
        assert plan["selo"] == "" and plan["badge_pct"] == 0 and plan["riscado"] == ""


def test_selo_do_ml_aparece_na_arte_e_nas_legendas():
    # C9: para o ML a arte NUNCA tinha selo (a watchlist só conhece IDs da
    # Shopee) enquanto o Telegram dizia "menor preço já registrado". Agora o
    # selo vem do veredito, igual para todo mundo — inclusive a arte.
    offer = make_offer(source="meli", item_id="MLB1", price_current_cents=3051,
                       price_ref_cents=7890, price_p25_cents=3051, price_window_days=91,
                       price_floor_cents=3051, price_floor_window_days=365)
    v = pricing.verdict(offer, 10)
    assert v.mode == "B"                       # 3051 == p25, não abaixo: sem "De/Por"
    assert v.seal == "🏷️ Menor preço dos últimos 12 meses (verificado)"
    post = _post(offer, v)
    assert all(v.seal in texto and "OFF" not in texto for texto in _legendas(post))
    assert creative.story_plan(offer, v)["selo"] == "MENOR PREÇO VERIFICADO · 12 MESES"
    assert creative.feed_plan(offer, v)["selo"] == "MENOR PREÇO VERIFICADO · 12 MESES"

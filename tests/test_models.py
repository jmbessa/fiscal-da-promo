import dataclasses

import pytest

from afiliado.models import NO_CLAIM, CopyParts, Offer, Post, Verdict, format_brl


def make_offer(**kw) -> Offer:
    base = dict(
        source="shopee",
        item_id="123456",
        title="Tênis Nike SB",
        price_original_cents=49999,
        price_current_cents=24999,
        commission_pct=12.0,
        image_url="https://cf.shopee.com.br/file/abc.jpg",
        product_url="https://shopee.com.br/product/1/123456",
    )
    base.update(kw)
    return Offer(**base)


def make_offer_ref(ref_cents: int, **kw) -> Offer:
    """Oferta com referência COMPLETA para os testes dos consumidores: mediana
    = p25 = `ref_cents` e janela de 90 dias — qualquer preço abaixo da
    referência já está no quartil barato. A regra do quartil em si é testada
    em test_pricing com históricos reais."""
    base = dict(price_ref_cents=ref_cents, price_p25_cents=ref_cents, price_window_days=90)
    base.update(kw)
    return make_offer(**base)


def test_format_brl():
    assert format_brl(24999) == "R$ 249,99"
    assert format_brl(1234567) == "R$ 12.345,67"
    assert format_brl(500) == "R$ 5,00"


def test_discount_pct():
    assert make_offer().discount_pct == 50
    assert make_offer(price_original_cents=0).discount_pct == 0


def test_real_discount_pct_usa_a_nossa_referencia():
    # 26,00 -> 18,90 = 27% verificável contra a NOSSA mediana, não contra o
    # "de" do vendedor (que aqui está inflado em 499,99).
    offer = make_offer(price_ref_cents=2600, price_current_cents=1890)
    assert offer.real_discount_pct == 27


def test_real_discount_pct_arredonda_para_baixo():
    # Teste obrigatório 11 (M4): 198,21 -> 179,38 é 9,5000% e `round` dava
    # "10% OFF" (banker's rounding) — passava no mínimo de 10. Agora 9.
    assert make_offer(price_ref_cents=19821, price_current_cents=17938).real_discount_pct == 9
    # ...em aritmética inteira: 100 -> 90 é 10, não floor(9.999999) = 9.
    assert make_offer(price_ref_cents=10000, price_current_cents=9000).real_discount_pct == 10
    assert make_offer(price_ref_cents=2600, price_current_cents=2500).real_discount_pct == 3


def test_real_discount_pct_zero_sem_referencia():
    assert make_offer().real_discount_pct == 0
    assert make_offer(price_ref_cents=0).real_discount_pct == 0


def test_real_discount_pct_zero_quando_nao_esta_abaixo_da_referencia():
    assert make_offer(price_ref_cents=2600, price_current_cents=2600).real_discount_pct == 0
    assert make_offer(price_ref_cents=2600, price_current_cents=3390).real_discount_pct == 0


def test_campos_da_regua_nascem_zerados():
    offer = make_offer()
    assert offer.price_ref_cents == 0
    assert offer.price_p25_cents == 0
    assert offer.price_window_days == 0
    assert offer.price_floor_cents == 0
    assert offer.price_floor_window_days == 0


def test_verdict_e_imutavel_e_sem_selo_por_padrao():
    v = Verdict("A", 27, "")
    assert v.seal_window_days == 0
    with pytest.raises(dataclasses.FrozenInstanceError):
        v.mode = "B"
    assert NO_CLAIM == Verdict("B", 0, "", 0)


def test_post_holds_parts():
    post = Post(
        offer=make_offer(),
        copy=CopyParts(headline="h", description="d", cta="c"),
        affiliate_link="https://shope.ee/x",
    )
    assert post.message_text == ""
    assert post.verdict == NO_CLAIM        # post que ninguém decidiu não alega nada


def test_post_accepts_verdict():
    v = Verdict("A", 27, "🏷️ Menor preço dos últimos 45 dias (verificado)", 45)
    post = Post(
        offer=make_offer(),
        copy=CopyParts(headline="h", description="d", cta="c"),
        affiliate_link="https://shope.ee/x",
        verdict=v,
    )
    assert post.verdict is v

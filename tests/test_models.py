from afiliado.models import CopyParts, Offer, Post, format_brl
from afiliado.watchlist import PriceFloor


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


def test_real_discount_pct_zero_sem_referencia():
    assert make_offer().real_discount_pct == 0
    assert make_offer(price_ref_cents=0).real_discount_pct == 0


def test_real_discount_pct_zero_quando_nao_esta_abaixo_da_referencia():
    assert make_offer(price_ref_cents=2600, price_current_cents=2600).real_discount_pct == 0
    assert make_offer(price_ref_cents=2600, price_current_cents=3390).real_discount_pct == 0


def test_price_ref_e_floor_default_zero():
    offer = make_offer()
    assert offer.price_ref_cents == 0
    assert offer.price_floor_cents == 0


def test_post_holds_parts():
    post = Post(
        offer=make_offer(),
        copy=CopyParts(headline="h", description="d", cta="c"),
        affiliate_link="https://shope.ee/x",
    )
    assert post.message_text == ""
    assert post.price_floor is None


def test_post_accepts_price_floor():
    floor = PriceFloor(min_price_cents=10000, window_days=180)
    post = Post(
        offer=make_offer(),
        copy=CopyParts(headline="h", description="d", cta="c"),
        affiliate_link="https://shope.ee/x",
        price_floor=floor,
    )
    assert post.price_floor is floor

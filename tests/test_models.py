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

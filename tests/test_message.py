from afiliado.message import build_message
from afiliado.models import CopyParts
from afiliado.watchlist import PriceFloor
from tests.test_models import make_offer

ESPERADO = """🚨 Promo Nike: 50% OFF
Nike SB com custo benefício.

Tênis Nike SB Chron 2 &quot;Black White&quot;
De: <s>R$ 499,99</s> | Por: <b>R$ 249,99</b> (50% OFF)

Corre que acaba rápido 👇
👉 https://shope.ee/abc123"""

ESPERADO_COM_SELO = """🚨 Promo Nike: 50% OFF
Nike SB com custo benefício.

Tênis Nike SB Chron 2 &quot;Black White&quot;
De: <s>R$ 499,99</s> | Por: <b>R$ 249,99</b> (50% OFF)
🏷️ Menor preço dos últimos 12 meses (verificado)

Corre que acaba rápido 👇
👉 https://shope.ee/abc123"""


def _copy():
    return CopyParts(headline="🚨 Promo Nike: 50% OFF",
                     description="Nike SB com custo benefício.",
                     cta="Corre que acaba rápido 👇")


def test_build_message_golden():
    offer = make_offer(title='Tênis Nike SB Chron 2 "Black White"')
    assert build_message(offer, _copy(), "https://shope.ee/abc123") == ESPERADO


def test_build_message_with_price_floor_badge():
    offer = make_offer(title='Tênis Nike SB Chron 2 "Black White"', price_current_cents=24999)
    floor = PriceFloor(min_price_cents=24999, window_days=365)
    result = build_message(offer, _copy(), "https://shope.ee/abc123", price_floor=floor)
    assert result == ESPERADO_COM_SELO


def test_build_message_no_badge_when_price_above_floor():
    offer = make_offer(title='Tênis Nike SB Chron 2 "Black White"', price_current_cents=24999)
    floor = PriceFloor(min_price_cents=19999, window_days=365)
    result = build_message(offer, _copy(), "https://shope.ee/abc123", price_floor=floor)
    assert result == ESPERADO

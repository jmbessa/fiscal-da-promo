from afiliado.message import build_message
from afiliado.models import CopyParts
from tests.test_models import make_offer

ESPERADO = """🚨 Promo Nike: 50% OFF
Nike SB com custo benefício.

Tênis Nike SB Chron 2 &quot;Black White&quot;
De: <s>R$ 499,99</s> | Por: <b>R$ 249,99</b> (50% OFF)

Corre que acaba rápido 👇
👉 https://shope.ee/abc123"""


def test_build_message_golden():
    offer = make_offer(title='Tênis Nike SB Chron 2 "Black White"')
    copy = CopyParts(headline="🚨 Promo Nike: 50% OFF",
                     description="Nike SB com custo benefício.",
                     cta="Corre que acaba rápido 👇")
    assert build_message(offer, copy, "https://shope.ee/abc123") == ESPERADO

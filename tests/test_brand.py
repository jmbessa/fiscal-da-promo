from PIL import Image

from afiliado.brand import CREAM, NAVY, draw_mascot

GOLD = (224, 166, 60)


def test_draw_mascot_paints_pixels():
    img = Image.new("RGB", (200, 200), NAVY)
    draw_mascot(img, 100, 100, 180, ink=NAVY, skin=CREAM, cap=NAVY)
    pixels = list(img.get_flattened_data())
    assert any(p == CREAM for p in pixels), "esperava pixels do rosto (creme)"
    assert any(p != NAVY for p in pixels), "esperava pixels diferentes do fundo"


def test_draw_mascot_on_gold_circle_avatar():
    img = Image.new("RGB", (200, 200), GOLD)
    draw_mascot(img, 100, 100, 196, ink=NAVY, skin=CREAM, cap=NAVY)
    pixels = list(img.get_flattened_data())
    assert any(p == CREAM for p in pixels)
    assert any(p == NAVY for p in pixels)

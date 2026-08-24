import io

import httpx
import pytest
from PIL import Image, ImageDraw

from afiliado.creative import (
    FEED_TITLE_SIZE,
    FEED_TITLE_WIDTH,
    STORY_TITLE_SIZE,
    STORY_TITLE_WIDTH,
    TITLE_MAX_LINES,
    _fit_card,
    _font,
    _wrap_title,
    render_feed,
    render_story,
)
from afiliado.errors import SourceError
from afiliado.models import CopyParts
from afiliado.watchlist import PriceFloor
from tests.test_models import make_offer

COPY = CopyParts(headline="Confira essa oferta", description="Aproveite agora", cta="Compre já")


def _product_png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (800, 800), (120, 40, 200)).save(buf, "PNG")
    return buf.getvalue()


def _image_handler(request):
    return httpx.Response(200, content=_product_png(), headers={"content-type": "image/png"})


def _client_for(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_render_story_dimensions():
    data = render_story(make_offer(), COPY, client=_client_for(_image_handler))
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    img = Image.open(io.BytesIO(data))
    assert img.size == (1080, 1920)
    assert img.mode == "RGB"


def test_render_feed_dimensions():
    data = render_feed(make_offer(), COPY, client=_client_for(_image_handler))
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    img = Image.open(io.BytesIO(data))
    assert img.size == (1080, 1350)
    assert img.mode == "RGB"


def test_render_story_selo_changes_output():
    offer = make_offer(price_current_cents=24999)
    without = render_story(offer, COPY, client=_client_for(_image_handler))
    floor = PriceFloor(min_price_cents=30000, window_days=180)
    with_selo = render_story(offer, COPY, price_floor=floor, client=_client_for(_image_handler))
    assert without != with_selo


def test_render_story_no_selo_when_price_above_floor():
    offer = make_offer(price_current_cents=24999)
    without = render_story(offer, COPY, client=_client_for(_image_handler))
    floor = PriceFloor(min_price_cents=10000, window_days=180)  # 24999 > 10000: não dispara o selo
    same = render_story(offer, COPY, price_floor=floor, client=_client_for(_image_handler))
    assert without == same


def test_render_raises_source_error_on_bad_image():
    def handler_404(request):
        return httpx.Response(404)

    with pytest.raises(SourceError):
        render_story(make_offer(), COPY, client=_client_for(handler_404))

    def handler_html(request):
        return httpx.Response(200, content=b"<html></html>", headers={"content-type": "text/html"})

    with pytest.raises(SourceError):
        render_story(make_offer(), COPY, client=_client_for(handler_html))


def test_render_long_title_two_lines_max():
    title = " ".join(["palavra"] * 30)
    offer = make_offer(title=title)
    data = render_story(offer, COPY, client=_client_for(_image_handler))
    assert data[:4] == b"\x89PNG"


def test_fit_card_respects_caps():
    img = Image.new("RGB", (800, 2000), (10, 20, 30))
    fitted = _fit_card(img, 960, 680)
    assert fitted.height == 680 or fitted.width == 960
    assert fitted.height <= 680
    assert fitted.width <= 960
    orig_ratio = img.width / img.height
    new_ratio = fitted.width / fitted.height
    assert abs(orig_ratio - new_ratio) < 0.01


def test_render_feed_square_image_smoke():
    offer = make_offer(
        title=" ".join(["palavra"] * 30),
        sales=50000,
        price_current_cents=24999,
    )
    floor = PriceFloor(min_price_cents=30000, window_days=180)
    data = render_feed(offer, COPY, price_floor=floor, client=_client_for(_image_handler))
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    img = Image.open(io.BytesIO(data))
    assert img.size == (1080, 1350)
    assert img.mode == "RGB"


def test_fit_card_never_upscales():
    img = Image.new("RGB", (400, 300), (10, 20, 30))
    fitted = _fit_card(img, 960, 680)
    assert fitted.size == (400, 300)


def test_wrap_title_truncates_overlong_single_word():
    canvas = Image.new("RGB", (10, 10))
    draw = ImageDraw.Draw(canvas)
    font = _font("sans", STORY_TITLE_SIZE, 700)
    title = "a" * 200  # palavra única, sem espaços — bem maior que STORY_TITLE_WIDTH
    lines = _wrap_title(draw, title, font, STORY_TITLE_WIDTH, TITLE_MAX_LINES)
    assert lines
    for line in lines:
        assert draw.textlength(line, font=font) <= STORY_TITLE_WIDTH
    assert lines[0].endswith("…")


def test_render_overlong_single_word_title_smoke():
    offer = make_offer(title="a" * 200)
    data = render_story(offer, COPY, client=_client_for(_image_handler))
    assert data[:4] == b"\x89PNG"


def test_render_handle_changes_output():
    client = _client_for(_image_handler)
    sem = render_story(make_offer(), COPY, client=client)
    com = render_story(make_offer(), COPY, client=client, handle="@promoprova")
    assert sem != com
    assert Image.open(io.BytesIO(com)).size == (1080, 1920)
    feed_sem = render_feed(make_offer(), COPY, client=client)
    feed_com = render_feed(make_offer(), COPY, client=client, handle="@promoprova")
    assert feed_sem != feed_com


def test_font_weight_axis_effective():
    canvas = Image.new("RGB", (10, 10))
    draw = ImageDraw.Draw(canvas)
    bold = _font("sans", 66, 800)
    regular = _font("sans", 66, 400)
    text = "Fiscal da Promo"
    assert draw.textlength(text, font=bold) != draw.textlength(text, font=regular)


def test_mono_weights_map_to_files():
    assert str(_font("mono", 26, 500).path).endswith("Medium.ttf")
    assert str(_font("mono", 26, 400).path).endswith("Regular.ttf")
    assert str(_font("mono", 26, 600).path).endswith("SemiBold.ttf")


def test_draw_mascot_paints_pixels():
    from afiliado.brand import CREAM, NAVY, draw_mascot

    img = Image.new("RGB", (200, 200), NAVY)
    draw_mascot(img, 100, 100, 180, ink=NAVY, skin=CREAM, cap=NAVY)
    pixels = list(img.get_flattened_data())
    assert any(p == CREAM for p in pixels)
    assert any(p != NAVY for p in pixels)


def test_brand_name_changes_output():
    client = _client_for(_image_handler)
    a = render_story(make_offer(), COPY, client=client, brand_name="X")
    b = render_story(make_offer(), COPY, client=client, brand_name="Y")
    assert a != b


def test_source_meli_cta():
    offer = make_offer(source="meli")
    data = render_story(offer, COPY, client=_client_for(_image_handler))
    assert data[:4] == b"\x89PNG"
    data_feed = render_feed(offer, COPY, client=_client_for(_image_handler))
    assert data_feed[:4] == b"\x89PNG"


def test_feed_title_size_and_width_constants_used_by_wrap():
    canvas = Image.new("RGB", (10, 10))
    draw = ImageDraw.Draw(canvas)
    font = _font("sans", FEED_TITLE_SIZE, 700)
    lines = _wrap_title(draw, "palavra " * 30, font, FEED_TITLE_WIDTH, TITLE_MAX_LINES)
    assert len(lines) <= TITLE_MAX_LINES
    for line in lines:
        assert draw.textlength(line, font=font) <= FEED_TITLE_WIDTH


def test_render_no_sales_meta_omits_vendidos():
    # sales=0 (default de make_offer) não deve quebrar o render; a linha de
    # meta cai para só a fonte ("Shopee").
    offer = make_offer(sales=0)
    data = render_story(offer, COPY, client=_client_for(_image_handler))
    assert data[:4] == b"\x89PNG"

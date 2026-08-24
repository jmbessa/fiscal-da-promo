import io

import httpx
import pytest
from PIL import Image

from afiliado.creative import render_feed, render_story
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


def test_render_story_badge_changes_output():
    offer = make_offer(price_current_cents=24999)
    without = render_story(offer, COPY, client=_client_for(_image_handler))
    floor = PriceFloor(min_price_cents=30000, window_days=180)
    with_badge = render_story(offer, COPY, price_floor=floor, client=_client_for(_image_handler))
    assert without != with_badge


def test_render_story_no_badge_when_price_above_floor():
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

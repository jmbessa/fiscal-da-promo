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
    _pill_left,
    _price_pill_dims,
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


def test_feed_selo_survives_two_line_title():
    # Título longo (2 linhas) + meta presente (sales>=1000) + selo aplicável:
    # o selo é o diferenciador da marca e é o ÚLTIMO a cair na guarda de
    # overflow (título 1 linha, depois meta, só então selo) — prova disso é
    # que os bytes mudam com price_floor (selo desenhado), mesmo sob um
    # título que antes forçava a guarda a descartar justamente o selo.
    offer = make_offer(
        title=" ".join(["palavra"] * 30),
        sales=32000,
        price_current_cents=6890,
        price_original_cents=12990,
    )
    client = _client_for(_image_handler)
    without = render_feed(offer, COPY, client=client)
    floor = PriceFloor(min_price_cents=7500, window_days=365)
    with_selo = render_feed(offer, COPY, price_floor=floor, client=client)
    assert without != with_selo


def test_story_selo_survives_two_line_title():
    offer = make_offer(
        title=" ".join(["palavra"] * 30),
        sales=32000,
        price_current_cents=6890,
        price_original_cents=12990,
    )
    client = _client_for(_image_handler)
    without = render_story(offer, COPY, client=client)
    floor = PriceFloor(min_price_cents=7500, window_days=365)
    with_selo = render_story(offer, COPY, price_floor=floor, client=client)
    assert without != with_selo


def test_render_no_sales_meta_omits_vendidos():
    # sales=0 (default de make_offer) não deve quebrar o render; a linha de
    # meta cai para só a fonte ("Shopee").
    offer = make_offer(sales=0)
    data = render_story(offer, COPY, client=_client_for(_image_handler))
    assert data[:4] == b"\x89PNG"


def test_meta_text_includes_rating_when_known():
    from afiliado.creative import _meta_text
    assert _meta_text(make_offer(sales=30000, rating=4.9)) == "4,9 ★ · 30 mil vendidos · Shopee"
    assert _meta_text(make_offer(sales=0, rating=0.0)) == "Shopee"
    assert _meta_text(make_offer(sales=500, source="meli")) == "500 vendidos · Mercado Livre"


def test_render_story_rating_changes_output():
    client = _client_for(_image_handler)
    sem = render_story(make_offer(sales=30000), COPY, client=client)
    com = render_story(make_offer(sales=30000, rating=4.9), COPY, client=client)
    assert sem != com


# --- Os dois modos (fase 4: régua honesta) -----------------------------------

def _com_desconto(**kw):
    return make_offer(price_original_cents=35000, price_ref_cents=2600,
                      price_current_cents=1890, **kw)


def test_pill_left_modo_a_usa_a_nossa_referencia():
    texto, riscado = _pill_left(_com_desconto(), 10)
    assert texto == "R$ 26,00"      # a NOSSA referência, não os R$ 350 do vendedor
    assert riscado is True


def test_pill_left_modo_b_traz_prova_social_sem_glifo_ausente():
    offer = make_offer(price_original_cents=35000, price_current_cents=4900,
                       price_ref_cents=5200, rating=4.9, sales=30000)
    texto, riscado = _pill_left(offer, 10)    # 6% verificado < 10
    assert texto == "4,9 · 30 mil vendidos"   # sem a estrela: a fonte não tem o glifo
    assert riscado is False


def test_pill_left_modo_b_vazio_quando_nada_e_conhecido():
    offer = make_offer(price_original_cents=35000, price_current_cents=4900,
                       rating=0.0, sales=0)
    assert _pill_left(offer, 10) == ("", False)


def test_price_pill_nunca_passa_da_largura_util():
    img = Image.new("RGB", (1080, 1920))
    draw = ImageDraw.Draw(img)
    longo = "4,9 · 999 mil vendidos e mais um monte de texto que não caberia jamais"
    dims = _price_pill_dims(draw, make_offer(), 36, 96, 20, 30, 24, (longo, False),
                            STORY_TITLE_WIDTH)
    assert dims["width"] <= STORY_TITLE_WIDTH


def test_arte_ignora_o_de_inflado_do_vendedor():
    # O price_original_cents do vendedor não entra mais na arte: mudar só ele
    # não pode mudar um pixel.
    inflado = make_offer(price_original_cents=35000, price_current_cents=4900,
                         rating=4.9, sales=30000)
    sem_de = make_offer(price_original_cents=4900, price_current_cents=4900,
                        rating=4.9, sales=30000)
    client = _client_for(_image_handler)
    assert render_story(inflado, COPY, client=client) == render_story(
        sem_de, COPY, client=client)
    assert render_feed(inflado, COPY, client=client) == render_feed(
        sem_de, COPY, client=client)


def test_arte_modo_b_nao_desenha_selo_de_desconto_nem_riscado():
    # Desconto verificado de 6% (abaixo do mínimo) tem que render exatamente
    # como uma oferta sem referência nenhuma: sem selo de %, sem preço riscado.
    quase = make_offer(price_current_cents=4900, price_ref_cents=5200,
                       rating=4.9, sales=30000)
    sem_ref = make_offer(price_current_cents=4900, rating=4.9, sales=30000)
    client = _client_for(_image_handler)
    assert render_story(quase, COPY, client=client) == render_story(
        sem_ref, COPY, client=client)


def test_arte_muda_entre_os_dois_modos():
    client = _client_for(_image_handler)
    modo_a = render_story(_com_desconto(rating=4.9, sales=30000), COPY, client=client)
    modo_b = render_story(make_offer(price_current_cents=1890, rating=4.9, sales=30000),
                          COPY, client=client)
    assert modo_a != modo_b


def test_min_real_discount_pct_decide_o_modo():
    client = _client_for(_image_handler)
    offer = make_offer(price_current_cents=4900, price_ref_cents=5200,
                       rating=4.9, sales=30000)   # 6% verificado
    assert (render_story(offer, COPY, client=client, min_real_discount_pct=5)
            != render_story(offer, COPY, client=client, min_real_discount_pct=10))

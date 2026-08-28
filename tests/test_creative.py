import io
import math

import httpx
import pytest
from PIL import Image, ImageChops, ImageDraw

from afiliado import pricing
from afiliado.creative import (
    FEED_NOTA_SIZE,
    FEED_PAD,
    FEED_TITLE_SIZE,
    FEED_TITLE_WIDTH,
    GOLD,
    MUTED,
    NAVY,
    PRICE_DIM_INK,
    STORY_NOTA_SIZE,
    STORY_PAD,
    STORY_TITLE_SIZE,
    STORY_TITLE_WIDTH,
    TITLE_MAX_LINES,
    _draw_meta,
    _draw_star,
    _fit_card,
    _font,
    _meta_dims,
    _meta_layout,
    _meta_parts,
    _pill_left,
    _price_pill_dims,
    _wrap_title,
    feed_plan,
    render_feed,
    render_story,
    selo_label,
    story_plan,
)
from afiliado.errors import SourceError
from afiliado.models import NO_CLAIM, CopyParts, Verdict
from tests.test_models import make_offer, make_offer_ref

COPY = CopyParts(headline="Confira essa oferta", description="Aproveite agora", cta="Compre já")
SELO_6M = Verdict("B", 0, "🏷️ Menor preço dos últimos 6 meses (verificado)", 180)


def _product_png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (800, 800), (120, 40, 200)).save(buf, "PNG")
    return buf.getvalue()


def _image_handler(request):
    return httpx.Response(200, content=_product_png(), headers={"content-type": "image/png"})


def _client_for(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _v(offer, minimo=10) -> Verdict:
    return pricing.verdict(offer, minimo)


def test_render_story_dimensions():
    data = render_story(make_offer(), COPY, NO_CLAIM, client=_client_for(_image_handler))
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    img = Image.open(io.BytesIO(data))
    assert img.size == (1080, 1920)
    assert img.mode == "RGB"


def test_render_feed_dimensions():
    data = render_feed(make_offer(), COPY, NO_CLAIM, client=_client_for(_image_handler))
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    img = Image.open(io.BytesIO(data))
    assert img.size == (1080, 1350)
    assert img.mode == "RGB"


def test_render_story_selo_changes_output():
    offer = make_offer(price_current_cents=24999)
    without = render_story(offer, COPY, NO_CLAIM, client=_client_for(_image_handler))
    with_selo = render_story(offer, COPY, SELO_6M, client=_client_for(_image_handler))
    assert without != with_selo


def test_render_story_no_selo_when_price_above_floor():
    # O veredito é estrito: 24999 > piso 10000 -> sem selo -> mesma arte.
    offer = make_offer(price_current_cents=24999, price_floor_cents=10000, price_floor_window_days=180)
    assert _v(offer).seal == ""
    without = render_story(make_offer(price_current_cents=24999), COPY, NO_CLAIM,
                           client=_client_for(_image_handler))
    same = render_story(offer, COPY, _v(offer), client=_client_for(_image_handler))
    assert without == same


def test_render_raises_source_error_on_bad_image():
    def handler_404(request):
        return httpx.Response(404)

    with pytest.raises(SourceError):
        render_story(make_offer(), COPY, NO_CLAIM, client=_client_for(handler_404))

    def handler_html(request):
        return httpx.Response(200, content=b"<html></html>", headers={"content-type": "text/html"})

    with pytest.raises(SourceError):
        render_story(make_offer(), COPY, NO_CLAIM, client=_client_for(handler_html))


def test_render_long_title_two_lines_max():
    title = " ".join(["palavra"] * 30)
    offer = make_offer(title=title)
    data = render_story(offer, COPY, NO_CLAIM, client=_client_for(_image_handler))
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
    data = render_feed(offer, COPY, SELO_6M, client=_client_for(_image_handler))
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
    data = render_story(offer, COPY, NO_CLAIM, client=_client_for(_image_handler))
    assert data[:4] == b"\x89PNG"


def test_render_handle_changes_output():
    client = _client_for(_image_handler)
    sem = render_story(make_offer(), COPY, NO_CLAIM, client=client)
    com = render_story(make_offer(), COPY, NO_CLAIM, client=client, handle="@promoprova")
    assert sem != com
    assert Image.open(io.BytesIO(com)).size == (1080, 1920)
    feed_sem = render_feed(make_offer(), COPY, NO_CLAIM, client=client)
    feed_com = render_feed(make_offer(), COPY, NO_CLAIM, client=client, handle="@promoprova")
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
    a = render_story(make_offer(), COPY, NO_CLAIM, client=client, brand_name="X")
    b = render_story(make_offer(), COPY, NO_CLAIM, client=client, brand_name="Y")
    assert a != b


def test_source_meli_cta():
    offer = make_offer(source="meli")
    data = render_story(offer, COPY, NO_CLAIM, client=_client_for(_image_handler))
    assert data[:4] == b"\x89PNG"
    data_feed = render_feed(offer, COPY, NO_CLAIM, client=_client_for(_image_handler))
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
    # Título longo (2 linhas) + meta presente (sales>=1000) + selo no
    # veredito: o selo é o diferenciador da marca e é o ÚLTIMO a cair na
    # guarda de overflow (título 1 linha, depois meta, só então selo).
    offer = make_offer(
        title=" ".join(["palavra"] * 30),
        sales=32000,
        price_current_cents=6890,
        price_original_cents=12990,
    )
    client = _client_for(_image_handler)
    without = render_feed(offer, COPY, NO_CLAIM, client=client)
    selo = Verdict("B", 0, "🏷️ Menor preço dos últimos 12 meses (verificado)", 365)
    with_selo = render_feed(offer, COPY, selo, client=client)
    assert without != with_selo
    assert feed_plan(offer, selo)["selo"] == "MENOR PREÇO VERIFICADO · 12 MESES"


def test_story_selo_survives_two_line_title():
    offer = make_offer(
        title=" ".join(["palavra"] * 30),
        sales=32000,
        price_current_cents=6890,
        price_original_cents=12990,
    )
    client = _client_for(_image_handler)
    without = render_story(offer, COPY, NO_CLAIM, client=client)
    selo = Verdict("B", 0, "🏷️ Menor preço dos últimos 12 meses (verificado)", 365)
    with_selo = render_story(offer, COPY, selo, client=client)
    assert without != with_selo
    assert story_plan(offer, selo)["selo"] == "MENOR PREÇO VERIFICADO · 12 MESES"


def test_render_no_sales_meta_omits_vendidos():
    # sales=0 (default de make_offer) não deve quebrar o render; a linha de
    # meta cai para só a fonte ("Shopee").
    offer = make_offer(sales=0)
    data = render_story(offer, COPY, NO_CLAIM, client=_client_for(_image_handler))
    assert data[:4] == b"\x89PNG"


def test_meta_parts_separa_o_texto_ao_redor_da_estrela():
    # A estrela é vetor (_draw_star), não caractere: a linha vem em dois
    # segmentos, e nenhum deles carrega o glifo que as fontes não têm.
    assert _meta_parts(make_offer(sales=30000, rating=4.9)) == ("4,9 ", " · 30 mil vendidos · Shopee")
    assert _meta_parts(make_offer(sales=0, rating=4.5)) == ("4,5 ", " · Shopee")
    # Sem nota: nada de estrela — o texto começa em vendas (ou na loja).
    assert _meta_parts(make_offer(sales=0, rating=0.0)) == ("", "Shopee")
    assert _meta_parts(make_offer(sales=500, source="meli")) == ("", "500 vendidos · Mercado Livre")
    for offer in (make_offer(sales=30000, rating=4.9), make_offer(sales=0, rating=0.0)):
        assert "★" not in "".join(_meta_parts(offer))
        assert "⭐" not in "".join(_meta_parts(offer))


def test_meta_diz_a_janela_do_sales_da_shopee():
    """Fase 5H: o número da Shopee é de ~30 dias e a arte dizia só "45 mil
    vendidos", para um anúncio que exibe 2 milhões. A linha de meta passa a
    dizer a janela; a do ML (janela 0, contador vitalício) não muda."""
    shopee = make_offer(sales=45950, rating=4.9, sales_window_days=30)
    assert _meta_parts(shopee) == ("4,9 ", " · 45 mil vendidos no último mês · Shopee")
    meli = make_offer(sales=250000, rating=4.9, source="meli", sales_e_faixa=True)
    assert _meta_parts(meli) == ("4,9 ", " · +250 mil vendidos · Mercado Livre")


def test_a_linha_de_meta_com_a_janela_cabe_na_arte():
    """A meta é UMA linha centrada: ela não quebra, ela VAZA. Com o texto da
    janela ela cresce ~250 px no story, então a folga vira teste — o pior caso
    plausível da Shopee (nota + 6 dígitos de vendas + janela + loja) tem de
    caber dentro das margens do story e do feed.

    Medido em 2026-08-28, story (mono 30, margem de 72 px): 840 px com "45 mil"
    (48 px de folga), 858 com "999 mil" (39) e 930 com "1,5 milhões" (3 px, o
    limite). O maior `sales` de 30 dias já visto na Shopee é 77.344 — 1,5
    milhão/mês é 20× isso. Acima daí a linha encosta na margem, e ela não tem
    guarda horizontal nenhuma (nunca teve: "99,9 milhões vendidos · Mercado
    Livre" já vazaria antes desta fase)."""
    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    piores = [make_offer(sales=n, rating=4.9, sales_window_days=30)
              for n in (45_950, 77_344, 100_000, 999_999, 1_500_000)]
    for offer in piores:
        story = _meta_dims(draw, offer, 30)["width"]
        assert (1080 - story) / 2 >= STORY_PAD, f"meta vaza no story: {_meta_parts(offer)}"
        feed = _meta_dims(draw, offer, 27)["width"]
        assert (1080 - feed) / 2 >= FEED_PAD, f"meta vaza no feed: {_meta_parts(offer)}"


def test_draw_star_desenha_poligono_de_cinco_pontas():
    img = Image.new("RGB", (100, 100), NAVY)
    _draw_star(ImageDraw.Draw(img), 50, 50, 40, GOLD)
    px = img.load()
    assert px[50, 50] == GOLD                       # centro
    assert px[50, 16] == GOLD                       # dentro da ponta de cima (r=40 -> ápice em y=10)
    # Entre duas pontas (-54°, onde fica o vértice interno a 0,4r): a 90% do
    # raio é fora da estrela, a 30% ainda é dentro.
    ang = math.radians(-54)
    fora = (round(50 + 36 * math.cos(ang)), round(50 + 36 * math.sin(ang)))
    dentro = (round(50 + 12 * math.cos(ang)), round(50 + 12 * math.sin(ang)))
    assert px[fora] == NAVY
    assert px[dentro] == GOLD


def test_meta_width_inclui_a_estrela_so_quando_ha_nota():
    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    font = _font("mono", 30, 400)
    com = _meta_dims(draw, make_offer(sales=30000, rating=4.9), 30)
    antes, depois = _meta_parts(make_offer(sales=30000, rating=4.9))
    so_texto = draw.textlength(antes, font=font) + draw.textlength(depois, font=font)
    assert com["width"] >= so_texto + 2 * 0.42 * 30   # estrela: 2r + espaçamento
    sem = _meta_dims(draw, make_offer(sales=30000, rating=0.0), 30)
    assert sem["width"] == draw.textlength("30 mil vendidos · Shopee", font=font)


def test_draw_meta_devolve_a_largura_medida_e_pinta_a_estrela_na_cor_do_texto():
    offer = make_offer(sales=30000, rating=4.9)
    img = Image.new("RGB", (1080, 120), NAVY)
    draw = ImageDraw.Draw(img)
    dims = _meta_dims(draw, offer, 30)
    x, y = 10, 20
    largura = _draw_meta(draw, x, y, offer, dims["font"], MUTED)
    assert largura == dims["width"]
    lay = _meta_layout(draw, offer, dims["font"])
    assert lay["r"] == 0.42 * 30
    # o centro da estrela, onde a medição diz que ela está, tem a cor do texto
    assert img.getpixel((round(x + lay["star_dx"]), round(y + lay["star_dy"]))) == MUTED
    # e o texto depois da estrela também foi desenhado (algum pixel MUTED lá)
    depois = img.crop((round(x + lay["star_dx"] + lay["r"]) + 1, y, 1080, 120))
    assert any(p == MUTED for p in depois.get_flattened_data())


def test_render_story_rating_changes_output():
    client = _client_for(_image_handler)
    sem = render_story(make_offer(sales=30000), COPY, NO_CLAIM, client=client)
    com = render_story(make_offer(sales=30000, rating=4.9), COPY, NO_CLAIM, client=client)
    assert sem != com


# --- Os dois modos (fase 4/5B: a arte obedece ao veredito) ---------------------

def _com_desconto(**kw):
    return make_offer_ref(2600, price_original_cents=35000, price_current_cents=1890, **kw)


def test_pill_left_modo_a_usa_a_nossa_referencia():
    texto, riscado = _pill_left(_com_desconto(), _v(_com_desconto()))
    assert texto == "R$ 26,00"      # a NOSSA referência, não os R$ 350 do vendedor
    assert riscado is True


def test_pill_left_modo_b_e_so_o_preco():
    # Modo B: a pill NÃO tem slot esquerdo — nota, vendas e loja ficam na
    # linha de meta logo abaixo (como no modo A), sem duplicar.
    offer = make_offer_ref(5200, price_original_cents=35000, price_current_cents=4900,
                           rating=4.9, sales=30000)
    assert _pill_left(offer, _v(offer)) == ("", False)   # 5% verificado < 10


def test_pill_left_obedece_ao_veredito():
    assert _pill_left(_com_desconto(), NO_CLAIM) == ("", False)
    assert _pill_left(make_offer(price_ref_cents=2600), Verdict("A", 27, "")) == ("R$ 26,00", True)


def _gold_bbox(png: bytes, box: tuple) -> tuple:
    """Caixa dos pixels exatamente GOLD dentro de `box` (relativa ao box)."""
    img = Image.open(io.BytesIO(png)).convert("RGB").crop(box)
    diff = ImageChops.difference(img, Image.new("RGB", img.size, GOLD)).convert("L")
    return diff.point(lambda v: 255 if v == 0 else 0).getbbox()


def test_price_pill_modo_b_encolhe_para_o_preco_e_fica_centralizada():
    offer = make_offer(price_current_cents=3390, rating=4.9, sales=30000)   # sem referência
    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    dims = _price_pill_dims(draw, offer, 36, 96, 20, 30, 24, _pill_left(offer, NO_CLAIM),
                            STORY_TITLE_WIDTH, STORY_NOTA_SIZE)
    assert dims["orig_text"] == ""
    asc, desc = dims["cur_font"].getmetrics()
    # sem slot ESQUERDO: só o preço, o rótulo "SEM CUPOM" (fase 5K) e o padding
    assert dims["width"] == dims["cur_w"] + 24 + dims["nota_w"] + 2 * 30
    assert dims["height"] == asc + desc + 2 * 20

    # Na arte: a única coisa dourada no corpo é a pill, CENTRALIZADA (como o
    # título, a meta e o selo) e exatamente com a largura medida. Era colada
    # em STORY_PAD até 2026-08-27; encostada na margem, ela desequilibrava o
    # bloco no story vertical, onde é o elemento mais pesado.
    png = render_story(offer, COPY, NO_CLAIM, client=_client_for(_image_handler))
    left, _, right, _ = _gold_bbox(png, (0, 1000, 1080, 1600))
    assert abs((right - left) - dims["width"]) <= 3
    esperado = (1080 - dims["width"]) / 2
    assert abs(left - esperado) <= 2
    assert abs((1080 - right) - esperado) <= 2      # simétrica dos dois lados


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
    assert render_story(inflado, COPY, NO_CLAIM, client=client) == render_story(
        sem_de, COPY, NO_CLAIM, client=client)
    assert render_feed(inflado, COPY, NO_CLAIM, client=client) == render_feed(
        sem_de, COPY, NO_CLAIM, client=client)


def test_arte_modo_b_nao_desenha_selo_de_desconto_nem_riscado():
    # Desconto verificado de 5% (abaixo do mínimo) tem que render exatamente
    # como uma oferta sem referência nenhuma: sem badge de %, sem preço riscado.
    quase = make_offer_ref(5200, price_current_cents=4900, rating=4.9, sales=30000)
    sem_ref = make_offer(price_current_cents=4900, rating=4.9, sales=30000)
    client = _client_for(_image_handler)
    assert _v(quase) == NO_CLAIM
    assert render_story(quase, COPY, _v(quase), client=client) == render_story(
        sem_ref, COPY, NO_CLAIM, client=client)


def test_arte_muda_entre_os_dois_modos():
    client = _client_for(_image_handler)
    offer = _com_desconto(rating=4.9, sales=30000)
    modo_a = render_story(offer, COPY, _v(offer), client=client)
    modo_b = render_story(offer, COPY, NO_CLAIM, client=client)
    assert modo_a != modo_b


def test_min_real_discount_pct_decide_o_modo_via_veredito():
    client = _client_for(_image_handler)
    offer = make_offer_ref(5200, price_current_cents=4900, rating=4.9, sales=30000)   # 5%
    assert (render_story(offer, COPY, _v(offer, 5), client=client)
            != render_story(offer, COPY, _v(offer, 10), client=client))


# --- Hook testável: o que a arte desenha ------------------------------------------

def test_selo_label_segue_o_veredito():
    assert selo_label(NO_CLAIM) == ""
    assert selo_label(SELO_6M) == "MENOR PREÇO VERIFICADO · 6 MESES"
    assert selo_label(Verdict("A", 27, "🏷️ Menor preço dos últimos 45 dias (verificado)", 45)) == (
        "MENOR PREÇO VERIFICADO · 45 DIAS")


def test_story_plan_e_feed_plan_expoem_selo_badge_e_riscado():
    offer = _com_desconto(rating=4.9, sales=30000, price_floor_cents=1890,
                          price_floor_window_days=90)
    v = _v(offer)
    assert v == Verdict("A", 27, "🏷️ Menor preço dos últimos 3 meses (verificado)", 90)
    for plan in (story_plan(offer, v), feed_plan(offer, v)):
        assert plan["selo"] == "MENOR PREÇO VERIFICADO · 3 MESES"
        assert plan["badge_pct"] == 27
        assert plan["riscado"] == "R$ 26,00"
        assert plan["title_lines"] == ["Tênis Nike SB"]
    # No story cabe tudo; no feed a guarda de overflow derruba a meta antes
    # do selo (o selo é o último a cair — comportamento de layout já existente).
    assert story_plan(offer, v)["meta"] is True
    assert feed_plan(offer, v)["meta"] is False
    for plan in (story_plan(offer, NO_CLAIM), feed_plan(offer, NO_CLAIM)):
        assert plan["selo"] == "" and plan["badge_pct"] == 0 and plan["riscado"] == ""


def test_price_pill_do_feed_tambem_e_centralizada():
    """O feed era o único formato com a pill fora do eixo — ao lado do selo e
    da meta, ambos centralizados, aquilo lia como descuido. Pedido do dono em
    2026-08-27, depois de ver os dois formatos lado a lado."""
    from afiliado.creative import FEED_SIZE, _feed_body_dims, _pill_left, render_feed

    offer = make_offer(price_current_cents=3390, rating=4.9, sales=30000)
    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    dims = _price_pill_dims(draw, offer, 32, 84, 18, 28, 22, _pill_left(offer, NO_CLAIM),
                            FEED_TITLE_WIDTH, FEED_NOTA_SIZE)
    png = render_feed(offer, COPY, NO_CLAIM, client=_client_for(_image_handler))
    largura = FEED_SIZE[0]
    left, _, right, _ = _gold_bbox(png, (0, 780, largura, 1160))
    esperado = (largura - dims["width"]) / 2
    assert abs(left - esperado) <= 2
    assert abs((largura - right) - esperado) <= 2


# --- Fase 5K: a arte diz que o preço é SEM CUPOM ------------------------------
#
# A colocação foi escolhida OLHANDO os previews (relatório da fase, 2026-08-28):
# o rótulo vai DENTRO da pill, à direita do preço. As três alternativas caíram
# na imagem, e cada teste abaixo trava o motivo pelo qual elas caíram.

# Preço e referência mais largos que o projeto pode publicar hoje:
# `selection.price_max_brl` é 1000, e a referência riscada é a nossa mediana —
# ela não tem teto de configuração.
PIOR_PRECO, PIOR_REF = 99999, 199999


def _pill_story(offer, verdict=NO_CLAIM):
    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    return _price_pill_dims(draw, offer, 36, 96, 20, 30, 24, _pill_left(offer, verdict),
                            STORY_TITLE_WIDTH, STORY_NOTA_SIZE)


def _pill_feed(offer, verdict=NO_CLAIM):
    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    return _price_pill_dims(draw, offer, 32, 84, 18, 28, 22, _pill_left(offer, verdict),
                            FEED_TITLE_WIDTH, FEED_NOTA_SIZE)


def test_a_pill_da_shopee_leva_o_rotulo_e_a_do_ml_nao():
    """K1: só a Shopee. O preço do ML é o do anúncio do buy box, que é o que a
    página mostra — rotular lá seria ruído sobre um preço que ninguém contesta."""
    shopee, meli = make_offer(), make_offer(source="meli")
    for plan in (story_plan(shopee, NO_CLAIM), feed_plan(shopee, NO_CLAIM)):
        assert plan["sem_cupom"] == "SEM CUPOM"
    for plan in (story_plan(meli, NO_CLAIM), feed_plan(meli, NO_CLAIM)):
        assert plan["sem_cupom"] == ""
    assert _pill_story(meli)["nota_text"] == ""
    assert _pill_feed(meli)["nota_text"] == ""


def test_o_rotulo_nao_custa_um_pixel_de_altura():
    """Por que ele foi para o LADO do preço e não para baixo dele: alinhado
    pela linha de base, o rótulo cabe na altura que o preço já ocupa. A
    variante "segunda linha dentro da pill" engordava a pill em ~50 px e o
    guarda de overflow derrubava o SELO no feed com título longo — visto no
    preview de 2026-08-28, e o selo é o último a cair por desenho."""
    for pill in (_pill_story, _pill_feed):
        com = pill(make_offer(price_current_cents=68999))
        sem = pill(make_offer(price_current_cents=68999, source="meli"))
        assert com["height"] == sem["height"]


def test_o_selo_e_a_meta_sobrevivem_ao_rotulo():
    """A consequência do teste acima, medida na peça inteira: o pior caso da
    suíte (título de 2 linhas + meta + selo) continua com selo nos dois
    formatos, e com meta no story."""
    offer = make_offer(title=" ".join(["palavra"] * 30), sales=45950,
                       sales_window_days=30, rating=4.9, price_current_cents=68999)
    selo = Verdict("B", 0, "🏷️ Menor preço dos últimos 6 meses (verificado)", 180)
    for plan in (story_plan(offer, selo), feed_plan(offer, selo)):
        assert plan["selo"] == "MENOR PREÇO VERIFICADO · 6 MESES"
        assert plan["sem_cupom"] == "SEM CUPOM"
    assert story_plan(offer, selo)["meta"] is True


def test_a_pill_com_o_rotulo_cabe_na_largura_util_no_pior_caso():
    """Por que ele NÃO foi para a linha de meta: aquela linha não tem guarda
    horizontal (registrado na fase 5H) e o rótulo a fazia encostar na margem —
    no pior caso ela era CORTADA pela borda do canvas. A pill tem guarda, e o
    rótulo entra dentro dele: no pior caso publicável a pill continua na
    largura útil, e a margem continua >= o padding do formato."""
    casos = [
        make_offer(price_current_cents=PIOR_PRECO),
        make_offer_ref(PIOR_REF, price_current_cents=PIOR_PRECO),
        make_offer_ref(PIOR_REF, price_current_cents=2000),
    ]
    for offer in casos:
        v = _v(offer)
        story, feed = _pill_story(offer, v), _pill_feed(offer, v)
        assert story["width"] <= STORY_TITLE_WIDTH, offer.price_ref_cents
        assert (1080 - story["width"]) / 2 >= STORY_PAD
        assert feed["width"] <= FEED_TITLE_WIDTH
        assert (1080 - feed["width"]) / 2 >= FEED_PAD
        # e o rótulo NUNCA é o que cede
        assert story["nota_text"] == feed["nota_text"] == "SEM CUPOM"
        # dentro da faixa publicável ele também não cobra nada do riscado:
        # o pior caso (906 px de pill no story) ainda sobra 30 px da largura
        # útil e 15 px da margem — medido em 2026-08-28.
        assert not story["orig_text"].endswith("…")
        assert not feed["orig_text"].endswith("…")


def test_quando_falta_espaco_quem_cede_e_a_referencia_riscada():
    """Precedência do guarda: o riscado é decoração e já nasceu com mecanismo
    de corte (`_hard_truncate`); o rótulo é a honestidade da peça e não pode
    sumir para caber."""
    offer = make_offer_ref(99999999, price_current_cents=PIOR_PRECO)   # R$ 999.999,99
    dims = _pill_story(offer, Verdict("A", 90, ""))
    assert dims["width"] <= STORY_TITLE_WIDTH
    assert dims["orig_text"].endswith("…")
    assert dims["nota_text"] == "SEM CUPOM"


def test_o_rotulo_esta_pintado_a_direita_do_preco_dentro_da_pill():
    """O que a imagem prova: a pill desenhada é a pill medida, e o slot que a
    medição reservou à direita do preço tem tinta. Sem isto, "o rótulo existe"
    seria uma afirmação sobre um dicionário, não sobre a peça."""
    offer = make_offer(price_current_cents=68999)
    dims = _pill_story(offer)
    assert dims["nota_text"] == "SEM CUPOM"
    png = render_story(offer, COPY, NO_CLAIM, client=_client_for(_image_handler))
    box = (0, 1000, 1080, 1600)
    left, top, right, bottom = _gold_bbox(png, box)
    assert abs((right - left) - dims["width"]) <= 3
    x0 = left + dims["pad_x"] + dims["cur_w"] + dims["gap"]
    pill = Image.open(io.BytesIO(png)).convert("RGB").crop(box)
    slot = pill.crop((round(x0), top, round(x0 + dims["nota_w"]), bottom))
    assert any(p == PRICE_DIM_INK for p in slot.get_flattened_data())


def test_respiro_do_feed_nao_derruba_a_meta_por_alguns_pixels():
    """O respiro do story (88) aplicado ao feed derrubava a linha de avaliações
    por SEIS pixels — o feed tem 570px a menos de altura. 64 é a proporção
    equivalente e cabe com folga. Este teste é o que impede alguém de igualar
    os dois números de novo sem medir."""
    from afiliado.creative import (FEED_META_GAP, FEED_PAD, FEED_SIZE,
                                   STORY_META_GAP, _feed_body_dims,
                                   _feed_footer_geometry, _pill_left)

    assert FEED_META_GAP < STORY_META_GAP, "o feed é mais curto; o respiro não pode ser igual"
    offer = make_offer(title="Creme Multirreparador Calmante, Cicaplast Baume B5+ La Roche Posay",
                       price_current_cents=7445, rating=4.9, sales=13000)
    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    footer = _feed_footer_geometry(*FEED_SIZE, FEED_PAD)
    bottom, _, _, meta, _ = _feed_body_dims(
        draw, offer, NO_CLAIM, 56, 700, 1, True, True, _pill_left(offer, NO_CLAIM))
    assert meta is not None
    assert bottom <= footer["divider_y"] - 36

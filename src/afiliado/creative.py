"""Renderizador de criativos (fase 2A): artes de story e feed em PNG.

Gera a imagem a partir de um `Offer` + `CopyParts` seguindo o layout de
referência: fundo blur do produto, card arredondado, título em Anton e tags
coloridas (vendas / preço / menor preço). `copy` faz parte da interface
pública para uso futuro (o texto do post é montado à parte, em message.py) —
esta fase não desenha os campos de `CopyParts` na arte.
"""

import importlib.resources
import io

import httpx
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from afiliado.errors import SourceError
from afiliado.models import CopyParts, Offer, format_brl
from afiliado.watchlist import PriceFloor

STORY_SIZE = (1080, 1920)
FEED_SIZE = (1080, 1350)

STORY_CARD_TOP = 150
FEED_CARD_TOP = 90

STORY_TITLE_GAP = 70
FEED_TITLE_GAP = 50

CARD_WIDTH = 960
CARD_RADIUS = 42

STORY_CARD_MAX_H = 1050
FEED_CARD_MAX_H = 680

TAGS_BOTTOM_MARGIN = 40

TITLE_FONT_SIZE = 64
TITLE_MAX_WIDTH = 960
TITLE_MAX_LINES = 2
TITLE_LINE_SPACING = 10
TITLE_TO_TAGS_GAP = 40

TAG_FONT_SIZE = 52
TAG_PAD_X = 28
TAG_PAD_Y = 16
TAG_STACK_GAP = 20

COLOR_SALES = (232, 119, 34)
COLOR_PRICE = (237, 28, 36)
COLOR_FLOOR = (46, 125, 50)

DOWNLOAD_TIMEOUT = 20

_FONT_CACHE: dict[int, ImageFont.FreeTypeFont] = {}


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    font = _FONT_CACHE.get(size)
    if font is not None:
        return font
    data = (importlib.resources.files("afiliado") / "assets/fonts/Anton-Regular.ttf").read_bytes()
    font = ImageFont.truetype(io.BytesIO(data), size)
    _FONT_CACHE[size] = font
    return font


def _download_image_bytes(url: str, client: httpx.Client) -> bytes:
    try:
        response = client.get(url, timeout=DOWNLOAD_TIMEOUT)
    except httpx.HTTPError as exc:
        raise SourceError(f"falha ao baixar imagem do produto: {exc}") from exc
    if response.status_code != 200:
        raise SourceError(
            f"falha ao baixar imagem do produto: status {response.status_code}")
    content_type = response.headers.get("content-type", "")
    if not content_type.startswith("image/"):
        raise SourceError(
            f"imagem do produto com content-type inesperado: {content_type!r}")
    return response.content


def _open_product_image(data: bytes) -> Image.Image:
    try:
        return Image.open(io.BytesIO(data)).convert("RGB")
    except Exception as exc:  # noqa: BLE001 - qualquer falha de decode é SourceError
        raise SourceError(f"falha ao decodificar imagem do produto: {exc}") from exc


def _make_background(product: Image.Image, width: int, height: int) -> Image.Image:
    bg = product.copy()
    scale = max(width / bg.width, height / bg.height)
    bg = bg.resize((max(1, round(bg.width * scale)), max(1, round(bg.height * scale))))
    left = (bg.width - width) // 2
    top = (bg.height - height) // 2
    bg = bg.crop((left, top, left + width, top + height))
    bg = bg.filter(ImageFilter.GaussianBlur(28))
    bg = ImageEnhance.Brightness(bg).enhance(0.38)
    return bg


def _fit_card(img: Image.Image, max_w: int, max_h: int) -> Image.Image:
    """Redimensiona proporcionalmente para caber em max_w x max_h (nunca ultrapassa
    nenhum dos dois limites; nunca amplia além de max_w, como antes)."""
    scale = min(max_w / img.width, max_h / img.height)
    new_w = max(1, round(img.width * scale))
    new_h = max(1, round(img.height * scale))
    return img.resize((new_w, new_h))


def _make_card(product: Image.Image, max_w: int, max_h: int, radius: int) -> tuple[Image.Image, Image.Image]:
    card = _fit_card(product, max_w, max_h)
    mask = Image.new("L", card.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, card.width - 1, card.height - 1], radius=radius, fill=255)
    return card, mask


def _wrap_title(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
    max_lines: int,
) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    i = 0
    while i < len(words) and len(lines) < max_lines:
        word = words[i]
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= max_width:
            current = candidate
            i += 1
        else:
            if not current:
                current = word
                i += 1
            lines.append(current)
            current = ""
    if current and len(lines) < max_lines:
        lines.append(current)
        current = ""
    if i < len(words):
        last = lines[-1] if lines else ""
        while last and draw.textlength(last + "…", font=font) > max_width:
            last = last.rsplit(" ", 1)[0] if " " in last else last[:-1]
        lines[-1] = f"{last}…" if last else "…"
    return lines


def _draw_title(draw: ImageDraw.ImageDraw, canvas_width: int, title: str, top: int) -> int:
    """Desenha o título centralizado; retorna o y logo abaixo do bloco de texto."""
    font = _load_font(TITLE_FONT_SIZE)
    lines = _wrap_title(draw, title, font, TITLE_MAX_WIDTH, TITLE_MAX_LINES)
    ascent, descent = font.getmetrics()
    line_height = ascent + descent + TITLE_LINE_SPACING
    y = top
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        line_width = bbox[2] - bbox[0]
        x = (canvas_width - line_width) // 2 - bbox[0]
        draw.text((x, y), line, font=font, fill="white")
        y += line_height
    return y


def _draw_tag(
    draw: ImageDraw.ImageDraw,
    canvas_width: int,
    top: int,
    text: str,
    font: ImageFont.FreeTypeFont,
    color: tuple[int, int, int],
) -> int:
    """Desenha uma tag (retângulo + texto) centralizada; retorna o y abaixo dela."""
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    tag_width = text_width + 2 * TAG_PAD_X
    tag_height = text_height + 2 * TAG_PAD_Y
    x0 = (canvas_width - tag_width) // 2
    y0 = top
    x1 = x0 + tag_width
    y1 = y0 + tag_height
    draw.rectangle([x0, y0, x1, y1], fill=color)
    text_x = x0 + TAG_PAD_X - bbox[0]
    text_y = y0 + TAG_PAD_Y - bbox[1]
    draw.text((text_x, text_y), text, font=font, fill="white")
    return y1


def _sales_tag_text(sales: int) -> str:
    amount = f"{sales // 1000} MIL" if sales >= 1000 else str(sales)
    return f"{amount} VENDIDOS"


def _tag_block_height(draw: ImageDraw.ImageDraw, texts: list[str], font: ImageFont.FreeTypeFont) -> int:
    if not texts:
        return 0
    heights = []
    for text in texts:
        bbox = draw.textbbox((0, 0), text, font=font)
        heights.append((bbox[3] - bbox[1]) + 2 * TAG_PAD_Y)
    return sum(heights) + TAG_STACK_GAP * (len(texts) - 1)


def _draw_tags(
    draw: ImageDraw.ImageDraw,
    canvas_width: int,
    canvas_height: int,
    top: int,
    offer: Offer,
    price_floor: PriceFloor | None,
) -> None:
    font = _load_font(TAG_FONT_SIZE)

    price_text = (
        f"de {format_brl(offer.price_original_cents)} "
        f"por {format_brl(offer.price_current_cents)}"
    )
    floor_text = None
    if price_floor is not None and offer.price_current_cents <= price_floor.min_price_cents:
        months = max(1, round(price_floor.window_days / 30))
        floor_text = f"MENOR PREÇO EM {months} MESES"

    sales_text = _sales_tag_text(offer.sales) if offer.sales >= 1000 else None
    include_sales = sales_text is not None

    # Guarda de layout: o preço e o selo de menor preço sempre têm que caber;
    # a tag de vendas (laranja) é a menos essencial e é a primeira a cair se o
    # bloco de tags ultrapassaria o canvas.
    if include_sales:
        texts = [sales_text, price_text] + ([floor_text] if floor_text else [])
        if top + _tag_block_height(draw, texts, font) > canvas_height - TAGS_BOTTOM_MARGIN:
            include_sales = False

    y = top
    if include_sales:
        y = _draw_tag(draw, canvas_width, y, sales_text, font, COLOR_SALES)
        y += TAG_STACK_GAP

    y = _draw_tag(draw, canvas_width, y, price_text, font, COLOR_PRICE)

    if floor_text:
        y += TAG_STACK_GAP
        _draw_tag(draw, canvas_width, y, floor_text, font, COLOR_FLOOR)


def _render(
    offer: Offer,
    copy: CopyParts,
    size: tuple[int, int],
    card_top: int,
    card_max_h: int,
    title_gap: int,
    price_floor: PriceFloor | None,
    client: httpx.Client | None,
) -> bytes:
    del copy  # reservado para fases futuras; não usado no template 2A
    width, height = size

    if client is None:
        with httpx.Client(timeout=DOWNLOAD_TIMEOUT) as owned_client:
            image_bytes = _download_image_bytes(offer.image_url, owned_client)
    else:
        image_bytes = _download_image_bytes(offer.image_url, client)

    product = _open_product_image(image_bytes)

    canvas = _make_background(product, width, height)
    card, mask = _make_card(product, CARD_WIDTH, card_max_h, CARD_RADIUS)
    card_x = (width - card.width) // 2
    canvas.paste(card, (card_x, card_top), mask)

    draw = ImageDraw.Draw(canvas)
    title_top = card_top + card.height + title_gap
    title_bottom = _draw_title(draw, width, offer.title, title_top)
    tags_top = title_bottom + TITLE_TO_TAGS_GAP
    _draw_tags(draw, width, height, tags_top, offer, price_floor)

    buffer = io.BytesIO()
    canvas.save(buffer, "PNG")
    return buffer.getvalue()


def render_story(
    offer: Offer,
    copy: CopyParts,
    price_floor: PriceFloor | None = None,
    client: httpx.Client | None = None,
) -> bytes:
    return _render(offer, copy, STORY_SIZE, STORY_CARD_TOP, STORY_CARD_MAX_H,
                    STORY_TITLE_GAP, price_floor, client)


def render_feed(
    offer: Offer,
    copy: CopyParts,
    price_floor: PriceFloor | None = None,
    client: httpx.Client | None = None,
) -> bytes:
    return _render(offer, copy, FEED_SIZE, FEED_CARD_TOP, FEED_CARD_MAX_H,
                    FEED_TITLE_GAP, price_floor, client)

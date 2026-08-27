"""Renderizador de criativos — fase 2C: design system "Fiscal da Promo"
(Bricolage Grotesque + IBM Plex Mono, mascote, navy/dourado).

Gera a arte de story (1080×1920) e de feed (1080×1350) a partir de um `Offer`
e do `Verdict` já decidido (`pricing.verdict`): fundo navy com brilho
radial, cabeçalho com mascote (ver `afiliado.brand`), card branco com a foto
do produto (badge "-N%" só em modo A), título, pill de preço (referência
riscada só em modo A), meta (vendas/fonte) e — quando o veredito traz o
selo — o selo "menor preço verificado" com a mesma janela do texto. A arte
NÃO recalcula modo nem selo: é o que faz Telegram, story e feed concordarem
(C9). `story_plan`/`feed_plan` expõem o que será desenhado, para teste.
`copy` faz parte da interface pública para uso futuro (o texto do post é
montado à parte, em message.py) — esta fase não desenha `CopyParts` na arte.
"""

import functools
import importlib.resources
import io
import math

import httpx
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from afiliado import pricing
from afiliado.brand import draw_mascot
from afiliado.errors import SourceError
from afiliado.models import CopyParts, Offer, Verdict, format_brl

# --- Paleta -----------------------------------------------------------------

NAVY = (16, 20, 39)          # #101427 fundo
SURFACE = (23, 28, 51)       # #171C33 cartões/pill do CTA
BORDER = (38, 44, 74)        # #262C4A linhas
PILL_BORDER = (58, 65, 102)  # #3A4166 borda do CTA
GOLD = (224, 166, 60)        # #E0A63C destaque
INK = (20, 17, 15)           # #14110F texto sobre dourado
CREAM = (246, 239, 225)      # #F6EFE1 rosto do mascote
TEXT = (242, 243, 247)       # #F2F3F7
MUTED = (154, 160, 184)      # #9AA0B8
BLUE = (127, 160, 240)       # #7FA0F0 "verificado"
SELO_BG = (24, 34, 66)       # rgba(74,117,232,.14) sobre navy
SELO_BORDER = (42, 64, 126)  # rgba(74,117,232,.45) sobre navy
SELO_TEXT = (201, 212, 245)  # #C9D4F5
WHITE = (255, 255, 255)

GLOW_COLOR = (74, 117, 232)
PRICE_DIM_INK = (94, 69, 35)  # INK a ~65% de opacidade sobre GOLD

# --- Dimensões ----------------------------------------------------------------

STORY_SIZE = (1080, 1920)
FEED_SIZE = (1080, 1350)
STORY_PAD = 72
FEED_PAD = 64

TITLE_MAX_LINES = 2
STORY_TITLE_SIZE = 66
STORY_TITLE_WIDTH = 936
FEED_TITLE_SIZE = 56
FEED_TITLE_WIDTH = 952
# Variante "3b" do design: título menor (48/w600) tentado no feed, em 2
# linhas, antes de reduzir a 1 linha — dá mais uma chance de manter meta e
# selo (o selo é o diferenciador da marca; é o último a cair).
FEED_TITLE_ALT_SIZE = 48
FEED_TITLE_ALT_WEIGHT = 600

# Espaçamentos abaixo da pill de preço. Ficam aqui porque cada um é usado em
# DOIS lugares — no cálculo do guarda de overflow (`_*_body_dims`) e no
# desenho (`_draw_*_body`). Enquanto eram literais repetidos, mexer só no
# desenho fazia o guarda achar que cabia o que já não cabia.
STORY_META_GAP = 88     # respiro entre o preço e a linha de avaliações
STORY_SELO_GAP = 28
FEED_META_GAP = 64     # 88 no story x (1350/1920): o feed tem 570px a menos
FEED_SELO_GAP = 24

DOWNLOAD_TIMEOUT = 20

DEFAULT_BRAND_NAME = "Fiscal da Promo"


# --- Fontes -------------------------------------------------------------------

_MONO_FILES = {
    "regular": "IBMPlexMono-Regular.ttf",
    "medium": "IBMPlexMono-Medium.ttf",
    "semibold": "IBMPlexMono-SemiBold.ttf",
}


@functools.lru_cache(maxsize=1)
def _sans_bytes() -> bytes:
    return (importlib.resources.files("afiliado") / "assets/fonts/BricolageGrotesque-Variable.ttf").read_bytes()


def _mono_weight_key(weight: int) -> str:
    if weight <= 400:
        return "regular"
    if weight < 600:
        return "medium"
    return "semibold"


@functools.lru_cache(maxsize=None)
def _font(family: str, size: int, weight: int = 400) -> ImageFont.FreeTypeFont:
    """family: 'sans' (Bricolage variável) ou 'mono' (IBM Plex Mono estática).

    Cacheado por (family, size, weight): `set_variation_by_axes` muta o
    objeto, então cada combinação ganha sua própria instância já com os
    eixos definidos — nunca reaproveitamos um objeto com eixos diferentes.
    """
    if family == "sans":
        font = ImageFont.truetype(io.BytesIO(_sans_bytes()), size)
        opsz = max(12, min(96, size))
        font.set_variation_by_axes([opsz, weight, 100])
        return font
    if family == "mono":
        filename = _MONO_FILES[_mono_weight_key(weight)]
        path = importlib.resources.files("afiliado") / f"assets/fonts/{filename}"
        return ImageFont.truetype(str(path), size)
    raise ValueError(f"família de fonte desconhecida: {family!r}")


# --- Download / decode da imagem do produto -----------------------------------

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


def _get_image_bytes(offer: Offer, client: httpx.Client | None) -> bytes:
    if client is None:
        with httpx.Client(timeout=DOWNLOAD_TIMEOUT) as owned_client:
            return _download_image_bytes(offer.image_url, owned_client)
    return _download_image_bytes(offer.image_url, client)


# --- Fundo: navy + brilho radial ------------------------------------------

def _glow_background(width: int, height: int, cx: float, cy: float, rx: float, ry: float) -> Image.Image:
    base = Image.new("RGBA", (width, height), (*NAVY, 255))
    glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse(
        [cx - rx, cy - ry, cx + rx, cy + ry], fill=(*GLOW_COLOR, round(0.16 * 255)))
    glow = glow.filter(ImageFilter.GaussianBlur(120))
    return Image.alpha_composite(base, glow).convert("RGB")


# --- Card (foto do produto por contain + badge de desconto) ------------------

def _fit_card(img: Image.Image, max_w: int, max_h: int) -> Image.Image:
    """Redimensiona proporcionalmente para caber em max_w x max_h (nunca ultrapassa
    nenhum dos dois limites e nunca amplia — imagens menores que os limites ficam
    no tamanho original)."""
    scale = min(max_w / img.width, max_h / img.height, 1.0)
    new_w = max(1, round(img.width * scale))
    new_h = max(1, round(img.height * scale))
    return img.resize((new_w, new_h))


def _draw_badge(
    draw: ImageDraw.ImageDraw, right_x: float, top_y: float, real_discount_pct: int,
    font_size: int, pad_y: int, pad_x: int, radius: int = 12,
) -> None:
    text = f"-{real_discount_pct}%"
    font = _font("sans", font_size, 800)
    bbox = draw.textbbox((0, 0), text, font=font)
    w = (bbox[2] - bbox[0]) + 2 * pad_x
    h = (bbox[3] - bbox[1]) + 2 * pad_y
    x0, y0 = right_x - w, top_y
    draw.rounded_rectangle([x0, y0, x0 + w, y0 + h], radius=radius, fill=NAVY)
    draw.text((x0 + pad_x - bbox[0], y0 + pad_y - bbox[1]), text, font=font, fill=TEXT)


def _draw_card(
    canvas: Image.Image, draw: ImageDraw.ImageDraw, product: Image.Image,
    x: int, y: int, w: int, h: int, radius: int, margin: int,
    real_discount_pct: int, badge_font_size: int, badge_pad_y: int, badge_pad_x: int,
    badge_offset: int,
) -> None:
    card = Image.new("RGB", (w, h), WHITE)
    inner_w, inner_h = w - 2 * margin, h - 2 * margin
    fitted = _fit_card(product, inner_w, inner_h)
    fx = margin + (inner_w - fitted.width) // 2
    fy = margin + (inner_h - fitted.height) // 2
    card.paste(fitted, (fx, fy))
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=255)
    canvas.paste(card, (x, y), mask)
    # 0 = sem desconto verificado: nada de selo de porcentagem (o post
    # desse item destaca prova social, não preço). O desconto do vendedor
    # (Offer.discount_pct) não entra aqui — ver afiliado.pricing.
    if real_discount_pct > 0:
        _draw_badge(draw, x + w - badge_offset, y + badge_offset, real_discount_pct,
                    badge_font_size, badge_pad_y, badge_pad_x)


# --- Cabeçalho (avatar + nome/handle) -----------------------------------------

def _draw_header_story(draw: ImageDraw.ImageDraw, canvas: Image.Image, x: int, y: int,
                        d: int, brand_name: str) -> None:
    draw.ellipse([x, y, x + d, y + d], fill=GOLD)
    cx, cy = x + d / 2, y + d / 2
    draw_mascot(canvas, cx, cy, d * 0.98, ink=NAVY, skin=CREAM, cap=NAVY)
    font = _font("sans", 34, 700)
    asc, desc = font.getmetrics()
    bbox = draw.textbbox((0, 0), brand_name, font=font)
    tx = x + d + 18
    ty = cy - (asc + desc) / 2
    draw.text((tx - bbox[0], ty - bbox[1]), brand_name, font=font, fill=TEXT)


def _draw_header_feed(draw: ImageDraw.ImageDraw, canvas: Image.Image, x: int, y: int,
                       d: int, brand_name: str, handle: str | None) -> None:
    draw.ellipse([x, y, x + d, y + d], fill=GOLD)
    cx, cy = x + d / 2, y + d / 2
    draw_mascot(canvas, cx, cy, d * 0.98, ink=NAVY, skin=CREAM, cap=NAVY)
    name_font = _font("sans", 34, 700)
    name_asc, name_desc = name_font.getmetrics()
    tx = x + d + 20
    if handle:
        handle_font = _font("mono", 22, 400)
        h_asc, h_desc = handle_font.getmetrics()
        block_h = (name_asc + name_desc) + 4 + (h_asc + h_desc)
        top = cy - block_h / 2
        bbox = draw.textbbox((0, 0), brand_name, font=name_font)
        draw.text((tx - bbox[0], top - bbox[1]), brand_name, font=name_font, fill=TEXT)
        htext = handle.upper()
        hy = top + (name_asc + name_desc) + 4
        hbbox = draw.textbbox((0, 0), htext, font=handle_font)
        draw.text((tx - hbbox[0], hy - hbbox[1]), htext, font=handle_font, fill=MUTED)
    else:
        top = cy - (name_asc + name_desc) / 2
        bbox = draw.textbbox((0, 0), brand_name, font=name_font)
        draw.text((tx - bbox[0], top - bbox[1]), brand_name, font=name_font, fill=TEXT)


# --- Título --------------------------------------------------------------------

def _hard_truncate(
    draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int
) -> str:
    """Trunca caractere a caractere (com "…" no final) até caber em max_width.

    Cobre o caso de uma palavra isolada (sem espaços) maior que o limite —
    diferente do resto de `_wrap_title`, que opera por palavra."""
    if draw.textlength(text, font=font) <= max_width:
        return text
    s = text
    while s and draw.textlength(s + "…", font=font) > max_width:
        s = s[:-1]
    return f"{s}…" if s else "…"


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
    # Garantia final: cobre o caso de uma única palavra sem espaços (ou o
    # resultado do bloco acima) ainda maior que max_width — nunca deve sobrar
    # uma linha mais larga que o limite.
    return [_hard_truncate(draw, line, font, max_width) for line in lines]


def _title_dims(draw: ImageDraw.ImageDraw, offer: Offer, size: int, width: int, max_lines: int,
                 weight: int = 700) -> dict:
    font = _font("sans", size, weight)
    lines = _wrap_title(draw, offer.title, font, width, max_lines)
    line_h = round(size * 1.04)
    return {"font": font, "lines": lines, "line_h": line_h, "height": line_h * len(lines)}


def _draw_title(draw: ImageDraw.ImageDraw, canvas_width: int, top: float, dims: dict) -> float:
    """Desenha o título centralizado; retorna o y logo abaixo do bloco de texto."""
    font, line_h = dims["font"], dims["line_h"]
    y = top
    for line in dims["lines"]:
        bbox = draw.textbbox((0, 0), line, font=font)
        line_width = bbox[2] - bbox[0]
        x = (canvas_width - line_width) / 2 - bbox[0]
        draw.text((x, y - bbox[1]), line, font=font, fill=TEXT)
        y += line_h
    return y


# --- Pill de preço (align-self: flex-start) -----------------------------------

def _pill_left(offer: Offer, verdict: Verdict) -> tuple[str, bool]:
    """(texto à esquerda do preço na pill, se ele é riscado) — pelo veredito.

    Modo A (desconto verificado): a NOSSA referência, riscada — nunca o "de"
    do vendedor. Modo B: NADA — a pill é só o preço, grande; nota, vendas e
    loja ficam na linha de meta logo abaixo (`_draw_meta`), exatamente como
    no modo A, sem duplicar a prova social.
    """
    if verdict.mode == "A":
        return format_brl(offer.price_ref_cents), True
    return "", False


def _price_pill_dims(
    draw: ImageDraw.ImageDraw, offer: Offer, orig_size: int, cur_size: int,
    pad_y: int, pad_x: int, gap: int, pill_left: tuple[str, bool] = ("", False),
    max_width: int | None = None,
) -> dict:
    left_text, strike = pill_left
    orig_font = _font("sans", orig_size, 600 if strike else 500)
    cur_font = _font("sans", cur_size, 800)
    cur_text = format_brl(offer.price_current_cents)
    cur_bbox = draw.textbbox((0, 0), cur_text, font=cur_font)
    orig_asc, orig_desc = orig_font.getmetrics()
    cur_asc, cur_desc = cur_font.getmetrics()
    cur_w = cur_bbox[2] - cur_bbox[0]

    # Guarda horizontal: a pill nunca pode passar da largura útil do canvas.
    if left_text and max_width is not None:
        disponivel = max_width - 2 * pad_x - gap - cur_w
        left_text = (_hard_truncate(draw, left_text, orig_font, disponivel)
                     if disponivel > 0 else "")

    if left_text:
        orig_bbox = draw.textbbox((0, 0), left_text, font=orig_font)
        orig_w = orig_bbox[2] - orig_bbox[0]
        content_h = max(orig_asc + orig_desc, cur_asc + cur_desc)
        content_w = orig_w + gap + cur_w
    else:
        # Sem o "de" (modo B) a pill encolhe para o preço + padding.
        orig_bbox, orig_w = (0, 0, 0, 0), 0
        content_h = cur_asc + cur_desc
        content_w = cur_w
    return {
        "orig_font": orig_font, "cur_font": cur_font,
        "orig_text": left_text, "cur_text": cur_text, "strike": strike,
        "orig_bbox": orig_bbox, "cur_bbox": cur_bbox,
        "orig_asc": orig_asc, "cur_asc": cur_asc,
        "orig_w": orig_w, "cur_w": cur_w,
        "width": content_w + 2 * pad_x, "height": content_h + 2 * pad_y,
        "pad_y": pad_y, "pad_x": pad_x, "gap": gap,
    }


def _draw_price_pill(draw: ImageDraw.ImageDraw, x: float, y: float, dims: dict, radius: int = 16) -> float:
    w, h = dims["width"], dims["height"]
    draw.rounded_rectangle([x, y, x + w, y + h], radius=radius, fill=GOLD)
    cur_font, orig_font = dims["cur_font"], dims["orig_font"]
    baseline = y + dims["pad_y"] + dims["cur_asc"]
    cur_x = x + dims["pad_x"]
    if dims["orig_text"]:
        orig_x = x + dims["pad_x"]
        orig_bbox = dims["orig_bbox"]
        orig_y = baseline - dims["orig_asc"]
        draw.text((orig_x - orig_bbox[0], orig_y - orig_bbox[1]), dims["orig_text"],
                   font=orig_font, fill=PRICE_DIM_INK)
        if dims["strike"]:
            strike_y = round(orig_y + (orig_bbox[1] + orig_bbox[3]) / 2)
            draw.line([(orig_x, strike_y), (orig_x + dims["orig_w"], strike_y)],
                      fill=PRICE_DIM_INK, width=3)
        cur_x = orig_x + dims["orig_w"] + dims["gap"]
    cur_bbox = dims["cur_bbox"]
    cur_y = baseline - dims["cur_asc"]
    draw.text((cur_x - cur_bbox[0], cur_y - cur_bbox[1]), dims["cur_text"], font=cur_font, fill=INK)
    return y + h


# --- Meta (nota [estrela] · vendas · fonte) ------------------------------------

STAR_RADIUS_EM = 0.42   # raio da estrela em relação ao tamanho da fonte
STAR_INNER = 0.4        # raio interno / raio externo (10 vértices alternados)
STAR_PAD_EM = 0.08      # respiro de cada lado da estrela


def _source_label(source: str) -> str:
    return "Mercado Livre" if source == "meli" else "Shopee"


def _meta_parts(offer: Offer) -> tuple[str, str]:
    """Linha mono abaixo do preço, em dois segmentos ao redor da estrela:
    (texto até a nota, o resto). Com nota: ("4,9 ", " · 30 mil vendidos ·
    Shopee") — a estrela entra entre os dois, desenhada como vetor
    (`_draw_star`). Sem nota (rating == 0): ("", "30 mil vendidos · Shopee") —
    nada de estrela, o texto começa em vendas (ou na loja)."""
    resto = " · ".join(p for p in (pricing.format_sales(offer.sales),
                                    _source_label(offer.source)) if p)
    if offer.rating > 0:
        nota = f"{offer.rating:.1f}".replace(".", ",")
        return f"{nota} ", f" · {resto}"
    return "", resto


def _draw_star(draw: ImageDraw.ImageDraw, cx: float, cy: float, r: float,
               fill: tuple[int, int, int]) -> None:
    """Estrela de 5 pontas como polígono: 10 vértices alternando raio `r` e
    `r * STAR_INNER`, começando com a ponta para cima. Não depende do glifo
    da fonte — nem a Bricolage nem a IBM Plex Mono têm U+2605/U+2B50 (caem
    no .notdef, o "tofu"); mesmo motivo de `_draw_check` desenhar o "✓"."""
    pontos = []
    for i in range(10):
        ang = -math.pi / 2 + i * math.pi / 5
        raio = r if i % 2 == 0 else r * STAR_INNER
        pontos.append((cx + raio * math.cos(ang), cy + raio * math.sin(ang)))
    draw.polygon(pontos, fill=fill)


def _meta_layout(draw: ImageDraw.ImageDraw, offer: Offer, font: ImageFont.FreeTypeFont) -> dict:
    """Medidas da linha de meta: larguras dos dois segmentos, a estrela
    (`2r` + respiro dos dois lados, centrada na altura-x da fonte) e a
    largura total — é o que `_meta_dims` mede e o que `_draw_meta` desenha."""
    antes, depois = _meta_parts(offer)
    w_antes = draw.textlength(antes, font=font) if antes else 0.0
    w_depois = draw.textlength(depois, font=font)
    r = pad = star_w = star_dx = star_dy = 0.0
    if antes:
        r = font.size * STAR_RADIUS_EM
        pad = font.size * STAR_PAD_EM
        star_w = 2 * r + 2 * pad
        # Centro vertical na altura-x: bbox de "x" relativo ao topo do
        # ascender (âncora "la", a mesma de draw.text sem anchor).
        _, x_top, _, x_bottom = font.getbbox("x")
        star_dx = w_antes + pad + r
        star_dy = (x_top + x_bottom) / 2
    return {
        "antes": antes, "depois": depois, "w_antes": w_antes, "w_depois": w_depois,
        "r": r, "star_w": star_w, "star_dx": star_dx, "star_dy": star_dy,
        "width": w_antes + star_w + w_depois,
    }


def _meta_dims(draw: ImageDraw.ImageDraw, offer: Offer, mono_size: int) -> dict:
    font = _font("mono", mono_size, 400)
    asc, desc = font.getmetrics()
    return {"font": font, "height": asc + desc,
            "width": _meta_layout(draw, offer, font)["width"]}


def _draw_meta(draw: ImageDraw.ImageDraw, x: float, y: float, offer: Offer,
               font: ImageFont.FreeTypeFont, fill: tuple[int, int, int]) -> float:
    """Desenha a meta em segmentos — texto até a nota, estrela vetorial, o
    resto — tudo na mesma cor. Devolve a largura total (a mesma que
    `_meta_dims` mede, estrela incluída), para quem chama continuar medindo."""
    lay = _meta_layout(draw, offer, font)
    cursor = x
    if lay["antes"]:
        draw.text((cursor, y), lay["antes"], font=font, fill=fill)
        _draw_star(draw, x + lay["star_dx"], y + lay["star_dy"], lay["r"], fill)
        cursor = x + lay["w_antes"] + lay["star_w"]
    draw.text((cursor, y), lay["depois"], font=font, fill=fill)
    return lay["width"]


# --- Selo "menor preço verificado" ---------------------------------------------

def selo_label(verdict: Verdict) -> str:
    """Rótulo do selo na arte — "" quando o veredito não traz selo. A decisão
    (SE há selo) é de `pricing.verdict`; aqui só a forma: "MENOR PREÇO
    VERIFICADO · 6 MESES" / "· 45 DIAS", com a mesma janela do texto."""
    if not verdict.seal:
        return ""
    return f"MENOR PREÇO VERIFICADO · {pricing.window_text(verdict.seal_window_days).upper()}"


def _draw_check(draw: ImageDraw.ImageDraw, x: float, y: float, size: float,
                 color: tuple[int, int, int], width: int = 5) -> None:
    """Desenha um "✓" com dois segmentos — não depende do glifo da fonte
    (a Bricolage pode não ter U+2713)."""
    draw.line([(x + size * 0.05, y + size * 0.55), (x + size * 0.38, y + size * 0.85)],
              fill=color, width=width)
    draw.line([(x + size * 0.38, y + size * 0.85), (x + size * 0.95, y + size * 0.12)],
              fill=color, width=width)


def _selo_dims(draw: ImageDraw.ImageDraw, verdict: Verdict, mono_size: int,
               pad_y: int, pad_x: int, gap: int) -> dict:
    text = selo_label(verdict)
    font = _font("mono", mono_size, 500)
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    asc, desc = font.getmetrics()
    check_size = round(mono_size * 1.2)
    content_h = max(check_size, asc + desc)
    content_w = check_size + gap + text_w
    return {
        "font": font, "text": text, "bbox": bbox, "asc": asc, "desc": desc,
        "check_size": check_size, "content_h": content_h,
        "width": content_w + 2 * pad_x, "height": content_h + 2 * pad_y,
        "pad_y": pad_y, "pad_x": pad_x, "gap": gap,
    }


def _draw_selo(draw: ImageDraw.ImageDraw, x: float, y: float, dims: dict, radius: int = 16) -> float:
    w, h = dims["width"], dims["height"]
    draw.rounded_rectangle([x, y, x + w, y + h], radius=radius, outline=SELO_BORDER, width=1, fill=SELO_BG)
    check_size, content_h = dims["check_size"], dims["content_h"]
    check_y = y + dims["pad_y"] + (content_h - check_size) / 2
    _draw_check(draw, x + dims["pad_x"], check_y, check_size, BLUE)
    text_x = x + dims["pad_x"] + check_size + dims["gap"]
    bbox = dims["bbox"]
    text_y = y + dims["pad_y"] + (content_h - (dims["asc"] + dims["desc"])) / 2
    draw.text((text_x - bbox[0], text_y - bbox[1]), dims["text"], font=dims["font"], fill=SELO_TEXT)
    return y + h


# --- Corpo (título + pill de preço + meta + selo) e guarda de overflow -------

def _story_body_dims(draw, offer, verdict, title_size, title_weight, title_lines_cap,
                      include_meta, include_selo, pill_left=("", False)):
    title = _title_dims(draw, offer, title_size, STORY_TITLE_WIDTH, title_lines_cap, title_weight)
    price = _price_pill_dims(draw, offer, 36, 96, 20, 30, 24, pill_left, STORY_TITLE_WIDTH)
    y = 1050 + title["height"] + 34 + price["height"]
    meta = None
    if include_meta:
        meta = _meta_dims(draw, offer, 30)
        y += STORY_META_GAP + meta["height"]
    selo = None
    if include_selo and verdict.seal:
        selo = _selo_dims(draw, verdict, 25, 20, 24, 16)
        y += STORY_SELO_GAP + selo["height"]
    return y, title, price, meta, selo


def _feed_body_dims(draw, offer, verdict, title_size, title_weight, title_lines_cap,
                     include_meta, include_selo, pill_left=("", False)):
    title = _title_dims(draw, offer, title_size, FEED_TITLE_WIDTH, title_lines_cap, title_weight)
    price = _price_pill_dims(draw, offer, 32, 84, 18, 28, 22, pill_left, FEED_TITLE_WIDTH)
    y = 790 + title["height"] + 24 + price["height"]
    meta = None
    if include_meta:
        meta = _meta_dims(draw, offer, 27)
        y += FEED_META_GAP + meta["height"]
    selo = None
    if include_selo and verdict.seal:
        selo = _selo_dims(draw, verdict, 23, 20, 24, 16)
        y += FEED_SELO_GAP + selo["height"]
    return y, title, price, meta, selo


def _run_guard_steps(draw, offer, verdict, allowed_bottom, dims_fn, title_size, title_weight,
                     pill_left=("", False)):
    """Guarda de overflow: se o corpo (título..selo) invadir o rodapé, reduz o
    título p/ 1 linha, depois descarta o meta, e só por último o selo — o selo
    é o diferenciador da marca (prova de menor preço) e é o que mais queremos
    preservar."""
    title_cap, meta_on, selo_on = TITLE_MAX_LINES, True, True
    while True:
        bottom, title, price, meta, selo = dims_fn(
            draw, offer, verdict, title_size, title_weight, title_cap, meta_on, selo_on,
            pill_left)
        if bottom <= allowed_bottom:
            return title, price, meta, selo
        if title_cap > 1:
            title_cap = 1
        elif meta_on:
            meta_on = False
        elif selo_on:
            selo_on = False
        else:
            return title, price, meta, selo  # não cabe mesmo assim — segue com o mínimo


def _story_body_options(draw, offer, verdict, allowed_bottom, pill_left=("", False)):
    return _run_guard_steps(draw, offer, verdict, allowed_bottom, _story_body_dims,
                             STORY_TITLE_SIZE, 700, pill_left)


def _feed_body_options(draw, offer, verdict, allowed_bottom, pill_left=("", False)):
    # Antes do passo "reduzir para 1 linha": tenta a variante "3b" do design
    # (título 48px/w600, ainda em 2 linhas) — se ela já couber com meta e
    # selo inteiros, evita truncar o título por causa de um rodapé apertado.
    for size, weight in ((FEED_TITLE_SIZE, 700), (FEED_TITLE_ALT_SIZE, FEED_TITLE_ALT_WEIGHT)):
        bottom, title, price, meta, selo = _feed_body_dims(
            draw, offer, verdict, size, weight, TITLE_MAX_LINES, True, True, pill_left)
        if bottom <= allowed_bottom:
            return title, price, meta, selo
    return _run_guard_steps(draw, offer, verdict, allowed_bottom, _feed_body_dims,
                             FEED_TITLE_SIZE, 700, pill_left)


def _draw_story_body(draw, canvas_width, offer, title, price, meta, selo) -> None:
    y = _draw_title(draw, canvas_width, 1050, title) + 34
    # A pill é centralizada, como o título, a meta e o selo: no story vertical
    # ela é o elemento mais pesado da composição, e encostada na margem
    # esquerda desequilibrava o bloco inteiro.
    y = _draw_price_pill(draw, (canvas_width - price["width"]) / 2, y, price)
    if meta is not None:
        _draw_meta(draw, (canvas_width - meta["width"]) / 2, y + STORY_META_GAP,
                   offer, meta["font"], MUTED)
        y += STORY_META_GAP + meta["height"]
    if selo is not None:
        selo_x = (canvas_width - selo["width"]) / 2
        _draw_selo(draw, selo_x, y + STORY_SELO_GAP, selo)


def _draw_feed_body(draw, canvas_width, offer, title, price, meta, selo) -> None:
    y = _draw_title(draw, canvas_width, 790, title) + 24
    # Centralizada como no story (pedido do dono, 2026-08-27): o feed era o
    # único formato com a pill fora do eixo, e ao lado do selo e da meta —
    # ambos centralizados — aquilo lia como descuido, não como escolha.
    y = _draw_price_pill(draw, (canvas_width - price["width"]) / 2, y, price)
    if meta is not None:
        _draw_meta(draw, (canvas_width - meta["width"]) / 2, y + FEED_META_GAP,
                   offer, meta["font"], MUTED)
        y += FEED_META_GAP + meta["height"]
    if selo is not None:
        selo_x = (canvas_width - selo["width"]) / 2
        _draw_selo(draw, selo_x, y + FEED_SELO_GAP, selo)


# --- Rodapés --------------------------------------------------------------------

def _story_footer_geometry(draw: ImageDraw.ImageDraw, width: int, height: int,
                            handle: str | None, offer: Offer) -> dict:
    pad_y, pad_x = 26, 40
    font = _font("sans", 42, 700)
    label = "MERCADO LIVRE" if offer.source == "meli" else "SHOPEE"
    text = f"→  LINK NA {label}"
    bbox = draw.textbbox((0, 0), text, font=font)
    w = (bbox[2] - bbox[0]) + 2 * pad_x
    h = (bbox[3] - bbox[1]) + 2 * pad_y

    handle_font = _font("mono", 26, 400)
    h_asc, h_desc = handle_font.getmetrics()
    handle_line_h = h_asc + h_desc

    bottom_margin = 72
    if handle:
        handle_bottom = height - bottom_margin
        handle_top = handle_bottom - handle_line_h
        cta_bottom = handle_top - 36
    else:
        handle_top = None
        cta_bottom = height - bottom_margin
    cta_top = cta_bottom - h
    cta_x0 = (width - w) / 2
    return {
        "cta_box": (cta_x0, cta_top, cta_x0 + w, cta_top + h),
        "cta_text": text, "cta_font": font, "cta_bbox": bbox,
        "cta_pad_x": pad_x, "cta_pad_y": pad_y,
        "handle_font": handle_font, "handle_top": handle_top,
    }


def _draw_story_footer(draw: ImageDraw.ImageDraw, width: int, handle: str | None, geo: dict) -> None:
    x0, y0, x1, y1 = geo["cta_box"]
    draw.rounded_rectangle([x0, y0, x1, y1], radius=999, outline=PILL_BORDER, width=2, fill=SURFACE)
    bbox = geo["cta_bbox"]
    draw.text((x0 + geo["cta_pad_x"] - bbox[0], y0 + geo["cta_pad_y"] - bbox[1]),
               geo["cta_text"], font=geo["cta_font"], fill=TEXT)
    if handle and geo["handle_top"] is not None:
        htext = handle.upper()
        hfont = geo["handle_font"]
        hbbox = draw.textbbox((0, 0), htext, font=hfont)
        hx = (width - (hbbox[2] - hbbox[0])) / 2
        draw.text((hx - hbbox[0], geo["handle_top"] - hbbox[1]), htext, font=hfont, fill=MUTED)


def _feed_footer_geometry(width: int, height: int, pad: int) -> dict:
    divider_y = height - pad - 28 - 66 - 28
    row_top = divider_y + 28
    circle_d = 66
    circle_x = width - pad - circle_d
    return {
        "divider_y": divider_y, "row_top": row_top,
        "circle_box": (circle_x, row_top, circle_x + circle_d, row_top + circle_d),
    }


def _draw_feed_footer(draw: ImageDraw.ImageDraw, width: int, pad: int, offer: Offer, geo: dict) -> None:
    y0 = geo["divider_y"]
    draw.line([(pad, y0), (width - pad, y0)], fill=BORDER, width=1)

    circle_box = geo["circle_box"]
    draw.ellipse(circle_box, fill=GOLD)
    arrow_font = _font("sans", 34, 700)
    abbox = draw.textbbox((0, 0), "→", font=arrow_font)
    acx = (circle_box[0] + circle_box[2]) / 2
    acy = (circle_box[1] + circle_box[3]) / 2
    aw, ah = abbox[2] - abbox[0], abbox[3] - abbox[1]
    draw.text((acx - aw / 2 - abbox[0], acy - ah / 2 - abbox[1]), "→", font=arrow_font, fill=INK)

    text_font = _font("sans", 38, 700)
    text = f"Link na bio · {_source_label(offer.source)}"
    t_asc, t_desc = text_font.getmetrics()
    row_top = geo["row_top"]
    row_h = circle_box[3] - circle_box[1]
    ty = row_top + (row_h - (t_asc + t_desc)) / 2
    tbbox = draw.textbbox((0, 0), text, font=text_font)
    draw.text((pad - tbbox[0], ty - tbbox[1]), text, font=text_font, fill=TEXT)


# --- Plano do corpo: o que a arte vai desenhar (hook testável) ---------------

def _story_plan(draw: ImageDraw.ImageDraw, offer: Offer, verdict: Verdict,
                handle: str | None) -> dict:
    width, height = STORY_SIZE
    footer = _story_footer_geometry(draw, width, height, handle, offer)
    pill_left = _pill_left(offer, verdict)
    title, price, meta, selo = _story_body_options(
        draw, offer, verdict, footer["cta_box"][1] - 36, pill_left)
    return {"footer": footer, "title": title, "price": price, "meta": meta, "selo": selo,
            "pill_left": pill_left, "badge_pct": verdict.discount_pct}


def _feed_plan(draw: ImageDraw.ImageDraw, offer: Offer, verdict: Verdict,
               handle: str | None) -> dict:
    width, height = FEED_SIZE
    footer = _feed_footer_geometry(width, height, FEED_PAD)
    pill_left = _pill_left(offer, verdict)
    title, price, meta, selo = _feed_body_options(
        draw, offer, verdict, footer["divider_y"] - 36, pill_left)
    return {"footer": footer, "title": title, "price": price, "meta": meta, "selo": selo,
            "pill_left": pill_left, "badge_pct": verdict.discount_pct}


def _resumo(plan: dict) -> dict:
    return {
        "selo": plan["selo"]["text"] if plan["selo"] is not None else "",
        "badge_pct": plan["badge_pct"],
        "riscado": plan["pill_left"][0],
        "title_lines": list(plan["title"]["lines"]),
        "meta": plan["meta"] is not None,
    }


def story_plan(offer: Offer, verdict: Verdict, handle: str | None = None) -> dict:
    """O que `render_story` vai desenhar para este veredito — sem baixar a
    imagem nem pintar: `selo` (rótulo, ou "" quando não há), `badge_pct`,
    `riscado` (a referência riscada na pill, ou ""), `title_lines`, `meta`.
    É o hook que prova, por teste e não por pixel, que arte, texto e
    legendas concordam (mesmo `Verdict` -> selo em todos ou em nenhum)."""
    draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    return _resumo(_story_plan(draw, offer, verdict, handle))


def feed_plan(offer: Offer, verdict: Verdict, handle: str | None = None) -> dict:
    """Idem `story_plan`, para a arte de feed."""
    draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    return _resumo(_feed_plan(draw, offer, verdict, handle))


# --- Render principal -----------------------------------------------------------

def _render_story(offer: Offer, verdict: Verdict, client: httpx.Client | None,
                   handle: str | None, brand_name: str) -> bytes:
    width, height = STORY_SIZE
    product = _open_product_image(_get_image_bytes(offer, client))

    canvas = _glow_background(width, height, 540, 154, 594, 528)
    draw = ImageDraw.Draw(canvas)
    plan = _story_plan(draw, offer, verdict, handle)

    _draw_header_story(draw, canvas, 72, 120, 68, brand_name)
    _draw_card(canvas, draw, product, 72, 224, 936, 790, 28, 24,
               plan["badge_pct"], 44, 14, 22, 28)
    _draw_story_body(draw, width, offer, plan["title"], plan["price"], plan["meta"], plan["selo"])
    _draw_story_footer(draw, width, handle, plan["footer"])

    buffer = io.BytesIO()
    canvas.save(buffer, "PNG")
    return buffer.getvalue()


def _render_feed(offer: Offer, verdict: Verdict, client: httpx.Client | None,
                  handle: str | None, brand_name: str) -> bytes:
    width, height = FEED_SIZE
    product = _open_product_image(_get_image_bytes(offer, client))

    canvas = _glow_background(width, height, 540, 81, 594, 338)
    draw = ImageDraw.Draw(canvas)
    plan = _feed_plan(draw, offer, verdict, handle)

    _draw_header_feed(draw, canvas, 64, 64, 62, brand_name, handle)
    _draw_card(canvas, draw, product, 64, 158, 952, 600, 26, 20,
               plan["badge_pct"], 42, 12, 20, 26)
    _draw_feed_body(draw, width, offer, plan["title"], plan["price"], plan["meta"], plan["selo"])
    _draw_feed_footer(draw, width, FEED_PAD, offer, plan["footer"])

    buffer = io.BytesIO()
    canvas.save(buffer, "PNG")
    return buffer.getvalue()


def render_story(
    offer: Offer,
    copy: CopyParts,
    verdict: Verdict,
    client: httpx.Client | None = None,
    handle: str | None = None,
    brand_name: str = DEFAULT_BRAND_NAME,
) -> bytes:
    del copy  # reservado para fases futuras; não usado no template atual
    return _render_story(offer, verdict, client, handle, brand_name)


def render_feed(
    offer: Offer,
    copy: CopyParts,
    verdict: Verdict,
    client: httpx.Client | None = None,
    handle: str | None = None,
    brand_name: str = DEFAULT_BRAND_NAME,
) -> bytes:
    del copy  # reservado para fases futuras; não usado no template atual
    return _render_feed(offer, verdict, client, handle, brand_name)

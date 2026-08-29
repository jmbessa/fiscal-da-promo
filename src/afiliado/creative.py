"""Renderizador de criativos — fase 2C: design system "Fiscal da Promo"
(Bricolage Grotesque + IBM Plex Mono, mascote, navy/dourado).

Gera a arte de story (1080×1920) e de feed (1080×1350) a partir de um `Offer`
e do `Verdict` já decidido (`pricing.verdict`): fundo navy com brilho
radial, cabeçalho com mascote (ver `afiliado.brand`), card branco com a foto
do produto (badge "-N%" só em modo A), título, pill de preço (referência
riscada só em modo A; "SEM CUPOM" à direita do preço só na Shopee — fase 5K,
DESLIGADO desde a 5N, ver `_pill_nota`), meta (vendas/fonte) e — quando o
veredito traz o selo — o selo "menor preço verificado" com a mesma janela do
texto. A arte NÃO recalcula modo nem selo: é o que faz Telegram, story e feed
concordarem (C9). `story_plan`/`feed_plan` expõem o que será desenhado, para teste.
`copy` faz parte da interface pública para uso futuro (o texto do post é
montado à parte, em message.py) — esta fase não desenha `CopyParts` na arte.
"""

import functools
import importlib.resources
import io
import math
from datetime import date

import httpx
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from afiliado import pricing
from afiliado.brand import draw_mascot
from afiliado.errors import SourceError
from afiliado.models import CopyParts, Offer, Post, Verdict, format_brl

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
# Fase 5D: a única cor de ACUSAÇÃO da paleta. Só o pico inflado a usa — se ela
# aparecer em mais um lugar, ela deixa de significar "olha isto aqui".
RED = (232, 84, 74)          # #E8544A

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

# Fase 5K — o corpo do rótulo "SEM CUPOM" dentro da pill do preço. Mesmo
# tamanho da linha de meta de cada formato, porque é a mesma VOZ: mono, caixa
# alta, letra miúda que qualifica o número (a sans é a voz humana — título,
# preço, CTA; a mono é a voz do sistema — meta, selo, handle). Continuam aqui
# com o rótulo desligado (5N): eles medem o rótulo, não decidem se ele sai.
STORY_NOTA_SIZE = 30
FEED_NOTA_SIZE = 27

DOWNLOAD_TIMEOUT = 20

DEFAULT_BRAND_NAME = "Fiscal da Promo"
DEFAULT_HANDLE = "@ofiscaldapromo"

# Fase 5D — a frase-assinatura. É uma CTA de IDENTIDADE, não um pedido de
# engajamento: pedir curtida, comentário ou compartilhamento é rebaixado pela
# Meta (regra oficial de engagement bait), e o padrão que funciona sem pedir
# nada é a frase fixa repetida em toda peça (o caso Erika Kullberg, em
# `docs/superpowers/reviews/2026-08-28-pesquisa-feed.md`). Constante, nunca
# gerada: é ela que constrói o reconhecimento.
ASSINATURA = "Quem conferiu? O Fiscal."


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


def _texto_dims(draw: ImageDraw.ImageDraw, text: str, size: int, width: int,
                max_lines: int, weight: int = 700, altura_linha: float = 1.04) -> dict:
    font = _font("sans", size, weight)
    lines = _wrap_title(draw, text, font, width, max_lines)
    line_h = round(size * altura_linha)
    return {"font": font, "lines": lines, "line_h": line_h, "height": line_h * len(lines)}


def _title_dims(draw: ImageDraw.ImageDraw, offer: Offer, size: int, width: int, max_lines: int,
                 weight: int = 700) -> dict:
    return _texto_dims(draw, offer.title, size, width, max_lines, weight)


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

def _pill_nota(offer: Offer) -> str:
    """O rótulo à DIREITA do preço na pill: a frase que qualifica o número.

    Fase 5P: quando o navegador leu o preço de checkout, é a CONDIÇÃO dele
    ("COM CUPOM", "NO PIX COM CUPOM") — o mesmo slot, o mesmo alinhamento, o
    sentido oposto ao da 5N. Sem leitura é o "SEM CUPOM" das ofertas da Shopee,
    "" no resto (fase 5K) — e "" em tudo enquanto `pricing.MOSTRAR_SEM_CUPOM`
    estiver desligado, que é o estado desde a fase 5N.

    Com ele vazio o slot da direita não existe: `nota_bloco` é 0 e a pill volta
    a medir preço + padding, exatamente como antes da 5K (verificado nos
    previews da 5N). Nada aqui reserva espaço para um rótulo que não vem.

    Quem decide é `pricing.sem_cupom`, junto do resto da régua — aqui só a
    forma (caixa alta, como o selo). A colocação foi escolhida olhando os
    previews de 2026-08-28 e continua valendo para quem religar o rótulo; as
    três alternativas caíram na imagem:

    - na LINHA DE META ("· sem cupom"): ela não tem guarda horizontal (fase
      5H) e passou a encostar na margem; no pior caso o texto era cortado pela
      borda do canvas. Pior ainda, no feed em modo A com selo a meta é
      DERRUBADA pelo guarda de overflow — e o rótulo sumia com ela;
    - em LINHA PRÓPRIA sob a pill: come o respiro entre o preço e a meta, que o
      dono pediu e que foi aumentado de propósito (`STORY_META_GAP`);
    - em SEGUNDA LINHA dentro da pill: engorda a pill em ~50 px e o guarda de
      overflow passa a derrubar o SELO no feed com título longo.

    À direita do preço, alinhado pela linha de base, ele custa ZERO altura
    (cabe na que o preço já ocupa) e entra no guarda horizontal que a pill já
    tinha — onde falta espaço quem cede é a referência riscada, não ele.

    "COM CUPOM" mede exatamente o mesmo que "SEM CUPOM" (162 px no story, 144 no
    feed), então a medição da 5K vale inteira. "NO PIX COM CUPOM" mede 288 px e
    não cabe no pior caso publicável junto do riscado — e é aí que o guarda faz
    o que sempre fez: corta o riscado, nunca a condição."""
    return pricing.rotulo_do_preco(offer).upper()


def _pill_left(offer: Offer, verdict: Verdict) -> tuple[str, bool]:
    """(texto à esquerda do preço na pill, se ele é riscado) — pelo veredito.

    Modo A (desconto verificado): a NOSSA referência, riscada — nunca o "de"
    do vendedor.

    Modo B COM preço de checkout (fase 5R): o preço de CATÁLOGO, riscado. É a
    BASE da porcentagem que o badge mostra, e sem ela o seguidor vê um "-12%"
    que não pode conferir. Continua não sendo o "de" do vendedor: é a nossa
    medição de hoje, o mesmo número que a página escreve como "ou R$ 599,00 sem
    cupom" — os dois números do par são observados por nós, que é exatamente a
    condição para o percentual poder ser publicado.

    Modo B sem leitura: NADA — a pill é só o preço, grande; nota, vendas e loja
    ficam na linha de meta logo abaixo (`_draw_meta`), sem duplicar a prova
    social. É o comportamento de sempre, e é o da maioria das peças.
    """
    if verdict.mode == "A":
        return format_brl(offer.price_ref_cents), True
    if offer.checkout_discount_pct > 0:
        return format_brl(offer.price_current_cents), True
    return "", False


def _badge_pct(offer: Offer, verdict: Verdict) -> int:
    """A porcentagem do badge do card — UMA por peça, sempre a mais forte que é
    verdadeira, e nunca a do vendedor.

    Modo A: a do veredito, que já é a da referência para o preço PUBLICADO
    (5P) — ela é sempre maior que a de checkout, porque a referência é maior
    que o catálogo (`1 - c/x` cresce com x).

    Modo B: a de CHECKOUT, quando existe. Este é o pedido do dono da fase 5R —
    "precisamos evidenciar a porcentagem de desconto nos stories" —, e até aqui
    a peça de modo B com preço de checkout não mostrava porcentagem nenhuma: o
    badge só existia no modo A. Sem leitura, 0, e o badge não é desenhado.
    """
    return verdict.discount_pct or offer.checkout_discount_pct


def _price_pill_dims(
    draw: ImageDraw.ImageDraw, offer: Offer, orig_size: int, cur_size: int,
    pad_y: int, pad_x: int, gap: int, pill_left: tuple[str, bool] = ("", False),
    max_width: int | None = None, nota_size: int = 0,
) -> dict:
    left_text, strike = pill_left
    orig_font = _font("sans", orig_size, 600 if strike else 500)
    cur_font = _font("sans", cur_size, 800)
    # `published_price_cents`: o de checkout quando a fase 5P o leu, o de
    # catálogo quando não. A pill e o texto do Telegram leem o MESMO campo — é
    # o que impede arte e legenda de mostrarem números diferentes.
    cur_text = format_brl(offer.published_price_cents)
    cur_bbox = draw.textbbox((0, 0), cur_text, font=cur_font)
    orig_asc, orig_desc = orig_font.getmetrics()
    cur_asc, cur_desc = cur_font.getmetrics()
    cur_w = cur_bbox[2] - cur_bbox[0]

    # Slot da direita: o rótulo "SEM CUPOM" (fase 5K). `nota_size == 0` é quem
    # não tem slot nenhum — o carrossel e o story usam os tamanhos do formato.
    # Rótulo vazio (`_pill_nota` -> "", o padrão desde a 5N) tem o MESMO efeito
    # de `nota_size == 0`: `nota_bloco` = 0 e a pill não guarda espaço nenhum.
    nota_text = _pill_nota(offer) if nota_size > 0 else ""
    nota_font = _font("mono", nota_size, 500) if nota_text else None
    nota_bbox = draw.textbbox((0, 0), nota_text, font=nota_font) if nota_text else (0, 0, 0, 0)
    nota_w = nota_bbox[2] - nota_bbox[0]
    nota_asc = nota_font.getmetrics()[0] if nota_text else 0
    # O que o rótulo tira da largura disponível: ele e o respiro antes dele.
    nota_bloco = (gap + nota_w) if nota_text else 0

    # Guarda horizontal: a pill nunca pode passar da largura útil do canvas.
    # O rótulo entra na conta ANTES do riscado: onde não cabe tudo, quem cede é
    # a referência riscada, nunca o rótulo.
    #
    # E ela cede INTEIRA — não é truncada (fase 5P). Até aqui o guarda chamava
    # `_hard_truncate`, e com a condição longa no pior caso publicável isso
    # produzia um riscado "R$ 7…" ao lado de "R$ 523,48" (visto no preview de
    # 2026-08-28): um número pela metade, riscado, que o seguidor lê como "de
    # R$ 7". Preço cortado não é decoração degradada, é informação FALSA — ao
    # contrário de um título cortado, que é onde `_hard_truncate` continua
    # servindo. Sem a referência a peça ainda diz o desconto: o badge de -N% e o
    # veredito continuam lá.
    if left_text and max_width is not None:
        disponivel = max_width - 2 * pad_x - gap - cur_w - nota_bloco
        medida = draw.textbbox((0, 0), left_text, font=orig_font)
        if (medida[2] - medida[0]) > disponivel:
            left_text = ""

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
    # `content_h` NÃO cresce com o rótulo: ele compartilha a linha de base do
    # preço e é muito menor que ele. É isso que faz a colocação custar zero
    # altura — e é por isso que ela não derruba meta nem selo.
    return {
        "orig_font": orig_font, "cur_font": cur_font,
        "orig_text": left_text, "cur_text": cur_text, "strike": strike,
        "orig_bbox": orig_bbox, "cur_bbox": cur_bbox,
        "orig_asc": orig_asc, "cur_asc": cur_asc,
        "orig_w": orig_w, "cur_w": cur_w,
        "nota_font": nota_font, "nota_text": nota_text, "nota_bbox": nota_bbox,
        "nota_asc": nota_asc, "nota_w": nota_w,
        "width": content_w + nota_bloco + 2 * pad_x, "height": content_h + 2 * pad_y,
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
    if dims["nota_text"]:
        # Mesma linha de base do preço e a mesma tinta apagada do riscado: a
        # pill fica com o número no meio e uma letra miúda de cada lado.
        nota_bbox = dims["nota_bbox"]
        nota_x = cur_x + dims["cur_w"] + dims["gap"]
        nota_y = baseline - dims["nota_asc"]
        draw.text((nota_x - nota_bbox[0], nota_y - nota_bbox[1]), dims["nota_text"],
                  font=dims["nota_font"], fill=PRICE_DIM_INK)
    return y + h


# --- Meta (nota [estrela] · vendas · fonte) ------------------------------------

STAR_RADIUS_EM = 0.42   # raio da estrela em relação ao tamanho da fonte
STAR_INNER = 0.4        # raio interno / raio externo (10 vértices alternados)
STAR_PAD_EM = 0.08      # respiro de cada lado da estrela


def _source_label(source: str) -> str:
    return "Mercado Livre" if source == "meli" else "Shopee"


def _meta_parts(offer: Offer) -> tuple[str, str]:
    """Linha mono abaixo do preço, em dois segmentos ao redor da estrela:
    (texto até a nota, o resto). Com nota: ("4,9 ", " · 45 mil vendidos no
    último mês · Shopee") — a estrela entra entre os dois, desenhada como vetor
    (`_draw_star`). Sem nota (rating == 0): ("", "45 mil vendidos no último mês
    · Shopee") — nada de estrela, o texto começa em vendas (ou na loja).

    O texto de vendas diz a JANELA que o número mede (fase 5H): a Shopee conta
    30 dias, o ML publica o contador vitalício ("+250 mil vendidos"). A linha
    não quebra — ela é centrada e única —, e a folga medida está em
    `tests/test_creative.py`."""
    resto = " · ".join(p for p in (pricing.format_sales(offer.sales, offer.sales_e_faixa,
                                                        offer.sales_window_days),
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
    price = _price_pill_dims(draw, offer, 36, 96, 20, 30, 24, pill_left, STORY_TITLE_WIDTH,
                             STORY_NOTA_SIZE)
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
    price = _price_pill_dims(draw, offer, 32, 84, 18, 28, 22, pill_left, FEED_TITLE_WIDTH,
                             FEED_NOTA_SIZE)
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

# Fase 5F: quando o story leva figurinha de link (instagrapi), o rodapé vira o
# PRÓPRIO botão — dourado, preenchido, dizendo o que fazer.
#
# Por quê: medido nos stories reais de 2026-08-27, a figurinha do instagrapi
# **não é desenhada**. Ela entra como área tocável (o `story_info` devolve o
# `story_link`, com `type: "gif"` e sem imagem) e nada aparece na tela. Ou
# seja: o toque funciona, mas quem precisa mostrar ONDE tocar é a nossa arte.
# Por isso a área tocável é posicionada EM CIMA desta pill (ver
# `story_cta_tap_area`), e não numa faixa vazia com uma seta apontando para
# lugar nenhum — que foi a primeira tentativa, e o dono não achou o link.
CTA_FIGURINHA = "LINK PARA O PRODUTO"


def _story_footer_geometry(draw: ImageDraw.ImageDraw, width: int, height: int,
                            handle: str | None, offer: Offer,
                            cta_figurinha: bool = False) -> dict:
    # No modo figurinha a pill É o botão: ela recebe a área tocável por cima
    # (ver `story_cta_tap_area`) e é a única coisa na tela que diz onde tocar,
    # já que a figurinha do Instagram não é desenhada. Por isso ela é maior —
    # fonte e respiro — além de dourada e preenchida.
    if cta_figurinha:
        pad_y, pad_x = 40, 64
        font = _font("sans", 54, 800)
    else:
        pad_y, pad_x = 26, 40
        font = _font("sans", 42, 700)
    label = "MERCADO LIVRE" if offer.source == "meli" else "SHOPEE"
    text = CTA_FIGURINHA if cta_figurinha else f"→  LINK NA {label}"
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
        "cta_figurinha": cta_figurinha,
    }


def story_cta_tap_area(offer: Offer, handle: str | None = None,
                       folga: float = 1.6) -> dict:
    """Onde a figurinha de link (invisível) deve ficar: EM CIMA da pill do
    rodapé, com folga para o dedo.

    Calculado a partir da geometria real do rodapé, não de números fixos: se a
    pill mudar de tamanho — e ela muda, o texto varia com a loja —, a área
    tocável acompanha. Devolve frações da tela, que é o que o instagrapi pede.
    """
    width, height = STORY_SIZE
    draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    geo = _story_footer_geometry(draw, width, height, handle, offer, True)
    x0, y0, x1, y1 = geo["cta_box"]
    largura = min(0.92, ((x1 - x0) / width) * folga)
    altura = min(0.14, ((y1 - y0) / height) * folga)
    return {"x": ((x0 + x1) / 2) / width, "y": ((y0 + y1) / 2) / height,
            "width": largura, "height": altura}


def _draw_story_footer(draw: ImageDraw.ImageDraw, width: int, handle: str | None, geo: dict) -> None:
    x0, y0, x1, y1 = geo["cta_box"]
    # No modo figurinha a pill é DOURADA e preenchida: ela não é decoração, é a
    # única instrução do story — e o teste real mostrou que a versão discreta
    # (contorno sobre navy) some ao lado da figurinha azul do Instagram.
    figurinha = geo.get("cta_figurinha", False)
    # METADE DA ALTURA, nunca 999: raio maior que o lado curto faz o Pillow
    # devolver uma ELIPSE. O comentário do fecho do carrossel dizia que "nas
    # pills largas do story isso nunca apareceu" — apareceu: o botão dourado,
    # que é a ÚNICA instrução do story no modo figurinha e a área que o dono
    # pediu para tocar, saía oval (medido no preview de 2026-08-28: 63 px de
    # largura preenchida no topo, contra 417 com o raio certo).
    raio = (y1 - y0) / 2
    if figurinha:
        draw.rounded_rectangle([x0, y0, x1, y1], radius=raio, fill=GOLD)
        cor_texto = INK
    else:
        draw.rounded_rectangle([x0, y0, x1, y1], radius=raio, outline=PILL_BORDER,
                               width=2, fill=SURFACE)
        cor_texto = TEXT
    bbox = geo["cta_bbox"]
    draw.text((x0 + geo["cta_pad_x"] - bbox[0], y0 + geo["cta_pad_y"] - bbox[1]),
               geo["cta_text"], font=geo["cta_font"], fill=cor_texto)
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
                handle: str | None, cta_figurinha: bool = False) -> dict:
    width, height = STORY_SIZE
    footer = _story_footer_geometry(draw, width, height, handle, offer, cta_figurinha)
    pill_left = _pill_left(offer, verdict)
    title, price, meta, selo = _story_body_options(
        draw, offer, verdict, footer["cta_box"][1] - 36, pill_left)
    return {"footer": footer, "title": title, "price": price, "meta": meta, "selo": selo,
            "pill_left": pill_left, "badge_pct": _badge_pct(offer, verdict)}


def _feed_plan(draw: ImageDraw.ImageDraw, offer: Offer, verdict: Verdict,
               handle: str | None) -> dict:
    width, height = FEED_SIZE
    footer = _feed_footer_geometry(width, height, FEED_PAD)
    pill_left = _pill_left(offer, verdict)
    title, price, meta, selo = _feed_body_options(
        draw, offer, verdict, footer["divider_y"] - 36, pill_left)
    return {"footer": footer, "title": title, "price": price, "meta": meta, "selo": selo,
            "pill_left": pill_left, "badge_pct": _badge_pct(offer, verdict)}


def _resumo(plan: dict) -> dict:
    return {
        "selo": plan["selo"]["text"] if plan["selo"] is not None else "",
        "badge_pct": plan["badge_pct"],
        "riscado": plan["pill_left"][0],
        "sem_cupom": plan["price"]["nota_text"],
        "title_lines": list(plan["title"]["lines"]),
        "meta": plan["meta"] is not None,
    }


def story_plan(offer: Offer, verdict: Verdict, handle: str | None = None) -> dict:
    """O que `render_story` vai desenhar para este veredito — sem baixar a
    imagem nem pintar: `selo` (rótulo, ou "" quando não há), `badge_pct`,
    `riscado` (a referência riscada na pill, ou ""), `sem_cupom` (o rótulo à
    direita do preço, ou ""), `title_lines`, `meta`.
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
                   handle: str | None, brand_name: str,
                   cta_figurinha: bool = False) -> bytes:
    width, height = STORY_SIZE
    product = _open_product_image(_get_image_bytes(offer, client))

    canvas = _glow_background(width, height, 540, 154, 594, 528)
    draw = ImageDraw.Draw(canvas)
    plan = _story_plan(draw, offer, verdict, handle, cta_figurinha)

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
    cta_figurinha: bool = False,
) -> bytes:
    """`cta_figurinha`: o story vai levar a figurinha de link do Instagram
    (canal `instagram_story_link`). O rodapé deixa de fingir um botão e vira
    uma seta DOURADA apontando para ela."""
    del copy  # reservado para fases futuras; não usado no template atual
    return _render_story(offer, verdict, client, handle, brand_name, cta_figurinha)


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


# =============================================================================
# Fase 5D — GRÁFICO DE HISTÓRICO DE PREÇO
#
# Esta é a única peça do projeto que NÃO reposta nada: ela é desenhada inteira
# a partir do nosso `price_log`. Desde 2026-04-30 o Instagram estendeu a regra
# de originalidade a fotos e carrosséis — perfil que posta majoritariamente
# conteúdo que não criou perde elegibilidade para recomendação a
# não-seguidores, e "foto do vendedor com etiqueta de preço por cima" é
# exatamente o formato punido. O gráfico é o que transforma repostagem em
# conteúdo original: sem ele a automação torna a conta invisível
# (`docs/feed.md` e a pesquisa que ele cita).
#
# Como todo o resto do design system, ele NÃO recalcula veredito: modo e selo
# vêm de `pricing.verdict` e entram por cima do desenho.
# =============================================================================

GRAFICO_SIZE = (1080, 1080)
GRAFICO_PAD = 64
GRAFICO_TITLE_SIZE = 46
GRAFICO_KICKER_Y = 156
# Espaço reservado abaixo do gráfico: rótulos do eixo x + divisória + rodapé.
GRAFICO_RODAPE_ALTURA = 250

# A régua do pico inflado mora em `pricing` (é regra de preço, não de desenho)
# e é a MESMA que o `flagrante` usa. Reexportada aqui só para quem já lê este
# módulo como o design system.
PICO_FATOR = pricing.PICO_FATOR
PICO_MAX_DIAS = pricing.PICO_MAX_DIAS

ROTULO_MEDIANA = "preço de sempre"
ROTULO_P25 = "promoção de verdade"

# Folga vertical da escala, em PIXELS (não em fração do valor): o círculo do
# pico e o rótulo "1 dia" ocupam sempre os mesmos ~80 px, independentemente de
# a amplitude da série ser de R$ 3 ou de R$ 300. Em fração do valor, uma série
# com um pico de 2,6× reservava R$ 11 de respiro e empurrava o resto da linha
# para o rodapé.
GRAFICO_FOLGA_TOPO_PX = 84
GRAFICO_FOLGA_TOPO_LIMPA_PX = 34    # série sem pico: nada a circular lá em cima
GRAFICO_FOLGA_BASE_PX = 44


def _serie_normalizada(historico: list[tuple[date, int]]) -> list[tuple[date, int]]:
    """A série pronta para desenhar: um ponto por DIA, do mais antigo ao mais
    novo, sem preço não-positivo.

    Dia repetido fica com o MENOR preço — a mesma regra conservadora de
    `StateDB.record_price`. A ordem de entrada não importa: quem chama monta a
    lista, e o desenho não pode depender disso."""
    por_dia: dict[date, int] = {}
    for dia, cents in historico:
        valor = int(cents)
        if valor <= 0:
            continue
        anterior = por_dia.get(dia)
        por_dia[dia] = valor if anterior is None else min(anterior, valor)
    return sorted(por_dia.items())


def _picos_inflados(serie: list[tuple[date, int]], mediana: int) -> list[dict]:
    """Os trechos acima de `mediana × PICO_FATOR` que duraram <= PICO_MAX_DIAS.

    Um trecho é uma sequência de pontos consecutivos da série acima do limiar,
    e a duração é medida em DIAS DE CALENDÁRIO (não em número de pontos): dois
    pontos separados por uma semana no price_log não são "dois dias".
    Sem mediana (`ref` desconhecida) não há limiar e não há pico — a acusação
    só existe contra a nossa referência."""
    if mediana <= 0:
        return []
    limiar = mediana * PICO_FATOR
    picos: list[dict] = []
    i = 0
    while i < len(serie):
        if serie[i][1] <= limiar:
            i += 1
            continue
        j = i
        while j + 1 < len(serie) and serie[j + 1][1] > limiar:
            j += 1
        dias = (serie[j][0] - serie[i][0]).days + 1
        if dias <= PICO_MAX_DIAS:
            topo = max(range(i, j + 1), key=lambda k: serie[k][1])
            picos.append({
                "inicio": i, "fim": j, "topo": topo, "dias": dias,
                "cents": serie[topo][1],
                "rotulo": f"{dias} dia" if dias == 1 else f"{dias} dias",
            })
        i = j + 1
    return picos


def _grafico_chips(draw: ImageDraw.ImageDraw, verdict: Verdict) -> list[dict]:
    """O VEREDITO por cima do gráfico, como chips: a porcentagem verificada
    (só no modo A) e o selo de menor preço (só quando o veredito o traz).
    Nada é recalculado aqui — `pricing.verdict` já decidiu."""
    chips: list[dict] = []
    if verdict.mode == "A" and verdict.discount_pct > 0:
        texto = f"-{verdict.discount_pct}% VERIFICADO"
        font = _font("sans", 32, 800)
        bbox = draw.textbbox((0, 0), texto, font=font)
        chips.append({"tipo": "badge", "text": texto, "font": font, "bbox": bbox,
                      "width": (bbox[2] - bbox[0]) + 2 * 26,
                      "height": (bbox[3] - bbox[1]) + 2 * 16})
    if verdict.seal:
        dims = _selo_dims(draw, verdict, 24, 16, 24, 14)
        dims["tipo"] = "selo"
        chips.append(dims)
    return chips


def _grafico_plan(draw: ImageDraw.ImageDraw, offer: Offer,
                  historico: list[tuple[date, int]], verdict: Verdict,
                  largura: int, altura: int, handle: str | None,
                  brand_name: str) -> dict:
    serie = _serie_normalizada(historico)
    if len(serie) < 2:
        raise SourceError(
            f"histórico insuficiente para o gráfico: {len(serie)} ponto(s) com "
            "preço válido — são necessários pelo menos 2")

    janela_dias = (serie[-1][0] - serie[0][0]).days + 1
    kicker = f"HISTÓRICO DE PREÇO · ÚLTIMOS {janela_dias} DIAS"
    title = _title_dims(draw, offer, GRAFICO_TITLE_SIZE, largura - 2 * GRAFICO_PAD,
                        TITLE_MAX_LINES, 700)
    title_top = GRAFICO_KICKER_Y + 46
    abaixo = title_top + title["height"]

    chips = _grafico_chips(draw, verdict)
    chips_top = abaixo + 26
    if chips:
        abaixo = chips_top + max(c["height"] for c in chips)

    x0, x1 = GRAFICO_PAD, largura - GRAFICO_PAD
    y0, y1 = abaixo + 48, altura - GRAFICO_RODAPE_ALTURA

    ref = max(0, offer.price_ref_cents)
    p25 = max(0, offer.price_p25_cents)
    picos = _picos_inflados(serie, ref)
    valores = [c for _, c in serie] + [v for v in (ref, p25) if v > 0]
    vmin, vmax = min(valores), max(valores)
    if vmax <= vmin:
        # Série constante: sem amplitude não há escala. Abre uma janela de 20%
        # ao redor do valor único, em vez de dividir por zero.
        folga_plana = max(1, round(vmax * 0.1))
        vmin, vmax = vmax - folga_plana, vmax + folga_plana
    # O respiro do topo existe para o círculo do pico e o rótulo "1 dia". Sem
    # pico ele é só ar entre o veredito e a linha.
    topo = y0 + (GRAFICO_FOLGA_TOPO_PX if picos else GRAFICO_FOLGA_TOPO_LIMPA_PX)
    base = y1 - GRAFICO_FOLGA_BASE_PX

    def y_de(cents: float) -> float:
        return base - (cents - vmin) / (vmax - vmin) * (base - topo)

    d0, dn = serie[0][0], serie[-1][0]
    extensao = (dn - d0).days

    def x_de(i: int, dia: date) -> float:
        if extensao <= 0:                      # tudo no mesmo dia: espaça por índice
            return x0 + (x1 - x0) * (i / (len(serie) - 1))
        return x0 + (x1 - x0) * ((dia - d0).days / extensao)

    pontos = [(x_de(i, dia), y_de(cents)) for i, (dia, cents) in enumerate(serie)]
    for pico in picos:
        pico["x"], pico["y"] = pontos[pico["topo"]]

    hoje_cents = serie[-1][1]
    hoje_texto = format_brl(hoje_cents)
    hoje_font = _font("sans", 54, 800)
    hoje_bbox = draw.textbbox((0, 0), hoje_texto, font=hoje_font)
    pill_w = (hoje_bbox[2] - hoje_bbox[0]) + 2 * 28
    pill_h = (hoje_bbox[3] - hoje_bbox[1]) + 2 * 20
    hx, hy = pontos[-1]
    # A pill fica À ESQUERDA do ponto e na ALTURA dele: o ponto de hoje é
    # sempre o último da série, colado na borda direita, e uma pill acima ou
    # abaixo dele cobria o trecho final da linha (medido nos previews).
    pill_x1 = max(x0 + pill_w, hx - 26)
    pill_y0 = min(max(y0, hy - pill_h / 2), y1 - pill_h)
    pill = (pill_x1 - pill_w, pill_y0, pill_x1, pill_y0 + pill_h)

    return {
        "serie": serie, "pontos_xy": pontos, "pontos": len(serie),
        "plot": (x0, y0, x1, y1),
        "y_mediana": y_de(ref) if ref > 0 else None,
        "y_p25": y_de(p25) if p25 > 0 else None,
        "faixa_p25": (x0, y_de(p25), x1, y1) if p25 > 0 else None,
        "picos": picos,
        "hoje_cents": hoje_cents, "x_hoje": hx, "y_hoje": hy,
        "pill_hoje": pill, "hoje_font": hoje_font, "hoje_bbox": hoje_bbox,
        "kicker": kicker, "title": title, "title_top": title_top,
        "chips": chips, "chips_top": chips_top,
        "janela_dias": janela_dias,
        "eixo_x": (d0.strftime("%d/%m"), "hoje"),
        "selo": selo_label(verdict), "badge_pct": verdict.discount_pct,
        "rotulos": {
            "mediana": format_brl(ref) if ref > 0 else "",
            "sempre": ROTULO_MEDIANA if ref > 0 else "",
            # Um rótulo só para a linha da mediana: o valor E o nome. Em dois
            # (valor à esquerda, nome à direita) o da direita caía atrás da
            # pill do preço de hoje — visto no preview de 2026-08-27.
            "mediana_linha": f"{format_brl(ref)} · {ROTULO_MEDIANA}" if ref > 0 else "",
            "p25": ROTULO_P25 if p25 > 0 else "",
            "hoje": hoje_texto,
            "assinatura": ASSINATURA,
            "handle": handle or DEFAULT_HANDLE,
            "picos": [p["rotulo"] for p in picos],
        },
        "divisoria_y": altura - 128, "rodape_y": altura - 94,
        "brand_name": brand_name,
    }


def _resumo_grafico(plan: dict) -> dict:
    return {
        "pontos": plan["pontos"], "plot": plan["plot"],
        "y_mediana": plan["y_mediana"], "y_p25": plan["y_p25"],
        "faixa_p25": plan["faixa_p25"],
        "picos": [{k: p[k] for k in ("dias", "rotulo", "cents", "x", "y")}
                  for p in plan["picos"]],
        "hoje_cents": plan["hoje_cents"], "x_hoje": plan["x_hoje"],
        "y_hoje": plan["y_hoje"], "janela_dias": plan["janela_dias"],
        "eixo_x": plan["eixo_x"], "selo": plan["selo"],
        "badge_pct": plan["badge_pct"], "rotulos": plan["rotulos"],
    }


def grafico_plan(offer: Offer, historico: list[tuple[date, int]], verdict: Verdict,
                 largura: int = GRAFICO_SIZE[0], altura: int = GRAFICO_SIZE[1],
                 handle: str | None = None) -> dict:
    """O que `render_grafico_preco` vai desenhar — sem pintar nada.

    Mesmo papel de `story_plan`/`feed_plan`: os testes afirmam sobre ISTO, não
    sobre pixels. Traz o nº de pontos, a caixa do gráfico, o y da mediana e o
    do p25 (com a faixa "promoção de verdade"), os picos detectados (duração,
    valor e onde), o preço de hoje e todos os rótulos — inclusive o selo e a
    porcentagem, que vêm do `Verdict` e não são recalculados aqui.

    Levanta `SourceError` com menos de 2 pontos válidos: quem chama decide o
    que fazer com um produto sem histórico."""
    draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    return _resumo_grafico(_grafico_plan(draw, offer, historico, verdict,
                                         largura, altura, handle, DEFAULT_BRAND_NAME))


def _draw_dashed_line(draw: ImageDraw.ImageDraw, x_ini: float, y: float, x_fim: float,
                      fill: tuple[int, int, int], width: int = 3,
                      traco: int = 18, vao: int = 14) -> None:
    x = x_ini
    while x < x_fim:
        draw.line([(x, y), (min(x + traco, x_fim), y)], fill=fill, width=width)
        x += traco + vao


def _draw_centered(draw: ImageDraw.ImageDraw, largura: int, y: float, text: str,
                   font: ImageFont.FreeTypeFont, fill: tuple[int, int, int]) -> None:
    bbox = draw.textbbox((0, 0), text, font=font)
    x = (largura - (bbox[2] - bbox[0])) / 2
    draw.text((x - bbox[0], y - bbox[1]), text, font=font, fill=fill)


def _draw_grafico_chips(draw: ImageDraw.ImageDraw, largura: int, plan: dict) -> None:
    chips, gap = plan["chips"], 20
    if not chips:
        return
    total = sum(c["width"] for c in chips) + gap * (len(chips) - 1)
    x = (largura - total) / 2
    for chip in chips:
        y = plan["chips_top"] + (max(c["height"] for c in chips) - chip["height"]) / 2
        if chip["tipo"] == "selo":
            _draw_selo(draw, x, y, chip, radius=14)
        else:
            draw.rounded_rectangle([x, y, x + chip["width"], y + chip["height"]],
                                   radius=14, fill=GOLD)
            bbox = chip["bbox"]
            draw.text((x + 26 - bbox[0], y + 16 - bbox[1]), chip["text"],
                      font=chip["font"], fill=INK)
        x += chip["width"] + gap


def _draw_chip_texto(draw: ImageDraw.ImageDraw, x: float, y: float, text: str,
                     font: ImageFont.FreeTypeFont, fill: tuple[int, int, int],
                     fundo: tuple[int, int, int] = NAVY) -> None:
    """Rótulo com placa NAVY por baixo. Um rótulo de gráfico cai em cima da
    linha de preço em algum momento — sem a placa, a mediana ficava ilegível
    exatamente na série que mais interessa (a que encosta na mediana)."""
    asc, desc = font.getmetrics()
    largura = draw.textlength(text, font=font)
    # Arredondada e com contorno: uma placa retangular seca lia como um BURACO
    # na linha de preço; com a borda, lê como rótulo (preview de 2026-08-27).
    draw.rounded_rectangle([x - 10, y - 5, x + largura + 10, y + asc + desc + 5],
                           radius=9, fill=fundo, outline=BORDER, width=1)
    draw.text((x, y), text, font=font, fill=fill)


def _draw_grafico_reguas(draw: ImageDraw.ImageDraw, plan: dict) -> None:
    """As LINHAS da régua da casa: a tracejada da mediana e a borda de cima da
    faixa do p25. Vão ANTES da linha de preço; os rótulos vão depois
    (`_draw_grafico_rotulos_regua`), senão a linha passa por cima deles."""
    x0, _, x1, _ = plan["plot"]
    if plan["faixa_p25"] is not None:
        draw.line([(x0, plan["faixa_p25"][1]), (x1, plan["faixa_p25"][1])],
                  fill=SELO_BORDER, width=2)
    if plan["y_mediana"] is not None:
        _draw_dashed_line(draw, x0, plan["y_mediana"], x1, MUTED, width=3)


def _draw_grafico_rotulos_regua(draw: ImageDraw.ImageDraw, plan: dict) -> None:
    """"preço de sempre" (com o valor) e "promoção de verdade" — depois da
    linha de preço, cada um sobre a sua placa."""
    x0, _, _, y1 = plan["plot"]
    font = _font("mono", 26, 500)
    linha_h = sum(font.getmetrics())
    if plan["faixa_p25"] is not None:
        yp = plan["faixa_p25"][1]
        # Colado no FUNDO da faixa: é lá que ela é mais larga, e é o lugar que
        # nunca disputa espaço com a linha da mediana logo acima.
        ty = max(y1 - linha_h - 12, yp + 8)
        _draw_chip_texto(draw, x0 + 10, ty, plan["rotulos"]["p25"], font, SELO_TEXT,
                         fundo=SELO_BG)
    if plan["y_mediana"] is not None:
        _draw_chip_texto(draw, x0 + 10, plan["y_mediana"] - 12 - linha_h,
                         plan["rotulos"]["mediana_linha"], font, MUTED)


def _draw_grafico_picos(draw: ImageDraw.ImageDraw, plan: dict) -> None:
    x0, y0, x1, _ = plan["plot"]
    font = _font("mono", 26, 600)
    linha_h = sum(font.getmetrics())
    raio = 26
    for pico in plan["picos"]:
        px, py = pico["x"], pico["y"]
        draw.ellipse([px - raio, py - raio, px + raio, py + raio], outline=RED, width=5)
        largura_rotulo = draw.textlength(pico["rotulo"], font=font)
        tx = min(max(x0, px - largura_rotulo / 2), x1 - largura_rotulo)
        ty = py - raio - 12 - linha_h
        if ty < y0:                       # sem espaço acima: o rótulo desce
            ty = py + raio + 10
        draw.text((tx, ty), pico["rotulo"], font=font, fill=RED)


def _draw_grafico_hoje(draw: ImageDraw.ImageDraw, plan: dict) -> None:
    hx, hy = plan["x_hoje"], plan["y_hoje"]
    px0, py0, px1, py1 = plan["pill_hoje"]
    draw.rounded_rectangle([px0, py0, px1, py1], radius=16, fill=GOLD)
    bbox = plan["hoje_bbox"]
    draw.text((px0 + 28 - bbox[0], py0 + 20 - bbox[1]), plan["rotulos"]["hoje"],
              font=plan["hoje_font"], fill=INK)
    draw.ellipse([hx - 17, hy - 17, hx + 17, hy + 17], fill=NAVY, outline=GOLD, width=5)
    draw.ellipse([hx - 7, hy - 7, hx + 7, hy + 7], fill=GOLD)


def _draw_grafico_rodape(draw: ImageDraw.ImageDraw, largura: int, plan: dict) -> None:
    dy = plan["divisoria_y"]
    draw.line([(GRAFICO_PAD, dy), (largura - GRAFICO_PAD, dy)], fill=BORDER, width=1)
    assinatura_font = _font("sans", 38, 700)
    a_asc, a_desc = assinatura_font.getmetrics()
    handle_font = _font("mono", 28, 500)
    h_asc, h_desc = handle_font.getmetrics()
    base = plan["rodape_y"] + max(a_asc, h_asc)
    texto = plan["rotulos"]["assinatura"]
    bbox = draw.textbbox((0, 0), texto, font=assinatura_font)
    draw.text((GRAFICO_PAD - bbox[0], base - a_asc - bbox[1]), texto,
              font=assinatura_font, fill=TEXT)
    handle = plan["rotulos"]["handle"].upper()
    hbbox = draw.textbbox((0, 0), handle, font=handle_font)
    hx = largura - GRAFICO_PAD - (hbbox[2] - hbbox[0])
    draw.text((hx - hbbox[0], base - h_asc - hbbox[1]), handle,
              font=handle_font, fill=GOLD)


def render_grafico_preco(offer: Offer, historico: list[tuple[date, int]],
                         verdict: Verdict, largura: int = GRAFICO_SIZE[0],
                         altura: int = GRAFICO_SIZE[1], handle: str | None = None,
                         brand_name: str = DEFAULT_BRAND_NAME) -> bytes:
    """A série de preços de um produto, com o veredito por cima (PNG).

    Linha dourada sobre navy, sem grade: só a linha tracejada da mediana
    (`offer.price_ref_cents`, "preço de sempre"), a faixa abaixo do p25
    (`offer.price_p25_cents`, "promoção de verdade") e o preço de hoje
    destacado. Cada pico inflado — acima de `mediana × 1,5` por até 2 dias —
    ganha o círculo vermelho e a duração ("1 dia"), que é a prova de que o
    "de" do vendedor não se sustenta.

    NÃO baixa a foto do produto: é justamente por ser 100% nosso que este
    desenho conta como conteúdo original para o Instagram. Modo e selo vêm do
    `Verdict` já decidido; nada é recalculado aqui.

    Série vazia ou com menos de 2 pontos válidos -> `SourceError`."""
    medida = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    plan = _grafico_plan(medida, offer, historico, verdict, largura, altura,
                         handle, brand_name)
    x0, y0, x1, y1 = plan["plot"]

    canvas = _glow_background(largura, altura, largura / 2, 120,
                              largura * 0.58, 340).convert("RGBA")
    # A ÚNICA camada translúcida do desenho é a faixa do p25 — e é de
    # propósito: enquanto havia também um preenchimento dourado sob a linha,
    # os dois se somavam e a faixa virava uma barra marrom sem significado
    # (preview de 2026-08-27). Com uma sombra só, ela lê como "a zona boa".
    overlay = Image.new("RGBA", (largura, altura), (0, 0, 0, 0))
    if plan["faixa_p25"] is not None:
        ImageDraw.Draw(overlay).rectangle(
            [x0, plan["faixa_p25"][1], x1, y1], fill=(*GLOW_COLOR, 46))
    canvas = Image.alpha_composite(canvas, overlay).convert("RGB")
    pontos = plan["pontos_xy"]

    draw = ImageDraw.Draw(canvas)
    _draw_header_feed(draw, canvas, GRAFICO_PAD, 58, 62, brand_name, None)
    _draw_centered(draw, largura, GRAFICO_KICKER_Y, plan["kicker"],
                   _font("mono", 26, 500), MUTED)
    _draw_title(draw, largura, plan["title_top"], plan["title"])
    _draw_grafico_chips(draw, largura, plan)

    draw.line([(x0, y1), (x1, y1)], fill=BORDER, width=1)
    _draw_grafico_reguas(draw, plan)
    draw.line(pontos, fill=GOLD, width=5, joint="curve")
    _draw_grafico_rotulos_regua(draw, plan)
    _draw_grafico_picos(draw, plan)
    _draw_grafico_hoje(draw, plan)

    eixo_font = _font("mono", 24, 400)
    esquerda, direita = plan["eixo_x"]
    draw.text((x0, y1 + 18), esquerda, font=eixo_font, fill=MUTED)
    draw.text((x1 - draw.textlength(direita, font=eixo_font), y1 + 18), direita,
              font=eixo_font, fill=MUTED)
    _draw_grafico_rodape(draw, largura, plan)

    buffer = io.BytesIO()
    canvas.save(buffer, "PNG")
    return buffer.getvalue()


# =============================================================================
# Fase 5D — CARROSSEL
#
# O carrossel é o motor de RETENÇÃO: maior save rate do Instagram (0,05%, 9x a
# imagem única) e o único formato que pode reaparecer no feed de quem não
# engajou. A imagem única — foto do produto com preço por cima — perdeu 45,98%
# de engajamento no ano e saiu da grade. Ver `docs/feed.md`.
#
# A capa vende o CONCEITO ("3 ofertas, 1 é real"), não um preço: é ela que dá
# motivo para deslizar. Os slides de oferta são a arte de feed que já existe,
# com um contador; o fecho é a frase-assinatura. Nada de pedido de curtida,
# comentário ou compartilhamento em lugar nenhum — a Meta rebaixa.
# =============================================================================

CARROSSEL_SIZE = FEED_SIZE
# A Meta aceita 10; a pesquisa só sustenta "6 a 8" fracamente (nenhum estudo
# com método), e menos slide é mais barato de gerar e de revisar. Capa e fecho
# ocupam dois: sobram seis ofertas.
CARROSSEL_MAX_SLIDES = 8
CTA_CARROSSEL = "LINK NA BIO"

CAPA_TITLE_SIZE = 92
CAPA_SUB_SIZE = 42
FECHO_TITLE_SIZE = 76


# Abaixo disto o carrossel não sai. Um "termômetro da semana" com uma oferta só
# não é o post que a capa promete ("6 ofertas, 4 passaram"), e a Meta pediria
# uma legenda que fala de seis para um álbum de três slides.
CARROSSEL_MIN_OFERTAS = 2


def _slides_de_oferta(posts: list[Post]) -> list[Post]:
    if not posts:
        raise SourceError("carrossel sem oferta nenhuma: capa e fecho não são post")
    return list(posts[:CARROSSEL_MAX_SLIDES - 2])


def _avisa(avisos: list[str] | None, texto: str) -> None:
    if avisos is not None:
        avisos.append(texto)


def carrossel_fotos(posts: list[Post], client: httpx.Client | None = None,
                    avisos: list[str] | None = None) -> list[tuple[Post, Image.Image]]:
    """As ofertas que REALMENTE entram no carrossel, com a foto já aberta.

    O carrossel era tudo-ou-nada, como o `render_feed`: uma foto que não baixa
    derrubava o post inteiro. Só que com SEIS fotos a chance de perder é seis
    vezes a de um post único, e o que se perde é o post com seis ofertas — não
    um slide. Produto cuja foto falha é PULADO, com aviso, e o carrossel sai
    com os que sobraram.

    O piso é `CARROSSEL_MIN_OFERTAS`, e ele vale contra a DEGRADAÇÃO: pedir
    seis e ficar com uma vira `SourceError` (com todos os motivos no texto),
    porque aí a peça não é mais a que a capa promete. Pedir uma e receber uma é
    decisão de quem chama, e o desenho não a desfaz.

    É uma etapa SEPARADA de `render_carrossel` porque quem sobreviveu decide o
    texto da capa ("6 OFERTAS. 4 PASSARAM.") e a legenda do álbum: gerar esses
    dois antes de saber quem entrou faria a peça anunciar um produto que não
    está lá. Quem chama resolve as fotos, monta os textos com os
    sobreviventes e só então desenha."""
    escolhidos = _slides_de_oferta(posts)
    vivos: list[tuple[Post, Image.Image]] = []
    perdidos: list[str] = []
    for post in escolhidos:
        try:
            vivos.append((post, _open_product_image(_get_image_bytes(post.offer, client))))
        except SourceError as exc:
            perdidos.append(f"{post.offer.item_id}: {exc}")
            _avisa(avisos, f"⚠️ carrossel: {post.offer.item_id} ficou de fora — {exc}")
    if perdidos and len(vivos) < CARROSSEL_MIN_OFERTAS:
        raise SourceError(
            f"carrossel com {len(vivos)} oferta(s) depois de {len(perdidos)} foto(s) "
            f"que não baixaram (mínimo {CARROSSEL_MIN_OFERTAS}) — " + "; ".join(perdidos))
    return vivos


# Capa e fecho centram o BLOCO inteiro (mascote + textos) nesta faixa da tela,
# em vez de ancorar cada peça num y fixo: com um título de 1, 2 ou 3 linhas os
# y fixos deixavam 300 px de vazio embaixo (preview de 2026-08-27).
CARROSSEL_MIOLO = (120, 1200)


def _centro_do_bloco(alturas: list[float], espacos: list[float]) -> list[float]:
    """Os topos de cada peça de um bloco centrado verticalmente em
    `CARROSSEL_MIOLO`. `espacos[i]` é o respiro DEPOIS da peça i."""
    topo_faixa, base_faixa = CARROSSEL_MIOLO
    total = sum(alturas) + sum(espacos[:len(alturas) - 1])
    y = topo_faixa + ((base_faixa - topo_faixa) - total) / 2
    topos = []
    for i, h in enumerate(alturas):
        topos.append(y)
        y += h + (espacos[i] if i < len(espacos) else 0)
    return topos


def _capa_plan(draw: ImageDraw.ImageDraw, titulo: str, subtitulo: str,
               handle: str | None) -> dict:
    largura, altura = CARROSSEL_SIZE
    disponivel = largura - 2 * FEED_PAD
    titulo_dims = _texto_dims(draw, titulo, CAPA_TITLE_SIZE, disponivel, 3, 800, 1.06)
    sub_dims = _texto_dims(draw, subtitulo, CAPA_SUB_SIZE, disponivel, 2, 500, 1.2)
    mascote_d = 280
    mascote_top, titulo_top, sub_top = _centro_do_bloco(
        [mascote_d, titulo_dims["height"], sub_dims["height"]], [64, 30])
    return {
        "tipo": "capa", "titulo": titulo, "subtitulo": subtitulo,
        "titulo_linhas": list(titulo_dims["lines"]),
        "subtitulo_linhas": list(sub_dims["lines"]),
        "titulo_dims": titulo_dims, "sub_dims": sub_dims,
        "titulo_top": titulo_top, "sub_top": sub_top,
        "mascote": (largura / 2, mascote_top + mascote_d / 2, mascote_d),
        "handle": handle or DEFAULT_HANDLE, "handle_y": altura - 118,
    }


def _fecho_plan(draw: ImageDraw.ImageDraw, handle: str | None) -> dict:
    largura, _ = CARROSSEL_SIZE
    assinatura = _texto_dims(draw, ASSINATURA, FECHO_TITLE_SIZE,
                             largura - 2 * FEED_PAD, 2, 800, 1.1)
    handle_font = _font("mono", 34, 500)
    cta_font = _font("sans", 46, 800)
    cta_bbox = draw.textbbox((0, 0), CTA_CARROSSEL, font=cta_font)
    cta_h = (cta_bbox[3] - cta_bbox[1]) + 2 * 26
    mascote_d = 240
    mascote_top, assinatura_top, handle_y, cta_top = _centro_do_bloco(
        [mascote_d, assinatura["height"], sum(handle_font.getmetrics()), cta_h],
        [60, 40, 60])
    return {
        "tipo": "fecho", "assinatura": ASSINATURA, "cta": CTA_CARROSSEL,
        "assinatura_dims": assinatura, "assinatura_top": assinatura_top,
        "handle": handle or DEFAULT_HANDLE, "handle_y": handle_y,
        "handle_font": handle_font,
        "mascote": (largura / 2, mascote_top + mascote_d / 2, mascote_d),
        "cta_font": cta_font, "cta_bbox": cta_bbox,
        "cta_size": ((cta_bbox[2] - cta_bbox[0]) + 2 * 44, cta_h),
        "cta_top": cta_top,
    }


def _oferta_plan(draw: ImageDraw.ImageDraw, post: Post, indice: int, total: int,
                 handle: str | None) -> dict:
    plan = _feed_plan(draw, post.offer, post.verdict, handle)
    return {
        "tipo": "oferta", "indice": indice, "total": total,
        "item_id": post.offer.item_id, "source": post.offer.source,
        "preco": format_brl(post.offer.published_price_cents),
        **_resumo(plan),
    }


def carrossel_plan(posts: list[Post], titulo: str, subtitulo: str,
                   handle: str | None = None) -> list[dict]:
    """O que cada slide vai ser, na ordem — capa, uma oferta por slide, fecho.

    Mesmo papel de `story_plan`/`feed_plan`/`grafico_plan`: os testes afirmam
    sobre ISTO. Cada slide de oferta traz o que o veredito do post autoriza
    (`selo`, `riscado`, `badge_pct`), lido de `post.verdict` e não recalculado.
    Sem oferta nenhuma levanta `SourceError`: capa e fecho não são post.

    Continua OFFLINE (nenhum teste de composição toca a rede) e continua
    concordando com o desenho, porque quem decide o elenco é `carrossel_fotos`,
    antes dos dois: passe a esta função os mesmos posts que foram desenhados
    (`[post for post, _ in fotos]`) e o plano é a peça."""
    escolhidos = _slides_de_oferta(posts)
    draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    capa = _capa_plan(draw, titulo, subtitulo, handle)
    fecho = _fecho_plan(draw, handle)
    slides = [{k: v for k, v in capa.items() if not k.endswith("_dims")}]
    slides += [_oferta_plan(draw, post, i, len(escolhidos), handle)
               for i, post in enumerate(escolhidos, start=1)]
    slides.append({k: v for k, v in fecho.items()
                   if k not in ("assinatura_dims", "cta_font", "cta_bbox",
                                "handle_font")})
    return slides


def _draw_mascote_em_disco(canvas: Image.Image, draw: ImageDraw.ImageDraw,
                           cx: float, cy: float, d: float) -> None:
    draw.ellipse([cx - d / 2, cy - d / 2, cx + d / 2, cy + d / 2], fill=GOLD)
    draw_mascot(canvas, cx, cy, d * 0.98, ink=NAVY, skin=CREAM, cap=NAVY)


def _draw_bloco_centralizado(draw: ImageDraw.ImageDraw, largura: int, top: float,
                             dims: dict, fill: tuple[int, int, int]) -> float:
    y = top
    for line in dims["lines"]:
        bbox = draw.textbbox((0, 0), line, font=dims["font"])
        x = (largura - (bbox[2] - bbox[0])) / 2
        draw.text((x - bbox[0], y - bbox[1]), line, font=dims["font"], fill=fill)
        y += dims["line_h"]
    return y


def _render_capa(titulo: str, subtitulo: str, handle: str | None) -> bytes:
    largura, altura = CARROSSEL_SIZE
    canvas = _glow_background(largura, altura, largura / 2, 300, largura * 0.62, 420)
    draw = ImageDraw.Draw(canvas)
    plan = _capa_plan(draw, titulo, subtitulo, handle)
    _draw_mascote_em_disco(canvas, draw, *plan["mascote"])
    _draw_bloco_centralizado(draw, largura, plan["titulo_top"], plan["titulo_dims"], TEXT)
    _draw_bloco_centralizado(draw, largura, plan["sub_top"], plan["sub_dims"], MUTED)
    _draw_centered(draw, largura, plan["handle_y"], plan["handle"].upper(),
                   _font("mono", 30, 500), GOLD)
    buffer = io.BytesIO()
    canvas.save(buffer, "PNG")
    return buffer.getvalue()


def _render_fecho(handle: str | None) -> bytes:
    largura, altura = CARROSSEL_SIZE
    canvas = _glow_background(largura, altura, largura / 2, 300, largura * 0.62, 420)
    draw = ImageDraw.Draw(canvas)
    plan = _fecho_plan(draw, handle)
    _draw_mascote_em_disco(canvas, draw, *plan["mascote"])
    _draw_bloco_centralizado(draw, largura, plan["assinatura_top"],
                             plan["assinatura_dims"], TEXT)
    _draw_centered(draw, largura, plan["handle_y"], plan["handle"].upper(),
                   plan["handle_font"], MUTED)
    cta_w, cta_h = plan["cta_size"]
    x0, y0 = (largura - cta_w) / 2, plan["cta_top"]
    # `radius` é METADE DA ALTURA, não 999: com um raio maior que o lado curto
    # o Pillow devolve uma elipse, e a pill do fecho saía oval (preview de
    # 2026-08-27). O mesmo defeito estava no rodapé do story e no contador do
    # carrossel, achados em 2026-08-28 — a regra vale para TODA pill.
    draw.rounded_rectangle([x0, y0, x0 + cta_w, y0 + cta_h], radius=cta_h / 2, fill=GOLD)
    bbox = plan["cta_bbox"]
    draw.text((x0 + 44 - bbox[0], y0 + 26 - bbox[1]), CTA_CARROSSEL,
              font=plan["cta_font"], fill=INK)
    buffer = io.BytesIO()
    canvas.save(buffer, "PNG")
    return buffer.getvalue()


def _draw_contador(draw: ImageDraw.ImageDraw, largura: int, indice: int, total: int) -> None:
    texto = f"{indice}/{total}"
    font = _font("mono", 26, 500)
    bbox = draw.textbbox((0, 0), texto, font=font)
    w = (bbox[2] - bbox[0]) + 2 * 20
    h = (bbox[3] - bbox[1]) + 2 * 14
    x0 = largura - FEED_PAD - w
    y0 = 64 + (62 - h) / 2                     # centrado no avatar do cabeçalho
    draw.rounded_rectangle([x0, y0, x0 + w, y0 + h], radius=h / 2,
                           fill=SURFACE, outline=PILL_BORDER, width=2)
    draw.text((x0 + 20 - bbox[0], y0 + 14 - bbox[1]), texto, font=font, fill=MUTED)


def _render_slide_oferta(post: Post, product: Image.Image, indice: int, total: int,
                         handle: str | None, brand_name: str) -> bytes:
    """A arte de feed que já existe, com o contador do carrossel — `_draw_card`,
    `_draw_price_pill` e `selo_label` são os mesmos; nada é reimplementado.

    A foto chega PRONTA (`_ofertas_com_foto`): é lá que se decide quem entra no
    carrossel, e baixá-la de novo aqui seria uma segunda chance de falhar
    depois de o plano já estar fechado."""
    width, height = CARROSSEL_SIZE
    canvas = _glow_background(width, height, 540, 81, 594, 338)
    draw = ImageDraw.Draw(canvas)
    plan = _feed_plan(draw, post.offer, post.verdict, handle)
    _draw_header_feed(draw, canvas, FEED_PAD, 64, 62, brand_name, handle)
    _draw_contador(draw, width, indice, total)
    _draw_card(canvas, draw, product, 64, 158, 952, 600, 26, 20,
               plan["badge_pct"], 42, 12, 20, 26)
    _draw_feed_body(draw, width, post.offer, plan["title"], plan["price"],
                    plan["meta"], plan["selo"])
    _draw_feed_footer(draw, width, FEED_PAD, post.offer, plan["footer"])
    buffer = io.BytesIO()
    canvas.save(buffer, "PNG")
    return buffer.getvalue()


def render_carrossel(fotos: list[tuple[Post, Image.Image]], titulo: str, subtitulo: str,
                     handle: str | None = None,
                     brand_name: str = DEFAULT_BRAND_NAME) -> list[bytes]:
    """Os PNGs do carrossel, na ordem: capa, um slide por oferta, fecho.

    `fotos` é o que `carrossel_fotos` devolveu — o elenco já decidido, com a
    foto de cada produto aberta. Esta função não baixa nada e não descarta
    ninguém: quem chega aqui vira slide, e é por isso que `titulo`/`subtitulo`
    (montados pelo chamador a partir desse mesmo elenco) podem falar de "6
    ofertas" com segurança.

    Capa: `titulo` grande, `subtitulo` e o mascote — nenhum preço, porque ela
    vende o CONCEITO ("3 ofertas, 1 é real"), que é o que faz alguém deslizar.
    Ofertas: a arte de feed com um contador (foto, título, pill de preço e o
    veredito, tudo pelas mesmas funções). Fecho: a frase-assinatura, o handle e
    "link na bio" — nunca um pedido de curtida ou comentário."""
    if not fotos:
        raise SourceError("carrossel sem oferta nenhuma: capa e fecho não são post")
    imagens = [_render_capa(titulo, subtitulo, handle)]
    imagens += [_render_slide_oferta(post, foto, i, len(fotos), handle, brand_name)
                for i, (post, foto) in enumerate(fotos, start=1)]
    imagens.append(_render_fecho(handle))
    return imagens

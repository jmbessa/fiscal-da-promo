"""Mascote do "Fiscal da Promo" (fase 2C): rosto creme, boné e lupa.

Porte do SVG do design (viewBox -15..215) para Pillow — as mesmas coordenadas
usadas no avatar (Instagram/Telegram) desenham o mascote no cabeçalho das
artes de story/feed em `creative.py`, garantindo que os dois fiquem idênticos.
"""

from PIL import Image, ImageDraw

# Defaults usados quando `draw_mascot` é chamado sem cores explícitas (ex.:
# uso avulso do módulo). `creative.py` é a fonte de verdade da paleta e
# sempre passa `ink`/`skin`/`cap` explicitamente.
NAVY = (16, 20, 39)
CREAM = (246, 239, 225)


def _quad(p0: tuple[float, float], p1: tuple[float, float], p2: tuple[float, float],
          n: int = 24) -> list[tuple[float, float]]:
    """Amostra uma curva bezier quadrática (p0→p1→p2) em `n` segmentos."""
    return [
        ((1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t ** 2 * p2[0],
         (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t ** 2 * p2[1])
        for t in (i / n for i in range(n + 1))
    ]


def draw_mascot(
    img: Image.Image,
    cx: float,
    cy: float,
    size: float,
    ink: tuple[int, int, int] = NAVY,
    skin: tuple[int, int, int] = CREAM,
    cap: tuple[int, int, int] | None = None,
) -> None:
    """Desenha o mascote centrado em (cx, cy).

    `size` é o lado do quadrado que o SVG de referência (viewBox de 230
    unidades, -15..215) ocupa.
    """
    cap = cap or ink
    s = size / 230.0
    ox, oy = cx - size / 2 + 15 * s, cy - size / 2 + 15 * s
    X = lambda x: ox + x * s
    Y = lambda y: oy + y * s
    d = ImageDraw.Draw(img)
    d.ellipse([X(36), Y(60), X(148), Y(172)], fill=skin)                     # rosto
    d.pieslice([X(36), Y(48), X(148), Y(160)], 180, 360, fill=cap)           # copa do boné
    d.rounded_rectangle([X(16), Y(96), X(168), Y(117)], radius=10.5 * s, fill=cap)  # aba
    d.ellipse([X(66), Y(124), X(82), Y(140)], fill=ink)                      # olho
    d.line(_quad((X(70), Y(156)), (X(90), Y(172)), (X(112), Y(158))),
           fill=ink, width=max(2, round(10 * s)), joint="curve")             # sorriso
    d.line([(X(152), Y(140)), (X(178), Y(168))], fill=cap, width=max(3, round(18 * s)))  # cabo da lupa
    d.ellipse([X(100), Y(90), X(164), Y(154)], fill=skin, outline=cap, width=max(2, round(13 * s)))  # lente
    d.ellipse([X(120), Y(110), X(144), Y(134)], fill=ink)                    # pupila

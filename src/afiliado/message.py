import html

from afiliado import pricing
from afiliado.models import CopyParts, Offer
from afiliado.watchlist import PriceFloor

DEFAULT_SEAL_TOLERANCE = 1.05


def _selo(offer: Offer, price_floor: PriceFloor | None, seal_tolerance: float) -> str:
    """Selo de menor preço. A watchlist (curada, janela conhecida) tem
    precedência; sem ela, vale a mínima do nosso próprio histórico, com a
    margem de `seal_tolerance` — e sem prometer uma janela que não medimos."""
    if price_floor is not None and offer.price_current_cents <= price_floor.min_price_cents:
        meses = max(1, round(price_floor.window_days / 30))
        return f"🏷️ Menor preço dos últimos {meses} meses (verificado)"
    if (offer.price_floor_cents > 0
            and offer.price_current_cents <= offer.price_floor_cents * seal_tolerance):
        return "🏷️ Menor preço já registrado (verificado)"
    return ""


def build_message(offer: Offer, copy: CopyParts, link: str,
                  price_floor: PriceFloor | None = None,
                  min_real_discount_pct: int = pricing.DEFAULT_MIN_REAL_DISCOUNT_PCT,
                  seal_tolerance: float = DEFAULT_SEAL_TOLERANCE) -> str:
    """Texto do post. O bloco de preço tem dois modos e quem decide é
    `pricing.price_line`: com desconto verificado sai "De/Por" contra a NOSSA
    referência; sem ele sai só o preço, com a prova social logo abaixo. O "de"
    do vendedor (price_original_cents) nunca aparece."""
    linha_preco, prova_social = pricing.price_line(offer, min_real_discount_pct)
    bloco = [linha_preco]
    if prova_social:
        bloco.append(prova_social)
    selo = _selo(offer, price_floor, seal_tolerance)
    if selo:
        bloco.append(selo)
    return (
        f"{html.escape(copy.headline)}\n"
        f"{html.escape(copy.description)}\n"
        f"\n"
        f"{html.escape(offer.title)}\n"
        + "\n".join(bloco) + "\n"
        f"\n"
        f"{html.escape(copy.cta)}\n"
        f"👉 {link}"
    )

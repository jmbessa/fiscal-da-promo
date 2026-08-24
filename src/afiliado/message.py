import html

from afiliado.models import CopyParts, Offer, format_brl
from afiliado.watchlist import PriceFloor


def build_message(offer: Offer, copy: CopyParts, link: str,
                  price_floor: PriceFloor | None = None) -> str:
    selo = ""
    if price_floor is not None and offer.price_current_cents <= price_floor.min_price_cents:
        meses = max(1, round(price_floor.window_days / 30))
        selo = f"\n🏷️ Menor preço dos últimos {meses} meses (verificado)"
    return (
        f"{html.escape(copy.headline)}\n"
        f"{html.escape(copy.description)}\n"
        f"\n"
        f"{html.escape(offer.title)}\n"
        f"De: <s>{format_brl(offer.price_original_cents)}</s> | "
        f"Por: <b>{format_brl(offer.price_current_cents)}</b> "
        f"({offer.discount_pct}% OFF)"
        f"{selo}\n"
        f"\n"
        f"{html.escape(copy.cta)}\n"
        f"👉 {link}"
    )

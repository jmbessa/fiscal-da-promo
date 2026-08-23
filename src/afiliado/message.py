import html

from afiliado.models import CopyParts, Offer, format_brl


def build_message(offer: Offer, copy: CopyParts, link: str) -> str:
    return (
        f"{html.escape(copy.headline)}\n"
        f"{html.escape(copy.description)}\n"
        f"\n"
        f"{html.escape(offer.title)}\n"
        f"De: <s>{format_brl(offer.price_original_cents)}</s> | "
        f"Por: <b>{format_brl(offer.price_current_cents)}</b> "
        f"({offer.discount_pct}% OFF)\n"
        f"\n"
        f"{html.escape(copy.cta)}\n"
        f"👉 {link}"
    )

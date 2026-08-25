from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from afiliado.watchlist import PriceFloor


def format_brl(cents: int) -> str:
    reais, centavos = divmod(cents, 100)
    return f"R$ {reais:,}".replace(",", ".") + f",{centavos:02d}"


@dataclass(frozen=True)
class Offer:
    source: str
    item_id: str
    title: str
    price_original_cents: int
    price_current_cents: int
    commission_pct: float
    image_url: str
    product_url: str
    offer_link: str = ""
    category: str = ""
    sales: int = 0
    rating: float = 0.0        # nota média (0 = desconhecida)

    @property
    def discount_pct(self) -> int:
        if self.price_original_cents <= 0:
            return 0
        return round((1 - self.price_current_cents / self.price_original_cents) * 100)


@dataclass(frozen=True)
class CopyParts:
    headline: str
    description: str
    cta: str


@dataclass
class Post:
    offer: Offer
    copy: CopyParts
    affiliate_link: str
    message_text: str = ""
    price_floor: "PriceFloor | None" = None

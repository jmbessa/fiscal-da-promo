import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from afiliado.models import Offer


@dataclass(frozen=True)
class PriceFloor:
    min_price_cents: int
    window_days: int


@dataclass(frozen=True)
class Watchlist:
    generated_at: date
    valid_days: int
    category_boosts: dict[str, float] = field(default_factory=dict)
    hot_items: dict[str, float] = field(default_factory=dict)      # item_id -> boost
    price_floors: dict[str, PriceFloor] = field(default_factory=dict)

    def days_old(self, today: date | None = None) -> int:
        return ((today or date.today()) - self.generated_at).days

    def is_stale(self, today: date | None = None) -> bool:
        return self.days_old(today) > self.valid_days

    def boost_for(self, offer: Offer) -> float:
        return (self.category_boosts.get(offer.category, 1.0)
                * self.hot_items.get(offer.item_id, 1.0))

    def price_floor(self, item_id: str) -> PriceFloor | None:
        return self.price_floors.get(item_id)


def load_watchlist(path: str | Path) -> Watchlist | None:
    """None se o arquivo não existe ou é inválido — o pipeline segue sem watchlist."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return Watchlist(
            generated_at=date.fromisoformat(raw["generated_at"]),
            valid_days=int(raw.get("valid_days", 14)),
            category_boosts={str(k): float(v) for k, v in (raw.get("category_boosts") or {}).items()},
            hot_items={str(k): float(v.get("boost", 1.0)) if isinstance(v, dict) else float(v)
                       for k, v in (raw.get("hot_items") or {}).items()},
            price_floors={str(k): PriceFloor(int(v["min_price_cents"]), int(v.get("window_days", 365)))
                          for k, v in (raw.get("price_floors") or {}).items()
                          if isinstance(v, dict) and "min_price_cents" in v},
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None

from typing import Protocol

from afiliado.models import Offer


class Source(Protocol):
    name: str

    def fetch_offers(self, cfg: dict) -> list[Offer]: ...

    def resolve_affiliate_link(self, offer: Offer) -> str: ...

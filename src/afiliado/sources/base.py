from typing import Protocol

from afiliado.models import Offer


class Source(Protocol):
    name: str

    def fetch_offers(self, cfg: dict) -> list[Offer]: ...

    def resolve_affiliate_link(self, offer: Offer) -> str: ...

    def refresh_price(self, offer: Offer) -> Offer:
        """Atualiza o preço da oferta imediatamente antes de publicar.
        Levanta SourceError se a oferta deixou de valer o post.

        OPCIONAL: o pipeline só chama quando a fonte implementa (via
        `getattr(src, "refresh_price", None)`) — fontes cujo `fetch_offers`
        já devolve preço ao vivo (ex.: Shopee) podem devolver a oferta
        inalterada ou simplesmente não implementar o método."""
        ...

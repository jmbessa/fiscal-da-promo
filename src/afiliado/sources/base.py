from typing import Protocol

from afiliado.models import Offer


class Source(Protocol):
    name: str

    # A janela, em dias, que o `sales` desta fonte MEDE — 0 = contador
    # VITALÍCIO (o número que o próprio anúncio exibe), 30 = último mês.
    # OBRIGATÓRIO numa fonte nova, e o mesmo valor tem de ir para o
    # `Offer.sales_window_days` que ela constrói: foi por falta desta
    # declaração que a Shopee publicou por meses um número de 30 dias como se
    # fosse o total do anúncio (fase 5H; o teste que trava isso está em
    # `tests/test_sales_window.py`).
    sales_window_days: int

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

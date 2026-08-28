from __future__ import annotations

from dataclasses import dataclass


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
    # Fase 5M — Mercado Livre: o ANÚNCIO (vendedor) de onde o preço publicado
    # veio e para onde o nosso link aponta. `item_id` do ML é o PRODUTO (a
    # página de catálogo, que tem dezenas de vendedores e um preço por
    # vendedor); é `anuncio_id` que amarra preço e link ao mesmo objeto.
    # Carimbado por `MeliSource.refresh_price` e lido por
    # `resolve_affiliate_link`. Vazio nas outras fontes (na Shopee o item JÁ é
    # o anúncio) e antes do refresh.
    anuncio_id: str = ""
    category: str = ""
    sales: int = 0
    rating: float = 0.0        # nota média (0 = desconhecida)
    price_min_cents: int = 0   # 0 = desconhecido; > 0 e != price_current => produto com variações
    price_max_cents: int = 0
    commission_brl: float = 0.0  # comissão absoluta em R$ informada pela API
    # Régua honesta (fase 4 → 5B). Referência = mediana da janela; p25 = topo
    # do quartil mais barato; janela = dias distintos que sustentam os dois.
    # 0 = desconhecido — e sem p25/janela o post NUNCA alega desconto.
    price_ref_cents: int = 0
    price_p25_cents: int = 0
    price_window_days: int = 0
    price_floor_cents: int = 0        # mínima histórica conhecida. 0 = desconhecida
    price_floor_window_days: int = 0
    # `sales` é um BALDE ("+250 mil"), não uma contagem exata? O Mercado Livre
    # só publica a faixa (100 mil, 250 mil, 1 milhão) e o anúncio escreve
    # "+250 mil vendidos". Escrever "250 mil vendidos" seco seria afirmar
    # precisão que o dado não tem — e, no primeiro story real, o número exibido
    # estava 50x abaixo do anúncio por outro motivo (era estimativa mensal).
    sales_e_faixa: bool = False
    # QUAL JANELA o `sales` mede, em dias. 0 = contador VITALÍCIO (o número que
    # o próprio anúncio exibe); 30 = unidades no último mês.
    #
    # Fase 5H: sem isto, `sales` era um int sem unidade, e as duas fontes o
    # preenchiam em escalas diferentes sem que nada no código dissesse.
    # Medido em 2026-08-28 contra o cubo `ShbMartItem` do JoomPulse, o
    # `productOfferV2.sales` da Shopee bate com `sold30Days` e fica 13× a 43×
    # abaixo do `sold1y` que o anúncio exibe (lençol 16692338189: nosso 45.950,
    # anúncio 2.000.000). A arte escrevia "45 mil vendidos" para um produto que
    # o comprador vê anunciado como 2 milhões.
    #
    # O default 0 é o valor CERTO para um payload antigo do ML (que já era
    # vitalício) e ERRADO para um payload antigo da Shopee, gravado em
    # `candidates` antes desta fase — ele volta a dizer "45 mil vendidos" sem a
    # janela. É aceito de propósito: `shopee.candidate_max_age_days` é 3, o
    # estoque gira sozinho, e uma migração de payload custaria mais que a
    # subestimação de três dias.
    sales_window_days: int = 0

    @property
    def discount_pct(self) -> int:
        """Desconto informado pelo vendedor — NÃO exibir e NÃO filtrar por ele
        (o "de" é inflado). Use real_discount_pct."""
        if self.price_original_cents <= 0:
            return 0
        return round((1 - self.price_current_cents / self.price_original_cents) * 100)

    @property
    def real_discount_pct(self) -> int:
        """Desconto verificável contra a NOSSA referência, arredondado para
        BAIXO em aritmética inteira (9,5% -> 9; `round` dava 10 e passava no
        mínimo de 10). 0 quando não há referência ou o preço atual não está
        abaixo dela."""
        if self.price_ref_cents <= 0 or self.price_current_cents >= self.price_ref_cents:
            return 0
        return (self.price_ref_cents - self.price_current_cents) * 100 // self.price_ref_cents


@dataclass(frozen=True)
class Verdict:
    """O que o post PODE alegar. Decidido UMA vez por `pricing.verdict` e
    consumido por texto, arte, legendas e copy sem recalcular nada — é o que
    faz Telegram, story, feed e copy concordarem (C9/C10)."""
    mode: str            # "A" (alega desconto) | "B" (preço + prova social)
    discount_pct: int    # só quando mode == "A"; senão 0
    seal: str            # "" ou o texto do selo, já com a janela real
    seal_window_days: int = 0   # janela (dias) que sustenta o selo; 0 = sem selo


# Um post que ninguém decidiu não alega nada: preço + prova social, sem selo.
NO_CLAIM = Verdict("B", 0, "")


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
    verdict: Verdict = NO_CLAIM

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
    # Fase 5P — o preço de CHECKOUT, lido do navegador (`afiliado.preco_real`),
    # e a condição que a própria página deu a ele ("com cupom", "no Pix com
    # cupom"). 0/"" = não houve leitura, que é o estado normal e o estado para o
    # qual toda falha da leitura cai.
    #
    # Ele NÃO substitui `price_current_cents`, e isso é o desenho: o preço de
    # catálogo é a série que o `price_log`, a mediana, o p25, o piso do selo, o
    # `check_price` e os filtros de faixa usam. Um preço de cupom dentro dessa
    # série tornaria a regra do quartil da 5B mentirosa — "abaixo do quartil
    # mais barato" passaria a valer todo dia em que houvesse cupom, que é
    # exatamente o padrão "promoção recorrente" que ela existe para não
    # certificar. O checkout muda o número EXIBIDO e nada mais.
    price_checkout_cents: int = 0
    price_checkout_label: str = ""
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

    @property
    def checkout_discount_pct(self) -> int:
        """Fase 5R — a porcentagem de CHECKOUT: quanto o cupom tira do preço de
        catálogo. Arredondada para BAIXO, como todo percentual deste projeto.

        São **dois números observados por nós**: `price_current_cents`, o preço
        de catálogo que a API acabou de medir, e `price_checkout_cents`, o preço
        exibido que o cubo (ou o navegador) trouxe. Nenhum dos dois é o "de" do
        vendedor — e é por isso que este número pode ir à peça, enquanto
        `discount_pct` e `priceDiscountRate` não podem.

        0 quando não há leitura, quando o exibido não é menor, ou quando não há
        catálogo com que comparar: sem os dois números não há conta."""
        if self.price_checkout_cents <= 0 or self.price_current_cents <= 0:
            return 0
        if self.price_checkout_cents >= self.price_current_cents:
            return 0
        return ((self.price_current_cents - self.price_checkout_cents) * 100
                // self.price_current_cents)

    @property
    def published_price_cents(self) -> int:
        """O número que vai à peça: o de checkout quando o navegador o leu, o de
        catálogo quando não (fase 5P). Um lugar só, para que arte, texto do
        Telegram e legendas nunca mostrem números diferentes."""
        return self.price_checkout_cents or self.price_current_cents

    @property
    def published_discount_pct(self) -> int:
        """O desconto que FECHA A CONTA dos dois números exibidos — a mesma
        aritmética de `real_discount_pct`, sobre o preço publicado.

        São dois números diferentes de propósito. `real_discount_pct` é o do
        PORTÃO: ele compara catálogo com catálogo e decide se o post pode alegar
        desconto (a régua da 5B). Este é o do RÓTULO: se a peça mostra
        "De: R$ 750,00 | Por: R$ 523,48", o percentual impresso tem de ser o de
        750,00 para 523,48, senão o seguidor faz a conta e ela não bate. Sem
        leitura os dois são o mesmo número."""
        publicado = self.published_price_cents
        if self.price_ref_cents <= 0 or publicado >= self.price_ref_cents:
            return 0
        return (self.price_ref_cents - publicado) * 100 // self.price_ref_cents


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

# O preço da Shopee: o que a API entrega, e o que ela nunca vai entregar

Investigação de **2026-08-28**, provocada por um caso real: o story publicou
**R$ 689,99** e o dono abriu o anúncio e viu **R$ 611,80**.

## O veredito: o story estava CERTO

A página da Shopee diz, em duas linhas:

> **R$ 611,80** no Pix com cupom
> ou **R$ 689,99 sem cupom** em outros métodos de pagamento

R$ 689,99 é o preço que qualquer pessoa paga sem cupom. R$ 611,80 exige **Pix
mais cupom** — é desconto de **checkout**, não preço de catálogo.

O problema não é de exatidão, é de leitura: a Shopee põe os R$ 611,80 em
vermelho grande e os R$ 689,99 em cinza pequeno. Quem clica bate o olho no
número menor e conclui que o nosso está velho.

## O preço com cupom NÃO é obtível pela API de afiliados

Cinco caminhos fechados, todos medidos nesta data:

1. **`productOfferV2`, todos os 26 campos** (introspecção do schema): os únicos
   de preço são `price`, `priceMin`, `priceMax` e `priceDiscountRate`. Para o
   item do caso, os quatro diziam 689,99 / 689,99 / 759 / 47%.
2. **As 8 queries que a API expõe** (`shopOfferV2`, `shopeeOfferV2`,
   `productOfferV2`, `conversionReport`, `validatedReport`,
   `partnerOrderReport`, `listItemFeeds`, `getItemFeedData`): nenhuma tem
   argumento ou retorno de cupom/voucher.
3. **O data feed de itens** (`getItemFeedData`): 16 colunas, das quais só
   `price`, `sale_price` e `discount_percentage` são de preço. `sale_price`
   difere de `price` em 48 de 100 linhas amostradas, e a diferença bate com
   `discount_percentage` — ou seja, é o par "de/por" que o `productOfferV2` já
   dá, não o preço com cupom.
4. **A rota pública do site** (`/api/v4/item/get`): **HTTP 403** a partir do
   servidor.
5. **A própria página** só entrega o número renderizando JavaScript, atrás de
   proteção anti-bot.

**Conclusão: é estrutural.** Desconto de meio de pagamento e cupom acontece no
checkout e não existe no catálogo de afiliados. Nenhuma engenharia nossa
resolve isso; só a Shopee, expondo o campo.

## O que fazer, então

O caminho honesto é rotular: a pill do preço leva **"sem cupom"**. A frase é
verdadeira mesmo quando não há cupom disponível (o preço sem cupom é aquele), e
transforma a aparente contradição em serviço — explicar a letra miúda é
exatamente a voz da conta.

O que **não** fazer: publicar um preço com cupom que não conseguimos verificar,
ou omitir o preço. Prometer menos do que a realidade e o seguidor achar mais
barato é o erro seguro; o contrário destrói a única coisa que a conta vende.

## Achado colateral, e ele é grande

`listItemFeeds` / `getItemFeedData` expõem **feeds de catálogo inteiros**:

| feed | itens |
|---|---|
| Shopee Brasil (FULL) | 10.000 |
| **Shopee Oficial BR (FULL)** | **100.000** |
| Shopee Brasil (DELTA) | 19.756 |
| Shopee Oficial BR (DELTA) | 170.217 |

Cada linha traz `itemid`, `title`, `price`, `sale_price`,
`discount_percentage`, `item_rating`, categorias globais, `image_link`,
`product_link` e o link curto. O teto por chamada é **500** (medido: 1000
devolve `error [11001] ... the maximum limit is 500`), então o catálogo oficial
inteiro sai em **200 chamadas**.

Compare com a descoberta de hoje: 8 chamadas por run dentro de janelas de 2.000
itens por (categoria, ordenação). O feed é uma superfície muito maior e mais
barata — vale uma fase própria, tanto para volume quanto para aliviar a pressão
sobre a API de busca.

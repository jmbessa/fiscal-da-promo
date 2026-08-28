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

### Feito (fase 5K, 2026-08-28)

A regra mora em `pricing.sem_cupom` — **só Shopee**, porque o preço que
publicamos do ML é o do anúncio que vence o buy box, exatamente o que a página
mostra. Dali ela viaja para tudo que publica preço, sem ninguém reimplementá-la:

| superfície | onde aparece |
|---|---|
| arte de story, de feed e slide do carrossel | `creative._pill_nota`: dentro da pill, à direita do preço |
| texto do Telegram | `pricing.price_line_html` |
| legenda do feed e do despacho de story | `pricing.price_line` |
| legenda do carrossel | `pricing.preco_publicado` |

**Por que dentro da pill.** A colocação foi decidida gerando previews e olhando
as imagens (story e feed, modo A e B, com e sem selo, título longo, story com
figurinha de link). As três alternativas caíram na imagem:

- **na linha de meta**: ela não tem guarda horizontal (fase 5H). Com o rótulo,
  o caso típico da Shopee vai de 840 px para 1056 px no story — margem de 12 px
  contra 72 de padding —, e a 1,5 milhão de vendas sai a 1146 px, cortada pela
  borda. Além disso, no feed em modo A com selo a meta é derrubada pelo guarda
  de overflow, e o rótulo sumia junto;
- **em segunda linha dentro da pill**: engorda a pill ~50 px e o guarda passa a
  derrubar o **selo** no feed com título longo;
- **em linha própria sob a pill**: come o respiro `STORY_META_GAP = 88`, que o
  dono pediu.

Alinhado pela linha de base do preço, o rótulo custa **zero altura** e cabe no
guarda horizontal que a pill já tinha. Pior caso publicável (`price_max_brl` =
1000, ou seja R$ 999,99, com referência riscada de R$ 1.999,99): pill de 906 px
no story — 30 px dentro da largura útil, 15 px de margem sobrando; 803 px no
feed. Onde não couber, quem cede é a referência riscada, nunca o rótulo.

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

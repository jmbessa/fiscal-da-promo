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

### Feito (fase 5L, 2026-08-28)

O feed entra **ao lado** da busca, em `ShopeeSource._fatia_do_feed`, com as
mesmas disciplinas dela: `_post`, teto de chamadas por run
(`shopee.feed_calls_per_run`), cursor de `offset` persistido e `SourceError`.

**FULL, e não DELTA — o contrário do que parecia.** O DELTA oficial tem
**170.217** linhas contra 100.000 do FULL (341 chamadas contra 200) e, numa
janela de 500 dele, **229 linhas são `DELETE`** contra 264 `NEW` e 7 `UPDATE`.
Ou seja: o DELTA custa 70% mais chamadas para entregar menos linha
aproveitável. Não existe, nesta conta, o caminho barato de manter o estoque
fresco pelo delta.

**O `datafeedId` carrega a data** (`428536169534861312_FULL_2026-08-27`) e o
arquivo é regerado todo dia, então ele é relistado a cada run (1 chamada, além
da janela). O ciclo de 200 janelas é, por isso, uma **amostra rotativa** do
catálogo — não uma partição dele.

**O que o feed não traz: `commission` e `sales`.** As duas só existem depois do
`refresh_price`, que roda imediatamente antes de publicar. Com o `min_ev_brl`
do config real, o piso de EV leria a comissão ausente como "vale zero" e
mataria 100% das candidatas do feed em silêncio — o mesmo defeito da 5J com
outro campo. A rede é `selection.comissao_desconhecida` mais um lote inteiro de
feed em `tests/test_zero_silencioso.py`.

**O que rende, medido em 2026-08-28.** Pelas mesmas 200 chamadas:

| superfície | itens varridos | elegíveis | por chamada |
|---|---|---|---|
| busca (5 raízes × 40 páginas × 50) | 10.000 | 5.460 (54,6%) | ~27 |
| data feed oficial (200 × 500) | 100.000 | ~32.000 (32,2/32,4/32,6% em três janelas) | ~160 |

E o que ele **não** rende: popularidade. Os itens do feed são a cauda longa do
catálogo — mediana de **1 venda** em 30 dias no feed oficial e **9** no "Shopee
Brasil" (25 itens sorteados de cada), contra os milhares do topo de categoria
que a busca traz ordenada. O feed é **alcance e reserva**, não substituto: sem
comissão e sem vendas ele entra no fim da fila e publica quando o topo se
esgota, que é o papel dele.

**Por curtidas, não por nota.** A nota do feed é 5,0 na mediana (165 de 172
linhas acima de 4,5 — não separa nada). O `like` separa: numa janela de 500 do
feed oficial, as 12 linhas mais curtidas somam **2.152** vendas de 30 dias
(mediana 33) e as 12 menos curtidas somam **2** (mediana 0). No "Shopee Brasil"
a coluna vem zerada em todas as linhas — é mais uma razão para o feed oficial.

**O teto de `feed_keep_per_run` não é da API, é do `state.db`.** 32% das linhas
passam nos portões; guardar tudo seriam ~9.800 candidatas/dia e, com
`candidate_max_age_days: 3`, ~29 mil linhas de ~600 bytes num arquivo binário
versionado — contra 60 posts/dia. Com 10, são ~610/dia.

**O `product_short link` já é de afiliado** (`utm_medium=affiliates`, 500 de
500 linhas) e vira o `offer_link` da oferta — a queda do `generateShortLink`.
Ele **não** vira o link publicado: é uma URL de ~700 caracteres contra os ~30
de um `shope.ee`, e trocar o gerador oficial por ela mexeria na atribuição de
todo post da loja por 60 chamadas/dia.

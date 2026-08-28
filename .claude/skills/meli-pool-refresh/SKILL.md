---
name: meli-pool-refresh
description: Regenera data/meli_offers.json (pool curado do Mercado Livre, formato da fase 5B) com o JoomPulse — top produtos por categoria, título/imagem, histórico semanal do anúncio que vence o buy box, mediana/p25/janela e mínima histórica. Rodar a cada 30 dias (validade do pool) ou quando o doctor avisar pool vencido/entradas ignoradas. Requer o conector JoomPulse (claude.ai) autenticado na sessão e respeita a cota diária de consultas.
---

# Regeneração do pool curado do Mercado Livre

Gera um novo `data/meli_offers.json` lido por `MeliSource.fetch_offers`. Desde a
fase 5B o pool carrega a **régua** do ML: `price_ref_cents` (mediana da janela),
`price_p25_cents` (topo do quartil mais barato), `price_window_days`, a mínima
histórica com a própria janela e o `buy_box_item_id` — o anúncio cujo preço vivo
o pipeline lê antes de publicar. Entrada sem qualquer um desses campos é
**pulada na carga** (ver "Validação" no fim); um pool que era foto de um dia não
passa mais.

## Pré-requisitos

- Conector **JoomPulse** disponível (ferramentas `mcp__claude_ai_JoomPulse__*`;
  carregue via ToolSearch se estiverem deferidas). Sem ele, avise o usuário e pare.
- Executar da raiz do repo. Leia `data/meli_offers.json` atual para comparar depois.
- Regras de consulta: leia `pulse://rules` (`read_resource`, não conta na cota)
  se for a primeira vez na sessão. Vendas ESTIMADAS (`catalogOrderCount1m`) são
  **ESTIMATIVAS** do JoomPulse — divulgue em todo resumo e no campo `source`.

## Orçamento (obrigatório)

O plano do JoomPulse tem **limite de consultas por dia**, e ele é PEQUENO.
Medido em 2026-08-26: 6 consultas antes da renovação diária e, depois dela,
**9 consultas — a 10ª devolveu `MCP subscription request limit exceeded`**. A
cota é da assinatura e é compartilhada entre `query_cubejs_meli` e
`query_cubejs_shopee`; `read_resource` não conta. Reconfirme o teto a cada
execução: ele pode mudar de plano.

- Parâmetro `max_consultas` por execução: **padrão 9**. Aceite outro valor se
  o usuário pedir; nunca ultrapasse.
- **Trabalhe em ONDAS fechadas, não em fases.** Uma onda = um lote de ~28
  produtos levado do começo ao fim (1 consulta de título + 4 de preço), de modo
  que, ao parar em qualquer ponto, o que está em disco é um pool COMPLETO e
  menor — nunca metade de um pool. Foi isso que salvou a execução de
  2026-08-26 (a onda 0 já estava fechada quando a cota morreu). Nunca faça
  "todos os títulos, depois todos os preços".
- Um pool pequeno e válido é melhor que um grande pela metade: 40 ofertas
  bastam para dias de publicação com dedupe de 30 dias.

## Produtos que o programa recusa (leia antes do Passo 1)

`data/meli_nao_afiliaveis.json` lista `product_id` para os quais o painel
devolve `error_code 111 — URL not allowed in affiliates program`. **Exclua-os
do pool sempre**: sem link não há comissão, e cada um custaria um descarte e
uma chamada de API por execução, para sempre. Medido em 2026-08-26: 3 de 40
produtos do pool caíram nessa condição (7,5%) — não é raro.

Depois de gerar o pool, rode `/meli-links-refresh`: os `product_id` que
voltarem com `error_code 111` devem ser **acrescentados** àquele arquivo e
**removidos** do pool antes de commitar.
- **Cada resultado bruto é salvo ANTES da próxima consulta** em
  `data/joompulse_raw/meli-pool-refresh/<AAAA-MM-DD>/<passo>_<lote>.json`
  (o JSON exato devolvido; o diretório está no `.gitignore`).
- `data/joompulse_raw/meli-pool-refresh/cursor.json` guarda onde parou:
  `{"data": "...", "consultas_feitas": N, "passo": "1"|"2"|"3"|"3b",
  "proximo_lote": k, "minimas_pendentes": ["<buyBoxId>", ...],
  "limite_observado": "<mensagem ou null>"}`. Atualize a cada consulta.
  `minimas_pendentes` são os anúncios que precisam do Passo 3b e não couberam
  no orçamento: suas entradas ficam FORA do pool até a próxima execução.
- Ao atingir `max_consultas` OU receber "limit exceeded": **PARE**, grave o
  cursor, informe quantas consultas fez e onde parou. A próxima execução lê o
  cursor e os arquivos brutos e **retoma dali sem repetir consulta** — os
  arquivos de dias anteriores continuam válidos enquanto o pool não vencer.
- O primeiro "limit exceeded" observado numa execução deve ser anotado em
  `docs/runbooks/meli-setup.md` (seção "Cota do JoomPulse") como o teto real,
  com a data e o número da consulta em que ocorreu.

## Categorias L1 do Mercado Livre (as do pool atual)

| id | nome |
|---|---|
| MLB1246 | Beleza e Cuidado Pessoal |
| MLB1574 | Casa, Móveis e Decoração |
| MLB1276 | Esportes e Fitness |
| MLB264586 | Saúde |

## Passo 1 — Top produtos por categoria (4 consultas)

Cubo `MercadoProductsWeekly` via `query_cubejs_meli`. **Somente `productId`
nas dimensões** — qualquer dimensão de anúncio quebra o rollup por produto.

```json
{"dimensions":["MercadoProductsWeekly.productId"],
 "measures":["MercadoProductsWeekly.catalogSales","MercadoProductsWeekly.catalogOrderCount1m","MercadoProductsWeekly.reviewsRating",
             "MercadoProductsWeekly.buyBoxPriceAmount","MercadoProductsWeekly.buyBoxPriceAmountHistoricMin",
             "MercadoProductsWeekly.buyBoxId"],
 "filters":[{"member":"MercadoProductsWeekly.merchantCategoryIdL1","operator":"equals","values":["<cat>"]},
            {"member":"MercadoProductsWeekly.catalogProduct","operator":"equals","values":["true"]},
            {"member":"MercadoProductsWeekly.catalogOrderCount1m","operator":"gt","values":["0"]}],
 "order":[["MercadoProductsWeekly.catalogOrderCount1m","desc"]],"limit":100}
```

## `sales` do pool é `catalogSales`, NUNCA `catalogOrderCount1m`

Os dois existem e são coisas diferentes:

| medida | o que é | exemplo (protetor MLB19755099) |
|---|---|---|
| `catalogSales` | contador ACUMULADO do próprio Mercado Livre — o "+250 mil vendidos" que aparece no anúncio | **250.000** |
| `catalogOrderCount1m` | ESTIMATIVA do JoomPulse de vendas no último mês | 5.148 |

O campo `sales` do pool vira "N vendidos" na arte, e o seguidor lê isso como o
contador do anúncio. Gravar a estimativa mensal ali dá um número **20 a 200
vezes menor** (medido no pool de 2026-08-26: a creatina dizia 27 mil quando são
1 milhão) — e ainda apresenta estimativa como fato, que é exatamente o que este
projeto existe para não fazer. Use `catalogSales` em `sales`.

`catalogOrderCount1m` continua servindo para ORDENAR (quem vende mais AGORA) e
para o filtro `gt 0` — só não vai para o arquivo.

### A mesma armadilha existe na Shopee — e ela mordeu (medido em 2026-08-28)

Não é uma peculiaridade do Mercado Livre: **toda loja publica os dois números**,
o contador do anúncio e a janela recente, e o campo que a API entrega raramente
diz qual dos dois é. O `productOfferV2.sales` da Shopee sobreviveu à correção do
ML acima porque ninguém foi conferir. Comparado com o cubo `ShbMartItem` do
JoomPulse — que define `sold1y` como o "cumulative lifetime sold counter **as
displayed by Shopee**" e `sold30Days` como a estimativa do último mês:

| item | nosso `sales` | `sold1y` (o que o anúncio exibe) | `sold30Days` |
|---|---|---|---|
| 16692338189 Lençol Micropercal | 45.950 | **2.000.000** | 50.000 |
| 22893738408 Lençol Extra Macio | 77.344 | **1.000.000** | 70.000 |
| 58256439593 Percarbonato | 73.175 | **100.000** | 70.000 |
| 9212570285 Creatina Soldiers | 31.077 | **100.000** | 30.000 |

Nos quatro casos o nosso número bate com a janela de ~30 dias e fica **13× a 43×
abaixo** do contador vitalício. (Os campos `sold*` são estimativas do JoomPulse
calibradas sobre os contadores arredondados que a Shopee publica, não dados
transacionais — a ordem de grandeza é que é inequívoca.) A arte escrevia "45 mil
vendidos" para um anúncio cuja PRÓPRIA FOTO traz o selo "+ DE 2 MILHÕES DE
UNIDADES VENDIDAS".

Buscar o `sold1y` para enriquecer a Shopee não fecha na cota (~9 consultas/dia
contra centenas de candidatas girando a cada 3 dias), então a fase 5H manteve o
número e mudou o TEXTO: cada fonte declara sua janela em
`Offer.sales_window_days` e `pricing.format_sales` escreve "45 mil vendidos no
último mês". Ao trocar de fonte de dados aqui, **confira a janela antes de
gravar em `sales`** — e ajuste a declaração da fonte, senão
`tests/test_sales_window.py` reprova.

(O filtro `catalogOrderCount1m gt 0` é exigido por `pulse://rules`: ordenar
`desc` por medida põe NULL primeiro.) Guarde por produto: `buyBoxId`,
`buyBoxPriceAmount`, `buyBoxPriceAmountHistoricMin`, `catalogSales`,
`catalogOrderCount1m`,
`reviewsRating`. Pré-filtro sem consulta: `reviewsRating >= 4.5`, preço do buy
box entre `selection.price_min_brl` e `price_max_brl` do `config.yaml`.

## Passo 2 — Título e imagem (≈1 consulta por 50 produtos)

Cubo `MlbProductsSortedByProductId`:

```json
{"dimensions":["MlbProductsSortedByProductId.productId","MlbProductsSortedByProductId.productName",
               "MlbProductsSortedByProductId.productImage"],
 "measures":["MlbProductsSortedByProductId.buyBoxId"],
 "filters":[{"member":"MlbProductsSortedByProductId.productId","operator":"equals","values":["<lote de até 50 ids>"]}],
 "limit":100}
```

Se vierem várias linhas por produto, fique com a do `buyBoxId` (inclua
`MlbProductsSortedByProductId.id` nas dimensões para distingui-las, ao custo
de mais linhas); página cheia de 100 → pagine com `offset`.

## Passo 3 — Histórico semanal do anúncio do buy box (≈1 consulta por 7 anúncios)

Cubo `MlbProductPricesDaily`: o histórico é por **ANÚNCIO** (`id` =
`buyBoxId`), uma linha por dia, `measures: price` (preço real do buy box).
Para caber no `limit 100`, agregue por semana — 13 linhas por anúncio, ~7
anúncios por consulta:

```json
{"dimensions":["MlbProductPricesDaily.id"],
 "measures":["MlbProductPricesDaily.price"],
 "timeDimensions":[{"dimension":"MlbProductPricesDaily.date","granularity":"week","dateRange":"last 13 weeks"}],
 "filters":[{"member":"MlbProductPricesDaily.id","operator":"equals","values":["<7 buyBoxIds>"]}],
 "limit":100}
```

Para cada anúncio, com as médias semanais ordenadas (centavos, sempre
**para baixo** — `Decimal` com `ROUND_FLOOR`; nunca float em dinheiro):

1. `price_ref_cents` = mediana pelo método "menor dos dois centrais"
   (posição `(n-1)//2` da lista ordenada).
2. `price_p25_cents` = 25º percentil para baixo (posição `(n-1)//4`).
3. `price_window_days` = 7 × semanas observadas. Menos de **2 semanas** não
   chega aos 14 dias da regra do quartil (a entrada nunca teria modo A) —
   pule; prefira exigir 4 semanas e registrar quantos caíram por isso.
4. `price_historic_min_cents` = `buyBoxPriceAmountHistoricMin` × 100
   (arredondamento normal de centavos), `price_min_window_days` = 365.
   **Se a mínima vier maior que o p25** (o vencedor do buy box mudou e a
   mínima histórica é de outro anúncio — caso real: MLB66637233, pool antigo
   com mínima R$ 30,51 e vencedor de hoje com mínima R$ 104,90), gaste **1
   consulta na série DIÁRIA daquele anúncio** (Passo 3b) e use a mínima
   diária verdadeira. Conte quantos precisaram disso para o resumo.
5. `buy_box_item_id` = `buyBoxId`.

**Média semanal NUNCA vira mínima.** A média de uma semana é ≥ a menor
diária daquela semana: usá-la como `price_historic_min_cents` faria o selo
"menor preço dos últimos N dias (verificado)" sair para um preço ACIMA de um
que existiu — a única alegação verificada do post viraria a mentira mais
fácil de desmentir. Mediana e p25 podem vir das médias semanais (são o preço
TÍPICO, e a suavização puxa para o meio); a mínima, não.

## Passo 3b — mínima diária, só para quem precisa (1 consulta por anúncio)

Mesmo cubo, sem agregação semanal: 13 semanas em dias são 91 linhas, dentro
do `limit 100` — mas cabe **um anúncio por consulta**, por isso só para os
anúncios em que `buyBoxPriceAmountHistoricMin > price_p25_cents`.

```json
{"dimensions":["MlbProductPricesDaily.id"],
 "measures":["MlbProductPricesDaily.price"],
 "timeDimensions":[{"dimension":"MlbProductPricesDaily.date","granularity":"day","dateRange":"last 13 weeks"}],
 "filters":[{"member":"MlbProductPricesDaily.id","operator":"equals","values":["<1 buyBoxId>"]}],
 "limit":100}
```

- `price_historic_min_cents` = **menor preço diário** da série (centavos, para
  baixo), `price_min_window_days` = **dias observados** (linhas com preço, não
  91 fixo): a janela do selo é o que foi medido.
- A mínima diária das mesmas 13 semanas é sempre ≤ p25 (o p25 é uma média
  semanal e nenhuma média é menor que a mínima do período), então a entrada
  passa no leitor — que rejeita `mínima > p25`.
- **Se não couber no orçamento do dia** (`max_consultas` ou "limit
  exceeded"): a entrada fica **FORA do pool** desta execução, registrada no
  cursor para a próxima. Nunca grave a mínima do outro anúncio nem uma média
  semanal no lugar dela — sem mínima confiável, é melhor não ter a entrada
  do que ter um selo falso.

**Ressalva a registrar no resumo:** o histórico é do anúncio que vence o buy
box HOJE; se o vencedor mudou dentro da janela, a série é a do vencedor atual
e a janela pode ser curta.

## Passo 4 — Gravar, validar, links, commitar

1. Escrever `data/meli_offers.json` (até 50 produtos por categoria, ordenados
   por `catalogOrderCount1m`):
```json
{"generated_at": "<hoje AAAA-MM-DD>", "valid_days": 30,
 "source": "JoomPulse (vendas = estimativa) — MercadoProductsWeekly + MlbProductPricesDaily",
 "offers": [{
   "product_id": "MLB18725310", "title": "...", "image_url": "...",
   "category": "MLB264586", "buy_box_item_id": "MLB3928374651",
   "buy_box_checked_at": "<hoje AAAA-MM-DD>",
   "price_ref_cents": 2590, "price_p25_cents": 2428, "price_window_days": 91,
   "price_historic_min_cents": 1699, "price_min_window_days": 365,
   "sales": 13337, "rating": 4.8}]}
```
   `buy_box_checked_at` é a data em que o `buyBoxId` foi lido (hoje, na
   geração). O leitor só aceita a entrada por **7 dias** a partir dela — ver
   "Passo semanal" abaixo.
2. Validar com o MESMO leitor do pipeline (é o que o `doctor` roda):
```
PYTHONPATH=src python -c "import httpx; from afiliado.config import load_config; from afiliado.sources.meli import MeliSource; s=MeliSource('x','y',client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))); o=s.fetch_offers(load_config('config.yaml')); print(len(o),'ofertas validas;', s.pool_warning or 'nenhuma ignorada')"
```
   Toda entrada ignorada vem com o motivo (`fora da faixa de preço`, `sem
   p25`, `mínima acima do p25`, `sem buy box`…) — corrija a curadoria, não o
   leitor.
3. Rode `/meli-links-refresh` para os `product_id` novos (sem link no pool de
   links a oferta é descartada no run).
4. Resumo ao usuário: quantos entraram/saíram vs. o pool anterior,
   distribuição `ref / mínima`, quantos caíram por regra, quantos precisaram
   da série diária (Passo 3b) e quantos ficaram de fora por falta de
   orçamento para ela, consultas gastas e o que ficou para a próxima
   execução. Vendas são **estimativas**.
5. Commit: `chore: regenera pool do ML (dados JoomPulse de <data>)` terminando
   com `Co-Authored-By:` do modelo em uso. `data/joompulse_raw/` NÃO entra
   (gitignored). Se o repo tiver remote, perguntar antes de push.

## Passo semanal — checar buy box (4 consultas)

O vencedor do buy box muda, e o anúncio do pool pode **sumir** da lista de
vendedores (visto ao vivo em 2026-08-26: MLB68104527 — o `buyBoxId` lido no
mesmo dia já não estava em `/products/{id}/items`; ver a tabela em
`docs/runbooks/meli-setup.md`). O pipeline então descarta a oferta ("sem buy
box") — e, pior, quando o anúncio continua na lista mas já não vence, o post
sairia com um preço que não é o da página. Por isso o leitor só aceita cada
entrada por **7 dias** a partir de `buy_box_checked_at` (motivo: "buy box não
verificado há N dias"), e este passo existe para renovar a data.

**Toda semana** (ou quando o doctor/ops avisar "buy box não verificado"):

1. Refaça **só o Passo 1** (4 consultas, uma por categoria) — salve os brutos
   e o cursor como em qualquer execução (seção Orçamento).
2. Para cada entrada do pool cujo `product_id` veio na resposta: grave
   `buy_box_item_id = buyBoxId` e `buy_box_checked_at = hoje`. Produto que
   não veio no top da categoria: deixe a entrada como está (ela vence sozinha
   em 7 dias e o aviso diz por quê).
3. Conte e informe no resumo quantos `buy_box_item_id` **mudaram**. Para
   esses, `price_ref/p25/mínima` são do anúncio ANTERIOR (a série do Passo 3
   é por anúncio): se o orçamento do dia permitir, refaça o Passo 3 (e o
   Passo 3b, quando a mínima do vencedor novo passar do p25) para eles;
   senão, registre que ficaram com a
   régua do vencedor antigo e o preço vivo do novo — a regra do quartil e o
   selo continuam honestos (o preço publicado é sempre o do anúncio gravado),
   mas a referência pode estar defasada até a próxima regeneração.
4. Valide com o leitor (Passo 4.2) e commite: `chore: renova buy box do pool
   do ML (JoomPulse de <data>)`.

Este passo NÃO regenera título/imagem/histórico — 4 consultas, não 40.

## Validação na carga (o que o leitor rejeita)

`MeliSource.fetch_offers` pula, contando no aviso por motivo: campo de preço
ausente/textual/≤ 0 (`price_ref_cents`, `price_p25_cents`,
`price_window_days`, `price_historic_min_cents`, `price_min_window_days`);
campo com fração de centavo (`4500.5` → `não inteiro`; `2590.0` vale 2590);
`price_ref_cents/100` fora de
`selection.price_min_brl..price_max_brl`; `price_p25_cents >
price_ref_cents`; `price_historic_min_cents > price_p25_cents`; sem
`buy_box_item_id`; `buy_box_checked_at` (ou, na falta dele, `generated_at`)
com mais de 7 dias ("buy box não verificado há N dias") ou inválida/no
futuro; `product_id` repetido. O aviso vai ao `doctor` e ao resumo de ops:
"3 entrada(s) do pool ignorada(s) (2 fora da faixa de preço, 1 sem p25)".

## Notas

- Nunca inventar números: todo valor gravado vem de uma linha de consulta
  salva em `data/joompulse_raw/`.
- O preço vivo publicado é o do `buy_box_item_id` (`refresh_price`), lido em
  `/products/{id}/items`; se esse anúncio sumir da lista, a oferta é
  descartada no run ("sem buy box") — não cai para o vendedor mais barato.
  A ORDEM dessa lista não é o buy box (`results[0]` bateu com a página em 2
  de 3 produtos ao vivo) — nunca use `results[0]` como vencedor.
- A regra do quartil (`pricing.verdict`) só alega desconto quando o preço vivo
  fica ESTRITAMENTE abaixo de `price_p25_cents` com janela ≥ 14 dias; o selo
  só quando ≤ `price_historic_min_cents`. Referência errada para cima vira
  desconto inventado — na dúvida, arredonde para baixo.
- Mínima errada para cima vira SELO inventado: `price_historic_min_cents` sai
  de `buyBoxPriceAmountHistoricMin` ou da série DIÁRIA (Passo 3b) — nunca de
  uma média semanal, que é ≥ a menor diária. Sem uma das duas, a entrada fica
  fora do pool.
- No run, o piso do pool ainda pode ser BAIXADO pelo nosso price_log (se o
  preço vivo que gravamos já foi menor, a mínima passa a ser a nossa) — nunca
  subido. Mas isso só corrige o item que JÁ publicamos alguma vez: uma mínima
  alta demais num item novo vira selo falso na primeira publicação, e o
  leitor só pega o caso extremo (`mínima > p25`). A defesa é o Passo 3b.

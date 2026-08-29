---
name: meli-pool-refresh
description: Regenera data/meli_offers.json (pool curado do Mercado Livre, formato da fase 5B) com o JoomPulse — top produtos por categoria, título/imagem e, na onda CARA, o histórico semanal do anúncio que vence o buy box (mediana/p25/janela e mínima histórica); a onda BARATA pula o histórico e grava a régua zerada, enchendo o pool muito mais rápido. Rodar a cada 30 dias (validade do pool) ou quando o doctor avisar pool vencido/entradas ignoradas. Requer o conector JoomPulse (claude.ai) autenticado na sessão e respeita a cota diária de consultas.
---

# Regeneração do pool curado do Mercado Livre

Gera um novo `data/meli_offers.json` lido por `MeliSource.fetch_offers`. Desde a
fase 5B o pool carrega a **régua** do ML: `price_ref_cents` (mediana da janela),
`price_p25_cents` (topo do quartil mais barato), `price_window_days`, a mínima
histórica com a própria janela e o `buy_box_item_id` — o anúncio cujo preço vivo
o pipeline lê antes de publicar. Entrada a que FALTE qualquer um desses campos é
**pulada na carga** (ver "Validação" no fim); um pool que era foto de um dia não
passa mais.

Desde a fase 5J há **duas modalidades de onda**: a cara, que compra o histórico
e permite alegar desconto verificado, e a barata, que grava os cinco campos de
régua como 0 e publica em modo B. Leia "Duas modalidades de onda" antes de
escolher — é a diferença entre encher o pool em dias e em semanas.

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

## Duas modalidades de onda: a BARATA e a CARA (leia antes de escolher)

O histórico de preço (Passo 3) custa **4 consultas a cada 28 produtos**;
título e imagem (Passo 2) custam **1 a cada 50**, e o Passo 1 já foi pago.
O histórico é ~15× mais caro que todo o resto junto, e é ele — sozinho — que
mantinha o pool em 35 produtos.

### Onda SEM HISTÓRICO (barata) — Passo 1 + Passo 2, pulando o Passo 3

Grave os **cinco campos de régua como 0**: `price_ref_cents`,
`price_p25_cents`, `price_window_days`, `price_historic_min_cents`,
`price_min_window_days`. Os cinco, presentes e zerados — o leitor rejeita
campo ausente (typo de curadoria) e rejeita zero PARCIAL ("régua parcial"),
porque `ref > 0` com `p25 = 0` é curadoria quebrada e não histórico faltando.

- **Rende ~50 produtos por consulta, contra 28 a cada 5.** É a diferença
  entre encher o pool em DIAS e em SEMANAS. Com a cota de 9 consultas/dia e o
  Passo 1 já pago: a onda cara entrega ~50 produtos/dia (5,6 por consulta),
  a barata cobre os **~260 candidatos que o Passo 1 sabe produzir em ~6
  consultas — um único dia**. Os mesmos 260 na onda cara custam ~47
  consultas, cinco a seis dias de cota inteira.
- **A oferta sai em modo B** — preço + prova social, sem alegar desconto e
  sem selo de menor preço — até o NOSSO `price_log` sustentar a régua
  (`selection.ref_min_observations` dias distintos; hoje 14). A partir daí
  ela ganha régua sozinha, das nossas medições, pelo degrau 3 de
  `pricing.enrich_offers`. Isso é uma promessa do código, não deste texto:
  `tests/test_pricing.py` trava as duas pontas (nunca alega desconto sem
  histórico; alega depois de 14 dias de price_log).
- **Ressalva medida (fase 5J):** essa graduação é LENTA na prática. O preço
  do ML só entra no `price_log` quando a oferta passa pelo `refresh_price`,
  isto é, quando ela é escolhida para publicar — e o dedupe de 30 dias a tira
  da fila logo em seguida. São ~1 observação por item a cada 30 dias, ou seja
  ~14 MESES para os 14 dias distintos, não 14 dias. Enquanto isso não mudar,
  trate o modo B como o estado permanente da onda barata.

### Onda COM HISTÓRICO (cara) — a de sempre, com o Passo 3

Continua existindo e é **preferível para os produtos de maior venda**, onde
alegar desconto verificado e carimbar o selo de menor preço vale as
consultas. Regra prática: gaste o Passo 3 no topo de `catalogOrderCount1m`
de cada categoria e deixe a cauda para a onda barata.

### Teto medido (2026-08-26) — de onde vêm os candidatos

`MercadoProductsWeekly` devolve **até 100 produtos por categoria por
consulta** (`limit: 100`), e as **4 categorias L1 atuais dão ~260
candidatos** depois do pré-filtro. Esse é o teto do Passo 1 como está: para
passar disso, **acrescente categorias L1** à tabela abaixo — não adianta
gastar mais consultas nas mesmas quatro.

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

**Só na onda CARA.** Na onda sem histórico, pule este passo e o 3b e grave os
cinco campos de régua como 0 (ver "Duas modalidades de onda").

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
   geração). Desde a fase 5M o leitor **não usa** nenhum dos dois campos e a
   entrada não expira por eles: eles ficam como procedência da régua curada e
   como chave da série de preço no JoomPulse.

   Entrada da onda SEM HISTÓRICO: tudo igual, com os cinco campos de régua
   zerados (os cinco presentes — ausente é erro, e zerar só alguns também):
```json
{"product_id": "MLB18725310", "title": "...", "image_url": "...",
 "category": "MLB264586", "buy_box_item_id": "MLB3928374651",
 "buy_box_checked_at": "<hoje AAAA-MM-DD>",
 "price_ref_cents": 0, "price_p25_cents": 0, "price_window_days": 0,
 "price_historic_min_cents": 0, "price_min_window_days": 0,
 "sales": 13337, "rating": 4.8}
```
2. Validar com o MESMO leitor do pipeline (é o que o `doctor` roda):
```
PYTHONPATH=src python -c "import httpx; from afiliado.config import load_config; from afiliado.sources.meli import MeliSource; s=MeliSource('x','y',client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))); o=s.fetch_offers(load_config('config.yaml')); print(len(o),'ofertas validas;', s.pool_warning or 'nenhuma ignorada')"
```
   Toda entrada ignorada vem com o motivo (`fora da faixa de preço`, `sem
   p25`, `mínima acima do p25`, `régua parcial`…) — corrija a curadoria, não o
   leitor.
3. Rode `/meli-links-refresh` para os `product_id` novos — ele cunha os links
   dos 3 anúncios mais baratos de cada produto. Produto sem anúncio linkado é
   descartado no run.
4. Resumo ao usuário: quantos entraram/saíram vs. o pool anterior,
   distribuição `ref / mínima`, quantos caíram por regra, quantos precisaram
   da série diária (Passo 3b) e quantos ficaram de fora por falta de
   orçamento para ela, consultas gastas e o que ficou para a próxima
   execução. Vendas são **estimativas**.
5. Commit: `chore: regenera pool do ML (dados JoomPulse de <data>)` terminando
   com `Co-Authored-By:` do modelo em uso. `data/joompulse_raw/` NÃO entra
   (gitignored). Se o repo tiver remote, perguntar antes de push.

## Passo semanal — checar buy box: NÃO EXISTE MAIS (fase 5M)

Havia aqui um passo de 4 consultas por semana para renovar
`buy_box_checked_at`, porque o leitor só aceitava a entrada por 7 dias a
partir dessa data. **Não rode mais.** Desde a fase 5M o preço publicado não é
o do anúncio do buy box (ele nunca foi o vencedor — era só um vendedor, e nos
dois stories errados de 2026-08-28 um caro): é o do **anúncio linkado mais
barato**, lido ao vivo em `/products/{id}/items` imediatamente antes de
publicar. Nada no pipeline lê `buy_box_item_id` nem `buy_box_checked_at`, e o
leitor não expira mais por eles — só pela validade do arquivo (30 dias).

Os dois campos continuam sendo gravados pelos Passos 1–3 (são a chave da série
de preço no JoomPulse e documentam de onde a régua curada veio), mas ninguém
os consulta em produção. **O que precisa de rotina agora é
`/meli-links-refresh`**: os anúncios linkados envelhecem (~65% sobrevivem a 30
dias) e é a cobertura de links que decide se o ML publica.

## Validação na carga (o que o leitor rejeita)

`MeliSource.fetch_offers` pula, contando no aviso por motivo: campo de preço
ausente/nulo/textual/negativo (`price_ref_cents`, `price_p25_cents`,
`price_window_days`, `price_historic_min_cents`, `price_min_window_days`);
campo com fração de centavo (`4500.5` → `não inteiro`; `2590.0` vale 2590);
**régua parcial** (uns campos de régua zerados e outros não);
`price_ref_cents/100` fora de
`selection.price_min_brl..price_max_brl`; `price_p25_cents >
price_ref_cents`; `price_historic_min_cents > price_p25_cents`;
`product_id` repetido. O aviso vai ao `doctor` e ao resumo de ops:
"3 entrada(s) do pool ignorada(s) (2 fora da faixa de preço, 1 sem p25)".

A régua curada é **validada e depois descartada** (fase 5M): ela é o histórico
do anúncio do buy box e o preço publicado é de outro anúncio, então a oferta
nasce sem régua e publica em modo B. Continue gravando os números certos — eles
ficam no arquivo, a validação segue pegando curadoria quebrada, e a faixa de
preço ainda é checada sobre a referência.

Desde a fase 5J o leitor **aceita** a entrada com os cinco campos de régua
presentes e iguais a 0 (a onda barata). Duas consequências que aparecem no
aviso e no `doctor`:

- a faixa `price_min_brl..price_max_brl` é checada sobre a REFERÊNCIA e não
  tem como rodar sem ela, então para essas entradas ela é **adiada**, não
  removida: quem barra é `validate.check_price`, com os mesmos números, sobre
  o preço VIVO lido pelo `refresh_price` antes de publicar. Uma oferta de
  R$ 3.000 continua caindo — só cai depois. Ainda assim, mantenha o
  pré-filtro de preço do Passo 1: cada entrada fora da faixa custa uma
  chamada de API e um descarte em todo run;
- o `doctor` imprime a proporção — "🏷️ Mercado Livre: 0 de 53 entrada(s)
  com régua curada; 53 em modo B esperando histórico" —, e a mesma linha vai
  ao resumo de ops a cada run. **Desde a 5M o primeiro número é sempre 0**:
  não é defeito do pool, é a régua sendo descartada de propósito.

## Notas

- Nunca inventar números: todo valor gravado vem de uma linha de consulta
  salva em `data/joompulse_raw/`.
- O preço vivo publicado é o do **anúncio linkado mais barato** que passa no
  piso de qualidade (`refresh_price`), lido em `/products/{id}/items`; se
  nenhum anúncio linkado sobrar na lista, a oferta é descartada no run — não
  cai para um vendedor sem link. Quem cunha os links é `/meli-links-refresh`.
- A regra do quartil (`pricing.verdict`) só alega desconto quando o preço vivo
  fica ESTRITAMENTE abaixo de `price_p25_cents` com janela ≥ 14 dias; o selo
  só quando ≤ `price_historic_min_cents`. Desde a 5M nenhuma oferta do ML
  chega lá com régua do pool (ela é zerada na carga) — a régua que vale é a do
  nosso `price_log`. Referência errada para cima ainda vira desconto
  inventado quando ela voltar: na dúvida, arredonde para baixo.
- Mínima errada para cima vira SELO inventado: `price_historic_min_cents` sai
  de `buyBoxPriceAmountHistoricMin` ou da série DIÁRIA (Passo 3b) — nunca de
  uma média semanal, que é ≥ a menor diária. Sem uma das duas, a entrada fica
  fora do pool.
- No run, o piso do pool ainda pode ser BAIXADO pelo nosso price_log (se o
  preço vivo que gravamos já foi menor, a mínima passa a ser a nossa) — nunca
  subido. Mas isso só corrige o item que JÁ publicamos alguma vez: uma mínima
  alta demais num item novo vira selo falso na primeira publicação, e o
  leitor só pega o caso extremo (`mínima > p25`). A defesa é o Passo 3b.

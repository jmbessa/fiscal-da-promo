---
name: watchlist-refresh
description: Atualiza data/watchlist.json com dados frescos do JoomPulse (categorias em alta, itens em aceleração na Shopee, referências honestas de preço e mínimas históricas). Rodar semanalmente. Requer o conector JoomPulse (claude.ai) autenticado na sessão.
---

# Atualização semanal da watchlist

Gera um novo `data/watchlist.json` consumido pelo pipeline (`afiliado run`) para
boost de ranking, referência honesta de preço (o "de" que o post pode alegar) e
selo "menor preço verificado". Validade: 14 dias — o chat de operações avisa
quando vencer.

## Pré-requisitos

- Conector **JoomPulse** disponível (ferramentas `mcp__claude_ai_JoomPulse__*`;
  carregue via ToolSearch se estiverem deferidas). Sem ele, avise o usuário e pare.
- Executar da raiz do repo. Leia `data/watchlist.json` atual para comparar depois.
- Regras de consulta: leia `pulse://rules` (`read_resource`, não conta na cota)
  se for a primeira vez na sessão. Divulgue sempre que vendas/GMV são
  **estimativas** do JoomPulse (caveat #21).

## Orçamento (obrigatório)

O plano do JoomPulse tem **limite de consultas por dia**, compartilhado entre
`query_cubejs_shopee` e `query_cubejs_meli` (fato: em 2026-08-26 a 7ª consulta
da sessão devolveu `MCP subscription request limit exceeded` e as seguintes
continuaram rejeitadas por ~15 min). `read_resource` não conta.

- Parâmetro `max_consultas` por execução: **padrão 35**. Nunca ultrapasse.
- **Cada resultado bruto é salvo ANTES da próxima consulta** em
  `data/joompulse_raw/watchlist-refresh/<AAAA-MM-DD>/<passo>_<lote>_p<pagina>.json`
  (JSON exato devolvido; diretório no `.gitignore`).
- `data/joompulse_raw/watchlist-refresh/cursor.json` guarda onde parou:
  `{"data": "...", "consultas_feitas": N, "passo": 1|2|3, "proximo_lote": k,
  "proximo_offset": o, "limite_observado": "<mensagem ou null>"}`.
- Ao atingir `max_consultas` OU receber "limit exceeded": **PARE**, grave o
  cursor, informe quantas consultas fez e onde parou. A próxima execução lê o
  cursor e os brutos e **retoma dali sem repetir consulta**.
- O primeiro "limit exceeded" observado deve ser anotado em
  `docs/runbooks/meli-setup.md` (seção "Cota do JoomPulse") como o teto real.

## Mapeamento de categorias (allowlist do config.yaml)

| categoryL1Name (JoomPulse) | id (config) |
|---|---|
| Beleza | 100630 |
| Casa e Decoração | 100636 |
| Saúde | 100001 |
| Esportes e Atividades ao Ar Livre | 100637 |
| Mãe e Bebê | 100632 |

Se o `config.yaml` mudar a allowlist, ajuste este mapeamento e os filtros abaixo.

## Passo 1 — Categorias (boosts)

Cubo `ShbCategoriesMonthly` via `query_cubejs_shopee`. Use o último mês COMPLETO
(meses parciais são excluídos; piso de dados 2026-05; NÃO confie em `isCurrent`):

```json
{"dimensions":["ShbCategoriesMonthly.categoryId","ShbCategoriesMonthly.categoryName"],
 "measures":["ShbCategoriesMonthly.orderCount","ShbCategoriesMonthly.orderGmv","ShbCategoriesMonthly.orderGmvGrowth1m"],
 "filters":[{"member":"ShbCategoriesMonthly.level","operator":"equals","values":["1"]},
            {"member":"ShbCategoriesMonthly.orderGmv","operator":"gt","values":["0"]}],
 "timeDimensions":[{"dimension":"ShbCategoriesMonthly.date","dateRange":["<AAAA-MM-01>","<AAAA-MM-31>"]}],
 "order":[["ShbCategoriesMonthly.orderGmv","desc"]],"limit":25}
```

Rubrica dos `category_boosts` (só categorias da allowlist):
- crescimento m/m (`orderGmvGrowth1m`) > +10% → **1.3**
- maior GMV absoluto entre as da allowlist, ou crescimento 0 a +10% → **1.15**
- crescimento −10% a 0 → **1.1**; abaixo de −10% → **1.0** (sem boost)

## Passo 2 — Itens em aceleração e campeões de venda

Cubo `ShbMartItem`, duas consultas (dimensões: `itemId,itemName,categoryL1Name,categoryL2Name`;
medidas: `price,sold30Days,revenue30Days,salesTrend,reviewsRating`):

1. **Aceleração**: filtros `price` 20–1000, `sold30Days > 1000`, `salesTrend > 50`;
   ordem `salesTrend desc`; limit 25.
2. **Volume**: filtros `price` 20–1000, `sold30Days > 0`; ordem `sold30Days desc`; limit 25.

Selecione a união das duas listas, mantendo apenas `categoryL1Name` da allowlist
e `reviewsRating >= 4.6`. Rubrica dos boosts de `hot_items`:
- `sold30Days >= 20000` E `salesTrend > 100` → **1.5**
- `salesTrend > 10000` (aceleração extrema, volume menor) → **1.4**
- `sold30Days >= 10000` OU `salesTrend > 2000` → **1.3**
- demais selecionados → **1.2**

Grave o campo `reason` curto (ex.: `"creatina: 30k vendas/30d, trend +1929%"`).

## Passo 3 — Referência honesta de preço (`price_refs`) e mínimas (`price_floors`)

Estes dois campos são a **régua** do pipeline: `price_refs` traz a mediana (o
"de" que o post pode alegar, `pricing.verdict` modo A) e o **p25** (o topo do
quartil mais barato: o post só alega desconto quando o preço de hoje fica
ESTRITAMENTE abaixo dele, com janela ≥ 14 dias — regra do quartil, fase 5B);
a mediana também é o teto de publicação (`selection.max_above_ref`).
`price_floors` sustenta o selo de menor preço (estrito: preço ≤ mínima, com a
janela real no texto). Errar a referência para cima vira desconto inventado —
na dúvida, arredonde mediana e p25 para BAIXO.

Cubo `ShbModelsPricesDaily`. Fatos já verificados (não gaste consulta
redescobrindo):

- **Grão: uma linha por `(itemId, priceStart)` — é um INTERVALO, não um dia.**
  Colunas: `itemId`, `modelPrice` (em BRL), `priceStart`, `priceEnd`.
- **Sem measures**: consulte como linhas cruas, `order` por `priceStart`,
  `limit` máximo 100 — **pagine com `offset`** até vir página com menos de 100.
- `modelId` é sempre 0 e não carrega informação — ignore.
- Retenção ~365 dias, mas a coleta da Shopee começou ~maio/2026: item novo tem
  janela curta, e isso é honesto por construção (`window_days`).
- `equals` aceita vários valores: peça o histórico de um LOTE de ids numa
  consulta só.
- `ShbMartItem.itemId` é a MESMA chave que a API da Shopee devolve como
  `itemId`, e é ela que casa com `Offer.item_id` no pipeline. Não há conversão.

```json
{"dimensions":["ShbModelsPricesDaily.itemId","ShbModelsPricesDaily.modelPrice",
               "ShbModelsPricesDaily.priceStart","ShbModelsPricesDaily.priceEnd"],
 "filters":[{"member":"ShbModelsPricesDaily.itemId","operator":"equals",
             "values":["<id1>","<id2>","<id3>"]}],
 "order":[["ShbModelsPricesDaily.priceStart","asc"]],"limit":100,"offset":0}
```

Para cada item, restrito aos **últimos 90 dias**:

1. **Expanda cada intervalo em dias**: um intervalo vale
   `(priceEnd - priceStart + 1)` dias naquele `modelPrice`. Recorte o intervalo
   na borda da janela de 90 dias ANTES de contar os dias.
2. **`price_refs[<itemId>].ref_cents` = mediana PONDERADA POR DIAS** dessa
   expansão × 100 (inteiro, para baixo — com número par de dias, o MENOR dos
   dois centrais, nunca a média). Sem a ponderação, um preço que durou 1 dia
   pesa igual a um que durou 60 — é exatamente esse o buraco que deixa o "de"
   inflado do vendedor passar (item 9212570285: típico ~R$ 26 em 90 dias, e
   R$ 68,90 por UM dia em 22/08).
3. **`price_refs[<itemId>].p25_cents` = 25º percentil PONDERADO POR DIAS** da
   mesma expansão × 100 (inteiro, para baixo): ordene os dias por preço e
   pegue o preço do dia na posição `(n_dias − 1) // 4`. Um preço que ocupa
   ≥ 25% dos dias É o p25 — e hoje só entra no modo A se ficar abaixo dele.
4. `price_refs[<itemId>].window_days` = número de dias distintos cobertos
   (90, ou o tamanho real quando o item tem menos histórico). Item com
   **menos de 14 dias** cobertos fica fora de `price_refs` (nunca teria modo
   A) — e entrada gravada sem `p25_cents` carrega 0 no pipeline, com o mesmo
   efeito.
5. **`price_floors[<itemId>].min_price_cents` = menor `modelPrice` da janela**
   × 100 (inteiro); `window_days` = dias entre o `priceStart` mais antigo do
   item e hoje.
6. `window_days` é **obrigatório** nos dois mapas: ausente, o pipeline carrega
   **0** (não 90/365) — e com 0 não há selo ("últimos N dias" sem N) nem modo
   A (a janela nunca chega aos 14 dias). Escrever a janela medida é a única
   forma de a entrada valer alguma coisa.

Item sem nenhuma linha no cubo fica de fora dos dois mapas: sem referência o
pipeline ainda publica, só não alega desconto (modo B do post).

## Passo 4 — Gravar, validar, comparar, commitar

1. Escrever `data/watchlist.json`:
```json
{"generated_at": "<hoje AAAA-MM-DD>", "valid_days": 14,
 "category_boosts": {"<id>": 1.3},
 "hot_items": {"<itemId>": {"boost": 1.5, "reason": "..."}},
 "price_refs": {"<itemId>": {"ref_cents": 2590, "p25_cents": 2428, "window_days": 90}},
 "price_floors": {"<itemId>": {"min_price_cents": 1699, "window_days": 196}}}
```
2. Validar:
```
python -c "from afiliado.watchlist import load_watchlist; wl=load_watchlist('data/watchlist.json'); assert wl and not wl.is_stale(); sem_p25=[k for k,v in wl.price_refs.items() if v.p25_cents<=0]; print(len(wl.hot_items),'itens,',len(wl.price_refs),'referencias (',len(sem_p25),'sem p25 ),',len(wl.price_floors),'pisos')"
```
Seção malformada degrada para vazio SEM quebrar o resto do arquivo — por isso
confira os números impressos, e não só a ausência de erro.
3. Mostrar ao usuário um resumo do diff vs. a watchlist anterior: itens que
   entraram/saíram, boosts de categoria que mudaram, referências e pisos que
   mudaram. Ao citar vendas/GMV no resumo, diga que são **estimativas** do
   JoomPulse — é regra do próprio conector divulgar isso.
4. Commit: `chore: atualiza watchlist (dados JoomPulse de <data>)` terminando com
   `Co-Authored-By:` do modelo em uso. `data/joompulse_raw/` NÃO entra
   (gitignored). Se o repo tiver remote, perguntar antes de push.

## Notas

- Categoria da Shopee é mensal (lag ~31d) — os boosts de categoria só mudam de
  fato quando um novo mês fecha; o valor da atualização semanal está nos itens.
- Nunca inventar números: todo valor citado vem de uma linha de consulta.
- Vendas, GMV e trend do JoomPulse são **estimativas**, não números oficiais
  da Shopee — divulgue isso sempre que citá-los (regra do próprio JoomPulse).
- A referência (`price_refs`) tem precedência sobre o histórico próprio do
  `StateDB` (ver `pricing.enrich_offers`): uma referência errada aqui não é
  corrigida pelo pipeline, ela substitui o que ele mediria sozinho.
- Watchlist vencida perde só `category_boosts`/`hot_items`; `price_refs` e
  `price_floors` são fatos datados e continuam valendo (com a janela real no
  texto) até serem substituídos — o vencimento não troca a régua de número.
- Itens com histórico de preço < 60 dias ainda entram, e o selo dirá "últimos
  N dias" (a partir de 60, "M meses") — correto e honesto por construção
  (`window_days`).

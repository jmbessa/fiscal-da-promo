---
name: watchlist-refresh
description: Atualiza data/watchlist.json com dados frescos do JoomPulse (categorias em alta, itens em aceleração na Shopee, mínimas de preço). Rodar semanalmente. Requer o conector JoomPulse (claude.ai) autenticado na sessão.
---

# Atualização semanal da watchlist

Gera um novo `data/watchlist.json` consumido pelo pipeline (`afiliado run`) para
boost de ranking e selo "menor preço verificado". Validade: 14 dias — o chat de
operações avisa quando vencer.

## Pré-requisitos

- Conector **JoomPulse** disponível (ferramentas `mcp__claude_ai_JoomPulse__*`;
  carregue via ToolSearch se estiverem deferidas). Sem ele, avise o usuário e pare.
- Executar da raiz do repo. Leia `data/watchlist.json` atual para comparar depois.
- Regras de consulta: leia `pulse://rules` se for a primeira vez na sessão.
  Divulgue sempre que vendas/GMV são **estimativas** do JoomPulse (caveat #21).

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

## Passo 3 — Mínimas de preço (pisos)

Cubo `ShbModelsPricesDaily` (SEM medidas — linhas cruas), filtro `itemId equals`
com a lista de ids do Passo 2, dimensões `itemId,modelPrice,priceStart,priceEnd`,
ordem `itemId asc, priceStart asc`, limit 100 — **pagine com `offset` até vir
página com menos de 100 linhas**. Para cada item:
- `min_price_cents` = menor `modelPrice` observado × 100 (inteiro);
- `window_days` = dias entre o `priceStart` mais antigo do item e hoje.

## Passo 4 — Gravar, validar, comparar, commitar

1. Escrever `data/watchlist.json`:
```json
{"generated_at": "<hoje AAAA-MM-DD>", "valid_days": 14,
 "category_boosts": {"<id>": 1.3},
 "hot_items": {"<itemId>": {"boost": 1.5, "reason": "..."}},
 "price_floors": {"<itemId>": {"min_price_cents": 1699, "window_days": 196}}}
```
2. Validar:
```
python -c "from afiliado.watchlist import load_watchlist; wl=load_watchlist('data/watchlist.json'); assert wl and not wl.is_stale(); print(len(wl.hot_items),'itens ok')"
```
3. Mostrar ao usuário um resumo do diff vs. a watchlist anterior: itens que
   entraram/saíram, boosts de categoria que mudaram, pisos que baixaram.
4. Commit: `chore: atualiza watchlist (dados JoomPulse de <data>)` terminando com
   `Co-Authored-By:` do modelo em uso. Se o repo tiver remote, perguntar antes de push.

## Notas

- Categoria da Shopee é mensal (lag ~31d) — os boosts de categoria só mudam de
  fato quando um novo mês fecha; o valor da atualização semanal está nos itens.
- Nunca inventar números: todo valor citado vem de uma linha de consulta.
- Itens com histórico de preço < 30 dias ainda entram, mas o selo dirá "1 mês" —
  correto e honesto por construção (`window_days`).

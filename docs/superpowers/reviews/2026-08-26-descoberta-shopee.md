# Descoberta Shopee — quantas ofertas únicas a API entrega, e como buscá-las

Medição contra `https://open-api.affiliate.shopee.com.br/graphql` (`productOfferV2`)
em 2026-08-26, 01:46–01:47 (chamadas 1–52, tentativa interrompida) e 10:2x–10:38
(chamadas 53–147). **147 chamadas de 150**; 0,5 s entre chamadas; nenhum 429,
nenhum erro de assinatura. Latência das 137 chamadas que devolveram nós:
mín 594 ms, mediana 832 ms, máx 1490 ms.

Todos os números citam a chamada (`call N`) que os gerou. Log completo por
chamada em `calls.jsonl` (147 linhas), todos os nós em `nodes.jsonl` (6 841
linhas), esquema em `introspection.json`, apêndice no fim deste arquivo.
Scripts: `medir.py` (chamadas), `analise.py`, `relatorio_dados*.py` (offline).

Filtro de elegibilidade usado (igual ao pipeline): `_parse_node` do repo
(preço presente, `periodEndTime` não vencido), título+imagem+link presentes,
preço entre R$ 20,00 e R$ 1.000,00. "Em allowlist" = `productCatIds[0]` é uma
das 5 categorias configuradas.

## Resumo em seis linhas

1. A listagem por categoria é uma **janela de 40 páginas × 50 = 2 000 itens**
   (`hasNextPage: false` na p40, vazio na p41 — calls 75/126). `limit` máximo é 50
   (call 52). A config atual lê 2 páginas: **5 % da janela**.
2. As 5 janelas-raiz somam 10 000 itens com **54,6 % elegíveis** (1 473/2 700
   nós amostrados nas p1–10 das 5 categorias + p20/30/40 Beleza + p40 Saúde)
   → **≈ 5 460 itens elegíveis** só nas raízes, todos com ≥ 700 vendas.
3. **Cada subcategoria de nível 2 tem a sua própria janela de 2 000** (p40 cheia
   em 5 de 5 testadas — calls 130, 131, 143, 144, 145 — com 300/300 itens
   inéditos). São 43 subcategorias vistas nas 5 raízes.
4. `sortType` 1 e 6 devolvem o **mesmo conjunto** que 2; 3 e 4 (preço) dão 0
   elegíveis; 5 (comissão) traz itens inéditos mas **72 % com zero vendas**.
   `listType` 1 e 2 = mesmo conjunto que 0; 3 e 4 exigem `matchId`.
5. `keyword` + `productCatId`: 19,1 elegíveis inéditos em allowlist por chamada
   na p1 (762 em 40 chamadas), p2 continua rendendo (~40/chamada).
6. **A conta:** só com as raízes varridas até a p40, 5 460 elegíveis/mês →
   **182 posts/dia com dedupe de 30 dias**. 60/dia precisa de 1 800 únicos/mês:
   **alcançável com dedupe 30**, margem 3×. Com a config atual (244 elegíveis,
   calls 2–3/12–13/22–23/32–33/42–43) dá 8/dia a dedupe 30 e 35/dia a dedupe 7
   — **afrouxar o dedupe não resolve; profundidade resolve.**

## 1. Fatos da API (o que a assinatura e as bordas mostraram)

| Fato | Evidência |
|---|---|
| `productOfferV2` aceita `page, limit, sortType, listType, productCatId, keyword, matchId, itemId, shopId, isAMSOffer, isKeySeller`; devolve `nodes` e `pageInfo{page,limit,hasNextPage}` | call 1 (introspecção) — o cliente atual não pede `pageInfo` |
| `limit` máximo = 50 | call 52: `error [11001] ... the maximum limit is 50` |
| Janela de 40 páginas por (categoria, sortType) | Beleza sort=2: p10/20/30/40 cheias (calls 11, 53, 74, 75), `hasNextPage:false` na p40 (call 75), p41/50/100/200 vazias (calls 126, 54, 55, 56). Saúde: p40 cheia (call 128), p50 vazia (call 57). sort=5 Beleza: p40 com 41 nós (call 78), p41 vazia (call 127) |
| A janela é um teto, não o fim do catálogo | vendas medianas na raiz Beleza: p1 11 738 → p10 2 654 → p20 1 709 → p30 1 337 → p40 1 036 (mín 704) — calls 2, 11, 53, 74, 75. Na p40 ainda há item com 1 618 vendas |
| `sortType` 2 é respeitado (vendas desc., aproximado) | medianas caem monotonamente p1→p10 nas 5 categorias (calls 2–51); dentro da página a ordem não é estritamente monótona |
| `sortType` 1 e 6 = mesmo conjunto que 2 | calls 58–59 (sort=1): 0 novos em 100; calls 66–67 (sort=6): 0 novos em 100 |
| `sortType` 3 = preço desc., 4 = preço asc. — 0 elegíveis | calls 60–61: R$ 10 905 a R$ 874 719; calls 62–63: R$ 0,10 a R$ 1,00 |
| `sortType` 5 = comissão desc.; conjunto disjunto do sort=2 | calls 64–65, 76–78: 340 novos em 341; interseção sort2∩sort5 em Beleza = 0 (651 vs 241 itens). Mas 72 % dos elegíveis com `sales=0`, 0 % com ≥ 100 vendas; comissão mediana cai de 0,45 (p1) a 0,03 (p40) |
| `listType` 1 e 2 = mesmo conjunto que 0 | calls 68–71: 0 novos em 200 |
| `listType` 3 e 4 exigem `matchId` | calls 72–73: `regarding listType must contain matchId` |
| `isAMSOffer=true`, `isKeySeller=true`: sem efeito na p1 | calls 136–137: 0 novos, mesmo conjunto da p1 padrão |
| `productCatId` aceita nível 2 e nível 3 | calls 123–125 (nível 2), 147 (nível 3, 100896: 50 nós, 17 novos) |
| Subcategoria nível 2 tem janela própria de 2 000, disjunta da raiz nas páginas fundas | 100662 p10 (call 129) 50/50 novos; p40 (call 130) 50/50; 100663 p40 (call 131) 50/50; 100018 p40 (call 143) 50/50; 100002 p40 (call 144) 50/50; 100727 p40 (call 145) 50/50; `hasNextPage:false` em todas as p40. Na p1 o overlap com o top-500 da raiz é alto: 100663 50/50 já vistos (call 123), 100659 49/50 (call 124), 100662 8/50 (call 125) |
| `keyword` funciona; `productCatId` junto NÃO é filtro estrito | 168 dos 2 100 nós de keyword (8 %) vieram sem a categoria pedida em `productCatIds` — "legging" 41/50 (call 107) e 40/50 na p2 (call 133), "tênis" 27/50 (call 108), "lençol berço" 29/50 (call 118). Vão para 100017 (moda) e afins; o filtro de allowlist do pipeline os descarta |
| `keyword` sem `productCatId` ≈ com | "creatina" sem cat (call 134) ∩ com cat (call 83) = 49 de 50 |
| `keyword` pagina além da p1 | "halteres" p2 (call 132): 50 novos/42 elegíveis; "legging" p2 (call 133): 40 novos/39 elegíveis (39 fora da allowlist) |
| Estabilidade da p1 em ~8 h 50 min (01:47 → 10:37) | calls 2/12/22/32/42 vs 138/139/140/141/142: conjunto muda 1 item em 50 (2 em Mãe e Bebê); ordem muda em 28–43 posições de 50; preços diferentes em 0–3 itens por página |

## 2. Tabela por experimento

"Novos" = itemId nunca visto em nenhuma chamada anterior (ordem cronológica).
"Novos eleg." = novos que passam o filtro de elegibilidade (sem aplicar allowlist,
salvo onde indicado).

| Experimento | Chamadas | Nós | Novos | Novos eleg. | Novos eleg./chamada | Observação |
|---|---|---|---|---|---|---|
| Introspecção | 1 (call 1) | – | – | – | – | esquema salvo |
| Exp1 raiz sort=2 p1–10, 5 cats | 50 (calls 2–51) | 2 500 | 2 500 | 1 365 | 27,3 | 0 overlap entre categorias; todas p1–10 cheias |
| Exp1 `limit=100` | 1 (call 52) | 0 | 0 | 0 | 0 | erro 11001, máx 50 |
| Profundidade raiz sort=2 (p20/30/40 Beleza, p40 Saúde) | 4 (calls 53, 74, 75, 128) | 200 | 200 | 108 | 27,0 | páginas cheias, todas inéditas |
| Sondas vazias (p41/50/100/200 Beleza, p50 Saúde) | 5 (calls 126, 54, 55, 56, 57) | 0 | 0 | 0 | 0 | fim da janela |
| Exp2 sort=1 | 2 (58–59) | 100 | 0 | 0 | 0 | = sort 2 |
| Exp2 sort=3 | 2 (60–61) | 100 | 100 | 0 | 0 | tudo > R$ 1 000 |
| Exp2 sort=4 | 2 (62–63) | 100 | 99 | 0 | 0 | tudo ≤ R$ 1 |
| Exp2 sort=5 p1–2 | 2 (64–65) | 100 | 100 | 92 | 46,0 | mas vendas medianas = 0 |
| Exp2 sort=6 | 2 (66–67) | 100 | 0 | 0 | 0 | = sort 2 |
| sort=5 profundidade (p10/20/40/41 Beleza) + p1 nas outras 4 cats | 8 (76–82, 127) | 341 | 340 | 296 | 37,0 | 72 % dos elegíveis com 0 vendas |
| Exp3 list=1 | 2 (68–69) | 100 | 0 | 0 | 0 | = list 0 |
| Exp3 list=2 | 2 (70–71) | 100 | 0 | 0 | 0 | = list 0 |
| Exp3 list=3, list=4 | 2 (72–73) | 0 | 0 | 0 | 0 | exigem matchId |
| Exp4 keyword+cat p1, 8 termos × 5 cats | 40 (83–122) | 2 000 | 1 170 | 845 (**762 em allowlist**) | 21,1 (**19,1**) | 70 % elegíveis; mín "maquiagem" 0 novos (call 96), máx "halteres" 50/43 (call 114) |
| keyword+cat p2 ("halteres", "legging") | 2 (132–133) | 100 | 90 | 81 (42 em allowlist) | 40,5 (21) | "legging" vaza para moda |
| keyword sem cat ("creatina") | 1 (134) | 50 | 1 | 1 | 1 | mesmo conjunto do call 83 |
| keyword+cat sort=5 ("creatina") | 1 (135) | 50 | 49 | 49 | 49 | 60 % com 0 vendas |
| Exp5 subcat nível 2 p1 (100663, 100659, 100662) | 3 (123–125) | 150 | 43 | 10 | 3,3 | p1 ≈ top da raiz |
| Subcat nível 2 p10/p40 (100662×2, 100663, 100018, 100002, 100727) | 6 (129–131, 143–145) | 300 | 300 | 186 | 31,0 | 62 % elegíveis; vendas medianas 156–697 |
| Subcat nível 2 sort=5 p1 (100662) | 1 (146) | 50 | 49 | 33 | 33 | 85 % com 0 vendas |
| Subcat nível 3 p1 (100896) | 1 (147) | 50 | 17 | 16 | 16 | 33/50 já no top da raiz |
| Flags isAMSOffer / isKeySeller | 2 (136–137) | 100 | 0 | 0 | 0 | sem efeito |
| Estabilidade p1 refetch, 5 cats | 5 (138–142) | 250 | 1 | 1 | 0,2 | ~8 h 50 min depois |
| **Total** | **147** | **6 841** | **5 059** | **3 083** | | |

Elegibilidade por origem (todos os nós, não só novos): raiz sort=2 p1–10 55 %
(1 365/2 500); raiz p20–40 54 % (108/200); keyword+cat p1 70 % (1 395/2 000);
subcat p10/p40 62 % (186/300); sort=5 87–92 % — mas ver vendas. O único motivo
de inelegibilidade encontrado foi preço (nenhum nó sem título/imagem/link,
nenhum `periodEndTime` vencido): 45 % da raiz custa < R$ 20 (Beleza 293/503,
Casa 283/500, Esportes 159/500, Mãe e Bebê 219/497, Saúde 180/500 — calls 2–51).

## 3. União total: itens únicos vistos em 147 chamadas

| Categoria raiz (`productCatIds[0]`) | Únicos | Elegíveis | % | Elegíveis com ≥ 100 vendas |
|---|---|---|---|---|
| Beleza 100630 | 1 550 | 738 | 48 % | 494 |
| Casa 100636 | 826 | 420 | 51 % | 385 |
| Saúde 100001 | 953 | 689 | 72 % | 589 |
| Esportes 100637 | 826 | 624 | 76 % | 559 |
| Mãe e Bebê 100632 | 779 | 499 | 64 % | 453 |
| Fora da allowlist (100017 moda 84, 100532 19, 100633 7, outras 15) | 125 | 113 | | |
| **Total** | **5 059** | **3 083** | 61 % | |
| **Total em allowlist** | **4 934** | **2 970** | 60 % | **2 480** (≥ 1 venda: 2 633; ≥ 1 000: 1 774) |

Beleza tem mais únicos porque foi a categoria das sondas (sort 3/4/5,
subcategorias, profundidade). A diferença 2 970 → 2 633 (≥ 1 venda) é quase toda
sort=5 (388 + 50 + 33 elegíveis, calls 64–65, 76–82, 135, 146).

## 4. Onde está o rendimento (elegíveis inéditos em allowlist por chamada)

Ordenado por rendimento útil — descartando sort=5, que rende muito em contagem e
nada em vendas:

| Fonte | Novos eleg./chamada | Vendas dos elegíveis (mediana; % ≥ 100) | Chamadas |
|---|---|---|---|
| Raiz sort=2, página nunca lida (p1–40) | 27,3 (p1–10), 27,0 (p20–40) | 2 363; 100 % / 1 118; 100 % | 2–51, 53, 74, 75, 128 |
| Subcategoria nível 2, p10–40 | 31,0 | 221; 99 % | 129–131, 143–145 |
| keyword+cat p2 (boa keyword) | 42 ("halteres", call 132) | 1 787; 83 % | 132 |
| keyword+cat p1 | 19,1 (varia 0–43 por termo) | 1 570; 100 % | 83–122 |
| Subcategoria nível 3 p1 | 16 | 1 512; 100 % | 147 |
| Subcategoria nível 2 p1 | 3,3 | – | 123–125 |
| Raiz p1 relida no mesmo dia | 0,2 | – | 138–142 |
| sort 1/6, list 1/2, flags | 0 | – | 58–59, 66–71, 136–137 |
| sort=5 (qualquer combinação) | 33–49 | **0; 0 %** (72–85 % com 0 vendas) | 64–65, 76–82, 135, 146 |
| sort 3/4 | 0 | – | 60–63 |

Leitura: **página nova = ~27–31 elegíveis com vendas reais**, seja da raiz
(p11–40, nunca lidas hoje) ou de subcategoria. Keyword p1 rende menos (19) porque
metade dos resultados já está no top da raiz (ex.: "maquiagem" 50/50 já vistos,
call 96; "organizador" 46/50, call 103; "dry fit" 44/50, call 113) — o valor
das keywords está nas páginas 2+ dos termos bons e em termos que a raiz não
cobre ("halteres" 0/50 no top-500, call 114; "lixeira" 4/50, call 101; "panela"
4/50, call 104).

## 5. Recomendação de config de descoberta (≤ 30 chamadas por run, 192 runs/dia)

Princípio: com 192 runs/dia, **um run não precisa ver tudo; o dia precisa**.
Cada run lê uma fatia diferente e um cursor persistido faz o dia inteiro
percorrer o espaço. A p1 muda ~2 % em 9 h (calls 138–142), então reler a mesma
página a cada 5 min é desperdício.

```yaml
shopee:
  page_size: 50            # máximo aceito (call 52)
  sort_types: [2]          # 1 e 6 = mesmo conjunto; 3/4 = 0 elegíveis; 5 = 72 % sem vendas
  list_type: 0             # 1 e 2 = mesmo conjunto; 3/4 exigem matchId
  pages: 40                # janela real; parar quando pageInfo.hasNextPage == false
  discovery:
    calls_per_run: 27      # 5 raiz + 20 subcategoria + 2 keyword
    cursor_file: data/shopee_cursor.json   # {raiz_page, subcat_idx, kw_idx}; run k continua de onde k-1 parou
    root:                  # 5 categorias × páginas 1..40 = 200 fatias
      per_run: 5           # run k lê a página ((k-1) mod 40)+1 das 5 categorias → raiz completa a cada 40 runs (3 h 20 min)
    subcats:               # nível 2 × páginas 1..40
      per_run: 20          # 20 subcats × 40 páginas = 800 fatias → ciclo de 40 runs
      ids:                 # as 20 com ≥ 25 itens no top-500 da raiz (contagem entre parênteses, calls 2–51)
        100630: [100663, 100659, 100662, 100664, 100658, 102002]        # Beleza (133, 80, 75, 71, 59, 37)
        100636: [100716, 100717, 100711, 100715, 100710, 100709, 100721, 100718]  # Casa (121, 79, 57, 56, 44, 30, 29, 25)
        100001: [100018, 100002, 100019]                                 # Saúde (190, 171, 133)
        100637: [100727, 100725, 100726, 100728]                         # Esportes (218, 160, 65, 57)
        100632: [100684, 100678, 100675, 100679, 100683]                 # Mãe e Bebê (253, 65, 48, 46, 27)
      skip_pages: [1]      # p1 da subcat ≈ top da raiz (calls 123–124: 0–1 novos); começar na p2
    keywords:              # 40 termos × páginas 1..2 = 80 fatias
      per_run: 2           # ciclo de 40 runs
      pages: 2
      by_category:
        100001: [creatina, whey, colágeno, massageador, vitamina, balança, termômetro, escova dental]
        100630: [sérum, protetor solar, shampoo, perfume, depilador, batom, hidratante, maquiagem]
        100636: [lixeira, panela, protetor colchão, mop, lençol, cortina, travesseiro, organizador]
        100637: [halteres, legging, guarda chuva, camisa térmica, tênis, conjunto fitness, bicicleta, dry fit]
        100632: [patinete, mamadeira, carrinho bebê, toalha umedecida, lençol berço, mordedor, brinquedo sensorial, fralda]
      # manter o filtro de allowlist em productCatIds[0]: 8 % dos nós de keyword vêm de outra categoria (calls 107, 108, 118, 133)
```

Consequências: 27 × 192 = 5 184 chamadas/dia (o cliente atual faz 1 920);
cada fatia do espaço é relida a cada ~3 h 20 min (7 vezes/dia — mais que o
suficiente dado o churn medido; se a cota diária da API for um problema,
`per_run` 3/10/1 dá o mesmo ciclo em 6 h 40 min com 2 688 chamadas/dia). As
listas de keywords são as 8 medidas por categoria, ordenadas por
elegíveis inéditos (calls 83–122); "maquiagem", "organizador", "dry fit" e
"fralda" ficam por último porque renderam 0–14.

Dois ajustes de código que a medição pede (para o outro agente, não feitos aqui):
pedir `pageInfo { hasNextPage }` e parar nele — hoje o `break` só dispara na
página vazia (p41), 1 chamada perdida por (categoria, sort); e aceitar
`keyword` no `productOfferV2` (campo `String`, call 1).

## 6. A conta que o dono pediu

Definições: "pool" = itens únicos elegíveis que a config consegue ver num mês;
posts/dia sustentáveis com dedupe de N dias = pool ÷ N (cada item no máximo uma
vez por N dias). Sem contar rotação (itens que entram nas janelas ao longo do
mês) — o que só soma.

**Pool com a config recomendada, por parcela:**

| Parcela | Cálculo | Itens elegíveis | Grau de medição |
|---|---|---|---|
| Raízes, p1–40, 5 categorias | 10 000 × 54,6 % (1 473/2 700 nós — calls 2–51, 53, 74, 75, 128) | **≈ 5 460** | bem medido: 4 profundidades, 2 categorias na borda |
| Subcategorias nível 2 (20 ids), p2–40 | 5 verificadas com p40 cheia e 62 % elegíveis (calls 129–131, 143–145): 5 × 1 950 × 0,62 ≈ 6 000; as outras 15: entre 0 e 15 × 1 950 × 0,62 ≈ 18 100 | **≥ 6 000**, até ≈ 24 000 | parcial: só 6 páginas fundas lidas; overlap com a raiz medido = 0/300 nas p10/p40 e 50–100 % na p1 |
| Keywords, 40 termos × p1–2 | p1: 762 em allowlist (calls 83–122); p2: ~21–42/chamada (calls 132–133) → 762 + 40 × ~25 | **≈ 1 700** | p1 bem medido; p2 só 2 termos |
| **Pool/mês** | | **piso 5 460 (só raízes); ≈ 13 000 com o que foi verificado; até ≈ 31 000** | |

**Posts/dia sustentáveis:**

| Config | Pool elegível/mês | Dedupe 30 dias | Dedupe 7 dias |
|---|---|---|---|
| Atual (2 páginas × 5 cats, sort 2, sem rotação) | 244 (calls 2–3, 12–13, 22–23, 32–33, 42–43) + churn de ~2 %/9 h | **8/dia** | 35/dia |
| Só raízes até p40 (200 chamadas por varredura = 7 runs) | 5 460 | **182/dia** | 780/dia |
| Recomendada, parcela verificada | ≈ 13 000 | ≈ 430/dia | ≈ 1 860/dia |

**60/dia é alcançável?** Sim, **com dedupe de 30 dias**, sem precisar de 7:
60 × 30 = 1 800 únicos/mês, e só as raízes lidas até a p40 dão 5 460 (3×). Com a
config atual não é alcançável nem a dedupe 7 (precisa de 420 únicos em
rotação; há 244). O gargalo é profundidade de leitura, não o dedupe.

Margem para os filtros que não medi (desconto mínimo real, selo, rating etc.):
mesmo que cortem 2/3 dos elegíveis, as raízes ainda dão 1 820/mês → 60/dia a
dedupe 30 fica no limite; aí as subcategorias (≥ 6 000 verificados) são a
folga. Qualidade por profundidade: raiz p40 mín 704 vendas (call 75); subcat
p40 mín 53–221 vendas (calls 130, 131, 143, 144, 145) — se o pipeline exigir
≥ 100 vendas, a raiz passa inteira e as subcats perdem uma fração pequena
(mediana 156–380).

## 7. O que uma tarde de medição não diz

- **Rotação real das janelas.** Medi a p1 duas vezes com ~8 h 50 min de
  intervalo: 1–2 itens novos em 50 por página (calls 138–142). Não sei quantos
  itens entram/saem da janela de 2 000 por dia nem por semana; a estimativa de
  pool acima é estática (piso). Só um cursor rodando alguns dias responde.
- **Sazonalidade.** Um dia de agosto. Black Friday/Natal mudam a janela e a
  fração < R$ 20.
- **Cota diária da API.** 147 chamadas em ~1 h sem 429. 5 184/dia não foi testado;
  o cliente atual já faz 1 920/dia.
- **Subcategorias: 5 de 43 verificadas na borda.** As 20 recomendadas foram
  escolhidas por tamanho no top-500 da raiz; as pequenas (ex.: 100008 Saúde, 6
  itens) provavelmente não enchem 40 páginas. Overlap subcat×raiz medido só em
  9 páginas (p1: alto; p10/p40: zero).
- **Keywords: p2 medida em 2 termos.** Não sei se keyword também tem janela de
  40 páginas nem quantas páginas cada termo sustenta.
- **Estabilidade do teto de 40 páginas.** É comportamento observado, não
  documentado; pode mudar.
- **Conversão.** Itens da p40 (≈ 1 000 vendas) e de subcategoria (≈ 200) vendem
  menos que os da p1 (≈ 10 000); a medição conta elegíveis, não cliques.
- **Filtros posteriores do pipeline** (desconto real, selo, rating, dedupe
  cruzado com Meli) não foram aplicados.

## Apêndice — as 147 chamadas

| call | exp | parâmetros | nós | novos | novos eleg. | eleg. | ms | hasNext | erro |
|---|---|---|---|---|---|---|---|---|---|
| 1 | intro | introspecção __type(Query) |  |  |  |  | 481 |  |  |
| 2 | exp1 | productCatId=100630 sortType=2 listType=0 page=1 limit=50 | 50 | 50 | 22 | 22 | 1009 | true |  |
| 3 | exp1 | productCatId=100630 sortType=2 listType=0 page=2 limit=50 | 50 | 50 | 20 | 20 | 680 | true |  |
| 4 | exp1 | productCatId=100630 sortType=2 listType=0 page=3 limit=50 | 50 | 50 | 22 | 22 | 744 | true |  |
| 5 | exp1 | productCatId=100630 sortType=2 listType=0 page=4 limit=50 | 50 | 50 | 20 | 20 | 685 | true |  |
| 6 | exp1 | productCatId=100630 sortType=2 listType=0 page=5 limit=50 | 50 | 50 | 19 | 19 | 653 | true |  |
| 7 | exp1 | productCatId=100630 sortType=2 listType=0 page=6 limit=50 | 50 | 50 | 21 | 21 | 672 | true |  |
| 8 | exp1 | productCatId=100630 sortType=2 listType=0 page=7 limit=50 | 50 | 50 | 24 | 24 | 736 | true |  |
| 9 | exp1 | productCatId=100630 sortType=2 listType=0 page=8 limit=50 | 50 | 50 | 21 | 21 | 725 | true |  |
| 10 | exp1 | productCatId=100630 sortType=2 listType=0 page=9 limit=50 | 50 | 50 | 20 | 20 | 659 | true |  |
| 11 | exp1 | productCatId=100630 sortType=2 listType=0 page=10 limit=50 | 50 | 50 | 21 | 21 | 738 | true |  |
| 12 | exp1 | productCatId=100636 sortType=2 listType=0 page=1 limit=50 | 50 | 50 | 13 | 13 | 726 | true |  |
| 13 | exp1 | productCatId=100636 sortType=2 listType=0 page=2 limit=50 | 50 | 50 | 19 | 19 | 696 | true |  |
| 14 | exp1 | productCatId=100636 sortType=2 listType=0 page=3 limit=50 | 50 | 50 | 23 | 23 | 714 | true |  |
| 15 | exp1 | productCatId=100636 sortType=2 listType=0 page=4 limit=50 | 50 | 50 | 22 | 22 | 698 | true |  |
| 16 | exp1 | productCatId=100636 sortType=2 listType=0 page=5 limit=50 | 50 | 50 | 26 | 26 | 740 | true |  |
| 17 | exp1 | productCatId=100636 sortType=2 listType=0 page=6 limit=50 | 50 | 50 | 24 | 24 | 662 | true |  |
| 18 | exp1 | productCatId=100636 sortType=2 listType=0 page=7 limit=50 | 50 | 50 | 19 | 19 | 751 | true |  |
| 19 | exp1 | productCatId=100636 sortType=2 listType=0 page=8 limit=50 | 50 | 50 | 23 | 23 | 872 | true |  |
| 20 | exp1 | productCatId=100636 sortType=2 listType=0 page=9 limit=50 | 50 | 50 | 26 | 26 | 805 | true |  |
| 21 | exp1 | productCatId=100636 sortType=2 listType=0 page=10 limit=50 | 50 | 50 | 22 | 22 | 707 | true |  |
| 22 | exp1 | productCatId=100001 sortType=2 listType=0 page=1 limit=50 | 50 | 50 | 28 | 28 | 633 | true |  |
| 23 | exp1 | productCatId=100001 sortType=2 listType=0 page=2 limit=50 | 50 | 50 | 35 | 35 | 671 | true |  |
| 24 | exp1 | productCatId=100001 sortType=2 listType=0 page=3 limit=50 | 50 | 50 | 29 | 29 | 700 | true |  |
| 25 | exp1 | productCatId=100001 sortType=2 listType=0 page=4 limit=50 | 50 | 50 | 32 | 32 | 705 | true |  |
| 26 | exp1 | productCatId=100001 sortType=2 listType=0 page=5 limit=50 | 50 | 50 | 28 | 28 | 708 | true |  |
| 27 | exp1 | productCatId=100001 sortType=2 listType=0 page=6 limit=50 | 50 | 50 | 30 | 30 | 648 | true |  |
| 28 | exp1 | productCatId=100001 sortType=2 listType=0 page=7 limit=50 | 50 | 50 | 33 | 33 | 668 | true |  |
| 29 | exp1 | productCatId=100001 sortType=2 listType=0 page=8 limit=50 | 50 | 50 | 39 | 39 | 908 | true |  |
| 30 | exp1 | productCatId=100001 sortType=2 listType=0 page=9 limit=50 | 50 | 50 | 35 | 35 | 706 | true |  |
| 31 | exp1 | productCatId=100001 sortType=2 listType=0 page=10 limit=50 | 50 | 50 | 31 | 31 | 715 | true |  |
| 32 | exp1 | productCatId=100637 sortType=2 listType=0 page=1 limit=50 | 50 | 50 | 31 | 31 | 812 | true |  |
| 33 | exp1 | productCatId=100637 sortType=2 listType=0 page=2 limit=50 | 50 | 50 | 32 | 32 | 739 | true |  |
| 34 | exp1 | productCatId=100637 sortType=2 listType=0 page=3 limit=50 | 50 | 50 | 34 | 34 | 726 | true |  |
| 35 | exp1 | productCatId=100637 sortType=2 listType=0 page=4 limit=50 | 50 | 50 | 36 | 36 | 857 | true |  |
| 36 | exp1 | productCatId=100637 sortType=2 listType=0 page=5 limit=50 | 50 | 50 | 34 | 34 | 782 | true |  |
| 37 | exp1 | productCatId=100637 sortType=2 listType=0 page=6 limit=50 | 50 | 50 | 33 | 33 | 740 | true |  |
| 38 | exp1 | productCatId=100637 sortType=2 listType=0 page=7 limit=50 | 50 | 50 | 34 | 34 | 745 | true |  |
| 39 | exp1 | productCatId=100637 sortType=2 listType=0 page=8 limit=50 | 50 | 50 | 37 | 37 | 753 | true |  |
| 40 | exp1 | productCatId=100637 sortType=2 listType=0 page=9 limit=50 | 50 | 50 | 28 | 28 | 714 | true |  |
| 41 | exp1 | productCatId=100637 sortType=2 listType=0 page=10 limit=50 | 50 | 50 | 41 | 41 | 808 | true |  |
| 42 | exp1 | productCatId=100632 sortType=2 listType=0 page=1 limit=50 | 50 | 50 | 21 | 21 | 645 | true |  |
| 43 | exp1 | productCatId=100632 sortType=2 listType=0 page=2 limit=50 | 50 | 50 | 23 | 23 | 677 | true |  |
| 44 | exp1 | productCatId=100632 sortType=2 listType=0 page=3 limit=50 | 50 | 50 | 28 | 28 | 641 | true |  |
| 45 | exp1 | productCatId=100632 sortType=2 listType=0 page=4 limit=50 | 50 | 50 | 30 | 30 | 617 | true |  |
| 46 | exp1 | productCatId=100632 sortType=2 listType=0 page=5 limit=50 | 50 | 50 | 31 | 31 | 662 | true |  |
| 47 | exp1 | productCatId=100632 sortType=2 listType=0 page=6 limit=50 | 50 | 50 | 29 | 29 | 701 | true |  |
| 48 | exp1 | productCatId=100632 sortType=2 listType=0 page=7 limit=50 | 50 | 50 | 28 | 28 | 594 | true |  |
| 49 | exp1 | productCatId=100632 sortType=2 listType=0 page=8 limit=50 | 50 | 50 | 32 | 32 | 685 | true |  |
| 50 | exp1 | productCatId=100632 sortType=2 listType=0 page=9 limit=50 | 50 | 50 | 27 | 27 | 739 | true |  |
| 51 | exp1 | productCatId=100632 sortType=2 listType=0 page=10 limit=50 | 50 | 50 | 29 | 29 | 668 | true |  |
| 52 | exp1 | productCatId=100630 sortType=2 listType=0 page=1 limit=100 | 0 | 0 | 0 | 0 | 168 |  | error [11001]: Params Error : Exceeded the maximum number of page limit, the maximum limit is 50 |
| 53 | extra | productCatId=100630 sortType=2 listType=0 page=20 limit=50 | 50 | 50 | 26 | 26 | 1154 | true |  |
| 54 | extra | productCatId=100630 sortType=2 listType=0 page=50 limit=50 | 0 | 0 | 0 | 0 | 521 | false |  |
| 55 | extra | productCatId=100630 sortType=2 listType=0 page=100 limit=50 | 0 | 0 | 0 | 0 | 566 | false |  |
| 56 | extra | productCatId=100630 sortType=2 listType=0 page=200 limit=50 | 0 | 0 | 0 | 0 | 506 | false |  |
| 57 | extra | productCatId=100001 sortType=2 listType=0 page=50 limit=50 | 0 | 0 | 0 | 0 | 504 | false |  |
| 58 | exp2 | productCatId=100630 sortType=1 listType=0 page=1 limit=50 | 50 | 0 | 0 | 24 | 1229 | true |  |
| 59 | exp2 | productCatId=100630 sortType=1 listType=0 page=2 limit=50 | 50 | 0 | 0 | 20 | 818 | true |  |
| 60 | exp2 | productCatId=100630 sortType=3 listType=0 page=1 limit=50 | 50 | 50 | 0 | 0 | 1119 | true |  |
| 61 | exp2 | productCatId=100630 sortType=3 listType=0 page=2 limit=50 | 50 | 50 | 0 | 0 | 1303 | true |  |
| 62 | exp2 | productCatId=100630 sortType=4 listType=0 page=1 limit=50 | 50 | 49 | 0 | 0 | 1021 | true |  |
| 63 | exp2 | productCatId=100630 sortType=4 listType=0 page=2 limit=50 | 50 | 50 | 0 | 0 | 1178 | true |  |
| 64 | exp2 | productCatId=100630 sortType=5 listType=0 page=1 limit=50 | 50 | 50 | 47 | 47 | 811 | true |  |
| 65 | exp2 | productCatId=100630 sortType=5 listType=0 page=2 limit=50 | 50 | 50 | 45 | 45 | 1000 | true |  |
| 66 | exp2 | productCatId=100630 sortType=6 listType=0 page=1 limit=50 | 50 | 0 | 0 | 24 | 1120 | true |  |
| 67 | exp2 | productCatId=100630 sortType=6 listType=0 page=2 limit=50 | 50 | 0 | 0 | 20 | 1158 | true |  |
| 68 | exp3 | productCatId=100630 sortType=2 listType=1 page=1 limit=50 | 50 | 0 | 0 | 21 | 1119 | true |  |
| 69 | exp3 | productCatId=100630 sortType=2 listType=1 page=2 limit=50 | 50 | 0 | 0 | 22 | 890 | true |  |
| 70 | exp3 | productCatId=100630 sortType=2 listType=2 page=1 limit=50 | 50 | 0 | 0 | 21 | 740 | true |  |
| 71 | exp3 | productCatId=100630 sortType=2 listType=2 page=2 limit=50 | 50 | 0 | 0 | 22 | 825 | true |  |
| 72 | exp3 | productCatId=100630 sortType=2 listType=3 page=1 limit=50 | 0 | 0 | 0 | 0 | 184 |  | error [11001]: Params Error : regarding listType must contain matchId |
| 73 | exp3 | productCatId=100630 sortType=2 listType=4 page=1 limit=50 | 0 | 0 | 0 | 0 | 216 |  | error [11001]: Params Error : regarding listType must contain matchId |
| 74 | extra | productCatId=100630 sortType=2 listType=0 page=30 limit=50 | 50 | 50 | 23 | 23 | 1304 | true |  |
| 75 | extra | productCatId=100630 sortType=2 listType=0 page=40 limit=50 | 50 | 50 | 25 | 25 | 730 | false |  |
| 76 | extra | productCatId=100630 sortType=5 listType=0 page=10 limit=50 | 50 | 50 | 42 | 42 | 812 | true |  |
| 77 | extra | productCatId=100630 sortType=5 listType=0 page=20 limit=50 | 50 | 50 | 46 | 46 | 894 | true |  |
| 78 | extra | productCatId=100630 sortType=5 listType=0 page=40 limit=50 | 41 | 41 | 31 | 31 | 810 | false |  |
| 79 | extra | productCatId=100636 sortType=5 listType=0 page=1 limit=50 | 50 | 50 | 35 | 35 | 754 | true |  |
| 80 | extra | productCatId=100001 sortType=5 listType=0 page=1 limit=50 | 50 | 50 | 49 | 49 | 760 | true |  |
| 81 | extra | productCatId=100637 sortType=5 listType=0 page=1 limit=50 | 50 | 50 | 47 | 47 | 850 | true |  |
| 82 | extra | productCatId=100632 sortType=5 listType=0 page=1 limit=50 | 50 | 49 | 46 | 46 | 1489 | true |  |
| 83 | exp4 | productCatId=100001 keyword=creatina sortType=2 listType=0 page=1 limit=50 | 50 | 24 | 24 | 48 | 1124 | true |  |
| 84 | exp4 | productCatId=100001 keyword=whey sortType=2 listType=0 page=1 limit=50 | 50 | 19 | 18 | 49 | 852 | true |  |
| 85 | exp4 | productCatId=100001 keyword=colágeno sortType=2 listType=0 page=1 limit=50 | 50 | 30 | 29 | 47 | 906 | true |  |
| 86 | exp4 | productCatId=100001 keyword=escova dental sortType=2 listType=0 page=1 limit=50 | 50 | 37 | 9 | 10 | 1004 | true |  |
| 87 | exp4 | productCatId=100001 keyword=balança sortType=2 listType=0 page=1 limit=50 | 50 | 19 | 13 | 41 | 857 | true |  |
| 88 | exp4 | productCatId=100001 keyword=vitamina sortType=2 listType=0 page=1 limit=50 | 50 | 23 | 20 | 39 | 816 | true |  |
| 89 | exp4 | productCatId=100001 keyword=massageador sortType=2 listType=0 page=1 limit=50 | 50 | 28 | 26 | 43 | 876 | true |  |
| 90 | exp4 | productCatId=100001 keyword=termômetro sortType=2 listType=0 page=1 limit=50 | 50 | 43 | 18 | 22 | 1020 | true |  |
| 91 | exp4 | productCatId=100630 keyword=shampoo sortType=2 listType=0 page=1 limit=50 | 50 | 30 | 20 | 34 | 759 | true |  |
| 92 | exp4 | productCatId=100630 keyword=protetor solar sortType=2 listType=0 page=1 limit=50 | 50 | 35 | 28 | 41 | 1342 | true |  |
| 93 | exp4 | productCatId=100630 keyword=sérum sortType=2 listType=0 page=1 limit=50 | 50 | 38 | 32 | 43 | 843 | true |  |
| 94 | exp4 | productCatId=100630 keyword=hidratante sortType=2 listType=0 page=1 limit=50 | 50 | 13 | 8 | 28 | 788 | true |  |
| 95 | exp4 | productCatId=100630 keyword=depilador sortType=2 listType=0 page=1 limit=50 | 50 | 25 | 11 | 24 | 817 | true |  |
| 96 | exp4 | productCatId=100630 keyword=maquiagem sortType=2 listType=0 page=1 limit=50 | 50 | 0 | 0 | 10 | 1006 | true |  |
| 97 | exp4 | productCatId=100630 keyword=perfume sortType=2 listType=0 page=1 limit=50 | 50 | 25 | 20 | 41 | 987 | true |  |
| 98 | exp4 | productCatId=100630 keyword=batom sortType=2 listType=0 page=1 limit=50 | 50 | 35 | 8 | 9 | 832 | true |  |
| 99 | exp4 | productCatId=100636 keyword=lençol sortType=2 listType=0 page=1 limit=50 | 50 | 28 | 18 | 29 | 1104 | true |  |
| 100 | exp4 | productCatId=100636 keyword=protetor colchão sortType=2 listType=0 page=1 limit=50 | 50 | 40 | 33 | 40 | 1086 | true |  |
| 101 | exp4 | productCatId=100636 keyword=lixeira sortType=2 listType=0 page=1 limit=50 | 50 | 46 | 36 | 40 | 1008 | true |  |
| 102 | exp4 | productCatId=100636 keyword=cortina sortType=2 listType=0 page=1 limit=50 | 50 | 36 | 15 | 22 | 1034 | true |  |
| 103 | exp4 | productCatId=100636 keyword=organizador sortType=2 listType=0 page=1 limit=50 | 50 | 4 | 0 | 13 | 795 | true |  |
| 104 | exp4 | productCatId=100636 keyword=panela sortType=2 listType=0 page=1 limit=50 | 50 | 46 | 27 | 29 | 919 | true |  |
| 105 | exp4 | productCatId=100636 keyword=travesseiro sortType=2 listType=0 page=1 limit=50 | 50 | 8 | 8 | 32 | 1027 | true |  |
| 106 | exp4 | productCatId=100636 keyword=mop sortType=2 listType=0 page=1 limit=50 | 50 | 36 | 21 | 26 | 833 | true |  |
| 107 | exp4 | productCatId=100637 keyword=legging sortType=2 listType=0 page=1 limit=50 | 50 | 41 | 41 | 50 | 990 | true |  |
| 108 | exp4 | productCatId=100637 keyword=tênis sortType=2 listType=0 page=1 limit=50 | 50 | 27 | 26 | 49 | 908 | true |  |
| 109 | exp4 | productCatId=100637 keyword=camisa térmica sortType=2 listType=0 page=1 limit=50 | 50 | 36 | 31 | 41 | 1081 | true |  |
| 110 | exp4 | productCatId=100637 keyword=conjunto fitness sortType=2 listType=0 page=1 limit=50 | 50 | 21 | 21 | 50 | 1000 | true |  |
| 111 | exp4 | productCatId=100637 keyword=bicicleta sortType=2 listType=0 page=1 limit=50 | 50 | 26 | 18 | 35 | 914 | true |  |
| 112 | exp4 | productCatId=100637 keyword=guarda chuva sortType=2 listType=0 page=1 limit=50 | 50 | 41 | 36 | 44 | 886 | true |  |
| 113 | exp4 | productCatId=100637 keyword=dry fit sortType=2 listType=0 page=1 limit=50 | 50 | 6 | 5 | 43 | 979 | true |  |
| 114 | exp4 | productCatId=100637 keyword=halteres sortType=2 listType=0 page=1 limit=50 | 50 | 50 | 43 | 43 | 880 | true |  |
| 115 | exp4 | productCatId=100632 keyword=mordedor sortType=2 listType=0 page=1 limit=50 | 50 | 31 | 10 | 14 | 1128 | true |  |
| 116 | exp4 | productCatId=100632 keyword=toalha umedecida sortType=2 listType=0 page=1 limit=50 | 50 | 27 | 25 | 44 | 889 | true |  |
| 117 | exp4 | productCatId=100632 keyword=brinquedo sensorial sortType=2 listType=0 page=1 limit=50 | 50 | 26 | 15 | 26 | 798 | true |  |
| 118 | exp4 | productCatId=100632 keyword=lençol berço sortType=2 listType=0 page=1 limit=50 | 50 | 40 | 23 | 28 | 1407 | true |  |
| 119 | exp4 | productCatId=100632 keyword=mamadeira sortType=2 listType=0 page=1 limit=50 | 50 | 41 | 31 | 34 | 842 | true |  |
| 120 | exp4 | productCatId=100632 keyword=fralda sortType=2 listType=0 page=1 limit=50 | 50 | 17 | 14 | 43 | 849 | true |  |
| 121 | exp4 | productCatId=100632 keyword=carrinho bebê sortType=2 listType=0 page=1 limit=50 | 50 | 37 | 29 | 41 | 951 | true |  |
| 122 | exp4 | productCatId=100632 keyword=patinete sortType=2 listType=0 page=1 limit=50 | 50 | 35 | 35 | 50 | 902 | true |  |
| 123 | exp5 | productCatId=100663 sortType=2 listType=0 page=1 limit=50 | 50 | 0 | 0 | 12 | 1082 | true |  |
| 124 | exp5 | productCatId=100659 sortType=2 listType=0 page=1 limit=50 | 50 | 1 | 0 | 24 | 797 | true |  |
| 125 | exp5 | productCatId=100662 sortType=2 listType=0 page=1 limit=50 | 50 | 42 | 10 | 11 | 998 | true |  |
| 126 | extra | productCatId=100630 sortType=2 listType=0 page=41 limit=50 | 0 | 0 | 0 | 0 | 771 | false |  |
| 127 | extra | productCatId=100630 sortType=5 listType=0 page=41 limit=50 | 0 | 0 | 0 | 0 | 542 | false |  |
| 128 | extra | productCatId=100001 sortType=2 listType=0 page=40 limit=50 | 50 | 50 | 34 | 34 | 744 | false |  |
| 129 | extra | productCatId=100662 sortType=2 listType=0 page=10 limit=50 | 50 | 50 | 15 | 15 | 691 | true |  |
| 130 | extra | productCatId=100662 sortType=2 listType=0 page=40 limit=50 | 50 | 50 | 19 | 19 | 836 | false |  |
| 131 | extra | productCatId=100663 sortType=2 listType=0 page=40 limit=50 | 50 | 50 | 24 | 24 | 877 | false |  |
| 132 | extra | productCatId=100637 keyword=halteres sortType=2 listType=0 page=2 limit=50 | 50 | 50 | 42 | 42 | 1215 | true |  |
| 133 | extra | productCatId=100637 keyword=legging sortType=2 listType=0 page=2 limit=50 | 50 | 40 | 39 | 48 | 987 | true |  |
| 134 | extra | keyword=creatina sortType=2 listType=0 page=1 limit=50 | 50 | 1 | 1 | 48 | 760 | true |  |
| 135 | extra | productCatId=100001 keyword=creatina sortType=5 listType=0 page=1 limit=50 | 50 | 49 | 49 | 50 | 1009 | true |  |
| 136 | extra | productCatId=100630 sortType=2 listType=0 page=1 limit=50 isAMSOffer=True | 50 | 0 | 0 | 21 | 687 | true |  |
| 137 | extra | productCatId=100630 sortType=2 listType=0 page=1 limit=50 isKeySeller=True | 50 | 0 | 0 | 23 | 656 | true |  |
| 138 | stab | productCatId=100630 sortType=2 listType=0 page=1 limit=50 | 50 | 0 | 0 | 21 | 1197 | true |  |
| 139 | stab | productCatId=100636 sortType=2 listType=0 page=1 limit=50 | 50 | 0 | 0 | 13 | 879 | true |  |
| 140 | stab | productCatId=100001 sortType=2 listType=0 page=1 limit=50 | 50 | 1 | 1 | 29 | 912 | true |  |
| 141 | stab | productCatId=100637 sortType=2 listType=0 page=1 limit=50 | 50 | 0 | 0 | 31 | 906 | true |  |
| 142 | stab | productCatId=100632 sortType=2 listType=0 page=1 limit=50 | 50 | 0 | 0 | 23 | 746 | true |  |
| 143 | extra | productCatId=100018 sortType=2 listType=0 page=40 limit=50 | 50 | 50 | 35 | 35 | 1122 | false |  |
| 144 | extra | productCatId=100002 sortType=2 listType=0 page=40 limit=50 | 50 | 50 | 49 | 49 | 867 | false |  |
| 145 | extra | productCatId=100727 sortType=2 listType=0 page=40 limit=50 | 50 | 50 | 44 | 44 | 1228 | false |  |
| 146 | extra | productCatId=100662 sortType=5 listType=0 page=1 limit=50 | 50 | 49 | 33 | 33 | 1490 | true |  |
| 147 | extra | productCatId=100896 sortType=2 listType=0 page=1 limit=50 | 50 | 17 | 16 | 44 | 842 | true |  |

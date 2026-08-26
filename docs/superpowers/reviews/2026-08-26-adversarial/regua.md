# Régua honesta e o quinto zero silencioso — achados adversariais

HEAD `31700c9`. Suíte do repo: 288 passed. Reproduções: `adv/test_regua_adv.py`
(30 testes; cada um AFIRMA o defeito — passar = achado reproduzido). Rodar:
`cd scratchpad/adv && python -m pytest -q -s -p no:cacheprovider --rootdir=. test_regua_adv.py`.

## Afirmações refutadas

- **[C] O QUINTO ZERO: `filter_offers` zera N>0 ofertas e o ops não recebe nada** —
  `src/afiliado/selection.py:22-49` (seis `continue` sem contador),
  `src/afiliado/pipeline.py:60-63` (nenhum aviso quando `candidates == []`),
  `src/afiliado/cli.py:286-290` (`houve_algo = published or discarded or warnings`).
  Spec §9 afirma "Nada falha em silêncio — tudo aparece no resumo de operações"
  e na linha seguinte sanciona o furo: run com 0 candidatas é "ausência de
  evento". Evidência: 50 ofertas da Shopee entram, todas caem no filtro (1
  centavo acima da ref / abaixo de `price_min_brl` / sem imagem / já postadas
  há < 30 dias) → `summary.published == discarded == warnings == []` → o cli
  não manda nada. Repro: `test_7a_*` (3 variantes), `test_7b_*`. Dano: a única
  fonte ativa (Shopee, ML desligado no config) pode ficar horas ou dias sem
  publicar — por dedupe esgotado, por feed fora da faixa de preço, por
  `max_above_ref` com o price_log maduro — e o único sinal é o silêncio, igual
  aos quatro bugs anteriores. `tests/test_zero_silencioso.py` protege só o pool
  do ML em teste unitário; em produção nada reporta "N entraram, 0 sobraram".
  Caso concreto já presente: o item do pool `MLB36931922` ("Perfumes Colônia
  Miniatura Cebolinha…", `price_ref_cents: 1990`) está abaixo de
  `price_min_brl: 20` e é descartado em silêncio em TODO run — nunca aparece em
  `discarded` (37 e não 38 em `test_5b_*`; `test_5a_*`). Só publica se o preço
  ao vivo for ≥ 20,00 E ≤ 19,90: morto por construção, e a curadoria não
  valida contra o config.

- **[C] Watchlist vencida troca a régua inteira sem trocar de aviso** —
  `src/afiliado/pipeline.py:35-38` (`watchlist = None` quando `is_stale()`),
  `pricing.py:134-146` (degraus 2 somem), `pipeline.py:89` (`price_floor =
  None`), `message.py:18-25`. O projeto afirma que a watchlist "quando existe,
  o veredito dela é FINAL" e que o pipeline "degrada" sem ela. Refutado em três
  direções, todas com o mesmo produto, mesmo dia, mesmo histórico:
  (a) fresca: piso curado 24000, preço 24999 → SEM selo; vencida: cai no degrau
  tolerante do price_log → "🏷️ Menor preço já registrado (verificado)" para um
  preço 4,2% ACIMA do piso que a watchlist acabou de dizer que existe
  (`test_1a_*`). A degradação torna a alegação MAIS agressiva, não menos.
  (b) fresca: ref 3000 → "De: R$ 30,00 … (27% OFF)"; vencida: ref = mediana do
  price_log 2500 → "De: R$ 25,00 … (12% OFF)". Dois "De:" diferentes em dias
  consecutivos, nenhum aviso além de "Watchlist vencida" (`test_1b_*`).
  (c) cenário real de hoje (price_log vazio): "27% OFF + Menor preço dos
  últimos 6 meses" vira "R$ 21,90" puro no dia seguinte (`test_1c_*`).
  Com `data/watchlist.json` gerado em 2026-08-23 e `valid_days: 14`, isso
  acontece em **2026-09-07** se ninguém rodar `/watchlist-refresh`.

- **[C] Selo tolerante mente até 5%: "Menor preço JÁ REGISTRADO" para preço acima
  da mínima registrada** — `src/afiliado/message.py:23-25`, `config.yaml:23`
  (`seal_tolerance: 1.05`). O texto afirma um fato sobre o NOSSO registro
  ("já registrado (verificado)") e o próprio registro o contradiz: piso 24000,
  preço 24999 → selo. O projeto codifica a mentira em
  `tests/test_message.py:121-125` (`test_build_message_selo_do_piso_proprio_com_tolerancia`).
  Para o ML é pior: o piso vem do JoomPulse (janela de 1 ano que não medimos)
  e o texto diz "já registrado" — item real `MLB66637233`, mínima histórica
  3051, ao vivo 3200 (4,9% acima) → "🏷️ Menor preço já registrado (verificado)"
  (`test_8b_*`, `test_8a_*`). Dano: a audiência checa o histórico (o próprio
  ML mostra) e a marca "Fiscal da Promo" vira piada; denúncia por propaganda
  enganosa é plausível com a palavra "verificado".

- **[C] A "referência" do ML é a foto de um dia, mas o post a veste de mediana** —
  `docs/runbooks/meli-setup.md:117-119` ("preço no momento da curadoria"),
  `src/afiliado/sources/meli.py:264-277` (`price_ref_cents` vira `price_ref_cents`
  E `price_original_cents`), `pricing.py:1-13` (docstring: "a mediana do nosso
  histórico de preços, não o 'de' do vendedor"). No pool real, 9 de 38 itens
  têm `price_ref / price_historic_min ≥ 2,5×` (`MLB17001105` 3,67×;
  `MLB22430943` 3,39×; `MLB75033614` 3,46×; `MLB19603205` 3,32×). Se a foto
  pegou um dia inflado, o post "verifica" contra o inflado: `MLB66637233` ref
  7890, ao vivo 3200 → "De: <s>R$ 78,90</s> | Por: <b>R$ 32,00</b> (59% OFF)"
  + selo falso acima (`test_8b_*`). Nada no código compara ref × mínima
  histórica para detectar referência implausível. A afirmação "o desconto
  nunca é o 'de' do vendedor" vale para a Shopee; no ML o "de" é o preço de um
  dia escolhido por um processo externo sem validação.

- **[C] A copy ignora `min_real_discount_pct`: headline "4% OFF" sobre bloco de
  preço sem De/Por** — `src/afiliado/copywriter.py:14-24` (prompt diz
  "Desconto verificado: 4%" e só proíbe palavras de desconto quando é 0),
  `copywriter.py:27-33` (fallback escreve "🔥 Oferta: 4% OFF"). O projeto afirma
  "abaixo disso o post não alega desconto: mostra preço + prova social"
  (`config.yaml:20`). Refutado: `real_discount_pct` 4, min 10 → `price_line` em
  modo B ("R$ 25,00") e o texto final começa com "🔥 Oferta: 4% OFF" +
  "Promoção por tempo limitado" (inventado) (`test_2b_*`, `test_2g_*`). A régua
  foi aplicada em `pricing`, `creative` e `message`, não em `copywriter`. Com
  o LLM no ar, o prompt entrega o número e não proíbe "baixou/off" → mesma
  contradição, aleatória. Post contraditório = parece fabricado.

- **[C] Mediana par fabrica um "De:" que nunca foi preço; rampa curta vira
  referência** — `src/afiliado/pricing.py:37` (média dos dois centrais),
  `pricing.py:138-141`. (a) histórico [2600×3, 6890×3] → ref 4745 → "De: R$
  47,45 | Por: R$ 26,00 (45% OFF)": R$ 47,45 nunca foi preço de ninguém
  (`test_3a_*`). O comentário "truncamento é conservador" trata de 1 centavo;
  o problema é o "De:" ser um número inventado apresentado como preço anterior.
  (b) preço real 100 por 5 dias; vendedor segura 150 por 7 dias; hoje "promo"
  a 120 → ref 15000 → "De: R$ 150,00 | Por: R$ 120,00 (20% OFF)" carimbado
  como verificado — 20% ACIMA do preço de duas semanas atrás (`test_3b_*`).
  Basta o preço alto ocupar > metade dos dias observados; com
  `ref_min_observations: 5` e price_log nascendo vazio, "metade" são 3 dias.
  (c) subida e platô: 100×5 então 150×7 → 150 == mediana → passa no
  `max_above_ref` como "o típico" (`test_3c_*`).

- **[A] `max_above_ref: 1.00` tem tolerância ZERO e a referência do ML é fixa:
  +1 centavo mata 38/38 e o run queima 75 chamadas de LLM para publicar nada** —
  `src/afiliado/validate.py:35-39`, `selection.py:35-37`, `config.yaml:18`,
  `pipeline.py:84-98` (ordem: refresh → link → **write_copy** → validate).
  Com o pool real, `live = ref × 1,01` rejeita 38/38; `live = ref + 1 centavo`
  rejeita 38/38 (`test_5a_*`). Como `price_ref_cents` do ML é uma foto de
  2026-08-26 e não se atualiza, qualquer deriva positiva do preço ao vivo —
  normal em marketplace — desliga o item pelos 30 dias de validade do pool.
  E a copy é gerada ANTES de `check_price`: `write_copy` tenta 2× quando o
  LLM falha → 37 ofertas × 2 + 1 ranking = **75 chamadas por run**, 0
  publicados, a cada 5 min = 21.600/dia contra a cota Max (`test_5b_*`). Não é
  silencioso (37 linhas em `discarded`), mas é dinheiro/cota queimada e um
  resumo de 37 linhas no ops a cada 5 min.

- **[A] Watchlist vencida = resumo no ops a cada 5 min (288/dia)** —
  `pipeline.py:36-37` + `cli.py:288-290`. `warnings` nunca fica vazio com
  watchlist vencida (ou ausente: `pipeline.py:34`), logo `houve_algo` é sempre
  True e `notify_empty_runs: false` deixa de valer. O oposto do zero
  silencioso: ruído que ensina o dono a ignorar o chat de ops — onde chegam os
  avisos reais. A partir de 2026-09-07 com o arquivo atual (`test_7c_*`).

- **[A] Config `0` vira o default em silêncio (e a docstring diz que 0 é
  suportado)** — `pricing.py:126-127`, `pipeline.py:92-95`, `cli.py:91-94`
  (todos `sel.get(k) or DEFAULT`). `pricing.py:64-65` afirma "`desconto > 0`
  também cobre min_real_discount_pct=0" — inalcançável: 0 no config vira 10
  antes de chegar lá. Valores que NÃO podem ser 0: `min_real_discount_pct`
  (→10), `ref_min_observations` (→5), `ref_window_days` (→90),
  `seal_tolerance` (→1.05). `posts_per_run: 0` é honrado — e publica nada
  sem aviso (`test_2a-2f_*`). `max_above_ref: 0` mataria tudo com referência
  (só protegido pelo teste do config real).

- **[A] O price_log do ML registra o preço do pool todo dia, nunca o ao vivo** —
  `pipeline.py:57` (record antes do refresh), `pipeline.py:84` (o refresh não
  grava), `pricing.py:100-102` (docstring: "Registra o preço atual"). Refutado:
  pool 7890, ao vivo 6990 → `price_history("meli", id) == [7890]`
  (`test_5c_*`). O único dado real de preço do ML (refresh_price) é descartado;
  o "histórico próprio" do ML é uma constante. Se alguém remover
  `price_ref_cents` do pool para "deixar o histórico decidir", a mediana será o
  preço velho do pool.

- **[A] Aviso do pool com diagnóstico errado; entradas sem `price_ref_cents`
  somem sem contador** — `sources/meli.py:176-195` (só `sem_piso` conta;
  `_parse_pool_offer → None` é `continue` mudo em :187), `pipeline.py:50-54`
  (`elif`: quando `count == 0` o motivo real é descartado). Pool com 3 entradas
  todas sem piso → ops recebe "pool vazio ou vencido — rode
  /meli-links-refresh" (errado duas vezes: não está vazio nem vencido, e
  `/meli-links-refresh` regenera links, não o pool de ofertas) e nunca a
  mensagem "3 entrada(s) ignorada(s)" (`test_6a_*`). Pool com 3 entradas, 2
  sem `price_ref_cents` → 1 oferta e `pool_warning is None` (`test_6b_*`). O
  runbook (`meli-setup.md:120-122`) afirma que "a contagem das puladas entra
  no aviso do resumo" — só das puladas por piso.

- **[M] 9,5000% vira "10% OFF" e passa no mínimo 10** — `models.py:49`
  (`round` = banker's rounding; 9.5 → 10 por ser par). ref 19821, cur 17938 →
  desconto real 9,5000% → "De: R$ 198,21 | Por: R$ 179,38 (10% OFF)"
  (`test_4_*`). Fronteira exata: qualquer real ≥ 9,5 em float. O `int(...)//`
  conservador da mediana não foi aplicado ao percentual.

- **[M] Selo no texto, nunca na arte; legenda do IG e do story nunca têm selo** —
  `creative.py:491-492` (`_selo_applicable` só watchlist), `message.py:23-25`
  (degrau tolerante só texto), `instagram_feed.py:128-142` e
  `story_dispatch.py:46-49` (legendas sem selo). Telegram diz "Menor preço já
  registrado (verificado)"; a arte do story/feed do mesmo post não mostra selo;
  para o ML a arte NUNCA mostra selo (não há `PriceFloor` da watchlist para
  ids MLB) (`test_8a_*`, `test_8b_*`). Inversa (arte com selo, texto sem) não
  ocorre. A inconsistência num só sentido parece o texto inventando o selo.

- **[M] "Últimos 90 dias" são 91; watchlist/pool em data local, price_log em
  UTC; janela da watchlist envelhece até 14 dias e o texto promete "6 meses"** —
  `state.py:114-116` e `:124-125` (`>=`/`<` no dia −90: consistentes entre si,
  mas 91 dias), `watchlist.py:31` e `sources/meli.py:167` (`date.today()`
  local) vs `state.py:37-38` (UTC); `message.py:20-21`. Watchlist com 13 dias
  de idade ainda diz "Menor preço dos últimos 6 meses (verificado)" — os
  últimos 13 dias não foram medidos (`test_9a_*`, `test_9b_*`, `test_8c_*`).

## Afirmações NÃO refutadas (tentei e não consegui)

- "`min_ev_brl: 0.50` não mata o ML com o pool atual" — menor `ev_score` do
  pool = 1,739 (`MLB36931922`; `test_5d_*`). Para cair abaixo de 0,50 com
  `commission_pct 4.0` o preço teria de ser < R$ 5,87 — já barrado por
  `price_min_brl`. Só morre se `meli.commission_pct` for 0/ausente (bug nº 3,
  ainda estruturalmente presente, protegido só pelo valor do config).
- "Preço subindo é bloqueado por `max_above_ref`" — sequência estritamente
  crescente [10100..10400] + hoje 10500 → mediana 10300 → bloqueado
  (`test_3c_*`, primeira metade). O bloqueio falha só quando o preço novo
  segura > metade dos dias (achado acima).
- "`MIN` em conflito no mesmo dia UTC dá um preço por dia sano" — 288 runs/dia
  colapsam num único mínimo do dia UTC; run às 21h–23h59 BRT cai no dia UTC
  seguinte, mas continua um valor por dia; `price_history` e `prune_price_log`
  usam o mesmo corte (dia −N incluído nos dois). Não há off-by-one ENTRE eles.
- "`price_line` nunca inventa desconto sem referência ou acima dela" — com
  `price_ref_cents == 0` ou `current >= ref`, `real_discount_pct == 0` e o
  modo é B (`tests/test_pricing.py:96-100` e `test_zero_silencioso.py:113-137`).
  O que refutei é o VALOR da referência (foto de um dia, mediana fabricada,
  rampa), não a mecânica do rótulo.
- "O aviso de entradas sem piso chega ao ops" — chega quando sobra ≥ 1 oferta
  (`pipeline.py:53-54` → `cli.py:288-290`). Falha só no caso `count == 0`
  (achado acima).
- "Com watchlist presente o veredito do selo é estrito" — `message.py:18-22`
  retorna "" quando `current > min_price_cents`, sem cair no degrau tolerante
  (`tests/test_message.py` cobre). Vale enquanto a watchlist estiver fresca.

## Riscos fora do código

- **Cold start define a régua da Shopee para sempre-ish**: `price_log` nasce
  vazio e `data/watchlist.json` tem ZERO `price_refs`. Os primeiros 5 dias UTC
  de cada item viram a mediana; itens da Shopee que não reaparecem 5 dias
  distintos nunca ganham referência (ficam em modo B para sempre). Lançar em
  época de rampa (novembro pré-Black Friday) grava o preço inflado como
  "típico" com 90 dias de memória.
- **ML: preço anunciado ≠ preço na página**: `_min_live_price_cents` pega o
  MENOR `price` entre todos os vendedores do produto de catálogo
  (`sources/meli.py:281-301`), sem olhar `status`/estoque; o link de afiliado
  leva à página do produto (buy box). O "Por: R$ 32,00" pode ser de um
  vendedor sem reputação e o usuário ver R$ 45 ao clicar — bait-and-switch
  involuntário, e o "59% OFF" fica ainda mais falso.
- **A curadoria do pool não é validada contra o config**: `price_ref_cents`
  abaixo de `price_min_brl`, ref 3,7× a mínima histórica, `generated_at` no
  futuro — tudo entra. O único zero silencioso já em produção (o item a
  R$ 19,90) veio daí.
- **`data/state.db` commitado a cada run no Actions**: com `record_observations`
  gravando até ~500 linhas/dia da Shopee (5 categorias × 2 páginas × 50), o
  arquivo cresce e muda em todo commit; conflitos de merge entre VPS e
  Actions apagam ou duplicam histórico — e a mediana muda sem ninguém saber.
- **Refresh manual da watchlist é o único freio dos achados 2 e 8**: a data de
  2026-09-07 não está em nenhum alarme; o aviso que existe (288/dia) é o tipo
  de aviso que vira ruído.

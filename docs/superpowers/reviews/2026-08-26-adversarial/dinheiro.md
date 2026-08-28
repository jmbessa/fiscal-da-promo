# Dinheiro e atribuição — achados adversariais

Árvore analisada: HEAD `31700c9`, 288 testes verdes. Scripts de reprodução em
`scratchpad/adv/r1..r7_*.py` (rodar da raiz com `python adv/run.py adv/rN_*.py`).
Caminhos abaixo são relativos à raiz do worktree.

## Afirmações refutadas

- **[C] O ML esgota em ~3 horas e vira zero silencioso pelos 30 dias seguintes** —
  `src/afiliado/selection.py:43` (dedupe descarta sem contar) +
  `src/afiliado/pipeline.py:50-54` (o único aviso do ML dispara quando
  `meli_offer_count == 0`, e o pool continua com 38). O projeto afirma (spec §9)
  "nada falha em silêncio" e vende o ML como "segunda fonte de ofertas"
  (README:50, runbook meli-setup.md:1). Reprodução `r3_pipeline.py` cenário E:
  40 runs no mesmo `state.db`, links ok, preço vivo = ref, Shopee com EV abaixo
  da mediana do ML → sequência `MMMM…(34×)SMMM..`: 37 posts do ML em 37 runs,
  depois ML = 0 e `warnings` só traz "Sem watchlist". Com o timer de 5 min
  (`deploy/afiliado.timer`), 37 runs ≈ 3h05. Pelo `dedupe_days: 30`
  (`config.yaml:14`) o ML contribui no máximo 38 posts por 30 dias ≈ 1,3/dia
  (1–3 % da meta de 50–100/dia) e ninguém é avisado de que a fonte sumiu. É o
  quinto zero silencioso: não é um filtro errado, é aritmética do pool.

- **[C] O pool inteiro sustenta 17,9 posts/dia, não 50–100; depois disso o canal
  cala sem aviso** — `config.yaml:11-14,54-62,111-114`. Reprodução
  `r5_dedupe_math.py`: pool ML 38 + Shopee máx. 500/run (5 cat × 1 sort × 2 pág ×
  50) = 538 itens únicos; com dedupe 30 dias a taxa sustentável é 538/30 =
  **17,9/dia**. A 100/dia o pool esgota em 5,4 dias; a 50/dia em 10,8. Sustentar
  50/dia exige ≥ 1.500 itens únicos por 30 dias; 100/dia, ≥ 3.000. Quando esgota,
  o run publica 0 e descarta 0; com watchlist válida `warnings == []`, logo
  `houve_algo` é falso e o chat de operações não recebe nada
  (`src/afiliado/cli.py:287-290`, `ops.notify_empty_runs: false`). No cenário E
  acima, os runs 39-40 publicaram nada. Ressalva: 500 é o teto por run; rotação
  do "mais vendidos" da API não foi medida (sem rede), mas o comentário em
  `config.yaml:55` diz que a API "praticamente ignora a ordenação".

- **[A] `data/meli_links.json` não existe, nunca foi commitado, e o `doctor` dá ✅
  mesmo assim** — `git log --all -- data/meli_links.json` vazio; `ls data/` (este
  worktree e o checkout `main`) não tem o arquivo; `.gitignore` não o ignora (ou
  seja, deveria estar no repo, como o skill manda em
  `.claude/skills/meli-links-refresh/SKILL.md:20,113`). `src/afiliado/cli.py:172-179`:
  o doctor valida token + pool de OFERTAS e imprime "✅ Mercado Livre: token ok; 38
  ofertas no pool" sem olhar o pool de LINKS. `pipeline.py:50-54` idem. Em
  produção o arquivo está ausente por construção: Actions faz `checkout`
  (`.github/workflows/publish.yml:27`) e a VPS faz `git clone`
  (`deploy/install-vps.sh:28`). Reprodução `r3_pipeline.py` cenário A (links
  ausentes, `sources.meli: true`): **34 descartes "sem link de afiliado no pool"
  + 34 chamadas a `/products/{id}/items` por run** antes de a Shopee publicar. A
  192 runs/dia (08h-23h55, 5 min) são ≈ 6.500 chamadas/dia à API do ML e um
  resumo de 34 linhas no chat de operações a cada 5 minutos. O runbook
  (`docs/runbooks/meli-setup.md:157-159`) e o skill (`SKILL.md:119-122`) afirmam
  que item sem link "é descartado silenciosamente" — errado no sentido oposto:
  cada descarte entra em `summary.discarded` (`pipeline.py:99-101`) e inunda.

- **[A] O portão de link protege o HOST, não a comissão; aceita 403 e página 200
  de "link não encontrado"** — `src/afiliado/validate.py:23-26`; spec §8 afirma
  "HTTP 200 e redireciona para o domínio esperado". Reprodução `r1_check_link.py`
  (MockTransport, config real): PASSAM (1) `https://www.mercadolivre.com.br/p/MLB…`
  puro, sem `ref`/`matt_*` — comissão zero; (2) Shopee `productLink` puro; (3)
  `meli.la` → vitrine → `/p/` com o `ref` perdido no segundo salto; (4) `meli.la`
  respondendo **403 sem redirect** (o próprio teste
  `tests/test_validate.py:109-112` fixa isso como comportamento desejado); (5)
  `meli.la` respondendo 200 com "Link não encontrado"; (6) redirect para `/p/`
  com `matt_word` de OUTRO afiliado. Hoje nenhum caminho de código gera link
  sem atribuição (ver "não refutadas"), mas o portão não detectaria um
  `meli_links.json` preenchido à mão/por outra ferramenta com URLs puras, um
  link curto revogado que responde 200/403, nem a vitrine derrubando o `ref`.
  Validação verde, comissão = 0, nenhum aviso.

- **[A] Cada validação é um clique real no link de afiliado — inclusive em
  `--dry-run`** — `pipeline.py:98` chama `validator(post, cfg)` ANTES do
  `if dry_run` (`:103`); `validate.py:14,20` faz GET com `follow_redirects=True`
  e User-Agent de navegador. Reprodução `r3_pipeline.py` cenário D (dry-run):
  `GETs no link de afiliado = 1 → https://meli.la/MLB46836439`. Em produção: 1
  clique por post publicado (até 100/dia) saindo do IP da VPS/runner, com zero
  conversões — padrão de tráfego inválido para os dois programas. E contamina o
  teste de atribuição que o dono precisa fazer: além dos "2 cliques" do contexto,
  qualquer `afiliado run --dry-run` gera cliques na conta.

- **[A] Para o ML a "referência honesta" é o preço de UM dia e de UM vendedor; o
  post pode alegar até 73 % OFF** — `src/afiliado/pricing.py:5-8` afirma que a
  referência é "a mediana do nosso histórico de preços, não o 'de' do vendedor".
  Para o ML: `sources/meli.py:259-276` carimba `price_ref_cents` do pool (runbook
  `meli-setup.md:117`: "preço no momento da curadoria"), `pricing.py:131-141`
  (degrau 1) nunca o substitui, e o preço vivo é o **mínimo entre todos os
  vendedores** (`meli.py:281-301`; runbook:37 e memória: até 64 ofertas por
  produto). Logo "De" = preço de um vendedor no dia da curadoria; "Por" = o
  vendedor mais barato hoje — populações diferentes, desconto quase garantido.
  Reprodução `r6_ref_ratio.py` no pool real: ref/mínima histórica mediana
  **1,54×**, 19/38 ≥ 1,5×, 10/38 ≥ 2×, máx. **3,67×** (Omo 2,2 kg: "De R$ 74,12
  Por R$ 20,19 (73 % OFF)" se o vivo bater a mínima). `r2_selo_ml.py`: creatina
  500 g a R$ 32,00 vivo → texto "De: <s>R$ 78,90</s> | Por: <b>R$ 32,00</b> (59 %
  OFF)". Ressalva: de onde o curador tirou `price_ref_cents` está fora do código;
  o que está no código é que esse número vira "De" sem nenhuma mediana.

- **[A] Selo "Menor preço já registrado (verificado)" no ML com preço ACIMA da
  mínima; texto e arte discordam** — `src/afiliado/message.py:23-25` (degrau
  tolerante × `seal_tolerance 1.05`). O docstring do próprio `_selo`
  (`message.py:12-16`) diz que um piso curado "5 % acima ganharia o selo por
  tabela" e por isso a watchlist tem veredito final — mas o piso curado do ML
  (`price_historic_min_cents`, JoomPulse, mesma proveniência da watchlist) entra
  por `price_floor_cents` e cai no degrau tolerante, porque `pipeline.py:89`
  passa `watchlist.price_floor(item_id)` = None (a watchlist só tem IDs Shopee).
  Reprodução `r2_selo_ml.py`: mínima R$ 30,51, vivo R$ 32,00 (+4,9 %) → Telegram
  imprime "🏷️ Menor preço já registrado (verificado)"; a arte
  (`creative._selo_applicable(offer, None)`, `creative.py:491-492`) devolve
  False → story/feed sem selo. A mesma oferta afirma coisas diferentes em
  canais diferentes, e a do Telegram é falsa.

- **[A] `max_above_ref: 1.00` contra referência de um dia: 1 centavo acima →
  descarte em cascata com uma chamada de API por item, todo run** —
  `selection.py:35-36` nunca dispara para o ML no ranking (na descoberta
  `price_current == price_ref`, `meli.py:268-269`); quem pega é
  `validate.py:35-39` DEPOIS do `refresh_price` (`pipeline.py:82-84`), então o
  custo é pago em rede. Reprodução `r3_pipeline.py` cenário B (vivo = ref +
  R$ 0,01): **34 descartes "preço R$ 192,06 acima da referência R$ 192,05" + 34
  chamadas `/products/{id}/items` por run**. Qualquer deriva de preço para cima
  no ML (pool válido por 30 dias, `meli.py:24`) zera a fonte — com 34 linhas de
  ruído por run, não em silêncio, mas com o mesmo resultado financeiro.

- **[M] `price_log` do ML grava o preço do pool, nunca o preço vivo** —
  `pipeline.py:57` (`record_observations`) roda antes do `refresh_price` em
  `:84`, e nada grava depois. `pricing.py:100-101` afirma "registra o preço
  atual de cada oferta". Reprodução `r7_pricelog_item.py`: publicado
  `MLB46836439` com vivo 18205 (está em `posted.price_cents`), ref 19205 →
  `price_log.price_cents = 19205`. O "histórico próprio" do ML é uma linha reta
  no valor do pool; os degraus 3 de referência e piso (`pricing.py:138-141,
  147-151`) jamais produzirão nada real para o ML.

- **[M] Comissão do ML é chute de config apresentado como fato ao ranker; 1 % →
  8 % inverte a ordem ML/Shopee** — `config.yaml:71-74`; `selection.py:56-57`
  (EV) e `:77-78` (prompt: "comissão=R$X (4.0%)" sem marcar como estimativa).
  Reprodução `r4_ev_sensitivity.py` com o pool real: EV máx. do ML = 4,11 @1 % /
  16,44 @4 % / 32,87 @8 %; mediana 1,33 / 5,32 / 10,63. Uma Shopee de R$ 30 a
  10 % com 3 mil vendas (EV 6,13) vence TODO o ML a 1 % e perde para a mediana
  do ML a 4 %. O piso `min_ev_brl 0.50` não zera (37/38 passam até a 1 %; o 38º
  cai por `price_min_brl 20`, R$ 19,90) — a distorção é só de ranking, mas
  decide quem ocupa os poucos slots. Agrava: os boosts da watchlist
  (`watchlist.py:36-38`; chaves de `data/watchlist.json` são categorias/IDs
  Shopee) dão ×1,05–1,95 à Shopee e ×1,0 sempre ao ML.

- **[M] Links do ML sem etiqueta nem data; trocar `meli.tag` não regenera nada**
  — `src/afiliado/meli_links.py:108` guarda só `product_id → short_url`; o skill
  (Passo 1) só gera para IDs sem link. Não perde comissão (a etiqueta é rótulo
  do painel), mas o relatório por etiqueta do painel fica preso à etiqueta
  antiga para sempre, e sem `generated_at` não há como saber se um link do pool
  foi revogado — e o portão de link não detecta (ver acima).

## Afirmações NÃO refutadas (tentei e não consegui)

- "Nenhum fallback publica link sem atribuição" — `meli.py:231-236` levanta
  `SourceError` sem link no pool; `shopee.py:98-109` só devolve `shortLink` da
  mutação ou `offerLink` da API (link de afiliado por definição) e levanta sem
  os dois; `product_url` só é usado em `selection.py:27` (não-vazio) e como
  `originUrl` da mutação (`shopee.py:101`). Não achei caminho que publique
  `product_url`.
- "Shopee `commission` é R$ por unidade" — fixture
  `tests/fixtures/shopee_product_offer.json`: 249,99 × 0,12 = 29,9988 = campo
  `commission`; `shopee.py:139,172-177` lê rate e valor coerentes; `ev_score`
  prefere `commission_brl`. Tentei mistura de unidade (taxa vs valor, pedido vs
  unidade) e não achei. O que a API paga de fato por pedido (regras por
  categoria/campanha) está fora do código.
- "`generateShortLink` preserva a atribuição" — a mutação é assinada com
  AppID/secret do afiliado (`shopee.py:41-49`); sem rede não dá para checar o
  destino. `subIds` não é usado: sem atribuição por canal (Telegram vs IG),
  mas sem perda.
- "Segredos colados no chat não estão no repo" — `git log --all -p` filtrado por
  `app_secret|client_secret|APP_ID|CLIENT_ID|matt_tool|cookie` com literal de ≥ 6
  caracteres só devolveu nomes de variável e o template do `.env`.
- "`price_ref_cents` velho infla o bônus de desconto no RANKING" — para o ML é
  impossível: o ranking roda antes do refresh e `current == ref` ⇒
  `real_discount_pct == 0` (`models.py:44-49`); para a Shopee a referência só
  nasce após ≥ 5 dias de observação (`pricing.py:138-141`), e `price_refs` da
  watchlist está vazio. O dano do ref velho do ML está no RÓTULO, não no
  ranking (achado acima).

## Riscos fora do código

- Atribuição do ML não provada (`ref` cifrado, `SOCIAL_PROFILE_ENCRYPTED`) — e
  qualquer teste isolado precisa descontar os cliques que o próprio
  `validate.check_link` gera (inclusive em dry-run).
- Taxas reais do programa de afiliados do ML por categoria; o pool concentra 19
  itens em `MLB1574`, 12 em `MLB264586`, 5 em `MLB1246`, 2 em `MLB1276` — se
  diferirem muito de 4 %, o ranking ML/Shopee é arbitrário (r4).
- A página de catálogo `/p/MLB…` mostra o vencedor do buy box, não
  necessariamente o vendedor mais barato que `_min_live_price_cents` usou: o
  post diz "Por R$ 32,00" e a landing pode mostrar outro preço.
- `meli.la`: expiração/revogação de link curto e o que o encurtador responde
  para link inválido (se for 200 com página de erro ou 403, o portão aceita).
- ~~`sales` do ML vem do JoomPulse (30 dias, `watchlist-refresh/SKILL.md:55-65`
  usa `sold30Days`) e `sales` da Shopee é o acumulado da API — mesmo `log10`
  para populações diferentes; não verificável sem a API.~~
  **RESPONDIDO em 2026-08-28, e era o contrário:** o `sales` do ML virou
  `catalogSales` (contador VITALÍCIO) no PR #4, e o da Shopee é que sempre foi a
  janela de ~30 dias — medido contra `ShbMartItem.sold1y`/`sold30Days`, 13× a
  43× abaixo do contador do anúncio (tabela em
  `.claude/skills/meli-pool-refresh/SKILL.md`). A suspeita de "mesmo `log10`
  para populações diferentes" estava certa: a fase 5H mediu o efeito (9% a favor
  do ML no `ev_score`, absorvidos pela `source_quota`) e consertou onde ele
  decidia de fato — a fatia por vendas do slate, que era 100% ML por construção.
- Padrão de cliques de datacenter (VPS/Actions) sem conversão → sinalização de
  tráfego inválido e possível bloqueio de conta nos dois programas.
- Rotação real do "mais vendidos" da Shopee por categoria: se a API devolver o
  mesmo top-100 por semanas, o teto de 17,9/dia (r5) é otimista.
- Instagram feed nunca leva link (`instagram_feed.py:141`: "Link na bio e no
  canal do Telegram") e `story_dispatch` depende de colagem manual do sticker:
  comissão desses canais depende de o link da bio ser de afiliado e de o dono
  colar o link certo — nada no código verifica.

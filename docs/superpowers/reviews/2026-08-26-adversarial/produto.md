# Produto e a matemática do volume — achados adversariais

Código em HEAD `31700c9`. Scripts de reprodução (rodados, saídas coladas abaixo) em
`...\scratchpad\adv\`: `repro_cap_loop.py`, `repro_utc_cap.py`, `repro_ig_timing.py`,
`repro_median.py`, `sim_ruler.py`, `inspect_data.py`. Suíte: 288 testes verdes — nenhum
deles cobre o que está abaixo.

Constantes usadas em toda a aritmética (com linha do `config.yaml`):
`posts_per_run: 1` (:11) · `price_min_brl: 20` (:12) · `price_max_brl: 1000` (:13) ·
`dedupe_days: 30` (:14) · `max_above_ref: 1.00` (:18) · `require_price_ref: false` (:19) ·
`min_real_discount_pct: 10` (:20) · `ref_min_observations: 5` (:22) · `min_ev_brl: 0.50` (:29) ·
`ev_weights popularity 0.3 / discount 0.5` (:43-44) · `sources.meli: false` (:52) ·
`sort_types: [2]` (:55) · `pages: 2` (:57) · `page_size: 50` (:58) · 5 `category_ids` (:62) ·
`meli.commission_pct: 4.0` (:74) · `telegram.max_per_day: 100` (:114) · `story_dispatch 6` (:117) ·
`instagram_feed 2` (:120) · `notify_empty_runs: false` (:127).
Timer: `OnCalendar=*-*-* 08..23:00/5:00` (`deploy/afiliado.timer:8`) = 16 h × 12 = **192 slots/dia**
(config.yaml:26, README:91 e spec §3 dizem "288" — ver M-1).

## Afirmações refutadas

- **[C] "50–100 ofertas/dia" — o estoque acaba no dia 1 e o canal vira o turnover da listagem da Shopee**
  — `src/afiliado/sources/shopee.py:74-95`, `src/afiliado/selection.py:43`, config.yaml:14/57-58/62/114.
  Descoberta = 5 categorias × 1 ordenação × 2 páginas × 50 = **10 chamadas GraphQL e no máximo 500 nós por run**
  (o mesmo conjunto a cada 5 min — `sort_type 2`, "mais vendidos", escolhido porque "a API praticamente
  ignora a ordenação", config.yaml:55). Após filtros o dono mediu **97 candidatas** (dry-run 2026-08-25;
  o número não está no repo). Com dedupe de 30 dias e teto 100:
  dia 1 → 97 posts (08:00 + 97×5 min ≈ 16:05 BRT, se todos validarem); dias 2–30 → só os T itens NOVOS que
  entram na listagem por dia; dia 31 → os mesmos 97 de novo. Simulação (`sim_ruler.py`):

  | turnover T/dia | dias 1-7 | média dias 2-30 | média 60 dias |
  |---|---|---|---|
  | 0 | 97, 0, 0, 0, 0, 0, 0 | 0,0 | 3,2 |
  | 5 | 97, 5, 5, 5, 5, 5, 5 | 5,0 | 6,5 |
  | 10 | 97, 10, 10, … | 10,0 | 11,4 |
  | 20 | 97, 20, 20, … | 20,0 | 21,3 |

  Para sustentar 100/dia seria preciso T = 100 itens novos por dia dentro do allowlist, i.e., a listagem
  de 500 renovada inteira a cada 5 dias — incompatível com uma listagem de "mais vendidos". ML: 38 itens
  no pool, 37 elegíveis (MLB36931922 a R$ 19,90 < `price_min_brl`, `inspect_data.py`) / 30 dias =
  **1,2/dia**. Dano: o canal que promete 50–100/dia entrega ~5–20/dia a partir do dia 2 e repete o mês
  anterior no dia 31. **Zero silencioso #5 (a):** o esgotamento é um "run vazio" — sem publicado, sem
  descartado, sem aviso — e `cli.py:288-290` não notifica; o dono não distingue "estoque acabou" de
  "tudo bem" (spec §9 chama isso de "ausência de evento").

- **[C] Teto atingido → cada run de 5 min gasta a fila INTEIRA em LLM + API sem publicar nada**
  — `src/afiliado/pipeline.py:109-129`. Quando um canal está no teto ele é pulado (`continue`, :112-114),
  `published_any` fica `False`, `count` não incrementa (:127-129) e **não há `break`**: o laço `for offer in
  fila` (:74) percorre TODAS as candidatas chamando `refresh_price` (:84), `resolve_affiliate_link` (:87 —
  1 mutação `generateShortLink` na Shopee), `write_copy` (:88 — LLM, 2 tentativas se falhar,
  `copywriter.py:42`) e `validator` (:98 — 2 GETs). Reprodução (`repro_cap_loop.py`, 97 candidatas, os 3
  canais no teto, LLM devolvendo None):
  ```
  publicados: 0
  chamadas LLM neste run (copy): 195   generateShortLink: 97   validações (2 GETs cada): 97
  ```
  Com LLM saudável: 1 ranking + 97 copies = 98 chamadas/run. Com haiku a ~8 s (medido pelo dono: 7,8 s)
  o run leva 97 × (8 + 1 + 2) ≈ **18 min > `TimeoutStartSec=600`** (`deploy/afiliado.service:20`):
  systemd manda SIGTERM, o Python morre sem exceção → sem `record_run`, sem "❌ Run abortado"
  (`cli.py:275-280` só captura `Exception`). Enquanto a unidade está ativa o timer pula disparos → 1 run
  a cada 10 min. Entre 13:20 e 21:00 BRT (janela morta do achado seguinte): 46 runs × ~55 copies
  ≈ **2.500 chamadas LLM/dia e 2.500 `generateShortLink`/dia desperdiçadas**, contra "~200 chamadas/dia"
  prometidas em `docs/runbooks/vps-setup.md:130-131`. Cada um desses runs ainda gera o aviso "teto diário
  atingido" (:131-134) → mensagem no chat de ops a cada run (ver A-1). `tests/test_pipeline.py:197-216`
  testa o teto com um canal SEM teto ao lado — o caso "todos no teto" nunca foi exercitado.

- **[C] Teto diário contado em dia UTC: canal cala 13:20–21:00 BRT todos os dias e fura o teto no dia 1**
  — `src/afiliado/state.py:64-75` (docstring admite o fuso). Janela do timer 08:00–23:55 BRT = 11:00–02:55
  UTC; a fronteira UTC cai às 21:00 BRT. Reprodução com `StateDB` real (`repro_utc_cap.py`):
  ```
  dia BRT 1: publicados=136  canal cala às 16:20 BRT  volta às 21:00 BRT
  dia BRT 2: publicados=100  canal cala às 13:20 BRT  volta às 21:00 BRT
  dia BRT 3: publicados=100  canal cala às 13:20 BRT  volta às 21:00 BRT
  ```
  Dia 1: 100 até 16:20 + 36 depois das 21:00 = **136 (teto furado em 36%)**. Regime: 64 posts 08:00–13:20,
  silêncio de 7h40 (almoço, tarde, início da noite), 36 posts 21:00–23:55. "Espaçado, parece humano"
  (vps-setup.md:12) — não. Efeito colateral: `price_log` também é por dia UTC, então
  `ref_min_observations: 5` fecha em 4 dias BRT (dois "dias" UTC por dia de operação).

- **[A] `max_per_day` não espalha — Instagram feed sempre às 21:00 e 21:05 BRT, as 6 artes de story em bloco 21:00–21:25**
  — `pipeline.py:110-122` publica em TODO canal abaixo do teto a cada run, então o teto é consumido pelos
  primeiros N slots do dia UTC. Reprodução (`repro_ig_timing.py`):
  ```
  dia BRT 1: instagram_feed 4 posts em ['08:00','08:05','21:00','21:05']   story_dispatch 12 posts (08:00-08:25 e 21:00-21:25)
  dia BRT 2+: instagram_feed 2 posts em ['21:00','21:05']                   story_dispatch 6 posts em 21:00,21:05,...,21:25
  ```
  Dia 1 dobra os tetos (4 feeds, 12 stories). Depois: nunca de manhã; as 6 artes "para não acumular mais do
  que dá pra postar num dia" (README:41-42) chegam de uma vez às 21h; o feed são sempre os 2 primeiros da
  fila às 21:00 — pelo achado A-2, os itens mais caros. "Instagram como motor de audiência" (pitch.md:37)
  postando 2 fotos em 5 minutos, sempre no mesmo horário.

- **[C] A "régua honesta" certifica exatamente o "de" inflado que diz combater**
  — `src/afiliado/pricing.py:5-8` (motivação: item a R$ 26 há 90 dias com "de R$ 68,90" por um dia),
  `median_cents` (:27-37), `Offer.real_discount_pct` (`models.py:43-49`), `price_line` (:70-83).
  A mediana conta **dias**, não vendas; o preço a que ninguém compra vira referência se ficar >50% dos
  dias. Reprodução (`repro_median.py`, funções reais):
  ```
  A) alterna 68,90 / 26,00 dia sim dia não (4 dias)   -> 'De: R$ 47,45 | Por: R$ 26,00 (45% OFF)'   (47,45 nunca existiu)
  B) segura 68,90 em 3 de cada 5 dias, vende a 26 nos outros 2 -> 'De: R$ 68,90 | Por: R$ 26,00 (62% OFF)'
  C) 54 dias a 68,90 / 36 dias a 26,00 (janela de 90d)   -> 'De: R$ 68,90 | Por: R$ 26,00 (62% OFF)'
  D) caso do docstring (26 por 89 dias, 68,90 por 1 dia) -> 'R$ 26,00'   (único caso que a régua pega)
  E) promoção PERMANENTE: 44 dias a 68,90, depois 46 a 48 -> 'R$ 48,00'  (0% — item mais barato é punido)
  ```
  O padrão "preço de tabela alto + promoção recorrente" é o padrão dominante de flash sale; com
  `max_above_ref: 1.00` só publicamos nos dias baratos — o canal vira **amplificador** do padrão, agora com
  o selo "verificado" nosso. O caso D é o único que a mediana corrige, e é o menos comum.

- **[C] Shopee: modo A (De/Por) é estruturalmente quase inatingível — todo item é postado no PRIMEIRO dia em que é visto, sem histórico**
  — `data/watchlist.json` tem 0 `price_refs` (`inspect_data.py`: "price_refs ABSENT"); a referência própria
  exige ≥5 dias (`pricing.py:138-141`); mas `require_price_ref: false` deixa passar sem referência
  (`selection.py:38-39`) e, pelo C-1, a fila é esgotada no mesmo dia → o item é publicado com 1 observação
  (modo B) e trancado por 30 dias. Modo A só existe no 2º ciclo (dia 31+) e só se o preço do dia estiver
  ≥10% abaixo da mediana das mínimas diárias. Modelo do critério (`sim_ruler.py`, séries sintéticas de
  30 dias, 20 mil itens):

  | modelo de preço | passa `max_above_ref` | modo A (≥10% OFF) |
  |---|---|---|
  | preço fixo | 100% | 0% |
  | ruído ±3%, sem flash | 50% | 0% |
  | ruído ±5%, flash −20% em 10% dos dias | 50% | 11% |
  | ruído ±10%, flash −25% em 10% dos dias | 50% | 22% |

  Conclusão: **100% dos posts Shopee nos primeiros 30 dias e ≥80% depois são "R$ 33,90 · ⭐ 4,9 · 30 mil
  vendidos", sem desconto** — um canal de promoções cujo post típico não tem promoção, competindo com
  canais que postam desconto em todo post. **Zero silencioso #5 (b):** a partir do 5º dia, mais da metade
  dos itens de preço flutuante é descartada em `filter_offers` (`selection.py:35-37`) sem entrar em
  `summary.discarded` nem gerar aviso (preço do momento ≥ mínima do dia ⇒ P(atual > mediana) > 50%).

- **[C] Mercado Livre: a "referência" é um snapshot de UM dia, não histórico — e o rótulo certifica desconto em item 3× acima da mínima**
  — `docs/runbooks/meli-setup.md:117-119` ("preço no momento da curadoria"), `sources/meli.py:259-276`
  (`price_ref_cents` = pool; `price_current_cents` = ref até o refresh). pitch.md:15-16 promete "comparado
  ao histórico de até 12 meses". Com o pool real (`inspect_data.py`): MLB75033614 "Conjunto 19 Peças"
  ref R$ 169,90, mínima histórica R$ 49,17 → preço ao vivo R$ 150 → **"De: R$ 169,90 | Por: R$ 150,00
  (12% OFF)"** num item que já custou 3,4× menos; a mínima só alimenta o selo (`message.py:18-22`), que não
  aparece. Nenhum dos 38 itens tem ref == mínima (0/38). Consequências adicionais: (i) no ranking
  `real_discount_pct` do ML é sempre 0 (`price_current == price_ref` até `pipeline.py:84`) → o bônus de
  desconto (`selection.py:61-62`) nunca influencia a posição do ML; (ii) `check_price` roda DEPOIS do LLM
  (`pipeline.py:88` vs `:98`) → cada item ML com preço ao vivo acima do snapshot custa 1 LLM + 1 GET
  `/products/{id}/items` + 1 link por run e, como fica no topo da `reserva` por EV (`pipeline.py:62`) e
  nunca entra em `posted`, é **tentado nos 192 runs do dia** enquanto o preço não cair (o commit 4ebc783
  mediu que "1.10 aprovaria só 2 dos 38" contra a mínima — contra um snapshot a taxa de reprovação é ~50%);
  (iii) `record_observations` grava o snapshot estático no `price_log` todo dia (`pipeline.py:57`) — o
  "histórico próprio" do ML é uma constante.

- **[A] `ev_score` é preço com maquiagem: o topo da fila é o item mais caro e o LLM só vê os 30 mais caros; o "piso de qualidade" não corta nada**
  — `selection.py:52-65`, `MAX_CANDIDATES_FOR_PROMPT = 30` (:8, :99). Amplitude dos fatores na faixa do
  config: preço/comissão absoluta **50×** (R$ 20 → R$ 1.000); popularidade máx **2,5×** (1 + 0,3·log10(10⁵));
  desconto verificado máx 1,5× (e **1,0× nos primeiros 30 dias**, pelo achado anterior); watchlist máx
  1,5 × 1,3 = 1,95×. Câmera R$ 800 a 3% com 100 vendas: 24 × (1 + 0,3·2,0) = **38,4**. Creatina R$ 30 a
  10% com 50 mil vendas: 3 × (1 + 0,3·4,7) = **7,2**; com TODOS os boosts 7,2 × 1,95 = 14 — ainda 2,7×
  atrás. O prompt de ranking reforça ("priorizando maior retorno esperado", :84-85) e o fallback é EV puro
  (:108). Com `posts_per_run: 1`, a manhã do canal (e o feed do IG às 21:00, A-1) é eletrônico/kit caro;
  o impulso barato sai depois. Como o estoque < teto (C-1), TUDO elegível é postado no mesmo dia — o
  ranking (LLM incluso) decide apenas a ORDEM; "seleção por retorno esperado" (pitch.md:46-47) = "postamos
  o que a API devolve, em ordem de preço". `min_ev_brl: 0.50`: R$ 20 × 2% × (1 + 0,3·log10(7)) = 0,50 →
  só corta item de R$ 20 com < 6 vendas e ≤ 2% de comissão; "evita postar sobras quando o estoque esgota"
  (config.yaml:26-27, README:101-104) — o esgotamento não é evitado, é atingido no dia 1.

- **[C] LLM fora → 100 posts/dia com a MESMA headline, e nenhum aviso em lugar nenhum** — **zero silencioso #5 (c)**
  — `copywriter.py:41-56` devolve `fallback_copy` em silêncio; `selection.py:100-108` idem no ranking;
  `pipeline.py` não tem nenhum `warning` para fallback; `llm.py:20-33` transforma QUALQUER falha em `None`
  (`claude` fora do PATH do systemd, `CLAUDE_CODE_OAUTH_TOKEN` expirado, `returncode != 0` por cota da
  janela de 5 h do Max — que o C-2 torna provável ao queimar ~2.500 chamadas/dia). Resultado no canal:
  "🔥 Achado do dia / Vale o clique: confira os detalhes e a avaliação. / Garanta o seu 👇" em cada um dos
  100 posts (modo B é o caso dominante, C-6). O chat de ops vê "✅ Publicados (1): • título" — idêntico a
  um run saudável. Não há mecanismo de variedade na copy: o prompt (`copywriter.py:14-24`) não recebe copies
  recentes, `tone` (config.yaml:88) é uma string fixa, e `recent_titles` (`state.py:56`: 7 dias, `limit 30`)
  alimenta só o ranking — a 100/dia cobre as últimas 2,5 h.

- **[A] "Resumo só quando há algo, evita 288 mensagens/dia" — qualquer estado persistente vira 192 mensagens/dia**
  — `cli.py:288-290` (`warnings` conta como "algo"). Estados que geram aviso em TODO run:
  watchlist vencida (`pipeline.py:35-38`; `data/watchlist.json` `generated_at 2026-08-23` + `valid_days 14`
  → vencida desde **2026-09-07**), pool ML vencido (`pipeline.py:50-54`; `meli_offers.json` 2026-08-26 + 30 →
  **2026-09-26**), teto diário atingido (`pipeline.py:131-134`, C-2/C-3: todo run das 13:20 às 21:00),
  candidata ML/Shopee que falha validação e fica no topo da reserva (C-7 (ii): 192 "descartados" iguais por
  dia). config.yaml:122-123 e README:114-116 prometem o contrário.

- **[M] "Trending" é rótulo: 23 IDs com boost ≤ 1,5× que não vence o fator preço, validade de 14 dias, e sem prova de que aparecem na listagem de afiliados**
  — `data/watchlist.json`: 23 `hot_items` (boost 1,2–1,5), 23 `price_floors`, 0 `price_refs`;
  `watchlist.py:36-38` só multiplica se `offer.item_id` estiver no que `productOfferV2` devolveu — nenhuma
  evidência no repo de interseção entre os IDs do JoomPulse (`ShbMartItem`) e as ofertas do programa de
  afiliados. `category_boosts` 1,05–1,3 (todas as 5 categorias do allowlist recebem boost, i.e., não
  discriminam nada entre si). Em 2026-09-07 vira `None` (`pipeline.py:38`) e o "diferencial de inteligência
  semanal" (pitch.md:48-49) some — com o aviso por run do A-3.

- **[M] Instagram "automático" é o feed (2/dia); os stories são 6 gestos manuais/dia, todos às 21h**
  — `channels/story_dispatch.py:1-5, 46-49`; pitch.md:50-51 ("um único operador dedicando minutos por
  dia"). 6 × 365 = 2.190 stories manuais/ano, entregues em bloco 21:00–21:25 (A-1).

- **[M] Documentação e config discordam entre si (quem opera vai calibrar pelo número errado)**
  — README:107-108 "telegram em 120/dia" vs config.yaml:114 (100); config.yaml:26, README:91, spec §3
  "288 execuções/dia" vs timer 08–23h = 192; spec §5.2 "sobram ~30–50 candidatas" vs 97 medidas; spec
  §5.3 "ex.: 3" por run vs 1; spec §8 "Preço: atual < original; desconto anunciado ≈ calculado" não existe
  mais (`validate.py:30-44`); `afiliado.service:19` "ciclo típico < 2 min" vs C-2 (18 min no teto).

## Afirmações NÃO refutadas (tentei e não consegui)

- **Run "feliz" cabe em 5 min.** Estimativa pelo código: 10 GraphQL (~1 s cada) + 1 ranking LLM (~8 s) +
  1 copy LLM (~8 s) + 1 `generateShortLink` + 2 GETs de validação (timeout 20 s, `validate.py:14`) +
  sendPhoto + 2 renders Pillow com download da imagem (`creative.py:65` timeout 20 s) + 2 Telegram + 2 Graph
  ≈ 40–70 s. Pior caso com 3 timeouts de LLM (120 s × 3, `llm.py:20`) = 360 s > 300 s, mas o systemd não
  sobrepõe oneshot ativo — perde 1 slot, não duplica. Sem rede não dá para medir.
- **Volume dos canais concorrentes (promosdenat etc., "30–60 achadinhos/dia").** Fora do repo; não
  verificável aqui. A comparação vale em qualquer caso pela aritmética de C-1.
- **Turnover real T da listagem `productOfferV2`.** Precisa da API. A refutação de C-1 vale para todo
  T < 100; o próprio config (:55, :60-61) descreve a listagem como estável.
- **`commission` da Shopee = R$ sobre o preço atual.** `shopee.py:158` assume; sem doc da API no repo.
- **Interseção `hot_items` × ofertas de afiliado.** Precisa da API.

## Riscos fora do código

- **Cota da Shopee Open API** sob o C-2: ~2.500 `generateShortLink` + 460 `productOfferV2`/dia a partir do
  primeiro dia em que o teto é batido; um bloqueio da API aborta o run (`shopee.py:53-54`) — esse SIM
  notifica, mas depois de queimar a cota.
- **Janela de 5 h do Claude Max**: ~2.500 chamadas/dia (C-2) contra a expectativa de 200; ao estourar, o
  C-9 (copy idêntica, silencioso) entra automaticamente e sem trilha.
- **Reação da audiência**: 100 posts/dia sem desconto (C-6), os mais caros primeiro (A-2), calando das
  13:20 às 21:00 (C-3), com headline repetida quando o LLM cai (C-9). Não há métrica de saída de membros
  no pipeline; o primeiro sinal será o painel de afiliados vazio.
- **Vendedores com preço alternado** (flash sale recorrente) são os que mais aparecem em "mais vendidos"
  na Shopee; o C-5 transforma cada um deles num "62% OFF (verificado)" com o nosso nome.
- **Fuso da VPS**: `install-vps.sh:32` engole a falha do `timedatectl`; sem `America/Sao_Paulo` o timer
  roda 08–23 **UTC** = 05:00–20:55 BRT, e o C-3 muda de forma (teto e IG às 21:00 UTC = 18:00 BRT).
- **`Persistent=true`** (`afiliado.timer:12`) dispara um run fora da janela ao religar a VPS de madrugada.

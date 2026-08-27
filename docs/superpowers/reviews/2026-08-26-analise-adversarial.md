# Análise adversarial — 2026-08-26

**Alvo:** HEAD `31700c9` (fases 1 → 3B → 4: Shopee + Telegram, Mercado Livre
híbrido, régua honesta de preço). 288 testes verdes. Nenhum post real publicado
ainda; `publish` do Actions desativado; VPS não instalada; `sources.meli: false`.

**Método:** cinco revisores independentes, cada um com uma área e a ordem de
**refutar** o que o projeto afirma — não confirmar. Só entrou achado com
evidência (arquivo:linha, cálculo ou script de reprodução executado). Os
relatórios brutos e os scripts estão em [`2026-08-26-adversarial/`](2026-08-26-adversarial/).

## Veredito

O sistema como está **não entrega a meta e, em três pontos, faria o oposto do
que promete**: publicaria ~18 posts/dia sustentáveis (não 50–100), calaria
sete horas por dia, e — no Mercado Livre — certificaria como "verificado" um
desconto contra o preço de um dia só. Nada disso está coberto pelos 288 testes.
O que resistiu ao ataque: nenhum segredo no histórico do git, nenhum caminho
que publique link sem atribuição, escape de HTML, scripts de deploy.

Os achados convergem em **quatro defeitos estruturais**, cada um apontado por
dois ou mais revisores por caminhos diferentes:

| # | Defeito | Quem achou |
|---|---|---|
| 1 | Estoque de ofertas × dedupe de 30 dias = 17,9 posts/dia; esgota no dia 1 | dinheiro, produto |
| 2 | Teto atingido → cada run varre a fila inteira (LLM + links + cliques), publica 0, estoura 600 s | produto, operação, régua |
| 3 | Dia contado em UTC → canal cala 13:20–21:00 BRT, fura o teto no dia 1, Instagram sempre às 21h | produto, operação |
| 4 | O quinto zero silencioso: filtro zera N>0 ofertas e ninguém é avisado; fonte/canal sem env só faz `print` | régua, operação, produto |

E em **um defeito de honestidade** que atravessa a régua inteira:

| 5 | A referência de preço do ML é a foto de um dia; a mediana conta dias e não vendas; o selo tolerante mente até 5%; a copy ignora o modo B | régua, dinheiro, produto |

---

## Críticos — bloqueiam o lançamento

### C1. A meta de 50–100/dia é aritmeticamente impossível com este estoque
- **Onde:** `shopee.py:74-95` (5 categorias × 1 ordenação × 2 páginas × 50 = teto de 500 nós/run, o mesmo conjunto a cada 5 min), `selection.py:43` (dedupe), `config.yaml` `dedupe_days: 30`.
- **Conta:** 38 ML + ≤500 Shopee = 538 itens únicos / 30 dias = **17,9/dia sustentáveis**. Dia 1 publica as ~97 candidatas até ~16h; dias 2–30 só o que entra de novo na listagem de "mais vendidos" (simulação: turnover 10/dia → 11/dia); dia 31 repete o mês anterior. ML: 37 elegíveis / 30 dias = 1,2/dia.
- **Dano:** o canal que promete 50–100 entrega 5–20 a partir do dia 2 — e o esgotamento é um "run vazio", indistinguível de "tudo bem" (ver C4).
- **Correção:** decisão do dono (ver "Decisões"): reduzir `dedupe_days` (7–10; os canais concorrentes repetem produto semanalmente) **e** alargar a descoberta (mais categorias, `pages` 5+, `sort_types` variados, `listType` alternativo, palavras-chave). Para 100/dia com dedupe de 7 são ~700 itens únicos/semana.

### C2. Teto atingido → o run gasta a fila inteira sem publicar nada
- **Onde:** `pipeline.py:74-129`. `count` só incrementa quando `published_any`; canal no teto faz `continue`; não há `break`. Cada oferta já pagou `refresh_price`, `generateShortLink` (mutação na Shopee), `write_copy` (até 2× `claude -p`, 120 s cada) e 2 GETs de validação antes de descobrir que não há canal.
- **Reprodução:** 97 candidatas, 3 canais no teto → **195 chamadas LLM, 97 links gerados, 0 publicados**, ~18 min > `TimeoutStartSec=600` → SIGTERM → sem resumo, ops em silêncio. Com o C3, isso acontece das 13:20 às 21:00 BRT: ~2.500 chamadas LLM/dia desperdiçadas (runbook promete ~200) e a cota do Max estourada — o que dispara o C4c.
- **Correção:** antes do laço e a cada iteração, verificar se algum canal ainda pode publicar; se nenhum, encerrar o run com o aviso de teto **uma vez**. Mover a geração de copy e de link para DEPOIS de saber que há canal.

### C3. Dia contado em UTC: canal cala 7h40 por dia e fura o teto
- **Onde:** `state.py:64-75` (a docstring admite). Timer 08:00–23:55 BRT = 11:00–02:55 UTC; a fronteira UTC cai às 21:00 BRT.
- **Reprodução com `StateDB` real:** dia 1 = **136 posts** (teto de 100 furado em 36%); regime = 64 posts 08:00–13:20, silêncio até 21:00, 36 posts 21:00–23:55. Instagram feed sempre às 21:00 e 21:05; as 6 artes de story em bloco 21:00–21:25 (o dono posta 6 stories à mão toda noite). "Espaçado, parece humano" — não.
- **Correção:** contar o dia em `America/Sao_Paulo` (config `timezone`) e **espaçar** o teto pela janela (intervalo mínimo por canal = janela / `max_per_day`), para que feed e stories se distribuam ao longo do dia.

### C4. O quinto zero silencioso (três formas)
- **(a) Filtro zera tudo sem aviso** — `selection.py:22-49` tem seis `continue` sem contador; `pipeline.py` não avisa quando `candidates == []`; `cli.py:288-290` não notifica run vazio. 50 ofertas entram, 0 sobram, ops recebe nada. Já em produção: o item `MLB36931922` (R$ 19,90 < `price_min_brl: 20`) é descartado em silêncio em todo run — morto por construção.
- **(b) Fonte/canal ligado sem env** — `cli.py:39-41,125,138,153` só faz `print()` no journal. O runbook da VPS lista "8 variáveis" e o template do `.env` não tem `MELI_*`: quem seguir o runbook e ligar `sources.meli: true` terá o ML mudo para sempre. `IG_ACCESS_TOKEN` em branco = Instagram nunca posta e ninguém avisa.
- **(c) LLM fora → 100 posts/dia com a MESMA headline** — `llm.py:20-33` transforma qualquer falha em `None`; `copywriter.py` devolve o fallback sem registrar; nada entra em `warnings`. O chat de ops vê "✅ Publicados (1)", igual a um run saudável.
- **Correção:** o resumo passa a reportar "N buscadas → M candidatas" com a contagem por portão; fonte/canal ignorado vira `warning`; contador de fallbacks de LLM no resumo; e um **heartbeat** diário ("vivo, X posts hoje") + `OnFailure=` no systemd — porque hoje uma VPS morta (a Oracle Always Free recolhe VMs ociosas, e esta fica ociosa >95% do tempo) é indistinguível de "sem oferta boa".

### C5. O resumo de ops é descartado em silêncio quando passa de 4096 caracteres
- **Onde:** `channels/telegram.py:59-66` ignora a resposta da API (4xx não levanta em httpx); `RunSummary.text()` não trunca. ~37 linhas de descarte já estouram.
- **Dano:** exatamente o run de falha em massa (bot removido, chat id errado, pool de links vazio) produz o resumo gigante → 400 → ninguém sabe, a cada 5 min. É o canal por onde passam TODOS os outros avisos.
- **Correção:** dividir em mensagens de ≤4000 chars, truncar descartes repetidos ("+31 iguais"), logar `ok: false`.

### C6. Cada validação é um clique real no link de afiliado — inclusive em `--dry-run`
- **Onde:** `validate.py:14-27` faz GET no link curto com `follow_redirects=True` e User-Agent de navegador; `pipeline.py:98` chama o validador ANTES do `if dry_run` (`:103`).
- **Dano:** (1) **o teste de atribuição do ML está contaminado** — cada `afiliado run --dry-run` já registrou clique no link que o dono tentava isolar; (2) em produção, 50–100 links/dia recebem o primeiro clique segundos após criados, do mesmo IP de datacenter, com UA idêntico e falso, antes de qualquer humano — assinatura de tráfego inválido para os dois programas (risco: encerramento da conta = 100% da receita); (3) o portão valida só o **host**: URL pura sem `ref`, `meli.la` respondendo 403 ou página "link não encontrado" em 200 — todos passam com comissão zero.
- **Correção:** não tocar o link de afiliado. Validar a **página do produto** (URL sem atribuição) e a imagem; confiar no gerador (API oficial / painel, gerado segundos antes). `--dry-run` sem nenhuma rede além da descoberta e sem escrita no `state.db` (hoje grava `price_log`).

### C7. Régua do Mercado Livre: referência de um dia, preço vivo do vendedor mais barato
- **Onde:** `meli-setup.md:117` ("preço no momento da curadoria"), `sources/meli.py:264-277`, `pricing.py:1-13` (docstring diz "mediana do nosso histórico").
- **Evidência no pool real:** ref/mínima histórica mediana **1,54×**, 9 de 38 itens ≥ 2,5×, máx **3,67×**. `MLB66637233`: ref R$ 78,90, mínima R$ 30,51 → ao vivo R$ 32,00 → **"De: R$ 78,90 | Por: R$ 32,00 (59% OFF)"** + selo falso (abaixo). O "De" é o preço de UM vendedor num dia; o "Por" é o MENOR entre até 64 vendedores — populações diferentes, desconto quase garantido. E a página de catálogo mostra o vencedor do buy box, não o mais barato: o post diz R$ 32 e o clique pode mostrar R$ 45.
- **Correção:** (a) `price_ref_cents` do pool = mediana ponderada por dias de 90 dias do `MlbProductPricesDaily` (JoomPulse), com `p25_cents` e `window_days` — regenerar o pool; (b) preço vivo = **buy box** (`/products/{id}` deve trazer `buy_box_winner`; verificar ao vivo), nunca o mínimo entre vendedores; (c) gravar o preço vivo no `price_log` (hoje grava o do pool todo dia — o "histórico próprio" do ML é uma constante); (d) validar o pool na curadoria contra o config (`price_min_brl`, ref vs mínima plausível).

### C8. A mediana conta dias, não vendas — e certifica o padrão "tabela alta + promoção recorrente"
- **Onde:** `pricing.py:27-37`, `models.py:43-49`, `pricing.py:138-141` (`ref_min_observations: 5`).
- **Reprodução com as funções reais:** vendedor segura R$ 68,90 em 3 de 5 dias e vende a R$ 26 nos outros → "De: R$ 68,90 | Por: R$ 26,00 (62% OFF) verificado"; alternando dia sim/dia não → "De: R$ 47,45" (preço que nunca existiu); rampa 100×5 dias + 150×7 dias + hoje 120 → "De: R$ 150 | Por: R$ 120 (20% OFF)" num preço 20% ACIMA de duas semanas atrás. Só o caso do docstring (89 dias baixo, 1 dia alto) é pego. Com 5 observações, "mais da metade dos dias" são 3 dias.
- **Correção — regra do quartil:** o "De" continua sendo a mediana (o preço típico), mas o post só **alega** desconto quando o preço de hoje está no **quartil mais barato** da janela (`current ≤ P25`) e a janela tem ≥ **14 dias** distintos. Vendedor que alterna cai (26 está na mediana, não no P25); rampa cai (120 > P25 = 100); promoção genuína (10% dos dias a −20%) passa. Promoção permanente vira modo B — correto: o preço novo É o típico. Watchlist e pool passam a carregar `p25_cents`.

### C9. O selo "Menor preço já registrado (verificado)" mente até 5%, e texto e arte discordam
- **Onde:** `message.py:23-25` × `seal_tolerance: 1.05`; `creative.py:491-492` (arte só aceita selo da watchlist); legendas do IG/story nunca têm selo.
- **Evidência:** piso 24000, preço 24999 → selo no Telegram; a arte do mesmo post sem selo; para o ML a arte NUNCA tem selo (a watchlist só tem IDs Shopee). O projeto codifica a mentira em `tests/test_message.py:121-125`. A palavra "verificado" ao lado de um fato falso é o que transforma reclamação em denúncia.
- **Correção:** remover o degrau tolerante (`seal_tolerance` sai); selo só quando `current ≤ piso` estritamente, com a janela REAL medida ("últimos N dias"); **uma única função** decide o selo para texto, arte e legendas.

### C10. A copy ignora o modo B: headline "4% OFF" sobre um bloco de preço sem De/Por
- **Onde:** `copywriter.py:14-33` — o prompt informa "Desconto verificado: 4%" e só proíbe palavras de desconto quando é 0; o fallback escreve "🔥 Oferta: 4% OFF". `min_real_discount_pct` foi aplicado em `pricing`, `creative` e `message`, não em `copywriter`.
- **Dano:** post contraditório ("4% OFF" + "R$ 25,00" sem "de") = parece fabricado.
- **Correção:** o copywriter recebe a MESMA decisão de modo que `price_line`; em modo B, desconto = 0 no prompt e no fallback.

### C11. Watchlist vencida troca a régua inteira sem trocar de aviso
- **Onde:** `pipeline.py:35-38` zera `watchlist` quando `is_stale()`; `pricing.py:134-146` perde os degraus 2; `pipeline.py:89` passa `price_floor=None`.
- **Evidência (mesmo produto, mesmo dia, mesmo histórico):** fresca → "De: R$ 30,00 (27% OFF)", sem selo; vencida → "De: R$ 25,00 (12% OFF)" **com** selo tolerante para um preço 4,2% acima do piso que a watchlist acabou de dizer que existe. A degradação torna a alegação MAIS agressiva. Acontece em **2026-09-07** com o arquivo atual, e a VPS nunca faz `git pull`.
- **Correção:** watchlist vencida perde só os **boosts**; referências e pisos são fatos datados e continuam valendo (com a data real na janela). Aviso de vencimento uma vez por dia, não por run.

---

## Sérios — corrigir antes de ligar

- **A1. `{"chosen": null}` do LLM derruba o run inteiro a cada 5 min** — `selection.py:104` (`TypeError` fora de qualquer `try`, `pipeline.py:61`). O item gatilho nunca é publicado, logo nunca sai do top-30 por EV: interrupção total sem auto-recuperação. Guardar o tipo; cair no ranking determinístico.
- **A2. `claude -p` é um agente com ferramentas, não uma função de texto** — `llm.py:25-28` não passa `--tools ""`/`--bare`/`--disallowedTools`/`--setting-sources`; o subprocesso herda as 11 variáveis de segredo; hooks em `.claude/settings.json` do repo executam em modo headless (reproduzido com canário: o CLI leu um `.env` do CWD e devolveu o token sem pedir permissão). Títulos de produto entram verbatim no prompt. Exploração por título não demonstrada (Haiku resistiu 2×), capacidade sim. Rodar sem ferramentas, com `env=` mínimo e sem settings do repo.
- **A3. Aviso idêntico 192×/dia** — watchlist vencida, pool vencido, teto atingido: qualquer estado persistente vira uma mensagem por run (`cli.py:288-290`), treinando o dono a ignorar o chat onde chegam os avisos reais. Deduplicar avisos por dia (tabela `warned`).
- **A4. Política de falhas §9 não implementada** — sem backoff em fonte (`HTTPTransport(retries=3)` só repete erro de conexão), Shopee 5xx aborta o run inteiro **inclusive o ML** (`pipeline.py:44`, sem isolamento por fonte); Telegram 429 com `retry_after` é ignorado e o pipeline segue para a próxima oferta dentro da janela de rate limit. Isolar fontes (falha de uma vira aviso), honrar `retry_after`.
- **A5. Token do bot do Telegram é entregue à Meta em cada post de feed** — `instagram_feed.py:104-116` usa `api.telegram.org/file/bot{TOKEN}/...` como `image_url`. O que expira é o `file_path`; o token é o segredo permanente do administrador do canal público. Hospedar a arte por um **bot secundário sem direitos** (ou outro host), nunca pelo bot do canal.
- **A6. `data/meli_links.json` não existe em nenhum checkout e nunca foi commitado** — o runbook diz "é commitado"; `doctor` imprime ✅ para o ML sem olhar o pool de links. Com `sources.meli: true` num clone limpo: 34 descartes + 34 chamadas à API do ML por run. `doctor` passa a checar o pool de links; o ML só liga quando ele existir.
- **A7. Nenhum post identifica que é publicidade / link de afiliado** — CDC art. 36, guia CONAR para influenciadores (2021), política de conteúdo de marca da Meta. Um canal que se chama "Fiscal" e omite a comissão em cada post é o perfil que gera representação no CONAR. Linha fixa em todo post: "🔗 link de afiliado · #publi" (decisão de texto do dono).
- **A8. `ev_score` é preço com maquiagem** — amplitude do fator comissão absoluta 50× (R$ 20 → 1.000) vs popularidade máx 2,5× vs desconto 1,5×: câmera de R$ 800 a 3% com 100 vendas (EV 38) vence creatina de R$ 30 a 10% com 50 mil vendas (EV 7,2, ou 14 com todos os boosts). O LLM só vê os 30 mais caros (`MAX_CANDIDATES_FOR_PROMPT`). Apresentar ao ranker um **slate diverso** (top por EV + top por vendas + top por desconto verificado) e amortecer a comissão (`commission_brl ** 0.7`).
- **A9. Actions "backup" não é backup** — 16 crons/dia × 1 post = 16/dia; proibido rodar junto com a VPS; `git push` sem `pull --rebase` perde o estado do run (dedupe e teto furados); `refresh_token` do ML funciona uma única vez no runner efêmero. Ou vira failover real (heartbeat dispara), ou se assume só como `workflow_dispatch` manual.
- **A10. `--dry-run` altera o `state.db` e clica** — `record_observations` roda sempre (só `record_run` respeita `dry_run`). Dry-run sem efeitos.
- **A11. Config `0` vira o default em silêncio** — `sel.get(k) or DEFAULT` em `pricing.py`, `pipeline.py`, `cli.py`: `min_real_discount_pct`, `ref_min_observations`, `ref_window_days`, `seal_tolerance` não podem ser 0; a docstring de `pricing.py:64-65` diz que 0 é suportado (inalcançável). `is None`.
- **A12. Stories são 6 gestos manuais/dia (às 21h), e o sistema os conta como publicados** — `story_dispatch.py` manda a arte ao chat de ops; `pipeline.py:123,128` grava `record_post`. Registrar como "despachado"; o spec para de chamar de automático.
  - **RESOLVIDO em 2026-08-27 (fase 5E), pela raiz:** os 6 gestos deixaram de existir. A premissa do achado — "a API não publica story" — estava errada; o fluxo foi testado AO VIVO na conta real e publicou (`POST /{ig_user_id}/media` com `media_type=STORIES` e sem `caption`, polling do container, `media_publish`). Prova: o container `18090130007292530` virou um story com `media_product_type: "STORY"` e `permalink: https://www.instagram.com/stories/ofiscaldapromo/<media_id>` (o `media_id` devolvido pelo `media_publish` começa em `1810721…`). O canal novo é `instagram_story` (`max_per_day: 6`), publicação de verdade — `manual=0`, entra em `summary.published`, em `day_stats().published` e no heartbeat. `story_dispatch` fica desligado como fallback manual, e a metade do A12 que sobrevive é ele: quando ligado, continua sendo despacho, não publicação. Os 6 fatos medidos estão em `docs/runbooks/meta-setup.md`.

## Menores

- `.gitignore` não cobre `data/meli_token.json.tmp` (sobra da escrita atômica, contém o refresh_token); os skills commitam sem `git add` de caminhos explícitos.
- `install-vps.sh` não é re-executável (`git pull` como root após `chown` → "dubious ownership"); `timedatectl` falha em silêncio → timer em UTC = 05:00–20:55 BRT; `Persistent=true` dispara run de madrugada ao religar; `curl | bash` como root; Node/Claude Code/deps Python sem pin; PAT com escopo de escrita em texto puro no `.git/config`.
- Download de imagem sem teto de bytes nem `MAX_IMAGE_PIXELS` (VM de 1 GB).
- 9,5000% vira "10% OFF" (banker's rounding) e passa no mínimo 10.
- "Últimos 90 dias" são 91; watchlist em data local, `price_log` em UTC; watchlist de 13 dias ainda diz "últimos 6 meses".
- Links do ML sem etiqueta nem data no pool: trocar `meli.tag` não regenera nada; link revogado não é detectado.
- Comissão do ML (4%) é chute apresentado ao ranker como fato: 1% → 8% inverte a ordem ML/Shopee; watchlist só dá boost à Shopee.
- Documentação diverge do config: 288 vs 192 runs/dia, Telegram 120 vs 100, "8 variáveis" vs 11 secrets, spec §8 descreve validação de preço que não existe mais, `meta-setup.md` recomenda Variante A e o config roda B.
- `sendPhoto` por URL com as `.webp` do pool ML: se o Telegram recusar, cai para texto sem foto em silêncio (não testável offline).

## O que resistiu (tentaram e não conseguiram refutar)

- **Nenhum segredo colado no chat está em lugar nenhum do git**: `-S` por cada fragmento (AppID, client id/secret, códigos TG-, CSRF), `--diff-filter=A` em `*token*|*cookie*|*.env*|*state.db*|*meli_links*`, `fsck --unreachable` (6 commits + 2 trees grepados), dump de 21.136 linhas de `git log --all -p` com regex de token Telegram/Meta/ML e nomes de cookie — único hit é o template vazio do `.env` no runbook. `.env`, `meli_token.json`, `state.db`, `meli_links.json` nunca foram commitados.
- **Nenhum caminho publica link sem atribuição**: ML levanta `SourceError` sem link no pool; Shopee só devolve `shortLink` da mutação ou `offerLink` da API; `product_url` nunca chega a um canal.
- **Token morto do ML não derruba a Shopee**: a autenticação do ML acontece por oferta, dentro do `try`.
- Escape de HTML no Telegram cobre título/copy; `price_line_html` só envolve saída de `format_brl`; falha de parse aparece no resumo, não é silenciosa.
- `github-secrets.sh` usa `--env-file` (nada em argv), `.gitattributes` força LF, `strip()` mata `\r` antes dos canais; `load_dotenv` tem CWD fixado pelo systemd.
- `state.db` não guarda segredo nem PII.
- Preço estritamente crescente É bloqueado por `max_above_ref`; `MIN` por dia UTC e as janelas de `price_history`/`prune` são consistentes entre si.
- Injeção no prompt de ranking só consegue reordenar itens já elegíveis (raio de dano: um post por item a cada 30 dias).
- Shopee `commission` é R$ por unidade sobre o preço atual (fixture: 249,99 × 0,12 = 29,9988).
- `min_ev_brl: 0.50` não mata o pool do ML (menor EV = 1,74).

## Riscos fora do código

- **Atribuição do ML não provada**, e o teste isolado precisa descontar os cliques que o próprio pipeline gerou em cada dry-run (C6).
- Termos dos programas de afiliados (Shopee/ML) sobre cliques automatizados e sobre o endpoint interno do painel: não verificáveis offline; o dano de um encerramento é 100% da receita.
- Cota do Claude Max: base de ~384 `claude -p`/dia, cada um com o system prompt inteiro do Claude Code; o C2 multiplica por ~30×.
- Oracle Always Free recolhe VMs ociosas; GitHub desativa workflows agendados após 60 dias sem atividade.
- Cold start define a régua da Shopee: os primeiros dias de cada item viram a mediana; lançar em rampa pré-Black Friday grava o preço inflado como "típico" por 90 dias. Mitigação: `price_refs` do JoomPulse (mediana de 90 dias) para os itens mais vendidos das 5 categorias ANTES do primeiro post.
- Taxas reais do ML por categoria; `sales` do ML (30 dias, JoomPulse) vs Shopee (acumulado) no mesmo `log10`.

## Plano proposto — fase 5

**5A · O sistema não pode se matar** (C2, C3, C4, C5, A1, A2, A3, A4, A10, A11 + heartbeat).
Tudo código, sem decisão de produto. É o pré-requisito de qualquer teste real.

**5B · A régua diz a verdade** (C7, C8, C9, C10, C11 + `price_log` ao vivo, validação do pool, `p25_cents` na watchlist e no pool, pool do ML regenerado com medianas, buy box).
Inclui rodar `/watchlist-refresh` com `price_refs`/`p25` para os itens mais vendidos das 5 categorias — é o que aplica a régua na Shopee de fato.

**5C · Volume real e proteção da conta** (C1, C6, A5, A6, A7, A8, A12 + docs).
Depende das decisões abaixo.

**5E · O story publica sozinho** (A12 pela raiz, 2026-08-27).
Não estava no plano porque o plano herdou a premissa errada de que a API não
publicava story. Ela publica: `instagram_story` entra como canal automático e o
`story_dispatch` vira fallback manual desligado.

## Decisões do dono

1. **Cadência de repetição e meta realista.** `dedupe_days` 30 → 7? Meta inicial 40–60/dia enquanto a descoberta é alargada? (C1)
2. **Identificação de publicidade em todo post.** Texto sugerido: "🔗 link de afiliado · #publi". (A7)
3. **Validação sem clique.** Aceitar validar a página do produto em vez do link de afiliado — perde-se a checagem do link curto, ganha-se a conta. (C6)
4. **Regra do quartil** (C8): alegar desconto só quando o preço de hoje está entre os 25% mais baratos da janela, com ≥14 dias. Menos posts em modo A, todos defensáveis. Alternativa é manter a mediana pura e aceitar o risco do "62% verificado" no padrão de flash sale recorrente.
5. **Teste de atribuição do ML**: refazer, sem nenhum `afiliado run` (nem dry-run) durante o teste, e descontar os cliques do pipeline no painel.

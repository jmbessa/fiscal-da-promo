# Operação e continuidade — achados adversariais

HEAD `31700c9`. Reproduções em `adv/repro_ops.py` (9 cenários, sem rede; rodar com
`PYTHONPATH="$PWD/src" python adv/repro_ops.py` da raiz) e `adv/scan_history.py`.
Linhas citadas são do worktree `fase3b-meli-hibrido`.

## Afirmações refutadas

- **[C] Teto diário atingido → o run varre a fila INTEIRA gastando LLM, links e
  validações, e morre no `TimeoutStartSec`** — `src/afiliado/pipeline.py:74-129`.
  Afirma: "Pula o canal sem contar como falha" (spec §9, linha 201) e "Um ciclo
  típico leva menos de 2 min" (`deploy/afiliado.service:19`). Evidência: `count` só
  incrementa quando `published_any` (127-129); com os 3 canais no teto, cada canal
  faz `continue` (112-114), `published_any` fica `False`, o `break` (75-76) nunca
  ocorre e o `for offer in fila` roda até o fim — e ANTES do laço de canais cada
  oferta já pagou `refresh_price`, `resolve_affiliate_link` (POST
  `generateShortLink`, `shopee.py:98-104`), `write_copy` (até 2× `claude -p` com
  timeout 120 s, `copywriter.py:42-43`, `llm.py:20`) e `validate_post` (2 GETs).
  Reprodução 1: 60 ofertas na fila, canais no teto → **121 chamadas `ask_json`,
  60 POSTs de link, 0 publicados**. Reprodução 2: canal falhando (`chat not
  found`) → mesma varredura, 60 tentativas de publish. Quando acontece: o dia é
  contado em UTC (`state.py:64-75`), o timer roda até 23:55 BRT — 36 posts entre
  21:00 e 23:55 BRT + 64 pela manhã ⇒ teto 100 do Telegram bate ≈13:15 BRT; das
  13:20 às 20:55 são **92 runs/dia** neste modo. Com 100+ candidatas (Shopee: 5
  categorias × 2 páginas × 50, `config.yaml:57-62`) × ~5-10 s por `claude -p`,
  o run passa de 600 s → SIGTERM do systemd → sem `record_run`, sem resumo, ops
  em silêncio; e ~11 mil `claude -p`/dia queimam a cota Max (cada chamada carrega
  o system prompt inteiro do Claude Code) → no dia seguinte a copy cai no fallback
  (ver A5). Dano: dinheiro (cota), silêncio, VPS girando à toa 7,5 h/dia.

- **[C] Resumo de ops descartado em silêncio quando passa de 4096 chars —
  exatamente no run de falha em massa** — `src/afiliado/channels/telegram.py:59-66`
  e `pipeline.py:17-25`. Afirma: "Nada falha em silêncio — tudo aparece no resumo
  de operações" (spec §9, linha 188). Evidência: `send_text` faz `c.post(...)` e
  ignora a resposta; só captura `httpx.HTTPError`, e httpx não levanta em 4xx;
  `RunSummary.text()` não trunca. Reprodução 2+3: 60 descartes → `len(text()) =
  5800` → API responde `400 message is too long` → `send_text` devolve `None`,
  sem exceção, sem log. Cenário: bot removido do canal, `TELEGRAM_CHANNEL_ID`
  errado, 429 prolongado ou pool de links do ML vazio (ver A10) → todas as
  ofertas descartadas → resumo gigante → ninguém sabe; repete a cada 5 min.
  (~37 linhas de descarte já estouram: ~110 chars/linha.)

- **[C] Canal ou fonte ligado no config sem env = zero silencioso (o quinto)** —
  `src/afiliado/cli.py:39-41,125,138,153-154`. Afirma: "Canal ligado sem env
  necessária: aviso ... nunca derruba o run" (`cli.py:105-106`). O "aviso" é um
  `print()` no stdout (journal); `summary.warnings` não recebe nada e o chat de
  ops vê "✅ Run concluído — Publicados (1)". Reprodução 9: `instagram_feed` e
  `meli` ligados, env ausente → canais `['telegram']`, fontes `['shopee']`, zero
  aviso no resumo. Gatilho real já plantado: `docs/runbooks/vps-setup.md:68-81`
  diz "as mesmas 8 variáveis" e o template do `.env`
  (`deploy/install-vps.sh:41-50`) não tem `MELI_*` — quem seguir o runbook e ligar
  `sources.meli: true` na VPS terá o ML mudo para sempre. `IG_ACCESS_TOKEN` em
  branco = Instagram nunca posta e ninguém avisa.

- **[C] Sem heartbeat: VPS morta é indistinguível de "sem oferta boa"** —
  `cli.py:287-290` + `config.yaml:126-127` (`notify_empty_runs: false`) +
  `deploy/afiliado.service` (sem `OnFailure=`) + `cli.py:275-280` (captura
  `Exception`; SIGTERM do `TimeoutStartSec` não é exceção → nenhum "❌ Run
  abortado"). Afirma: "ops é avisada no Telegram"; runbook treina o dono a ler
  silêncio como normal (`vps-setup.md:122-125`). Host recomendado é Oracle Always
  Free (`vps-setup.md:41-44`), que **recolhe instâncias ociosas** (política
  pública: 7 dias abaixo de 20% de CPU/rede/memória) — este pipeline fica ocioso
  >95% do tempo. A VPS some, o chat cala, o "backup" está desativado e não se
  liga sozinho (A1). Nenhum dead-man's switch em lugar nenhum.

- **[A] "Actions como backup/redundância" não é backup** — `README.md:94-99`,
  spec linha 48-49, `.github/workflows/publish.yml:1-6`. Evidência: o próprio
  projeto proíbe rodar os dois (`vps-setup.md:15-18`, `install-vps.sh:72-74`:
  "publicariam a mesma oferta duas vezes") e nada faz failover automático —
  reabilitar o workflow é gesto humano. Vazão do "backup": 16 crons/dia
  (`publish.yml:10-11`) × `posts_per_run: 1` (`config.yaml:11`) = **16 posts/dia
  = 16-32% da meta**. `data/state.db` nunca foi commitado (`git log --all --
  data/state.db` vazio) — o "commita de volta" (README:95) nunca aconteceu. E no
  Actions o `refresh_token` do ML funciona uma única vez (`meli-setup.md:187-194`
  admite; runner efêmero perde `data/meli_token.json`).

- **[A] Actions: `git push` sem `pull --rebase` → estado do run perdido → dedupe
  e teto furados** — `publish.yml:50-57`. Checkout no SHA do disparo; qualquer
  push no `main` durante os 2-3 min do run → push non-fast-forward → passo falha
  → posts já publicados NÃO ficam em `posted` → run seguinte (com o `state.db`
  antigo) repete os mesmos itens (dedupe 30 d furado, `count_posts_today`
  subconta). Sem retry, sem rebase. Dormente enquanto o workflow está desligado.

- **[A] Política de falhas §9 não implementada: sem backoff em fonte, sem retry em
  IG/story, sem `retry_after`** — spec linha 194 "Retry com backoff (3x)" vs
  `sources/shopee.py:38-39` (`HTTPTransport(retries=3)`: httpx só repete erro de
  CONEXÃO, sem backoff, nunca 5xx/429) + `shopee.py:51-54` `raise_for_status` →
  `SourceError` → `pipeline.py:44` aborta o run inteiro, inclusive o ML
  (`cli.py:54-59` monta Shopee antes; nenhum isolamento por fonte). Numa janela
  de 5xx/429 da Shopee: "❌ Run abortado" a cada 5 min, zero posts de qualquer
  fonte. Spec linha 200 "Publicação falha | Retry 3x" vs `telegram.py:10-27`
  (repete só `httpx.HTTPError`; JSON `ok:false` — 429/400 — volta na hora),
  `instagram_feed.py:144-155` (1 chamada), `story_dispatch.py:50-51` →
  `telegram.py:69-87` (1 chamada). Reprodução 4: 429 com `retry_after: 35` → 2
  chamadas imediatas (`sendPhoto`, `sendMessage`), `ok=False`, sem sleep; o
  pipeline segue para a PRÓXIMA oferta dentro da mesma janela de rate limit.

- **[A] Toda a saída do Instagram (2 feeds + 6 stories) sai entre 21:00 e ~21:30
  BRT, todo dia** — `state.py:64-75` (dia UTC; 00:00 UTC = 21:00 BRT) ×
  `deploy/afiliado.timer:8` (roda até 23:55 BRT) × `pipeline.py:110-126` (cada
  post vai a TODOS os canais). O dia UTC começa às 21:00 BRT com o timer ativo:
  teto do IG consumido às 21:00/21:05, dos stories até ~21:25; às 08:00 BRT
  `count_posts_today` ainda devolve o teto (Reprodução 6: `= 2` às 08:00 BRT,
  `= 2` às 20:59 BRT). O dono precisa postar 6 stories à mão entre 21:00 e
  21:30 toda noite; o feed nunca posta de dia. Telegram idem: 36 posts
  21:00-23:55 + 64 até ~13:15 ⇒ até **136 posts por dia-calendário**;
  `config.yaml:114` chama 100 de "teto real da operação", `README.md:108` diz 120.

- **[A] LLM fora = copy genérica IDÊNTICA em 100% dos posts, sem aviso** —
  `llm.py:20-33` (timeout/exit≠0 → `None`; o CLI sai ≠0 ao bater o limite da
  assinatura Max), `copywriter.py:27-38,56` (fallback igual para todo produto),
  nenhuma linha em `summary.warnings` para isso. Reprodução 8: fallback passa em
  `check_copy` e é idêntico para dois produtos. Spec §9 linha 188 refutada de
  novo. Combinado com C1, a queda da cota é auto-infligida.

- **[A] Aviso idêntico 192×/dia — fadiga de alerta garantida** —
  `pipeline.py:33-38,50-54,131-134` + `cli.py:288-290` (envia sempre que
  `warnings` ≠ []). Reprodução 5: watchlist vencida, 3 runs vazios → 3 envios.
  `data/watchlist.json` gerada 2026-08-23, `valid_days: 14` → vence 2026-09-07;
  a VPS **nunca faz `git pull`** (`afiliado.service:18` só roda `afiliado run`;
  atualização é manual, `vps-setup.md:114`) — o arquivo instalado vence em ≤14
  dias e a mensagem dispara 192×/dia para sempre; `data/meli_offers.json`
  (gerado 2026-08-26, 30 d) idem em 2026-09-25. Linhas distintas possíveis por
  run: 5 (sem watchlist | vencida; meli pool vazio | N ignoradas; teto × 3
  canais); na fase de teto (C1) 3-4 delas disparam em todos os 92 runs.

- **[A] IG com token inválido = 192 fotos-lixo/dia no chat de ops + 192 linhas de
  descarte** — `channels/instagram_feed.py:79` (`_host_art` manda a arte ao chat
  de ops ANTES da Graph API, 85-92); o teto conta só sucessos
  (`pipeline.py:119-123`), então IG falhando é tentado em todo run. Não refutado
  que o token de Página não expira (`config.yaml:105`, `meta-setup.md:89-90`) —
  mas qualquer invalidação (troca de senha, sessão da Meta, revisão do app)
  dispara isso.

- **[A] Bot token do Telegram é entregue à Meta a cada post de feed** —
  `instagram_feed.py:104-116` → `telegram.py:106` monta
  `https://api.telegram.org/file/bot{TOKEN}/...` e passa como `image_url` para
  `graph.facebook.com` (85-89). Docstring (7-10) chama de "trade-off aceito".
  Quem vir essa URL (logs da Meta, ferramentas de debug do app, app comprometido)
  controla o bot: posta no canal público como a marca, lê o chat de ops, apaga
  mensagens → sequestro do canal.

- **[A] "Totalmente automático — nenhum post passa por revisão humana" é falso
  para stories, e o sistema conta o story como publicado** — spec linha 25 e
  155-158 ("Publicação continua 100% automática") vs
  `channels/story_dispatch.py:1-5` ("o dono do projeto posta a arte no app e cola
  o sticker"); `README.md:36-42` admite "semi-automático". `pipeline.py:123,128`
  grava `record_post` e `summary.published` quando a arte só chegou ao chat de
  ops; nada verifica se o story foi postado. São 6 gestos manuais/dia, às
  21:00-21:30 (A4).

- **[A] `data/meli_links.json` não existe em lugar nenhum — o ML nasce 100%
  descartado** — `meli-setup.md:160-161` ("é commitado"), `README.md:62-69`.
  `git log --all -- data/meli_links.json` vazio; ausente no worktree e no
  checkout principal (`data/` só tem `watchlist.json`). Ligar `sources.meli:
  true` num clone limpo: cada oferta ML paga `refresh_price` (OAuth POST + GET,
  `meli.py:199-227`, chamado ANTES do link em `pipeline.py:84-87`) e cai em
  `SourceError("sem link de afiliado no pool")` (`meli.py:231-236`). Com 38 itens
  no pool ranqueados na frente (EV ML ≈ R$ 3-7 vs `commission_brl` da Shopee), 38
  linhas ≈ 5 k chars → resumo descartado (C2) → silêncio. A skill grava o arquivo
  no Windows do dono; a VPS nunca faz pull (A6).

- **[M] `install-vps.sh` não é re-executável** — `deploy/install-vps.sh:25-26`
  faz `git -C /opt/afiliado pull` como root depois de `chown -R afiliado`
  (linha 54). git ≥ 2.35.2 (CVE-2022-24765; backport no Ubuntu 22.04 via
  USN-5376-1) → "fatal: detected dubious ownership" → `set -e` aborta no passo
  3/7. O caminho de atualização do runbook (`vps-setup.md:114`, como `afiliado`)
  funciona. Checar na VPS: `sudo git -C /opt/afiliado status`.

- **[M] `--dry-run` altera `state.db`** — `pipeline.py:57`
  (`record_observations` roda sempre; só `record_run` respeita `dry_run`, 136).
  Afirma "imprime em vez de publicar" (`cli.py:24`). Reprodução 7: 5 linhas em
  `price_log` após um dry-run. Com `state.db` commitado pelo Actions
  (`publish.yml:55`), um dry-run local suja um binário rastreado → conflito
  binário imergível no pull.

- **[M] Documentação diverge do que roda** — "288 execuções/dia" (`README.md:91`,
  spec 47, `config.yaml:26` — base do `min_ev_brl`) vs timer 08:00-23:55 = 192
  (`afiliado.timer:8`); Telegram "120/dia" (`README.md:108`) vs 100
  (`config.yaml:114`); "8 variáveis" (`vps-setup.md:69`) vs 11 secrets
  (`publish.yml:39-49`); `meta-setup.md:4-6` recomenda Variante A, `config.yaml:105`
  roda B; template do `.env` (`install-vps.sh:41-50`) sem `MELI_*`.

- **[M] Fuso cai para UTC em silêncio** — `install-vps.sh:32`: `timedatectl ...
  || echo` — em VPS container/LXC sem privilégio o timer roda em UTC → janela
  05:00-20:55 BRT (perde 21:00-23:55, o horário nobre) e desloca os tetos de A4.
  Só um echo na instalação.

- **[M] `Persistent=true` fora da janela** — `afiliado.timer:12`: VPS fora do ar
  às 23:55 e de volta às 03:00 → um run (e um post) às 03:00.

## Afirmações NÃO refutadas (tentei e não consegui)

- "Token morto do ML derruba a Shopee" — não: `fetch_offers` do ML é local
  (`meli.py:148-153`); a autenticação só acontece em `refresh_price`, por oferta,
  dentro do `try` (`pipeline.py:81-101`). Custo: 2 POSTs de OAuth falhando por
  oferta ML por run (`_access_token` fica `None`, `meli.py:54-56,58-82`).
- CRLF/quoting nos scripts: `.gitattributes:1-4` força LF em `.sh/.service/.timer`
  e `text=auto eol=lf` cobre `.py` e `SKILL.md`; `.env` não é rastreado mas
  `load_dotenv` (`cli.py:233,237`) e `_env` (`cli.py:66`) fazem `strip()` →
  `\r` morre antes de chegar aos canais. Li linha a linha; sem brecha.
- Segredos no histórico: varri 99 commits / 19.816 linhas de diff
  (`adv/scan_history.py`) por `VAR=valor` — só o template vazio em `8855266`.
  `.env`, `data/meli_token.json` nunca commitados.
- `OnCalendar=*-*-* 08..23:00/5:00` — sem `systemd-analyze calendar` no Windows;
  a sintaxe lê como horas 08-23, minutos */5.
- `TimeoutStartSec=600` limita run travado (oneshot respeita); o tick de +5 min é
  ignorado enquanto a unidade está ativa, não enfileira.
- `CLAUDE_CODE_OAUTH_TOKEN` via `.env` → `os.environ` → subprocess: caminho
  coerente; `shutil.which("claude")` acha `/usr/bin/claude` no PATH padrão do
  systemd com o prefixo do nodesource. Não executei numa VPS.
- Limites do Telegram a 1 post/5 min: muito abaixo; o 429 só importa no laço de
  falha (A3).
- `gh secret set --env-file` com CRLF (`github-secrets.sh:6-8`): não verificável
  offline.

## Riscos fora do código

- Oracle Always Free recolhe VM ociosa (C4) — e ninguém fica sabendo.
- Cota da assinatura Max (janela de 5 h + semanal): base de 384 `claude -p`/dia,
  cada um com o system prompt inteiro do Claude Code; o laço C1 multiplica por
  ~30×. `npm install -g @anthropic-ai/claude-code` sem pin no Actions
  (`publish.yml:34`) e na VPS (`install-vps.sh:21`); deps Python sem pin
  (`pyproject.toml:6`) — mudança de flag/saída do CLI vira fallback silencioso
  (A5).
- Injeção de prompt: títulos de produto entram verbatim no `claude -p`
  (`copywriter.py:18`, `selection.py:75`) com o toolset padrão do Claude Code
  ativo (`llm.py:26` não passa `--tools`/`--disallowedTools`); o CLI roda como
  `afiliado` em `/opt/afiliado`, onde `.env` (600, dono `afiliado`) é legível
  pela ferramenta Read sem prompt de permissão. Não testável sem o CLI.
- GitHub desativa workflows agendados após 60 dias sem atividade no repo — o
  "backup", mesmo ligado, para sozinho.
- `sendPhoto` por URL com as imagens `.webp` do pool ML (`data/meli_offers.json`):
  se o Telegram recusar, `telegram.py:47-52` cai para texto sem foto, sem aviso
  — degradação silenciosa em todo post ML. Não testável offline.
- `ProtectSystem=full` deixa `/usr/lib/node_modules` somente-leitura → auto-update
  do Claude Code falha em toda chamada (ruído no journal); versão futura pode
  recusar rodar desatualizada.
- ML: 192 rotações de `refresh_token`/dia na VPS (`meli.py:50-56` não reaproveita
  o `access_token` persistido — `_load_refresh_token` 110-121 ignora
  `access_token`/`expires_at` gravados em 131-135). Rate limit do
  `/oauth/token` do ML mataria a fonte; desconhecido.

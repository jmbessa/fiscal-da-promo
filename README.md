# Afiliado

Pipeline automático de divulgação de ofertas com link de afiliado
(Shopee → Telegram). Spec: `docs/superpowers/specs/2026-08-23-afiliado-design.md`.

## Setup local

1. `pip install -e .[dev]` (Python 3.12+)
2. `pytest` — a suíte não toca a rede.
3. Instalar Claude Code e logar (assinatura Max): `npm i -g @anthropic-ai/claude-code && claude`

## Credenciais (variáveis de ambiente)

| Variável | Como obter |
|---|---|
| `SHOPEE_APP_ID` / `SHOPEE_APP_SECRET` | Portal Shopee Afiliados BR → área de API aberta (Open API) → solicitar credenciais |
| `TELEGRAM_BOT_TOKEN` | Falar com o @BotFather no Telegram → `/newbot` |
| `TELEGRAM_CHANNEL_ID` | Criar canal público, adicionar o bot como administrador; usar `@nomedocanal` |
| `TELEGRAM_OPS_CHAT_ID` | Mandar `/start` para o bot no privado; pegar o `chat.id` em `https://api.telegram.org/bot<TOKEN>/getUpdates` |
| `CLAUDE_CODE_OAUTH_TOKEN` | Rodar `claude setup-token` na sua máquina (usa a assinatura Max) |
| `ART_HOST_BOT_TOKEN` | Bot **secundário** que hospeda a arte do feed (fase 5C). Crie um segundo bot no @BotFather e adicione **só ao chat de operações** — a URL da arte que vai à Meta carrega o token de quem a enviou, e não pode ser o do administrador do canal |
| `IG_USER_ID` / `IG_ACCESS_TOKEN` | Feed automático do Instagram (fase 2A) — seguir `docs/runbooks/meta-setup.md` |
| `IG_USERNAME` / `IG_PASSWORD` | Story **com figurinha de link** (fase 5F, canal `instagram_story_link`). É a **senha da conta**, não um token revogável — só na máquina do dono, **nunca** nos GitHub Secrets nem na VPS. Seguir `docs/runbooks/instagrapi-stories.md` |
| `MELI_CLIENT_ID` / `MELI_CLIENT_SECRET` / `MELI_REFRESH_TOKEN` | Fonte Mercado Livre (fase 3, desligada por padrão) — seguir `docs/runbooks/meli-setup.md` |

São **14 variáveis**: `SHOPEE_APP_ID`, `SHOPEE_APP_SECRET`, `TELEGRAM_BOT_TOKEN`,
`TELEGRAM_CHANNEL_ID`, `TELEGRAM_OPS_CHAT_ID`, `ART_HOST_BOT_TOKEN`,
`CLAUDE_CODE_OAUTH_TOKEN`, `IG_USER_ID`, `IG_ACCESS_TOKEN`, `IG_USERNAME`,
`IG_PASSWORD`, `MELI_CLIENT_ID`, `MELI_CLIENT_SECRET`, `MELI_REFRESH_TOKEN`. As
do Instagram e do Mercado Livre podem ficar vazias: o canal/fonte correspondente
é ignorado com aviso no chat de operações. `ART_HOST_BOT_TOKEN` vazia também
funciona — com um aviso diário, porque aí o token do bot do canal é o que viaja
até a Meta.

No GitHub: Settings → Secrets and variables → Actions → criar os secrets acima
(todos já repassados pelo `publish.yml`) — **menos `IG_USERNAME`/`IG_PASSWORD`**,
que não vão para o Actions de jeito nenhum: o canal que as usa não roda lá (ver
`docs/runbooks/instagrapi-stories.md`). Na VPS, o `.env` (template completo em
`deploy/install-vps.sh`), com essas duas vazias.

## Instagram (fase 2A)

As artes seguem o design system "Fiscal da Promo" (fase 2C): fontes Bricolage
Grotesque (variável) e IBM Plex Mono, paleta navy/dourado e o mascote
desenhado por código em `src/afiliado/brand.py` (ver `docs/brand-guidelines.md`);
nome e handle exibidos nas artes vêm de `brand:` em `config.yaml`.

Dois canais automáticos (feed e story) e um fallback manual desligado:

- **`instagram_feed` (100% automático via Graph API, teto de 2/dia)** —
  publica direto no feed (1080×1350) sem intervenção humana. Requer
  `IG_USER_ID` / `IG_ACCESS_TOKEN` (obtidos via `docs/runbooks/meta-setup.md`;
  o projeto roda a **Variante B**, `instagram.api: facebook_login`) e
  `channels.instagram_feed.enabled: true`. O caption do feed nunca leva o
  link de afiliado — só "🔗 Link na bio e no canal do Telegram" — porque a API
  não permite CTA clicável fora da bio. A arte é hospedada pelo bot de
  `ART_HOST_BOT_TOKEN` (ver Credenciais).
- **`instagram_story` (100% automático via Graph API, teto de 6/dia — fase
  5E)** — publica o story (1080×1920) sozinho: container com
  `media_type=STORIES`, polling do `status_code` e `media_publish`. Mesmas envs
  e mesma hospedagem de arte do feed; a cota de publicação da Meta (100/24 h) é
  **compartilhada** entre os dois. **Story não aceita legenda nem sticker de
  link pela API** — a arte já traz o handle e a chamada, e o link mora na bio e
  no Telegram. Até a fase 5C o projeto afirmava que a API não publicava story;
  a afirmação estava errada, e o teste ao vivo de 2026-08-27 está registrado em
  `docs/runbooks/meta-setup.md`.
- **`story_dispatch` (MANUAL, DESLIGADO desde a fase 5E, teto de 6/dia)** —
  fallback para o dia em que a conta perder a permissão de publicação. Gera a
  arte de story e a manda ao chat de operações do Telegram, seguida de uma
  segunda mensagem só com o link de afiliado; quem posta é você. Não depende de
  nenhuma credencial da Meta. No resumo do run essas ofertas aparecem numa
  seção própria — **"📤 Despachados p/ ops — postar no app"** —, fora da
  contagem de publicados (e fora do heartbeat da manhã: enquanto você não
  posta, ninguém publicou nada).

## Mercado Livre (fase 3, parte 1 — desligado por padrão)

Segunda fonte de ofertas, além da Shopee. Habilite em `config.yaml` →
`sources.meli: true` e configure `MELI_CLIENT_ID`/`MELI_CLIENT_SECRET`
(setup completo em `docs/runbooks/meli-setup.md`); sem essas credenciais o
run segue normalmente só com Shopee, com um aviso no stdout.

A autenticação tenta `client_credentials` primeiro (sem estado, ideal para
CI) e cai para `refresh_token` quando necessário — o Mercado Livre rotaciona
o refresh token a cada uso, então o pipeline persiste a rotação em
`data/meli_token.json` (segredo, gitignored) a cada troca.

Não há API oficial de link de afiliado no Mercado Livre — `resolve_affiliate_link`
lê um **pool de links pré-gerados** em `data/meli_links.json`, preenchido
pelo skill `/meli-links-refresh` (`.claude/skills/meli-links-refresh/`),
que gera links em lote pelo painel de afiliados (`src/afiliado/meli_links.py`)
e mescla o resultado no pool sem sobrescrever links existentes — item sem
link no pool é descartado (comportamento já existente do pipeline, promove
a próxima oferta da fila). Fluxo completo (pool curado de ofertas → preço
ao vivo → link) em `docs/runbooks/meli-setup.md`.

## Comandos

- `afiliado doctor` — verifica Shopee, Telegram e Claude CLI com credenciais reais.
- `afiliado run --dry-run` — pipeline completo (APIs reais) imprimindo os posts
  sem publicar. Sem efeitos colaterais: não escreve no `state.db`, não baixa a
  imagem e não toca no link de afiliado.
- `afiliado run` — executa e publica de verdade.
- `afiliado stories [--posts N] [--dry-run]` — o mesmo pipeline com **só o
  canal de API privada** (`instagram_story_link`, instagrapi, story com
  figurinha de link), para o dono rodar da própria máquina (fase 5F). É o
  único comando que o monta; `afiliado run` o ignora mesmo ligado, porque ele
  não pode rodar no GitHub Actions. O contrário também vale: os canais que o
  Actions publica — inclusive o `instagram_story` da Graph API — **não** sobem
  aqui, e este comando usa um **banco próprio** (`state.stories_path`, padrão
  `data/state_stories.db`, no `.gitignore`), então o dedupe dele é
  independente do resto.
- `afiliado feed [--tipo termometro|flagrante] [--dry-run]` — o conteúdo de
  feed da fase 5D, que **não** sai pelo `afiliado run`. `termometro` (padrão)
  monta o carrossel do dia (capa + até 6 ofertas + fecho) e publica pela Graph
  API; `flagrante` desenha o gráfico de 90 dias do "de" inflado de um vendedor
  e **despacha ao chat de operações** — nunca publica, porque nomear um
  vendedor é risco jurídico e isso não se automatiza. Em produção quem o chama
  são duas tarefas do Agendador do Windows (`FiscalDaPromo-Feed` e
  `FiscalDaPromo-Flagrante`); o teto de **uma peça por dia** vive no código,
  não na cadência (ver Agendamento). `--dry-run` grava as artes em
  `.claude/previews/` e não escreve no banco.
- `afiliado ig-login` — cria/renova `data/ig_session.json`, a sessão do
  instagrapi, lendo `IG_USERNAME`/`IG_PASSWORD` do ambiente. Um login
  bem-sucedido também **rearma** o canal, se ele tiver se desarmado hoje. Ver
  `docs/runbooks/instagrapi-stories.md`.

## Portões e política de falhas (fase 5A)

- **Link de afiliado nunca é clicado pelo pipeline.** A validação do link é
  offline (`https` + host em `validation.allowed_domains`); um GET no link
  curto seria um clique artificial do IP da VPS. Só a imagem é baixada.
- **Fontes isoladas.** A Shopee em 5xx vira aviso e o run segue com o ML; a
  Shopee repete até 3× com backoff (0,5 s, 1,5 s, 4 s) em 429/5xx/conexão. O
  run só aborta quando todas as fontes falham — e mesmo assim manda o resumo.
- **Telegram.** Erro de rede: 3 tentativas; `429` com `retry_after` ≤ 30 s:
  dorme e repete uma vez. O resumo de ops é dividido em mensagens de até 4000
  caracteres e uma resposta `ok: false` vai ao journal.
- **`claude -p` sem ferramentas.** O LLM roda com `--tools ""`, sem settings/
  hooks do projeto, em diretório temporário vazio e com um ambiente em lista
  branca (nenhum segredo de Telegram/Shopee/ML/Instagram).

## Watchlist semanal

`data/watchlist.json` alimenta o boost de ranking, a referência de preço da
Shopee (mediana + p25 + janela) e o selo "menor preço verificado". Atualize
**1x por semana** abrindo o Claude Code no projeto e digitando
`/watchlist-refresh` (requer o conector JoomPulse na sessão) — o skill em
`.claude/skills/watchlist-refresh/` faz consultas, arquivo e commit.
Validade: 14 dias; vencida, o pipeline roda sem boosts e avisa no chat de
operações — as referências e mínimas (fatos datados) continuam valendo.

## A régua diz a verdade (fase 5B)

O que um post alega é decidido UMA vez, em `pricing.verdict`, e texto, arte,
legendas e copy só obedecem:

- **Modo A ("De/Por, N% OFF")** só quando há referência com p25 e janela de
  ≥ 14 dias distintos, o preço de hoje está ESTRITAMENTE abaixo do p25 (no
  quartil mais barato da janela) e o desconto contra a mediana — arredondado
  para baixo — atinge `selection.min_real_discount_pct`. Preço alternado,
  rampa e "promoção recorrente" caem; promoção rara passa.
- **Modo B** (preço + prova social) em todo o resto — inclusive quando o
  vendedor anuncia "de R$ 350", que nunca aparece.
- **Selo** "Menor preço dos últimos N dias/M meses (verificado)" só quando o
  preço ≤ mínima conhecida, com a janela real; sem tolerância. A mínima
  curada (watchlist/pool) não envelhece impune: se o nosso próprio histórico
  de preços viu mais barato, a mínima passa a ser a NOSSA (a observação
  própria só baixa o piso, nunca o sobe).
- Mercado Livre: o pool (`/meli-pool-refresh`) traz mediana/p25/janela/mínima
  do anúncio que vence o buy box, e o preço publicado é o desse anúncio
  (`refresh_price`), nunca o vendedor mais barato. O vencedor muda: a
  verificação do buy box vale 7 dias (`buy_box_checked_at`; o skill tem um
  passo semanal que a renova) — vencida, a entrada é ignorada com motivo.

## Volume: 60 ofertas/dia (fase 5C)

A meta é **60 ofertas/dia**, somadas as duas lojas, com **dedupe de 30 dias**
e **cota 50/50** entre Shopee e Mercado Livre (`selection.source_quota` — a
fração é normalizada entre as fontes LIGADAS, então com o ML desligado a
Shopee fica com o teto inteiro; e se uma não tem candidata, a outra completa).

> **A cota é uma meta, não uma garantia — e a diferença é visível (fase 5I).**
> Com 37 produtos no pool do ML e dedupe de 30 dias, o ML sustenta ~1,2
> oferta/dia contra uma cota de 30: a Shopee cobre o resto e **nada falha**,
> porque a cota reparte o teto e nunca o deixa ocioso. Por isso o resumo de
> operações passou a dizer `🏷️ Hoje por fonte: meli X/30 · shopee Y/30` e a
> avisar quando uma fonte ligada fica abaixo de metade da cota, nomeando o
> motivo provável (estoque esgotado pelo dedupe). A correção de verdade é
> aumentar o pool.

Isso só fecha por causa da **descoberta rotativa**. A medição de 2026-08-26
contra a API real (147 chamadas —
`docs/superpowers/reviews/2026-08-26-descoberta-shopee.md`) mostrou que:

- cada `(categoria, sortType)` é uma janela de **40 páginas × 50 = 2.000
  itens** (`hasNextPage: false` na p40), e **54,6%** dos itens das raízes
  passam nos filtros — **≈ 5.460 elegíveis/mês só nas 5 raízes**, mais ≥ 6.000
  verificados nas subcategorias de nível 2 e ~1.700 das palavras-chave;
- a config antiga lia 2 páginas por categoria e as **relia a cada 5 min**:
  244 itens únicos por mês = **8 posts/dia** sustentáveis a dedupe 30 — e
  afrouxar o dedupe para 7 dias só levaria a 35/dia. O gargalo era
  profundidade de leitura, não o dedupe;
- 60/dia × 30 dias = 1.800 únicos/mês, contra 5.460 só nas raízes: **margem
  de 3×**, com o dedupe de 30 dias intacto.

Então, em vez de reler o mesmo topo, **cada run lê uma fatia diferente**
(`shopee.calls_per_run: 8` — 5 raízes, 2 subcategorias, 1 palavra-chave, uma
página cada) com um **cursor persistido** (tabela `discovery_cursor`); as
raízes inteiras saem a cada 40 runs (~3h20) e o espaço todo em cerca de um
dia. O que cada fatia acha entra num **estoque de candidatas** (tabela
`candidates`, validade `shopee.candidate_max_age_days: 3`), e as candidatas de
um run são o estoque ∪ a fatia da vez. O resumo do run mostra a conta:
`🔎 shopee: 8 chamadas · 400 nós · 212 elegíveis · 37 novos no estoque`.

Só o preço da fatia RECÉM buscada vira observação no `price_log` (o de uma
candidata de três dias não é "o preço de hoje"), e a oferta escolhida tem
preço e comissão atualizados ao vivo (`refresh_price`, 1 chamada por
`itemId`) logo antes de publicar — item que saiu da listagem é descartado.

`selection.min_ev_brl` continua cortando candidatas com valor esperado abaixo
do piso. Desde a fase 5C a comissão entra **amortecida** no EV
(`ev_weights.commission_exp: 0.7`) e o ranker recebe um **slate diverso**: a
união de 10 por EV, 10 por vendas e 10 por desconto alegável — antes o LLM só
via os 30 itens mais caros. Nenhuma categoria ocupa mais que a fatia dela
(`max(4, 30 ÷ categorias presentes)`: 6 vagas com as 5 raízes ligadas, 30 se
tudo vier de uma só), e o que sobrar das 30 vagas é completado por EV: o
prompt promete 30 candidatas e precisa entregar 30.

## Agendamento

- **A máquina do dono (produção desde 2026-08-28, fase 5I)** — quatro tarefas
  no **Agendador de Tarefas do Windows**, criadas por
  `deploy/agendar-windows.ps1` (idempotente, com `-Remover`):
  `FiscalDaPromo-Run` (`afiliado run --posts-per-run 4`) e
  `FiscalDaPromo-Stories` (`afiliado stories --posts 4`) **a cada 15 min** das
  08:03/08:08 às 23:15, mais `FiscalDaPromo-Feed` e `FiscalDaPromo-Flagrante`
  **a cada 2 h**. Runbook completo — a ordem da virada, como conferir que
  rodou, como voltar — em `docs/runbooks/producao-windows.md`.
  **Por que saiu do Actions**, com os três fatos medidos: (1) o agendador do
  GitHub entregou **1 run em toda a história do repositório** contra ~16
  disparos esperados em ~25 h, e o único saiu **51 min atrasado**; (2) o story
  com figurinha **não pode** rodar num IP de datacenter (`challenge_required`)
  e a Graph API não publica figurinha nenhuma; (3) a máquina foi medida em
  2026-08-28 com **48,7 h de uptime** e suspensão em corrente alternada = 0.
  **Por que 15 min:** medido, um `afiliado run` gasta **8 chamadas** de
  descoberta (sempre, mesmo sem publicar nada) + 2 por oferta publicada — 608
  por tarefa por dia, ~1.216 com as duas, contra os ~1.920/dia que a VPS já
  fazia. E é a cadência que faz o maior salto do `pacing_budget` cair para 1,
  com `--posts-per-run 4` cobrindo três disparos perdidos.
- **O buraco na cadência é o sensor (fase 5G, recalibrado na 5I)** — o resumo
  do chat de operações **acusa buracos** — em horas e em disparos perdidos —
  acima de `schedule.max_gap_minutes` (**40**, para a cadência de 15 min:
  tolera um disparo perdido e acusa a partir do segundo). É ele que denuncia
  uma máquina parada. O `afiliado doctor` completa: no Windows ele confere se
  as quatro tarefas existem e estão habilitadas.
- **GitHub Actions (fallback manual)** — `.github/workflows/publish.yml`
  perdeu o `schedule:` e ficou só com `workflow_dispatch`: dois hosts
  publicando ao mesmo tempo postariam a mesma oferta duas vezes (cada um tem o
  seu `state.db`, e é ele que guarda o dedupe). O disparo manual roda o
  pipeline **e** as duas peças de feed, com `--posts-per-run 5` (maior que o
  da máquina de propósito: emergência publica o orçamento acumulado), e
  commita `data/state.db` de volta com `git pull --rebase` antes do push.
  Aba Actions → publish → Run workflow (com opção dry-run).
- **Conteúdo de feed, 1×/dia (fase 5D, revisto na 5G e na 5I)** — `afiliado
  feed --tipo termometro` (publica o carrossel) e `--tipo flagrante` (despacha
  ao chat de ops para o dono aprovar). Quem garante o "uma vez por dia" é o
  código — o teto de `channels.instagram_carrossel` (1/dia, com o ritmo da 5A)
  e uma marca em `day_flags` para o flagrante, gravada só depois do despacho
  bem-sucedido —, e não a cadência do agendador: um disparo que falha é
  repetido pelo seguinte e a peça ainda sai no mesmo dia. A pesquisa pede 2–3
  carrosséis por semana; a cadência entregue é 7 — o teto e o ritmo da 5A
  mandam, e baixar é editar `max_per_day`.
- **VPS a cada 5 min (opcional)** — o timer systemd chama `afiliado run` a
  cada 5 minutos das 08:00 às 23:55 (192 execuções/dia, 1 oferta por run),
  para quem quiser cadência mais fina e um estoque de candidatas mais fresco;
  setup em `docs/runbooks/vps-setup.md`. `state.db` fica local e persiste
  sozinho (sem commit). **Nunca rode dois hosts ao mesmo tempo** — dois
  estados independentes furam o dedupe e o teto.

O teto e o ritmo mandam em todos os casos: `--posts-per-run` diz só quanto UM
run pode chegar a publicar; quem distribui os 60/dia pela janela é o
`pacing_budget` da fase 5A.

Cada canal tem um teto diário (`max_per_day` em `config.yaml`, contado no
SQLite **no dia local** de `schedule.timezone`): `telegram` em 60/dia (a meta
do canal), `instagram_story_link` em 60/dia e `instagram_feed` em 2/dia
(`instagram_story` e `story_dispatch`, os dois fallbacks, estão desligados).
Desde a fase 5A o teto é **distribuído pela janela** (`schedule.window_start`
– `window_end`): um canal só publica enquanto o que já postou hoje está
abaixo de `min(max_per_day, floor(max_per_day × fração da janela decorrida) + 1)`
— 60/dia vira ~1 a cada 15 min (a mesma cadência do agendador, e não por
acaso), o 2º feed só sai da metade da janela em diante, e fora da janela nada
é publicado. Quando
nenhum canal pode publicar, o run termina antes de chamar o LLM (nenhuma
oferta paga preço, link ou copy sem ter onde ser publicada); um canal que
bate o teto de verdade aparece como aviso no resumo, não como falha.

Com 61 runs/dia na máquina do dono (ou 192 na VPS), mandar um resumo a cada
execução inundaria o chat de operações. O resumo só é enviado quando o run publicou,
descartou algo ou gerou aviso — e cada aviso entra **uma vez por dia** (tabela
`warned`), então uma watchlist vencida não vira uma mensagem por run. O
primeiro run do dia manda um heartbeat ("☀️ Bom dia — ontem: N publicados, M
descartados em K runs"), sempre; um run vazio sem aviso novo não notifica.
Runs abortados (todas as fontes falharam) ou interrompidos por sinal notificam
sempre, e a própria unidade systemd avisa se morrer (`OnFailure=`). Para voltar
ao resumo em todo run, `ops.notify_empty_runs: true` em `config.yaml`. Quando o
filtro zera tudo, o resumo diz quantas ofertas entraram e qual portão descartou
cada uma; quando o LLM cai, diz em quantas chamadas a copy/ranking foram de
fallback.

## O que este projeto NÃO faz

- **Não sinaliza publicidade nem afiliação nos posts** (decisão do dono, fase
  5C). A análise adversarial de 2026-08-26 registra o risco regulatório dessa
  escolha (CDC art. 36, guia CONAR para influenciadores) em
  `docs/superpowers/reviews/2026-08-26-analise-adversarial.md`, A7.
- **Não põe link clicável no story.** O story É publicado automaticamente
  (`instagram_story`, fase 5E), mas a API da Meta não tem sticker de link: a
  arte leva o handle e a chamada, e o link fica na bio e no Telegram.
- **Não clica no próprio link de afiliado.** A validação do link é offline
  (`https` + host permitido); um GET no link curto seria um clique artificial
  do IP do runner, segundos depois de gerado.

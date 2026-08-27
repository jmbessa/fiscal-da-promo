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
| `MELI_CLIENT_ID` / `MELI_CLIENT_SECRET` / `MELI_REFRESH_TOKEN` | Fonte Mercado Livre (fase 3, desligada por padrão) — seguir `docs/runbooks/meli-setup.md` |

São **12 variáveis**: `SHOPEE_APP_ID`, `SHOPEE_APP_SECRET`, `TELEGRAM_BOT_TOKEN`,
`TELEGRAM_CHANNEL_ID`, `TELEGRAM_OPS_CHAT_ID`, `ART_HOST_BOT_TOKEN`,
`CLAUDE_CODE_OAUTH_TOKEN`, `IG_USER_ID`, `IG_ACCESS_TOKEN`, `MELI_CLIENT_ID`,
`MELI_CLIENT_SECRET`, `MELI_REFRESH_TOKEN`. As do Instagram e do Mercado Livre
podem ficar vazias: o canal/fonte correspondente é ignorado com aviso no chat
de operações. `ART_HOST_BOT_TOKEN` vazia também funciona — com um aviso diário,
porque aí o token do bot do canal é o que viaja até a Meta.

No GitHub: Settings → Secrets and variables → Actions → criar os secrets acima
(todos já repassados pelo `publish.yml`). Na VPS, o `.env` (template completo
em `deploy/install-vps.sh`).

## Instagram (fase 2A)

As artes seguem o design system "Fiscal da Promo" (fase 2C): fontes Bricolage
Grotesque (variável) e IBM Plex Mono, paleta navy/dourado e o mascote
desenhado por código em `src/afiliado/brand.py` (ver `docs/brand-guidelines.md`);
nome e handle exibidos nas artes vêm de `brand:` em `config.yaml`.

Dois canais, dois níveis de automação:

- **`story_dispatch` (MANUAL, ligado por padrão, teto de 6/dia)** — o pipeline
  **não posta stories**. Ele gera a arte (1080×1920) e a manda ao chat de
  operações do Telegram, seguida de uma segunda mensagem só com o link de
  afiliado; quem posta é você: abrir o Telegram, salvar a arte, postar como
  story no Instagram e colar o link no sticker. Não depende de nenhuma
  credencial da Meta. O teto (`max_per_day: 6`) limita as artes ao que dá pra
  postar à mão num dia, e no resumo do run essas ofertas aparecem numa seção
  própria — **"📤 Despachados p/ ops — postar no app"** —, fora da contagem de
  publicados (e fora do heartbeat da manhã: enquanto você não posta, ninguém
  publicou nada).
- **`instagram_feed` (100% automático via Graph API, teto de 2/dia)** —
  publica direto no feed (1080×1350) sem intervenção humana. Requer
  `IG_USER_ID` / `IG_ACCESS_TOKEN` (obtidos via `docs/runbooks/meta-setup.md`;
  o projeto roda a **Variante B**, `instagram.api: facebook_login`) e
  `channels.instagram_feed.enabled: true`. O caption do feed nunca leva o
  link de afiliado — só "🔗 Link na bio e no canal do Telegram" — porque a API
  não permite CTA clicável fora da bio. A arte é hospedada pelo bot de
  `ART_HOST_BOT_TOKEN` (ver Credenciais).

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

- **GitHub Actions (produção)** — `.github/workflows/publish.yml` roda **de
  hora em hora entre 08:00 e 23:00 BRT** (16 jobs/dia, `--posts-per-run 5`) e
  commita `data/state.db` de volta com `git pull --rebase` antes do push. Em
  conflito no binário o run atual vence e o log registra um `::warning::` —
  nunca se perde um run. O GitHub cobra **cada job arredondado para o minuto
  seguinte**: 16 jobs × 31 dias × 3 min = 1.488 dos 2.000 min/mês do plano
  grátis para repositório privado, com folga até 4 min/job. A duração real
  ainda não foi medida — o job a imprime no *Summary*; a conta inteira está em
  `docs/runbooks/vps-setup.md`. Disparo manual: aba Actions → publish → Run
  workflow (com opção dry-run).
- **VPS a cada 5 min (opcional)** — o timer systemd chama `afiliado run` a
  cada 5 minutos das 08:00 às 23:55 (192 execuções/dia, 1 oferta por run),
  para quem quiser cadência mais fina e um estoque de candidatas mais fresco;
  setup em `docs/runbooks/vps-setup.md`. `state.db` fica local e persiste
  sozinho (sem commit). **Não rode as duas ao mesmo tempo** — dois estados
  independentes furam o dedupe e o teto.

O teto e o ritmo mandam nos dois casos: `--posts-per-run` diz só quanto UM run
pode chegar a publicar; quem distribui os 60/dia pela janela é o
`pacing_budget` da fase 5A.

Cada canal tem um teto diário (`max_per_day` em `config.yaml`, contado no
SQLite **no dia local** de `schedule.timezone`): `telegram` em 60/dia (a meta
do canal), `story_dispatch` em 6/dia (artes de story que chegam ao seu chat —
**manual**: você posta o story à mão) e `instagram_feed` em 2/dia.
Desde a fase 5A o teto é **distribuído pela janela** (`schedule.window_start`
– `window_end`): um canal só publica enquanto o que já postou hoje está
abaixo de `min(max_per_day, floor(max_per_day × fração da janela decorrida) + 1)`
— 60/dia vira ~1 a cada 16 min, 6 stories viram ~1 a cada 2h40, o 2º feed só
sai da metade da janela em diante, e fora da janela nada é publicado. Quando
nenhum canal pode publicar, o run termina antes de chamar o LLM (nenhuma
oferta paga preço, link ou copy sem ter onde ser publicada); um canal que
bate o teto de verdade aparece como aviso no resumo, não como falha.

Com 16 runs/dia no Actions (ou 192 na VPS), mandar um resumo a cada execução
inundaria o chat de operações. O resumo só é enviado quando o run publicou,
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
- **Não posta stories.** `story_dispatch` entrega a arte e o link ao chat de
  operações; o gesto de postar é seu (ver Instagram, acima).
- **Não clica no próprio link de afiliado.** A validação do link é offline
  (`https` + host permitido); um GET no link curto seria um clique artificial
  do IP do runner, segundos depois de gerado.

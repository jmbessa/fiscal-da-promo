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
| `IG_USER_ID` / `IG_ACCESS_TOKEN` | Feed automático do Instagram (fase 2A) — seguir `docs/runbooks/meta-setup.md` |
| `MELI_CLIENT_ID` / `MELI_CLIENT_SECRET` / `MELI_REFRESH_TOKEN` | Fonte Mercado Livre (fase 3, desligada por padrão) — seguir `docs/runbooks/meli-setup.md` |

São **11 variáveis**: `SHOPEE_APP_ID`, `SHOPEE_APP_SECRET`, `TELEGRAM_BOT_TOKEN`,
`TELEGRAM_CHANNEL_ID`, `TELEGRAM_OPS_CHAT_ID`, `CLAUDE_CODE_OAUTH_TOKEN`,
`IG_USER_ID`, `IG_ACCESS_TOKEN`, `MELI_CLIENT_ID`, `MELI_CLIENT_SECRET`,
`MELI_REFRESH_TOKEN`. As do Instagram e do Mercado Livre podem ficar vazias:
o canal/fonte correspondente é ignorado com aviso no chat de operações.

No GitHub: Settings → Secrets and variables → Actions → criar os secrets acima
(todos já repassados pelo `publish.yml`). Na VPS, o `.env` (template completo
em `deploy/install-vps.sh`).

## Instagram (fase 2A)

As artes seguem o design system "Fiscal da Promo" (fase 2C): fontes Bricolage
Grotesque (variável) e IBM Plex Mono, paleta navy/dourado e o mascote
desenhado por código em `src/afiliado/brand.py` (ver `docs/brand-guidelines.md`);
nome e handle exibidos nas artes vêm de `brand:` em `config.yaml`.

Dois canais, dois níveis de automação:

- **`story_dispatch` (semi-automático, ligado por padrão, teto de 6/dia)** —
  cada run gera a arte de story (1080×1920) e manda ao chat de operações do
  Telegram, seguida de uma segunda mensagem só com o link de afiliado. O gesto
  manual: abrir o Telegram, salvar a arte, postar como story no Instagram e
  colar o link recebido no sticker de link. Não depende de nenhuma credencial
  da Meta. O teto diário (`max_per_day: 6`) evita acumular mais artes do que
  dá pra postar manualmente num dia.
- **`instagram_feed` (100% automático via Graph API, teto de 2/dia)** —
  publica direto no feed (1080×1350) sem intervenção humana. Requer
  `IG_USER_ID` / `IG_ACCESS_TOKEN` (obtidos via `docs/runbooks/meta-setup.md`;
  variante da API em `instagram.api`) e `channels.instagram_feed.enabled: true`. O caption do feed nunca leva o
  link de afiliado — só "🔗 Link na bio e no canal do Telegram" — porque a API
  não permite CTA clicável fora da bio.

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
  preço ≤ mínima conhecida, com a janela real; sem tolerância.
- Mercado Livre: o pool (`/meli-pool-refresh`) traz mediana/p25/janela/mínima
  do anúncio que vence o buy box, e o preço publicado é o desse anúncio
  (`refresh_price`), nunca o vendedor mais barato.

## Agendamento

Duas modalidades, desde a fase 1.8:

- **VPS a cada 5 min (produção)** — o timer systemd chama `afiliado run` a
  cada 5 minutos das 08:00 às 23:55 (192 execuções/dia, 1 oferta por run). É
  a cadência real do canal; setup completo em `docs/runbooks/vps-setup.md`.
  `state.db` fica local e persiste sozinho (sem commit).
- **`.github/workflows/publish.yml` (backup)** — roda de hora em hora, das
  08h às 23h BRT, e commita `data/state.db` de volta. Não acompanha a cadência
  de 5 min (o cron do Actions tem piso de 5 min mas atrasa 5–30 min, e custo de
  minutos muito acima da cota gratuita rodando 192x/dia) — serve como
  redundância caso a VPS caia, e para disparo manual: aba Actions → publish →
  Run workflow (com opção dry-run).

Com a cadência de 5 minutos e dedupe de 30 dias, o estoque de boas ofertas
esgota rápido — sem um piso de qualidade o pipeline passaria a postar sobras.
`selection.min_ev_brl` corta candidatas com valor esperado (comissão em R$ ×
popularidade, sem boost de watchlist) abaixo do piso; 0 ou ausente desliga.

Cada canal tem um teto diário (`max_per_day` em `config.yaml`, contado no
SQLite **no dia local** de `schedule.timezone`): `telegram` em 60/dia (a meta
do canal), `story_dispatch` em 6/dia (artes de story que chegam ao seu chat —
**semi-automático**: você posta o story à mão) e `instagram_feed` em 2/dia.
Desde a fase 5A o teto é **distribuído pela janela** (`schedule.window_start`
– `window_end`): um canal só publica enquanto o que já postou hoje está
abaixo de `min(max_per_day, floor(max_per_day × fração da janela decorrida) + 1)`
— 60/dia vira ~1 a cada 16 min, 6 stories viram ~1 a cada 2h40, o 2º feed só
sai da metade da janela em diante, e fora da janela nada é publicado. Quando
nenhum canal pode publicar, o run termina antes de chamar o LLM (nenhuma
oferta paga preço, link ou copy sem ter onde ser publicada); um canal que
bate o teto de verdade aparece como aviso no resumo, não como falha.

Com 192 runs/dia, mandar um resumo a cada execução inundaria o chat de
operações. O resumo só é enviado quando o run publicou, descartou algo ou
gerou aviso — e cada aviso entra **uma vez por dia** (tabela `warned`), então
uma watchlist vencida não vira 192 mensagens. O primeiro run do dia manda um
heartbeat ("☀️ Bom dia — ontem: N publicados, M descartados em K runs"),
sempre; um run vazio sem aviso novo não notifica. Runs abortados (todas as
fontes falharam) ou interrompidos por sinal notificam sempre, e a própria
unidade systemd avisa se morrer (`OnFailure=`). Para voltar ao resumo em todo
run, `ops.notify_empty_runs: true` em `config.yaml`. Quando o filtro zera
tudo, o resumo diz quantas ofertas entraram e qual portão descartou cada uma;
quando o LLM cai, diz em quantas chamadas a copy/ranking foram de fallback.

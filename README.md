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

No GitHub: Settings → Secrets and variables → Actions → criar os secrets acima
(todos já repassados pelo `publish.yml`).

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

**Pendente do spike da parte 2:** não existe API oficial de link de afiliado
no Mercado Livre. Por ora, `resolve_affiliate_link` usa um **pool de links
pré-gerados** em `data/meli_links.json`, abastecido manualmente pelo painel
de afiliados — item sem link no pool é descartado (comportamento já
existente do pipeline, promove a próxima oferta da fila).

## Comandos

- `afiliado doctor` — verifica Shopee, Telegram e Claude CLI com credenciais reais.
- `afiliado run --dry-run` — pipeline completo (APIs reais) imprimindo os posts sem publicar.
- `afiliado run` — executa e publica de verdade.

## Watchlist semanal

`data/watchlist.json` alimenta o boost de ranking e o selo "menor preço
verificado". Atualize **1x por semana** abrindo o Claude Code no projeto e
digitando `/watchlist-refresh` (requer o conector JoomPulse na sessão) — o
skill em `.claude/skills/watchlist-refresh/` faz consultas, arquivo e commit.
Validade: 14 dias; vencida, o pipeline roda sem boosts e avisa no chat de
operações.

## Agendamento

Duas modalidades, desde a fase 1.8:

- **VPS a cada 5 min (produção)** — cron da VPS chama `afiliado run` a cada 5
  minutos (288 execuções/dia, 1 oferta por run). É a cadência real do canal;
  setup completo em `docs/runbooks/vps-setup.md`. `state.db` fica local e
  persiste sozinho (sem commit).
- **`.github/workflows/publish.yml` (backup)** — roda de hora em hora, das
  08h às 23h BRT, e commita `data/state.db` de volta. Não acompanha a cadência
  de 5 min (o cron do Actions tem piso de 5 min mas atrasa 5–30 min, e custo de
  minutos muito acima da cota gratuita rodando 288x/dia) — serve como
  redundância caso a VPS caia, e para disparo manual: aba Actions → publish →
  Run workflow (com opção dry-run).

Com a cadência de 5 minutos e dedupe de 30 dias, o estoque de boas ofertas
esgota rápido — sem um piso de qualidade o pipeline passaria a postar sobras.
`selection.min_ev_brl` corta candidatas com valor esperado (comissão em R$ ×
popularidade, sem boost de watchlist) abaixo do piso; 0 ou ausente desliga.

Canais com esforço manual ou limites de audiência/API têm um teto diário
opcional (`max_per_day` em `config.yaml`, contado no SQLite): `telegram` em
120/dia (teto de segurança do motor de volume na cadência de 5 min),
`story_dispatch` em 6/dia (artes de story que chegam ao seu chat) e
`instagram_feed` em 2/dia. Um canal que bate o teto no meio do run é pulado em
silêncio (aparece como aviso no resumo, não como falha); ajuste os valores na
seção `channels:` do `config.yaml`.

Com 288 runs/dia, mandar um resumo a cada execução inundaria o chat de
operações. Desde a fase 1.8, o resumo só é enviado quando o run publicou,
descartou algo ou gerou aviso — run completamente vazio não notifica. O
caminho de exceção (run abortado) continua notificando sempre. Para voltar ao
comportamento antigo (resumo em todo run), `ops.notify_empty_runs: true` em
`config.yaml`.

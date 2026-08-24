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

No GitHub: Settings → Secrets and variables → Actions → criar os 6 secrets acima.

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

`.github/workflows/publish.yml` roda 3x/dia (09:00, 12:30, 19:30 BRT) e commita
`data/state.db` de volta. Disparo manual: aba Actions → publish → Run workflow
(com opção dry-run).

## VPS (futuro)

O sistema não depende do Actions: numa VPS basta clonar, exportar as mesmas
variáveis num `.env`/profile e agendar `afiliado run` no cron. O `state.db`
local persiste sozinho (sem commit).

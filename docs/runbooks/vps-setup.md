# Runbook — Setup VPS (produção, cadência de 5 min)

Desde a fase 1.8, a produção roda numa VPS via cron a cada 5 minutos (288
execuções/dia). O `.github/workflows/publish.yml` (hora em hora, 08h–23h BRT)
vira backup/disparo manual — ver seção "Agendamento" do `README.md`.

O sistema não depende do Actions: qualquer máquina com Python 3.12+, Node
(para o Claude Code CLI) e as variáveis de ambiente já roda `afiliado run`.

## 1. Provisionar e clonar

- [ ] VPS com Python 3.12+ e Node 20+ instalados.
- [ ] `git clone` do repositório no diretório de trabalho da VPS (ex.:
      `/opt/afiliado`).
- [ ] `pip install -e .` (ambiente virtual recomendado: `python -m venv .venv`
      antes do install).
- [ ] `npm install -g @anthropic-ai/claude-code`.

## 2. Credenciais (`.env`)

Diferente do Actions (secrets), a VPS lê um `.env` local (gitignored) —
`afiliado run` chama `load_dotenv()` automaticamente antes de tudo. Crie
`/opt/afiliado/.env` com as mesmas variáveis do workflow (ver README →
"Credenciais"):

```
SHOPEE_APP_ID=...
SHOPEE_APP_SECRET=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHANNEL_ID=...
TELEGRAM_OPS_CHAT_ID=...
CLAUDE_CODE_OAUTH_TOKEN=...
IG_USER_ID=...
IG_ACCESS_TOKEN=...
```

- [ ] `CLAUDE_CODE_OAUTH_TOKEN` gerado com `claude setup-token` (mesma cota
      Max usada no Actions).
- [ ] Permissão restrita no arquivo: `chmod 600 .env`.

## 3. Agendar no cron

```cron
*/5 * * * * cd /opt/afiliado && .venv/bin/afiliado run >> /var/log/afiliado/run.log 2>&1
```

- [ ] Criar `/var/log/afiliado/` (ou trocar o caminho do log) com permissão de
      escrita para o usuário do cron.
- [ ] `crontab -e` (usuário dedicado, não root, se possível) e colar a linha
      acima com o caminho real do clone/venv.
- [ ] `state.db` (`config.yaml` → `state.path`, padrão `data/state.db`) fica
      local e persiste sozinho entre execuções — nenhum commit necessário
      (diferente do Actions, onde o runner é efêmero).

Alternativa com `systemd` (mais robusta a reinícios/observabilidade):
crie um `afiliado.service` (`Type=oneshot`, `WorkingDirectory=/opt/afiliado`,
`ExecStart=.venv/bin/afiliado run`) e um `afiliado.timer` com
`OnCalendar=*:0/5` + `Persistent=true`; habilite com
`systemctl enable --now afiliado.timer`.

## 4. Validar

- [ ] `afiliado doctor` (com o `.env` carregado) — confere Shopee, Telegram e
      Claude CLI.
- [ ] `afiliado run --dry-run` — um ciclo completo sem publicar.
- [ ] Deixar o cron/timer rodar um ciclo real e conferir o chat de operações:
      com a fase 1.8, resumo só chega quando o run publicou, descartou algo ou
      gerou aviso (`ops.notify_empty_runs: true` em `config.yaml` volta ao
      envio sempre).

## Notas

- `selection.posts_per_run: 1` e `selection.min_ev_brl` (piso de valor
  esperado) são o ajuste de `config.yaml` pensado para esta cadência — ver
  comentários na seção `selection:`.
- Tetos diários por canal (`max_per_day`) seguem contados no SQLite
  (`state.db`), então funcionam igual entre VPS e Actions rodando ao mesmo
  tempo — mas rodar os dois cron simultaneamente numa mesma janela de tempo
  não é recomendado (duplicaria checagens sem ganho; o Actions é só backup).

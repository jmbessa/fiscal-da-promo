# Runbook — Agendamento de produção (meta: 60 ofertas/dia)

O `config.yaml` define **quanto** postar (`channels.telegram.max_per_day: 60`,
`selection.posts_per_run`, piso `selection.min_ev_brl`) e **em que janela**
(`schedule:` — fuso, início e fim do dia de operação; o teto de cada canal é
distribuído ao longo dela); o agendador externo define **de quanto em quanto
tempo checar**. Duas opções, ambas sem custo:

| | GitHub Actions | VPS gratuita |
|---|---|---|
| Custo | R$ 0 dentro da cota (2.000 min/mês em repo privado) | R$ 0 (Oracle Always Free) |
| Cadência | a cada 45–90 min, em lotes (`posts_per_run: 4–8`) | a cada 5 min, 1 por vez |
| Pontualidade | atrasos de 5–30 min são normais | exata |
| Ritmo no canal | rajadas | espaçado, parece humano |
| Setup | nenhum (já pronto) | ~20 min, pede cartão só para verificação |

Regra de ouro: **os dois nunca rodam ao mesmo tempo** — cada um tem seu
`state.db`, e juntos publicariam a mesma oferta duas vezes. Ao ligar a VPS,
desative o workflow `publish` (GitHub → Actions → publish → `...` → *Disable
workflow*).

---

## Opção A — GitHub Actions (sem infraestrutura nova)

O workflow `.github/workflows/publish.yml` existe e funciona, mas hoje está
configurado para a Opção B (`posts_per_run: 1`, cron de hora em hora). Para usar
o Actions como produção, **é preciso editar dois arquivos**:

- `.github/workflows/publish.yml`: trocar os crons por um a cada ~45 min na
  janela 08h–23h BRT (ex.: `0,45 11-23 * * *` + `0,45 0-2 * * *`);
- `config.yaml`: `selection.posts_per_run: 4`.

Resultado: ≈21 execuções/dia × 4 ≈ 84 ofertas/dia, ~50 min de runner/dia.
- Cota: 2.000 min/mês. Cada execução leva ~2–3 min; acima de ~25 execuções/dia
  o mês estoura — se precisar de mais volume ou de ritmo espaçado, use a
  Opção B.

## Opção B — VPS gratuita (cadência de 5 min)

### B1. Provisionar de graça

- **Oracle Cloud Always Free** (recomendado): 2 VMs AMD (1 GB) **ou** até 4
  núcleos ARM Ampere com 24 GB — gratuitos por tempo indeterminado. Pede cartão
  apenas para verificação de identidade; não cobra enquanto a conta ficar no
  nível *Always Free*. Escolha Ubuntu 22.04.
- **Google Cloud**: instância `e2-micro` sempre gratuita nas regiões
  us-west1/us-central1/us-east1 (latência maior para o Brasil, sem impacto aqui).
- Pagas, se preferir simplicidade: Hetzner CX22 (~€3,79/mês), DigitalOcean
  (~US$ 6/mês).

O pipeline é leve: 1 vCPU e 1 GB bastam.

### B2. Instalar

```bash
curl -fsSL https://raw.githubusercontent.com/<usuario>/<repo>/main/deploy/install-vps.sh -o install-vps.sh
bash install-vps.sh https://github.com/<usuario>/<repo>.git
```

Repositório privado: use um token de acesso pessoal na URL do clone
(`https://<TOKEN>@github.com/<usuario>/<repo>.git`) ou copie o projeto via `scp`.

O script instala Python, Node e o Claude Code, cria o usuário de serviço
`afiliado`, monta o venv em `/opt/afiliado/.venv`, ajusta o fuso para
America/Sao_Paulo (se falhar, avisa em destaque — corrija antes de ligar o
timer: a janela e o ritmo usam a hora local) e instala as unidades systemd de
`deploy/` (`afiliado.service`, `afiliado.timer` e `afiliado-notify.service`,
que avisa o chat de operações se a unidade morrer). É re-executável: rodar
de novo atualiza o código (`git pull` como o usuário `afiliado`) e reinstala
as unidades.

### B3. Segredos

A VPS lê um `.env` local (o CLI chama `load_dotenv()` antes de tudo) — as
mesmas **11 variáveis** dos GitHub Secrets (o script já cria o template):

```bash
sudo nano /opt/afiliado/.env
sudo chmod 600 /opt/afiliado/.env
```

```
SHOPEE_APP_ID=            CLAUDE_CODE_OAUTH_TOKEN=     MELI_CLIENT_ID=
SHOPEE_APP_SECRET=        IG_USER_ID=                  MELI_CLIENT_SECRET=
TELEGRAM_BOT_TOKEN=       IG_ACCESS_TOKEN=             MELI_REFRESH_TOKEN=
TELEGRAM_CHANNEL_ID=
TELEGRAM_OPS_CHAT_ID=
```

`IG_*` e `MELI_*` podem ficar vazias: o canal/fonte é ignorado e o aviso
("canal instagram_feed ignorado: … ausente") chega ao chat de operações, uma
vez por dia.

### B4. Validar antes de ligar

```bash
sudo -u afiliado -H /opt/afiliado/.venv/bin/afiliado doctor
sudo -u afiliado -H /opt/afiliado/.venv/bin/afiliado run --dry-run
```

### B5. Ligar

```bash
sudo systemctl enable --now afiliado.timer
systemctl list-timers afiliado.timer      # próximo disparo
journalctl -u afiliado.service -f         # acompanhar
```

`deploy/afiliado.timer` dispara **a cada 5 min das 08:00 às 23:55** (192
runs/dia, `OnCalendar=*-*-* 08..23:00/5:00`, `Persistent=false`: um disparo
perdido não é recuperado de madrugada). Para mudar janela ou intervalo, edite
`/etc/systemd/system/afiliado.timer` **e** `schedule.window_start/window_end`
no `config.yaml` (o ritmo diário usa a mesma janela; fora dela o orçamento é
0), depois `systemctl daemon-reload && systemctl restart afiliado.timer`.

Alternativa sem systemd (crontab do usuário):
```cron
*/5 8-23 * * * cd /opt/afiliado && .venv/bin/afiliado run >> /var/log/afiliado/run.log 2>&1
```

---

## Operação

| Situação | Comando |
|---|---|
| Atualizar o código | `cd /opt/afiliado && sudo -u afiliado -H git pull && sudo /opt/afiliado/.venv/bin/pip install -e .` |
| Ver os últimos runs | `journalctl -u afiliado.service --since "1 hour ago"` |
| Pausar tudo | `sudo systemctl disable --now afiliado.timer` |
| Rodar um ciclo agora | `sudo systemctl start afiliado.service` |
| Testar o aviso de falha | `sudo systemctl start afiliado-notify.service` (chega "❌ unidade afiliado falhou" no chat de operações) |
| Backup do estado | `sudo cp /opt/afiliado/data/state.db ~/state-$(date +%F).db` |

## Notas

- **Checar ≠ postar.** A cada 5 min o pipeline checa; publica só se houver
  oferta acima do piso (`selection.min_ev_brl`), inédita no dedupe de 30 dias e
  dentro do orçamento do momento de algum canal (o teto diário é distribuído
  pela janela: 60/dia é ~1 a cada 16 min). Quando nenhum canal pode publicar,
  o run termina antes de chamar o LLM. Runs sem novidade não geram mensagem
  no chat de operações (`ops.notify_empty_runs: false`) — com três exceções
  que **sempre** chegam: o heartbeat do primeiro run do dia ("☀️ Bom dia —
  ontem: N publicados …"), avisos novos (cada texto uma vez por dia — se o
  Bom dia não chegar, a VPS morreu) e falhas (run abortado, interrompido por
  sinal, ou a unidade morta: `OnFailure=` → "❌ unidade afiliado falhou").
- **Silêncio não é normal.** Sem o "Bom dia" às ~08:00, algo caiu: a Oracle
  Always Free recolhe VMs ociosas. `systemctl status afiliado.timer` e
  `journalctl -u afiliado.service --since today`.
- O `state.db` da VPS passa a ser a fonte de verdade do dedupe; ao migrar de
  volta para o Actions, copie o arquivo para o repositório antes.
- `IG_ACCESS_TOKEN` é token de Página e não expira; `CLAUDE_CODE_OAUTH_TOKEN`
  vale ~1 ano (renovar em ago/2027).
- Consumo de LLM: 1 ranking por run com candidatas + ~1 copy por oferta
  publicada (2 se a primeira falhar). Com 60/dia são ~250 chamadas curtas por
  dia; se o LLM cair, o resumo diz "LLM indisponível em X de Y chamadas" e a
  copy/ranking seguem no fallback determinístico. O `claude -p` roda sem
  ferramentas, sem settings do repositório e com ambiente em lista branca
  (só `CLAUDE_CODE_OAUTH_TOKEN` e o básico do sistema).

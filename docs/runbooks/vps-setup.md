# Runbook — Agendamento de produção (meta: 60 ofertas/dia)

O `config.yaml` define **quanto** postar (`channels.telegram.max_per_day: 60`,
`selection.posts_per_run`, piso `selection.min_ev_brl`) e **em que janela**
(`schedule:` — fuso, início e fim do dia de operação; o teto de cada canal é
distribuído ao longo dela); o agendador externo define **de quanto em quanto
tempo checar**. Duas opções, ambas sem custo:

> **Desde a fase 5C a produção é a Opção A (GitHub Actions).** Decisão do
> dono: não depender de uma VPS que a Oracle Always Free recolhe quando fica
> ociosa (e esta fica >95% do tempo). A Opção B continua documentada e
> suportada — é para quem quiser a cadência de 5 min.

| | GitHub Actions (**produção**) | VPS gratuita (opcional) |
|---|---|---|
| Custo | R$ 0 dentro da cota (2.000 min/mês em repo privado) | R$ 0 (Oracle Always Free) |
| Cadência | a cada 30 min, `--posts-per-run 4` | a cada 5 min, 1 por vez |
| Pontualidade | atrasos de 5–30 min são normais | exata |
| Ritmo no canal | o `pacing_budget` da fase 5A espaça os 60/dia nos dois casos | idem |
| Estoque de candidatas | 32 fatias de descoberta/dia | 192 fatias/dia (mais fresco) |
| Setup | nenhum (já pronto) | ~20 min, pede cartão só para verificação |
| Sobrevive sozinho | sim | a VM ociosa pode ser recolhida |

Regra de ouro: **os dois nunca rodam ao mesmo tempo** — cada um tem seu
`state.db`, e juntos publicariam a mesma oferta duas vezes. Ao ligar a VPS,
desative o workflow `publish` (GitHub → Actions → publish → `...` → *Disable
workflow*).

---

## Opção A — GitHub Actions (produção, já configurada)

Não é preciso editar nada: `.github/workflows/publish.yml` já roda a cada
**30 min entre 08:00 e 23:30 BRT** (32 runs/dia) com `--posts-per-run 4`.

- **Cota:** 32 runs × ~1,5 min (com cache de pip e do npm global) ≈ 48 min/dia
  ≈ **1.440 min/mês**, dentro dos 2.000 do plano grátis para repositório
  privado. Sem os caches o setup sozinho passava de 2,5 min/run (≈2.400
  min/mês) e estouraria. Confira o consumo em GitHub → Settings → Billing; se
  apertar, corte o cron das 12:00–18:00 BRT para 1×/hora antes de mexer em
  qualquer outra coisa.
- **Estado:** o passo "Commitar estado" faz `git pull --rebase` antes do push;
  em conflito no binário do `state.db` o run ATUAL vence e o log registra um
  `::warning::` — o pior caso é reesquecer os posts de um run, nunca perder o
  run. A mensagem leva `[skip ci]`, então o commit de estado não roda a suíte.
- **Segredos:** os 12 do README. `MELI_REFRESH_TOKEN` é só rede de segurança —
  o `/products/{id}/items` do Mercado Livre aceita o token de aplicação
  (`client_credentials`, verificado ao vivo em 2026-08-26), então o runner
  efêmero nunca rotaciona refresh token e o workflow não precisa de PAT nem de
  `gh secret set` (detalhes e o plano B em `docs/runbooks/meli-setup.md`).
- **Desligar:** GitHub → Actions → publish → `...` → *Disable workflow*. O
  GitHub também desativa workflows agendados após 60 dias sem atividade no
  repositório — o heartbeat diário no chat de operações é o que denuncia isso.
- **Disparo manual:** aba Actions → publish → Run workflow (com opção
  dry-run).

## Opção B — VPS gratuita (cadência de 5 min, opcional)

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

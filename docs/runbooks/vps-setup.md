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
| Cadência | de hora em hora, `--posts-per-run 5` | a cada 5 min, 1 por vez |
| Pontualidade | atrasos de 5–30 min são normais | exata |
| Ritmo no canal | o `pacing_budget` da fase 5A espaça os 60/dia nos dois casos | idem |
| Estoque de candidatas | 16 fatias de descoberta/dia | 192 fatias/dia (mais fresco) |
| Setup | nenhum (já pronto) | ~20 min, pede cartão só para verificação |
| Sobrevive sozinho | sim | a VM ociosa pode ser recolhida |

Regra de ouro: **os dois nunca rodam ao mesmo tempo** — cada um tem seu
`state.db`, e juntos publicariam a mesma oferta duas vezes. Ao ligar a VPS,
desative o workflow `publish` (GitHub → Actions → publish → `...` → *Disable
workflow*).

---

## Opção A — GitHub Actions (produção, já configurada)

Não é preciso editar nada: `.github/workflows/publish.yml` já roda **de hora em
hora entre 08:00 e 23:00 BRT** (16 jobs/dia) com `--posts-per-run 5`.

### A cota de minutos — a conta, refeita

A regra de cobrança que a revisão da fase 5C encontrou: **o GitHub arredonda a
duração de cada JOB para o minuto seguinte** (runner Linux, multiplicador 1×), e
o plano grátis de repositório **privado** dá **2.000 min/mês**. Um job de 1 min
e 1 s custa 2 min. A conta é `jobs/dia × dias do mês × minutos COBRADOS por
job`, com o mês mais longo (31 dias):

| jobs/dia | cadência | 2 min/job | 3 min/job | 4 min/job |
|---:|---|---:|---:|---:|
| 32 | 30 min, 08:00–23:30 | 1.984 (99%) | 2.976 (**149%**) | 3.968 (**198%**) |
| 18 | 45 min no pico, 90 fora | 1.116 (56%) | 1.674 (84%) | 2.232 (**112%**) |
| **16** | **1 h, 08:00–23:00** | **992 (50%)** | **1.488 (74%)** | **1.984 (99%)** |

A duração real do job **nunca foi medida** — o "~1,5 min" da fase 5C era uma
estimativa, e a 1,5 min o GitHub cobra 2. Enquanto não há medição, a cadência
tem de caber no pior caso plausível: **16 jobs/dia é a única linha que
sobrevive até 4 min/job**, e ainda deixa ~500 min/mês para o `tests.yml` (que
roda a cada push e também consome a mesma cota; o commit de estado leva
`[skip ci]` e não conta).

Por que 1 h e não "45 min no pico": o campo de minuto do cron se repete a cada
hora, então `*/45` dispara aos minutos 0 e 45 — intervalos de 45 e **15** min,
não uma cadência de 45. Cadências honestas são divisores de 60.

**Posts por run.** 60/dia ÷ 16 runs = 3,75. Pelo ritmo real (`pacing_budget`
com 60/dia e a janela 08:00–23:15), os orçamentos dos 16 disparos são
1, 4, 8, 12, 16, 20, 24, 28, 32, 36, 40, 44, 48, 52, 56, **60** — o maior salto
entre dois runs consecutivos é **4**, e `--posts-per-run 5` o cobre com uma
vaga de folga para recuperar um disparo perdido. `schedule.window_end` é
**23:15** justamente para o último disparo (23:00) alcançar os 60: com 23:55 o
orçamento das 23:00 seria 59 e a meta era inalcançável por construção. Se
mudar a cadência, refaça as duas contas — há testes que travam as duas
(`tests/test_workflow_yaml.py`).

**Descoberta.** 8 chamadas/run × 16 runs = 128 chamadas/dia (eram 256). A
varredura completa das 5 raízes passa de ~1,25 para **~2,5 dias**; a margem do
dedupe não muda, porque ela vem do TAMANHO do espaço (≈5.460 itens elegíveis
nas raízes, contra 1.800 posts/mês), não do número de varreduras.

- **Meça, depois decida.** O passo final do job ("Duração do job") imprime a
  duração real e a joga no *Summary* do run. Anote aqui a primeira medição:
  `duração dos passos: ___ s → ___ min cobrados → ___ min/mês`. Com o número
  na mão, a tabela acima diz se dá para voltar a 30 min. Confira o consumo em
  GitHub → Settings → Billing.
- **Trava de segurança:** `timeout-minutes: 20` no job. Sem ela vale o padrão
  do GitHub (6 h): um run em martelo contra a API da loja queimaria 360 min —
  18% da cota mensal — antes de alguém perceber.
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

### O `state.db` commitado — o que a medição disse

A fase 5C deixou isto como "risco aberto: alguns GB por mês, o GitHub reclama
em semanas". A revisão **mediu**, com o volume real da descoberta nova, e o
susto era ~10× exagerado. **Veredito: o Actions serve como produção; observe o
tamanho.**

| cenário | `candidates` | `price_log` | total |
|---|---:|---:|---:|
| Actions, 32 runs/dia (cadência medida) | 29,8 MB | 47,7 MB | **77,9 MB** |
| VPS, 192 runs/dia | 51,1 MB | 122,8 MB | **174,2 MB** |

Nesta cadência de **16 jobs/dia** a entrada é metade da medida — espere
**~40 MB** (extrapolação, não medição; a primeira semana de produção diz o
número real).

O que engana é achar que o git guarda 40 MB a cada commit. Não guarda: o
crescimento medido é de **0,375 MB por commit**. A 16 commits/dia dá ~6 MB/dia
≈ **0,18 GB/mês** — o limite de 5 GB do GitHub fica a **mais de dois anos**, e
não a semanas. (O número por commit foi medido com o arquivo maior; com ~40 MB
ele tende a ser menor ainda.)

Então o motivo real para encolher o arquivo **não é o limite do GitHub**: é o
tempo de `checkout` e a RAM do runner ao abrir o SQLite. Enquanto o job couber
no `timeout-minutes: 20`, não há o que fazer. Quando incomodar, as alavancas,
ranqueadas pelo **efeito medido** — nenhuma aplicada, e a primeira tem custo:

1. `selection.ref_window_days` de 90 → 30 dias: **−32 MB**. Custo: mediana e
   p25 passam a olhar 30 dias. Ainda acima dos 14 dias distintos que a regra do
   quartil exige, mas **90 dias é o que sustenta a régua honesta** — é a última
   coisa a mexer, não a primeira.
2. `shopee.candidate_max_age_days` de 3 → 1: **−20 MB**. Custo: candidata não
   publicada em 24 h precisa ser redescoberta; com o ciclo de ~2,5 dias do
   Actions, isso reduz a fila.
3. Reescrever o histórico do `state.db` — branch órfã com um commit só e
   force-push, ou `git gc` agressivo — ou mudar o estado para a Opção B, onde
   ele nem é commitado.

Acompanhe em Settings → Repository. As tabelas de linha curta já são
`WITHOUT ROWID` desde a fase 5C (o `price_log` caiu de 47 para 22 MB nos
volumes de então) — esse ganho já está dentro dos números acima.

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
perdido não é recuperado de madrugada). A janela do `config.yaml` termina
**23:15** (ver a conta na Opção A), então os disparos de 23:20 em diante já
recebem orçamento 0 e terminam sem publicar — inofensivo, mas se quiser o
timer exatamente alinhado, mude o `OnCalendar` para `08..23:15/5:00`. Para
mudar janela ou intervalo, edite
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

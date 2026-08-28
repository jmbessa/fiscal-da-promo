# Runbook — a produção roda na máquina do dono (fase 5I, 2026-08-28)

Desde **2026-08-28** quem publica é a **máquina do dono**, pelo **Agendador de
Tarefas do Windows**. O GitHub Actions continua existindo, mas só como
**fallback manual** (`workflow_dispatch`).

Este runbook cobre: por que mudou, o que foi criado, **a ordem da virada**,
como conferir que rodou, e como voltar para o Actions se a máquina cair.

---

## 1. Por que mudou — os três fatos medidos

1. **O agendador do GitHub não entrega.** Consulta à API do GitHub em
   2026-08-28: o workflow `publish` teve **1 run em toda a história do
   repositório** (`33115325845`, 2026-08-27T20:51Z) contra **~16 disparos
   esperados em ~25 h** — e o único saiu **51 min atrasado**. O `tests.yml`
   rodava normalmente por push no mesmo período: não é billing nem permissão, é
   o agendador. A fase 5G moveu o cron do minuto 0 para o 7 como mitigação;
   ela ficou **documentada, nunca comprovada**.
2. **Story com figurinha não pode rodar no Actions.** O próprio código recusa
   (`AVISO_STORY_LINK_FORA_DO_RUN`): IP de datacenter que muda a cada execução
   é o padrão que mais dispara `challenge_required`. E não há figurinha pela
   API oficial — a Graph API não suporta sticker em story
   (`docs/superpowers/reviews/2026-08-27-sticker-de-link.md`).
3. **A máquina do dono é um host viável, e isso foi medido em 2026-08-28:**
   **48,7 h** de uptime contínuo, plano de energia **"Ultimate Performance"** e
   suspensão em corrente alternada = **0 (nunca suspende)**.

A máquina é o único lugar onde os dois requisitos do dono coexistem: **60
ofertas por dia, divididas ao longo do dia** e **stories com figurinha,
automatizados**.

---

## 2. O que foi criado

`deploy/agendar-windows.ps1` cria **quatro** tarefas. Ele é **idempotente**
(rodar de novo atualiza, não duplica), tem `-Remover` para desfazer, e **falha
antes de criar qualquer coisa** se a pasta do projeto, o `config.yaml` ou o
`afiliado.exe` não existirem.

| tarefa | comando | cadência | janela |
|---|---|---|---|
| `FiscalDaPromo-Run` | `afiliado run --posts-per-run 4` | 15 min | 08:03 → 23:15 |
| `FiscalDaPromo-Stories` | `afiliado stories --posts 4` | 15 min | 08:08 → 23:15 |
| `FiscalDaPromo-Feed` | `afiliado feed --tipo termometro` | 2 h | 08:11 → 23:15 |
| `FiscalDaPromo-Flagrante` | `afiliado feed --tipo flagrante` | 2 h | 08:16 → 23:15 |

Para as quatro: **Iniciar em** = a pasta do projeto (é de onde saem
`config.yaml`, `.env` e `data/`), **não** "iniciar somente se ocioso", **não**
exigir energia da tomada, e `MultipleInstances = IgnoreNew` (um run travado não
empilha dez atrás dele). Nenhuma credencial é gravada: as tarefas rodam como o
usuário interativo — o único modo que o Agendador aceita **sem guardar senha** —
e herdam o `.env` da pasta.

Os minutos de início são **irregulares e diferentes** de propósito: 60 posts no
minuto zero de cada hora parecem robô, e duas tarefas no mesmo instante são dois
processos Python disputando a máquina e as mesmas APIs.

### Por que 15 minutos — a conta

**Medido** com o pipeline real e transporte dublê (nenhuma rede): um `afiliado
run` gasta **8 chamadas de descoberta** — sempre, inclusive nos runs que não
publicam nada, porque `fetch_offers` roda **antes** do teste de canal aberto —
mais **2 por oferta publicada** (`refresh_price` + `generateShortLink`).

| cadência | disparos/dia | chamadas/dia (1 tarefa) | maior salto do ritmo | `--posts-per-run` mínimo |
|---|---:|---:|---:|---:|
| **15 min** | **61** | **608** | **1** | **3** |
| 20 min | 46 | 488 | 2 | 6 |
| 30 min | 31 | 368 | 2 | 6 |

Com as duas tarefas de 15 min são ~**1.216 chamadas/dia** à Shopee. Cabe: o
cliente da VPS (5 min) já fazia ~1.920/dia, e a medição de 2026-08-26 viu 147
chamadas em uma hora **sem um único 429**. Não há cota diária publicada pela
Shopee — o número acima é o que se sabe, não uma garantia.

15 min também é o que o `config.yaml` já dizia (`channels.telegram.max_per_day:
60`, "~1 a cada 15 min") e o que faz o ritmo entregar **60/dia distribuídas**: o
maior salto do `pacing_budget` cai de 4 (cadência de 1 h do Actions) para **1**.
`--posts-per-run 4` cobre este disparo mais **três** perdidos — 45 min de
máquina parada, recuperados sozinhos.

Ao mudar a cadência, mude junto `schedule.max_gap_minutes` no `config.yaml` e
`$PostsPorRun` no script: `tests/test_agendador_windows.py` trava os três.

### Por que as duas tarefas de FEED existem

O passo "Conteúdo do feed" do `publish.yml` era o **único** lugar que chamava
`afiliado feed`. Desligar o `schedule:` sem mais nada mataria o carrossel do
termômetro e o flagrante **em silêncio**. São duas tarefas (e não um comando
encadeado) para que uma falha não derrube a outra e para o `doctor` conseguir
nomear qual peça ficou sem agendador. A cadência de 2 h é só **retentativa**:
quem garante "uma por dia" é o código (`_carrossel_pode_sair` e
`_flagrante_pode_sair`), e um disparo que falha é repetido pelo seguinte — a
peça ainda sai no mesmo dia.

---

## 3. Pré-requisito que o dono faz UMA vez

Depois que o PR #5 for mesclado e o worktree removido, **na pasta principal**:

```powershell
pip install -e .        # a instalação editável hoje aponta para o worktree
afiliado ig-login       # a sessão do instagrapi é gitignored e só existe no worktree
```

Sem os dois, as tarefas rodam **código velho** ou **não logam**. Isto não se
automatiza aqui de propósito: `pip install -e .` decide qual cópia do projeto é
a produção, e `ig-login` mexe na sessão do Instagram — as duas são decisões do
dono.

---

## 4. A ORDEM DA VIRADA — e ela importa

Invertida, fica um intervalo sem ninguém publicando.

1. **Criar as tarefas:**
   ```powershell
   powershell -ExecutionPolicy Bypass -File deploy\agendar-windows.ps1
   ```
   Ele imprime as quatro tarefas, o executável e o diretório de trabalho.
2. **Conferir:** `afiliado doctor`. Ele agora checa se as quatro tarefas
   existem e estão habilitadas.
3. **Ver um run REAL acontecer.** Espere o próximo disparo (≤ 15 min) e confira
   o resumo no chat de operações, ou force com
   `Start-ScheduledTask -TaskName FiscalDaPromo-Run` e olhe a saída.
4. **SÓ ENTÃO** desligar o gatilho automático do Actions — que nesta fase já
   veio desligado no `publish.yml` (o `schedule:` foi removido). Se você estiver
   fazendo a virada em outra ordem, a regra é esta: o Actions só para depois de
   a máquina ter publicado uma vez.

**Nunca deixe os dois publicando ao mesmo tempo.** Os dois hosts têm `state.db`
separados, e é o `state.db` que guarda o dedupe: a mesma oferta sairia duas
vezes.

---

## 5. Como conferir que rodou

- **Chat de operações.** É a fonte primária. O primeiro run do dia manda o
  heartbeat ("☀️ Bom dia — ontem: N publicados…"), e o resumo de cada run traz
  a linha `🏷️ Hoje por fonte: meli X/30 · shopee Y/30`.
- **Buraco na cadência.** Se a máquina parar, o run seguinte acusa:
  `⚠️ Buraco na cadência: N h desde o run anterior — ~K disparo(s) perdido(s)`.
  O limiar é `schedule.max_gap_minutes` (**40** min para a cadência de 15:
  tolera um disparo perdido e acusa a partir do segundo). **Este aviso é hoje o
  sensor de "a máquina parou".**
- **`afiliado doctor`.** Diz se as quatro tarefas existem e estão habilitadas,
  além das credenciais de sempre.
- **Agendador de Tarefas** (`taskschd.msc`) → Biblioteca → as quatro
  `FiscalDaPromo-*`: colunas *Última Execução* e *Resultado da Última
  Execução* (`0x0` = sucesso).
- **Linha de comando:**
  ```powershell
  Get-ScheduledTask -TaskName "FiscalDaPromo-*" | Get-ScheduledTaskInfo |
      Select-Object TaskName, LastRunTime, LastTaskResult, NextRunTime
  ```

### Quando algo não sai

| sintoma | provável causa |
|---|---|
| `doctor` diz "a tarefa X não existe" | o script nunca rodou, ou rodou em outro usuário |
| tarefa existe e `LastTaskResult` ≠ 0 | o comando falhou — rode `afiliado run` à mão na pasta do projeto e leia o erro |
| tarefa não dispara com a máquina ligada | usuário não está conectado (`LogonType Interactive`) |
| nada no chat de ops e nenhum aviso | confira `TELEGRAM_*` no `.env` da **pasta do projeto** |
| stories param, ofertas continuam | canal desarmado hoje — `docs/runbooks/instagrapi-stories.md` |

---

## 6. Como voltar para o Actions se a máquina cair

**Emergência (um dia só):** aba Actions → `publish` → *Run workflow* (com opção
`dry-run` se quiser só ver). Ele publica de verdade e commita o `data/state.db`
do runner. `--posts-per-run 5` está maior que o da máquina de propósito: um
disparo de emergência precisa publicar o orçamento **acumulado**.

**Volta prolongada:**

1. **Desligue as tarefas primeiro** — `deploy\agendar-windows.ps1 -Remover`, ou
   desabilite as quatro no Agendador. Sem isto, posto duplo.
2. Devolva o `schedule:` ao `.github/workflows/publish.yml` (o cabeçalho de lá
   guarda o cron da 5G: `7 11-23 * * *` e `7 0-2 * * *`, 16 disparos/dia) e
   ajuste `schedule.max_gap_minutes` para **150** e `pipeline.CADENCIA_MINUTOS`
   para **60** — os testes de cadência acompanham a mudança.
3. Sabendo o que você está reaceitando: ~15 de 16 disparos descartados na única
   medição, e **nenhum story com figurinha** (o Actions não roda o instagrapi).
4. **Estado divergente.** O `data/state.db` do repositório e o da máquina são
   dois. Ao voltar, o dedupe e os tetos do dia valem pelo banco de quem está
   publicando — espere alguma repetição no dia da troca. O banco do
   `afiliado stories` (`data/state_stories.db`) é local e não é commitado: ele
   simplesmente para.

**Terceira opção:** a VPS (`docs/runbooks/vps-setup.md`), a cada 5 min, com o
mesmo problema do story (IP de datacenter) e a mesma regra de exclusividade.

---

## 7. O que este desenho ainda não resolve

- **A máquina desligada não publica.** Não há failover automático: quem
  percebe é o aviso de cadência no chat de operações, e quem age é o dono.
  `StartWhenAvailable` faz o disparo perdido sair assim que a máquina volta, e
  `--posts-per-run 4` recupera até três.
- **A mistura entre as lojas.** O pool do ML sustenta ~1,2 oferta/dia contra
  uma cota de 30; a Shopee cobre o resto e nada falha. Agora isso aparece no
  resumo (`🏷️ Hoje por fonte`) e vira aviso quando uma fonte fica abaixo de
  metade da cota. A correção é aumentar o pool, e é trabalho de outra fase.
- **A cota da API da Shopee é desconhecida.** ~1.216 chamadas/dia é
  confortável perto do que o projeto já fez, mas não há número publicado.

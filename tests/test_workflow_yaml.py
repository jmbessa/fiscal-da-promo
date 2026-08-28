"""O `publish.yml` FOI a produção entre as fases 5C e 5I. Desde 2026-08-28 ele
é o FALLBACK MANUAL: a produção roda na máquina do dono, pelo Agendador de
Tarefas do Windows (tests/test_agendador_windows.py). O que sobrou aqui — o
disparo à mão, o estado que não se perde, os segredos — continua tendo de bater
com o que o código e os runbooks dizem."""

import re

import yaml

from afiliado import cli, pipeline

WORKFLOW = ".github/workflows/publish.yml"


def _workflow() -> dict:
    with open(WORKFLOW, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _texto() -> str:
    with open(WORKFLOW, encoding="utf-8") as f:
        return f.read()


def _config() -> dict:
    with open("config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _passo(nome: str) -> dict:
    steps = _workflow()["jobs"]["run"]["steps"]
    return next(s for s in steps if s.get("name") == nome)


def _on() -> dict:
    return _workflow().get("on", _workflow().get(True))  # PyYAML: "on:" é True


def _posts_per_run() -> int:
    return int(re.search(r"--posts-per-run (\d+)", _passo("Executar pipeline")["run"]).group(1))


def _param_do_agendador(nome: str) -> str:
    with open(cli.SCRIPT_DO_AGENDADOR, encoding="utf-8-sig") as f:
        return re.search(rf"\${nome}\s*=\s*\"?([^\"\r\n,)]+)\"?", f.read()).group(1).strip()


# -- fase 5I: o agendamento morreu, o disparo à mão ficou ----------------------

def test_o_publish_nao_dispara_mais_sozinho():
    """POSTO DUPLO: os dois hosts têm `state.db` SEPARADOS, e é o `state.db`
    que guarda o dedupe. Com a máquina do dono publicando, um disparo agendado
    daqui publicaria a MESMA oferta uma segunda vez. Por isso o `schedule:` foi
    REMOVIDO, e não deixado "de reserva"."""
    assert "schedule" not in _on()
    assert "workflow_dispatch" in _on()
    assert "- cron:" not in _texto()


def test_o_cabecalho_diz_por_que_a_producao_mudou_de_lugar():
    """Sem os três fatos escritos aqui, daqui a duas semanas alguém religa o
    `schedule:` "porque o cron é mais simples" — e volta o posto duplo."""
    texto = _texto()
    assert "NÃO É MAIS A PRODUÇÃO" in texto
    assert "2026-08-28" in texto and "51 min" in texto      # 1. o agendador
    assert "challenge_required" in texto                    # 2. o story
    assert "48,7 h" in texto and "nunca suspende" in texto   # 3. a máquina
    assert "POSTO DUPLO" in texto
    assert cli.RUNBOOK_DA_PRODUCAO in texto
    assert cli.SCRIPT_DO_AGENDADOR in texto
    # A ordem da virada — criar as tarefas, ver um run real, só então desligar.
    assert "A ORDEM DA VIRADA" in texto


def test_o_fallback_manual_publica_o_orcamento_acumulado():
    """Um disparo de emergência acontece porque a máquina caiu: ele precisa
    publicar o que o ritmo acumulou desde o último run, não a fatia de 15 min
    de um disparo normal."""
    assert _posts_per_run() >= int(_param_do_agendador("PostsPorRun"))


def test_o_job_tem_timeout_curto():
    # C1 da revisão da 5C: sem `timeout-minutes` vale o padrão do GitHub, 6 h.
    # Um run que entra em martelo contra a API da loja (backoff de 0,5+1,5+4,0 s
    # em cada uma de milhares de chamadas) queimaria 360 min — 18% da cota
    # mensal — antes de alguém perceber.
    assert _workflow()["jobs"]["run"]["timeout-minutes"] == 20


def test_o_veredito_do_tamanho_do_state_db_e_o_medido():
    """I-1/I-2: a fase 5C deixou "alguns GB por mês, o GitHub reclama em
    semanas" — estimativa, e 10× exagerada. A revisão mediu 77,9 MB de arquivo
    a 32 runs/dia e **0,375 MB por commit** de crescimento do git. O número
    medido continua escrito nos dois lugares; o que mudou na 5I foi o VEREDITO
    que ele sustenta — o tamanho nunca foi o motivo de tirar a produção do
    Actions, quem a tirou foi o agendador."""
    with open("docs/runbooks/vps-setup.md", encoding="utf-8") as f:
        runbook = f.read()
    workflow = _texto()
    for texto in (runbook, workflow):
        assert "0,375 MB por commit" in texto
        assert "Actions serve como produção" not in texto
    # A quebra de linha do markdown não conta.
    assert "nunca foi motivo para tirar a produção do Actions" in " ".join(runbook.split())
    assert "77,9 MB" in runbook and "174,2 MB" in runbook
    # As duas alavancas ficam DOCUMENTADAS, não aplicadas: 90 dias é o que
    # sustenta a régua honesta.
    assert _config()["selection"]["ref_window_days"] == 90
    assert _config()["shopee"]["candidate_max_age_days"] == 3


def test_a_medicao_do_agendador_fica_escrita():
    """G4: 1 de ~16 disparos em ~25 h, e o único com 51 min de atraso, medido
    em 2026-08-28. É o número que decidiu a mudança de host na 5I — sem estar
    escrito, vira chute de novo daqui a duas semanas e alguém religa o cron."""
    with open("docs/runbooks/vps-setup.md", encoding="utf-8") as f:
        runbook = f.read()
    for texto in (runbook, _texto()):
        assert "2026-08-28" in texto and "51 min" in texto


def test_o_docs_do_feed_diz_quando_a_peca_sai_de_verdade():
    """A seção "Cadência entregue" prometia 08:00 pelo Actions — hora que, com
    ~15 de 16 disparos descartados, muitas vezes não acontecia."""
    with open("docs/feed.md", encoding="utf-8") as f:
        secao = f.read().split("### Cadência entregue")[1].split("###")[0]
    secao = " ".join(secao.split())      # a quebra de linha do markdown não conta
    assert "08:00" not in secao
    assert "primeiro disparo do dia em que a cota ainda não foi gasta" in secao


def test_publish_nao_roda_dois_ao_mesmo_tempo():
    concurrency = _workflow()["concurrency"]
    assert concurrency["group"] == "publish"
    assert concurrency["cancel-in-progress"] is False


def test_o_job_mede_a_propria_duracao():
    """I-3: 1,5 min/run nunca foi medido. O job passa a imprimir a própria
    duração (e a jogar no resumo do run) para que a primeira medição real
    entre no runbook em vez de mais uma estimativa."""
    passo = _passo("Duração do job")
    assert passo["if"] == "always()"
    assert "GITHUB_STEP_SUMMARY" in passo["run"]
    assert "JOB_START" in passo["run"]
    inicio = next(s for s in _workflow()["jobs"]["run"]["steps"]
                  if "JOB_START" in str(s.get("run", "")) and s is not passo)
    assert "GITHUB_ENV" in inicio["run"]


# -- fase 5D (F1): alguém chama o `afiliado feed` ------------------------------

PASSO_FEED = "Conteúdo do feed"


def test_o_feed_roda_em_todo_disparo_e_o_freio_mora_no_codigo():
    """A fase 5D entregou `afiliado feed` e NADA o executava; a 5D o prendeu ao
    cron das 08:00 (`if: github.event.schedule == '0 11 * * *'`) para não
    gastar minutos nos outros 15 disparos.

    A 5G mediu que ~15 dos 16 disparos são DESCARTADOS pelo agendador: prender
    a peça a um slug de cron é feed que nunca sai — e que não sai em silêncio,
    porque o passo é `continue-on-error`. O passo perde o `if:` (roda em todo
    disparo, `workflow_dispatch` incluído) e quem garante o "uma vez por dia" é
    o código, onde os testes alcançam: `_carrossel_pode_sair` e
    `_flagrante_pode_sair`, ambos ANTES de qualquer descoberta."""
    passo = _passo(PASSO_FEED)
    assert "if" not in passo
    assert "github.event.schedule" not in yaml.safe_dump(passo)
    assert "afiliado feed" in passo["run"]
    # Os dois freios que o comentário do passo promete existem de verdade...
    assert callable(cli._carrossel_pode_sair) and callable(cli._flagrante_pode_sair)
    # ...e o comentário diz o CUSTO da troca (dois comandos por disparo, que
    # saem antes de qualquer rede quando a cota do dia já foi gasta) no lugar
    # da justificativa do `if:` que morreu.
    with open(WORKFLOW, encoding="utf-8") as f:
        assert "startup do Python" in f.read()


def test_o_feed_nao_pode_derrubar_o_commit_de_estado():
    """Um carrossel que falha (foto que não baixa, Graph API fora) não pode
    impedir o commit do `state.db` que o `run` acabou de produzir: sem ele o
    dedupe e o teto do próximo run saem furados. Daí `continue-on-error`, e a
    posição — depois do run, antes do commit."""
    assert _passo(PASSO_FEED)["continue-on-error"] is True
    nomes = [s.get("name") for s in _workflow()["jobs"]["run"]["steps"]]
    assert (nomes.index("Executar pipeline") < nomes.index(PASSO_FEED)
            < nomes.index("Commitar estado"))
    # E uma peça não pode derrubar a outra dentro do próprio passo: o shell do
    # Actions é `bash -e` e o primeiro comando com saída != 0 encerraria o resto.
    run = _passo(PASSO_FEED)["run"]
    assert run.count("afiliado feed") == run.count("||") == 2


def test_o_feed_recebe_os_mesmos_segredos_do_run():
    """O carrossel publica pela Graph API (IG_*), hospeda a arte pelo bot
    secundário (ART_HOST_BOT_TOKEN), ranqueia com o LLM (CLAUDE_CODE_*) e
    despacha o flagrante ao chat de ops (TELEGRAM_*) — e busca nas duas lojas."""
    assert _passo(PASSO_FEED)["env"] == _passo("Executar pipeline")["env"]


def test_publish_repassa_todos_os_segredos():
    env = _passo("Executar pipeline")["env"]
    for var in ("SHOPEE_APP_ID", "SHOPEE_APP_SECRET", "TELEGRAM_BOT_TOKEN",
                "TELEGRAM_CHANNEL_ID", "TELEGRAM_OPS_CHAT_ID", "ART_HOST_BOT_TOKEN",
                "CLAUDE_CODE_OAUTH_TOKEN", "IG_USER_ID", "IG_ACCESS_TOKEN",
                "MELI_CLIENT_ID", "MELI_CLIENT_SECRET", "MELI_REFRESH_TOKEN"):
        assert env[var] == "${{ secrets.%s }}" % var, var


def test_publish_faz_pull_rebase_antes_de_empurrar_o_estado():
    # A9: `git push` sem `pull --rebase` descartava o estado do run anterior —
    # dedupe e teto diário furados no run seguinte.
    script = _passo("Commitar estado")["run"]
    assert "git pull --rebase" in script
    assert script.index("git pull --rebase") < script.index("git push")


def test_em_conflito_no_binario_o_run_atual_vence_com_aviso():
    """Menor da revisão da 5C: `git rebase --continue || git rebase --skip`
    DESCARTAVA o commit do run atual — o oposto do que o comentário prometia.
    O conflito passa a ser resolvido abortando o rebase, voltando ao que o
    remoto tem (nada dos outros se perde) e recolocando o state.db deste run."""
    script = _passo("Commitar estado")["run"]
    assert "git rebase --skip" not in script
    assert "git rebase --abort" in script
    assert 'git reset --hard "origin/${GITHUB_REF_NAME}"' in script
    assert script.index("git reset --hard") < script.index("cp \"$ESTADO_DO_RUN\"")
    assert "::warning::" in script and "state.db" in script


def test_o_primeiro_run_nao_falha_por_falta_do_state_db():
    """Menor da revisão: com `set -e`, `git add` de um caminho inexistente
    derruba o passo — o primeiro run (banco ainda não criado) virava vermelho
    sem ter feito nada de errado."""
    script = _passo("Commitar estado")["run"]
    assert "if [ ! -f data/state.db ]" in script
    assert script.index("if [ ! -f data/state.db ]") < script.index("git add data/state.db")


def test_o_commit_de_estado_nao_dispara_a_suite():
    # tests.yml roda em [push]; o GitHub pula o run quando a mensagem do commit
    # tem [skip ci] — sem isso cada run de publicação gastaria minutos rodando
    # a suíte de novo.
    assert "[skip ci]" in _passo("Commitar estado")["run"]


def test_setup_tem_cache_de_pip_e_de_npm():
    # Sem cache, o setup passava de 2,5 min/run: 32 runs/dia estouram os 2.000
    # min/mês do plano grátis do repositório privado.
    steps = _workflow()["jobs"]["run"]["steps"]
    python = next(s for s in steps if str(s.get("uses", "")).startswith("actions/setup-python"))
    assert python["with"]["cache"] == "pip"
    cache = next(s for s in steps if str(s.get("uses", "")).startswith("actions/cache"))
    assert cache["with"]["path"] == "~/.npm"


def test_claude_code_tem_versao_fixa():
    versao = _workflow()["env"]["CLAUDE_CODE_VERSION"]
    assert versao and versao[0].isdigit()
    instala = next(s for s in _workflow()["jobs"]["run"]["steps"]
                   if "npm install -g @anthropic-ai/claude-code" in str(s.get("run", "")))
    assert "@${{ env.CLAUDE_CODE_VERSION }}" in instala["run"]

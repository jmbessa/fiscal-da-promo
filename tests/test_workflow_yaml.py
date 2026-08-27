"""O `publish.yml` é a PRODUÇÃO desde a fase 5C (M8) — o que ele promete
(cadência, cota de minutos, estado que não se perde, segredos) precisa bater
com o que o código e os runbooks dizem."""

import re
from datetime import datetime

import yaml

from afiliado import pipeline

WORKFLOW = ".github/workflows/publish.yml"

# Cota do plano grátis para repositório PRIVADO, e a regra de cobrança que a
# revisão da 5C encontrou: o GitHub arredonda a duração de CADA JOB para o
# minuto seguinte (runner Linux, multiplicador 1×).
COTA_MENSAL_MIN = 2000
MINUTOS_COBRADOS_POR_JOB = 3      # pessimista, enquanto não há medição real
DIAS_DO_MES_MAIS_LONGO = 31


def _workflow() -> dict:
    with open(WORKFLOW, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _config() -> dict:
    with open("config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _passo(nome: str) -> dict:
    steps = _workflow()["jobs"]["run"]["steps"]
    return next(s for s in steps if s.get("name") == nome)


def _disparos_brt() -> list[datetime]:
    """Todos os horários de disparo do cron, convertidos de UTC para BRT
    (UTC−3, sem horário de verão no Brasil desde 2019)."""
    on_section = _workflow().get("on", _workflow().get(True))  # PyYAML: "on:" é True
    horarios: list[datetime] = []
    for entry in on_section["schedule"]:
        minutos, horas = entry["cron"].split()[0], entry["cron"].split()[1]
        inicio, _, fim = horas.partition("-")
        for h in range(int(inicio), int(fim or inicio) + 1):
            for m in (int(x) for x in minutos.split(",")):
                horarios.append(datetime(2026, 8, 26, (h - 3) % 24, m))
    return sorted(horarios)


def _posts_per_run() -> int:
    return int(re.search(r"--posts-per-run (\d+)", _passo("Executar pipeline")["run"]).group(1))


def test_publish_roda_de_hora_em_hora_das_08_as_23_brt():
    # 16 jobs/dia: minuto 0 de 11h a 23h UTC (08:00–20:00 BRT) + 0h a 2h UTC
    # (21:00–23:00 BRT). Eram 32 (de 30 em 30 min) até a revisão da 5C mostrar
    # que o GitHub cobra cada job arredondado para cima — ver
    # test_a_cadencia_cabe_na_cota_mensal_de_minutos.
    crons = [entry["cron"] for entry in
             _workflow().get("on", _workflow().get(True))["schedule"]]
    assert crons == ["0 11-23 * * *", "0 0-2 * * *"]
    assert len(_disparos_brt()) == 16
    assert _disparos_brt()[0].hour == 8 and _disparos_brt()[-1].hour == 23


def test_a_cadencia_cabe_na_cota_mensal_de_minutos():
    """I-3 da revisão: o GitHub cobra cada JOB arredondado para o minuto
    seguinte. 32 runs/dia × 30 dias = 960 jobs/mês; a 2 min cobrados dariam
    1.920 de 2.000 (96%, sem folga) e a 3 min, 2.880 (44% acima). O número de
    1,5 min/run nunca foi medido — enquanto não for, a cadência precisa caber
    no pior caso plausível."""
    jobs_por_mes = len(_disparos_brt()) * DIAS_DO_MES_MAIS_LONGO
    assert jobs_por_mes * MINUTOS_COBRADOS_POR_JOB <= COTA_MENSAL_MIN * 0.8


def test_o_job_tem_timeout_curto():
    # C1 da revisão da 5C: sem `timeout-minutes` vale o padrão do GitHub, 6 h.
    # Um run que entra em martelo contra a API da loja (backoff de 0,5+1,5+4,0 s
    # em cada uma de milhares de chamadas) queimaria 360 min — 18% da cota
    # mensal — antes de alguém perceber.
    assert _workflow()["jobs"]["run"]["timeout-minutes"] == 20


def test_o_veredito_do_tamanho_do_state_db_e_o_medido():
    """I-1/I-2: a fase 5C deixou "alguns GB por mês, o GitHub reclama em
    semanas" — estimativa, e 10× exagerada. A revisão mediu 77,9 MB de arquivo
    a 32 runs/dia e **0,375 MB por commit** de crescimento do git. O runbook e
    o workflow passam a dizer o número medido e o veredito que ele sustenta."""
    with open("docs/runbooks/vps-setup.md", encoding="utf-8") as f:
        runbook = f.read()
    with open(WORKFLOW, encoding="utf-8") as f:
        workflow = f.read()
    for texto in (runbook, workflow):
        assert "0,375 MB por commit" in texto
        assert "Actions serve como produção" in texto
    assert "77,9 MB" in runbook and "174,2 MB" in runbook
    # As duas alavancas ficam DOCUMENTADAS, não aplicadas: 90 dias é o que
    # sustenta a régua honesta.
    assert _config()["selection"]["ref_window_days"] == 90
    assert _config()["shopee"]["candidate_max_age_days"] == 3


def test_publish_nao_roda_dois_ao_mesmo_tempo():
    concurrency = _workflow()["concurrency"]
    assert concurrency["group"] == "publish"
    assert concurrency["cancel-in-progress"] is False


def test_posts_per_run_cobre_o_maior_salto_do_ritmo():
    """A VPS roda a cada 5 min com `posts_per_run: 1`; o Actions roda de hora
    em hora e precisa publicar tudo que o ritmo (`pacing_budget`) liberou desde
    o run anterior. Com 60/dia e 16 runs, o maior salto entre dois runs
    consecutivos é 4 — `--posts-per-run` tem de cobri-lo, com folga para
    recuperar um disparo perdido (atraso de cron é rotina no Actions)."""
    cfg = _config()
    teto = cfg["channels"]["telegram"]["max_per_day"]
    horario = pipeline.schedule_settings(cfg)
    orcamentos = [pipeline.pacing_budget(teto, t, horario["window_start"],
                                         horario["window_end"])
                  for t in _disparos_brt()]
    maior_salto = max(b - a for a, b in zip(orcamentos, orcamentos[1:]))
    assert maior_salto == 4
    assert _posts_per_run() > maior_salto


def test_o_ultimo_run_do_dia_alcanca_o_teto_diario():
    """Menor da revisão: com o último cron às 23:30 e `window_end: 23:55`, o
    orçamento do último run era 59 — a meta de 60/dia era inalcançável por
    construção. A janela do config termina alinhada ao último disparo."""
    cfg = _config()
    teto = cfg["channels"]["telegram"]["max_per_day"]
    horario = pipeline.schedule_settings(cfg)
    assert pipeline.pacing_budget(teto, _disparos_brt()[-1], horario["window_start"],
                                  horario["window_end"]) == teto


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

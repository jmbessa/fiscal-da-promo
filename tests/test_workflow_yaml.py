"""O `publish.yml` é a PRODUÇÃO desde a fase 5C (M8) — o que ele promete
(cadência, cota de minutos, estado que não se perde, segredos) precisa bater
com o que o código e os runbooks dizem."""

import yaml

WORKFLOW = ".github/workflows/publish.yml"


def _workflow() -> dict:
    with open(WORKFLOW, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _passo(nome: str) -> dict:
    steps = _workflow()["jobs"]["run"]["steps"]
    return next(s for s in steps if s.get("name") == nome)


def test_publish_roda_a_cada_30_min_das_08_as_2330_brt():
    # 32 runs/dia: 0,30 de 11h a 23h UTC (08:00–20:30 BRT) + 0,30 de 0h a 2h
    # UTC (21:00–23:30 BRT). Antes eram 16 crons de hora em hora, apresentados
    # como "backup" — 16 posts/dia contra uma meta de 60 (A9).
    on_section = _workflow().get("on", _workflow().get(True))  # PyYAML: "on:" é True
    crons = [entry["cron"] for entry in on_section["schedule"]]
    assert crons == ["0,30 11-23 * * *", "0,30 0-2 * * *"]
    horas = sum(len(range(*(int(p) for p in c.split()[1].split("-"))) ) + 1
                for c in crons)
    assert horas * 2 == 32                       # 2 disparos por hora


def test_publish_nao_roda_dois_ao_mesmo_tempo():
    concurrency = _workflow()["concurrency"]
    assert concurrency["group"] == "publish"
    assert concurrency["cancel-in-progress"] is False


def test_publish_usa_posts_per_run_maior_que_o_da_vps():
    # A VPS roda a cada 5 min com posts_per_run 1; o Actions roda a cada 30.
    assert "--posts-per-run 4" in _passo("Executar pipeline")["run"]


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
    script = _passo("Commitar estado")["run"]
    # Durante um rebase, --theirs é o commit sendo aplicado: o deste run.
    assert "git checkout --theirs data/state.db" in script
    assert "::warning::" in script and "state.db" in script


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

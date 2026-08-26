"""As unidades systemd e o script de instalação são texto — mas o que eles
afirmam sobre a operação (janela, falha notificada, variáveis do .env)
precisa bater com o código. Estes testes travam o que a fase 5A mudou."""

from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[1] / "deploy"


def _unidade(nome: str) -> dict[str, str]:
    pares = {}
    for linha in (DEPLOY / nome).read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if linha and not linha.startswith(("#", "[")) and "=" in linha:
            chave, _, valor = linha.partition("=")
            pares[chave.strip()] = valor.strip()
    return pares


def test_timer_nao_dispara_run_atrasado_fora_da_janela():
    # Persistent=true disparava um run às 03:00 ao religar a VPS.
    assert _unidade("afiliado.timer")["Persistent"] == "false"


ENV_VARS = ("SHOPEE_APP_ID", "SHOPEE_APP_SECRET", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL_ID",
            "TELEGRAM_OPS_CHAT_ID", "CLAUDE_CODE_OAUTH_TOKEN", "IG_USER_ID", "IG_ACCESS_TOKEN",
            "MELI_CLIENT_ID", "MELI_CLIENT_SECRET", "MELI_REFRESH_TOKEN")


def test_service_notifica_a_falha_da_unidade():
    # M12: SIGTERM do TimeoutStartSec, OOM, venv quebrado — a unidade morre e
    # ninguém fica sabendo. OnFailure= dispara a unidade de aviso.
    assert _unidade("afiliado.service")["OnFailure"] == "afiliado-notify.service"


def test_unidade_de_aviso_roda_o_script_que_fala_com_o_bot():
    notify = _unidade("afiliado-notify.service")
    assert notify["Type"] == "oneshot"
    assert notify["ExecStart"].endswith("/deploy/notify-failure.sh")
    script = (DEPLOY / "notify-failure.sh").read_text(encoding="utf-8")
    assert script.startswith("#!/usr/bin/env bash")
    assert "unidade afiliado falhou — ver journalctl -u afiliado" in script
    assert "api.telegram.org" in script and "curl" in script
    assert ".env" in script and "TELEGRAM_OPS_CHAT_ID" in script
    assert "set -x" not in script                 # segredo nunca vai ao journal


def test_install_vps_instala_a_unidade_de_aviso_e_e_reexecutavel():
    script = (DEPLOY / "install-vps.sh").read_text(encoding="utf-8")
    assert "afiliado-notify.service" in script
    assert "notify-failure.sh" in script
    # git pull como o usuário de serviço (git >= 2.35.2: "dubious ownership"
    # para o root num diretório do afiliado) — ou safe.directory para o root.
    assert ('runuser -u "$APP_USER"' in script and "pull --ff-only" in script) \
        or "safe.directory" in script


def test_install_vps_fuso_falhando_e_aviso_em_destaque_nao_echo():
    script = (DEPLOY / "install-vps.sh").read_text(encoding="utf-8")
    assert "timedatectl set-timezone America/Sao_Paulo || echo" not in script
    assert "if ! timedatectl set-timezone America/Sao_Paulo" in script
    assert "ATENÇÃO" in script


def test_install_vps_template_do_env_tem_todas_as_variaveis():
    script = (DEPLOY / "install-vps.sh").read_text(encoding="utf-8")
    for var in ENV_VARS:
        assert f"{var}=" in script, var

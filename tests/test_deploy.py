"""As unidades systemd e o script de instalação são texto — mas o que eles
afirmam sobre a operação (janela, falha notificada, variáveis do .env)
precisa bater com o código. Estes testes travam o que a fase 5A mudou."""

from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
DEPLOY = RAIZ / "deploy"


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


# 12 desde a fase 5C: ART_HOST_BOT_TOKEN entrou com o bot secundário que
# hospeda a arte do feed (A5). 14 desde a fase 5F: IG_USERNAME/IG_PASSWORD, do
# canal `instagram_story_link` — e essas duas NÃO são token revogável.
ENV_VARS = ("SHOPEE_APP_ID", "SHOPEE_APP_SECRET", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL_ID",
            "TELEGRAM_OPS_CHAT_ID", "ART_HOST_BOT_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",
            "IG_USER_ID", "IG_ACCESS_TOKEN", "IG_USERNAME", "IG_PASSWORD",
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


# -- fase 5F: instagrapi (story COM figurinha de link) -------------------------

def test_o_template_avisa_que_ig_password_e_a_senha_da_conta():
    """As outras 12 variáveis são tokens revogáveis; estas duas são a conta.
    Quem preenche o .env precisa ler isso ANTES de colar a senha."""
    script = (DEPLOY / "install-vps.sh").read_text(encoding="utf-8")
    assert "IG_PASSWORD" in script and "senha da conta" in script
    assert "não é token revogável" in script or "não é um token revogável" in script


def test_instagrapi_nao_e_dependencia_obrigatoria():
    """Critério da 5F: a suíte roda numa máquina SEM instagrapi. Ele vive no
    extra `stories` (`pip install -e .[stories]`), e o import do canal é
    preguiçoso — nada aqui importa instagrapi de verdade."""
    pyproject = (RAIZ / "pyproject.toml").read_text(encoding="utf-8")
    obrigatorias = pyproject.split("dependencies = ", 1)[1].split("]", 1)[0]
    assert "instagrapi" not in obrigatorias
    extras = pyproject.split("[project.optional-dependencies]", 1)[1].split("\n[", 1)[0]
    assert "stories = [" in extras and "instagrapi" in extras


def test_a_sessao_do_instagram_nunca_vai_ao_git():
    """`data/ig_session.json` guarda cookies e o perfil de device da conta —
    é credencial viva, e commitá-la entrega a sessão a quem clonar o repo."""
    assert "data/ig_session.json" in (RAIZ / ".gitignore").read_text(encoding="utf-8")


def test_o_banco_do_comando_local_nao_vai_ao_git():
    """Rodada de correção da 5F (I2): `data/state.db` é rastreado no git e o
    Actions o commita a cada run. O `afiliado stories` roda na máquina do dono,
    fora do Actions — se escrevesse no mesmo arquivo, todo `git pull` viraria
    conflito binário. Ele tem banco próprio, e esse é local."""
    gitignore = (RAIZ / ".gitignore").read_text(encoding="utf-8")
    assert "data/state_stories.db" in gitignore
    config = (RAIZ / "config.yaml").read_text(encoding="utf-8")
    assert "stories_path: data/state_stories.db" in config

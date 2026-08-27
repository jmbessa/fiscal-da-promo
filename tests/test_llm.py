import os
import subprocess
from pathlib import Path

from afiliado import llm


def _fake_run(stdout: str, returncode: int = 0):
    def fake(*args, **kwargs):
        return subprocess.CompletedProcess(args=args, returncode=returncode,
                                           stdout=stdout, stderr="")
    return fake


def test_parse_json_block_with_fences():
    assert llm.parse_json_block('Claro!\n```json\n{"a": 1}\n```\n') == {"a": 1}


def test_parse_json_block_invalid():
    assert llm.parse_json_block("sem json aqui") is None
    assert llm.parse_json_block("{quebrado") is None


def test_parse_json_block_skips_invalid_json_before():
    # Should skip {config} and extract {"a": 1}
    assert llm.parse_json_block('Vou usar {config} para calcular: ```json\n{"a": 1}\n```') == {"a": 1}


def test_parse_json_block_first_only():
    # Should return only the first JSON object, not span to the last
    assert llm.parse_json_block('{"first": 1} depois texto {"second": 2}') == {"first": 1}


def test_ask_json_success(monkeypatch):
    monkeypatch.setattr(llm.shutil, "which", lambda _: "claude")
    monkeypatch.setattr(llm.subprocess, "run", _fake_run('{"chosen": ["1"]}'))
    assert llm.ask_json("x") == {"chosen": ["1"]}


def test_ask_json_cli_failure_returns_none(monkeypatch):
    monkeypatch.setattr(llm.shutil, "which", lambda _: "claude")
    monkeypatch.setattr(llm.subprocess, "run", _fake_run("erro", returncode=1))
    assert llm.ask_json("x") is None


def test_ask_json_cli_missing_returns_none(monkeypatch):
    monkeypatch.setattr(llm.shutil, "which", lambda _: None)
    assert llm.ask_json("x") is None


SEGREDOS = {"TELEGRAM_BOT_TOKEN": "1:segredo", "TELEGRAM_CHANNEL_ID": "@c",
            "TELEGRAM_OPS_CHAT_ID": "9", "SHOPEE_APP_ID": "a", "SHOPEE_APP_SECRET": "s",
            "MELI_CLIENT_ID": "m", "MELI_CLIENT_SECRET": "ms", "MELI_REFRESH_TOKEN": "mr",
            "IG_USER_ID": "i", "IG_ACCESS_TOKEN": "it"}


def _captura(monkeypatch):
    capturado = {}

    def fake(args, **kwargs):
        capturado["args"] = list(args)
        capturado.update(kwargs)
        capturado["cwd_conteudo"] = os.listdir(kwargs["cwd"])   # durante a chamada
        return subprocess.CompletedProcess(args=args, returncode=0,
                                           stdout='{"ok": true}', stderr="")

    monkeypatch.setattr(llm.shutil, "which", lambda _: "claude")
    monkeypatch.setattr(llm.subprocess, "run", fake)
    return capturado


def test_ask_json_roda_sem_ferramentas_e_sem_settings_do_projeto(monkeypatch):
    # A2: `claude -p` é um agente com ferramentas; sem estas flags ele lê o
    # .env do CWD e executa hooks de .claude/settings.json do repo. `--bare`
    # NÃO entra: desliga a autenticação OAuth (verificado ao vivo).
    capturado = _captura(monkeypatch)
    assert llm.ask_json("x", model="haiku") == {"ok": True}
    args = capturado["args"]
    assert args[1:3] == ["-p", "x"]
    assert args[args.index("--tools") + 1] == ""
    assert args[args.index("--setting-sources") + 1] == ""
    assert "--strict-mcp-config" in args
    assert "--no-session-persistence" in args
    assert "--bare" not in args
    assert args[args.index("--model") + 1] == "haiku"


def test_ask_json_env_e_lista_branca_sem_segredos(monkeypatch, tmp_path):
    for k, v in SEGREDOS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-token")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    capturado = _captura(monkeypatch)
    llm.ask_json("x")
    env = capturado["env"]
    for k in SEGREDOS:
        assert k not in env
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "oauth-token"
    assert env["CLAUDE_CONFIG_DIR"] == str(tmp_path / "cfg")
    assert "PATH" in env
    assert set(env) <= set(llm.ENV_WHITELIST)


def test_ask_json_claude_config_dir_so_entra_se_existir(monkeypatch):
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    capturado = _captura(monkeypatch)
    llm.ask_json("x")
    assert "CLAUDE_CONFIG_DIR" not in capturado["env"]


def test_ask_json_cwd_e_diretorio_temporario_vazio(monkeypatch):
    capturado = _captura(monkeypatch)
    llm.ask_json("x")
    cwd = Path(capturado["cwd"])
    assert cwd.resolve() != Path.cwd().resolve()
    assert not (cwd / ".env").exists() and not (cwd / ".claude").exists()
    assert capturado["cwd_conteudo"] == []
    assert not cwd.exists()             # descartado depois da chamada


def test_ask_json_conta_chamadas_e_falhas(monkeypatch):
    # C4c: toda falha virava None em silêncio; o pipeline precisa saber quantas
    # vezes caiu no fallback para avisar o ops.
    llm.stats.reset()
    monkeypatch.setattr(llm.shutil, "which", lambda _: "claude")
    monkeypatch.setattr(llm.subprocess, "run", _fake_run('{"a": 1}'))
    assert llm.ask_json("x") == {"a": 1}
    assert (llm.stats.chamadas, llm.stats.falhas) == (1, 0)
    monkeypatch.setattr(llm.subprocess, "run", _fake_run("erro", returncode=1))
    assert llm.ask_json("x") is None
    assert (llm.stats.chamadas, llm.stats.falhas) == (2, 1)
    monkeypatch.setattr(llm.subprocess, "run", _fake_run("sem json nenhum"))
    assert llm.ask_json("x") is None
    assert (llm.stats.chamadas, llm.stats.falhas) == (3, 2)
    monkeypatch.setattr(llm.shutil, "which", lambda _: None)
    assert llm.ask_json("x") is None
    assert (llm.stats.chamadas, llm.stats.falhas) == (4, 3)
    llm.stats.reset()
    assert (llm.stats.chamadas, llm.stats.falhas) == (0, 0)

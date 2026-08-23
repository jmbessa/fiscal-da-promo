import subprocess

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

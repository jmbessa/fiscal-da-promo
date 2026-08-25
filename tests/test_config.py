import pytest

from afiliado.config import load_config


def test_load_config_reads_project_yaml():
    cfg = load_config("config.yaml")
    assert cfg["selection"]["posts_per_run"] == 1
    assert cfg["llm"]["model"] == "haiku"


def test_load_config_rejects_missing_keys(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("llm:\n  model: haiku\n", encoding="utf-8")
    with pytest.raises(ValueError, match="obrigat"):
        load_config(p)

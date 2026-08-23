from pathlib import Path

import yaml

REQUIRED_TOP_KEYS = ("state", "llm", "selection", "shopee", "validation", "copy")


def load_config(path: str | Path = "config.yaml") -> dict:
    cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    missing = [k for k in REQUIRED_TOP_KEYS if k not in cfg]
    if missing:
        raise ValueError(f"config sem chaves obrigatórias: {missing}")
    return cfg

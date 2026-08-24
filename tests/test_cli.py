import json

from afiliado import cli, pipeline
from afiliado.watchlist import Watchlist


def test_run_dry_invokes_pipeline(monkeypatch, tmp_path):
    monkeypatch.setenv("SHOPEE_APP_ID", "id")
    monkeypatch.setenv("SHOPEE_APP_SECRET", "secret")
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        (open("config.yaml", encoding="utf-8").read()
         .replace("data/state.db", str(tmp_path / "s.db").replace("\\", "/"))
         .replace("data/watchlist.json",
                  str(tmp_path / "sem-watchlist.json").replace("\\", "/"))),
        encoding="utf-8")

    chamado = {}

    def fake_run(cfg, sources, channels, db, dry_run=False, validator=None, watchlist=None):
        chamado.update(dry_run=dry_run, n_sources=len(sources), n_channels=len(channels),
                       watchlist=watchlist)
        return pipeline.RunSummary()

    monkeypatch.setattr(pipeline, "run", fake_run)
    assert cli.main(["run", "--dry-run", "--config", str(cfg_file)]) == 0
    assert chamado == {"dry_run": True, "n_sources": 1, "n_channels": 0, "watchlist": None}


def test_run_loads_watchlist_from_config_path(monkeypatch, tmp_path):
    monkeypatch.setenv("SHOPEE_APP_ID", "id")
    monkeypatch.setenv("SHOPEE_APP_SECRET", "secret")
    wl_path = tmp_path / "watchlist.json"
    wl_path.write_text(json.dumps({"generated_at": "2026-08-23", "valid_days": 14}),
                       encoding="utf-8")
    cfg_file = tmp_path / "config.yaml"
    cfg_text = (open("config.yaml", encoding="utf-8").read()
               .replace("data/state.db", str(tmp_path / "s.db").replace("\\", "/")))
    cfg_text += f"\nwatchlist:\n  path: {str(wl_path).replace(chr(92), '/')}\n"
    cfg_file.write_text(cfg_text, encoding="utf-8")

    chamado = {}

    def fake_run(cfg, sources, channels, db, dry_run=False, validator=None, watchlist=None):
        chamado["watchlist"] = watchlist
        return pipeline.RunSummary()

    monkeypatch.setattr(pipeline, "run", fake_run)
    assert cli.main(["run", "--dry-run", "--config", str(cfg_file)]) == 0
    assert isinstance(chamado["watchlist"], Watchlist)


def test_run_builds_channels_from_config(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SHOPEE_APP_ID", "id")
    monkeypatch.setenv("SHOPEE_APP_SECRET", "secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHANNEL_ID", "@canal")
    monkeypatch.setenv("TELEGRAM_OPS_CHAT_ID", "999")
    monkeypatch.delenv("IG_USER_ID", raising=False)
    monkeypatch.delenv("IG_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr(cli, "send_text", lambda *a, **k: None)

    cfg_file = tmp_path / "config.yaml"
    cfg_text = (open("config.yaml", encoding="utf-8").read()
               .replace("data/state.db", str(tmp_path / "s.db").replace("\\", "/"))
               .replace("data/watchlist.json",
                        str(tmp_path / "sem-watchlist.json").replace("\\", "/")))
    # chave de topo duplicada: PyYAML safe_load mantém o último valor, então este
    # bloco sobrescreve a seção `channels` do config.yaml base sem depender de
    # combinar com os comentários inline daquele arquivo.
    cfg_text += "\nchannels:\n  telegram: true\n  story_dispatch: true\n  instagram_feed: true\n"
    cfg_file.write_text(cfg_text, encoding="utf-8")

    chamado = {}

    def fake_run(cfg, sources, channels, db, dry_run=False, validator=None, watchlist=None):
        chamado["channels"] = channels
        return pipeline.RunSummary()

    monkeypatch.setattr(pipeline, "run", fake_run)
    assert cli.main(["run", "--config", str(cfg_file)]) == 0
    assert len(chamado["channels"]) == 2
    names = {c.name for c in chamado["channels"]}
    assert names == {"telegram", "story_dispatch"}
    out = capsys.readouterr().out
    assert "⚠️" in out and "instagram_feed" in out


def test_run_survives_load_watchlist_raising(monkeypatch, tmp_path):
    monkeypatch.setenv("SHOPEE_APP_ID", "id")
    monkeypatch.setenv("SHOPEE_APP_SECRET", "secret")
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        (open("config.yaml", encoding="utf-8").read()
         .replace("data/state.db", str(tmp_path / "s.db").replace("\\", "/"))),
        encoding="utf-8")

    def boom(path):
        raise AttributeError("shape inesperado")

    monkeypatch.setattr(cli, "load_watchlist", boom)

    chamado = {}

    def fake_run(cfg, sources, channels, db, dry_run=False, validator=None, watchlist=None):
        chamado["watchlist"] = watchlist
        return pipeline.RunSummary()

    monkeypatch.setattr(pipeline, "run", fake_run)
    assert cli.main(["run", "--dry-run", "--config", str(cfg_file)]) == 0
    assert chamado["watchlist"] is None

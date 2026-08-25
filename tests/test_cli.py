import io
import json

import pytest
import os

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


def test_run_builds_channels_from_dict_config(monkeypatch, tmp_path, capsys):
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
    cfg_text += (
        "\nchannels:\n"
        "  telegram: true\n"
        "  story_dispatch:\n"
        "    enabled: true\n"
        "    max_per_day: 6\n"
        "  instagram_feed:\n"
        "    enabled: false\n"
    )
    cfg_file.write_text(cfg_text, encoding="utf-8")

    chamado = {}

    def fake_run(cfg, sources, channels, db, dry_run=False, validator=None, watchlist=None):
        chamado["channels"] = channels
        return pipeline.RunSummary()

    monkeypatch.setattr(pipeline, "run", fake_run)
    assert cli.main(["run", "--config", str(cfg_file)]) == 0
    channels = chamado["channels"]
    assert len(channels) == 2
    names = {c.name for c in channels}
    assert names == {"telegram", "story_dispatch"}
    story = next(c for c in channels if c.name == "story_dispatch")
    assert story.max_per_day == 6
    telegram = next(c for c in channels if c.name == "telegram")
    assert getattr(telegram, "max_per_day", None) is None


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



def test_run_passes_brand_handle_to_channels(monkeypatch, tmp_path):
    monkeypatch.setenv("SHOPEE_APP_ID", "id")
    monkeypatch.setenv("SHOPEE_APP_SECRET", "secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHANNEL_ID", "@canal")
    monkeypatch.setenv("TELEGRAM_OPS_CHAT_ID", "999")
    monkeypatch.setattr(cli, "send_text", lambda *a, **k: None)
    cfg_file = tmp_path / "config.yaml"
    cfg_text = (open("config.yaml", encoding="utf-8").read()
               .replace("data/state.db", str(tmp_path / "s.db").replace("\\", "/"))
               .replace("data/watchlist.json",
                        str(tmp_path / "sem-watchlist.json").replace("\\", "/")))
    cfg_text += "\nbrand:\n  handle: \"@teste\"\nchannels:\n  telegram: true\n  story_dispatch: true\n"
    cfg_file.write_text(cfg_text, encoding="utf-8")
    chamado = {}

    def fake_run(cfg, sources, channels, db, dry_run=False, validator=None, watchlist=None):
        chamado["channels"] = channels
        return pipeline.RunSummary()

    monkeypatch.setattr(pipeline, "run", fake_run)
    assert cli.main(["run", "--config", str(cfg_file)]) == 0
    story = next(c for c in chamado["channels"] if c.name == "story_dispatch")
    assert story.brand_handle == "@teste"


def test_run_passes_brand_name_to_channels(monkeypatch, tmp_path):
    # Não sobrescreve a seção `brand:` — o `name` vem do config.yaml real
    # ("Fiscal da Promo"), validando que `_build_channels` repassa `brand.name`.
    monkeypatch.setenv("SHOPEE_APP_ID", "id")
    monkeypatch.setenv("SHOPEE_APP_SECRET", "secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_OPS_CHAT_ID", "999")
    cfg_file = tmp_path / "config.yaml"
    cfg_text = (open("config.yaml", encoding="utf-8").read()
               .replace("data/state.db", str(tmp_path / "s.db").replace("\\", "/"))
               .replace("data/watchlist.json",
                        str(tmp_path / "sem-watchlist.json").replace("\\", "/")))
    cfg_file.write_text(cfg_text, encoding="utf-8")
    chamado = {}

    def fake_run(cfg, sources, channels, db, dry_run=False, validator=None, watchlist=None):
        chamado["channels"] = channels
        return pipeline.RunSummary()

    monkeypatch.setattr(pipeline, "run", fake_run)
    assert cli.main(["run", "--config", str(cfg_file)]) == 0
    story = next(c for c in chamado["channels"] if c.name == "story_dispatch")
    assert story.brand_name == "Fiscal da Promo"


@pytest.mark.dotenv_real
def test_load_dotenv_sets_missing_and_keeps_existing(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    linhas = ["A_NOVA=1", 'JA_EXISTE="ignorado"', "# comentario", "SEM_IGUAL", ""]
    env_file.write_text(chr(10).join(linhas), encoding="utf-8")
    monkeypatch.delenv("A_NOVA", raising=False)
    monkeypatch.setenv("JA_EXISTE", "original")
    assert cli.load_dotenv(env_file) == 2          # .env do projeto tem precedência
    assert os.environ["A_NOVA"] == "1"
    assert os.environ["JA_EXISTE"] == "ignorado"
    monkeypatch.setenv("JA_EXISTE", "original")
    assert cli.load_dotenv(env_file, override=False) == 0
    assert os.environ["JA_EXISTE"] == "original"


@pytest.mark.dotenv_real
def test_load_dotenv_missing_file_is_noop(tmp_path):
    assert cli.load_dotenv(tmp_path / "nao-existe.env") == 0


def test_configure_stdout_makes_cp1252_stream_print_emoji():
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252")
    cli.configure_stdout(stream)
    print("❌ ok", file=stream)
    stream.flush()
    assert raw.getvalue().startswith("❌".encode("utf-8"))


def test_ops_summary_skipped_on_empty_run(monkeypatch, tmp_path):
    monkeypatch.setenv("SHOPEE_APP_ID", "id")
    monkeypatch.setenv("SHOPEE_APP_SECRET", "secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_OPS_CHAT_ID", "999")
    cfg_file = tmp_path / "config.yaml"
    cfg_text = (open("config.yaml", encoding="utf-8").read()
               .replace("data/state.db", str(tmp_path / "s.db").replace("\\", "/"))
               .replace("data/watchlist.json",
                        str(tmp_path / "sem-watchlist.json").replace("\\", "/")))
    cfg_file.write_text(cfg_text, encoding="utf-8")

    enviados = []
    monkeypatch.setattr(cli, "send_text", lambda *a, **k: enviados.append((a, k)))

    def fake_run(cfg, sources, channels, db, dry_run=False, validator=None, watchlist=None):
        return pipeline.RunSummary()

    monkeypatch.setattr(pipeline, "run", fake_run)
    assert cli.main(["run", "--config", str(cfg_file)]) == 0
    assert enviados == []


def test_ops_summary_sent_when_something_happened(monkeypatch, tmp_path):
    monkeypatch.setenv("SHOPEE_APP_ID", "id")
    monkeypatch.setenv("SHOPEE_APP_SECRET", "secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_OPS_CHAT_ID", "999")
    cfg_file = tmp_path / "config.yaml"
    cfg_text = (open("config.yaml", encoding="utf-8").read()
               .replace("data/state.db", str(tmp_path / "s.db").replace("\\", "/"))
               .replace("data/watchlist.json",
                        str(tmp_path / "sem-watchlist.json").replace("\\", "/")))
    cfg_file.write_text(cfg_text, encoding="utf-8")

    enviados = []
    monkeypatch.setattr(cli, "send_text", lambda *a, **k: enviados.append((a, k)))

    def fake_run(cfg, sources, channels, db, dry_run=False, validator=None, watchlist=None):
        return pipeline.RunSummary(published=["x"])

    monkeypatch.setattr(pipeline, "run", fake_run)
    assert cli.main(["run", "--config", str(cfg_file)]) == 0
    assert len(enviados) == 1


def test_ops_summary_forced_by_config(monkeypatch, tmp_path):
    monkeypatch.setenv("SHOPEE_APP_ID", "id")
    monkeypatch.setenv("SHOPEE_APP_SECRET", "secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_OPS_CHAT_ID", "999")
    cfg_file = tmp_path / "config.yaml"
    cfg_text = (open("config.yaml", encoding="utf-8").read()
               .replace("data/state.db", str(tmp_path / "s.db").replace("\\", "/"))
               .replace("data/watchlist.json",
                        str(tmp_path / "sem-watchlist.json").replace("\\", "/")))
    cfg_text += chr(10).join(["", "ops:", "  notify_empty_runs: true", ""])
    cfg_file.write_text(cfg_text, encoding="utf-8")

    enviados = []
    monkeypatch.setattr(cli, "send_text", lambda *a, **k: enviados.append((a, k)))

    def fake_run(cfg, sources, channels, db, dry_run=False, validator=None, watchlist=None):
        return pipeline.RunSummary()

    monkeypatch.setattr(pipeline, "run", fake_run)
    assert cli.main(["run", "--config", str(cfg_file)]) == 0
    assert len(enviados) == 1


def test_build_sources_defaults_to_shopee_only_when_key_absent(monkeypatch):
    monkeypatch.setenv("SHOPEE_APP_ID", "id")
    monkeypatch.setenv("SHOPEE_APP_SECRET", "secret")
    sources = cli._build_sources({})
    assert [s.name for s in sources] == ["shopee"]


def test_run_builds_meli_source_when_enabled_and_env_present(monkeypatch, tmp_path):
    monkeypatch.setenv("SHOPEE_APP_ID", "id")
    monkeypatch.setenv("SHOPEE_APP_SECRET", "secret")
    monkeypatch.setenv("MELI_CLIENT_ID", "mcid")
    monkeypatch.setenv("MELI_CLIENT_SECRET", "msecret")
    cfg_file = tmp_path / "config.yaml"
    cfg_text = (open("config.yaml", encoding="utf-8").read()
               .replace("data/state.db", str(tmp_path / "s.db").replace("\\", "/"))
               .replace("data/watchlist.json",
                        str(tmp_path / "sem-watchlist.json").replace("\\", "/")))
    cfg_text += "\nsources:\n  shopee: true\n  meli: true\n"
    cfg_file.write_text(cfg_text, encoding="utf-8")

    chamado = {}

    def fake_run(cfg, sources, channels, db, dry_run=False, validator=None, watchlist=None):
        chamado["sources"] = sources
        return pipeline.RunSummary()

    monkeypatch.setattr(pipeline, "run", fake_run)
    assert cli.main(["run", "--dry-run", "--config", str(cfg_file)]) == 0
    names = {s.name for s in chamado["sources"]}
    assert names == {"shopee", "meli"}


def test_run_warns_and_skips_meli_without_env(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SHOPEE_APP_ID", "id")
    monkeypatch.setenv("SHOPEE_APP_SECRET", "secret")
    monkeypatch.delenv("MELI_CLIENT_ID", raising=False)
    monkeypatch.delenv("MELI_CLIENT_SECRET", raising=False)
    cfg_file = tmp_path / "config.yaml"
    cfg_text = (open("config.yaml", encoding="utf-8").read()
               .replace("data/state.db", str(tmp_path / "s.db").replace("\\", "/"))
               .replace("data/watchlist.json",
                        str(tmp_path / "sem-watchlist.json").replace("\\", "/")))
    cfg_text += "\nsources:\n  shopee: true\n  meli: true\n"
    cfg_file.write_text(cfg_text, encoding="utf-8")

    chamado = {}

    def fake_run(cfg, sources, channels, db, dry_run=False, validator=None, watchlist=None):
        chamado["sources"] = sources
        return pipeline.RunSummary()

    monkeypatch.setattr(pipeline, "run", fake_run)
    assert cli.main(["run", "--dry-run", "--config", str(cfg_file)]) == 0
    names = {s.name for s in chamado["sources"]}
    assert names == {"shopee"}
    out = capsys.readouterr().out
    assert "⚠️" in out and "meli" in out


def test_instagram_api_variant_from_config(monkeypatch, tmp_path):
    for k, v in {"SHOPEE_APP_ID": "id", "SHOPEE_APP_SECRET": "s", "TELEGRAM_BOT_TOKEN": "tok",
                 "TELEGRAM_CHANNEL_ID": "@c", "TELEGRAM_OPS_CHAT_ID": "999",
                 "IG_USER_ID": "178", "IG_ACCESS_TOKEN": "igtok"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(cli, "send_text", lambda *a, **k: None)
    base = (open("config.yaml", encoding="utf-8").read()
            .replace("data/state.db", str(tmp_path / "s.db").replace(chr(92), "/"))
            .replace("data/watchlist.json", str(tmp_path / "sem.json").replace(chr(92), "/")))
    extra = chr(10).join(["", "instagram:", "  api: facebook_login", "channels:", "  telegram: true",
                          "  instagram_feed: true", ""])
    cfg_file = tmp_path / "config.yaml"; cfg_file.write_text(base + extra, encoding="utf-8")
    chamado = {}

    def fake_run(cfg, sources, channels, db, dry_run=False, validator=None, watchlist=None):
        chamado["channels"] = channels
        return pipeline.RunSummary()

    monkeypatch.setattr(pipeline, "run", fake_run)
    assert cli.main(["run", "--config", str(cfg_file)]) == 0
    ig = next(c for c in chamado["channels"] if c.name == "instagram_feed")
    assert ig.graph.startswith("https://graph.facebook.com")
    assert cli._instagram_api({}) == "instagram_login"

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

    def fake_run(cfg, sources, channels, db, dry_run=False, validator=None, watchlist=None,
                 warnings_iniciais=None):
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

    def fake_run(cfg, sources, channels, db, dry_run=False, validator=None, watchlist=None,
                 warnings_iniciais=None):
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

    def fake_run(cfg, sources, channels, db, dry_run=False, validator=None, watchlist=None,
                 warnings_iniciais=None):
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

    def fake_run(cfg, sources, channels, db, dry_run=False, validator=None, watchlist=None,
                 warnings_iniciais=None):
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

    def fake_run(cfg, sources, channels, db, dry_run=False, validator=None, watchlist=None,
                 warnings_iniciais=None):
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

    def fake_run(cfg, sources, channels, db, dry_run=False, validator=None, watchlist=None,
                 warnings_iniciais=None):
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

    def fake_run(cfg, sources, channels, db, dry_run=False, validator=None, watchlist=None,
                 warnings_iniciais=None):
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

    def fake_run(cfg, sources, channels, db, dry_run=False, validator=None, watchlist=None,
                 warnings_iniciais=None):
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

    def fake_run(cfg, sources, channels, db, dry_run=False, validator=None, watchlist=None,
                 warnings_iniciais=None):
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

    def fake_run(cfg, sources, channels, db, dry_run=False, validator=None, watchlist=None,
                 warnings_iniciais=None):
        return pipeline.RunSummary()

    monkeypatch.setattr(pipeline, "run", fake_run)
    assert cli.main(["run", "--config", str(cfg_file)]) == 0
    assert len(enviados) == 1


def test_run_abortado_manda_o_resumo_com_os_avisos_e_sai_com_erro(monkeypatch, tmp_path):
    # M8: todas as fontes falharam → o run aborta, mas o ops recebe o resumo
    # com os avisos (qual fonte, qual erro), não só "❌ Run abortado".
    monkeypatch.setenv("SHOPEE_APP_ID", "id")
    monkeypatch.setenv("SHOPEE_APP_SECRET", "secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_OPS_CHAT_ID", "999")
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        (open("config.yaml", encoding="utf-8").read()
         .replace("data/state.db", str(tmp_path / "s.db").replace("\\", "/"))
         .replace("data/watchlist.json",
                  str(tmp_path / "sem-watchlist.json").replace("\\", "/"))),
        encoding="utf-8")
    enviados = []
    monkeypatch.setattr(cli, "send_text", lambda token, chat, text, *a, **k: enviados.append(text))

    def fake_run(cfg, sources, channels, db, dry_run=False, validator=None, watchlist=None,
                 warnings_iniciais=None):
        raise pipeline.RunAborted(
            pipeline.RunSummary(warnings=["⚠️ fonte shopee falhou: HTTP 503"]),
            "todas as fontes falharam")

    monkeypatch.setattr(pipeline, "run", fake_run)
    with pytest.raises(pipeline.RunAborted):
        cli.main(["run", "--config", str(cfg_file)])
    assert len(enviados) == 1
    assert enviados[0].startswith("❌ Run abortado: todas as fontes falharam")
    assert "fonte shopee falhou: HTTP 503" in enviados[0]


def test_run_abortado_imprime_a_causa_no_journal(monkeypatch, tmp_path, capsys):
    # M0-4: a causa vai também ao stdout (journalctl), não só ao chat de ops.
    monkeypatch.setenv("SHOPEE_APP_ID", "id")
    monkeypatch.setenv("SHOPEE_APP_SECRET", "secret")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        (open("config.yaml", encoding="utf-8").read()
         .replace("data/state.db", str(tmp_path / "s.db").replace("\\", "/"))
         .replace("data/watchlist.json",
                  str(tmp_path / "sem-watchlist.json").replace("\\", "/"))),
        encoding="utf-8")

    def fake_run(cfg, sources, channels, db, dry_run=False, validator=None, watchlist=None,
                 warnings_iniciais=None):
        raise pipeline.RunAborted(pipeline.RunSummary(),
                                  "todas as fontes falharam — shopee: HTTP 503")

    monkeypatch.setattr(pipeline, "run", fake_run)
    with pytest.raises(pipeline.RunAborted):
        cli.main(["run", "--config", str(cfg_file)])
    assert "❌ Run abortado: todas as fontes falharam — shopee: HTTP 503" in capsys.readouterr().out


def _doctor_base(monkeypatch):
    """doctor com Shopee vazia, ML sem env, LLM ok e Instagram desligado — só o
    Telegram decide o resultado."""
    class _Shopee:
        def fetch_offers(self, cfg):
            return []

    monkeypatch.setattr(cli, "_shopee", lambda: _Shopee())
    monkeypatch.setattr(cli, "_meli", lambda: None)
    monkeypatch.setattr(cli.llm, "ask_json", lambda *a, **k: {"ok": True})
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_OPS_CHAT_ID", "999")
    for k in ("IG_USER_ID", "IG_ACCESS_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    from afiliado import config
    return config.load_config("config.yaml")


def test_doctor_usa_o_retorno_do_send_text(monkeypatch, capsys):
    # M0-5 (revisão da 5A): o doctor imprimia "✅ Telegram: mensagem de teste
    # enviada" ignorando o bool de send_text — bot removido passava no doctor.
    cfg = _doctor_base(monkeypatch)
    monkeypatch.setattr(cli, "send_text", lambda *a, **k: False)
    assert cli.doctor(cfg) == 1
    out = capsys.readouterr().out
    assert "❌ Telegram" in out and "✅ Telegram" not in out

    monkeypatch.setattr(cli, "send_text", lambda *a, **k: True)
    assert cli.doctor(cfg) == 0
    assert "✅ Telegram: mensagem de teste enviada" in capsys.readouterr().out


def test_heartbeat_e_enviado_mesmo_com_notify_empty_runs_false(monkeypatch, tmp_path):
    # M12: o "Bom dia" é um aviso, então passa pelo mesmo caminho de envio —
    # run "vazio" com heartbeat notifica.
    monkeypatch.setenv("SHOPEE_APP_ID", "id")
    monkeypatch.setenv("SHOPEE_APP_SECRET", "secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_OPS_CHAT_ID", "999")
    cfg_file = tmp_path / "config.yaml"
    cfg_text = (open("config.yaml", encoding="utf-8").read()
               .replace("data/state.db", str(tmp_path / "s.db").replace("\\", "/"))
               .replace("data/watchlist.json",
                        str(tmp_path / "sem-watchlist.json").replace("\\", "/")))
    cfg_text += chr(10).join(["", "ops:", "  notify_empty_runs: false", ""])
    cfg_file.write_text(cfg_text, encoding="utf-8")
    enviados = []
    monkeypatch.setattr(cli, "send_text", lambda token, chat, text, *a, **k: enviados.append(text))

    def fake_run(cfg, sources, channels, db, dry_run=False, validator=None, watchlist=None,
                 warnings_iniciais=None):
        return pipeline.RunSummary(
            warnings=["☀️ Bom dia — ontem: 12 publicados, 3 descartados em 190 runs"])

    monkeypatch.setattr(pipeline, "run", fake_run)
    assert cli.main(["run", "--config", str(cfg_file)]) == 0
    assert len(enviados) == 1 and "Bom dia" in enviados[0]


def test_sinal_avisa_o_ops_e_sai_com_128_mais_n(monkeypatch):
    # M12: o SIGTERM do TimeoutStartSec matava o Python sem exceção — sem
    # resumo, sem "❌ Run abortado", ops em silêncio.
    enviados = []
    monkeypatch.setattr(cli, "send_text", lambda token, chat, text, *a, **k: enviados.append(text))
    handler = cli._signal_handler("tok", "999")
    with pytest.raises(SystemExit) as info:
        handler(15, None)
    assert info.value.code == 143
    assert enviados == ["❌ Run interrompido (sinal 15)"]


def test_sinal_sem_ops_so_sai(monkeypatch):
    enviados = []
    monkeypatch.setattr(cli, "send_text", lambda *a, **k: enviados.append(a))
    with pytest.raises(SystemExit) as info:
        cli._signal_handler("", "")(2, None)
    assert info.value.code == 130 and enviados == []


def test_main_instala_handlers_de_sigterm_e_sigint(monkeypatch, tmp_path):
    import signal
    monkeypatch.setenv("SHOPEE_APP_ID", "id")
    monkeypatch.setenv("SHOPEE_APP_SECRET", "secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_OPS_CHAT_ID", "999")
    monkeypatch.setattr(cli, "send_text", lambda *a, **k: None)
    instalados = {}
    monkeypatch.setattr(cli.signal, "signal", lambda signum, h: instalados.__setitem__(signum, h))
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        (open("config.yaml", encoding="utf-8").read()
         .replace("data/state.db", str(tmp_path / "s.db").replace("\\", "/"))
         .replace("data/watchlist.json",
                  str(tmp_path / "sem-watchlist.json").replace("\\", "/"))),
        encoding="utf-8")

    def fake_run(cfg, sources, channels, db, dry_run=False, validator=None, watchlist=None,
                 warnings_iniciais=None):
        return pipeline.RunSummary()

    monkeypatch.setattr(pipeline, "run", fake_run)
    assert cli.main(["run", "--config", str(cfg_file)]) == 0
    assert signal.SIGTERM in instalados and signal.SIGINT in instalados


def test_build_sources_defaults_to_shopee_only_when_key_absent(monkeypatch):
    monkeypatch.setenv("SHOPEE_APP_ID", "id")
    monkeypatch.setenv("SHOPEE_APP_SECRET", "secret")
    sources, avisos = cli._build_sources({})
    assert [s.name for s in sources] == ["shopee"]
    assert avisos == []


def test_build_sources_devolve_o_aviso_da_fonte_sem_env(monkeypatch, capsys):
    monkeypatch.setenv("SHOPEE_APP_ID", "id")
    monkeypatch.setenv("SHOPEE_APP_SECRET", "secret")
    monkeypatch.delenv("MELI_CLIENT_ID", raising=False)
    monkeypatch.delenv("MELI_CLIENT_SECRET", raising=False)
    sources, avisos = cli._build_sources({"sources": {"shopee": True, "meli": True}})
    assert [s.name for s in sources] == ["shopee"]
    assert len(avisos) == 1 and avisos[0].startswith("⚠️") and "meli" in avisos[0]
    assert avisos[0] in capsys.readouterr().out      # o print continua


def test_build_channels_devolve_o_aviso_do_canal_sem_env(monkeypatch, capsys):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHANNEL_ID", "@canal")
    for k in ("TELEGRAM_OPS_CHAT_ID", "IG_USER_ID", "IG_ACCESS_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    channels, avisos = cli._build_channels(
        {"channels": {"telegram": True, "story_dispatch": True, "instagram_feed": True}})
    assert [c.name for c in channels] == ["telegram"]
    assert [a.split(" ")[2] for a in avisos] == ["story_dispatch", "instagram_feed"]
    assert all(a.startswith("⚠️ canal") for a in avisos)
    saida = capsys.readouterr().out
    assert all(a in saida for a in avisos)


def test_run_canal_ligado_sem_env_vira_aviso_no_resumo(monkeypatch, tmp_path):
    # Teste obrigatório 5: o aviso chega ao pipeline (e dali ao chat de ops),
    # não só ao journal.
    monkeypatch.setenv("SHOPEE_APP_ID", "id")
    monkeypatch.setenv("SHOPEE_APP_SECRET", "secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHANNEL_ID", "@canal")
    monkeypatch.setenv("TELEGRAM_OPS_CHAT_ID", "999")
    monkeypatch.delenv("IG_USER_ID", raising=False)
    monkeypatch.delenv("IG_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("MELI_CLIENT_ID", raising=False)
    monkeypatch.setattr(cli, "send_text", lambda *a, **k: None)
    cfg_file = tmp_path / "config.yaml"
    cfg_text = (open("config.yaml", encoding="utf-8").read()
               .replace("data/state.db", str(tmp_path / "s.db").replace("\\", "/"))
               .replace("data/watchlist.json",
                        str(tmp_path / "sem-watchlist.json").replace("\\", "/")))
    cfg_text += ("\nsources:\n  shopee: true\n  meli: true\n"
                 "channels:\n  telegram: true\n  instagram_feed: true\n")
    cfg_file.write_text(cfg_text, encoding="utf-8")
    chamado = {}

    def fake_run(cfg, sources, channels, db, dry_run=False, validator=None, watchlist=None,
                 warnings_iniciais=None):
        chamado["avisos"] = list(warnings_iniciais or [])
        return pipeline.RunSummary()

    monkeypatch.setattr(pipeline, "run", fake_run)
    assert cli.main(["run", "--config", str(cfg_file)]) == 0
    assert any("instagram_feed" in a for a in chamado["avisos"])
    assert any("meli" in a for a in chamado["avisos"])


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

    def fake_run(cfg, sources, channels, db, dry_run=False, validator=None, watchlist=None,
                 warnings_iniciais=None):
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

    def fake_run(cfg, sources, channels, db, dry_run=False, validator=None, watchlist=None,
                 warnings_iniciais=None):
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

    def fake_run(cfg, sources, channels, db, dry_run=False, validator=None, watchlist=None,
                 warnings_iniciais=None):
        chamado["channels"] = channels
        return pipeline.RunSummary()

    monkeypatch.setattr(pipeline, "run", fake_run)
    assert cli.main(["run", "--config", str(cfg_file)]) == 0
    ig = next(c for c in chamado["channels"] if c.name == "instagram_feed")
    assert ig.graph.startswith("https://graph.facebook.com")
    assert cli._instagram_api({}) == "instagram_login"


def test_build_channels_passes_regua_from_config(monkeypatch):
    # selection.min_real_discount_pct / seal_tolerance chegam aos canais que
    # renderizam arte ou legenda — antes só o texto do Telegram (pipeline)
    # respeitava o config; a arte e a legenda do IG usavam o padrão do módulo.
    for k, v in {"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_OPS_CHAT_ID": "999",
                 "IG_USER_ID": "178", "IG_ACCESS_TOKEN": "igtok"}.items():
        monkeypatch.setenv(k, v)
    cfg = {"channels": {"story_dispatch": True, "instagram_feed": True},
           "selection": {"min_real_discount_pct": 30, "seal_tolerance": 1.10}}
    channels = {c.name: c for c in cli._build_channels(cfg)[0]}
    assert set(channels) == {"story_dispatch", "instagram_feed"}
    for canal in channels.values():
        assert canal.min_real_discount_pct == 30
        assert canal.seal_tolerance == 1.10


def test_regua_honra_config_zero():
    # `min_real_discount_pct: 0` e `seal_tolerance: 0` chegam como 0 aos
    # canais — antes `or DEFAULT` os trocava por 10 e 1.05 em silêncio.
    assert cli._regua({"selection": {"min_real_discount_pct": 0, "seal_tolerance": 0}}) == {
        "min_real_discount_pct": 0, "seal_tolerance": 0.0}


def test_build_channels_regua_defaults_match_pricing_and_message(monkeypatch):
    from afiliado import message, pricing
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_OPS_CHAT_ID", "999")
    (story,), _ = cli._build_channels({"channels": {"story_dispatch": True}})
    assert story.min_real_discount_pct == pricing.DEFAULT_MIN_REAL_DISCOUNT_PCT
    assert story.seal_tolerance == message.DEFAULT_SEAL_TOLERANCE

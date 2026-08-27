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
    # Fase 5E: o canal de story do config.yaml real é o `instagram_story` (o
    # `story_dispatch` ficou desligado como fallback manual), e ele pede as
    # envs do Instagram.
    monkeypatch.setenv("IG_USER_ID", "178")
    monkeypatch.setenv("IG_ACCESS_TOKEN", "igtok")
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
    story = next(c for c in chamado["channels"] if c.name == "instagram_story")
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


def test_ops_summary_sent_when_only_despachos(monkeypatch, tmp_path):
    """A12 (rodada de correção): tirar o despacho de `published` não pode
    silenciar o resumo — um run que só despachou artes AINDA é um run que
    aconteceu, e o ops precisa ver a lista."""
    monkeypatch.setenv("SHOPEE_APP_ID", "id")
    monkeypatch.setenv("SHOPEE_APP_SECRET", "secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_OPS_CHAT_ID", "999")
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        open("config.yaml", encoding="utf-8").read()
        .replace("data/state.db", str(tmp_path / "s.db").replace("\\", "/"))
        .replace("data/watchlist.json",
                 str(tmp_path / "sem-watchlist.json").replace("\\", "/")),
        encoding="utf-8")
    enviados = []
    monkeypatch.setattr(cli, "send_text", lambda *a, **k: enviados.append(a))

    def fake_run(cfg, sources, channels, db, dry_run=False, validator=None, watchlist=None,
                 warnings_iniciais=None):
        return pipeline.RunSummary(dispatched=["Kit de arte"])

    monkeypatch.setattr(pipeline, "run", fake_run)
    assert cli.main(["run", "--config", str(cfg_file)]) == 0
    assert len(enviados) == 1 and "Kit de arte" in enviados[0][2]


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

    monkeypatch.setattr(cli, "_shopee", lambda db=None: _Shopee())
    monkeypatch.setattr(cli, "_meli", lambda cfg=None: None)
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


def test_doctor_imprime_a_validacao_do_pool_do_meli(monkeypatch, tmp_path, capsys):
    # Teste obrigatório 8: o doctor roda a mesma validação do pool que o run
    # e imprime o resultado — quantas valem e por que as outras caíram.
    import httpx
    from tests.test_meli import write_pool
    cfg = _doctor_base(monkeypatch)
    monkeypatch.setattr(cli, "send_text", lambda *a, **k: True)
    pool = write_pool(tmp_path / "pool.json", [
        {"product_id": "A", "title": "t", "price_ref_cents": 5000},
        {"product_id": "B", "title": "t", "price_ref_cents": 999999},
        {"product_id": "C", "title": "t", "price_ref_cents": 1000},
        {"product_id": "D", "title": "t", "price_ref_cents": 5000, "price_p25_cents": None},
    ])
    cfg["meli"]["offers_path"] = str(pool)
    # Explícito de propósito: este teste é sobre a MENSAGEM da validação do
    # pool, e o doctor só chega nela com a fonte ligada. Herdar
    # `sources.meli` do config real faria o resultado depender de um
    # interruptor de produção — foi o que quebrou quando o ML foi ligado.
    cfg.setdefault("sources", {})["meli"] = True
    links = tmp_path / "l.json"
    links.write_text('{"A": "https://meli.la/x"}', encoding="utf-8")

    def token_ok(request):
        return httpx.Response(200, json={"access_token": "TOK", "expires_in": 21600})

    meli = cli.MeliSource("cid", "sec", token_path=tmp_path / "t.json",
                          links_path=links,
                          client=httpx.Client(transport=httpx.MockTransport(token_ok)))
    monkeypatch.setattr(cli, "_meli", lambda cfg=None: meli)
    assert cli.doctor(cfg) == 0
    out = capsys.readouterr().out
    assert ("⚠️ Mercado Livre: token ok; 1 oferta(s) válida(s) no pool; "
            "3 entrada(s) do pool ignorada(s) (2 fora da faixa de preço, 1 sem p25)") in out


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


def test_build_channels_nao_carrega_regua(monkeypatch):
    # Fase 5B: a régua (modo + selo) é decidida uma vez no pipeline e viaja
    # no `Post.verdict`; nenhum canal guarda min_real_discount_pct nem a
    # antiga tolerância do selo — não há como arte e texto divergirem por config.
    for k, v in {"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_OPS_CHAT_ID": "999",
                 "IG_USER_ID": "178", "IG_ACCESS_TOKEN": "igtok"}.items():
        monkeypatch.setenv(k, v)
    cfg = {"channels": {"story_dispatch": True, "instagram_feed": True},
           "selection": {"min_real_discount_pct": 30}}
    channels = {c.name: c for c in cli._build_channels(cfg)[0]}
    assert set(channels) == {"story_dispatch", "instagram_feed"}
    for canal in channels.values():
        assert not hasattr(canal, "min_real_discount_pct")
    assert not hasattr(cli, "_regua")


# --- Fase 5C (M4/A5): bot secundário para hospedar a arte do feed ------------

def _env_do_feed(monkeypatch):
    for k, v in {"TELEGRAM_BOT_TOKEN": "TOKDOCANAL", "TELEGRAM_OPS_CHAT_ID": "999",
                 "IG_USER_ID": "178", "IG_ACCESS_TOKEN": "igtok"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("ART_HOST_BOT_TOKEN", raising=False)
    return {"channels": {"instagram_feed": True}}


def test_art_host_bot_token_chega_ao_canal(monkeypatch):
    cfg = _env_do_feed(monkeypatch)
    monkeypatch.setenv("ART_HOST_BOT_TOKEN", " TOKDEARTE ")     # com espaços: .strip()
    canais, avisos = cli._build_channels(cfg)
    assert canais[0].art_host_bot_token == "TOKDEARTE"
    assert canais[0].bot_token == "TOKDOCANAL"
    assert avisos == []


def test_sem_art_host_bot_token_o_canal_usa_o_do_proprio_bot_e_avisa(monkeypatch):
    cfg = _env_do_feed(monkeypatch)
    canais, avisos = cli._build_channels(cfg)
    assert canais[0].art_host_bot_token == "TOKDOCANAL"          # comportamento atual
    assert avisos == [cli.ART_HOST_AVISO]


def test_o_aviso_do_art_host_so_chega_uma_vez_por_dia(tmp_path, monkeypatch):
    """O teste anterior tinha este nome e não testava nada disso: ele olhava a
    lista devolvida por `_build_channels`, que é a mesma em todo run. Quem
    deduplica é o `warn_once` do pipeline (fase 5A, A3) — e é o resumo dele que
    o chat de operações lê."""
    from afiliado import llm, pipeline
    from afiliado.state import StateDB
    from tests.test_models import make_offer
    from tests.test_pipeline import (CFG, FakeChannel, FakeSource, _congela,
                                     no_network_validator)

    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")

    def roda(item_id):
        return pipeline.run(CFG, [FakeSource([make_offer(item_id=item_id)])],
                            [FakeChannel()], db, validator=no_network_validator,
                            warnings_iniciais=[cli.ART_HOST_AVISO])

    _congela(monkeypatch, 9, 0, dia=26)
    assert cli.ART_HOST_AVISO in roda("a").warnings
    assert cli.ART_HOST_AVISO not in roda("b").warnings      # mesmo dia: uma vez só
    _congela(monkeypatch, 9, 0, dia=27)
    assert cli.ART_HOST_AVISO in roda("c").warnings          # dia novo, aviso de novo
    db.close()


# --- Fase 5C (M5/A6): o doctor olha o pool de links do ML --------------------

def _doctor_com_meli(monkeypatch, tmp_path, links: dict | None, ligado: bool):
    import json

    import httpx
    from tests.test_meli import write_pool
    cfg = _doctor_base(monkeypatch)
    monkeypatch.setattr(cli, "send_text", lambda *a, **k: True)
    pool = write_pool(tmp_path / "pool.json", [
        {"product_id": "A", "title": "t", "price_ref_cents": 5000},
        {"product_id": "B", "title": "t", "price_ref_cents": 6000},
    ])
    cfg["meli"]["offers_path"] = str(pool)
    cfg["sources"] = {"shopee": True, "meli": ligado}
    links_path = tmp_path / "links.json"
    if links is not None:
        links_path.write_text(json.dumps(links), encoding="utf-8")
    meli = cli.MeliSource("cid", "sec", token_path=tmp_path / "t.json",
                          links_path=links_path,
                          client=httpx.Client(transport=httpx.MockTransport(
                              lambda r: httpx.Response(200, json={"access_token": "T",
                                                                  "expires_in": 21600}))))
    monkeypatch.setattr(cli, "_meli", lambda c=None: meli)
    return cfg


def test_doctor_falha_com_ml_ligado_e_nenhum_link(monkeypatch, tmp_path, capsys):
    # A6: `data/meli_links.json` nunca foi commitado; com sources.meli: true
    # num clone limpo o ML descartava tudo e o doctor dizia ✅.
    cfg = _doctor_com_meli(monkeypatch, tmp_path, links=None, ligado=True)
    assert cli.doctor(cfg) == 1
    out = capsys.readouterr().out
    assert "❌ Mercado Livre: pool de links ausente" in out
    assert "/meli-links-refresh" in out


def test_doctor_conta_quantos_produtos_do_pool_tem_link(monkeypatch, tmp_path, capsys):
    cfg = _doctor_com_meli(monkeypatch, tmp_path, links={"A": "https://meli.la/a"},
                           ligado=True)
    assert cli.doctor(cfg) == 0
    assert "⚠️ Mercado Livre: 1 de 2 produto(s) do pool com link" in capsys.readouterr().out


def test_doctor_com_pool_de_links_completo(monkeypatch, tmp_path, capsys):
    cfg = _doctor_com_meli(monkeypatch, tmp_path,
                           links={"A": "https://meli.la/a", "B": "https://meli.la/b"},
                           ligado=True)
    assert cli.doctor(cfg) == 0
    assert "✅ Mercado Livre: 2 de 2 produto(s) do pool com link" in capsys.readouterr().out


def test_doctor_com_pool_de_ofertas_vazio_diz_a_causa(monkeypatch, tmp_path, capsys):
    """Menor da revisão da 5C: com o pool de OFERTAS vazio o doctor dizia
    "0 de 0 produto(s) do pool com link" e mandava rodar /meli-links-refresh —
    veredito certo, causa errada. O que falta são PRODUTOS."""
    import json
    cfg = _doctor_com_meli(monkeypatch, tmp_path, links=None, ligado=True)
    vazio = tmp_path / "vazio.json"
    vazio.write_text(json.dumps({"generated_at": "2026-08-26", "valid_days": 30,
                                 "offers": []}), encoding="utf-8")
    cfg["meli"]["offers_path"] = str(vazio)
    assert cli.doctor(cfg) == 1
    out = capsys.readouterr().out
    assert "pool de OFERTAS vazio ou inválido" in out
    assert "/meli-pool-refresh" in out


def test_doctor_com_ml_desligado_nao_falha_por_falta_de_link(monkeypatch, tmp_path, capsys):
    cfg = _doctor_com_meli(monkeypatch, tmp_path, links=None, ligado=False)
    assert cli.doctor(cfg) == 0
    assert "⚠️ Mercado Livre: pool de links ausente" in capsys.readouterr().out


# --- Fase 5C (M8): o Actions roda a cada 30 min, a VPS a cada 5 --------------

def test_posts_per_run_da_linha_de_comando_sobrepoe_o_config(monkeypatch, tmp_path):
    monkeypatch.setenv("SHOPEE_APP_ID", "id")
    monkeypatch.setenv("SHOPEE_APP_SECRET", "secret")
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        open("config.yaml", encoding="utf-8").read()
        .replace("data/state.db", str(tmp_path / "s.db").replace("\\", "/")),
        encoding="utf-8")
    visto = {}

    def fake_run(cfg, *a, **k):
        visto["n"] = cfg["selection"]["posts_per_run"]
        return cli.pipeline.RunSummary()

    monkeypatch.setattr(cli.pipeline, "run", fake_run)
    cli.main(["run", "--dry-run", "--config", str(cfg_file)])
    assert visto["n"] == 1                                    # o do config.yaml
    cli.main(["run", "--dry-run", "--posts-per-run", "4", "--config", str(cfg_file)])
    assert visto["n"] == 4


# --- Fase 5E: o story deixa de ser gesto manual ------------------------------

def _env_do_instagram(monkeypatch):
    for k, v in {"TELEGRAM_BOT_TOKEN": "TOKDOCANAL", "TELEGRAM_OPS_CHAT_ID": "999",
                 "IG_USER_ID": "178", "IG_ACCESS_TOKEN": "igtok"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("ART_HOST_BOT_TOKEN", raising=False)


def test_build_channels_monta_o_instagram_story(monkeypatch):
    _env_do_instagram(monkeypatch)
    monkeypatch.setenv("ART_HOST_BOT_TOKEN", " TOKDEARTE ")      # com espaços: .strip()
    canais, avisos = cli._build_channels({
        "channels": {"instagram_story": {"enabled": True, "max_per_day": 6}},
        "instagram": {"api": "facebook_login"},
        "brand": {"handle": "@ofiscaldapromo", "name": "Fiscal da Promo"},
    })
    assert [c.name for c in canais] == ["instagram_story"]
    canal = canais[0]
    assert canal.max_per_day == 6
    assert canal.max_per_run == 1
    assert canal.ig_user_id == "178" and canal.access_token == "igtok"
    assert canal.art_host_bot_token == "TOKDEARTE"
    assert canal.bot_token == "TOKDOCANAL" and canal.ops_chat_id == "999"
    assert canal.graph.startswith("https://graph.facebook.com")
    assert canal.brand_handle == "@ofiscaldapromo"
    assert canal.brand_name == "Fiscal da Promo"
    # Publicação de verdade: não cai na trilha de despacho manual (A12).
    assert not getattr(canal, "manual", False)
    assert avisos == []


def test_build_channels_avisa_quando_falta_env_do_instagram_story(monkeypatch, capsys):
    _env_do_instagram(monkeypatch)
    monkeypatch.delenv("IG_ACCESS_TOKEN", raising=False)
    canais, avisos = cli._build_channels({"channels": {"instagram_story": True}})
    assert canais == []
    assert [a.split(" ")[2] for a in avisos] == ["instagram_story"]
    assert avisos[0].startswith("⚠️ canal instagram_story ignorado")
    assert avisos[0] in capsys.readouterr().out


def test_o_story_tambem_avisa_quando_falta_o_bot_de_hospedagem(monkeypatch):
    _env_do_instagram(monkeypatch)
    canais, avisos = cli._build_channels({"channels": {"instagram_story": True}})
    assert canais[0].art_host_bot_token == "TOKDOCANAL"          # comportamento atual
    assert avisos == [cli.ART_HOST_AVISO_STORY]
    assert "instagram_story" in cli.ART_HOST_AVISO_STORY
    # Os dois avisos precisam ser textos DIFERENTES: o warn_once do pipeline
    # deduplica pela mensagem (sem dígitos), e um engoliria o outro no dia.
    assert cli.ART_HOST_AVISO_STORY != cli.ART_HOST_AVISO


def test_build_channels_monta_feed_e_story_juntos(monkeypatch):
    _env_do_instagram(monkeypatch)
    canais, avisos = cli._build_channels(
        {"channels": {"instagram_feed": True, "instagram_story": True}})
    assert [c.name for c in canais] == ["instagram_feed", "instagram_story"]
    assert avisos == [cli.ART_HOST_AVISO, cli.ART_HOST_AVISO_STORY]


def test_config_yaml_liga_o_story_automatico_e_desliga_o_despacho_manual():
    """Mudança 3: `instagram_story` é o caminho normal (6/dia, o teto que era do
    despacho) e `story_dispatch` vira fallback manual — para quando a conta
    perder a permissão de publicação."""
    from afiliado import config
    canais = config.load_config("config.yaml")["channels"]
    assert canais["instagram_story"]["enabled"] is True
    assert canais["instagram_story"]["max_per_day"] == 6
    assert canais["story_dispatch"]["enabled"] is False


def test_run_monta_o_story_a_partir_do_config_yaml(monkeypatch, tmp_path):
    """O config.yaml real, sem sobrescrever `channels:` — é ele que decide."""
    monkeypatch.setenv("SHOPEE_APP_ID", "id")
    monkeypatch.setenv("SHOPEE_APP_SECRET", "secret")
    _env_do_instagram(monkeypatch)
    monkeypatch.setenv("TELEGRAM_CHANNEL_ID", "@canal")
    monkeypatch.setattr(cli, "send_text", lambda *a, **k: None)
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
        chamado["channels"] = channels
        return pipeline.RunSummary()

    monkeypatch.setattr(pipeline, "run", fake_run)
    assert cli.main(["run", "--config", str(cfg_file)]) == 0
    nomes = [c.name for c in chamado["channels"]]
    assert "instagram_story" in nomes
    assert "story_dispatch" not in nomes            # fallback manual, desligado
    story = next(c for c in chamado["channels"] if c.name == "instagram_story")
    assert story.max_per_day == 6
    assert story.brand_name == "Fiscal da Promo"

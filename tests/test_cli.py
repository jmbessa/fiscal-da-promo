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
                 warnings_iniciais=None, **kw):
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
                 warnings_iniciais=None, **kw):
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
                 warnings_iniciais=None, **kw):
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
                 warnings_iniciais=None, **kw):
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
                 warnings_iniciais=None, **kw):
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
                 warnings_iniciais=None, **kw):
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
                 warnings_iniciais=None, **kw):
        chamado["channels"] = channels
        return pipeline.RunSummary()

    monkeypatch.setattr(pipeline, "run", fake_run)
    assert cli.main(["run", "--config", str(cfg_file)]) == 0
    # `brand.name` é repassado a TODO canal que desenha arte — não depende de
    # qual canal de story está ligado no config (isso é decisão de operação).
    com_marca = [c for c in chamado["channels"] if hasattr(c, "brand_name")]
    assert com_marca, "nenhum canal recebeu brand_name"
    assert all(c.brand_name == "Fiscal da Promo" for c in com_marca)


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
                 warnings_iniciais=None, **kw):
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
                 warnings_iniciais=None, **kw):
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
                 warnings_iniciais=None, **kw):
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
                 warnings_iniciais=None, **kw):
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
                 warnings_iniciais=None, **kw):
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
                 warnings_iniciais=None, **kw):
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
    cfg = config.load_config("config.yaml")
    # Explícito de propósito: estes testes são sobre o VEREDITO do doctor, e o
    # `config.yaml` é de produção. Herdar `channels.instagram_story_link` dele
    # fazia 20 testes quebrarem no dia em que o dono ligou o canal — a mesma
    # armadilha do `sources.meli` (ver test_zero_silencioso). Quem quer testar
    # esse canal liga na própria fixture.
    cfg.setdefault("channels", {})["instagram_story_link"] = {"enabled": False}
    return cfg


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


class _ShopeeComFeed:
    """Dublê que devolve a fatia de descoberta com a linha do data feed."""

    def __init__(self, feed: str = "", feed_warning: str = ""):
        from afiliado.sources.shopee import DiscoveryStats
        self.discovery_stats = DiscoveryStats(calls=1, nodes=0, eligible=0,
                                              feed=feed, feed_warning=feed_warning)
        self.cfg_visto: dict = {}

    def fetch_offers(self, cfg):
        self.cfg_visto = cfg["shopee"]
        return []


def test_doctor_confere_o_data_feed_junto_com_a_busca(monkeypatch, capsys):
    """Fase 5L: o feed é a segunda superfície de descoberta e só falava pelo
    resumo do run, uma vez por dia. O doctor o exercita — e com o
    `feed_calls_per_run` do config, não com um número inventado."""
    cfg = _doctor_base(monkeypatch)
    monkeypatch.setattr(cli, "send_text", lambda *a, **k: True)
    fonte = _ShopeeComFeed(feed="2 chamadas · 500 linhas · 160 elegíveis · 10 mantidas")
    monkeypatch.setattr(cli, "_shopee", lambda db=None: fonte)

    assert cli.doctor(cfg) == 0
    out = capsys.readouterr().out
    assert "📦 Data feed: 2 chamadas · 500 linhas · 160 elegíveis · 10 mantidas" in out
    assert fonte.cfg_visto["feed_calls_per_run"] == cfg["shopee"]["feed_calls_per_run"]


def test_doctor_avisa_do_feed_fora_do_ar_sem_ficar_vermelho(monkeypatch, capsys):
    """Feed quebrado NÃO é doctor vermelho: a busca continua publicando, e
    pintar de ❌ um sistema que está entregando ensina a ignorar o ❌."""
    cfg = _doctor_base(monkeypatch)
    monkeypatch.setattr(cli, "send_text", lambda *a, **k: True)
    monkeypatch.setattr(cli, "_shopee", lambda db=None: _ShopeeComFeed(
        feed_warning="⚠️ shopee: data feed indisponível (quota) — a busca continua"))

    assert cli.doctor(cfg) == 0
    assert "data feed indisponível" in capsys.readouterr().out


def test_doctor_calado_sobre_o_feed_quando_ele_esta_desligado(monkeypatch, capsys):
    cfg = _doctor_base(monkeypatch)
    monkeypatch.setattr(cli, "send_text", lambda *a, **k: True)
    monkeypatch.setattr(cli, "_shopee", lambda db=None: _ShopeeComFeed())
    cli.doctor(cfg)
    assert "Data feed" not in capsys.readouterr().out


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
    from tests.test_meli import write_links
    links = write_links(tmp_path / "l.json", {"A": {"MLB1": "https://meli.la/x"}})

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
                 warnings_iniciais=None, **kw):
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
                 warnings_iniciais=None, **kw):
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
                 warnings_iniciais=None, **kw):
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
                 warnings_iniciais=None, **kw):
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
                 warnings_iniciais=None, **kw):
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
                 warnings_iniciais=None, **kw):
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
    """`links` no formato da fase 5M: `{product_id: {item_id: link}}`."""
    import httpx
    from tests.test_meli import write_links, write_pool
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
        write_links(links_path, links)
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
    cfg = _doctor_com_meli(monkeypatch, tmp_path, links={"A": {"MLB1": "https://meli.la/a"}},
                           ligado=True)
    assert cli.doctor(cfg) == 0
    assert ("⚠️ Mercado Livre: 1 de 2 produto(s) do pool com anúncio linkado"
            in capsys.readouterr().out)


def test_doctor_com_pool_de_links_completo(monkeypatch, tmp_path, capsys):
    cfg = _doctor_com_meli(monkeypatch, tmp_path,
                           links={"A": {"MLB1": "https://meli.la/a"},
                                  "B": {"MLB2": "https://meli.la/b"}},
                           ligado=True)
    assert cli.doctor(cfg) == 0
    assert ("✅ Mercado Livre: 2 de 2 produto(s) do pool com anúncio linkado"
            in capsys.readouterr().out)


def test_doctor_conta_zero_para_o_produto_que_so_tem_o_link_antigo(monkeypatch, tmp_path,
                                                                   capsys):
    """Fase 5M: o link de catálogo continua no arquivo, mas não publica preço —
    a cobertura precisa dizer isso em vez de esconder atrás de um ✅."""
    cfg = _doctor_com_meli(monkeypatch, tmp_path,
                           links={"A": {"MLB1": "https://meli.la/a"}, "B": {}},
                           ligado=True)
    assert cli.doctor(cfg) == 0
    assert ("⚠️ Mercado Livre: 1 de 2 produto(s) do pool com anúncio linkado"
            in capsys.readouterr().out)


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


def test_doctor_conta_quanto_do_pool_tem_regua_curada(monkeypatch, tmp_path, capsys):
    """Fase 5J (J4): quantas entradas do pool têm régua e quantas estão em modo
    B esperando o nosso price_log. Sem este número, "o ML só publica modo B"
    vira descoberta de semanas depois.

    Desde a 5M (M4) o primeiro número é ZERO por construção — a régua curada é
    do anúncio do buy box e o preço é do anúncio linkado —, e o doctor precisa
    dizer isso em vez de calar."""
    from tests.test_meli import SEM_HISTORICO, write_pool
    cfg = _doctor_com_meli(monkeypatch, tmp_path,
                           links={"A": {"MLB1": "https://meli.la/a"},
                                  "B": {"MLB2": "https://meli.la/b"},
                                  "C": {"MLB3": "https://meli.la/c"}}, ligado=True)
    cfg["meli"]["offers_path"] = str(write_pool(tmp_path / "misto.json", [
        {"product_id": "A", "title": "t", "price_ref_cents": 5000},
        {"product_id": "B", "title": "t", **SEM_HISTORICO},
        {"product_id": "C", "title": "t", **SEM_HISTORICO},
    ]))
    assert cli.doctor(cfg) == 0
    assert ("🏷️ Mercado Livre: 0 de 3 entrada(s) com régua curada; "
            "3 em modo B esperando histórico") in capsys.readouterr().out


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


def test_config_yaml_desliga_o_despacho_manual_de_story():
    """Mudança 3: o story deixou de ser gesto manual. `story_dispatch` fica
    como fallback DESLIGADO — para quando a conta perder a permissão de
    publicação. Qual dos dois canais automáticos está ligado é decisão de
    operação (ver `test_config_yaml_liga_exatamente_um_canal_de_story`)."""
    from afiliado import config
    canais = config.load_config("config.yaml")["channels"]
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
                 warnings_iniciais=None, **kw):
        chamado["channels"] = channels
        return pipeline.RunSummary()

    monkeypatch.setattr(pipeline, "run", fake_run)
    assert cli.main(["run", "--config", str(cfg_file)]) == 0
    nomes = [c.name for c in chamado["channels"]]
    assert "story_dispatch" not in nomes            # fallback manual, desligado
    # O canal da API privada NUNCA sobe pelo `afiliado run` (é o que o Actions
    # executa): ele roda só em `afiliado stories`, na máquina do dono.
    assert "instagram_story_link" not in nomes
    # Qual canal automático de story está ligado é decisão de operação e muda;
    # se for o oficial, ele tem de vir montado com a marca e o teto do config.
    from afiliado import config as _config
    ligado = (_config.load_config(str(cfg_file))["channels"]
               .get("instagram_story", {}).get("enabled"))
    if ligado:
        story = next(c for c in chamado["channels"] if c.name == "instagram_story")
        assert story.brand_name == "Fiscal da Promo"
        assert story.max_per_day >= 1
    else:
        assert "instagram_story" not in nomes


# -- fase 5F: `afiliado stories` e `afiliado ig-login` --------------------------
#
# O canal `instagram_story_link` (instagrapi, story COM figurinha de link) NÃO
# roda no GitHub Actions: IP de datacenter que muda a cada execução + sessão de
# app móvel forjada é o padrão que mais dispara `challenge_required`. Ele tem
# comando próprio, para o dono rodar da máquina dele. Nada aqui importa
# instagrapi nem toca o Instagram.

SENHA_DE_TESTE = "S3nh4-D0-D0n0"


def _env_do_story_link(monkeypatch):
    _env_do_instagram(monkeypatch)
    monkeypatch.setenv("IG_USERNAME", "ofiscaldapromo")
    monkeypatch.setenv("IG_PASSWORD", SENHA_DE_TESTE)
    monkeypatch.delenv("IG_TOTP_SEED", raising=False)


def _config_com_story_link(tmp_path, extra: str = "", oficial: str = "false") -> str:
    cfg_text = (open("config.yaml", encoding="utf-8").read()
                .replace("data/state_stories.db",
                         str(tmp_path / "stories.db").replace("\\", "/"))
                .replace("data/state.db", str(tmp_path / "s.db").replace("\\", "/"))
                .replace("data/watchlist.json",
                         str(tmp_path / "sem-watchlist.json").replace("\\", "/")))
    sessao = str(tmp_path / "ig_session.json").replace("\\", "/")
    cfg_text += ("\nchannels:\n"
                 "  telegram: true\n"
                 "  instagram_feed: true\n"
                 "  instagram_story:\n"
                 f"    enabled: {oficial}\n"     # regra de ouro: nunca os dois
                 "  instagram_story_link:\n"
                 "    enabled: true\n"
                 "    max_per_day: 6\n"
                 "    max_sem_link: 2\n"
                 f"    session_path: {sessao}\n") + extra
    caminho = tmp_path / "config.yaml"
    caminho.write_text(cfg_text, encoding="utf-8")
    return str(caminho)


def _arquivo_do_banco(db) -> str:
    """O arquivo que este StateDB abriu — é assim que se confere que o
    `afiliado stories` não escreve no `state.db` que o Actions commita."""
    return db.conn.execute("PRAGMA database_list").fetchone()[2]


def _captura_run(monkeypatch) -> dict:
    chamado = {}

    def fake_run(cfg, sources, channels, db, dry_run=False, validator=None, watchlist=None,
                 warnings_iniciais=None, **kw):
        chamado.update(channels=channels, dry_run=dry_run, cfg=cfg, db=db,
                       banco=_arquivo_do_banco(db),
                       avisos=list(warnings_iniciais or []), **kw)
        return pipeline.RunSummary()

    monkeypatch.setattr(pipeline, "run", fake_run)
    return chamado


def test_so_o_run_de_producao_cobra_a_cadencia(monkeypatch, tmp_path):
    """Fase 5G (G3): o aviso de buraco na cadência é sobre o agendador do
    Actions, que dispara de hora em hora. O `afiliado stories` roda na máquina
    do dono, de 2 em 2 horas e só enquanto ela está acordada — lá, um intervalo
    grande é o normal, e "~3 disparos perdidos" seria contado com a cadência
    errada. Um falso positivo por semana ensina o dono a ignorar o aviso."""
    monkeypatch.setenv("SHOPEE_APP_ID", "id")
    monkeypatch.setenv("SHOPEE_APP_SECRET", "secret")
    _env_do_story_link(monkeypatch)
    monkeypatch.setenv("TELEGRAM_CHANNEL_ID", "@canal")
    monkeypatch.setattr(cli, "send_text", lambda *a, **k: None)
    cfg_file = _config_com_story_link(tmp_path)

    chamado = _captura_run(monkeypatch)
    assert cli.main(["stories", "--config", cfg_file]) == 0
    assert chamado["checa_cadencia"] is False

    chamado = _captura_run(monkeypatch)
    assert cli.main(["run", "--config", cfg_file]) == 0
    assert chamado["checa_cadencia"] is True


def test_stories_roda_so_os_canais_de_story(monkeypatch, tmp_path):
    """Mudança 4: `afiliado stories` reaproveita o pipeline inteiro (ritmo,
    dedupe, teto diário, resumo) com os canais de story e mais nada — o
    Telegram e o feed continuam saindo pelo `afiliado run`, no Actions."""
    monkeypatch.setenv("SHOPEE_APP_ID", "id")
    monkeypatch.setenv("SHOPEE_APP_SECRET", "secret")
    _env_do_story_link(monkeypatch)
    monkeypatch.setenv("TELEGRAM_CHANNEL_ID", "@canal")
    monkeypatch.setattr(cli, "send_text", lambda *a, **k: None)
    chamado = _captura_run(monkeypatch)

    assert cli.main(["stories", "--config", _config_com_story_link(tmp_path)]) == 0

    canal = chamado["channels"][0]
    assert [c.name for c in chamado["channels"]] == ["instagram_story_link"]
    assert canal.max_per_day == 6 and canal.max_sem_link == 2
    assert canal.brand_handle == "@ofiscaldapromo"
    assert str(canal.session_path) == str(tmp_path / "ig_session.json")
    assert chamado["dry_run"] is False


def test_run_nao_monta_o_canal_de_api_privada_nem_ligado(monkeypatch, tmp_path, capsys):
    """A trava que importa: `afiliado run` é o que roda no Actions. Mesmo com o
    canal ligado no config, ele não é montado ali — e o aviso diz por quê."""
    monkeypatch.setenv("SHOPEE_APP_ID", "id")
    monkeypatch.setenv("SHOPEE_APP_SECRET", "secret")
    _env_do_story_link(monkeypatch)
    monkeypatch.setenv("TELEGRAM_CHANNEL_ID", "@canal")
    monkeypatch.setattr(cli, "send_text", lambda *a, **k: None)
    chamado = _captura_run(monkeypatch)

    assert cli.main(["run", "--config", _config_com_story_link(tmp_path)]) == 0

    nomes = [c.name for c in chamado["channels"]]
    assert "instagram_story_link" not in nomes
    assert "telegram" in nomes                       # o resto do run continua
    assert cli.AVISO_STORY_LINK_FORA_DO_RUN in chamado["avisos"]
    assert "afiliado stories" in cli.AVISO_STORY_LINK_FORA_DO_RUN
    assert "Actions" in cli.AVISO_STORY_LINK_FORA_DO_RUN
    assert cli.AVISO_STORY_LINK_FORA_DO_RUN in capsys.readouterr().out


def test_stories_dry_run_nao_publica_nem_escreve(monkeypatch, tmp_path):
    """Regra da 5A: `--dry-run` não constrói canal nenhum (logo não publica) e
    o pipeline não escreve no banco."""
    monkeypatch.setenv("SHOPEE_APP_ID", "id")
    monkeypatch.setenv("SHOPEE_APP_SECRET", "secret")
    _env_do_story_link(monkeypatch)
    chamado = _captura_run(monkeypatch)

    assert cli.main(["stories", "--dry-run",
                     "--config", _config_com_story_link(tmp_path)]) == 0
    assert chamado["channels"] == [] and chamado["dry_run"] is True


def test_stories_aceita_posts(monkeypatch, tmp_path):
    monkeypatch.setenv("SHOPEE_APP_ID", "id")
    monkeypatch.setenv("SHOPEE_APP_SECRET", "secret")
    _env_do_story_link(monkeypatch)
    chamado = _captura_run(monkeypatch)

    assert cli.main(["stories", "--dry-run", "--posts", "3",
                     "--config", _config_com_story_link(tmp_path)]) == 0
    assert chamado["cfg"]["selection"]["posts_per_run"] == 3


def test_stories_sem_credencial_avisa_e_nao_monta(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SHOPEE_APP_ID", "id")
    monkeypatch.setenv("SHOPEE_APP_SECRET", "secret")
    _env_do_story_link(monkeypatch)
    monkeypatch.delenv("IG_PASSWORD", raising=False)
    monkeypatch.setattr(cli, "send_text", lambda *a, **k: None)
    chamado = _captura_run(monkeypatch)

    assert cli.main(["stories", "--config", _config_com_story_link(tmp_path)]) == 0
    assert chamado["channels"] == []
    aviso = next(a for a in chamado["avisos"] if "instagram_story_link" in a)
    assert "IG_USERNAME/IG_PASSWORD" in aviso
    assert SENHA_DE_TESTE not in capsys.readouterr().out


def test_stories_sem_sessao_salva_avisa_para_rodar_ig_login(monkeypatch, tmp_path):
    monkeypatch.setenv("SHOPEE_APP_ID", "id")
    monkeypatch.setenv("SHOPEE_APP_SECRET", "secret")
    _env_do_story_link(monkeypatch)
    monkeypatch.setattr(cli, "send_text", lambda *a, **k: None)
    chamado = _captura_run(monkeypatch)

    assert cli.main(["stories", "--config", _config_com_story_link(tmp_path)]) == 0
    assert any("ig-login" in a for a in chamado["avisos"])


# -- rodada de correção: I1, I2, I3 --------------------------------------------

def _ambiente_de_stories(monkeypatch):
    monkeypatch.setenv("SHOPEE_APP_ID", "id")
    monkeypatch.setenv("SHOPEE_APP_SECRET", "secret")
    _env_do_story_link(monkeypatch)
    monkeypatch.setenv("TELEGRAM_CHANNEL_ID", "@canal")
    monkeypatch.setattr(cli, "send_text", lambda *a, **k: None)


def test_stories_nao_monta_o_canal_da_graph_api(monkeypatch, tmp_path):
    """I1: `instagram_story` (Graph API) sai pelo `afiliado run`, no Actions.
    Montá-lo TAMBÉM aqui daria dois tetos de 6/dia e dois dedupes sobre a mesma
    conta — o comando local ignora, e diz de quem ele é."""
    _ambiente_de_stories(monkeypatch)
    chamado = _captura_run(monkeypatch)
    cfg_file = _config_com_story_link(tmp_path, oficial="true",
                                      extra="  # oficial ligado\n")
    # Só o oficial ligado: o de figurinha fica de fora do teste do I1.
    texto = open(cfg_file, encoding="utf-8").read().replace(
        "  instagram_story_link:\n    enabled: true\n",
        "  instagram_story_link:\n    enabled: false\n")
    open(cfg_file, "w", encoding="utf-8").write(texto)

    assert cli.main(["stories", "--config", cfg_file]) == 0
    assert chamado["channels"] == []
    assert cli.AVISO_STORY_OFICIAL_FORA_DO_STORIES in chamado["avisos"]
    assert "afiliado run" in cli.AVISO_STORY_OFICIAL_FORA_DO_STORIES


def test_stories_recusa_o_canal_privado_com_o_oficial_ligado(monkeypatch, tmp_path,
                                                             capsys):
    """I3: a regra de ouro vira código. Com os dois ligados o `afiliado stories`
    publicava o MESMO post pela API privada e pela oficial, na mesma conta e no
    mesmo minuto — o padrão que a investigação apontou como o que mais chama
    atenção. O doctor reclamava; nada impedia. Agora falha FECHADA."""
    _ambiente_de_stories(monkeypatch)
    chamado = _captura_run(monkeypatch)

    assert cli.main(["stories", "--config",
                     _config_com_story_link(tmp_path, oficial="true")]) == 0

    assert chamado["channels"] == []
    aviso = next(a for a in chamado["avisos"]
                 if a == cli.AVISO_REGRA_DE_OURO)
    assert "instagram_story" in aviso and "doctor" in aviso
    assert aviso in capsys.readouterr().out


def test_stories_usa_banco_proprio_e_o_run_usa_o_do_actions(monkeypatch, tmp_path):
    """I2: `data/state.db` é rastreado no git e o Actions o commita. O comando
    local escrevendo nele faria todo `git pull` virar conflito binário."""
    _ambiente_de_stories(monkeypatch)
    chamado = _captura_run(monkeypatch)
    cfg_file = _config_com_story_link(tmp_path)

    assert cli.main(["stories", "--config", cfg_file]) == 0
    assert chamado["banco"] == str(tmp_path / "stories.db")

    assert cli.main(["run", "--config", cfg_file]) == 0
    assert chamado["banco"] == str(tmp_path / "s.db")


def test_o_canal_recebe_o_banco_para_o_desarme_atravessar_o_run(monkeypatch, tmp_path):
    """C2 do lado do comando: sem o banco, o desarme voltaria a valer só por um
    processo."""
    _ambiente_de_stories(monkeypatch)
    chamado = _captura_run(monkeypatch)

    assert cli.main(["stories", "--config", _config_com_story_link(tmp_path)]) == 0
    canal = chamado["channels"][0]
    assert canal.estado is chamado["db"]


def test_stories_avisa_quando_o_canal_amanheceu_desarmado(monkeypatch, tmp_path):
    """O canal desarmado ontem à noite (ou no run das 14h) nasce fechado e o
    ops precisa saber por quê — senão o dia fica mudo em silêncio."""
    from afiliado.channels import instagram_story_link as mod
    from afiliado.state import StateDB

    _ambiente_de_stories(monkeypatch)
    cfg_file = _config_com_story_link(tmp_path)
    db = StateDB(tmp_path / "stories.db")
    db.set_day_flag(mod.CHAVE_DESARMADO, mod.AVISO_SESSAO)
    db.close()
    chamado = _captura_run(monkeypatch)

    assert cli.main(["stories", "--config", cfg_file]) == 0
    canal = chamado["channels"][0]
    assert canal.disponivel is False and canal.max_per_run == 0
    assert mod.AVISO_SESSAO in chamado["avisos"]     # e o canal não repete depois
    assert canal.warnings == []


# -- `afiliado ig-login` -------------------------------------------------------

class _ClienteFalso:
    """Duplo do `Client` do instagrapi para o `ig-login`: nada de rede."""

    ultimo = None

    def __init__(self, erro=None):
        self.chamadas = []
        self.erro = erro
        _ClienteFalso.ultimo = self

    def load_settings(self, caminho):
        self.chamadas.append(("load_settings", str(caminho)))

    def dump_settings(self, caminho):
        self.chamadas.append(("dump_settings", str(caminho)))
        from pathlib import Path
        Path(caminho).write_text('{"uuids": {}}', encoding="utf-8")

    def totp_generate_code(self, seed):
        self.chamadas.append(("totp", seed))
        return "654321"

    def login(self, usuario, senha, **kwargs):
        self.chamadas.append(("login", usuario, kwargs.get("verification_code")))
        if self.erro is not None:
            raise self.erro
        return True


def _instagrapi_falso(monkeypatch, erro=None):
    from afiliado.channels import instagram_story_link as mod
    monkeypatch.setattr(mod, "_instagrapi", lambda: (lambda: _ClienteFalso(erro), None, ()))


def test_ig_login_cria_a_sessao_e_nao_imprime_credencial(monkeypatch, tmp_path, capsys):
    """Mudança 3: as credenciais vêm do AMBIENTE, nunca da linha de comando —
    argumento de CLI fica no histórico do shell e no `ps` de qualquer usuário."""
    _env_do_story_link(monkeypatch)
    _instagrapi_falso(monkeypatch)
    cfg_file = _config_com_story_link(tmp_path)

    assert cli.main(["ig-login", "--config", cfg_file]) == 0

    sessao = tmp_path / "ig_session.json"
    assert sessao.is_file()
    assert [c[0] for c in _ClienteFalso.ultimo.chamadas] == ["login", "dump_settings"]
    saida = capsys.readouterr().out
    assert "✅" in saida and str(sessao) in saida     # só sucesso e o caminho
    assert SENHA_DE_TESTE not in saida


def test_ig_login_bem_sucedido_rearma_o_canal_desarmado(monkeypatch, tmp_path):
    """C2: o desarme dura o dia — e `afiliado ig-login` é o gesto que diz "a
    sessão voltou". Ele apaga a marca; senão o dono re-logaria e o canal
    continuaria mudo até a virada do dia, sem entender por quê."""
    from afiliado.channels import instagram_story_link as mod
    from afiliado.state import StateDB

    _env_do_story_link(monkeypatch)
    _instagrapi_falso(monkeypatch)
    cfg_file = _config_com_story_link(tmp_path)
    db = StateDB(tmp_path / "stories.db")
    db.set_day_flag(mod.CHAVE_DESARMADO, mod.AVISO_SESSAO)
    db.set_day_flag(mod.CHAVE_SEM_LINK, "1")
    db.close()

    assert cli.main(["ig-login", "--config", cfg_file]) == 0

    db = StateDB(tmp_path / "stories.db")
    assert db.day_flag(mod.CHAVE_DESARMADO) == ""
    assert db.day_flag(mod.CHAVE_SEM_LINK) == ""
    db.close()


def test_ig_login_que_falha_nao_rearma_nada(monkeypatch, tmp_path):
    """A contraprova: um login que falhou não é evidência de sessão boa."""
    from afiliado.channels import instagram_story_link as mod
    from afiliado.state import StateDB

    _env_do_story_link(monkeypatch)
    _instagrapi_falso(monkeypatch, erro=RuntimeError("challenge_required"))
    cfg_file = _config_com_story_link(tmp_path)
    db = StateDB(tmp_path / "stories.db")
    db.set_day_flag(mod.CHAVE_DESARMADO, mod.AVISO_SESSAO)
    db.close()

    assert cli.main(["ig-login", "--config", cfg_file]) == 1

    db = StateDB(tmp_path / "stories.db")
    assert db.day_flag(mod.CHAVE_DESARMADO) == mod.AVISO_SESSAO
    db.close()


def test_ig_login_usa_totp_quando_ha_semente(monkeypatch, tmp_path):
    """2FA do instagrapi é só TOTP (app autenticador); SMS não funciona."""
    _env_do_story_link(monkeypatch)
    monkeypatch.setenv("IG_TOTP_SEED", "SEMENTE")
    _instagrapi_falso(monkeypatch)

    assert cli.main(["ig-login", "--config", _config_com_story_link(tmp_path)]) == 0
    chamadas = _ClienteFalso.ultimo.chamadas
    assert ("totp", "SEMENTE") in chamadas
    assert ("login", "ofiscaldapromo", "654321") in chamadas


def test_ig_login_reaproveita_a_sessao_existente(monkeypatch, tmp_path):
    _env_do_story_link(monkeypatch)
    _instagrapi_falso(monkeypatch)
    (tmp_path / "ig_session.json").write_text('{"uuids": {}}', encoding="utf-8")

    assert cli.main(["ig-login", "--config", _config_com_story_link(tmp_path)]) == 0
    assert [c[0] for c in _ClienteFalso.ultimo.chamadas] == [
        "load_settings", "login", "dump_settings"]


def test_ig_login_sem_env_falha_dizendo_o_que_falta(monkeypatch, tmp_path, capsys):
    _env_do_story_link(monkeypatch)
    monkeypatch.delenv("IG_PASSWORD", raising=False)

    assert cli.main(["ig-login", "--config", _config_com_story_link(tmp_path)]) == 1
    saida = capsys.readouterr().out
    assert "❌" in saida and "IG_USERNAME" in saida and "IG_PASSWORD" in saida


def test_ig_login_que_falha_nao_vaza_a_senha(monkeypatch, tmp_path, capsys):
    """Teste 10 do brief, do lado do comando: nem quando a senha vem DENTRO do
    texto da exceção do instagrapi."""
    _env_do_story_link(monkeypatch)
    _instagrapi_falso(monkeypatch, erro=RuntimeError(f"login {SENHA_DE_TESTE} recusado"))

    assert cli.main(["ig-login", "--config", _config_com_story_link(tmp_path)]) == 1
    saida = capsys.readouterr().out
    assert "❌" in saida and "RuntimeError" in saida
    assert SENHA_DE_TESTE not in saida
    assert not (tmp_path / "ig_session.json").exists()


def test_config_yaml_liga_exatamente_um_canal_de_story():
    """A REGRA DE OURO, e só ela: publicar pela API privada e pela oficial na
    MESMA conta é o padrão que chama atenção.

    Este teste afirmava QUAL dos dois estava ligado — e quebrou junto com 19
    outros no dia em que o dono trocou a escolha dele (2026-08-27, ao adotar o
    caminho do sticker). Qual canal usar é decisão de operação e muda; o que
    não muda é que só um pode estar ligado."""
    from afiliado import config
    canais = config.load_config("config.yaml")["channels"]
    link = canais["instagram_story_link"]
    oficial = canais["instagram_story"]
    assert bool(link["enabled"]) != bool(oficial["enabled"]), (
        "exatamente um canal de story deve estar ligado em config.yaml")
    assert link["max_sem_link"] >= 1


# -- `doctor` e a regra de ouro ------------------------------------------------

def _doctor_com_story_link(monkeypatch, tmp_path, ligado=True, oficial=False):
    cfg = _doctor_base(monkeypatch)
    monkeypatch.setattr(cli, "send_text", lambda *a, **k: True)
    cfg["channels"] = {"instagram_story_link": {"enabled": ligado,
                                                "max_per_day": 6, "max_sem_link": 2,
                                                "session_path": str(tmp_path / "ig.json")},
                       "instagram_story": {"enabled": oficial}}
    # O doctor lê o desarme do banco do `afiliado stories`: aponte para tmp_path
    # senão o teste passa a depender do estado real da máquina.
    cfg.setdefault("state", {})["stories_path"] = str(tmp_path / "stories.db")
    return cfg


def _sem_login_de_verdade(monkeypatch):
    """Prende a Mudança 6: o doctor NÃO faz login. Cada autenticação extra é o
    que atrai desafio — um diagnóstico por hora seria um login por hora."""
    from afiliado.channels import instagram_story_link as mod

    def boom():
        raise AssertionError("o doctor tentou entrar no Instagram")

    monkeypatch.setattr(mod, "_instagrapi", boom)


def test_doctor_reclama_com_os_dois_canais_de_story_ligados(monkeypatch, tmp_path, capsys):
    """Teste 9 do brief. Publicar pela API privada e pela oficial na mesma
    conta, no mesmo dia, é o padrão que chama atenção."""
    _env_do_story_link(monkeypatch)
    _sem_login_de_verdade(monkeypatch)
    cfg = _doctor_com_story_link(monkeypatch, tmp_path, ligado=True, oficial=True)

    assert cli.doctor(cfg) == 1
    saida = capsys.readouterr().out
    assert "❌" in saida
    assert "instagram_story_link" in saida and "instagram_story" in saida
    assert "mesma conta" in saida.lower()
    assert SENHA_DE_TESTE not in saida


def test_doctor_confere_a_sessao_e_as_credenciais_sem_mostrar_valor(monkeypatch, tmp_path,
                                                                    capsys):
    _env_do_story_link(monkeypatch)
    _sem_login_de_verdade(monkeypatch)
    cfg = _doctor_com_story_link(monkeypatch, tmp_path)
    (tmp_path / "ig.json").write_text('{"uuids": {}}', encoding="utf-8")

    assert cli.doctor(cfg) == 0
    saida = capsys.readouterr().out
    assert "IG_USERNAME/IG_PASSWORD presentes" in saida
    # O que o mtime mede é a ÚLTIMA GRAVAÇÃO, e `_guarda_sessao` reescreve o
    # arquivo a cada login bem-sucedido: "sessão de N dias" seria sempre ~0 e
    # não diria nada sobre a idade do device. O texto diz o que o número é.
    assert "última sessão gravada há 0 dia(s)" in saida
    assert str(tmp_path / "ig.json") in saida
    assert SENHA_DE_TESTE not in saida          # presença, nunca valor


def test_a_senha_e_lida_sem_strip(monkeypatch, tmp_path, capsys):
    """Menor da revisão: `_env` fazia `.strip()` no IG_PASSWORD. Senha que
    termina em espaço vira `BadPassword` — que o canal relata como "sessão
    inválida", mandando o dono para o galho errado do runbook."""
    _env_do_story_link(monkeypatch)
    _sem_login_de_verdade(monkeypatch)
    monkeypatch.setenv("IG_PASSWORD", "  ")     # uma senha de dois espaços É uma senha
    cfg = _doctor_com_story_link(monkeypatch, tmp_path)

    assert cli.doctor(cfg) == 0
    assert "IG_USERNAME/IG_PASSWORD presentes" in capsys.readouterr().out
    assert cli._senha() == "  "


def test_doctor_sem_sessao_manda_rodar_ig_login(monkeypatch, tmp_path, capsys):
    _env_do_story_link(monkeypatch)
    _sem_login_de_verdade(monkeypatch)
    cfg = _doctor_com_story_link(monkeypatch, tmp_path)

    assert cli.doctor(cfg) == 0        # o canal ainda funciona: ele loga na hora
    saida = capsys.readouterr().out
    assert "⚠️" in saida and "afiliado ig-login" in saida


def test_doctor_sem_credenciais_do_instagrapi_falha(monkeypatch, tmp_path, capsys):
    _env_do_story_link(monkeypatch)
    monkeypatch.delenv("IG_PASSWORD", raising=False)
    _sem_login_de_verdade(monkeypatch)
    cfg = _doctor_com_story_link(monkeypatch, tmp_path)

    assert cli.doctor(cfg) == 1
    saida = capsys.readouterr().out
    assert "❌" in saida and "IG_PASSWORD" in saida
    assert "IG_USERNAME/IG_PASSWORD presentes" not in saida


def test_doctor_calado_quando_o_canal_esta_desligado(monkeypatch, tmp_path, capsys):
    _env_do_story_link(monkeypatch)
    _sem_login_de_verdade(monkeypatch)
    cfg = _doctor_com_story_link(monkeypatch, tmp_path, ligado=False, oficial=True)

    assert cli.doctor(cfg) == 0
    assert "instagram_story_link" not in capsys.readouterr().out


def test_ig_login_sem_instagrapi_instalado_diz_como_instalar(monkeypatch, tmp_path, capsys):
    from afiliado.channels import instagram_story_link as mod

    def sem_biblioteca():
        raise ImportError("No module named 'instagrapi'")

    _env_do_story_link(monkeypatch)
    monkeypatch.setattr(mod, "_instagrapi", sem_biblioteca)
    assert cli.main(["ig-login", "--config", _config_com_story_link(tmp_path)]) == 1
    assert "pip install" in capsys.readouterr().out


# --- Fase 5E (revisão): o doctor confere a PERMISSÃO de publicar --------------
# `GET /{ig_user}?fields=username` passa só com `instagram_basic` e dizia ✅ com
# a permissão de PUBLICAR perdida. Agora que o story também sai pela API, essa
# perda mata os DOIS canais do Instagram de uma vez, e o único sinal seria o
# aviso de 3 falhas seguidas — enquanto o config.yaml manda o dono ligar o
# `story_dispatch` exatamente nesse evento. Nenhum teste toca a rede.

def _doctor_do_instagram(monkeypatch, resposta, status=200):
    """doctor com tudo verde, Instagram configurado e a Graph API devolvendo
    `resposta`. Devolve `(cfg, chamadas)` — `chamadas` registra (url, params)."""
    import httpx
    cfg = _doctor_base(monkeypatch)
    monkeypatch.setattr(cli, "send_text", lambda *a, **k: True)
    monkeypatch.setenv("IG_USER_ID", "178")
    monkeypatch.setenv("IG_ACCESS_TOKEN", "igtok")
    chamadas = []

    def fake_get(url, params=None, timeout=None):
        chamadas.append((url, params))
        return httpx.Response(status, json=resposta)

    monkeypatch.setattr(cli.httpx, "get", fake_get)
    return cfg, chamadas


def test_doctor_checa_a_permissao_de_publicacao_e_mostra_a_cota(monkeypatch, capsys):
    """Uma chamada só: `content_publishing_limit` exige a permissão de publicar
    E devolve a cota compartilhada entre feed e story."""
    cfg, chamadas = _doctor_do_instagram(monkeypatch, {"data": [{
        "config": {"quota_total": 100, "quota_duration": 86400}, "quota_usage": 8}]})
    assert cli.doctor(cfg) == 0
    url, params = chamadas[0]
    assert url.endswith("/178/content_publishing_limit")
    assert params["fields"] == "config,quota_usage"
    assert params["access_token"] == "igtok"
    assert ("✅ Instagram: @ofiscaldapromo · publicação liberada · "
            "8 de 100 na cota de 24 h") in capsys.readouterr().out


def test_doctor_falha_quando_a_conta_perdeu_a_permissao_de_publicar(monkeypatch, capsys):
    cfg, _ = _doctor_do_instagram(monkeypatch, {"error": {
        "message": "(#200) Requires instagram_content_publish permission"}}, status=400)
    assert cli.doctor(cfg) == 1
    out = capsys.readouterr().out
    assert "❌ Instagram" in out and "✅ Instagram" not in out
    assert "instagram_content_publish" in out
    # E o que fazer: o fallback manual que o config.yaml já descreve.
    assert "story_dispatch" in out


def test_doctor_mostra_cota_zerada(monkeypatch, capsys):
    """0 é número: um `if usage` engoliria a conta que ainda não publicou hoje."""
    cfg, _ = _doctor_do_instagram(monkeypatch, {"data": [{
        "config": {"quota_total": 100, "quota_duration": 86400}, "quota_usage": 0}]})
    assert cli.doctor(cfg) == 0
    assert "0 de 100 na cota de 24 h" in capsys.readouterr().out


def test_doctor_le_a_cota_sem_o_envelope_data(monkeypatch, capsys):
    """A rota foi medida ao vivo devolvendo `{"data": [{...}]}`; o objeto solto
    é a outra forma plausível e vale o mesmo."""
    cfg, _ = _doctor_do_instagram(monkeypatch, {
        "config": {"quota_total": 100, "quota_duration": 86400}, "quota_usage": 1})
    assert cli.doctor(cfg) == 0
    assert "1 de 100 na cota de 24 h" in capsys.readouterr().out


@pytest.mark.parametrize("resposta", [
    {"data": []}, {"data": "?"}, {}, [], {"data": [{"config": {}}]},
    {"data": [{"config": {"quota_total": "cem"}, "quota_usage": None}]}])
def test_doctor_tolera_forma_estranha_da_cota(monkeypatch, capsys, resposta):
    """A permissão é o que o HTTP 200 prova; a cota é o extra. Forma que o
    doctor não conhece vira "cota não informada" — nunca uma exceção, e nunca
    um ❌ que faria o dono desligar um canal que está funcionando."""
    cfg, _ = _doctor_do_instagram(monkeypatch, resposta)
    assert cli.doctor(cfg) == 0
    out = capsys.readouterr().out
    assert "✅ Instagram" in out and "publicação liberada" in out
    assert "cota não informada" in out


def test_doctor_com_a_graph_api_fora_do_ar_nao_levanta(monkeypatch, capsys):
    import httpx
    cfg, _ = _doctor_do_instagram(monkeypatch, {})

    def explode(url, params=None, timeout=None):
        raise httpx.ConnectError("sem rede")

    monkeypatch.setattr(cli.httpx, "get", explode)
    assert cli.doctor(cfg) == 1
    assert "❌ Instagram" in capsys.readouterr().out


def test_doctor_sem_instagram_configurado_continua_so_avisando(monkeypatch, capsys):
    """Comportamento preservado: sem IG_USER_ID/IG_ACCESS_TOKEN o doctor avisa
    e NÃO falha — quem não configurou o Instagram não perdeu permissão nenhuma."""
    cfg = _doctor_base(monkeypatch)
    monkeypatch.setattr(cli, "send_text", lambda *a, **k: True)
    assert cli.doctor(cfg) == 0
    assert "ℹ️ Instagram: não configurado" in capsys.readouterr().out


def test_doctor_diz_quando_o_story_link_esta_desarmado_hoje(monkeypatch, tmp_path, capsys):
    """Rodada de correção da 5F: o desarme dura o dia e vive no banco do
    `afiliado stories`. Sem isto, um dia inteiro sem story parecia "não havia
    oferta boa" — e o único lugar que dizia a verdade era o resumo de operações
    daquele run, que a essa altura já rolou para cima no chat."""
    from afiliado.channels import instagram_story_link as mod
    from afiliado.config import load_config
    from afiliado.state import StateDB

    _env_do_story_link(monkeypatch)
    cfg = load_config(_config_com_story_link(tmp_path))
    db = StateDB(tmp_path / "stories.db")
    db.set_day_flag(mod.CHAVE_DESARMADO, mod.AVISO_SESSAO)
    db.close()

    assert cli._doctor_story_link(cfg) is False
    out = capsys.readouterr().out
    assert "DESARMADO hoje" in out and mod.AVISO_SESSAO in out
    assert "afiliado ig-login" in out


def test_doctor_diz_quando_o_story_link_esta_armado(monkeypatch, tmp_path, capsys):
    from afiliado.config import load_config

    _env_do_story_link(monkeypatch)
    cfg = load_config(_config_com_story_link(tmp_path))
    assert cli._doctor_story_link(cfg) is True
    assert "armado hoje" in capsys.readouterr().out


# --- Fase 5I (T4): o doctor passa a enxergar o agendador ----------------------
#
# Foi exatamente esta a falha que ficou invisível na 5G: nada no projeto sabia
# dizer "ninguém está me chamando". O `doctor` agora confere se as duas tarefas
# do Agendador de Tarefas do Windows existem e estão habilitadas.
#
# Nenhum teste consulta o Agendador de verdade: a consulta é INJETADA, como o
# projeto já faz com o transporte HTTP. (O `conftest.py` neutraliza a consulta
# real em toda a suíte, para nenhum teste esquecido tocar a máquina.)


def test_doctor_acusa_a_tarefa_agendada_que_nao_existe(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_no_windows", lambda: True)
    assert cli._doctor_agendador(consulta=lambda nome: "") is False
    out = capsys.readouterr().out
    for nome in cli.TAREFAS_DA_PRODUCAO:
        assert f"❌ Agendador: a tarefa {nome} não existe" in out
    assert cli.SCRIPT_DO_AGENDADOR in out
    assert "docs/runbooks/producao-windows.md" in out


def test_doctor_acusa_a_tarefa_agendada_desabilitada(monkeypatch, capsys):
    """Tarefa desabilitada é pior do que ausente: ela aparece na lista do
    Agendador e não roda. O veredito é o mesmo (❌) e a causa é nomeada."""
    monkeypatch.setattr(cli, "_no_windows", lambda: True)
    assert cli._doctor_agendador(consulta=lambda nome: "Disabled") is False
    assert "DESABILITADA" in capsys.readouterr().out


def test_doctor_aprova_as_duas_tarefas_prontas(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_no_windows", lambda: True)
    assert cli._doctor_agendador(consulta=lambda nome: "Ready") is True
    out = capsys.readouterr().out
    for nome in cli.TAREFAS_DA_PRODUCAO:
        assert f"✅ Agendador: {nome}" in out


def test_doctor_aceita_a_tarefa_em_execucao(monkeypatch):
    """`Running` é uma tarefa saudável no meio de um run — não pode virar ❌."""
    monkeypatch.setattr(cli, "_no_windows", lambda: True)
    assert cli._doctor_agendador(consulta=lambda nome: "Running") is True


def test_doctor_fora_do_windows_pula_o_item_sem_falhar(monkeypatch, capsys):
    """A produção é o Agendador do Windows, mas a suíte roda no Linux do CI e
    o projeto ainda pode rodar na VPS. Fora do Windows o item é PULADO — não
    invente dependência de plataforma."""
    monkeypatch.setattr(cli, "_no_windows", lambda: False)

    def nao_deveria(nome):
        raise AssertionError("consultou o agendador fora do Windows")

    assert cli._doctor_agendador(consulta=nao_deveria) is True
    assert "ℹ️ Agendador" in capsys.readouterr().out


def test_doctor_nao_derruba_o_diagnostico_quando_a_consulta_falha(monkeypatch, capsys):
    """Consulta que estourou não é prova de tarefa ausente — e mandar o dono
    recriar as tarefas por causa de um erro do PowerShell é pior que calar."""
    monkeypatch.setattr(cli, "_no_windows", lambda: True)

    def explode(nome):
        raise OSError("powershell sumiu")

    assert cli._doctor_agendador(consulta=explode) is True
    assert "⚠️ Agendador" in capsys.readouterr().out


def test_o_doctor_reprova_quando_o_agendador_esta_vazio(monkeypatch, capsys):
    """E o item está LIGADO no `doctor` de verdade, não só solto no módulo."""
    cfg = _doctor_base(monkeypatch)
    monkeypatch.setattr(cli, "send_text", lambda *a, **k: True)
    monkeypatch.setattr(cli, "_no_windows", lambda: True)
    monkeypatch.setattr(cli, "estado_da_tarefa", lambda nome: "")
    assert cli.doctor(cfg) == 1
    assert "❌ Agendador" in capsys.readouterr().out

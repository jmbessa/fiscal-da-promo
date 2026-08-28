"""Fase 5D — comando `afiliado feed`.

Duas peças, dois destinos: o TERMÔMETRO monta o carrossel do dia e publica; o
FLAGRANTE gera o gráfico e NÃO publica — vai ao chat de operações esperar o
"ok" do dono, porque nomear um vendedor específico é risco jurídico e isso não
se automatiza (`docs/feed.md`).

Nenhum teste toca a rede: as imagens dos produtos vêm de `httpx.MockTransport`
e o cliente HTTP das artes é injetado por `cli._cliente_http`.
"""

import io
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from PIL import Image

from afiliado import cli, creative, state
from afiliado.state import StateDB
from tests.test_models import make_offer

BRT = timezone(timedelta(hours=-3))


@pytest.fixture(autouse=True)
def relogio_da_operacao(monkeypatch):
    """Meio-dia de um dia útil, no fuso da operação. O ritmo da 5A dá orçamento
    0 fora da janela de 08:00-23:15, e a suíte não pode depender da hora em que
    o CI roda."""
    monkeypatch.setattr(
        state, "_now",
        lambda: datetime(2026, 8, 27, 12, 0, tzinfo=BRT).astimezone(timezone.utc))


def _product_png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (600, 600), (120, 40, 200)).save(buf, "PNG")
    return buf.getvalue()


class _Fonte:
    """Fonte de teste: devolve as ofertas dadas e não mexe em preço."""
    observes_price_on_discovery = True

    def __init__(self, name: str, offers: list):
        self.name = name
        self._offers = offers

    def fetch_offers(self, cfg):
        return list(self._offers)

    def refresh_price(self, offer):
        return offer


def _cfg(tmp_path, extra: str = "") -> str:
    base = (open("config.yaml", encoding="utf-8").read()
            .replace("data/state.db", str(tmp_path / "s.db").replace("\\", "/"))
            .replace("data/watchlist.json",
                     str(tmp_path / "sem-watchlist.json").replace("\\", "/")))
    caminho = tmp_path / "config.yaml"
    caminho.write_text(base + extra, encoding="utf-8")
    return str(caminho)


def _ofertas(n: int = 3) -> list:
    """Ofertas que passam nos portões do config real: acima de R$ 20, dentro do
    allowlist de categoria e acima do piso de EV. Todas em modo B (o preço de
    hoje não está abaixo do p25) — nenhuma "passa" na capa."""
    return [make_offer(item_id=f"i{k}", title=f"Produto de Teste {k}",
                       category="100630", price_current_cents=2490 + k,
                       price_ref_cents=2600, price_p25_cents=2400,
                       price_window_days=90, sales=3000 + k, rating=4.8,
                       commission_pct=12.0)
            for k in range(1, n + 1)]


@pytest.fixture
def rede(monkeypatch):
    """Todo tráfego HTTP do comando passa por aqui — e nada sai da máquina."""
    vistos = []

    def handler(request):
        vistos.append(f"{request.url.host}{request.url.path}")
        if request.url.host in ("cf.shopee.com.br", "http2.mlstatic.com"):
            return httpx.Response(200, content=_product_png(),
                                  headers={"content-type": "image/png"})
        return httpx.Response(404)

    monkeypatch.setattr(
        cli, "_cliente_http",
        lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    return vistos


@pytest.fixture
def previews(tmp_path, monkeypatch):
    destino = tmp_path / "previews"
    monkeypatch.setattr(cli, "PREVIEWS_DIR", destino)
    return destino


def _fontes(monkeypatch, offers, nome: str = "shopee"):
    monkeypatch.setattr(cli, "_build_sources",
                        lambda cfg, db=None: ([_Fonte(nome, offers)], []))


# --- Teste obrigatório 7: --dry-run não escreve nem publica -------------------

def test_feed_dry_run_nao_escreve_no_banco_nem_chama_a_graph_api(
        tmp_path, monkeypatch, rede, previews, capsys):
    _fontes(monkeypatch, _ofertas(3))
    cfg_file = _cfg(tmp_path)
    assert cli.main(["feed", "--dry-run", "--config", cfg_file]) == 0

    assert not any("graph." in url for url in rede)
    assert not any("api.telegram.org" in url for url in rede)

    db = StateDB(tmp_path / "s.db")
    assert db.conn.execute("SELECT COUNT(*) FROM posted").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM price_log").fetchone()[0] == 0
    db.close()

    # capa + 3 ofertas + fecho, gravados em .claude/previews/
    pngs = sorted(previews.glob("*.png"))
    assert len(pngs) == 5
    for p in pngs:
        assert Image.open(p).size == creative.CARROSSEL_SIZE
    saida = capsys.readouterr().out
    assert str(pngs[0]) in saida


def test_feed_dry_run_imprime_a_legenda_com_nome_completo_e_janela(
        tmp_path, monkeypatch, rede, previews, capsys):
    _fontes(monkeypatch, _ofertas(2))
    assert cli.main(["feed", "--dry-run", "--config", _cfg(tmp_path)]) == 0
    saida = capsys.readouterr().out
    assert "Produto de Teste 1" in saida
    assert "Preço verificado nos últimos 90 dias." in saida
    assert creative.ASSINATURA in saida
    for isca in ("comenta", "curte", "compartilha", "marque"):
        assert isca not in saida.lower()


# --- Teste obrigatório 6: flagrante NÃO publica -------------------------------

def _oferta_inflada(**kw):
    base = dict(item_id="inflado", title="Creatina Monohidratada 300g",
                category="100001", price_original_cents=6890,
                price_current_cents=2600, price_ref_cents=2600,
                price_p25_cents=2500, price_window_days=90)
    base.update(kw)
    return make_offer(**base)


def _grava_pico(caminho, item_id="inflado"):
    db = StateDB(caminho)
    hoje = db.local_today()
    precos = [2600] * 90
    precos[60] = 6890
    inicio = hoje - timedelta(days=89)
    for i, cents in enumerate(precos):
        db.record_price("shopee", item_id, cents,
                        day=(inicio + timedelta(days=i)).isoformat())
    db.close()


def test_feed_flagrante_nao_publica_despacha_ao_ops(
        tmp_path, monkeypatch, rede, capsys):
    _grava_pico(tmp_path / "s.db")
    _fontes(monkeypatch, [_oferta_inflada()])
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_OPS_CHAT_ID", "999")
    despachos = []

    def fake_photo(token, chat_id, data, caption="", **kw):
        despachos.append((token, chat_id, data, caption))
        return {"ok": True, "result": {"message_id": 7}}

    monkeypatch.setattr(cli, "send_photo_bytes", fake_photo)
    monkeypatch.setattr(cli, "send_text", lambda *a, **k: True)

    assert cli.main(["feed", "--tipo", "flagrante", "--config", _cfg(tmp_path)]) == 0

    # NÃO publicou: nenhuma chamada à Graph API.
    assert not any("graph." in url for url in rede)
    assert len(despachos) == 1
    token, chat_id, png, legenda = despachos[0]
    assert (token, chat_id) == ("tok", "999")
    assert Image.open(io.BytesIO(png)).size == creative.GRAFICO_SIZE
    assert "Creatina Monohidratada 300g" in legenda
    assert "aprov" in legenda.lower()          # o dono precisa aprovar
    assert "62" in legenda                      # o desconto que o vendedor alega

    # E nada foi gravado como publicação.
    db = StateDB(tmp_path / "s.db")
    assert db.conn.execute("SELECT COUNT(*) FROM posted").fetchone()[0] == 0
    db.close()


def test_feed_flagrante_dry_run_grava_o_grafico_e_nao_despacha(
        tmp_path, monkeypatch, rede, previews, capsys):
    _grava_pico(tmp_path / "s.db")
    _fontes(monkeypatch, [_oferta_inflada()])
    chamado = []
    monkeypatch.setattr(cli, "send_photo_bytes",
                        lambda *a, **k: chamado.append(a) or {"ok": True})

    assert cli.main(["feed", "--tipo", "flagrante", "--dry-run",
                     "--config", _cfg(tmp_path)]) == 0
    assert chamado == []
    pngs = list(previews.glob("*.png"))
    assert len(pngs) == 1
    assert Image.open(pngs[0]).size == creative.GRAFICO_SIZE


def test_serie_do_grafico_termina_no_preco_de_hoje():
    """O último ponto do gráfico é o preço que a legenda anuncia. O price_log
    pode não ter a observação de hoje (em dry-run nada é gravado), e um
    gráfico que contradiz a própria legenda é pior que nenhum gráfico."""
    from datetime import date

    hoje = date(2026, 8, 27)
    historico = [(hoje - timedelta(days=2), 2600), (hoje - timedelta(days=1), 2760)]
    offer = _oferta_inflada(price_current_cents=2600)
    assert cli.serie_ate_hoje(historico, offer, hoje) == historico + [(hoje, 2600)]

    # Observação de hoje já no log: o preço vivo SUBSTITUI, não duplica o dia.
    com_hoje = historico + [(hoje, 2760)]
    assert cli.serie_ate_hoje(com_hoje, offer, hoje) == historico + [(hoje, 2600)]

    # Sem preço vivo, a série fica como está — nada é inventado.
    sem_preco = make_offer(item_id="x", price_current_cents=0)
    assert cli.serie_ate_hoje(historico, sem_preco, hoje) == historico


def test_feed_flagrante_grafico_e_legenda_dizem_o_mesmo_preco(
        tmp_path, monkeypatch, rede, previews, capsys):
    from afiliado import creative as _creative

    _grava_pico(tmp_path / "s.db")
    _fontes(monkeypatch, [_oferta_inflada(price_current_cents=2300)])
    visto = {}
    real = _creative.render_grafico_preco

    def espia(offer, historico, verdict, **kw):
        visto["serie"] = historico
        return real(offer, historico, verdict, **kw)

    monkeypatch.setattr(cli.creative, "render_grafico_preco", espia)
    assert cli.main(["feed", "--tipo", "flagrante", "--dry-run",
                     "--config", _cfg(tmp_path)]) == 0
    assert visto["serie"][-1][1] == 2300
    assert "hoje: R$ 23,00" in capsys.readouterr().out


def test_feed_flagrante_sem_flagrante_termina_em_silencio(
        tmp_path, monkeypatch, rede, capsys):
    _fontes(monkeypatch, _ofertas(2))          # nenhuma alega desconto de vendedor
    monkeypatch.setattr(cli, "send_photo_bytes",
                        lambda *a, **k: pytest.fail("não devia despachar"))
    assert cli.main(["feed", "--tipo", "flagrante", "--dry-run",
                     "--config", _cfg(tmp_path)]) == 0
    assert "nenhum flagrante" in capsys.readouterr().out.lower()


# --- Publicação de verdade ----------------------------------------------------

def _cfg_com_canal(tmp_path, max_per_day: int = 1) -> str:
    return _cfg(tmp_path,
                "\nchannels:\n"
                "  telegram: false\n"
                "  instagram_feed: true\n"
                f"  instagram_carrossel:\n    enabled: true\n"
                f"    max_per_day: {max_per_day}\n")


def _liga_instagram(monkeypatch):
    for nome, valor in (("IG_USER_ID", "IGUSER"), ("IG_ACCESS_TOKEN", "IGTOKEN"),
                        ("TELEGRAM_BOT_TOKEN", "tok"),
                        ("TELEGRAM_OPS_CHAT_ID", "999")):
        monkeypatch.setenv(nome, valor)


def test_feed_termometro_publica_um_carrossel_e_conta_como_um_post(
        tmp_path, monkeypatch, capsys):
    _fontes(monkeypatch, _ofertas(3))
    _liga_instagram(monkeypatch)
    monkeypatch.setattr(cli, "send_text", lambda *a, **k: True)
    publicados = []

    class _Canal:
        name = "instagram_feed"

        def publish_carrossel(self, imagens, caption):
            from afiliado.channels.base import PublishResult
            publicados.append((imagens, caption))
            return PublishResult(True, "post123")

    monkeypatch.setattr(cli, "_canal_do_carrossel", lambda cfg, avisos: _Canal())
    monkeypatch.setattr(
        cli, "_cliente_http",
        lambda: httpx.Client(transport=httpx.MockTransport(
            lambda r: httpx.Response(200, content=_product_png(),
                                     headers={"content-type": "image/png"}))))

    assert cli.main(["feed", "--config", _cfg_com_canal(tmp_path)]) == 0
    assert len(publicados) == 1
    imagens, caption = publicados[0]
    assert len(imagens) == 5
    assert "Produto de Teste 1" in caption

    db = StateDB(tmp_path / "s.db")
    # UM post no canal que conta para o teto...
    assert db.count_posts_today(cli.CANAL_CARROSSEL) == 1
    # ...e as três ofertas registradas para o dedupe não repetir amanhã.
    assert db.conn.execute(
        "SELECT COUNT(*) FROM posted WHERE channel=?",
        (cli.CANAL_CARROSSEL_ITEM,)).fetchone()[0] == 3
    db.close()


def test_feed_termometro_respeita_o_teto_diario(tmp_path, monkeypatch, capsys):
    _fontes(monkeypatch, _ofertas(3))
    _liga_instagram(monkeypatch)
    monkeypatch.setattr(cli, "send_text", lambda *a, **k: True)
    monkeypatch.setattr(cli, "_canal_do_carrossel",
                        lambda cfg, avisos: pytest.fail("não devia montar canal"))

    db = StateDB(tmp_path / "s.db")
    from tests.test_state import make_post
    db.record_post(make_post(item_id="ontem"), cli.CANAL_CARROSSEL, "1")
    db.close()

    assert cli.main(["feed", "--config", _cfg_com_canal(tmp_path, 1)]) == 0
    assert "teto" in capsys.readouterr().out.lower()


def test_feed_termometro_falha_de_publicacao_sai_com_erro(tmp_path, monkeypatch, capsys):
    _fontes(monkeypatch, _ofertas(2))
    _liga_instagram(monkeypatch)
    monkeypatch.setattr(cli, "send_text", lambda *a, **k: True)

    class _Canal:
        name = "instagram_feed"

        def publish_carrossel(self, imagens, caption):
            from afiliado.channels.base import PublishResult
            return PublishResult(False, error="children inválido")

    monkeypatch.setattr(cli, "_canal_do_carrossel", lambda cfg, avisos: _Canal())
    monkeypatch.setattr(
        cli, "_cliente_http",
        lambda: httpx.Client(transport=httpx.MockTransport(
            lambda r: httpx.Response(200, content=_product_png(),
                                     headers={"content-type": "image/png"}))))

    assert cli.main(["feed", "--config", _cfg_com_canal(tmp_path)]) == 1
    saida = capsys.readouterr().out
    assert "children inválido" in saida

    db = StateDB(tmp_path / "s.db")
    assert db.count_posts_today(cli.CANAL_CARROSSEL) == 0
    db.close()


def test_feed_sem_candidata_nao_quebra(tmp_path, monkeypatch, rede, previews, capsys):
    _fontes(monkeypatch, [])
    assert cli.main(["feed", "--dry-run", "--config", _cfg(tmp_path)]) == 0
    assert "candidata" in capsys.readouterr().out.lower()
    assert list(previews.glob("*.png")) == []


def test_feed_capa_conta_quantas_passaram(tmp_path, monkeypatch, rede, previews, capsys):
    """A capa vende o conceito, e o número dela é o que a RÉGUA diz — não um
    número escolhido pelo marketing."""
    aprovada = make_offer(item_id="ok", title="Aprovada de Verdade",
                          category="100630", price_current_cents=2000,
                          price_ref_cents=2600, price_p25_cents=2400,
                          price_window_days=90, sales=9000, rating=4.9,
                          commission_pct=12.0)
    _fontes(monkeypatch, _ofertas(3) + [aprovada])
    assert cli.main(["feed", "--dry-run", "--config", _cfg(tmp_path)]) == 0
    # 4 ofertas, 1 em modo A: a variante "3 ofertas, 1 é real" da pesquisa.
    assert "4 OFERTAS. 1 É REAL." in capsys.readouterr().out


def test_feed_capa_quando_nenhuma_passa(tmp_path, monkeypatch, rede, previews, capsys):
    _fontes(monkeypatch, _ofertas(3))
    assert cli.main(["feed", "--dry-run", "--config", _cfg(tmp_path)]) == 0
    assert "NENHUMA DAS 3 PASSOU." in capsys.readouterr().out

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
                       commission_pct=12.0,
                       image_url=f"https://cf.shopee.com.br/file/i{k}.jpg")
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
    # F2: o estoque de candidatas é gravado no run de verdade, nunca aqui.
    assert db.conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 0
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
    db = StateDB(caminho, timezone="America/Sao_Paulo")
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


# --- Rodada de fechamento (F5): o flagrante despachado não volta amanhã -------

def _despacha_flagrante(monkeypatch, cfg_file, ofertas):
    """Roda o comando de verdade capturando o que foi ao chat de ops."""
    _fontes(monkeypatch, ofertas)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_OPS_CHAT_ID", "999")
    despachos = []
    monkeypatch.setattr(
        cli, "send_photo_bytes",
        lambda t, c, data, caption="", **kw: despachos.append(caption) or {"ok": True})
    codigo = cli.main(["feed", "--tipo", "flagrante", "--config", cfg_file])
    return codigo, despachos


def test_flagrante_despachado_nao_se_repete_dentro_da_janela(
        tmp_path, monkeypatch, rede, capsys):
    """Ele não registrava nada: agendado todo dia, o mesmo produto voltaria
    toda manhã ao chat de operações. A marca vai em `day_flags` e NÃO em
    `posted` — gravar como publicação bloquearia o produto no Telegram por 30
    dias, que é efeito colateral de outra decisão."""
    _grava_pico(tmp_path / "s.db")
    cfg_file = _cfg(tmp_path)

    codigo, despachos = _despacha_flagrante(monkeypatch, cfg_file, [_oferta_inflada()])
    assert codigo == 0 and len(despachos) == 1

    db = StateDB(tmp_path / "s.db")
    assert db.conn.execute("SELECT COUNT(*) FROM posted").fetchone()[0] == 0
    assert db.day_flag_recente(cli.chave_do_flagrante(_oferta_inflada()),
                               cli.FLAGRANTE_DEDUPE_DAYS)
    db.close()

    # Amanhã (mesma janela): nada sai, e o comando não vira erro.
    codigo, despachos = _despacha_flagrante(monkeypatch, cfg_file, [_oferta_inflada()])
    assert codigo == 0 and despachos == []
    assert "despachado" in capsys.readouterr().out.lower()


def test_passada_a_janela_o_flagrante_pode_voltar(tmp_path, monkeypatch, rede, capsys):
    _grava_pico(tmp_path / "s.db")
    cfg_file = _cfg(tmp_path)
    codigo, despachos = _despacha_flagrante(monkeypatch, cfg_file, [_oferta_inflada()])
    assert codigo == 0 and len(despachos) == 1

    depois = datetime(2026, 8, 27, 12, 0, tzinfo=BRT) + timedelta(
        days=cli.FLAGRANTE_DEDUPE_DAYS)
    monkeypatch.setattr(state, "_now", lambda: depois.astimezone(timezone.utc))
    _grava_pico(tmp_path / "s.db")           # histórico fresco na nova janela
    codigo, despachos = _despacha_flagrante(monkeypatch, cfg_file, [_oferta_inflada()])
    assert codigo == 0 and len(despachos) == 1


def test_a_janela_nao_engole_o_segundo_pior_flagrante(
        tmp_path, monkeypatch, rede, capsys):
    """O dedupe é por PRODUTO, não "um flagrante por semana": bloqueado o de
    maior gravidade, o comando desce para o próximo — senão uma semana inteira
    de denúncias morreria por causa de um item."""
    _grava_pico(tmp_path / "s.db", item_id="inflado")
    _grava_pico(tmp_path / "s.db", item_id="outro")
    cfg_file = _cfg(tmp_path)
    ofertas = [_oferta_inflada(),
               _oferta_inflada(item_id="outro", title="Whey Protein 900g",
                               price_original_cents=5900)]

    codigo, primeiro = _despacha_flagrante(monkeypatch, cfg_file, ofertas)
    assert codigo == 0 and len(primeiro) == 1
    codigo, segundo = _despacha_flagrante(monkeypatch, cfg_file, ofertas)
    assert codigo == 0 and len(segundo) == 1
    assert segundo[0] != primeiro[0]

    codigo, terceiro = _despacha_flagrante(monkeypatch, cfg_file, ofertas)
    assert codigo == 0 and terceiro == []


def test_flagrante_em_dry_run_nao_marca_nada(tmp_path, monkeypatch, rede, previews):
    """A10: `--dry-run` não escreve — nem a marca do dedupe. Se escrevesse,
    olhar o preview de manhã calaria a peça de verdade da tarde."""
    _grava_pico(tmp_path / "s.db")
    _fontes(monkeypatch, [_oferta_inflada()])
    assert cli.main(["feed", "--tipo", "flagrante", "--dry-run",
                     "--config", _cfg(tmp_path)]) == 0

    db = StateDB(tmp_path / "s.db")
    assert db.conn.execute("SELECT COUNT(*) FROM day_flags").fetchone()[0] == 0
    db.close()


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


# --- Rodada de fechamento (F2): a fatia descoberta não é jogada fora ----------

def test_feed_alimenta_o_estoque_de_candidatas(tmp_path, monkeypatch, rede, capsys):
    """O comando LÊ o estoque e paga uma fatia nova de descoberta (8 chamadas à
    Shopee) — e a fatia sumia quando o processo morria. Ela é gravada, como o
    `pipeline.run` faz: a descoberta é do projeto, não do comando."""
    _fontes(monkeypatch, _ofertas(3))
    monkeypatch.setattr(cli, "send_photo_bytes", lambda *a, **k: {"ok": True})
    assert cli.main(["feed", "--tipo", "flagrante", "--config", _cfg(tmp_path)]) == 0

    db = StateDB(tmp_path / "s.db")
    assert {r[0] for r in db.conn.execute(
        "SELECT item_id FROM candidates WHERE source='shopee'")} == {"i1", "i2", "i3"}
    db.close()


def test_feed_nao_grava_estoque_de_fonte_sem_validade_de_candidata(
        tmp_path, monkeypatch, rede, capsys):
    """`<fonte>.candidate_max_age_days` ausente/0 = a fonte não usa o estoque
    (o pool do ML é relido inteiro a cada run). Gravar as ofertas dela no
    `candidates` só encheria o `state.db` que o Actions commita."""
    _fontes(monkeypatch, _ofertas(2), nome="meli")
    monkeypatch.setattr(cli, "send_photo_bytes", lambda *a, **k: {"ok": True})
    assert cli.main(["feed", "--tipo", "flagrante", "--config", _cfg(tmp_path)]) == 0

    db = StateDB(tmp_path / "s.db")
    assert db.conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 0
    db.close()


# --- Rodada de fechamento (F3): o aviso do canal chega ao ops -----------------

class _CanalQueAvisa:
    """O canal do carrossel acumula avisos em `self.warnings` (o "polling cego"
    da 5E, quando a Meta não devolve `status_code` do container). Quem os drena
    é o laço do `pipeline.run` — que este comando não usa."""
    name = "instagram_feed"
    AVISO = "⚠️ instagram_carrossel: polling cego (a Meta não devolveu status)"

    def __init__(self, ok: bool = True):
        self.warnings: list[str] = []
        self.publicados: list[tuple[list[bytes], str]] = []
        self._ok = ok

    def publish_carrossel(self, imagens, caption):
        from afiliado.channels.base import PublishResult
        self.warnings.append(self.AVISO)
        self.publicados.append((imagens, caption))
        return (PublishResult(True, "post123") if self._ok
                else PublishResult(False, error="children inválido"))


def _canal_e_ops(monkeypatch, canal):
    ops: list[str] = []
    monkeypatch.setattr(cli, "_canal_do_carrossel", lambda cfg, avisos: canal)
    monkeypatch.setattr(cli, "send_text",
                        lambda token, chat, texto, **kw: ops.append(texto) or True)
    monkeypatch.setattr(
        cli, "_cliente_http",
        lambda: httpx.Client(transport=httpx.MockTransport(
            lambda r: httpx.Response(200, content=_product_png(),
                                     headers={"content-type": "image/png"}))))
    return ops


def test_o_aviso_do_canal_do_carrossel_chega_ao_chat_de_operacoes(
        tmp_path, monkeypatch, capsys):
    _fontes(monkeypatch, _ofertas(3))
    _liga_instagram(monkeypatch)
    canal = _CanalQueAvisa()
    ops = _canal_e_ops(monkeypatch, canal)

    assert cli.main(["feed", "--config", _cfg_com_canal(tmp_path)]) == 0
    assert any(_CanalQueAvisa.AVISO in texto for texto in ops)
    assert canal.warnings == []            # drenado, não copiado


def test_a_legenda_e_a_capa_so_falam_das_ofertas_que_entraram(
        tmp_path, monkeypatch, capsys):
    """F4 no comando: a foto de um produto não baixa, ele é pulado — e a capa
    ("N OFERTAS") e a legenda (um item por linha) são montadas DEPOIS disso.
    Uma legenda que lista um produto que o álbum não tem é a peça mentindo
    sobre si mesma, e a legenda é pública."""
    _fontes(monkeypatch, _ofertas(3))
    _liga_instagram(monkeypatch)
    canal = _CanalQueAvisa()
    ops = _canal_e_ops(monkeypatch, canal)
    monkeypatch.setattr(
        cli, "_cliente_http",
        lambda: httpx.Client(transport=httpx.MockTransport(
            lambda r: httpx.Response(404) if "/i2." in r.url.path
            else httpx.Response(200, content=_product_png(),
                                headers={"content-type": "image/png"}))))

    assert cli.main(["feed", "--config", _cfg_com_canal(tmp_path)]) == 0
    imagens, legenda = canal.publicados[0]
    assert len(imagens) == 4                    # capa + 2 ofertas + fecho
    assert "NENHUMA DAS 2 PASSOU." in legenda
    assert "Produto de Teste 2" not in legenda
    assert "Produto de Teste 1" in legenda and "Produto de Teste 3" in legenda
    # E o dono fica sabendo do que ficou de fora.
    assert any("i2" in texto for texto in ops)

    db = StateDB(tmp_path / "s.db")
    assert db.conn.execute(
        "SELECT COUNT(*) FROM posted WHERE channel=?",
        (cli.CANAL_CARROSSEL_ITEM,)).fetchone()[0] == 2
    db.close()


def test_carrossel_que_nem_chega_a_existir_avisa_o_ops(tmp_path, monkeypatch, capsys):
    """Com as fotos todas quebradas o post não sai — e o passo do Actions é
    `continue-on-error`, então o job segue VERDE. Se isto só fosse ao log, o
    feed podia parar por uma semana sem que ninguém notasse."""
    _fontes(monkeypatch, _ofertas(3))
    _liga_instagram(monkeypatch)
    ops = _canal_e_ops(monkeypatch, _CanalQueAvisa())
    monkeypatch.setattr(
        cli, "_cliente_http",
        lambda: httpx.Client(transport=httpx.MockTransport(
            lambda r: httpx.Response(404))))

    assert cli.main(["feed", "--config", _cfg_com_canal(tmp_path)]) == 1
    assert any("não foi gerado" in texto for texto in ops)


def test_o_aviso_do_canal_sai_mesmo_quando_a_publicacao_falha(
        tmp_path, monkeypatch, capsys):
    """O aviso nasce DURANTE a publicação: se ele só saísse no caminho feliz,
    o run que mais precisa de diagnóstico seria justamente o mudo."""
    _fontes(monkeypatch, _ofertas(3))
    _liga_instagram(monkeypatch)
    ops = _canal_e_ops(monkeypatch, _CanalQueAvisa(ok=False))

    assert cli.main(["feed", "--config", _cfg_com_canal(tmp_path)]) == 1
    assert any(_CanalQueAvisa.AVISO in texto for texto in ops)
    assert any("children inválido" in texto for texto in ops)

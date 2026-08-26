from datetime import date, timedelta

from afiliado import llm, pipeline
from afiliado.channels.base import PublishResult
from afiliado.errors import SourceError, ValidationError
from afiliado.models import CopyParts, Post
from afiliado.state import StateDB
from afiliado.watchlist import Watchlist
from tests.test_models import make_offer

CFG = {
    "selection": {"posts_per_run": 2, "price_min_brl": 20,
                  "price_max_brl": 1000, "dedupe_days": 30, "category_ids": [],
                  "max_above_ref": 1.00, "require_price_ref": False,
                  "min_real_discount_pct": 10, "ref_window_days": 90,
                  "ref_min_observations": 5, "seal_tolerance": 1.05,
                  "ev_weights": {"popularity": 0.3, "discount": 0.5}},
    "llm": {"model": "haiku"},
    "copy": {"tone": "pt-BR"},
    "validation": {"allowed_domains": ["shope.ee"]},
}


class FakeSource:
    name = "shopee"

    def __init__(self, offers):
        self._offers = offers

    def fetch_offers(self, cfg):
        return self._offers

    def resolve_affiliate_link(self, offer):
        return "https://shope.ee/ok"


class FakeChannel:
    name = "telegram"

    def __init__(self):
        self.sent = []

    def publish(self, post):
        self.sent.append(post)
        return PublishResult(True, str(len(self.sent)))


class NamedFakeChannel:
    def __init__(self, name, max_per_run=None, always_fail=False):
        self.name = name
        self.sent = []
        self.max_per_run = max_per_run
        self.always_fail = always_fail

    def publish(self, post):
        self.sent.append(post)
        if self.always_fail:
            return PublishResult(False, error=f"{self.name} sempre falha")
        return PublishResult(True, str(len(self.sent)))


def no_network_validator(post, cfg, client=None):
    return None


def test_run_publishes_and_records(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)  # força fallbacks
    db = StateDB(tmp_path / "s.db")
    offers = [make_offer(item_id=str(i)) for i in range(3)]
    ch = FakeChannel()
    summary = pipeline.run(CFG, [FakeSource(offers)], [ch], db,
                           validator=no_network_validator)
    assert len(ch.sent) == 2                       # posts_per_run
    assert len(summary.published) == 2
    assert db.was_posted_recently("shopee", ch.sent[0].offer.item_id, 30)
    db.close()


def test_run_discards_and_promotes_next(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    offers = [make_offer(item_id=str(i)) for i in range(3)]

    def validator(post, cfg, client=None):
        if post.offer.item_id == "0":
            raise ValidationError("link morto")

    ch = FakeChannel()
    summary = pipeline.run(CFG, [FakeSource(offers)], [ch], db, validator=validator)
    assert len(ch.sent) == 2
    assert "0" not in [p.offer.item_id for p in ch.sent]
    assert len(summary.discarded) == 1
    db.close()


def test_dry_run_does_not_publish_nor_record(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    ch = FakeChannel()
    pipeline.run(CFG, [FakeSource([make_offer()])], [ch], db, dry_run=True,
                 validator=no_network_validator)
    assert ch.sent == []
    assert not db.was_posted_recently("shopee", "123456", 30)
    assert "DRY-RUN" in capsys.readouterr().out
    db.close()


def test_summary_text():
    s = pipeline.RunSummary(published=["a"], discarded=["b: x"])
    assert "Publicados (1)" in s.text() and "Descartados (1)" in s.text()


def test_summary_text_includes_warnings():
    s = pipeline.RunSummary(published=["a"], discarded=[], warnings=["⚠️ aviso 1", "⚠️ aviso 2"])
    text = s.text()
    assert "⚠️ aviso 1" in text
    assert "⚠️ aviso 2" in text


def test_run_warns_without_watchlist(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    offers = [make_offer(item_id=str(i)) for i in range(3)]
    ch = FakeChannel()
    summary = pipeline.run(CFG, [FakeSource(offers)], [ch], db,
                           validator=no_network_validator)
    assert "Sem watchlist" in summary.text()
    db.close()


def test_run_stale_watchlist_no_boost_and_warns(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    offers = [
        make_offer(item_id="0", commission_pct=20.0),
        make_offer(item_id="1", commission_pct=12.0),
        make_offer(item_id="2", commission_pct=5.0),
    ]

    db1 = StateDB(tmp_path / "s1.db")
    ch1 = FakeChannel()
    summary_no_wl = pipeline.run(CFG, [FakeSource(offers)], [ch1], db1,
                                 validator=no_network_validator)
    db1.close()

    stale_wl = Watchlist(generated_at=date.today() - timedelta(days=30), valid_days=14,
                         hot_items={"2": 5.0})
    db2 = StateDB(tmp_path / "s2.db")
    ch2 = FakeChannel()
    summary_stale = pipeline.run(CFG, [FakeSource(offers)], [ch2], db2,
                                 validator=no_network_validator, watchlist=stale_wl)
    db2.close()

    assert "Watchlist vencida" in summary_stale.text()
    assert [p.offer.item_id for p in ch1.sent] == [p.offer.item_id for p in ch2.sent]


def test_run_hot_item_jumps_queue(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    offers = [make_offer(item_id=str(i)) for i in range(3)]  # EV igual entre si
    wl = Watchlist(generated_at=date.today(), valid_days=14, hot_items={"2": 2.0})
    ch = FakeChannel()
    pipeline.run(CFG, [FakeSource(offers)], [ch], db,
                validator=no_network_validator, watchlist=wl)
    assert ch.sent[0].offer.item_id == "2"
    db.close()


def test_run_counts_per_offer_with_two_channels(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    offers = [make_offer(item_id=str(i)) for i in range(3)]
    ch_a = NamedFakeChannel("A")
    ch_b = NamedFakeChannel("B")
    summary = pipeline.run(CFG, [FakeSource(offers)], [ch_a, ch_b], db,
                           validator=no_network_validator)
    assert len(summary.published) == 2   # posts_per_run=2, contagem por oferta
    assert len(ch_a.sent) == 2
    assert len(ch_b.sent) == 2
    db.close()


def test_run_respects_channel_max_per_run(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    offers = [make_offer(item_id=str(i)) for i in range(3)]
    ch_a = NamedFakeChannel("A")
    ch_b = NamedFakeChannel("B", max_per_run=1)
    summary = pipeline.run(CFG, [FakeSource(offers)], [ch_a, ch_b], db,
                           validator=no_network_validator)
    assert len(summary.published) == 2
    assert len(ch_a.sent) == 2      # sem limite: recebe todas as ofertas publicadas
    assert len(ch_b.sent) == 1      # max_per_run=1: só a primeira
    db.close()


def test_run_respects_channel_max_per_day(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    offers = [make_offer(item_id=str(i)) for i in range(3)]
    ch_s = NamedFakeChannel("s")
    ch_s.max_per_day = 2
    ch_t = NamedFakeChannel("t")

    ja_postado = Post(offer=make_offer(item_id="ja-postado"),
                      copy=CopyParts(headline="h", description="d", cta="c"),
                      affiliate_link="https://shope.ee/x", message_text="msg")
    db.record_post(ja_postado, channel="s", message_id="x")

    cfg = {**CFG, "selection": {**CFG["selection"], "posts_per_run": 3}}
    summary = pipeline.run(cfg, [FakeSource(offers)], [ch_s, ch_t], db,
                           validator=no_network_validator)
    assert len(ch_s.sent) == 1       # teto diário 2: 1 já feito hoje + 1 neste run
    assert len(ch_t.sent) == 3       # sem teto: recebe todas
    assert len(summary.published) == 3   # ofertas seguem contando via "t"
    assert any("teto diário" in w for w in summary.warnings)
    db.close()


def test_run_refresh_price_failure_discards_and_promotes_next(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    offers = [make_offer(item_id=str(i)) for i in range(3)]

    class FlakySource(FakeSource):
        def refresh_price(self, offer):
            if offer.item_id == "0":
                raise SourceError("preço acima da mínima histórica")
            return offer

    ch = FakeChannel()
    summary = pipeline.run(CFG, [FlakySource(offers)], [ch], db, validator=no_network_validator)
    assert len(ch.sent) == 2               # posts_per_run=2, oferta "0" descartada
    assert "0" not in [p.offer.item_id for p in ch.sent]
    assert len(summary.discarded) == 1
    assert "mínima histórica" in summary.discarded[0]
    db.close()


def test_run_source_without_refresh_price_still_works(tmp_path, monkeypatch):
    # FakeSource (usado no resto deste arquivo) não implementa refresh_price
    # -- getattr(src, "refresh_price", None) deve simplesmente pular a
    # chamada, sem quebrar fontes que não o implementam.
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    assert getattr(FakeSource(offers=[]), "refresh_price", None) is None
    db = StateDB(tmp_path / "s.db")
    offers = [make_offer(item_id=str(i)) for i in range(2)]
    ch = FakeChannel()
    summary = pipeline.run(CFG, [FakeSource(offers)], [ch], db, validator=no_network_validator)
    assert len(summary.published) == 2
    db.close()


def test_run_warns_when_meli_source_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")

    class EmptyMeliSource:
        name = "meli"

        def fetch_offers(self, cfg):
            return []

        def resolve_affiliate_link(self, offer):
            return "x"

    ch = FakeChannel()
    summary = pipeline.run(CFG, [EmptyMeliSource()], [ch], db, validator=no_network_validator)
    assert any("meli" in w and "pool" in w for w in summary.warnings)
    db.close()


def test_run_no_meli_warning_when_meli_source_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    ch = FakeChannel()
    summary = pipeline.run(CFG, [FakeSource([])], [ch], db, validator=no_network_validator)
    assert not any("meli" in w for w in summary.warnings)
    db.close()


def test_run_no_meli_warning_when_meli_source_has_offers(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")

    class NonEmptyMeliSource:
        name = "meli"

        def fetch_offers(self, cfg):
            return [make_offer(item_id="m1", source="meli")]

        def resolve_affiliate_link(self, offer):
            return "https://mercadolivre.com/sec/x"

    ch = FakeChannel()
    summary = pipeline.run(CFG, [NonEmptyMeliSource()], [ch], db, validator=no_network_validator)
    assert not any("meli" in w for w in summary.warnings)
    db.close()


def test_run_offer_counts_when_one_channel_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    offers = [make_offer(item_id=str(i)) for i in range(3)]
    ch_a = NamedFakeChannel("A")
    ch_b = NamedFakeChannel("B", always_fail=True)
    summary = pipeline.run(CFG, [FakeSource(offers)], [ch_a, ch_b], db,
                           validator=no_network_validator)
    assert len(summary.published) == 2      # A publicou; oferta conta como publicada
    assert len(ch_a.sent) == 2
    assert len(ch_b.sent) == 2
    assert len(summary.discarded) == 2      # uma linha de falha de B por oferta
    assert all("B" in d for d in summary.discarded)
    db.close()


# --- Fase 4: régua honesta no pipeline ---------------------------------------

class RefreshingSource(FakeSource):
    """Fonte com refresh_price, para provar que o rótulo é recalculado DEPOIS
    do preço ao vivo (antes ele mostrava o desconto do preço velho)."""

    def __init__(self, offers, novo_preco_cents):
        super().__init__(offers)
        self._novo = novo_preco_cents

    def refresh_price(self, offer):
        import dataclasses
        return dataclasses.replace(offer, price_current_cents=self._novo)


def test_run_rotulo_usa_o_desconto_depois_do_refresh(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    # Referência 100,00; na busca custava 99,00 (1%), no refresh cai para 50,00.
    offer = make_offer(item_id="x", price_ref_cents=10000, price_current_cents=9900)
    ch = FakeChannel()
    summary = pipeline.run(CFG, [RefreshingSource([offer], 5000)], [ch], db,
                           validator=no_network_validator)
    assert summary.published == ["Tênis Nike SB (50% OFF)"]
    db.close()


def test_run_rotulo_sem_desconto_verificado_e_so_o_titulo(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    ch = FakeChannel()
    summary = pipeline.run(CFG, [FakeSource([make_offer(item_id="x")])], [ch], db,
                           validator=no_network_validator)
    assert summary.published == ["Tênis Nike SB"]
    db.close()


def test_run_grava_o_historico_de_precos(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    offers = [make_offer(item_id="a", price_current_cents=2600),
              make_offer(item_id="b", price_current_cents=3390)]
    pipeline.run(CFG, [FakeSource(offers)], [FakeChannel()], db,
                 validator=no_network_validator)
    assert db.price_history("shopee", "a", days=1) == [2600]
    assert db.price_history("shopee", "b", days=1) == [3390]
    db.close()


def test_run_usa_o_historico_como_referencia(tmp_path, monkeypatch):
    # Com histórico suficiente, a mediana vira referência e a oferta que hoje
    # está MAIS CARA que o típico não é publicada.
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    hoje = date.today()
    for i in range(1, 7):
        db.record_price("shopee", "caro", 2600, day=(hoje - timedelta(days=i)).isoformat())
    offers = [make_offer(item_id="caro", price_current_cents=3390),
              make_offer(item_id="normal", price_current_cents=2500)]
    ch = FakeChannel()
    summary = pipeline.run(CFG, [FakeSource(offers)], [ch], db,
                           validator=no_network_validator)
    assert [p.offer.item_id for p in ch.sent] == ["normal"]
    assert len(summary.published) == 1
    db.close()


def test_run_config_zero_chega_ao_build_message(tmp_path, monkeypatch):
    # A11: `min_real_discount_pct: 0` / `seal_tolerance: 0` no config eram
    # trocados pelo default antes de chegar ao texto do post.
    from afiliado import message
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    capturado = {}
    original = message.build_message

    def espiao(offer, copy, link, **kw):
        capturado.update(kw)
        return original(offer, copy, link, **kw)

    monkeypatch.setattr(message, "build_message", espiao)
    cfg = {**CFG, "selection": {**CFG["selection"],
                                "min_real_discount_pct": 0, "seal_tolerance": 0}}
    db = StateDB(tmp_path / "s.db")
    pipeline.run(cfg, [FakeSource([make_offer()])], [FakeChannel()], db,
                 validator=no_network_validator)
    assert capturado["min_real_discount_pct"] == 0
    assert capturado["seal_tolerance"] == 0.0
    db.close()


def test_run_expoe_o_aviso_do_pool_do_meli(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")

    class MeliComAviso(FakeSource):
        name = "meli"
        pool_warning = "2 entrada(s) do pool ignorada(s): price_historic_min_cents ausente"

    summary = pipeline.run(CFG, [MeliComAviso([make_offer(item_id="x", source="meli")])],
                           [FakeChannel()], db, validator=no_network_validator)
    assert any("entrada(s) do pool ignorada(s)" in w for w in summary.warnings)
    db.close()

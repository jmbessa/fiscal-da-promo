from datetime import date, timedelta

from afiliado import llm, pipeline
from afiliado.channels.base import PublishResult
from afiliado.errors import ValidationError
from afiliado.state import StateDB
from afiliado.watchlist import Watchlist
from tests.test_models import make_offer

CFG = {
    "selection": {"posts_per_run": 2, "min_discount_pct": 20, "price_min_brl": 20,
                  "price_max_brl": 1000, "dedupe_days": 30, "category_ids": [],
                  "ev_weights": {"popularity": 0.3}},
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

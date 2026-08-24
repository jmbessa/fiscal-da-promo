from afiliado import llm, pipeline
from afiliado.channels.base import PublishResult
from afiliado.errors import ValidationError
from afiliado.state import StateDB
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

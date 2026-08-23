from afiliado.models import CopyParts, Post
from afiliado.state import StateDB
from tests.test_models import make_offer


def make_post(**offer_kw) -> Post:
    return Post(
        offer=make_offer(**offer_kw),
        copy=CopyParts(headline="h", description="d", cta="c"),
        affiliate_link="https://shope.ee/x",
        message_text="msg",
    )


def test_record_and_dedupe(tmp_path):
    db = StateDB(tmp_path / "state.db")
    assert not db.was_posted_recently("shopee", "123456", days=30)
    db.record_post(make_post(), channel="telegram", message_id="42")
    assert db.was_posted_recently("shopee", "123456", days=30)
    assert not db.was_posted_recently("shopee", "999", days=30)
    db.close()


def test_recent_titles(tmp_path):
    db = StateDB(tmp_path / "state.db")
    db.record_post(make_post(), channel="telegram", message_id="1")
    assert db.recent_titles(days=7) == ["Tênis Nike SB"]
    db.close()


def test_record_run(tmp_path):
    db = StateDB(tmp_path / "state.db")
    db.record_run(published=3, discarded=1, notes="ok")
    db.close()  # sem exceção = schema e insert funcionam

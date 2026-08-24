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


def test_count_posts_today(tmp_path):
    from datetime import date, timedelta

    db = StateDB(tmp_path / "state.db")
    db.record_post(make_post(item_id="1"), channel="a", message_id="1")
    db.record_post(make_post(item_id="2"), channel="a", message_id="2")
    db.record_post(make_post(item_id="3"), channel="b", message_id="3")

    ontem = date.today() - timedelta(days=1)
    db.conn.execute(
        "INSERT OR REPLACE INTO posted VALUES (?,?,?,?,?,?,?)",
        ("shopee", "999", "a", "Tênis de ontem", 1000, "9",
         f"{ontem.isoformat()}T12:00:00"),
    )
    db.conn.commit()

    assert db.count_posts_today("a") == 2
    assert db.count_posts_today("b") == 1
    assert db.count_posts_today("c") == 0
    db.close()

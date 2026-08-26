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


def test_record_price_mantem_o_menor_do_dia(tmp_path):
    # Duas observações no mesmo dia: a referência fica com a MENOR (a mais
    # conservadora — menos desconto alegado depois).
    db = StateDB(tmp_path / "state.db")
    db.record_price("shopee", "123456", 3390, day="2026-08-20")
    db.record_price("shopee", "123456", 2600, day="2026-08-20")
    db.record_price("shopee", "123456", 6890, day="2026-08-20")
    assert db.price_history("shopee", "123456", days=30) == [2600]
    db.close()


def test_record_price_ignora_valores_nao_positivos(tmp_path):
    db = StateDB(tmp_path / "state.db")
    db.record_price("shopee", "123456", 0, day="2026-08-20")
    db.record_price("shopee", "123456", -100, day="2026-08-21")
    assert db.price_history("shopee", "123456", days=3650) == []
    db.close()


def test_price_history_respeita_a_janela_e_ordena_do_mais_recente(tmp_path):
    from datetime import date, timedelta

    db = StateDB(tmp_path / "state.db")
    hoje = date.today()
    for delta, cents in ((0, 3390), (10, 2600), (100, 9999)):
        db.record_price("shopee", "123456", cents,
                        day=(hoje - timedelta(days=delta)).isoformat())
    assert db.price_history("shopee", "123456", days=90) == [3390, 2600]
    assert db.price_history("shopee", "123456", days=365) == [3390, 2600, 9999]
    assert db.price_history("shopee", "outro", days=365) == []
    db.close()


def test_prune_price_log_apaga_o_que_saiu_da_janela(tmp_path):
    from datetime import date, timedelta

    db = StateDB(tmp_path / "state.db")
    hoje = date.today()
    for delta, cents in ((0, 3390), (10, 2600), (100, 9999)):
        db.record_price("shopee", "123456", cents,
                        day=(hoje - timedelta(days=delta)).isoformat())
    db.prune_price_log(days=90)
    assert db.price_history("shopee", "123456", days=3650) == [3390, 2600]
    db.close()


def test_record_run_poda_o_price_log(tmp_path):
    from datetime import date, timedelta

    db = StateDB(tmp_path / "state.db")
    hoje = date.today()
    db.record_price("shopee", "123456", 9999,
                    day=(hoje - timedelta(days=100)).isoformat())
    db.record_price("shopee", "123456", 2600, day=hoje.isoformat())
    db.record_run(published=1, discarded=0, ref_window_days=90)
    assert db.price_history("shopee", "123456", days=3650) == [2600]
    db.close()


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

import json
from datetime import date

from afiliado.watchlist import PriceFloor, Watchlist, load_watchlist
from tests.test_models import make_offer


def write_watchlist(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_load_watchlist_all_fields(tmp_path):
    path = write_watchlist(tmp_path / "watchlist.json", {
        "generated_at": "2026-08-23",
        "valid_days": 14,
        "category_boosts": {"100630": 1.3},
        "hot_items": {"22991771385": {"boost": 1.5, "reason": "salesTrend +3686%"}},
        "price_floors": {"22991771385": {"min_price_cents": 4999, "window_days": 365}},
    })
    wl = load_watchlist(path)
    assert wl is not None
    assert wl.generated_at == date(2026, 8, 23)
    assert wl.valid_days == 14
    assert wl.category_boosts == {"100630": 1.3}
    assert wl.hot_items == {"22991771385": 1.5}
    assert wl.price_floors == {"22991771385": PriceFloor(4999, 365)}


def test_load_watchlist_missing_file_returns_none(tmp_path):
    assert load_watchlist(tmp_path / "does-not-exist.json") is None


def test_load_watchlist_invalid_json_returns_none(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not valid json", encoding="utf-8")
    assert load_watchlist(path) is None


def test_load_watchlist_missing_required_field_returns_none(tmp_path):
    path = write_watchlist(tmp_path / "watchlist.json", {"valid_days": 14})
    assert load_watchlist(path) is None


def test_is_stale_within_window():
    wl = Watchlist(generated_at=date(2026, 8, 1), valid_days=14)
    assert wl.days_old(date(2026, 8, 14)) == 13
    assert wl.is_stale(date(2026, 8, 14)) is False


def test_is_stale_past_window():
    wl = Watchlist(generated_at=date(2026, 8, 1), valid_days=14)
    assert wl.days_old(date(2026, 8, 16)) == 15
    assert wl.is_stale(date(2026, 8, 16)) is True


def test_boost_for_category_only():
    wl = Watchlist(generated_at=date(2026, 8, 1), valid_days=14,
                    category_boosts={"100630": 1.3})
    offer = make_offer(category="100630")
    assert wl.boost_for(offer) == 1.3


def test_boost_for_item_only():
    wl = Watchlist(generated_at=date(2026, 8, 1), valid_days=14,
                    hot_items={"123456": 1.5})
    offer = make_offer(item_id="123456")
    assert wl.boost_for(offer) == 1.5


def test_boost_for_category_and_item_multiplies():
    wl = Watchlist(generated_at=date(2026, 8, 1), valid_days=14,
                    category_boosts={"100630": 1.3}, hot_items={"123456": 1.5})
    offer = make_offer(item_id="123456", category="100630")
    assert wl.boost_for(offer) == 1.3 * 1.5


def test_boost_for_neither_is_one():
    wl = Watchlist(generated_at=date(2026, 8, 1), valid_days=14)
    offer = make_offer()
    assert wl.boost_for(offer) == 1.0


def test_price_floor_hit():
    wl = Watchlist(generated_at=date(2026, 8, 1), valid_days=14,
                    price_floors={"123456": PriceFloor(4999, 365)})
    assert wl.price_floor("123456") == PriceFloor(4999, 365)


def test_price_floor_miss():
    wl = Watchlist(generated_at=date(2026, 8, 1), valid_days=14)
    assert wl.price_floor("123456") is None


def test_load_watchlist_hot_items_accepts_dict_and_numeric_formats(tmp_path):
    path = write_watchlist(tmp_path / "watchlist.json", {
        "generated_at": "2026-08-23",
        "valid_days": 14,
        "hot_items": {"a": {"boost": 1.5, "reason": "x"}, "b": 2.0},
    })
    wl = load_watchlist(path)
    assert wl is not None
    assert wl.hot_items == {"a": 1.5, "b": 2.0}

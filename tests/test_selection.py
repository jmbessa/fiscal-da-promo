import pytest

from afiliado import llm, selection
from afiliado.state import StateDB
from tests.test_models import make_offer
from tests.test_state import make_post

CFG = {
    "selection": {"posts_per_run": 2, "min_discount_pct": 20, "price_min_brl": 20,
                  "price_max_brl": 1000, "dedupe_days": 30, "category_ids": [],
                  "ev_weights": {"popularity": 0.3}},
    "llm": {"model": "haiku"},
}


def test_filter_offers(tmp_path):
    db = StateDB(tmp_path / "s.db")
    db.record_post(make_post(item_id="dup"), channel="telegram", message_id="1")
    offers = [
        make_offer(item_id="ok"),
        make_offer(item_id="dup"),                                  # já postado
        make_offer(item_id="caro", price_current_cents=200_000,
                   price_original_cents=400_000),                    # fora da faixa
        make_offer(item_id="pouco", price_original_cents=26_000),    # desconto < 20%
        make_offer(item_id="semtitulo", title=""),                   # inválido
    ]
    result = selection.filter_offers(offers, db, CFG)
    assert [o.item_id for o in result] == ["ok"]
    db.close()


def test_filter_offers_category_allowlist(tmp_path):
    db = StateDB(tmp_path / "s.db")
    cfg = {**CFG, "selection": {**CFG["selection"], "category_ids": ["100636"]}}
    offers = [make_offer(item_id="a", category="100636"),
              make_offer(item_id="b", category="999")]
    assert [o.item_id for o in selection.filter_offers(offers, db, cfg)] == ["a"]
    db.close()


def test_ev_score():
    o1 = make_offer()  # price_current=24999, commission_pct=12.0, sales=0
    assert selection.ev_score(o1, CFG) == pytest.approx(29.9988)
    o2 = make_offer(sales=999)  # log10(1000)=3 -> multiplicador 1.9
    assert selection.ev_score(o2, CFG) == pytest.approx(56.99772)


def test_order_by_ev():
    low = make_offer(item_id="low", commission_pct=5.0, sales=0)
    mid = make_offer(item_id="mid", commission_pct=12.0, sales=100)
    high = make_offer(item_id="high", commission_pct=20.0, sales=500)
    ordered = selection.order_by_ev([mid, low, high], CFG)
    assert [o.item_id for o in ordered] == ["high", "mid", "low"]


def test_rank_prompt_includes_commission():
    prompt = selection._rank_prompt([make_offer()], [], 2)
    assert "comissão=R$" in prompt


def test_rank_offers_uses_llm_choice(monkeypatch):
    cands = [make_offer(item_id=str(i)) for i in range(5)]
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: {"chosen": ["3", "1"]})
    assert [o.item_id for o in selection.rank_offers(cands, [], CFG)] == ["3", "1"]


def test_rank_offers_fallback_on_llm_failure(monkeypatch):
    cands = [make_offer(item_id="a", commission_pct=5.0),
             make_offer(item_id="b", commission_pct=20.0)]
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    ranked = selection.rank_offers(cands + [make_offer(item_id="c", commission_pct=12.0)], [], CFG)
    assert ranked[0].item_id == "b"  # maior EV primeiro
    assert ranked[1].item_id == "c"
    assert len(ranked) == 2


def test_rank_offers_skips_llm_when_few_candidates(monkeypatch):
    called = []
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: called.append(1))
    cands = [make_offer(item_id="a")]
    assert selection.rank_offers(cands, [], CFG) == cands
    assert not called


def test_rank_offers_rejects_duplicate_ids(monkeypatch):
    cands = [
        make_offer(item_id="0", commission_pct=5.0),
        make_offer(item_id="1", commission_pct=8.0),
        make_offer(item_id="2", commission_pct=10.0),
        make_offer(item_id="3", commission_pct=15.0),
        make_offer(item_id="4", commission_pct=20.0),
    ]
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: {"chosen": ["3", "3"]})
    ranked = selection.rank_offers(cands, [], CFG)
    fallback = selection.order_by_ev(cands, CFG)[:2]
    assert ranked == fallback
    assert len(ranked) == 2
    assert len({o.item_id for o in ranked}) == 2  # distinct offers


def test_rank_offers_partial_valid_ids_falls_back(monkeypatch):
    cands = [
        make_offer(item_id="0", commission_pct=5.0),
        make_offer(item_id="1", commission_pct=10.0),
        make_offer(item_id="3", commission_pct=15.0),
    ]
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: {"chosen": ["3", "does-not-exist"]})
    ranked = selection.rank_offers(cands, [], CFG)
    fallback = selection.order_by_ev(cands, CFG)[:2]
    assert ranked == fallback


def test_rank_offers_caps_candidates_at_30(monkeypatch):
    cands = [make_offer(item_id=str(i), commission_pct=float(i + 1)) for i in range(40)]
    captured = {}

    def fake_ask_json(prompt, **kwargs):
        captured["prompt"] = prompt
        return None

    monkeypatch.setattr(llm, "ask_json", fake_ask_json)
    ranked = selection.rank_offers(cands, [], CFG)
    assert captured["prompt"].count("- id=") <= 30
    top30 = selection.order_by_ev(cands, CFG)[:30]
    assert ranked == top30[:CFG["selection"]["posts_per_run"]]

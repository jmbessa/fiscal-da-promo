from afiliado import llm, selection
from afiliado.state import StateDB
from tests.test_models import make_offer
from tests.test_state import make_post

CFG = {
    "selection": {"posts_per_run": 2, "min_discount_pct": 20, "price_min_brl": 20,
                  "price_max_brl": 1000, "dedupe_days": 30, "category_ids": []},
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


def test_rank_offers_uses_llm_choice(monkeypatch):
    cands = [make_offer(item_id=str(i)) for i in range(5)]
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: {"chosen": ["3", "1"]})
    assert [o.item_id for o in selection.rank_offers(cands, [], CFG)] == ["3", "1"]


def test_rank_offers_fallback_on_llm_failure(monkeypatch):
    cands = [make_offer(item_id="a", price_original_cents=30_000),   # ~17%... reprovado? não: filtro já passou; aqui só ordena
             make_offer(item_id="b", price_original_cents=100_000)]  # 75%
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    ranked = selection.rank_offers(cands + [make_offer(item_id="c")], [], CFG)
    assert ranked[0].item_id == "b"  # maior desconto primeiro
    assert len(ranked) == 2


def test_rank_offers_skips_llm_when_few_candidates(monkeypatch):
    called = []
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: called.append(1))
    cands = [make_offer(item_id="a")]
    assert selection.rank_offers(cands, [], CFG) == cands
    assert not called

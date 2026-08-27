from datetime import date

import pytest

from afiliado import llm, selection
from afiliado.state import StateDB
from afiliado.watchlist import Watchlist
from tests.test_models import make_offer
from tests.test_state import make_post

CFG = {
    "selection": {"posts_per_run": 2, "price_min_brl": 20,
                  "price_max_brl": 1000, "dedupe_days": 30, "category_ids": [],
                  "max_above_ref": 1.00, "require_price_ref": False,
                  "min_real_discount_pct": 10,
                  "ev_weights": {"popularity": 0.3, "discount": 0.5}},
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
        make_offer(item_id="semtitulo", title=""),                   # inválido
    ]
    result = selection.filter_offers(offers, db, CFG)
    assert [o.item_id for o in result] == ["ok"]
    db.close()


def test_filter_offers_publica_sem_desconto(tmp_path):
    # Decisão de volume máximo: o desconto do vendedor NÃO é mais portão. Uma
    # oferta sem desconto nenhum (o caso de toda oferta do ML, que nasce com
    # discount_pct == 0) continua candidata — o que muda é só o que o post
    # alega. Sem esta regra o ML publicava ZERO ofertas, em silêncio.
    db = StateDB(tmp_path / "s.db")
    offers = [
        make_offer(item_id="sem-desconto", price_original_cents=24_999,
                   price_current_cents=24_999),
        make_offer(item_id="meli", source="meli", price_original_cents=7_890,
                   price_current_cents=7_890, price_ref_cents=7_890),
    ]
    result = selection.filter_offers(offers, db, CFG)
    assert [o.item_id for o in result] == ["sem-desconto", "meli"]
    db.close()


def test_filter_offers_descarta_mais_caro_que_a_referencia(tmp_path):
    # Régua honesta: o único corte de preço é não anunciar algo mais caro que
    # o típico (caso real da creatina: referência R$ 26, hoje R$ 33,90).
    db = StateDB(tmp_path / "s.db")
    offers = [
        make_offer(item_id="no-preco", price_ref_cents=2600, price_current_cents=2600),
        make_offer(item_id="abaixo", price_ref_cents=5200, price_current_cents=3900),
        make_offer(item_id="caro", price_ref_cents=2600, price_current_cents=3390),
    ]
    result = selection.filter_offers(offers, db, CFG)
    assert [o.item_id for o in result] == ["no-preco", "abaixo"]
    db.close()


def test_filter_offers_max_above_ref_com_folga(tmp_path):
    db = StateDB(tmp_path / "s.db")
    cfg = {**CFG, "selection": {**CFG["selection"], "max_above_ref": 1.10}}
    offers = [
        make_offer(item_id="dentro", price_ref_cents=2600, price_current_cents=2860),
        make_offer(item_id="fora", price_ref_cents=2600, price_current_cents=2861),
    ]
    assert [o.item_id for o in selection.filter_offers(offers, db, cfg)] == ["dentro"]
    db.close()


def test_filter_offers_require_price_ref(tmp_path):
    db = StateDB(tmp_path / "s.db")
    cfg = {**CFG, "selection": {**CFG["selection"], "require_price_ref": True}}
    offers = [make_offer(item_id="com-ref", price_ref_cents=2600, price_current_cents=2500),
              make_offer(item_id="sem-ref")]
    assert [o.item_id for o in selection.filter_offers(offers, db, cfg)] == ["com-ref"]
    db.close()


def test_ev_score_bonifica_desconto_verificado():
    base = make_offer(price_current_cents=24999)
    com_desconto = make_offer(price_current_cents=24999, price_ref_cents=49998)  # 50%
    assert com_desconto.real_discount_pct == 50
    assert (selection.ev_score(com_desconto, CFG)
            == pytest.approx(selection.ev_score(base, CFG) * (1 + 0.5 * 0.5)))


def test_ev_score_ignora_desconto_do_vendedor():
    # "de" inflado do vendedor não vale nada no score: sem referência própria,
    # o bônus é zero.
    inflado = make_offer(price_original_cents=350_000, price_current_cents=24999)
    assert inflado.discount_pct == 93
    limpo = make_offer(price_original_cents=24999, price_current_cents=24999)
    assert selection.ev_score(inflado, CFG) == pytest.approx(selection.ev_score(limpo, CFG))


def test_rank_prompt_usa_desconto_verificado():
    offer = make_offer(price_original_cents=350_000, price_current_cents=24999,
                       price_ref_cents=49998)
    prompt = selection._rank_prompt([offer], [], 2)
    assert "desconto verificado=50%" in prompt
    assert "desconto=93%" not in prompt
    assert "Desconto 0% não é defeito" in prompt


def test_filter_offers_min_ev_floor(tmp_path):
    db = StateDB(tmp_path / "s.db")
    offers = [
        make_offer(item_id="baixo", commission_pct=1.0,
                   price_current_cents=5000, price_original_cents=10000),
        make_offer(item_id="medio", commission_pct=6.0,
                   price_current_cents=5000, price_original_cents=10000),
        make_offer(item_id="alto", commission_pct=20.0,
                   price_current_cents=5000, price_original_cents=10000),
    ]
    cfg = {**CFG, "selection": {**CFG["selection"], "min_ev_brl": 2.0}}
    result = selection.filter_offers(offers, db, cfg)
    assert [o.item_id for o in result] == ["medio", "alto"]

    result_sem_piso = selection.filter_offers(offers, db, CFG)
    assert [o.item_id for o in result_sem_piso] == ["baixo", "medio", "alto"]
    db.close()


def test_filter_offers_category_allowlist(tmp_path):
    db = StateDB(tmp_path / "s.db")
    cfg = {**CFG, "selection": {**CFG["selection"], "category_ids": ["100636"]}}
    offers = [make_offer(item_id="a", category="100636"),
              make_offer(item_id="b", category="999")]
    assert [o.item_id for o in selection.filter_offers(offers, db, cfg)] == ["a"]
    db.close()


def test_filter_offers_category_por_fonte(tmp_path):
    # category_ids em dict: cada fonte tem seu próprio allowlist (vazio = todas
    # as categorias passam para aquela fonte). Sem isso, ofertas do meli
    # (categorias MLB...) eram sempre descartadas pelo allowlist da shopee.
    db = StateDB(tmp_path / "s.db")
    cfg = {**CFG, "selection": {**CFG["selection"],
                                "category_ids": {"shopee": ["100636"], "meli": []}}}
    offers = [
        make_offer(item_id="shopee-ok", source="shopee", category="100636"),
        make_offer(item_id="shopee-fora", source="shopee", category="999"),
        make_offer(item_id="meli-qualquer", source="meli", category="MLB1000"),
    ]
    result = {o.item_id for o in selection.filter_offers(offers, db, cfg)}
    assert result == {"shopee-ok", "meli-qualquer"}
    db.close()


def test_filter_offers_category_lista_legado(tmp_path):
    # Lista simples (formato antigo) continua valendo para TODAS as fontes.
    db = StateDB(tmp_path / "s.db")
    cfg = {**CFG, "selection": {**CFG["selection"], "category_ids": ["100636"]}}
    offers = [
        make_offer(item_id="shopee-ok", source="shopee", category="100636"),
        make_offer(item_id="shopee-fora", source="shopee", category="999"),
        make_offer(item_id="meli-fora", source="meli", category="MLB1000"),
    ]
    assert [o.item_id for o in selection.filter_offers(offers, db, cfg)] == ["shopee-ok"]
    db.close()


def test_ev_score():
    o1 = make_offer()  # price_current=24999, commission_pct=12.0, sales=0
    assert selection.ev_score(o1, CFG) == pytest.approx(29.9988)
    o2 = make_offer(sales=999)  # log10(1000)=3 -> multiplicador 1.9
    assert selection.ev_score(o2, CFG) == pytest.approx(56.99772)


def test_ev_score_prefere_comissao_absoluta():
    # commission_brl vindo da API tem precedência sobre a estimativa via
    # commission_pct, mesmo quando os dois valores são incoerentes entre si.
    offer = make_offer(commission_brl=5.0, commission_pct=999.0)
    assert selection.ev_score(offer, CFG) == pytest.approx(5.0)


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


def test_ev_score_with_watchlist_boost():
    offer = make_offer(item_id="123456")
    wl = Watchlist(generated_at=date(2026, 8, 1), valid_days=14,
                    hot_items={"123456": 1.5})
    base = selection.ev_score(offer, CFG)
    assert selection.ev_score(offer, CFG, watchlist=wl) == pytest.approx(base * 1.5)


def test_rank_prompt_marks_hot_items():
    hot = make_offer(item_id="hot")
    cold = make_offer(item_id="cold")
    wl = Watchlist(generated_at=date(2026, 8, 1), valid_days=14,
                    hot_items={"hot": 1.5})
    prompt = selection._rank_prompt([hot, cold], [], 2, watchlist=wl)
    linhas = {l.split(" | ")[0]: l for l in prompt.splitlines() if l.startswith("- id=")}
    assert "em alta: sim" in linhas["- id=hot"]
    assert "em alta: sim" not in linhas["- id=cold"]


def test_rank_prompt_no_hot_marker_without_watchlist():
    prompt = selection._rank_prompt([make_offer()], [], 2)
    assert "em alta" not in prompt

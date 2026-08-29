from datetime import date

import pytest

from afiliado import llm, selection
from afiliado.state import StateDB
from afiliado.watchlist import Watchlist
from tests.test_models import make_offer, make_offer_ref
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


def test_filter_offers_with_stats_conta_por_portao(tmp_path):
    # C4a: seis `continue` sem contador — 50 ofertas entravam, 0 sobravam e
    # ninguém sabia por quê. Cada portão conta o que descartou.
    db = StateDB(tmp_path / "s.db")
    db.record_post(make_post(item_id="dup"), channel="telegram", message_id="1")
    cfg = {**CFG, "selection": {**CFG["selection"], "category_ids": ["100636"],
                                "require_price_ref": True, "min_ev_brl": 2.0}}
    offers = [
        make_offer(item_id="ok", category="100636", price_ref_cents=30000),
        make_offer(item_id="semtitulo", title="", category="100636"),
        make_offer(item_id="semimg", image_url="", category="100636"),
        make_offer(item_id="cat", category="999"),
        make_offer(item_id="caro", category="100636", price_ref_cents=2600,
                   price_current_cents=3390),
        make_offer(item_id="semref", category="100636"),
        make_offer(item_id="faixa", category="100636", price_ref_cents=1000,
                   price_current_cents=999),
        make_offer(item_id="dup", category="100636", price_ref_cents=30000),
        make_offer(item_id="ev", category="100636", price_ref_cents=30000,
                   commission_pct=0.1),
    ]
    result, stats = selection.filter_offers_with_stats(offers, db, cfg)
    assert [o.item_id for o in result] == ["ok"]
    assert stats == selection.FilterStats(sem_dados=2, categoria=1, acima_ref=1, sem_ref=1,
                                          faixa_preco=1, dedupe=1, ev=1)
    assert stats.total == 8
    assert stats.resumo() == ("dedupe: 1 · faixa de preço: 1 · acima da referência: 1 · "
                              "sem dados: 2 · categoria: 1 · EV: 1 · sem referência: 1")
    assert selection.filter_offers(offers, db, cfg) == result   # wrapper antigo
    db.close()


def test_filter_stats_resumo_omite_sem_referencia_quando_zero():
    stats = selection.FilterStats(dedupe=3, faixa_preco=2)
    assert stats.resumo() == ("dedupe: 3 · faixa de preço: 2 · acima da referência: 0 · "
                              "sem dados: 0 · categoria: 0 · EV: 0")


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
    com_desconto = make_offer_ref(49998, price_current_cents=24999)  # 50%, modo A
    assert com_desconto.real_discount_pct == 50
    assert (selection.ev_score(com_desconto, CFG)
            == pytest.approx(selection.ev_score(base, CFG) * (1 + 0.5 * 0.5)))


def test_ev_score_so_bonifica_o_desconto_alegavel():
    # O bônus usava o `real_discount_pct` cru: um item com 50% que a régua
    # PROÍBE alegar (sem p25/janela -> modo B) subia no ranking por um
    # desconto que o post dele nunca vai dizer. Agora vale o veredito.
    mudo = make_offer(price_current_cents=24999, price_ref_cents=49998)  # 50% cru
    assert mudo.real_discount_pct == 50
    assert selection.ev_score(mudo, CFG) == pytest.approx(
        selection.ev_score(make_offer(price_current_cents=24999), CFG))
    # e o mínimo do config manda: 20% cru com min_real_discount_pct=30 é modo B
    quase = make_offer_ref(10000, price_current_cents=8000)
    cfg = {**CFG, "selection": {**CFG["selection"], "min_real_discount_pct": 30}}
    sem_ref = make_offer(price_current_cents=8000)
    assert selection.ev_score(quase, cfg) == pytest.approx(selection.ev_score(sem_ref, cfg))
    assert selection.ev_score(quase, CFG) > selection.ev_score(sem_ref, CFG)


def test_ev_score_ignora_desconto_do_vendedor():
    # "de" inflado do vendedor não vale nada no score: sem referência própria,
    # o bônus é zero.
    inflado = make_offer(price_original_cents=350_000, price_current_cents=24999)
    assert inflado.discount_pct == 93
    limpo = make_offer(price_original_cents=24999, price_current_cents=24999)
    assert selection.ev_score(inflado, CFG) == pytest.approx(selection.ev_score(limpo, CFG))


def test_rank_prompt_usa_desconto_verificado():
    offer = make_offer_ref(49998, price_original_cents=350_000, price_current_cents=24999)
    prompt = selection._rank_prompt([offer], [], 2, cfg=CFG)
    assert "desconto verificado=50%" in prompt
    assert "desconto=93%" not in prompt
    assert "Desconto 0% não é defeito" in prompt


def test_rank_prompt_mostra_o_desconto_do_veredito_nao_o_cru():
    # O que o LLM ranqueia é o que o post pode alegar: sem p25/janela o item
    # entra na lista com 0%, não com os 50% que nunca serão publicados.
    mudo = make_offer(price_current_cents=24999, price_ref_cents=49998)
    prompt = selection._rank_prompt([mudo], [], 2, cfg=CFG)
    assert "desconto verificado=0%" in prompt
    assert "50%" not in prompt


def test_rank_prompt_diz_a_janela_das_vendas():
    """As duas lojas entram na MESMA lista e o LLM é instruído a olhar "apelo
    popular": `vendas=1000000` do ML ao lado de `vendas=45950` da Shopee o faria
    reconcentrar o que a fatia por vendas acabou de diversificar. O número vai
    com a unidade."""
    shopee = make_offer(item_id="s1", sales=45_950, sales_window_days=30)
    meli = make_offer(item_id="m1", source="meli", sales=1_000_000, sales_e_faixa=True)
    prompt = selection._rank_prompt([shopee, meli], [], 2, cfg=CFG)
    assert "vendas=45950 (últimos 30 dias)" in prompt
    assert "vendas=1000000 (total)" in prompt


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
    # A comissão entra AMORTECIDA (fase 5C, M3): ** ev_weights.commission_exp.
    o1 = make_offer()  # price_current=24999, commission_pct=12.0, sales=0
    assert selection.ev_score(o1, CFG) == pytest.approx(29.9988 ** 0.7)
    o2 = make_offer(sales=999)  # log10(1000)=3 -> multiplicador 1.9
    assert selection.ev_score(o2, CFG) == pytest.approx(29.9988 ** 0.7 * 1.9)


def test_ev_score_prefere_comissao_absoluta():
    # commission_brl vindo da API tem precedência sobre a estimativa via
    # commission_pct, mesmo quando os dois valores são incoerentes entre si.
    offer = make_offer(commission_brl=5.0, commission_pct=999.0)
    assert selection.ev_score(offer, CFG) == pytest.approx(5.0 ** 0.7)


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
    # O fallback é o slate (M3): alterna maior EV e maior número de vendas —
    # antes repetia o topo do EV, que com a comissão crua eram os mais caros.
    cands = [make_offer(item_id="a", commission_pct=5.0, sales=90_000),
             make_offer(item_id="b", commission_pct=20.0, sales=1),
             make_offer(item_id="c", commission_pct=12.0, sales=2)]
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    ranked = selection.rank_offers(cands, [], CFG)
    assert [o.item_id for o in ranked] == ["b", "a"]
    assert len(ranked) == 2
    # Com a comissão CRUA (comportamento anterior), "a" — 90 mil vendas —
    # ficava em terceiro e nunca era publicado.
    cru = {**CFG, "selection": {**CFG["selection"],
                                "ev_weights": {"popularity": 0.3, "discount": 0.5,
                                               "commission_exp": 1.0}}}
    assert [o.item_id for o in selection.order_by_ev(cands, cru)] == ["b", "c", "a"]


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
    fallback = selection.build_slate(cands, CFG)[:2]
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
    fallback = selection.build_slate(cands, CFG)[:2]
    assert ranked == fallback


@pytest.mark.parametrize("chosen", [None, 5, {"a": 1}, "id1"])
def test_rank_offers_chosen_invalido_cai_no_fallback_sem_excecao(monkeypatch, chosen):
    # `{"chosen": null}` (ou int, dict, string) do LLM derrubava o run inteiro
    # com TypeError fora de qualquer try — e, como o item gatilho nunca era
    # publicado, derrubava de novo a cada 5 min. Qualquer coisa que não seja
    # lista cai no ranking determinístico. Com "id1" (string iterável) o bug
    # era mais sutil: iterava os CARACTERES e escolhia o item "1".
    cands = [make_offer(item_id=str(i), commission_pct=float(i + 1)) for i in range(4)]
    cfg = {**CFG, "selection": {**CFG["selection"], "posts_per_run": 1}}
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: {"chosen": chosen})
    ranked = selection.rank_offers(cands, [], cfg)
    assert [o.item_id for o in ranked] == ["3"]   # maior EV, nunca o "1" dos caracteres


def test_rank_offers_caps_candidates_at_30(monkeypatch):
    cands = [make_offer(item_id=str(i), commission_pct=float(i + 1)) for i in range(40)]
    captured = {}

    def fake_ask_json(prompt, **kwargs):
        captured["prompt"] = prompt
        return None

    monkeypatch.setattr(llm, "ask_json", fake_ask_json)
    ranked = selection.rank_offers(cands, [], CFG)
    assert captured["prompt"].count("- id=") <= 30
    slate = selection.build_slate(cands, CFG)
    assert ranked == slate[:CFG["selection"]["posts_per_run"]]


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


# =============================================================================
# Fase 5C (M2) — cota por fonte: 50/50 entre as lojas LIGADAS
# =============================================================================

CFG_COTA = {**CFG,
            "selection": {**CFG["selection"], "source_quota": {"shopee": 0.5, "meli": 0.5}},
            "channels": {"telegram": {"enabled": True, "max_per_day": 60}}}


def test_meta_diaria_por_fonte_e_a_cota_vezes_o_teto():
    assert selection.source_targets(CFG_COTA, ["shopee", "meli"]) == {"shopee": 30, "meli": 30}


def test_com_uma_fonte_so_a_cota_e_cem_por_cento():
    """A cota é normalizada entre as fontes LIGADAS — com o ML desligado a
    Shopee recebe o teto inteiro, não metade dele."""
    assert selection.source_targets(CFG_COTA, ["shopee"]) == {"shopee": 60}


def test_sem_source_quota_a_meta_e_dividida_igualmente():
    assert selection.source_targets({**CFG_COTA, "selection": CFG["selection"]},
                                    ["shopee", "meli"]) == {"shopee": 30, "meli": 30}


def test_sem_teto_de_telegram_nao_ha_meta():
    assert selection.source_targets({**CFG_COTA, "channels": {"telegram": True}},
                                    ["shopee", "meli"]) == {}


def test_a_fila_prefere_a_fonte_abaixo_da_meta():
    fila = [make_offer(item_id="s1"), make_offer(item_id="s2"),
            make_offer(item_id="m1", source="meli")]
    metas = {"shopee": 30, "meli": 30}
    # Shopee já estourou a cota do dia, ML mal começou: o ML publica primeiro.
    assert selection.next_index_by_quota(fila, metas, {"shopee": 30, "meli": 2}) == 2
    # as duas abaixo da meta: vale a ordem do ranking
    assert selection.next_index_by_quota(fila, metas, {"shopee": 1, "meli": 1}) == 0


def test_a_cota_intercala_as_lojas_em_vez_de_esvaziar_uma():
    """Menor da revisão da 5C: enquanto as DUAS fontes estavam abaixo da meta,
    a função devolvia sempre o índice 0 — a loja mais bem ranqueada publicava
    as 30 dela seguidas e só então a outra aparecia no canal. O desempate passa
    a ser a razão `publicados/meta`: quem está proporcionalmente mais atrás vai
    primeiro; empate mantém a ordem do ranking."""
    fila = [make_offer(item_id=f"s{i}") for i in range(5)] + [
        make_offer(item_id=f"m{i}", source="meli") for i in range(5)]
    metas = {"shopee": 30, "meli": 30}
    publicados: dict[str, int] = {}
    escolhidos = []
    for _ in range(6):
        offer = fila.pop(selection.next_index_by_quota(fila, metas, publicados))
        publicados[offer.source] = publicados.get(offer.source, 0) + 1
        escolhidos.append(offer.source)
    assert escolhidos == ["shopee", "meli"] * 3


def test_a_cota_desempata_pela_razao_e_nao_pelo_numero_absoluto():
    """Metas diferentes (a Shopee com o dobro): 10 de 40 (25%) está mais
    adiantada que 4 de 20 (20%), mesmo publicando mais em número absoluto."""
    fila = [make_offer(item_id="s1"), make_offer(item_id="m1", source="meli")]
    assert selection.next_index_by_quota(fila, {"shopee": 40, "meli": 20},
                                         {"shopee": 10, "meli": 4}) == 1


def test_uma_fonte_completa_a_outra_quando_a_preferida_nao_tem_candidata():
    fila = [make_offer(item_id="s1")]              # só Shopee na fila
    indice = selection.next_index_by_quota(fila, {"shopee": 30, "meli": 30},
                                           {"shopee": 40, "meli": 0})
    assert indice == 0                              # nenhuma do ML: a Shopee completa


def test_sem_metas_a_fila_segue_o_ranking():
    fila = [make_offer(item_id="s1"), make_offer(item_id="m1", source="meli")]
    assert selection.next_index_by_quota(fila, {}, {}) == 0
    assert selection.next_index_by_quota([], {}, {}) is None


# =============================================================================
# Fase 5C (M3/A8) — comissão amortecida e slate diverso
# =============================================================================

def test_ev_amortece_a_comissao():
    """A8: a amplitude do fator comissão era 50x (R$ 20 → R$ 1.000) contra 2,5x
    de popularidade. `commission_brl ** 0.7` derruba isso para ~15x."""
    barato = make_offer(item_id="b", commission_brl=3.0, sales=0)
    caro = make_offer(item_id="c", commission_brl=24.0, sales=0)
    assert selection.ev_score(barato, CFG) == pytest.approx(3.0 ** 0.7)
    razao = selection.ev_score(caro, CFG) / selection.ev_score(barato, CFG)
    assert razao == pytest.approx(8 ** 0.7)          # 8x de comissão vira 4,3x de EV
    # expoente 1.0 desliga a amortização (comportamento anterior)
    cfg1 = {**CFG, "selection": {**CFG["selection"],
                                 "ev_weights": {"popularity": 0.3, "discount": 0.5,
                                                "commission_exp": 1.0}}}
    assert selection.ev_score(caro, cfg1) == pytest.approx(24.0)


CFG_EXP1 = {**CFG, "selection": {**CFG["selection"],
                                 "ev_weights": {"popularity": 0.3, "discount": 0.5,
                                                "commission_exp": 1.0}}}


def test_o_expoente_nao_inverte_a_camera_e_a_creatina():
    """I-4 da revisão: o comentário do `commission_exp` (e o do config)
    afirmavam que 0.7 inverte "câmera de R$ 800 a 3% com 100 vendas × creatina
    de R$ 30 a 10% com 50 mil vendas". **Não inverte**: 14,81 × 5,20 — a câmera
    continua ganhando, só que por 2,8× em vez de 5,3×. Quem põe a creatina
    diante do LLM é o recorte por VENDAS do `build_slate`, não o expoente."""
    camera = make_offer(item_id="cam", commission_brl=24.0, sales=100)
    creatina = make_offer(item_id="cre", commission_brl=3.0, sales=50000)
    assert selection.ev_score(camera, CFG) == pytest.approx(14.81, abs=0.01)
    assert selection.ev_score(creatina, CFG) == pytest.approx(5.20, abs=0.01)
    assert selection.ev_score(camera, CFG) > selection.ev_score(creatina, CFG)
    razao = selection.ev_score(camera, CFG) / selection.ev_score(creatina, CFG)
    razao_crua = selection.ev_score(camera, CFG_EXP1) / selection.ev_score(creatina, CFG_EXP1)
    assert razao == pytest.approx(2.85, abs=0.01)
    assert razao_crua == pytest.approx(5.32, abs=0.01)


def test_o_expoente_move_o_cruzamento_da_comissao():
    """O que o expoente faz DE FATO: contra a creatina (R$ 3,00 e 50 mil
    vendas), um item de 100 vendas precisava valer mais de R$ 4,51 de comissão
    para ganhar; com 0.7 precisa passar de R$ 5,38. É esse deslocamento — não
    uma inversão — que o comentário do config descreve."""
    creatina = make_offer(item_id="cre", commission_brl=3.0, sales=50000)
    for cfg, cruzamento in ((CFG_EXP1, 4.51), (CFG, 5.38)):
        abaixo = make_offer(item_id="a", commission_brl=cruzamento - 0.05, sales=100)
        acima = make_offer(item_id="b", commission_brl=cruzamento + 0.05, sales=100)
        assert selection.ev_score(abaixo, cfg) < selection.ev_score(creatina, cfg)
        assert selection.ev_score(acima, cfg) > selection.ev_score(creatina, cfg)


def test_o_expoente_inverte_de_verdade_um_par_mais_proximo():
    """A inversão REAL que o 0.7 produz: R$ 12,50 de comissão com 90 mil vendas
    contra R$ 30,00 com 2 vendas. Com a comissão crua ganha o de 2 vendas
    (34,29 × 31,08); com o expoente, o campeão de vendas passa na frente
    (14,57 × 12,36)."""
    popular = make_offer(item_id="pop", commission_brl=12.50, sales=90000)
    caro = make_offer(item_id="caro", commission_brl=30.0, sales=2)
    assert selection.ev_score(caro, CFG_EXP1) > selection.ev_score(popular, CFG_EXP1)
    assert selection.ev_score(popular, CFG) > selection.ev_score(caro, CFG)
    assert [o.item_id for o in selection.order_by_ev([caro, popular], CFG)] == ["pop", "caro"]


def test_ev_com_comissao_zero_nao_explode():
    assert selection.ev_score(make_offer(commission_brl=0.0, commission_pct=0.0), CFG) == 0.0


def test_o_log10_do_ev_amortece_a_escala_diferente_das_fontes():
    """Fase 5H: `sales` NÃO é comparável entre fontes (o do ML é vitalício, o
    da Shopee é de 30 dias), e ainda assim ele entra CRU no `ev_score`. A
    medição que sustenta essa decisão, para ninguém "consertar" por intuição:
    o `log10` reduz 250.000 × 45.950 (5,4×) a 2,62 × 2,40 no fator de
    popularidade — 9% de vantagem para o ML, com a `source_quota` de 0,5/0,5
    limitando o resto. Quem conserta a comparação crua é a fatia por vendas do
    slate, onde ela decidia a fatia INTEIRA."""
    meli = make_offer(source="meli", commission_brl=10.0, sales=250_000)
    shopee = make_offer(source="shopee", commission_brl=10.0, sales=45_950)
    assert selection.ev_score(meli, CFG) / selection.ev_score(shopee, CFG) == pytest.approx(
        1.09, abs=0.01)


def test_a_fatia_por_vendas_ordena_por_posicao_dentro_da_fonte():
    """H3: com o ML em escala vitalícia e a Shopee em 30 dias, `sorted(sales)`
    cru punha TODO o ML na frente. A fatia passa a alternar: o mais vendido de
    cada loja, depois o segundo de cada."""
    candidatas = [make_offer(source="meli", item_id=f"m{i}", sales=s)
                  for i, s in enumerate((1_000_000, 250_000, 100_000))]
    candidatas += [make_offer(source="shopee", item_id=f"s{i}", sales=s)
                   for i, s in enumerate((77_344, 45_950, 31_077))]
    ordem = [o.item_id for o in selection._por_vendas_normalizadas(candidatas)]
    assert ordem == ["m0", "s0", "m1", "s1", "m2", "s2"]


def test_a_fatia_por_vendas_nao_premia_a_fonte_com_mais_candidatas():
    """Por POSIÇÃO e não por percentil (`i / n`): o pool da Shopee tem centenas
    de candidatas e o do ML dezenas — o percentil daria os primeiros lugares à
    Shopee inteira e a monocultura só trocaria de dono."""
    candidatas = [make_offer(source="shopee", item_id=f"s{i}", sales=80_000 - i)
                  for i in range(300)]
    candidatas += [make_offer(source="meli", item_id=f"m{i}", sales=1_000_000 - i)
                   for i in range(5)]
    ordem = [o.item_id for o in selection._por_vendas_normalizadas(candidatas)][:6]
    assert ordem == ["m0", "s0", "m1", "s1", "m2", "s2"]


def test_o_slate_por_vendas_nao_vira_monocultura_do_meli():
    """O efeito no slate: com o ML ganhando também no EV, as 30 vagas saíam
    TODAS dele — a fatia por vendas, que existe para diversificar, repetia o
    que o EV já tinha escolhido. Uma categoria só, para que o limite por
    categoria não resgate ninguém."""
    candidatas = [make_offer(source="meli", item_id=f"m{i}", category="saude",
                             commission_brl=20.0, sales=1_000_000 - i * 1000)
                  for i in range(40)]
    candidatas += [make_offer(source="shopee", item_id=f"s{i}", category="saude",
                              commission_brl=2.0, sales=77_000 - i * 100)
                   for i in range(20)]
    slate = selection.build_slate(candidatas, CFG)
    assert {o.source for o in slate} == {"meli", "shopee"}
    # o campeão de vendas da Shopee chega ao LLM, não só o do ML
    assert {"m0", "s0"} <= {o.item_id for o in slate}


def _camera(i):
    return make_offer(item_id=f"cam{i}", category="eletro", commission_brl=24.0,
                      sales=100, price_current_cents=80000)


def _creatina(i):
    return make_offer(item_id=f"cre{i}", category="saude", commission_brl=3.0,
                      sales=50000, price_current_cents=3000)


def test_slate_traz_o_campeao_de_vendas_mesmo_com_ev_baixo():
    candidatas = [_camera(i) for i in range(30)] + [_creatina(0)]
    slate = selection.build_slate(candidatas, CFG)
    assert "cre0" in {o.item_id for o in slate}


def test_slate_reparte_as_vagas_entre_as_categorias_presentes():
    """I-5 da revisão: o limite por categoria deixou de ser 4 fixo e passou a
    ser `max(4, ceil(30 / categorias presentes))` — com 3 categorias são 10
    vagas cada, e o slate enche em vez de saturar em 12."""
    candidatas = ([_camera(i) for i in range(30)] + [_creatina(i) for i in range(30)]
                  + [make_offer(item_id=f"casa{i}", category="casa", commission_brl=5.0,
                                sales=900) for i in range(30)])
    slate = selection.build_slate(candidatas, CFG)
    from collections import Counter
    por_categoria = Counter(o.category for o in slate)
    assert len(slate) == selection.MAX_CANDIDATES_FOR_PROMPT
    assert max(por_categoria.values()) <= 10
    assert len(por_categoria) == 3                # nenhuma categoria ocupa o slate
    assert len(slate) == len(set(o.item_id for o in slate))


def test_slate_enche_as_trinta_vagas_com_as_cinco_raizes():
    """Teste do brief: 13.000 candidatas em 5 categorias → 30 no slate. Com o
    limite fixo de 4 e as 5 raízes que o config permite, o slate saturava em
    20 e o prompt prometia 30 vagas que nunca existiam."""
    candidatas = [make_offer(item_id=f"x{i}", category=f"c{i % 5}",
                             commission_brl=1.0 + (i % 7), sales=(i % 500) * 10)
                  for i in range(13000)]
    slate = selection.build_slate(candidatas, CFG)
    assert len(slate) == selection.MAX_CANDIDATES_FOR_PROMPT
    assert len({o.category for o in slate}) == 5


def test_slate_com_estoque_concentrado_numa_categoria_nao_degrada():
    """O outro lado do mesmo bug: estoque inteiro numa categoria dava um slate
    de QUATRO itens, em silêncio — o LLM escolhia 4 de 13.000."""
    candidatas = [make_offer(item_id=f"x{i}", category="100630",
                             commission_brl=1.0 + (i % 7), sales=(i % 500) * 10)
                  for i in range(13000)]
    assert len(selection.build_slate(candidatas, CFG)) == selection.MAX_CANDIDATES_FOR_PROMPT


def test_slate_cheio_mesmo_com_categorias_de_um_item_so():
    """Rede de segurança: 5 categorias presentes dão limite 6, mas quatro
    delas têm um item só. Antes de sobrar vaga vazia, o slate completa por EV
    ignorando o limite — melhor menos diverso que pela metade."""
    candidatas = [make_offer(item_id=f"g{i}", category="grande", commission_brl=2.0,
                             sales=i) for i in range(100)]
    candidatas += [make_offer(item_id=f"p{i}", category=f"peq{i}", commission_brl=1.0,
                              sales=1) for i in range(4)]
    slate = selection.build_slate(candidatas, CFG)
    assert len(slate) == selection.MAX_CANDIDATES_FOR_PROMPT
    assert {"p0", "p1", "p2", "p3"} <= {o.item_id for o in slate}   # os raros entram


def test_slate_alterna_as_origens():
    """O fallback determinístico é a própria união, alternando EV, vendas e
    desconto — não o topo do EV de novo."""
    candidatas = [_camera(i) for i in range(3)] + [_creatina(i) for i in range(3)] + [
        make_offer(item_id=f"promo{i}", category=f"cat{i}", commission_brl=1.0, sales=10,
                   price_current_cents=1000, price_ref_cents=5000, price_p25_cents=2000,
                   price_window_days=90) for i in range(3)]
    slate = selection.build_slate(candidatas, CFG)
    assert [o.item_id[:3] for o in slate[:3]] == ["cam", "cre", "pro"]


def test_slate_nunca_passa_do_teto_do_prompt():
    candidatas = [make_offer(item_id=f"x{i}", category=f"c{i}", commission_brl=float(i + 1),
                             sales=i * 100) for i in range(200)]
    assert len(selection.build_slate(candidatas, CFG)) <= selection.MAX_CANDIDATES_FOR_PROMPT


def test_rank_offers_cai_no_slate_quando_o_llm_falha(monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    candidatas = [_camera(i) for i in range(3)] + [_creatina(i) for i in range(3)]
    cfg = {**CFG, "selection": {**CFG["selection"], "posts_per_run": 2}}
    escolhidas = selection.rank_offers(candidatas, [], cfg)
    assert [o.item_id for o in escolhidas] == [o.item_id
                                               for o in selection.build_slate(candidatas, cfg)[:2]]


def test_rank_offers_so_escolhe_do_slate_apresentado(monkeypatch):
    candidatas = [_camera(i) for i in range(30)] + [_creatina(0)]
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: {"chosen": ["cre0"]})
    cfg = {**CFG, "selection": {**CFG["selection"], "posts_per_run": 1}}
    assert [o.item_id for o in selection.rank_offers(candidatas, [], cfg)] == ["cre0"]


# -- fase 5J: "preço ainda desconhecido" não pode morrer nos portões ---------
# A entrada do ML sem histórico chega com `price_current_cents == 0` — o pool
# não traz mais a mediana da janela, e o preço só existe depois do
# `refresh_price`, que roda DEPOIS destes portões. Barrá-la aqui mataria a fase
# inteira em silêncio, que é a assinatura do zero silencioso.

def _sem_preco(item_id="novo", **kw):
    return make_offer(source="meli", item_id=item_id, price_original_cents=0,
                      price_current_cents=0, commission_pct=4.0, sales=13337, **kw)


def test_faixa_de_preco_nao_mata_a_oferta_de_preco_desconhecido(tmp_path):
    db = StateDB(tmp_path / "s.db")
    result, stats = selection.filter_offers_with_stats([_sem_preco()], db, CFG)
    assert [o.item_id for o in result] == ["novo"]
    assert stats.faixa_preco == 0
    db.close()


def test_piso_de_ev_nao_mata_a_oferta_de_preco_desconhecido(tmp_path):
    # O EV é comissão (preço × taxa) ponderada por popularidade: sem preço não
    # há comissão a medir, e o piso não tem como julgar. Com o `min_ev_brl` do
    # config real (0,50), TODA entrada sem histórico caía aqui.
    db = StateDB(tmp_path / "s.db")
    cfg = {**CFG, "selection": {**CFG["selection"], "min_ev_brl": 0.50}}
    assert selection.ev_score(_sem_preco(), cfg) == 0.0
    result, stats = selection.filter_offers_with_stats([_sem_preco()], db, cfg)
    assert [o.item_id for o in result] == ["novo"]
    assert stats.ev == 0
    db.close()


def test_a_faixa_continua_valendo_para_quem_tem_preco(tmp_path):
    # O portão não foi afrouxado: quem TEM preço continua sendo medido por ele.
    db = StateDB(tmp_path / "s.db")
    com_preco = [make_offer(item_id="cara", price_current_cents=300_000),
                 make_offer(item_id="barata", price_current_cents=1999)]
    result, stats = selection.filter_offers_with_stats(com_preco, db, CFG)
    assert result == [] and stats.faixa_preco == 2
    db.close()


# -- fase 5L: "comissão ainda não medida" não pode morrer no piso de EV ------
# A linha do data feed da Shopee chega SEM comissão e SEM vendas (o feed não
# traz nenhum dos dois): as duas só existem depois do `refresh_price`, que roda
# imediatamente antes de publicar — muito DEPOIS destes portões. É o defeito da
# 5J com outro campo: lá o preço 0 significava "ainda não sei" e o piso o lia
# como "vale zero".

def _sem_comissao(item_id="feed", **kw):
    return make_offer(source="shopee", item_id=item_id, commission_pct=0.0,
                      commission_brl=0.0, sales=0, **kw)


def test_piso_de_ev_nao_mata_a_oferta_de_comissao_desconhecida(tmp_path):
    db = StateDB(tmp_path / "s.db")
    cfg = {**CFG, "selection": {**CFG["selection"], "min_ev_brl": 0.50}}
    assert selection.ev_score(_sem_comissao(), cfg) == 0.0
    result, stats = selection.filter_offers_with_stats([_sem_comissao()], db, cfg)
    assert [o.item_id for o in result] == ["feed"]
    assert stats.ev == 0
    db.close()


def test_o_piso_continua_valendo_para_quem_tem_comissao_medida(tmp_path):
    """O portão não foi afrouxado: comissão MEDIDA e baixa continua caindo."""
    db = StateDB(tmp_path / "s.db")
    cfg = {**CFG, "selection": {**CFG["selection"], "min_ev_brl": 2.0}}
    magra = make_offer(item_id="magra", commission_pct=0.5,
                       price_current_cents=2000, price_original_cents=2000)
    result, stats = selection.filter_offers_with_stats([magra], db, cfg)
    assert result == [] and stats.ev == 1
    db.close()


def test_o_prompt_do_ranker_nao_afirma_que_a_comissao_e_zero():
    """`comissão=R$0.00 (0.0%)` é uma AFIRMAÇÃO falsa para quem ranqueia: a
    comissão não é zero, é desconhecida — e o prompt manda priorizar retorno
    esperado. O LLM descartaria toda candidata do feed por um número que
    ninguém mediu."""
    linha = selection._rank_prompt([_sem_comissao()], [], 1, None, CFG)
    assert "R$0.00" not in linha and "0.0%" not in linha
    assert "a medir" in linha
    # e quem TEM comissão medida continua sendo apresentado com o número.
    com = selection._rank_prompt([make_offer(item_id="x", commission_pct=12.0)], [], 1,
                                 None, CFG)
    assert "R$30.00" in com and "12.0%" in com

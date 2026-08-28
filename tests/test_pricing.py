import dataclasses
import itertools
from datetime import date, timedelta

from afiliado import pricing
from afiliado.models import NO_CLAIM, Verdict
from afiliado.state import StateDB
from afiliado.watchlist import PriceFloor, PriceRef, Watchlist
from tests.test_models import make_offer, make_offer_ref

CFG = {"selection": {"ref_window_days": 90, "ref_min_observations": 14,
                     "min_real_discount_pct": 10}}


def _wl(**kw) -> Watchlist:
    return Watchlist(generated_at=date.today(), valid_days=14, **kw)


def _seed_history(db: StateDB, cents: list[int], source="shopee", item_id="123456",
                  start_days_ago: int = 1) -> None:
    """cents[0] = `start_days_ago` dias atrás, cents[1] = um dia antes, ...

    O "hoje" vem do BANCO (`local_today`, fuso da operação), nunca de
    `date.today()` da máquina. Entre 00h e 03h UTC os dois discordam, e o CI
    — que roda em UTC — falhava todas as noites com um dia a menos na janela:
    a observação de hoje caía em cima de `cents[0]` e as duas viravam uma."""
    hoje = db.local_today()
    for i, valor in enumerate(cents):
        db.record_price(source, item_id, valor,
                        day=(hoje - timedelta(days=start_days_ago + i)).isoformat())


_contador = itertools.count()


def _veredito_apos_historico(tmp_path, anteriores: list[int], hoje_cents: int,
                             minimo: int = 10, cfg: dict = CFG):
    """Como o pipeline: grava o preço de hoje, enriquece pelo price_log e
    decide. Devolve ((modo, desconto), oferta enriquecida) — o selo é
    testado à parte (hoje = mínima do histórico ganha selo, e isso é
    correto, mas não é o que estes cenários medem). Um banco novo por
    chamada: dois históricos no mesmo arquivo se fundiriam pelo MIN do dia."""
    db = StateDB(tmp_path / f"s{next(_contador)}.db")
    _seed_history(db, anteriores)
    offer = make_offer(price_current_cents=hoje_cents)
    pricing.record_observations(db, [offer])
    (out,) = pricing.enrich_offers([offer], db, None, cfg)
    db.close()
    v = pricing.verdict(out, minimo)
    return (v.mode, v.discount_pct), out


MODO_B = ("B", 0)


# -- median_cents / p25_cents / window_text ------------------------------------

def test_median_cents_impar():
    assert pricing.median_cents([2600, 3390, 1890]) == 2600


def test_median_cents_par_pega_o_menor_dos_dois_centrais():
    # C8: a média dos dois centrais fabricava um "De:" que nunca foi preço
    # (R$ 47,45 de [26,00 ×3, 68,90 ×3]). Agora é sempre um preço que existiu.
    assert pricing.median_cents([1000, 2000, 3000, 5000]) == 2000
    assert pricing.median_cents([2600, 2600, 2600, 6890, 6890, 6890]) == 2600
    assert pricing.median_cents([2500, 2501]) == 2500


def test_median_cents_vazio():
    assert pricing.median_cents([]) == 0


def test_p25_cents_sempre_para_baixo():
    assert pricing.p25_cents([]) == 0
    assert pricing.p25_cents([500]) == 500
    assert pricing.p25_cents([4000, 1000, 3000, 2000]) == 1000      # posição 0,75 -> 0
    assert pricing.p25_cents([10, 20, 30, 40, 50]) == 20            # posição 1
    assert pricing.p25_cents([1, 2, 3, 4, 5, 6, 7, 8]) == 2          # posição 1,75 -> 1
    assert pricing.p25_cents([1, 2, 3, 4, 5, 6, 7, 8, 9]) == 3       # posição 2
    # 30 dias: 27 a 5000 e 3 a 4000 -> o topo do quartil barato é 5000
    assert pricing.p25_cents([5000] * 27 + [4000] * 3) == 5000
    # ... mas com 8 dias a 4000 (27%) o quartil barato é todo 4000
    assert pricing.p25_cents([5000] * 22 + [4000] * 8) == 4000


def test_window_text_nunca_promete_mais_do_que_mediu():
    assert pricing.window_text(1) == "1 dias"
    assert pricing.window_text(45) == "45 dias"
    assert pricing.window_text(59) == "59 dias"
    assert pricing.window_text(60) == "2 meses"
    assert pricing.window_text(89) == "2 meses"
    assert pricing.window_text(90) == "3 meses"
    assert pricing.window_text(191) == "6 meses"
    assert pricing.window_text(365) == "12 meses"


# -- format_sales ------------------------------------------------------------

def test_format_sales():
    assert pricing.format_sales(30000) == "30 mil vendidos"
    assert pricing.format_sales(1000) == "1 mil vendidos"
    assert pricing.format_sales(850) == "850 vendidos"
    assert pricing.format_sales(1) == "1 vendidos"
    assert pricing.format_sales(0) == ""


# -- verdict: a regra, formalmente ------------------------------------------------

def test_verdict_modo_a_exige_ref_p25_janela_quartil_e_minimo():
    base = dict(price_ref_cents=2600, price_p25_cents=2400, price_window_days=30,
                price_current_cents=1890)                      # 27%, no quartil barato
    assert pricing.verdict(make_offer(**base), 10) == Verdict("A", 27, "")
    assert pricing.verdict(make_offer(**{**base, "price_p25_cents": 0}), 10).mode == "B"
    assert pricing.verdict(make_offer(**{**base, "price_window_days": 13}), 10).mode == "B"
    assert pricing.verdict(make_offer(**{**base, "price_window_days": 14}), 10).mode == "A"
    assert pricing.verdict(make_offer(**{**base, "price_current_cents": 2400}), 10).mode == "B"
    assert pricing.verdict(make_offer(**{**base, "price_current_cents": 2300}), 10) == Verdict("A", 11, "")
    assert pricing.verdict(make_offer(**{**base, "price_current_cents": 2399}), 10).mode == "B"  # 7% < 10
    assert pricing.verdict(make_offer(**{**base, "price_current_cents": 2399}), 7).mode == "A"
    assert pricing.verdict(make_offer(**{**base, "price_current_cents": 2500}), 0).mode == "B"
    assert pricing.verdict(make_offer(**base), 28).mode == "B"        # 27 < 28
    assert pricing.verdict(make_offer(**base), 27).mode == "A"


def test_verdict_modo_b_e_sempre_zero_por_cento():
    v = pricing.verdict(make_offer(price_ref_cents=2600, price_current_cents=2500), 10)
    assert v == NO_CLAIM
    assert pricing.verdict(make_offer(), 0) == NO_CLAIM            # min 0: nunca "0% OFF"


def test_verdict_quartil_e_estrito():
    # `<` e não `<=`: preços são discretos e repetidos; um preço que ocupa
    # 40% dos dias É o p25 e, com `<=`, ganharia "62% OFF verificado" — o
    # padrão "tabela alta + promoção recorrente" (C8).
    igual_ao_p25 = make_offer(price_ref_cents=6890, price_p25_cents=2600,
                              price_window_days=90, price_current_cents=2600)
    assert pricing.verdict(igual_ao_p25, 10).mode == "B"
    abaixo = make_offer(price_ref_cents=6890, price_p25_cents=2600,
                        price_window_days=90, price_current_cents=2599)
    assert pricing.verdict(abaixo, 10) == Verdict("A", 62, "")


# -- Teste obrigatório 1: os cinco cenários do repro_median.py --------------------

def test_cenario_a_alternancia_dia_sim_dia_nao_e_modo_b(tmp_path):
    v, out = _veredito_apos_historico(tmp_path, [6890, 2600, 6890, 2600], 2600)
    assert v == MODO_B and out.price_window_days == 0          # 5 dias < 14: sem referência
    # e mesmo com 31 dias: 26,00 É o p25 (e a mediana) -> nada a alegar
    v, out = _veredito_apos_historico(tmp_path, [6890, 2600] * 15, 2600)
    assert out.price_window_days == 31 and out.price_p25_cents == 2600
    assert out.price_ref_cents == 2600 and v == MODO_B


def test_cenario_b_tres_de_cinco_dias_e_modo_b(tmp_path):
    v, out = _veredito_apos_historico(tmp_path, [6890, 6890, 6890, 2600], 2600)
    assert v == MODO_B and out.price_ref_cents == 0            # < 14 dias: sem referência
    # com 90 dias no mesmo padrão (54/36): 26,00 ocupa 40% dos dias = é o p25
    v, out = _veredito_apos_historico(tmp_path, [6890] * 54 + [2600] * 35, 2600)
    assert out.price_ref_cents == 6890 and out.price_p25_cents == 2600
    assert v == MODO_B


def test_cenario_c_54_dias_caros_36_baratos_e_modo_b(tmp_path):
    # O "De: R$ 68,90 | Por: R$ 26,00 (62% OFF)" do achado C8: hoje está no
    # topo do quartil barato, não abaixo dele -> modo B (com `<=` seria A).
    v, out = _veredito_apos_historico(tmp_path, [6890] * 54 + [2600] * 35, 2600)
    assert v == MODO_B
    assert pricing.price_line(out, pricing.verdict(out, 10)) == ("R$ 26,00", "")


def test_cenario_d_docstring_89_dias_baratos_um_caro_e_modo_b(tmp_path):
    # 26,00 há 89 dias e 68,90 por um dia: 26,00 é o preço típico — não há
    # desconto a alegar (a régua antiga já acertava este).
    v, out = _veredito_apos_historico(tmp_path, [6890] + [2600] * 88, 2600)
    assert out.price_ref_cents == 2600 and out.price_p25_cents == 2600
    assert v == MODO_B


def test_cenario_e_promocao_permanente_e_modo_b(tmp_path):
    # 68,90 por 44 dias, depois 48,00 por 46: o preço novo É o típico.
    v, out = _veredito_apos_historico(tmp_path, [4800] * 45 + [6890] * 44, 4800)
    assert out.price_ref_cents == 4800 and out.price_p25_cents == 4800
    assert v == MODO_B


def test_promocao_genuina_10pct_dos_dias_a_menos_20_e_modo_a(tmp_path):
    # 27 dias a 50,00, 3 dias (10%) a 40,00 incluindo hoje: 40 < p25 = 50,
    # 30 dias de janela, 20% contra a mediana -> "De: R$ 50,00 | Por: R$ 40,00".
    v, out = _veredito_apos_historico(tmp_path, [4000, 4000] + [5000] * 27, 4000)
    assert out.price_window_days == 30
    assert out.price_ref_cents == 5000 and out.price_p25_cents == 5000
    assert v == ("A", 20)
    veredito = pricing.verdict(out, 10)
    assert pricing.price_line(out, veredito) == ("De: R$ 50,00 | Por: R$ 40,00 (20% OFF)", "")
    # hoje é a mínima dos 30 dias observados: o selo diz exatamente isso
    assert veredito.seal == "🏷️ Menor preço dos últimos 30 dias (verificado)"


# -- Teste obrigatório 2: rampa --------------------------------------------------

def test_rampa_100x5_150x7_hoje_120_e_modo_b(tmp_path):
    # "De: R$ 150 | Por: R$ 120 (20% OFF)" num preço 20% ACIMA de duas
    # semanas atrás (C8): cai pela janela (13 dias < 14: nem referência há)...
    v, out = _veredito_apos_historico(tmp_path, [15000] * 7 + [10000] * 5, 12000)
    assert out.price_window_days == 0 and out.price_ref_cents == 0 and v == MODO_B
    # ...e, com janela suficiente, cai pelo quartil (120 > p25 = 100).
    v, out = _veredito_apos_historico(tmp_path, [15000] * 8 + [10000] * 6, 12000)
    assert out.price_window_days == 15 and out.price_p25_cents == 10000
    assert out.price_ref_cents == 15000 and v == MODO_B


# -- Teste obrigatório 3: janela de 13 vs 14 dias ----------------------------------

def test_janela_13_dias_e_modo_b_14_dias_e_modo_a(tmp_path):
    v, out = _veredito_apos_historico(tmp_path, [5000] * 12, 4000)
    assert out.price_window_days == 0 and v == MODO_B            # < ref_min_observations
    v, out = _veredito_apos_historico(tmp_path, [5000] * 13, 4000)
    assert out.price_window_days == 14 and v == ("A", 20)


def test_janela_minima_vale_mesmo_com_ref_min_observations_baixo(tmp_path):
    # ref_min_observations: 5 traz a referência com 13 dias, mas a regra do
    # quartil exige >= 14 dias distintos para ALEGAR.
    cfg = {"selection": {"ref_window_days": 90, "ref_min_observations": 5}}
    v, out = _veredito_apos_historico(tmp_path, [5000] * 12, 4000, cfg=cfg)
    assert out.price_ref_cents == 5000 and out.price_window_days == 13
    assert v == MODO_B


# -- Teste obrigatório 4: selo estrito com a janela real ----------------------------

def test_selo_estrito_piso_24000():
    sem = make_offer(price_current_cents=24001, price_floor_cents=24000, price_floor_window_days=45)
    assert pricing.verdict(sem, 10).seal == ""
    com = make_offer(price_current_cents=24000, price_floor_cents=24000, price_floor_window_days=45)
    assert pricing.verdict(com, 10) == Verdict(
        "B", 0, "🏷️ Menor preço dos últimos 45 dias (verificado)", 45)


def test_selo_diz_meses_a_partir_de_60_dias():
    offer = make_offer(price_current_cents=2000, price_floor_cents=2000, price_floor_window_days=191)
    assert pricing.verdict(offer, 10).seal == "🏷️ Menor preço dos últimos 6 meses (verificado)"


def test_selo_sem_janela_conhecida_nao_existe():
    # O texto diz "últimos N dias" — sem N medido não há o que afirmar.
    offer = make_offer(price_current_cents=2000, price_floor_cents=2000)
    assert pricing.verdict(offer, 10).seal == ""


def test_selo_e_modo_a_convivem():
    offer = make_offer_ref(2600, price_current_cents=1890,
                           price_floor_cents=1890, price_floor_window_days=90)
    assert pricing.verdict(offer, 10) == Verdict(
        "A", 27, "🏷️ Menor preço dos últimos 3 meses (verificado)", 90)


# -- price_line --------------------------------------------------------------

def test_price_line_modo_a_desconto_verificado():
    offer = make_offer_ref(2600, price_current_cents=1890)
    preco, prova = pricing.price_line(offer, pricing.verdict(offer, 10))
    assert preco == "De: R$ 26,00 | Por: R$ 18,90 (27% OFF)"
    assert prova == ""


def test_price_line_modo_a_ignora_o_de_do_vendedor():
    # "de" inflado do vendedor (350,00) nunca aparece — só a NOSSA referência.
    offer = make_offer_ref(2600, price_original_cents=35000, price_current_cents=1890)
    preco, _ = pricing.price_line(offer, pricing.verdict(offer, 10))
    assert "350,00" not in preco
    assert "R$ 26,00" in preco


def test_price_line_modo_b_sem_referencia():
    offer = make_offer(price_current_cents=3390, rating=4.9, sales=30000)
    preco, prova = pricing.price_line(offer, pricing.verdict(offer, 10))
    assert preco == "R$ 33,90"
    assert prova == "⭐ 4,9 · 30 mil vendidos"


def test_price_line_modo_b_quando_o_desconto_fica_abaixo_do_minimo():
    # 2600 -> 2500 = 3% verificado, abaixo do mínimo de 10: não alega desconto.
    offer = make_offer_ref(2600, price_current_cents=2500, rating=0.0, sales=850)
    preco, prova = pricing.price_line(offer, pricing.verdict(offer, 10))
    assert preco == "R$ 25,00"
    assert "OFF" not in preco
    assert prova == "850 vendidos"


def test_price_line_modo_b_sem_prova_social_conhecida():
    offer = make_offer(price_current_cents=3390, rating=0.0, sales=0)
    assert pricing.price_line(offer, NO_CLAIM) == ("R$ 33,90", "")


def test_price_line_modo_b_so_com_nota():
    offer = make_offer(price_current_cents=3390, rating=4.9, sales=0)
    assert pricing.price_line(offer, NO_CLAIM)[1] == "⭐ 4,9"


def test_price_line_obedece_ao_veredito_e_nao_recalcula():
    # A oferta tem 27% verificável, mas quem manda é o veredito recebido.
    offer = make_offer_ref(2600, price_current_cents=1890)
    assert pricing.price_line(offer, NO_CLAIM)[0] == "R$ 18,90"
    assert pricing.price_line(offer, Verdict("A", 27, ""))[0] == "De: R$ 26,00 | Por: R$ 18,90 (27% OFF)"


# -- price_line_html (Telegram) ------------------------------------------------

def test_price_line_html_modo_a_risca_a_referencia_e_destaca_o_preco():
    offer = make_offer_ref(2600, price_current_cents=1890)
    assert pricing.price_line_html(offer, pricing.verdict(offer, 10)) == (
        "De: <s>R$ 26,00</s> | Por: <b>R$ 18,90</b> (27% OFF)", "")


def test_price_line_html_modo_b_o_preco_e_o_heroi_e_a_prova_social_e_texto_puro():
    offer = make_offer(price_current_cents=3390, rating=4.9, sales=30000)
    assert pricing.price_line_html(offer, NO_CLAIM) == (
        "<b>R$ 33,90</b>", "⭐ 4,9 · 30 mil vendidos")


def test_price_line_html_toma_a_mesma_decisao_que_price_line():
    casos = [
        (make_offer_ref(2600, price_current_cents=1890), 10),   # 27% >= 10: A
        (make_offer_ref(2600, price_current_cents=2500), 10),   # 3% < 10: B
        (make_offer_ref(2600, price_current_cents=2500), 3),    # 3% >= 3: A
        (make_offer_ref(2600, price_current_cents=3390), 0),    # acima da ref: B
        (make_offer(price_current_cents=3390, rating=4.9, sales=850), 0),   # sem ref: B
    ]
    for offer, minimo in casos:
        v = pricing.verdict(offer, minimo)
        preco_html, prova_html = pricing.price_line_html(offer, v)
        preco, prova = pricing.price_line(offer, v)
        sem_tags = preco_html
        for tag in ("<s>", "</s>", "<b>", "</b>"):
            sem_tags = sem_tags.replace(tag, "")
        assert sem_tags == preco
        assert prova_html == prova
        assert ("<s>" in preco_html) == ("OFF" in preco) == (v.mode == "A")


# -- record_observations -----------------------------------------------------

def test_record_observations_grava_o_preco_atual(tmp_path):
    db = StateDB(tmp_path / "s.db")
    offers = [make_offer(item_id="a", price_current_cents=2600),
              make_offer(item_id="b", source="meli", price_current_cents=7890)]
    pricing.record_observations(db, offers)
    assert db.price_history("shopee", "a", days=1) == [2600]
    assert db.price_history("meli", "b", days=1) == [7890]
    db.close()


# -- enrich_offers: precedência das 4 fontes ---------------------------------

def test_enrich_degrau_1_valor_ja_presente_vence(tmp_path):
    db = StateDB(tmp_path / "s.db")
    # Histórico próprio ACIMA do piso da oferta: o piso curado só cede a uma
    # observação nossa MAIS BARATA (ver os testes do Fix 3 abaixo).
    _seed_history(db, [9000] * 20)
    wl = _wl(price_refs={"123456": PriceRef(2000, 90, 1900)},
             price_floors={"123456": PriceFloor(1500, 90)})
    offer = make_offer(price_ref_cents=7890, price_p25_cents=7000, price_window_days=91,
                       price_floor_cents=3051, price_floor_window_days=365)
    (out,) = pricing.enrich_offers([offer], db, wl, CFG)
    assert out == offer
    db.close()


def test_enrich_degrau_2_watchlist_traz_ref_p25_e_janelas(tmp_path):
    db = StateDB(tmp_path / "s.db")
    _seed_history(db, [3000] * 20)          # acima do piso curado: não o baixa
    wl = _wl(price_refs={"123456": PriceRef(2590, 90, 2428)},
             price_floors={"123456": PriceFloor(1699, 196)})
    (out,) = pricing.enrich_offers([make_offer()], db, wl, CFG)
    assert (out.price_ref_cents, out.price_p25_cents, out.price_window_days) == (2590, 2428, 90)
    assert (out.price_floor_cents, out.price_floor_window_days) == (1699, 196)
    db.close()


def test_enrich_watchlist_sem_p25_nunca_da_modo_a(tmp_path):
    db = StateDB(tmp_path / "s.db")
    wl = _wl(price_refs={"123456": PriceRef(3000, 90)})       # formato antigo: sem p25
    (out,) = pricing.enrich_offers([make_offer(price_current_cents=2190)], db, wl, CFG)
    assert out.price_ref_cents == 3000 and out.price_p25_cents == 0
    assert out.real_discount_pct == 27
    assert pricing.verdict(out, 10) == NO_CLAIM                # conservador por construção
    db.close()


def test_enrich_degrau_3_mediana_p25_e_dias_distintos_do_price_log(tmp_path):
    db = StateDB(tmp_path / "s.db")
    _seed_history(db, [2600, 2600, 2700, 2500, 2600, 2400] + [2600] * 8)     # 14 dias
    (out,) = pricing.enrich_offers([make_offer()], db, None, CFG)
    assert out.price_ref_cents == 2600
    assert out.price_p25_cents == 2600
    assert out.price_window_days == 14
    assert out.price_floor_cents == 2400
    assert out.price_floor_window_days == 14
    db.close()


def test_enrich_degrau_3_exige_observacoes_minimas(tmp_path):
    db = StateDB(tmp_path / "s.db")
    _seed_history(db, [2600] * 13)  # 13 dias < ref_min_observations=14
    (out,) = pricing.enrich_offers([make_offer()], db, None, CFG)
    assert out.price_ref_cents == 0 and out.price_p25_cents == 0 and out.price_window_days == 0
    # o piso segue a mesma exigência: poucos dias observados não são "mínima
    # histórica" e não podem virar selo de menor preço.
    assert out.price_floor_cents == 0 and out.price_floor_window_days == 0
    db.close()


# -- Fix 3: o piso curado envelhece; a observação própria só pode BAIXÁ-LO ----

def test_enrich_piso_curado_cede_a_observacao_propria_mais_barata(tmp_path):
    # Piso curado 24000 (watchlist de meses atrás) e UMA observação nossa de
    # 23500: o selo "menor preço" só pode sair a <= 23500. Sem isto, o piso
    # envelhecia sem limite e o post carimbava "menor preço dos últimos 12
    # meses" num preço que nós mesmos já tínhamos visto mais barato.
    db = StateDB(tmp_path / "s.db")
    _seed_history(db, [23500])                       # um único dia observado
    wl = _wl(price_floors={"123456": PriceFloor(24000, 365)})
    (acima,) = pricing.enrich_offers([make_offer(price_current_cents=23600)], db, wl, CFG)
    assert acima.price_floor_cents == 23500
    assert acima.price_floor_window_days == 365      # a MAIOR das duas janelas
    assert pricing.verdict(acima, 10).seal == ""     # 23600 > 23500: sem selo
    (no_piso,) = pricing.enrich_offers([make_offer(price_current_cents=23500)], db, wl, CFG)
    assert pricing.verdict(no_piso, 10).seal == "🏷️ Menor preço dos últimos 12 meses (verificado)"
    db.close()


def test_enrich_piso_baixa_sem_exigir_ref_min_observations(tmp_path):
    # Um dia observado não vira PISO do nada (degrau 3 exige 14 dias), mas
    # BAIXA um piso curado: um preço que nós vimos existiu — negá-lo é que
    # seria invenção.
    db = StateDB(tmp_path / "s.db")
    _seed_history(db, [23500])
    (sem_curado,) = pricing.enrich_offers([make_offer()], db, None, CFG)
    assert sem_curado.price_floor_cents == 0         # nada curado, 1 dia < 14
    wl = _wl(price_floors={"123456": PriceFloor(24000, 365)})
    (com_curado,) = pricing.enrich_offers([make_offer()], db, wl, CFG)
    assert com_curado.price_floor_cents == 23500
    db.close()


def test_enrich_observacao_propria_nunca_sobe_o_piso(tmp_path):
    # O caminho é de mão única: histórico próprio mais CARO não afrouxa o selo
    # nem encurta a janela do piso curado.
    db = StateDB(tmp_path / "s.db")
    _seed_history(db, [30000] * 20)
    wl = _wl(price_floors={"123456": PriceFloor(24000, 365)})
    (out,) = pricing.enrich_offers([make_offer(price_current_cents=24000)], db, wl, CFG)
    assert (out.price_floor_cents, out.price_floor_window_days) == (24000, 365)
    assert pricing.verdict(out, 10).seal.endswith("últimos 12 meses (verificado)")
    db.close()


def test_enrich_piso_do_pool_do_ml_tambem_cede(tmp_path):
    # Degrau 1 (mínima histórica do pool do ML) segue a mesma regra: o preço
    # vivo que gravamos ontem é observação nossa e vale mais que a mínima do
    # JoomPulse — que pode ser de outro anúncio (o vencedor do buy box muda).
    db = StateDB(tmp_path / "s.db")
    _seed_history(db, [2900] * 3, source="meli", item_id="MLB1")
    offer = make_offer(source="meli", item_id="MLB1", price_current_cents=2900,
                       price_ref_cents=7890, price_p25_cents=3051, price_window_days=91,
                       price_floor_cents=3051, price_floor_window_days=365)
    (out,) = pricing.enrich_offers([offer], db, None, CFG)
    assert (out.price_floor_cents, out.price_floor_window_days) == (2900, 365)
    db.close()


def test_enrich_piso_sem_janela_curada_herda_a_janela_medida(tmp_path):
    # Piso curado sem janela (0) que a nossa observação baixa: a janela passa
    # a ser a nossa, medida — e aí o selo pode existir, dizendo N de verdade.
    db = StateDB(tmp_path / "s.db")
    _seed_history(db, [2000] * 5)
    wl = _wl(price_floors={"123456": PriceFloor(2500, 0)})
    (out,) = pricing.enrich_offers([make_offer(price_current_cents=2000)], db, wl, CFG)
    assert (out.price_floor_cents, out.price_floor_window_days) == (2000, 5)
    assert pricing.verdict(out, 10).seal == "🏷️ Menor preço dos últimos 5 dias (verificado)"
    db.close()


def test_enrich_default_de_ref_min_observations_e_14(tmp_path):
    db = StateDB(tmp_path / "s.db")
    _seed_history(db, [2600] * 13)
    (out,) = pricing.enrich_offers([make_offer()], db, None, {"selection": {}})
    assert out.price_ref_cents == 0
    _seed_history(db, [2600], start_days_ago=14)
    (out,) = pricing.enrich_offers([make_offer()], db, None, {"selection": {}})
    assert out.price_ref_cents == 2600 and out.price_window_days == 14
    db.close()


def test_enrich_honra_ref_min_observations_zero(tmp_path):
    # Config `0` virava o default em silêncio (`sel.get(k) or DEFAULT`):
    # ref_min_observations: 0 era lido como 5. Só o valor AUSENTE cai no default.
    db = StateDB(tmp_path / "s.db")
    _seed_history(db, [2600])  # um único dia observado
    cfg = {"selection": {"ref_window_days": 90, "ref_min_observations": 0,
                         "min_real_discount_pct": 10}}
    (out,) = pricing.enrich_offers([make_offer()], db, None, cfg)
    assert out.price_ref_cents == 2600 and out.price_window_days == 1
    assert out.price_floor_cents == 2600 and out.price_floor_window_days == 1
    db.close()


def test_setting_so_o_ausente_cai_no_default():
    assert pricing.setting({"x": 0}, "x", 10) == 0
    assert pricing.setting({"x": 0.0}, "x", 1.05) == 0.0
    assert pricing.setting({"x": None}, "x", 10) == 10
    assert pricing.setting({}, "x", 10) == 10
    assert pricing.setting({"x": 3}, "x", 10) == 3


def test_enrich_degrau_3_respeita_a_janela(tmp_path):
    db = StateDB(tmp_path / "s.db")
    _seed_history(db, [9999] * 20, start_days_ago=100)
    (out,) = pricing.enrich_offers([make_offer()], db, None, CFG)
    assert out.price_ref_cents == 0
    db.close()


def test_enrich_degrau_4_desconhecida(tmp_path):
    db = StateDB(tmp_path / "s.db")
    (out,) = pricing.enrich_offers([make_offer()], db, None, CFG)
    assert out == make_offer()
    db.close()


def test_enrich_nao_muta_a_oferta_original(tmp_path):
    db = StateDB(tmp_path / "s.db")
    wl = _wl(price_refs={"123456": PriceRef(2590, 90, 2400)})
    original = make_offer()
    (out,) = pricing.enrich_offers([original], db, wl, CFG)
    assert original.price_ref_cents == 0     # frozen: replace devolve outro objeto
    assert out is not original
    assert out.price_ref_cents == 2590
    db.close()


def test_enrich_usa_a_chave_por_fonte(tmp_path):
    # price_log é chaveado por (source, item_id): o histórico da Shopee não
    # pode virar referência de um item do ML com o mesmo id.
    db = StateDB(tmp_path / "s.db")
    _seed_history(db, [2600] * 20, source="shopee", item_id="123456")
    (out,) = pricing.enrich_offers([make_offer(source="meli")], db, None, CFG)
    assert out.price_ref_cents == 0
    db.close()


def test_format_sales_marca_a_faixa_do_mercado_livre():
    """O contador do ML é um BALDE, não uma contagem: o anúncio publica
    "+250 mil vendidos". Escrever "250 mil vendidos" seco afirma uma precisão
    que o dado não tem.

    Contexto (2026-08-27): o dono flagrou um story dizendo "5 mil vendidos"
    para um produto com 250 mil no anúncio. Eram DOIS erros somados — o número
    era a estimativa MENSAL do JoomPulse (`catalogOrderCount1m`) em vez do
    contador acumulado (`catalogSales`), e ainda vinha sem o "+" que diz que é
    um piso. Este teste guarda a segunda metade."""
    assert pricing.format_sales(250000, faixa=True) == "+250 mil vendidos"
    assert pricing.format_sales(250000) == "250 mil vendidos"
    # Shopee devolve contagem fina; nada de "+" ali.
    assert pricing.format_sales(33800) == "33 mil vendidos"
    assert pricing.format_sales(0, faixa=True) == ""


def test_format_sales_escreve_milhoes_por_extenso():
    """1.000.000 virava "1000 mil vendidos" — a creatina do pool tem 1 milhão."""
    assert pricing.format_sales(1_000_000, faixa=True) == "+1 milhão vendidos"
    assert pricing.format_sales(2_000_000, faixa=True) == "+2 milhões vendidos"
    assert pricing.format_sales(1_500_000) == "1,5 milhões vendidos"


def test_format_sales_diz_a_janela_que_o_numero_mede():
    """Fase 5H: o `sales` da Shopee é a janela de ~30 dias, não o total.

    Medido em 2026-08-28 contra o cubo `ShbMartItem` do JoomPulse: o lençol
    16692338189 tem `sales` 45.950 no nosso banco, `sold30Days` 50.000 e
    `sold1y` (o contador que o anúncio EXIBE) 2.000.000. Escrever "45 mil
    vendidos" seco para um anúncio que diz 2 milhões é a mesma contradição do
    protetor solar do ML, só que subestimando. O número não muda; o texto passa
    a dizer o que ele mede."""
    assert pricing.format_sales(45950, window_days=30) == "45 mil vendidos no último mês"
    # Janela 0 = contador vitalício: o texto do ML não muda uma vírgula.
    assert pricing.format_sales(250000, faixa=True) == "+250 mil vendidos"
    assert pricing.format_sales(250000, faixa=True, window_days=0) == "+250 mil vendidos"
    assert pricing.format_sales(45950) == "45 mil vendidos"
    # Zero continua sem texto, com ou sem janela.
    assert pricing.format_sales(0, window_days=30) == ""
    # Qualquer outra janela sai por extenso — nada de "mês" para 7 dias.
    assert pricing.format_sales(1200, window_days=7) == "1 mil vendidos nos últimos 7 dias"


def test_prova_social_diz_a_janela_da_oferta():
    """Quem passa a janela para o texto é a própria oferta: nenhum consumidor
    (Telegram, arte, legenda) precisa saber de que loja o número veio."""
    offer = make_offer(sales=45950, rating=4.9, sales_window_days=30)
    assert pricing.price_line(offer, NO_CLAIM)[1] == "⭐ 4,9 · 45 mil vendidos no último mês"
    assert pricing.price_line_html(offer, NO_CLAIM)[1] == (
        "⭐ 4,9 · 45 mil vendidos no último mês")
    vitalicio = make_offer(sales=250000, rating=4.9, sales_e_faixa=True)
    assert pricing.price_line(vitalicio, NO_CLAIM)[1] == "⭐ 4,9 · +250 mil vendidos"


# -- fase 5J (J2): a entrada SEM HISTÓRICO nunca alega desconto nem sela -----
#
# Estruturalmente isto já vale — `verdict` só dá modo A com janela >=
# MIN_WINDOW_DAYS e preço estritamente abaixo do p25, e com janela 0 é
# impossível. A trava existe assim mesmo porque é a ÚNICA coisa que separa esta
# fase de uma regressão na honestidade da conta: se uma entrada sem histórico
# conseguisse alegar desconto ou carimbar selo, o ativo do projeto ia junto.
# Por isso o teste sai do ARQUIVO do pool e vai até o veredito, como o pipeline
# faz — não parte de um `Offer` montado à mão com os campos convenientes.

def _oferta_sem_historico(tmp_path):
    from tests.test_meli import (SEM_HISTORICO, _no_network_handler, source_with,
                                 write_pool)
    pool = write_pool(tmp_path / "pool.json", [
        {"product_id": "MLB-NOVO", "title": "Creatina 500g", **SEM_HISTORICO,
         "image_url": "https://http2.mlstatic.com/x.jpg", "category": "MLB264586",
         "sales": 13337, "rating": 4.9}])
    src = source_with(_no_network_handler, tmp_path)
    (offer,) = src.fetch_offers({"meli": {"offers_path": str(pool)},
                                 "selection": {"price_min_brl": 20, "price_max_brl": 1000}})
    assert (offer.price_ref_cents, offer.price_p25_cents, offer.price_window_days) == (0, 0, 0)
    assert (offer.price_floor_cents, offer.price_floor_window_days) == (0, 0)
    return offer


def test_entrada_sem_historico_nunca_alega_desconto_nem_carimba_selo(tmp_path):
    """Qualquer que seja o preço vivo, e mesmo com o mínimo de desconto em 0
    (a régua mais permissiva que o config aceita): modo B, 0% e sem selo."""
    db = StateDB(tmp_path / "s.db")
    offer = _oferta_sem_historico(tmp_path)
    for minimo in (0, 10):
        for centavos in (1, 100, 2000, 3390, 50_000, 1_000_000):
            vivo = dataclasses.replace(offer, price_current_cents=centavos)
            (enriquecida,) = pricing.enrich_offers([vivo], db, None, CFG)
            veredito = pricing.verdict(enriquecida, minimo)
            assert veredito == Verdict("B", 0, "", 0), (minimo, centavos, veredito)
            # E o texto que sai dele não tem "De:" nem percentual nenhum.
            linha, _ = pricing.price_line(enriquecida, veredito)
            assert "De:" not in linha and "OFF" not in linha
    db.close()


def test_entrada_sem_historico_com_historico_curto_continua_em_modo_b(tmp_path):
    """Um dia antes da graduação ainda é modo B: `ref_min_observations` é
    exigência, não sugestão — com 13 dias a régua não existe."""
    db = StateDB(tmp_path / "s.db")
    offer = _oferta_sem_historico(tmp_path)
    _seed_history(db, [10000] * 7 + [9000] * 6, source="meli", item_id=offer.item_id)
    vivo = dataclasses.replace(offer, price_current_cents=8000)
    (enriquecida,) = pricing.enrich_offers([vivo], db, None, CFG)
    assert enriquecida.price_ref_cents == 0 and enriquecida.price_floor_cents == 0
    assert pricing.verdict(enriquecida, 10) == Verdict("B", 0, "", 0)
    db.close()


def test_depois_de_ref_min_observations_dias_a_mesma_entrada_ganha_regua(tmp_path):
    """A graduação que justifica a fase: a oferta que entrou sem régua ganha
    uma — das NOSSAS medições (degrau 3), não de uma consulta paga — e só então
    pode virar modo A. O selo vem junto e diz a janela que MEDIMOS."""
    db = StateDB(tmp_path / "s.db")
    offer = _oferta_sem_historico(tmp_path)
    _seed_history(db, [10000] * 7 + [9000] * 7, source="meli", item_id=offer.item_id)
    vivo = dataclasses.replace(offer, price_current_cents=8000)
    (enriquecida,) = pricing.enrich_offers([vivo], db, None, CFG)
    assert (enriquecida.price_ref_cents, enriquecida.price_p25_cents,
            enriquecida.price_window_days) == (9000, 9000, 14)
    veredito = pricing.verdict(enriquecida, 10)
    assert veredito.mode == "A"
    assert veredito.discount_pct == 11          # (9000-8000)/9000, para baixo
    assert veredito.seal == "🏷️ Menor preço dos últimos 14 dias (verificado)"
    assert pricing.price_line(enriquecida, veredito)[0] == (
        "De: R$ 90,00 | Por: R$ 80,00 (11% OFF)")
    db.close()

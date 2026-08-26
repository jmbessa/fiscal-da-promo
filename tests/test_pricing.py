from datetime import date, timedelta

from afiliado import pricing
from afiliado.state import StateDB
from afiliado.watchlist import PriceFloor, PriceRef, Watchlist
from tests.test_models import make_offer

CFG = {"selection": {"ref_window_days": 90, "ref_min_observations": 5,
                     "min_real_discount_pct": 10}}


def _wl(**kw) -> Watchlist:
    return Watchlist(generated_at=date.today(), valid_days=14, **kw)


def _seed_history(db: StateDB, cents: list[int], source="shopee", item_id="123456") -> None:
    hoje = date.today()
    for i, valor in enumerate(cents):
        db.record_price(source, item_id, valor, day=(hoje - timedelta(days=i)).isoformat())


# -- median_cents ------------------------------------------------------------

def test_median_cents_impar():
    assert pricing.median_cents([2600, 3390, 1890]) == 2600


def test_median_cents_par_tira_a_media_dos_dois_centrais():
    assert pricing.median_cents([1000, 2000, 3000, 5000]) == 2500
    # média inteira: 2500 e 2501 -> 2500 (arredondamento para baixo é
    # conservador: referência menor = menos desconto alegado)
    assert pricing.median_cents([2500, 2501]) == 2500


def test_median_cents_vazio():
    assert pricing.median_cents([]) == 0


# -- format_sales ------------------------------------------------------------

def test_format_sales():
    assert pricing.format_sales(30000) == "30 mil vendidos"
    assert pricing.format_sales(1000) == "1 mil vendidos"
    assert pricing.format_sales(850) == "850 vendidos"
    assert pricing.format_sales(1) == "1 vendidos"
    assert pricing.format_sales(0) == ""


# -- price_line --------------------------------------------------------------

def test_price_line_modo_a_desconto_verificado():
    offer = make_offer(price_ref_cents=2600, price_current_cents=1890)
    preco, prova = pricing.price_line(offer, 10)
    assert preco == "De: R$ 26,00 | Por: R$ 18,90 (27% OFF)"
    assert prova == ""


def test_price_line_modo_a_ignora_o_de_do_vendedor():
    # "de" inflado do vendedor (350,00) nunca aparece — só a NOSSA referência.
    offer = make_offer(price_original_cents=35000, price_ref_cents=2600,
                       price_current_cents=1890)
    preco, _ = pricing.price_line(offer, 10)
    assert "350,00" not in preco
    assert "R$ 26,00" in preco


def test_price_line_modo_b_sem_referencia():
    offer = make_offer(price_current_cents=3390, rating=4.9, sales=30000)
    preco, prova = pricing.price_line(offer, 10)
    assert preco == "R$ 33,90"
    assert prova == "⭐ 4,9 · 30 mil vendidos"


def test_price_line_modo_b_quando_o_desconto_fica_abaixo_do_minimo():
    # 2600 -> 2500 = 4% verificado, abaixo do mínimo de 10: não alega desconto.
    offer = make_offer(price_ref_cents=2600, price_current_cents=2500,
                       rating=0.0, sales=850)
    preco, prova = pricing.price_line(offer, 10)
    assert preco == "R$ 25,00"
    assert "OFF" not in preco
    assert prova == "850 vendidos"


def test_price_line_modo_b_sem_prova_social_conhecida():
    offer = make_offer(price_current_cents=3390, rating=0.0, sales=0)
    preco, prova = pricing.price_line(offer, 10)
    assert preco == "R$ 33,90"
    assert prova == ""


def test_price_line_modo_b_so_com_nota():
    offer = make_offer(price_current_cents=3390, rating=4.9, sales=0)
    assert pricing.price_line(offer, 10)[1] == "⭐ 4,9"


def test_price_line_nunca_inventa_desconto_com_preco_acima_da_referencia():
    offer = make_offer(price_ref_cents=2600, price_current_cents=3390)
    preco, _ = pricing.price_line(offer, 10)
    assert preco == "R$ 33,90"
    assert "OFF" not in preco


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
    _seed_history(db, [1000] * 6)
    wl = _wl(price_refs={"123456": PriceRef(2000, 90)},
             price_floors={"123456": PriceFloor(1500, 90)})
    offer = make_offer(price_ref_cents=7890, price_floor_cents=3051)
    (out,) = pricing.enrich_offers([offer], db, wl, CFG)
    assert out.price_ref_cents == 7890
    assert out.price_floor_cents == 3051
    db.close()


def test_enrich_degrau_2_watchlist(tmp_path):
    db = StateDB(tmp_path / "s.db")
    _seed_history(db, [1000] * 6)
    wl = _wl(price_refs={"123456": PriceRef(2590, 90)},
             price_floors={"123456": PriceFloor(1500, 90)})
    (out,) = pricing.enrich_offers([make_offer()], db, wl, CFG)
    assert out.price_ref_cents == 2590
    assert out.price_floor_cents == 1500
    db.close()


def test_enrich_degrau_3_mediana_do_price_log(tmp_path):
    db = StateDB(tmp_path / "s.db")
    _seed_history(db, [2600, 2600, 2700, 2500, 2600, 2400])
    (out,) = pricing.enrich_offers([make_offer()], db, None, CFG)
    assert out.price_ref_cents == 2600
    assert out.price_floor_cents == 2400
    db.close()


def test_enrich_degrau_3_exige_observacoes_minimas(tmp_path):
    db = StateDB(tmp_path / "s.db")
    _seed_history(db, [2600, 2500, 2400])  # 3 dias < ref_min_observations=5
    (out,) = pricing.enrich_offers([make_offer()], db, None, CFG)
    assert out.price_ref_cents == 0
    # o piso segue a mesma exigência: um único dia observado não é "mínima
    # histórica" e não pode virar selo de menor preço.
    assert out.price_floor_cents == 0
    db.close()


def test_enrich_degrau_3_respeita_a_janela(tmp_path):
    db = StateDB(tmp_path / "s.db")
    hoje = date.today()
    for i in range(6):
        db.record_price("shopee", "123456", 9999,
                        day=(hoje - timedelta(days=100 + i)).isoformat())
    (out,) = pricing.enrich_offers([make_offer()], db, None, CFG)
    assert out.price_ref_cents == 0
    db.close()


def test_enrich_degrau_4_desconhecida(tmp_path):
    db = StateDB(tmp_path / "s.db")
    (out,) = pricing.enrich_offers([make_offer()], db, None, CFG)
    assert out.price_ref_cents == 0
    assert out.price_floor_cents == 0
    db.close()


def test_enrich_nao_muta_a_oferta_original(tmp_path):
    db = StateDB(tmp_path / "s.db")
    wl = _wl(price_refs={"123456": PriceRef(2590, 90)})
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
    _seed_history(db, [2600] * 6, source="shopee", item_id="123456")
    (out,) = pricing.enrich_offers([make_offer(source="meli")], db, None, CFG)
    assert out.price_ref_cents == 0
    db.close()

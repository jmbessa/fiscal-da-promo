import json
from datetime import date

from afiliado.watchlist import PriceFloor, PriceRef, Watchlist, load_watchlist
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


def test_section_dates_datam_cada_secao(tmp_path):
    # 5O: semear a régua da Shopee NÃO revisa os `hot_items`. Com uma data só,
    # gravar `generated_at = hoje` afirmaria que os boosts foram revistos hoje.
    path = write_watchlist(tmp_path / "watchlist.json", {
        "generated_at": "2026-08-23",
        "valid_days": 14,
        "section_dates": {"price_refs": "2026-08-28", "price_floors": "2026-08-28"},
        "hot_items": {"11503789697": {"boost": 1.5, "reason": "escrita em 23/08"}},
        "price_refs": {"11503789697": {"ref_cents": 15291, "p25_cents": 12997,
                                       "window_days": 68}},
    })
    wl = load_watchlist(path)
    assert wl is not None
    assert wl.section_date("price_refs") == date(2026, 8, 28)
    assert wl.section_date("hot_items") == date(2026, 8, 23)
    assert wl.generated_at == date(2026, 8, 23)
    # A régua nova não rejuvenesce a opinião: a validade continua a do dia 23.
    assert wl.days_old(date(2026, 8, 30)) == 7


def test_arquivo_antigo_sem_section_dates_herda_generated_at(tmp_path):
    # O arquivo em produção (23 pisos e 23 hot_items de 2026-08-23) não tem
    # `section_dates` — toda seção herda a data do arquivo, como antes.
    path = write_watchlist(tmp_path / "watchlist.json", {
        "generated_at": "2026-08-23",
        "valid_days": 14,
        "category_boosts": {"100630": 1.3},
        "hot_items": {"22991771385": {"boost": 1.5, "reason": "trend +3686%"}},
        "price_floors": {"22991771385": {"min_price_cents": 3500, "window_days": 191}},
    })
    wl = load_watchlist(path)
    assert wl is not None
    assert wl.section_dates == {}
    for secao in ("category_boosts", "hot_items", "price_floors", "price_refs"):
        assert wl.section_date(secao) == date(2026, 8, 23)
    assert wl.price_floors == {"22991771385": PriceFloor(3500, 191)}
    assert wl.days_old(date(2026, 8, 30)) == 7
    assert wl.is_stale(date(2026, 9, 7)) is True


def test_is_stale_mede_a_opiniao_mais_velha():
    # `is_stale` fecha os BOOSTS (opinião da semana); refs e pisos são fatos
    # datados que `facts_only` mantém. Quem manda é a opinião mais velha.
    wl = Watchlist(generated_at=date(2026, 8, 1), valid_days=14,
                   section_dates={"category_boosts": date(2026, 8, 20),
                                  "hot_items": date(2026, 8, 20),
                                  "price_refs": date(2026, 8, 28)})
    assert wl.days_old(date(2026, 8, 26)) == 6
    assert wl.is_stale(date(2026, 8, 26)) is False

    meio_velha = Watchlist(generated_at=date(2026, 8, 1), valid_days=14,
                           section_dates={"category_boosts": date(2026, 8, 20),
                                          "hot_items": date(2026, 8, 1)})
    assert meio_velha.days_old(date(2026, 8, 26)) == 25
    assert meio_velha.is_stale(date(2026, 8, 26)) is True


def test_measured_at_por_entrada(tmp_path):
    # Onda a onda, a seção inteira não tem uma data só: a entrada que traz a
    # sua manda, e quem não traz herda a da seção.
    path = write_watchlist(tmp_path / "watchlist.json", {
        "generated_at": "2026-08-23",
        "section_dates": {"price_refs": "2026-08-29", "price_floors": "2026-08-29"},
        "price_refs": {
            "a": {"ref_cents": 15291, "p25_cents": 12997, "window_days": 68,
                  "measured_at": "2026-08-28"},
            "b": {"ref_cents": 19990, "p25_cents": 15991, "window_days": 42}},
        "price_floors": {"a": {"min_price_cents": 12997, "window_days": 68,
                               "measured_at": "2026-08-28"}},
    })
    wl = load_watchlist(path)
    assert wl is not None
    assert wl.price_ref("a").measured_at == date(2026, 8, 28)
    assert wl.price_ref("b").measured_at is None           # herda a seção
    assert wl.price_floor("a").measured_at == date(2026, 8, 28)
    assert wl.section_date("price_refs") == date(2026, 8, 29)


def test_datas_invalidas_degradam_sem_derrubar_o_arquivo(tmp_path):
    path = write_watchlist(tmp_path / "secao.json", {
        "generated_at": "2026-08-23",
        "section_dates": {"price_refs": "ontem", "hot_items": "2026-08-25"},
        "price_refs": {"a": {"ref_cents": 15291, "p25_cents": 12997,
                             "window_days": 68, "measured_at": "amanhã"}},
    })
    wl = load_watchlist(path)
    assert wl is not None
    assert wl.section_date("price_refs") == date(2026, 8, 23)   # volta ao arquivo
    assert wl.section_date("hot_items") == date(2026, 8, 25)
    assert wl.price_ref("a") == PriceRef(15291, 68, 12997)      # sem measured_at

    path = write_watchlist(tmp_path / "lista.json", {
        "generated_at": "2026-08-23", "section_dates": [1, 2, 3],
        "category_boosts": {"100630": 1.3},
    })
    wl = load_watchlist(path)
    assert wl is not None
    assert wl.section_dates == {}
    assert wl.category_boosts == {"100630": 1.3}


def test_load_watchlist_price_refs_com_p25(tmp_path):
    # Formato da 5B: {"ref_cents", "p25_cents", "window_days"}.
    path = write_watchlist(tmp_path / "watchlist.json", {
        "generated_at": "2026-08-23",
        "price_refs": {"9212570285": {"ref_cents": 2590, "p25_cents": 2428, "window_days": 90}},
    })
    wl = load_watchlist(path)
    assert wl is not None
    assert wl.price_refs == {"9212570285": PriceRef(2590, 90, 2428)}
    assert wl.price_ref("9212570285").p25_cents == 2428
    assert wl.price_ref("nao-existe") is None


def test_load_watchlist_price_refs_sem_p25_carrega_zero(tmp_path):
    # Entrada antiga (sem p25) carrega p25_cents = 0 -> nunca modo A.
    path = write_watchlist(tmp_path / "watchlist.json", {
        "generated_at": "2026-08-23",
        "price_refs": {"9212570285": {"ref_cents": 2590, "window_days": 90}},
    })
    wl = load_watchlist(path)
    assert wl.price_refs == {"9212570285": PriceRef(2590, 90, 0)}
    assert wl.price_ref("9212570285").p25_cents == 0


def test_load_watchlist_sem_window_days_carrega_zero(tmp_path):
    # Janela ausente vale 0, não 365/90: o texto do selo diz "últimos N dias"
    # e a regra do quartil exige >= 14 dias MEDIDOS — um default silencioso
    # inventava a janela (365 dias de mínima a partir de um arquivo que não
    # disse nada). Com 0, nem selo nem modo A disparam.
    path = write_watchlist(tmp_path / "watchlist.json", {
        "generated_at": "2026-08-23",
        "price_floors": {"a": {"min_price_cents": 4999}},
        "price_refs": {"b": {"ref_cents": 2590, "p25_cents": 2428}},
    })
    wl = load_watchlist(path)
    assert wl.price_floors == {"a": PriceFloor(4999, 0)}
    assert wl.price_refs == {"b": PriceRef(2590, 0, 2428)}


def test_facts_only_mantem_refs_e_pisos_e_zera_boosts():
    # C11: watchlist vencida perde só os boosts; referências e pisos são
    # fatos datados e continuam na régua.
    wl = Watchlist(generated_at=date(2026, 8, 1), valid_days=14,
                   category_boosts={"100630": 1.3}, hot_items={"123456": 1.5},
                   price_refs={"123456": PriceRef(2590, 90, 2428)},
                   price_floors={"123456": PriceFloor(1699, 196)})
    fatos = wl.facts_only()
    assert fatos.category_boosts == {} and fatos.hot_items == {}
    assert fatos.boost_for(make_offer(item_id="123456", category="100630")) == 1.0
    assert fatos.price_refs == wl.price_refs and fatos.price_floors == wl.price_floors
    assert fatos.generated_at == wl.generated_at and fatos.is_stale(date(2026, 8, 20))
    assert wl.hot_items == {"123456": 1.5}                 # a original não muda


def test_load_watchlist_price_refs_malformada_degrada(tmp_path):
    path = write_watchlist(tmp_path / "refs_lista.json", {
        "generated_at": "2026-08-23",
        "price_refs": [1, 2, 3],
        "category_boosts": {"100630": 1.3},
    })
    wl = load_watchlist(path)
    assert wl is not None
    assert wl.price_refs == {}
    assert wl.category_boosts == {"100630": 1.3}

    path = write_watchlist(tmp_path / "refs_item_invalido.json", {
        "generated_at": "2026-08-23",
        "price_refs": {"bom": {"ref_cents": 2590}, "ruim": "nao é dict"},
    })
    wl = load_watchlist(path)
    assert wl is not None
    assert wl.price_refs == {"bom": PriceRef(2590, 0)}      # sem window_days: 0


def test_watchlist_sem_price_refs_fica_vazia():
    wl = Watchlist(generated_at=date(2026, 8, 1), valid_days=14)
    assert wl.price_refs == {}
    assert wl.price_ref("x") is None


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


def test_load_watchlist_wrong_container_shapes(tmp_path):
    # Uma seção com formato inesperado degrada para vazio; o resto do arquivo
    # (generated_at, valid_days, e as demais seções válidas) continua utilizável.
    path = write_watchlist(tmp_path / "hot_items_str.json", {
        "generated_at": "2026-08-23",
        "valid_days": 14,
        "category_boosts": {"100630": 1.3},
        "hot_items": "a string value",
    })
    wl = load_watchlist(path)
    assert wl is not None
    assert wl.hot_items == {}
    assert wl.category_boosts == {"100630": 1.3}

    path = write_watchlist(tmp_path / "price_floors_list.json", {
        "generated_at": "2026-08-23",
        "valid_days": 14,
        "price_floors": [1, 2, 3],
    })
    wl = load_watchlist(path)
    assert wl is not None
    assert wl.price_floors == {}

    path = write_watchlist(tmp_path / "category_boosts_str.json", {
        "generated_at": "2026-08-23",
        "valid_days": 14,
        "category_boosts": "oops",
    })
    wl = load_watchlist(path)
    assert wl is not None
    assert wl.category_boosts == {}

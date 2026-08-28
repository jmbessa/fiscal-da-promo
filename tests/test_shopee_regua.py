"""Fase 5O — a régua da Shopee semeada a partir dos INTERVALOS do JoomPulse.

Nenhum teste toca a rede nem o conector: as linhas do cubo entram como o JSON
que ele devolve, gravado à mão aqui.
"""

import json
from datetime import date

import pytest

from afiliado import pricing, shopee_regua
from afiliado.state import StateDB
from afiliado.watchlist import PriceFloor, PriceRef, Watchlist, load_watchlist
from tests.test_models import make_offer

HOJE = date(2026, 8, 28)


def linha(item_id: str, inicio: str, fim: str | None, preco) -> dict:
    """Uma linha crua do cubo `ShbModelsPricesDaily`, com o prefixo e tudo."""
    return {"ShbModelsPricesDaily.itemId": item_id,
            "ShbModelsPricesDaily.modelId": 0,
            "ShbModelsPricesDaily.modelPrice": preco,
            "ShbModelsPricesDaily.priceStart": inicio,
            "ShbModelsPricesDaily.priceEnd": fim}


def intervalo(inicio: str, fim: str | None, centavos: int):
    return (date.fromisoformat(inicio),
            date.fromisoformat(fim) if fim else None, centavos)


# ---------------------------------------------------------------- O1: expansão

def test_intervalo_vale_os_dias_que_cobre():
    dias = shopee_regua.dias_observados(
        [intervalo("2026-08-20", "2026-08-22", 12997)], HOJE)
    assert dias == {date(2026, 8, 20): 12997, date(2026, 8, 21): 12997,
                    date(2026, 8, 22): 12997}


def test_dia_sem_linha_nao_entra_na_janela():
    # O buraco entre 22/08 e 26/08 é dia NÃO OBSERVADO: a janela mede 5 dias,
    # não os 9 do intervalo entre as pontas. A janela é o que o selo publica
    # ("menor preço dos últimos N dias") — inflá-la é mentir sobre a medição.
    dias = shopee_regua.dias_observados(
        [intervalo("2026-08-20", "2026-08-22", 12997),
         intervalo("2026-08-26", "2026-08-28", 15291)], HOJE)
    assert len(dias) == 6
    assert date(2026, 8, 24) not in dias


def test_recorta_na_borda_da_janela_antes_de_contar():
    # Intervalo que começa antes da janela de 90 dias e outro que "termina"
    # depois de hoje: os dois entram só pela parte medida.
    dias = shopee_regua.dias_observados(
        [intervalo("2026-01-01", "2026-06-10", 9900),        # janela começa 31/05
         intervalo("2026-08-27", "2026-12-31", 15291)], HOJE, janela_dias=90)
    assert min(dias) == date(2026, 5, 31)
    assert max(dias) == HOJE
    assert dias[date(2026, 5, 31)] == 9900
    assert set(dias) & {date(2026, 5, 30), date(2026, 8, 29)} == set()


def test_intervalo_sem_fim_conta_so_o_dia_de_inicio():
    # Sem `priceEnd` a linha prova UM dia. Esticá-la até hoje inventaria
    # observação — e é justamente a janela que o selo publica.
    dias = shopee_regua.dias_observados(
        [intervalo("2026-08-20", None, 12997)], HOJE)
    assert dias == {date(2026, 8, 20): 12997}


def test_intervalo_invertido_ou_fora_da_janela_e_ignorado():
    assert shopee_regua.dias_observados(
        [intervalo("2026-08-22", "2026-08-20", 12997)], HOJE) == {}
    assert shopee_regua.dias_observados(
        [intervalo("2025-01-01", "2025-02-01", 12997)], HOJE) == {}


def test_sobreposicao_fica_com_o_mais_barato():
    # Dois intervalos cobrindo o mesmo dia não deveriam existir; se
    # existirem, o dia vale o MENOR preço — a escolha conservadora nos três
    # números (mediana e p25 menores autorizam menos desconto; piso menor
    # dificulta o selo).
    dias = shopee_regua.dias_observados(
        [intervalo("2026-08-20", "2026-08-21", 15291),
         intervalo("2026-08-21", "2026-08-22", 12997)], HOJE)
    assert dias[date(2026, 8, 21)] == 12997


def test_centavos_com_decimal_para_baixo():
    assert shopee_regua.centavos(129.97) == 12997
    assert shopee_regua.centavos("152.91") == 15291
    assert shopee_regua.centavos("15.999") == 1599      # nunca arredonda para cima
    assert shopee_regua.centavos(410.01) == 41001       # o float 41000.99999 não passa
    assert shopee_regua.centavos(0) is None
    assert shopee_regua.centavos(-5) is None
    assert shopee_regua.centavos(None) is None
    assert shopee_regua.centavos("grátis") is None


# ------------------------------------------------------------------ O2: guardas

def test_regua_reproduz_a_conta_medida_a_mao():
    # Os números que o dono mediu à mão em 11503789697 (janela de 90 dias):
    # 68 dias observados, mediana 15291, p25 12997, mínima 12997. Os dois
    # intervalos abaixo são a forma mais simples que os produz.
    regua, motivo = shopee_regua.regua_do_item(
        "11503789697",
        [intervalo("2026-06-22", "2026-07-09", 12997),
         intervalo("2026-07-10", "2026-08-28", 15291)], HOJE)
    assert motivo == ""
    assert regua.window_days == 68
    assert (regua.ref_cents, regua.p25_cents, regua.min_cents) == (15291, 12997, 12997)
    assert regua.medido_em == HOJE
    assert regua.price_ref() == PriceRef(15291, 68, 12997, HOJE)
    assert regua.price_floor() == PriceFloor(12997, 68, HOJE)


def test_regua_usa_a_mediana_e_o_p25_do_pricing():
    # Não reimplementar mediana nem percentil: os três números saem da
    # expansão em dias passada por `pricing.median_cents`/`p25_cents`.
    dias = shopee_regua.dias_observados(
        [intervalo("2026-06-01", "2026-08-28", 15291)], HOJE)
    precos = list(dias.values())
    regua, _ = shopee_regua.regua_do_item(
        "x", [intervalo("2026-06-01", "2026-08-28", 15291)], HOJE)
    assert regua.ref_cents == pricing.median_cents(precos)
    assert regua.p25_cents == pricing.p25_cents(precos)


def test_janela_curta_nao_vira_entrada():
    # Régua curta é PIOR que régua ausente: ela autoriza alegação. O mínimo é
    # o mesmo `pricing.MIN_WINDOW_DAYS` da regra do quartil.
    curto = [intervalo("2026-08-16", "2026-08-28", 12997)]      # 13 dias
    regua, motivo = shopee_regua.regua_do_item("x", curto, HOJE)
    assert regua is None
    assert "13" in motivo and str(pricing.MIN_WINDOW_DAYS) in motivo
    # um dia a mais e ela existe
    ok, motivo = shopee_regua.regua_do_item(
        "x", [intervalo("2026-08-15", "2026-08-28", 12997)], HOJE)
    assert motivo == "" and ok.window_days == pricing.MIN_WINDOW_DAYS


def test_item_sem_linhas_no_cubo_nao_gera_entrada():
    # Silêncio, não zero: sem linhas a entrada não existe.
    regua, motivo = shopee_regua.regua_do_item("x", [], HOJE)
    assert regua is None and "sem linha" in motivo


def test_minima_nunca_fica_acima_do_p25(monkeypatch):
    # Pela construção (a mínima sai da MESMA expansão) isto não acontece; a
    # guarda existe porque mínima alta demais vira selo inventado — é a mesma
    # regra que o leitor do pool do ML aplica.
    monkeypatch.setattr(shopee_regua.pricing, "p25_cents", lambda _: 1)
    regua, motivo = shopee_regua.regua_do_item(
        "x", [intervalo("2026-06-01", "2026-08-28", 15291)], HOJE)
    assert regua is None and motivo == "mínima acima do p25"


def test_p25_nunca_fica_acima_da_referencia(monkeypatch):
    monkeypatch.setattr(shopee_regua.pricing, "median_cents", lambda _: 1)
    regua, motivo = shopee_regua.regua_do_item(
        "x", [intervalo("2026-06-01", "2026-08-28", 15291)], HOJE)
    assert regua is None and motivo == "p25 acima da referência"


# ------------------------------------------------------- as linhas cruas do cubo

def test_reguas_do_bruto_agrupa_por_item_e_diz_quem_caiu():
    bruto = {"data": [
        linha("11503789697", "2026-06-22", "2026-07-09", 129.97),
        linha("11503789697", "2026-07-10", "2026-08-28", 152.91),
        linha("22493114640", "2026-08-20", "2026-08-28", 410.01),      # 9 dias
        linha("lixo", "não é data", "2026-08-28", 10.0),
    ]}
    reguas, recusados = shopee_regua.reguas_do_bruto([bruto], HOJE)
    assert set(reguas) == {"11503789697"}
    assert reguas["11503789697"].window_days == 68
    assert "9 dia" in recusados["22493114640"]
    assert "sem linha" in recusados["lixo"]


def test_ultimo_item_de_pagina_cheia_e_recusado():
    # O cubo devolve no máximo `limit` linhas e o grão é o INTERVALO: com ~4
    # itens por consulta, a página cheia é o caso NORMAL, e nela o último item
    # está cortado no meio da série — faltam justamente os intervalos mais
    # recentes. Uma régua feita disso mede uma janela que não existiu.
    bruto = {"query": {"limit": 3}, "data": [
        linha("inteiro", "2026-06-01", "2026-07-15", 152.91),
        linha("inteiro", "2026-07-16", "2026-08-28", 129.97),
        linha("cortado", "2026-06-01", "2026-08-28", 410.01),
    ]}
    reguas, recusados = shopee_regua.reguas_do_bruto([bruto], HOJE)
    assert set(reguas) == {"inteiro"}
    assert "cortad" in recusados["cortado"]


def test_pagina_seguinte_absolve_o_item_cortado():
    cheia = {"query": {"limit": 2}, "data": [
        linha("a", "2026-06-01", "2026-08-28", 152.91),
        linha("b", "2026-06-01", "2026-07-15", 410.01),
    ]}
    resto = {"query": {"limit": 2}, "data": [
        linha("b", "2026-07-16", "2026-08-28", 380.00),
    ]}
    reguas, recusados = shopee_regua.reguas_do_bruto([cheia, resto], HOJE)
    assert recusados == {}
    assert reguas["b"].window_days == 89
    assert reguas["b"].min_cents == 38000


def test_reguas_do_bruto_aceita_lista_e_dict_e_chave_sem_prefixo():
    lista = [linha("a", "2026-06-01", "2026-08-28", 152.91)]
    sem_prefixo = [{"itemId": "b", "modelPrice": 152.91,
                    "priceStart": "2026-06-01T00:00:00.000",
                    "priceEnd": "2026-08-28T00:00:00.000"}]
    reguas, _ = shopee_regua.reguas_do_bruto([lista, sem_prefixo], HOJE)
    assert set(reguas) == {"a", "b"}
    assert reguas["a"].window_days == reguas["b"].window_days == 89


# ------------------------------------------------------- O3: gravar sem mentir

def watchlist_antiga() -> dict:
    return {
        "generated_at": "2026-08-23",
        "valid_days": 14,
        "category_boosts": {"100630": 1.3},
        "hot_items": {"22991771385": {"boost": 1.5, "reason": "trend +3686%"}},
        "price_floors": {"22991771385": {"min_price_cents": 3500, "window_days": 191}},
    }


def test_mesclar_nao_rejuvenesce_a_opiniao():
    regua, _ = shopee_regua.regua_do_item(
        "11503789697", [intervalo("2026-06-22", "2026-08-28", 15291)], HOJE)
    novo = shopee_regua.mesclar(watchlist_antiga(), {"11503789697": regua}, HOJE)
    assert novo["generated_at"] == "2026-08-23"          # os boosts são de 23/08
    assert novo["hot_items"] == watchlist_antiga()["hot_items"]
    assert novo["section_dates"]["price_refs"] == "2026-08-28"
    assert "hot_items" not in novo.get("section_dates", {})


def test_mesclar_preserva_o_piso_antigo_com_a_data_dele():
    regua, _ = shopee_regua.regua_do_item(
        "11503789697", [intervalo("2026-06-22", "2026-08-28", 15291)], HOJE)
    novo = shopee_regua.mesclar(watchlist_antiga(), {"11503789697": regua}, HOJE)
    antigo = novo["price_floors"]["22991771385"]
    assert antigo["min_price_cents"] == 3500 and antigo["window_days"] == 191
    # a seção passa a dizer 28/08; a entrada antiga carimba a SUA data para não
    # ser lida como medida hoje.
    assert antigo["measured_at"] == "2026-08-23"
    assert novo["price_floors"]["11503789697"]["measured_at"] == "2026-08-28"


def test_mesclar_e_load_watchlist_fecham_o_ciclo(tmp_path):
    regua, _ = shopee_regua.regua_do_item(
        "11503789697", [intervalo("2026-06-22", "2026-08-28", 15291)], HOJE)
    caminho = tmp_path / "watchlist.json"
    shopee_regua.escrever(caminho, shopee_regua.mesclar(
        watchlist_antiga(), {"11503789697": regua}, HOJE))
    wl = load_watchlist(caminho)
    assert wl is not None
    assert wl.price_ref("11503789697").window_days == 68
    assert wl.price_floor("11503789697").min_price_cents == regua.min_cents
    assert wl.price_floor("22991771385") == PriceFloor(3500, 191, date(2026, 8, 23))
    assert wl.hot_items == {"22991771385": 1.5}
    assert wl.days_old(date(2026, 8, 30)) == 7           # a opinião continua de 23/08


def test_mesclar_duas_ondas_data_cada_entrada():
    primeira, _ = shopee_regua.regua_do_item(
        "a", [intervalo("2026-06-22", "2026-08-28", 15291)], HOJE)
    depois = date(2026, 9, 4)
    segunda, _ = shopee_regua.regua_do_item(
        "b", [intervalo("2026-06-29", "2026-09-04", 15291)], depois)
    onda1 = shopee_regua.mesclar(watchlist_antiga(), {"a": primeira}, HOJE)
    onda2 = shopee_regua.mesclar(onda1, {"b": segunda}, depois)
    assert onda2["price_refs"]["a"]["measured_at"] == "2026-08-28"
    assert onda2["price_refs"]["b"]["measured_at"] == "2026-09-04"
    assert onda2["section_dates"]["price_refs"] == "2026-09-04"


# ------------------------------------------------------------- quais itens semear

CFG = {
    "selection": {"posts_per_run": 2, "price_min_brl": 20, "price_max_brl": 1000,
                  "dedupe_days": 30, "category_ids": [], "max_above_ref": 1.00,
                  "require_price_ref": False, "min_real_discount_pct": 10,
                  "ev_weights": {"popularity": 0.3, "discount": 0.5}},
    "shopee": {"candidate_max_age_days": 3},
    "state": {"path": "data/state.db"},
}


def test_alvos_ordena_por_ev_e_pula_quem_ja_tem_regua(tmp_path):
    db = StateDB(tmp_path / "s.db")
    db.upsert_candidates([
        make_offer(item_id="pouco", sales=10, price_current_cents=3000),
        make_offer(item_id="muito", sales=90_000, price_current_cents=3000),
        make_offer(item_id="ja_tem", sales=50_000, price_current_cents=3000),
    ])
    wl = Watchlist(generated_at=date(2026, 8, 23), valid_days=14,
                   price_refs={"ja_tem": PriceRef(3000, 68, 2800)})
    ids = shopee_regua.alvos(db, CFG, wl, n=5)
    assert ids == ["muito", "pouco"]
    assert shopee_regua.alvos(db, CFG, wl, n=1) == ["muito"]
    db.close()


def test_alvos_sem_watchlist_devolve_o_estoque_inteiro(tmp_path):
    db = StateDB(tmp_path / "s.db")
    db.upsert_candidates([make_offer(item_id="a", price_current_cents=3000)])
    assert shopee_regua.alvos(db, CFG, None, n=5) == ["a"]
    db.close()


# ------------------------------------------------------------------------- CLI

def bruto_em_disco(tmp_path) -> str:
    caminho = tmp_path / "bruto" / "3_lote0.json"
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(json.dumps({"data": [
        linha("11503789697", "2026-06-22", "2026-07-09", 129.97),
        linha("11503789697", "2026-07-10", "2026-08-28", 152.91),
        linha("22493114640", "2026-08-20", "2026-08-28", 410.01),
    ]}), encoding="utf-8")
    return str(caminho.parent)


def test_main_semeia_a_partir_do_bruto(tmp_path, capsys):
    watchlist = tmp_path / "watchlist.json"
    watchlist.write_text(json.dumps(watchlist_antiga()), encoding="utf-8")
    codigo = shopee_regua.main(["semear", bruto_em_disco(tmp_path),
                                "--watchlist", str(watchlist), "--hoje", "2026-08-28"])
    saida = capsys.readouterr().out
    assert codigo == 0
    wl = load_watchlist(watchlist)
    assert wl.price_ref("11503789697") == PriceRef(15291, 68, 12997, HOJE)
    assert wl.price_ref("22493114640") is None
    assert "11503789697" in saida and "22493114640" in saida


def test_main_dry_run_nao_grava(tmp_path, capsys):
    watchlist = tmp_path / "watchlist.json"
    watchlist.write_text(json.dumps(watchlist_antiga()), encoding="utf-8")
    antes = watchlist.read_text(encoding="utf-8")
    assert shopee_regua.main(["semear", bruto_em_disco(tmp_path), "--watchlist",
                              str(watchlist), "--hoje", "2026-08-28", "--dry-run"]) == 0
    assert watchlist.read_text(encoding="utf-8") == antes
    assert "68" in capsys.readouterr().out


def test_main_sem_regua_aceita_nao_grava_nada(tmp_path, capsys):
    watchlist = tmp_path / "watchlist.json"
    watchlist.write_text(json.dumps(watchlist_antiga()), encoding="utf-8")
    antes = watchlist.read_text(encoding="utf-8")
    vazio = tmp_path / "vazio"
    vazio.mkdir()
    (vazio / "3_lote0.json").write_text(json.dumps({"data": [
        linha("22493114640", "2026-08-20", "2026-08-28", 410.01)]}), encoding="utf-8")
    assert shopee_regua.main(["semear", str(vazio), "--watchlist", str(watchlist),
                              "--hoje", "2026-08-28"]) == 0
    assert watchlist.read_text(encoding="utf-8") == antes
    assert "nenhuma régua" in capsys.readouterr().out


def test_main_watchlist_inexistente_e_erro(tmp_path):
    with pytest.raises(SystemExit):
        shopee_regua.main(["semear", bruto_em_disco(tmp_path),
                           "--watchlist", str(tmp_path / "nao-existe.json")])

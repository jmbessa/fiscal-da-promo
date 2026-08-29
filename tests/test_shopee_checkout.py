"""Fase 5R — o preço EXIBIDO da Shopee, vindo do cubo `ShbMartItem`.

Nenhum teste toca a rede nem o conector: as linhas do cubo entram como o JSON
colunar que ele devolve, gravado à mão aqui.
"""

import json
from datetime import date

import pytest

from afiliado import shopee_checkout
from afiliado.state import StateDB
from afiliado.watchlist import CheckoutPrice, Watchlist, load_watchlist
from tests.test_models import make_offer

HOJE = date(2026, 8, 29)


def bruto(*linhas) -> dict:
    """Uma resposta colunar do conector, com as três colunas que a coleta pede."""
    return {"columns": ["itemId", "itemLastSeenDate", "price"],
            "data": [list(l) for l in linhas],
            "dimensionCount": 2, "totalRows": len(linhas),
            "types": ["number", "time", "number"]}


# ------------------------------------------------------- R1: ler as linhas do cubo

def test_uma_linha_por_item_vira_um_preco_com_a_data_da_raspagem():
    precos, recusados = shopee_checkout.precos_do_bruto(
        [bruto((23598844177, "2026-08-28T16:36:34.864", 523.48))], HOJE)
    assert recusados == {}
    assert precos["23598844177"] == shopee_checkout.PrecoDeCheckout(
        "23598844177", 52348, date(2026, 8, 28))


def test_centavos_sempre_para_baixo():
    precos, _ = shopee_checkout.precos_do_bruto(
        [bruto((1, "2026-08-28T00:00:00.000", 90.559))], HOJE)
    assert precos["1"].price_cents == 9055


def test_sem_a_data_da_raspagem_nao_ha_entrada():
    """A idade do dado é metade da guarda. Sem `itemLastSeenDate` não há como
    dizer quantos dias o preço tem, e um preço sem idade seria apresentado como
    se fosse de hoje — o erro do `buy_box_item_id` com outra roupa."""
    _, recusados = shopee_checkout.precos_do_bruto(
        [bruto((1, None, 90.55), (2, "não é data", 90.55))], HOJE)
    assert "idade" in recusados["1"] and "idade" in recusados["2"]


def test_preco_zero_negativo_ou_ilegivel_nao_vira_entrada():
    _, recusados = shopee_checkout.precos_do_bruto(
        [bruto((1, "2026-08-28", 0), (2, "2026-08-28", -3), (3, "2026-08-28", "abc"))], HOJE)
    assert set(recusados) == {"1", "2", "3"}
    assert all("preço" in m for m in recusados.values())


def test_raspagem_no_futuro_e_recusada():
    _, recusados = shopee_checkout.precos_do_bruto(
        [bruto((1, "2026-08-30", 90.55))], HOJE)
    assert "futuro" in recusados["1"]


def test_linha_sem_item_e_ignorada_sem_derrubar_as_outras():
    precos, recusados = shopee_checkout.precos_do_bruto(
        [bruto((None, "2026-08-28", 90.55), (2, "2026-08-28", 90.55))], HOJE)
    assert set(precos) == {"2"} and recusados == {}


def test_pagina_cheia_nao_corta_item_nenhum():
    """Ao contrário do cubo de histórico (5O), o grão aqui é o ITEM: a página
    cheia significa que sobraram itens para a próxima consulta, não que o
    último veio pela metade. `pagina_cheia` avisa quem coleta; nenhuma entrada
    é recusada por isso."""
    cheia = {"query": {"limit": 2}, **bruto((1, "2026-08-28", 10.0),
                                            (2, "2026-08-28", 20.0))}
    precos, recusados = shopee_checkout.precos_do_bruto([cheia], HOJE)
    assert set(precos) == {"1", "2"} and recusados == {}
    assert shopee_checkout.pagina_cheia([cheia]) is True
    assert shopee_checkout.pagina_cheia([bruto((1, "2026-08-28", 10.0))]) is False


# ------------------------------------------------------------ R2: falhar fechado

def entrada(preco_cents: int, medido_em: str = "2026-08-29") -> CheckoutPrice:
    return CheckoutPrice(preco_cents, date.fromisoformat(medido_em))


def aplica(offer, entrada_, hoje=HOJE, **kw):
    return shopee_checkout.aplica(offer, entrada_, hoje, **kw)


def test_o_preco_do_cubo_carimba_a_oferta_com_a_condicao():
    offer = make_offer(price_current_cents=59900)
    novo, motivo = aplica(offer, entrada(52348, "2026-08-28"))
    assert motivo == ""
    assert novo.price_checkout_cents == 52348
    assert novo.price_checkout_label == shopee_checkout.CONDICAO
    assert novo.price_current_cents == 59900        # o catálogo NÃO é substituído


def test_sem_entrada_a_oferta_sai_identica():
    offer = make_offer(price_current_cents=59900)
    novo, motivo = aplica(offer, None)
    assert novo is offer and motivo == shopee_checkout.SEM_ENTRADA


def test_so_a_shopee():
    offer = make_offer(source="meli", price_current_cents=59900)
    novo, motivo = aplica(offer, entrada(52348))
    assert novo is offer and motivo == shopee_checkout.FORA_DA_SHOPEE


def test_preco_do_cubo_maior_ou_igual_ao_vivo_nao_publica():
    """O preço vivo é do `refresh_price`, de segundos atrás; o do cubo tem até
    dias. Quando o do cubo não é MENOR, ele não é desconto nenhum — é o preço
    velho de um item que ficou mais barato. Silêncio, não adivinhação."""
    offer = make_offer(price_current_cents=59900)
    for preco in (59900, 60000):
        novo, motivo = aplica(offer, entrada(preco))
        assert novo is offer and motivo == shopee_checkout.NAO_E_MENOR


def test_diferenca_grande_demais_nao_publica():
    """Medido em 2026-08-29 sobre 100 itens: 70 dos 88 com preço de cubo menor
    ficam em até 15%, com o cotovelo entre 14,57% e 15,34%. Acima disso a cauda
    vai a 55,8% (R$ 199,90 -> R$ 88,27), que não é cupom — é o preço tendo
    mudado desde a raspagem."""
    offer = make_offer(price_current_cents=59900)
    limite = int(59900 * (1 - shopee_checkout.GAP_MAX))
    assert aplica(offer, entrada(limite))[1] == ""            # exatamente no teto: passa
    novo, motivo = aplica(offer, entrada(limite - 1))
    assert novo is offer
    assert motivo.startswith("o preço do cubo está 15.0% abaixo do vivo")
    assert "teto de 15%" in motivo


def test_diferenca_pequena_demais_nao_publica():
    """Menos de 1% não é cupom, é ruído de arredondamento de uma medida `avg` —
    e um "com cupom" colado a uma diferença de centavos afirma um cupom que
    provavelmente não existe."""
    offer = make_offer(price_current_cents=59900)
    novo, motivo = aplica(offer, entrada(59900 - 100))
    assert novo is offer
    assert motivo == shopee_checkout.PERTO_DEMAIS.format(piso=shopee_checkout.GAP_MIN)
    assert "menor que 1%" in motivo


def test_preco_velho_demais_nao_publica():
    offer = make_offer(price_current_cents=59900)
    velho = date.fromordinal(HOJE.toordinal() - shopee_checkout.IDADE_MAX_DIAS - 1)
    novo, motivo = aplica(offer, CheckoutPrice(52348, velho))
    assert novo is offer and shopee_checkout.VELHO.split("{")[0].strip() in motivo
    no_limite = date.fromordinal(HOJE.toordinal() - shopee_checkout.IDADE_MAX_DIAS)
    assert aplica(offer, CheckoutPrice(52348, no_limite))[1] == ""


def test_entrada_sem_data_nao_publica():
    offer = make_offer(price_current_cents=59900)
    novo, motivo = aplica(offer, CheckoutPrice(52348, None))
    assert novo is offer and "idade" in motivo


def test_leitura_do_navegador_tem_precedencia():
    """A 5P lê a página VIVA e ancora o número na frase que a qualifica. O cubo
    é uma foto de até três dias atrás: onde os dois existem, quem vence é a
    leitura."""
    offer = make_offer(price_current_cents=59900, price_checkout_cents=51000,
                       price_checkout_label="no Pix com cupom")
    novo, motivo = aplica(offer, entrada(52348))
    assert novo is offer and motivo == shopee_checkout.JA_LIDO


def test_a_idade_do_dado_sai_no_motivo_de_quem_publicou():
    offer = make_offer(price_current_cents=59900)
    _, _, idade = shopee_checkout.avalia(offer, entrada(52348, "2026-08-27"), HOJE)
    assert idade == 2


# ------------------------------------------------------------ o carimbador do run

def test_carimbador_aplica_conta_e_avisa_quando_a_secao_venceu():
    wl = Watchlist(generated_at=HOJE, valid_days=14,
                   checkout_prices={"123456": entrada(52348, "2026-08-28")},
                   section_dates={"checkout_prices": date(2026, 8, 28)})
    carimbador = shopee_checkout.monta({"preco_checkout": {"enabled": True}}, wl, HOJE)
    offer, _ = carimbador.aplica(make_offer(price_current_cents=59900))
    assert offer.price_checkout_cents == 52348
    assert carimbador.aplicados == 1 and carimbador.warnings == []

    vencida = shopee_checkout.monta({"preco_checkout": {"enabled": True}}, wl,
                                    date(2026, 9, 30))
    vencida.aplica(make_offer(price_current_cents=59900))
    assert vencida.aplicados == 0
    assert any("preco_checkout" in a for a in vencida.warnings)


def test_desligado_no_config_nao_monta_carimbador():
    wl = Watchlist(generated_at=HOJE, valid_days=14,
                   checkout_prices={"123456": entrada(52348)})
    assert shopee_checkout.monta({"preco_checkout": {"enabled": False}}, wl, HOJE) is None
    assert shopee_checkout.monta({}, None, HOJE) is None
    # ligado mas sem watchlist: nada a carimbar, e nada a montar
    assert shopee_checkout.monta({"preco_checkout": {"enabled": True}}, None, HOJE) is None


def test_config_honra_zero_e_o_teto_configuravel():
    cfg = shopee_checkout.config_de({"preco_checkout": {"enabled": True, "gap_min_pct": 0,
                                                        "gap_max_pct": 12,
                                                        "idade_max_dias": 1}})
    assert cfg == {"enabled": True, "gap_min": 0.0, "gap_max": 0.12, "idade_max_dias": 1}
    assert shopee_checkout.config_de({})["enabled"] is False


# ------------------------------------------------------------------ R1: gravar

def watchlist_antiga() -> dict:
    return {"generated_at": "2026-08-23", "valid_days": 14,
            "category_boosts": {"100630": 1.3},
            "hot_items": {"22991771385": {"boost": 1.5}},
            "price_refs": {"22991771385": {"ref_cents": 3500, "window_days": 90,
                                           "p25_cents": 3000}}}


def test_mesclar_grava_a_secao_sem_tocar_no_resto():
    precos = {"a": shopee_checkout.PrecoDeCheckout("a", 52348, date(2026, 8, 28))}
    novo = shopee_checkout.mesclar(watchlist_antiga(), precos, HOJE)
    assert novo["checkout_prices"]["a"] == {"price_cents": 52348,
                                            "measured_at": "2026-08-28"}
    assert novo["section_dates"]["checkout_prices"] == "2026-08-29"
    assert novo["generated_at"] == "2026-08-23"
    assert novo["price_refs"] == watchlist_antiga()["price_refs"]


def test_mesclar_troca_a_entrada_antiga_do_mesmo_item():
    antes = shopee_checkout.mesclar(
        watchlist_antiga(), {"a": shopee_checkout.PrecoDeCheckout("a", 52348,
                                                                  date(2026, 8, 26))},
        date(2026, 8, 26))
    depois = shopee_checkout.mesclar(
        antes, {"a": shopee_checkout.PrecoDeCheckout("a", 51000, date(2026, 8, 29))}, HOJE)
    assert depois["checkout_prices"]["a"] == {"price_cents": 51000,
                                              "measured_at": "2026-08-29"}


def test_watchlist_le_a_secao_e_ela_sobrevive_ao_vencimento():
    conteudo = shopee_checkout.mesclar(
        watchlist_antiga(), {"a": shopee_checkout.PrecoDeCheckout("a", 52348,
                                                                  date(2026, 8, 28))}, HOJE)
    wl = Watchlist(generated_at=HOJE, valid_days=14,
                   checkout_prices={"a": CheckoutPrice(52348, date(2026, 8, 28))})
    assert wl.checkout_price("a") == CheckoutPrice(52348, date(2026, 8, 28))
    assert wl.facts_only().checkout_prices == wl.checkout_prices
    assert conteudo["checkout_prices"]["a"]["price_cents"] == 52348


def test_load_watchlist_le_a_secao_e_degrada_sozinha(tmp_path):
    caminho = tmp_path / "w.json"
    caminho.write_text(json.dumps({
        "generated_at": "2026-08-29", "valid_days": 14,
        "checkout_prices": {"a": {"price_cents": 52348, "measured_at": "2026-08-28"},
                            "b": {"price_cents": 100},
                            "c": {"sem o campo": 1}, "d": "nem é objeto"}}),
        encoding="utf-8")
    wl = load_watchlist(caminho)
    assert wl.checkout_price("a") == CheckoutPrice(52348, date(2026, 8, 28))
    assert wl.checkout_price("b") == CheckoutPrice(100, None)
    assert wl.checkout_price("c") is None and wl.checkout_price("d") is None

    quebrada = tmp_path / "q.json"
    quebrada.write_text(json.dumps({"generated_at": "2026-08-29",
                                    "checkout_prices": "lixo"}), encoding="utf-8")
    assert load_watchlist(quebrada).checkout_prices == {}


# ----------------------------------------------------------- quais itens coletar

CFG = {
    "selection": {"posts_per_run": 2, "price_min_brl": 20, "price_max_brl": 1000,
                  "dedupe_days": 30, "category_ids": [], "max_above_ref": 1.00,
                  "require_price_ref": False, "min_real_discount_pct": 10,
                  "ev_weights": {"popularity": 0.3, "discount": 0.5}},
    "shopee": {"candidate_max_age_days": 3},
    "state": {"path": "data/state.db"},
}


def test_alvos_ordena_por_ev_e_pula_quem_ja_tem_preco_FRESCO(tmp_path):
    db = StateDB(tmp_path / "s.db")
    db.upsert_candidates([
        make_offer(item_id="pouco", sales=10, price_current_cents=3000),
        make_offer(item_id="muito", sales=90_000, price_current_cents=3000),
        make_offer(item_id="fresco", sales=50_000, price_current_cents=3000),
        make_offer(item_id="velho", sales=70_000, price_current_cents=3000),
    ])
    wl = Watchlist(generated_at=HOJE, valid_days=14, price_refs={},
                   checkout_prices={"fresco": entrada(2800, "2026-08-29"),
                                    "velho": entrada(2800, "2026-08-20")})
    assert shopee_checkout.alvos(db, CFG, wl, HOJE, n=5) == ["muito", "velho", "pouco"]
    db.close()


def test_alvos_sem_watchlist_devolve_o_estoque_inteiro(tmp_path):
    db = StateDB(tmp_path / "s.db")
    db.upsert_candidates([make_offer(item_id="a", price_current_cents=3000)])
    assert shopee_checkout.alvos(db, CFG, None, HOJE, n=5) == ["a"]
    db.close()


# ------------------------------------------------------------------------- CLI

def bruto_em_disco(tmp_path) -> str:
    caminho = tmp_path / "bruto" / "lote0_p0.json"
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(json.dumps(bruto((23598844177, "2026-08-28T16:36:34.864", 523.48),
                                        (1, "não é data", 10.0))), encoding="utf-8")
    return str(caminho.parent)


def test_main_coleta_a_partir_do_bruto(tmp_path, capsys):
    watchlist = tmp_path / "watchlist.json"
    watchlist.write_text(json.dumps(watchlist_antiga()), encoding="utf-8")
    codigo = shopee_checkout.main(["coletar", bruto_em_disco(tmp_path),
                                   "--watchlist", str(watchlist), "--hoje", "2026-08-29"])
    saida = capsys.readouterr().out
    assert codigo == 0
    wl = load_watchlist(watchlist)
    assert wl.checkout_price("23598844177") == CheckoutPrice(52348, date(2026, 8, 28))
    assert "23598844177" in saida and "1" in saida


def test_main_dry_run_nao_grava(tmp_path, capsys):
    watchlist = tmp_path / "watchlist.json"
    watchlist.write_text(json.dumps(watchlist_antiga()), encoding="utf-8")
    antes = watchlist.read_text(encoding="utf-8")
    assert shopee_checkout.main(["coletar", bruto_em_disco(tmp_path), "--watchlist",
                                 str(watchlist), "--hoje", "2026-08-29", "--dry-run"]) == 0
    assert watchlist.read_text(encoding="utf-8") == antes
    assert "523" in capsys.readouterr().out


def test_main_watchlist_ausente_e_erro(tmp_path):
    with pytest.raises(SystemExit):
        shopee_checkout.main(["coletar", bruto_em_disco(tmp_path),
                              "--watchlist", str(tmp_path / "não existe.json")])

"""Fase 5D — `flagrante.encontra`: achar o "de" que não se sustenta.

É a mecânica de acusação-com-prova que a pesquisa aponta como a única que
transformou dado em audiência (o padrão Erika Kullberg, em
`docs/superpowers/reviews/2026-08-28-pesquisa-feed.md`). O dado já existia no
price_log; faltavam a consulta e o ranqueamento por gravidade.
"""

from datetime import timedelta

from afiliado import flagrante, pricing
from afiliado.state import StateDB
from tests.test_models import make_offer

CFG = {"selection": {"ref_window_days": 90, "ref_min_observations": 14}}


def _grava(db: StateDB, item_id: str, precos: list[int], source: str = "shopee") -> None:
    """Um preço por dia, o último é o de hoje (dia LOCAL do banco)."""
    hoje = db.local_today()
    inicio = hoje - timedelta(days=len(precos) - 1)
    for i, cents in enumerate(precos):
        db.record_price(source, item_id, cents, day=(inicio + timedelta(days=i)).isoformat())


def _serie_com_pico(dias: int = 90, pico_em: int = 60, dias_de_pico: int = 1) -> list[int]:
    precos = [2600] * dias
    for i in range(dias_de_pico):
        precos[pico_em + i] = 6890
    return precos


def _oferta_inflada(**kw):
    """O vendedor anuncia "de R$ 68,90 por R$ 26,00" — 62% de desconto contra
    um "de" que é 2,6x a nossa mediana."""
    base = dict(item_id="inflado", price_original_cents=6890, price_current_cents=2600,
                price_ref_cents=2600, price_p25_cents=2500, price_window_days=90)
    base.update(kw)
    return make_offer(**base)


# --- O caso que a régua existe para pegar ------------------------------------

def test_encontra_o_de_que_nao_se_sustenta(tmp_path):
    db = StateDB(tmp_path / "s.db")
    _grava(db, "inflado", _serie_com_pico())
    offer = _oferta_inflada()
    assert offer.discount_pct == 62

    achados = flagrante.encontra([offer], db, CFG)
    assert len(achados) == 1
    f = achados[0]
    assert f.offer is offer
    assert f.desconto_alegado_pct == 62
    assert f.pico_cents == 6890
    assert f.dias_no_pico == 1
    # gravidade = (6890/2600) x (62/100)
    assert abs(f.gravidade - (6890 / 2600) * 0.62) < 1e-9
    # O histórico volta pronto para `creative.render_grafico_preco`.
    assert len(f.historico) == 90
    assert f.historico[-1] == (db.local_today(), 2600)
    assert max(p for _, p in f.historico) == 6890
    db.close()


# --- O que NÃO é flagrante ---------------------------------------------------

def test_ignora_desconto_real(tmp_path):
    """Preço abaixo do p25 com um "de" plausível: o vendedor está mesmo dando
    desconto. Acusar isto seria a acusação injusta que o projeto não pode fazer."""
    db = StateDB(tmp_path / "s.db")
    _grava(db, "honesto", [2600] * 90)
    offer = make_offer(item_id="honesto", price_original_cents=3200,
                       price_current_cents=1890, price_ref_cents=2600,
                       price_p25_cents=2400, price_window_days=90)
    assert offer.discount_pct == 41          # o vendedor alega desconto grande...
    assert offer.price_original_cents < 2600 * pricing.PICO_FATOR   # ...mas o "de" é plausível
    assert flagrante.encontra([offer], db, CFG) == []
    db.close()


def test_ignora_desconto_alegado_pequeno(tmp_path):
    db = StateDB(tmp_path / "s.db")
    _grava(db, "inflado", _serie_com_pico())
    # "de" bem acima da mediana, mas o desconto anunciado é de 20%.
    offer = _oferta_inflada(price_original_cents=6890, price_current_cents=5500)
    assert offer.discount_pct == 20
    assert flagrante.encontra([offer], db, CFG) == []
    db.close()


def test_ignora_produto_sem_historico_suficiente(tmp_path):
    db = StateDB(tmp_path / "s.db")
    _grava(db, "inflado", [2600] * 5 + [6890])      # 6 dias < ref_min_observations
    assert flagrante.encontra([_oferta_inflada()], db, CFG) == []
    db.close()


def test_ignora_preco_que_ficou_muito_tempo_no_alto(tmp_path):
    """Três dias acima da mediana x 1,5 não é etiqueta de um dia — é preço.
    O flagrante só vale para o pico que não se sustenta."""
    db = StateDB(tmp_path / "s.db")
    _grava(db, "inflado", _serie_com_pico(dias_de_pico=3))
    assert flagrante.encontra([_oferta_inflada()], db, CFG) == []
    db.close()


def test_ignora_oferta_sem_a_nossa_referencia(tmp_path):
    """Sem mediana não há régua, e sem régua não há acusação. `price_ref_cents`
    é decidido uma vez, por `pricing.enrich_offers` — aqui ele só é lido."""
    db = StateDB(tmp_path / "s.db")
    _grava(db, "sem_ref", _serie_com_pico())
    offer = _oferta_inflada(item_id="sem_ref", price_ref_cents=0, price_p25_cents=0,
                            price_window_days=0)
    assert flagrante.encontra([offer], db, CFG) == []
    db.close()


def test_o_mercado_livre_simplesmente_nao_entra(tmp_path):
    """O ML não expõe o "de" do vendedor: `price_original_cents` é 0 e
    `discount_pct` também. Ausência não é erro — a oferta só não é flagrante."""
    db = StateDB(tmp_path / "s.db")
    _grava(db, "MLB1", _serie_com_pico(), source="meli")
    offer = make_offer(source="meli", item_id="MLB1", price_original_cents=0,
                       price_current_cents=2600, price_ref_cents=2600,
                       price_p25_cents=2500, price_window_days=90)
    assert offer.discount_pct == 0
    assert flagrante.encontra([offer], db, CFG) == []
    db.close()


def test_lista_vazia_e_lista_vazia(tmp_path):
    db = StateDB(tmp_path / "s.db")
    assert flagrante.encontra([], db, CFG) == []
    db.close()


# --- Ranqueamento ------------------------------------------------------------

def test_ordena_do_mais_escandaloso_para_o_menos(tmp_path):
    db = StateDB(tmp_path / "s.db")
    _grava(db, "escandaloso", _serie_com_pico())
    _grava(db, "menos", [10000] * 89 + [10000])
    db.record_price("shopee", "menos", 19900,
                    day=(db.local_today() - timedelta(days=30)).isoformat())

    pior = _oferta_inflada(item_id="escandaloso")                    # 2,65 x 0,62
    menor = make_offer(item_id="menos", price_original_cents=19900,
                       price_current_cents=10000, price_ref_cents=10000,
                       price_p25_cents=9500, price_window_days=90)   # 1,99 x 0,50
    achados = flagrante.encontra([menor, pior], db, CFG)
    assert [f.offer.item_id for f in achados] == ["escandaloso", "menos"]
    assert achados[0].gravidade > achados[1].gravidade
    db.close()


def test_flagrante_e_congelado():
    import dataclasses

    import pytest

    f = flagrante.Flagrante(make_offer(), [], 0, 0, 0, 0.0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        f.pico_cents = 1


def test_respeita_a_janela_e_o_minimo_de_observacoes_do_config(tmp_path):
    db = StateDB(tmp_path / "s.db")
    _grava(db, "inflado", _serie_com_pico(dias=20, pico_em=10))
    # 20 dias de histórico: passa com ref_min_observations 14, não com 30.
    assert len(flagrante.encontra([_oferta_inflada()], db, CFG)) == 1
    exigente = {"selection": {"ref_window_days": 90, "ref_min_observations": 30}}
    assert flagrante.encontra([_oferta_inflada()], db, exigente) == []
    # Janela curta corta o histórico e derruba o mínimo junto.
    curta = {"selection": {"ref_window_days": 5, "ref_min_observations": 14}}
    assert flagrante.encontra([_oferta_inflada()], db, curta) == []
    db.close()

"""Fase 5V — o painel de observação.

Ele existe porque a régua honesta esperava uma coisa que não ia acontecer.
Medido no `state.db` de produção em 2026-08-31: a descoberta rotativa tinha
visto **16.523 itens distintos em 6 dias** e **nenhum** deles nos 6 — o melhor
tinha 5 dias, e eram 14 itens em 16 mil. A régua exige 14 dias DO MESMO item,
então `price_refs` ficaria em 0 para sempre.

Nada aqui toca a rede: a fonte é um dublê que devolve preço (ou levanta
`SourceError`), que é exatamente o que a Shopee faz quando o item sai da
listagem de afiliados.
"""

import dataclasses

import pytest

from afiliado import painel
from afiliado.errors import SourceError
from afiliado.models import Offer
from afiliado.state import StateDB


@pytest.fixture
def db(tmp_path):
    banco = StateDB(str(tmp_path / "state.db"), timezone="America/Sao_Paulo")
    yield banco
    banco.close()


class FonteDuble:
    """Devolve `precos[item_id]`; item ausente levanta `SourceError`, como o
    `refresh_price` de verdade quando a oferta saiu da listagem."""

    def __init__(self, precos: dict[str, int], explode_em: str | None = None):
        self.precos = precos
        self.explode_em = explode_em
        self.pedidos: list[str] = []

    def refresh_price(self, offer: Offer) -> Offer:
        self.pedidos.append(offer.item_id)
        if offer.item_id == self.explode_em:
            raise RuntimeError("a máquina caiu no meio da passada")
        if offer.item_id not in self.precos:
            raise SourceError(f"shopee: item {offer.item_id} saiu da listagem")
        return dataclasses.replace(offer, price_current_cents=self.precos[offer.item_id])


def _cfg(**painel_cfg) -> dict:
    return {"painel": painel_cfg,
            "selection": {"ref_min_observations": 14, "ref_window_days": 90}}


# -- a estabilidade, que é a razão de o painel existir -------------------------

def test_reincluir_um_item_NAO_reinicia_a_data_de_entrada(db):
    """A data de entrada é o que mede a história de um membro. Se a passada
    diária a regravasse, o painel viraria uma lista sempre nova — que é
    exatamente a largura que a descoberta rotativa já faz e que NÃO constrói
    régua nenhuma."""
    assert db.painel_incluir("shopee", ["A", "B"], day="2026-08-01") == 2
    assert db.painel_incluir("shopee", ["A", "B", "C"], day="2026-08-20") == 1
    entradas = {i: d for i, d, _ in db.painel("shopee")}
    assert entradas == {"A": "2026-08-01", "B": "2026-08-01", "C": "2026-08-20"}


def test_o_painel_devolve_os_membros_mais_antigos_primeiro(db):
    """Quem tem mais história é lido primeiro: se a passada morrer no meio, o
    que se perde é o dia de quem tinha menos a perder."""
    db.painel_incluir("shopee", ["novo"], day="2026-08-20")
    db.painel_incluir("shopee", ["velho"], day="2026-08-01")
    assert [i for i, _, _ in db.painel("shopee")] == ["velho", "novo"]


def test_completa_enche_ate_o_tamanho_e_para(db, monkeypatch):
    from afiliado import shopee_regua

    fila = [Offer(source="shopee", item_id=str(n), title="t", price_original_cents=1000,
                  price_current_cents=1000, commission_pct=5.0, image_url="",
                  product_url="https://x") for n in range(50)]
    monkeypatch.setattr(shopee_regua, "fila", lambda *a, **k: fila)
    assert painel.completa(db, _cfg(tamanho=10), None) == 10
    assert len(db.painel("shopee")) == 10
    # Chamar de novo não mexe em nada: o painel já está cheio.
    assert painel.completa(db, _cfg(tamanho=10), None) == 0
    assert len(db.painel("shopee")) == 10


def test_completa_nao_repoe_quem_ja_esta_no_painel(db, monkeypatch):
    """A fila do dia seguinte traz os mesmos itens no topo — o painel tem de
    reconhecê-los e usar as vagas em quem ainda não está."""
    from afiliado import shopee_regua

    fila = [Offer(source="shopee", item_id=str(n), title="t", price_original_cents=1000,
                  price_current_cents=1000, commission_pct=5.0, image_url="",
                  product_url="https://x") for n in range(50)]
    monkeypatch.setattr(shopee_regua, "fila", lambda *a, **k: fila)
    painel.completa(db, _cfg(tamanho=5), None)
    painel.completa(db, _cfg(tamanho=8), None)
    assert sorted(int(i) for i, _, _ in db.painel("shopee")) == list(range(8))


# -- a leitura do dia ----------------------------------------------------------

def test_a_passada_grava_um_preco_por_item_no_price_log(db):
    db.painel_incluir("shopee", ["A", "B", "C"])
    fonte = FonteDuble({"A": 1000, "B": 2000, "C": 3000})
    assert painel.observa(db, fonte, _cfg()) == (3, 0)
    assert db.price_history("shopee", "A", 30) == [1000]
    assert db.price_history("shopee", "C", 30) == [3000]


def test_item_que_saiu_da_listagem_conta_falha_e_NAO_derruba_a_passada(db):
    """`SourceError` é informação, não acidente: a Shopee tira e devolve
    ofertas de afiliado o tempo todo. O que não pode é um item sumido custar os
    outros 199 do dia."""
    db.painel_incluir("shopee", ["A", "sumiu", "C"])
    fonte = FonteDuble({"A": 1000, "C": 3000})
    assert painel.observa(db, fonte, _cfg()) == (2, 1)
    assert db.price_history("shopee", "A", 30) == [1000]
    assert db.price_history("shopee", "sumiu", 30) == []
    falhas = {i: f for i, _, f in db.painel("shopee")}
    assert falhas == {"A": 0, "sumiu": 1, "C": 0}


def test_preco_zero_nao_entra_no_price_log(db):
    """Preço não-positivo é leitura inválida, não preço. Gravá-lo envenenaria a
    mediana e o piso do selo — as duas coisas que o painel existe para
    construir."""
    db.painel_incluir("shopee", ["A"])
    assert painel.observa(db, FonteDuble({"A": 0}), _cfg()) == (0, 1)
    assert db.price_history("shopee", "A", 30) == []


def test_a_passada_grava_a_cada_item_e_nao_no_fim(db):
    """Se a máquina cair no meio de 200 chamadas de rede, o que já foi lido tem
    de estar no banco. Uma série de preço com um BURACO é pior que uma série
    mais curta — e o buraco é exatamente o que um commit no fim produziria."""
    db.painel_incluir("shopee", ["A"], day="2026-08-01")
    db.painel_incluir("shopee", ["B"], day="2026-08-02")
    db.painel_incluir("shopee", ["explode"], day="2026-08-03")
    fonte = FonteDuble({"A": 1000, "B": 2000}, explode_em="explode")
    with pytest.raises(RuntimeError):
        painel.observa(db, fonte, _cfg())
    assert db.price_history("shopee", "A", 30) == [1000]
    assert db.price_history("shopee", "B", 30) == [2000]


def test_dry_run_consulta_e_nao_grava_nada(db):
    db.painel_incluir("shopee", ["A"])
    fonte = FonteDuble({"A": 1000})
    assert painel.observa(db, fonte, _cfg(), dry_run=True) == (1, 0)
    assert fonte.pedidos == ["A"]              # consultou de verdade
    assert db.price_history("shopee", "A", 30) == []


# -- entrar e sair -------------------------------------------------------------

def test_uma_leitura_boa_ZERA_as_falhas(db):
    """O que tira um item do painel é falhar de forma CONSECUTIVA. Um item que
    sumiu na terça e voltou na quarta não pode carregar a terça para sempre."""
    db.painel_incluir("shopee", ["A"])
    db.painel_leitura("shopee", "A", ok=False)
    db.painel_leitura("shopee", "A", ok=False)
    assert dict((i, f) for i, _, f in db.painel("shopee")) == {"A": 2}
    db.painel_leitura("shopee", "A", ok=True)
    assert dict((i, f) for i, _, f in db.painel("shopee")) == {"A": 0}


def test_expurgar_tira_so_quem_bateu_o_limite(db):
    db.painel_incluir("shopee", ["fica", "sai"])
    for _ in range(2):
        db.painel_leitura("shopee", "fica", ok=False)
    for _ in range(3):
        db.painel_leitura("shopee", "sai", ok=False)
    assert db.painel_expurgar("shopee", 3) == ["sai"]
    assert [i for i, _, _ in db.painel("shopee")] == ["fica"]


# -- o progresso, que é a única medida honesta de "está funcionando" ----------

def test_no_primeiro_dia_o_progresso_e_zero_e_isso_esta_certo(db):
    db.painel_incluir("shopee", ["A", "B"])
    painel.observa(db, FonteDuble({"A": 1000, "B": 2000}), _cfg())
    tamanho, prontos, minimo = painel.progresso(db, _cfg())
    assert (tamanho, prontos, minimo) == (2, 0, 14)


def test_um_item_com_dias_suficientes_conta_como_pronto(db):
    """A conta é de DIAS distintos, não de leituras: ler o mesmo item dez vezes
    hoje continua sendo um dia."""
    db.painel_incluir("shopee", ["A", "B"])
    for n in range(14):
        db.record_price("shopee", "A", 1000 + n, day=f"2026-08-{n + 1:02d}")
    for n in range(3):
        db.record_price("shopee", "B", 2000, day=f"2026-08-{n + 1:02d}")
    tamanho, prontos, minimo = painel.progresso(db, _cfg())
    assert (tamanho, prontos, minimo) == (2, 1, 14)


def test_dias_observados_ignora_item_fora_da_lista(db):
    db.record_price("shopee", "A", 1000, day="2026-08-01")
    db.record_price("shopee", "Z", 1000, day="2026-08-01")
    assert db.dias_observados("shopee", ["A"], 90) == {"A": 1}


# -- o config ------------------------------------------------------------------

def test_o_config_real_tem_o_painel_ligado_e_dimensionado():
    """O tamanho é o que decide o CUSTO: uma leitura é uma chamada, e o
    pipeline já faz ~608/dia contra as ~1.920/dia que a VPS rodava sem 429."""
    import yaml

    with open("config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    conf = painel.config_de(cfg)
    assert conf["enabled"] is True
    assert 50 <= conf["tamanho"] <= 500
    assert conf["max_falhas"] >= 2      # uma falha isolada não expulsa ninguém


def test_config_ausente_cai_no_padrao():
    assert painel.config_de({}) == painel.PADRAO
    assert painel.config_de({"painel": {"tamanho": 7}})["tamanho"] == 7
    assert painel.config_de({"painel": {"tamanho": 7}})["max_falhas"] == \
        painel.PADRAO["max_falhas"]

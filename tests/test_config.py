import pytest

from afiliado import selection, validate
from afiliado.config import load_config
from afiliado.errors import ValidationError
from afiliado.sources.meli import _parse_pool_offer
from afiliado.state import StateDB
from tests.test_models import make_offer


def test_load_config_reads_project_yaml():
    cfg = load_config("config.yaml")
    assert cfg["selection"]["posts_per_run"] == 1
    assert cfg["llm"]["model"] == "haiku"
    # Fase 3 (correção pós-revisão): category_ids por fonte e allowed_domains
    # com os domínios do ML — sem isso o ML publica zero ofertas em silêncio.
    assert "meli" in cfg["selection"]["category_ids"]
    assert "mercadolivre.com" in cfg["validation"]["allowed_domains"]


def test_config_carrega_a_descoberta_medida_em_2026_08_26():
    """Fase 5C (C1): o que a medição de 147 chamadas reais recomendou tem de
    estar no config, senão o volume volta a 8 posts/dia sustentáveis."""
    sh = load_config("config.yaml")["shopee"]
    assert sh["pages"] == 40                  # a janela real por listagem
    assert sh["page_size"] == 50              # limit máximo aceito
    assert sh["sort_types"] == [2] and sh["list_type"] == 0
    assert sh["calls_per_run"] == 8
    assert sh["candidate_max_age_days"] == 3
    assert len(sh["category_ids"]) == 5
    assert len(sh["subcategory_ids"]) == 26   # as de >= 25 itens no top-500 da raiz
    assert sh["subcategory_first_page"] == 2  # a p1 da subcategoria ≈ topo da raiz
    termos = [t for lista in sh["keywords"].values() for t in lista]
    assert len(termos) == 40 == len(set(termos))
    assert set(sh["keywords"]) == set(load_config("config.yaml")["selection"]
                                      ["category_ids"]["shopee"])


def test_o_teto_de_preco_e_o_medido_na_fase_5s():
    """Fase 5S: `price_max_brl` é 150 porque foi MEDIDO, não arbitrado.

    Os 100 itens mais vendidos da Shopee Brasil em 30 dias (JoomPulse
    `ShbMartItem`, `sold30Days > 0`, preço > R$ 20) têm mediana R$ 36,05, p90
    R$ 68,75, máximo R$ 148,20 — **0 de 100 acima de R$ 150** e 4 de 100 acima
    de R$ 100. R$ 150 é onde a distribuição do que o catálogo VENDE termina.

    O que nós publicávamos com o teto em 1000, medido no `posted` real
    (101 posts no Telegram, 27–29/08/2026): mediana R$ 150,50, média R$ 234,09,
    21% acima de R$ 400, o mais caro uma autoclave de R$ 850,00 — 4,2× a
    mediana do que o catálogo vende.

    O que o teto NÃO custa (medido sobre `data/state.db`, 15.688 candidatas da
    Shopee + o pool de 53 do ML): 8.904 candidatas sobrevivem a 150 contra
    9.572 a 1000 (−7%), e são 148 dias de estoque para um teto de 60 posts/dia.

    Por que não 100: a 100 o ML perde um produto publicável (o preço vivo do
    Kit 10 potes 640ml é R$ 109,70) e a faixa R$ 100–150, onde vivem 4 dos 100
    mais vendidos, morre junto. A 150 o ML não perde NADA (ver
    `test_o_teto_de_150_nao_derruba_o_mercado_livre`)."""
    sel = load_config("config.yaml")["selection"]
    assert sel["price_max_brl"] == 150


def test_o_piso_de_preco_segue_20_porque_ninguem_o_mediu():
    """Fase 5S (S3): o piso NÃO mudou, e o motivo é a falta de dado.

    A amostra do catálogo filtrou preço > R$ 20 — não há nela nenhuma evidência
    sobre o que acontece abaixo disso. O que dá para medir é só o NOSSO lado, e
    ele diz que a mudança seria grande: 5.795 das 15.688 candidatas (36,9%)
    estão abaixo de R$ 20, com comissão mediana de R$ 0,60, e o
    `min_ev_brl: 0.50` não as segura (5.763 das 5.795 passariam nele).

    Baixar o piso é, portanto, uma decisão com 37% de raio de alcance e zero
    evidência de conversão. Fica como está até alguém medir."""
    sel = load_config("config.yaml")["selection"]
    assert sel["price_min_brl"] == 20


def test_o_expoente_da_comissao_nao_mudou_e_a_medicao_diz_por_que():
    """Fase 5S (S2): a hipótese era que o teto tratasse o sintoma e o
    `commission_exp` fosse a causa raiz do item caro no topo. A medição sobre o
    estoque real diz que não — ele é quase inerte no preço.

    Preço MEDIANO dos 60 primeiros de `order_by_ev` (60 = o teto diário do
    Telegram, isto é, o dia que o pipeline publicaria):

        teto \\ exp    0.0     0.3     0.5     0.7     1.0
           150         34     101     111     111     114
          1000         34     127     191     212     284

    A 150, ir de 0,7 a 1,0 move a mediana R$ 3; ir de 1000 a 150 move R$ 101.
    O 0,0 é outro produto, não um ajuste — ver
    `tests/test_selection.py::test_o_expoente_zero_cega_o_ev_para_a_comissao`.
    Por isso o expoente ficou como estava."""
    assert load_config("config.yaml")["selection"]["ev_weights"]["commission_exp"] == 0.7


def test_o_teto_do_config_morde_nos_TRES_portoes(tmp_path):
    """O número do `config.yaml` é o número que corta — nos três lugares.

    `price_max_brl` é lido por `selection.filter_offers` (antes do refresh),
    por `validate.check_price` (depois, sobre o preço VIVO) e pela carga do
    pool do ML (`_parse_pool_offer`, sobre a referência curada). Um teto
    baixado em um só deles seria um teto que não existe: a oferta cara entraria
    pela porta que ficou aberta."""
    cfg = load_config("config.yaml")
    teto = cfg["selection"]["price_max_brl"]
    # Categoria do allowlist real da Shopee: sem ela o portão que corta é
    # outro e o teste não estaria medindo o teto.
    cat = cfg["selection"]["category_ids"]["shopee"][0]
    acima = make_offer(item_id="caro", category=cat,
                       price_current_cents=(teto + 1) * 100)
    dentro = make_offer(item_id="barato", category=cat,
                        price_current_cents=teto * 100)

    db = StateDB(tmp_path / "s.db")
    candidatas, cortes = selection.filter_offers_with_stats([acima, dentro], db, cfg)
    assert [o.item_id for o in candidatas] == [dentro.item_id]
    assert cortes.faixa_preco == 1

    validate.check_price(dentro, cfg)
    with pytest.raises(ValidationError, match="fora da faixa"):
        validate.check_price(acima, cfg)

    def entrada(ref_cents):
        return {"product_id": "MLB1", "title": "t", "price_ref_cents": ref_cents,
                "price_p25_cents": ref_cents, "price_window_days": 90,
                "price_historic_min_cents": ref_cents, "price_min_window_days": 365}
    assert _parse_pool_offer(entrada((teto + 1) * 100), 4.0,
                             cfg["selection"])[1] == "fora da faixa de preço"
    assert _parse_pool_offer(entrada(teto * 100), 4.0, cfg["selection"])[0] is not None


def test_o_teto_de_150_nao_derruba_o_mercado_livre():
    """O ponto de atenção da fase 5S, medido: o teto não mata o ML.

    O ML entrega pouco por um motivo ESTRUTURAL que o teto não toca — o pool
    tem 53 produtos e o dedupe é de 30 dias, ou seja, 53/30 ≈ 1,8 oferta/dia em
    regime, contra uma meta de 30/dia (`source_quota` 0,5 × 60 do Telegram).
    O teto é irrelevante diante disso:

    - carga do pool: 52 das 53 entradas passam a 150, contra 53 a 1000 — a
      única perdida (colchão inflável, ref R$ 209,87) já estava fora por
      dedupe;
    - `filter_offers`: **36 sobreviventes a 150 e 36 a 1000**, o mesmo número;
    - preço VIVO (o nosso `price_log`, 23 anúncios do ML já refrescados): 21
      dos 23 abaixo de 150, e os 2 acima (R$ 209,87 e R$ 179,00) já estão em
      dedupe — nenhum produto PUBLICÁVEL é perdido.

    Por isso não há teto por fonte nesta fase: ele resolveria um problema que a
    medição não encontrou. Este teste trava a premissa — se o pool crescer e
    encher de item caro, ele cai e a decisão volta à mesa."""
    cfg = load_config("config.yaml")
    caros = [e for e in _pool_do_ml() if e.get("price_ref_cents", 0) / 100
             > cfg["selection"]["price_max_brl"]]
    assert len(caros) <= 1, [e["title"] for e in caros]


def _pool_do_ml():
    import json
    from pathlib import Path
    return json.loads(Path("data/meli_offers.json").read_text(encoding="utf-8"))["offers"]


def test_load_config_rejects_missing_keys(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("llm:\n  model: haiku\n", encoding="utf-8")
    with pytest.raises(ValueError, match="obrigat"):
        load_config(p)

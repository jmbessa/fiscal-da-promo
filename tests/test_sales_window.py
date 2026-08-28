"""A SEMÂNTICA de `Offer.sales`: o que o número MEDE, fonte por fonte.

Este arquivo existe por causa de um erro que aconteceu DUAS vezes.

1. O Mercado Livre gravava `catalogOrderCount1m` (estimativa do MÊS) num campo
   que a arte escreve como "N vendidos" — o dono flagrou um story dizendo
   "5 mil vendidos" para um anúncio que diz "+250 mil vendidos". Corrigido
   trocando para `catalogSales` (o contador vitalício).
2. A Shopee tinha o MESMO defeito, e a correção do ML passou ao lado dele.
   Medido em 2026-08-28 contra o cubo `ShbMartItem` do JoomPulse:

   | item                          | nosso `sales` | `sold1y` (o anúncio) | `sold30Days` |
   |-------------------------------|---------------|----------------------|--------------|
   | 16692338189 Lençol Micropercal|        45.950 |            2.000.000 |       50.000 |
   | 22893738408 Lençol Extra Macio|        77.344 |            1.000.000 |       70.000 |
   | 58256439593 Percarbonato      |        73.175 |              100.000 |       70.000 |
   | 9212570285 Creatina Soldiers  |        31.077 |              100.000 |       30.000 |

   Nos quatro, `productOfferV2.sales` bate com a janela de ~30 dias e fica 13× a
   43× abaixo do contador que o anúncio exibe.

O que deixou o erro passar as duas vezes não foi a falta de cuidado: foi não
haver NADA no projeto afirmando o que `sales` significa. Um int sem unidade
comparado, somado e escrito como se as duas fontes falassem a mesma língua.
Este arquivo é essa afirmação, e ela é varrida do pacote — uma fonte NOVA entra
aqui sozinha, e reprova enquanto não declarar a janela dela.
"""

import importlib
import inspect
import pkgutil
from datetime import date

import pytest

import afiliado.sources
from afiliado.sources.meli import _parse_pool_offer
from afiliado.sources.shopee import _parse_node

# A janela (em dias) que o `sales` de cada fonte mede. 0 = contador VITALÍCIO,
# o número que o próprio anúncio exibe.
JANELAS = {"meli": 0, "shopee": 30}


def _classes_de_fonte() -> list[type]:
    """Toda classe de fonte do pacote `afiliado.sources`, por varredura — uma
    fonte nova é descoberta sem ninguém precisar lembrar deste arquivo."""
    achadas = []
    for info in pkgutil.iter_modules(afiliado.sources.__path__):
        modulo = importlib.import_module(f"afiliado.sources.{info.name}")
        for obj in vars(modulo).values():
            if (inspect.isclass(obj) and obj.__module__ == modulo.__name__
                    and isinstance(getattr(obj, "name", None), str)
                    and callable(getattr(obj, "fetch_offers", None))):
                achadas.append(obj)
    return achadas


def janela_declarada(fonte: type) -> int:
    """A janela que a fonte DECLARA, ou um AssertionError que diz o que fazer.

    Não existe default: herdar o `0` de `Offer.sales_window_days` em silêncio é
    exatamente o defeito da Shopee — 30 dias apresentados como o total do
    anúncio."""
    janela = getattr(fonte, "sales_window_days", None)
    assert isinstance(janela, int) and not isinstance(janela, bool), (
        f"a fonte {getattr(fonte, 'name', fonte)!r} não declara "
        "`sales_window_days`: diga em quantos dias o `sales` dela é medido "
        "(0 = contador vitalício, 30 = último mês) na classe da fonte, no "
        "`Offer` que ela constrói e em `tests/test_sales_window.py`")
    assert janela >= 0, f"janela negativa em {fonte!r}: {janela}"
    return janela


def test_toda_fonte_declara_a_janela_do_sales():
    fontes = _classes_de_fonte()
    assert {f.name for f in fontes} == set(JANELAS)
    for fonte in fontes:
        assert janela_declarada(fonte) == JANELAS[fonte.name]


def test_fonte_nova_sem_declarar_a_janela_reprova_com_a_instrucao():
    """A prova de que a verificação acima PEGA o caso — e que a mensagem diz o
    que fazer, em vez de o número passar valendo "vitalício"."""

    class FonteNova:
        name = "temu"

        def fetch_offers(self, cfg):
            return []

    with pytest.raises(AssertionError, match="sales_window_days"):
        janela_declarada(FonteNova)


def test_a_oferta_da_shopee_carrega_a_janela_de_30_dias():
    """A declaração da classe e o `Offer` que ela constrói não podem divergir:
    quem lê o número é o `Offer`."""
    offer = _parse_node({"itemId": 16692338189, "productName": "Lençol Micropercal",
                         "price": "89.90", "commissionRate": "0.10",
                         "imageUrl": "https://x/i.jpg", "productLink": "https://x/p",
                         "sales": 45950})
    assert offer.sales == 45950
    assert offer.sales_window_days == JANELAS["shopee"] == 30
    assert offer.sales_e_faixa is False       # contagem fina, não balde: sem "+"


def test_a_oferta_do_meli_carrega_o_contador_vitalicio():
    item = {"product_id": "MLB18725310", "title": "Creatina 1kg Growth",
            "image_url": "https://x/i.jpg", "category": "MLB264586",
            "buy_box_item_id": "MLB3928374651",
            "price_ref_cents": 2590, "price_p25_cents": 2428, "price_window_days": 91,
            "price_historic_min_cents": 1699, "price_min_window_days": 365,
            "sales": 1_000_000, "rating": 4.8}
    offer, motivo = _parse_pool_offer(item, 4.0, {}, date(2026, 8, 28), date(2026, 8, 28))
    assert motivo == "" and offer is not None
    assert offer.sales == 1_000_000
    assert offer.sales_window_days == JANELAS["meli"] == 0
    assert offer.sales_e_faixa is True        # o ML publica a FAIXA: sai com "+"

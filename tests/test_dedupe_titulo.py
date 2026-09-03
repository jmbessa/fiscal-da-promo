"""Fase 5X — o mesmo produto, outro vendedor, outro `item_id`.

O dono viu o canal repetindo oferta. O dedupe existente é por
`(fonte, item_id)` e estava funcionando: medido no `posted` de produção em
2026-09-02, **nenhum** `item_id` saiu duas vezes no mesmo canal. O que saiu
repetido foi o PRODUTO — "Boneca Bebê Reborn Unicórnio Menina Original
Realista..." três vezes, com três ids distintos.

`recent_titles` já ia ao ranker, mas como DICA num prompt ("priorize variedade
vs. posts recentes"). Dica não é portão: o modelo obedece na maioria das vezes,
e "na maioria das vezes" é como se publica repetido.
"""

import pytest

from afiliado.selection import (chave_de_titulo, filter_offers,
                                filter_offers_with_stats)
from afiliado.state import StateDB
from tests.test_models import make_offer
from tests.test_state import make_post


@pytest.fixture
def db(tmp_path):
    banco = StateDB(str(tmp_path / "state.db"), timezone="America/Sao_Paulo")
    yield banco
    banco.close()


def _cfg(dias: int = 7) -> dict:
    return {"selection": {"dedupe_days": 30, "dedupe_titulo_dias": dias,
                          "price_min_brl": 1, "price_max_brl": 10000,
                          "max_above_ref": 1.2, "min_ev_brl": 0}}


def _kw(item_id: str, titulo: str) -> dict:
    return {"item_id": item_id, "title": titulo, "price_current_cents": 5000,
            "image_url": "https://x/i.jpg", "product_url": "https://x/p"}


def _oferta(item_id: str, titulo: str):
    return make_offer(**_kw(item_id, titulo))


def _publicado(db, item_id: str, titulo: str) -> None:
    """Grava um post em `posted` com o título de verdade.

    `make_post` recebe o VEREDITO como primeiro posicional e a oferta por
    palavra-chave — passar a oferta na primeira posição a fazia virar veredito,
    e o post ia para o banco com o título PADRÃO. Os três testes que dependiam
    disso passaram a testar nada, e foi o próprio dedupe que os pegou."""
    db.record_post(make_post(**_kw(item_id, titulo)), "telegram", item_id)


# -- a chave --------------------------------------------------------------------

def test_a_chave_ignora_a_ORDEM_das_palavras():
    """Dois vendedores escrevem o mesmo produto em ordens diferentes. Se a
    chave dependesse da ordem, ela não pegaria justamente o caso real."""
    a = chave_de_titulo("Boneca Bebê Reborn Unicórnio Menina Original Realista")
    b = chave_de_titulo("Bebê Reborn Boneca Unicórnio Realista Menina Silicone")
    assert a == b != ""


def test_a_chave_ignora_acento_caixa_e_pontuacao():
    assert (chave_de_titulo("FONE DE OUVIDO BLUETOOTH TWS, sem fio!")
            == chave_de_titulo("fone de ouvido bluetooth tws"))


def test_a_chave_ignora_numero_porque_tamanho_nao_e_outro_produto():
    """"300g" e "1kg" são variações do mesmo produto para quem lê o canal —
    publicar as duas na mesma semana é repetir."""
    assert (chave_de_titulo("Coador de Café Reutilizável 103 Silicone")
            == chave_de_titulo("Coador de Café Reutilizável 102 Silicone"))


def test_produtos_diferentes_NAO_colidem():
    assert (chave_de_titulo("Fone de Ouvido Bluetooth TWS")
            != chave_de_titulo("Cadeira Gamer Ergonômica Reclinável"))
    assert (chave_de_titulo("Creatina Monohidratada 300g Dark Lab")
            != chave_de_titulo("Whey Protein Concentrado 1kg Growth"))


def test_titulo_curto_demais_nao_forma_chave():
    """Duas palavras não são evidência de repetição — e "" nunca colide, senão
    todo título curto viraria o mesmo produto."""
    assert chave_de_titulo("Kit Original") == ""
    assert chave_de_titulo("") == ""
    assert chave_de_titulo("de da do em") == ""


def test_as_palavras_de_ruido_nao_entram_na_chave():
    """"Kit", "original", "promoção", "frete grátis" não distinguem produto
    nenhum. Se entrassem, dois produtos diferentes com os mesmos adjetivos de
    vitrine colidiriam."""
    assert (chave_de_titulo("Kit Original Fone de Ouvido Bluetooth TWS Promoção")
            == chave_de_titulo("Fone de Ouvido Bluetooth TWS"))


# -- o portão --------------------------------------------------------------------

def test_o_mesmo_produto_de_outro_vendedor_e_cortado(db):
    """O caso medido: mesmo produto, `item_id` diferente. O dedupe por id
    deixa passar; este não."""
    _publicado(db, "i1", "Boneca Bebê Reborn Unicórnio Menina Original Realista")
    nova = _oferta("i2", "Bebê Reborn Boneca Unicórnio Realista Menina Silicone")
    passaram, stats = filter_offers_with_stats([nova], db, _cfg())
    assert passaram == []
    assert stats.dedupe_titulo == 1
    assert stats.dedupe == 0      # o id é outro: quem cortou foi o título


def test_produto_diferente_continua_passando(db):
    _publicado(db, "i1", "Boneca Bebê Reborn Unicórnio Menina")
    outra = _oferta("i2", "Cadeira Gamer Ergonômica Reclinável Preta")
    assert filter_offers([outra], db, _cfg()) == [outra]


def test_dois_anuncios_do_mesmo_produto_no_MESMO_run(db):
    """Nenhum dos dois está em `posted` ainda, então o portão do banco deixaria
    os dois passarem — e o run publicaria a repetição de uma vez só."""
    a = _oferta("i1", "Boneca Bebê Reborn Unicórnio Menina Original")
    b = _oferta("i2", "Bebê Reborn Boneca Unicórnio Menina Realista")
    passaram, stats = filter_offers_with_stats([a, b], db, _cfg())
    assert passaram == [a]
    assert stats.dedupe_titulo == 1


def test_a_janela_do_titulo_e_mais_CURTA_que_a_do_id(db):
    """A chave é ampla de propósito (ignora tamanho, cor e vendedor), então uma
    janela longa apagaria categorias inteiras do estoque. Fora da janela, o
    produto volta a poder sair."""
    import datetime as dt

    _publicado(db, "i1", "Boneca Bebê Reborn Unicórnio Menina")
    db.conn.execute("UPDATE posted SET posted_at=?",
                    ((dt.datetime.now(dt.timezone.utc)
                      - dt.timedelta(days=10)).isoformat(),))
    db.conn.commit()
    nova = _oferta("i2", "Bebê Reborn Boneca Unicórnio Realista")
    assert filter_offers([nova], db, _cfg(dias=7)) == [nova]
    assert filter_offers([nova], db, _cfg(dias=30)) == []


def test_zero_desliga_o_portao(db):
    _publicado(db, "i1", "Boneca Bebê Reborn Unicórnio Menina")
    nova = _oferta("i2", "Bebê Reborn Boneca Unicórnio Realista")
    assert filter_offers([nova], db, _cfg(dias=0)) == [nova]


def test_o_config_real_tem_o_portao_ligado_e_curto():
    import yaml

    with open("config.yaml", encoding="utf-8") as f:
        sel = yaml.safe_load(f)["selection"]
    assert sel["dedupe_titulo_dias"] > 0
    assert sel["dedupe_titulo_dias"] < sel["dedupe_days"]


def test_o_corte_aparece_no_resumo(db):
    """Um portão que corta em silêncio é o defeito que este projeto passou a
    fase 5A inteira consertando — "50 buscadas → 0 candidatas" tem de dizer
    QUEM cortou."""
    _publicado(db, "i1", "Boneca Bebê Reborn Unicórnio Menina")
    _, stats = filter_offers_with_stats(
        [_oferta("i2", "Bebê Reborn Boneca Unicórnio Realista")], db, _cfg())
    assert "título repetido: 1" in stats.resumo()
    assert stats.total == 1

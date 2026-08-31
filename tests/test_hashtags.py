"""Fase 5U (U4) — hashtags derivadas da categoria, no fim da legenda.

Nenhum dos 5 posts publicados até 2026-08-29 tinha hashtag. Para uma conta sem
audiência isso é um sinal de categorização a menos: a hashtag é o que diz ao
Instagram DE QUE o post é, para quem ainda não segue.

O que este módulo NÃO faz, de propósito: bloco de 30 hashtags genéricas. Ele
manda de 4 a 6 derivadas da `offer.category` mais as fixas da marca, e o mapa
mora no `config.yaml` — categoria é dado de negócio, não constante de código.
"""

import httpx
import pytest

from afiliado import categorias, cli, creative
from afiliado.channels.instagram_feed import InstagramFeedChannel
from afiliado.channels.instagram_reel import InstagramReelChannel
from afiliado.config import load_config
from afiliado.models import NO_CLAIM, CopyParts, Post
from tests.test_models import make_offer

COPY = CopyParts(headline="🔥 Achado do dia", description="Vale o clique.",
                 cta="Garanta o seu 👇")

SECAO = {
    "fixas": ["#fiscaldapromo", "#achadinhos"],
    "por_categoria": {
        "100630": ["#beleza", "#skincare", "#maquiagem", "#autocuidado"],
        "100636": ["#casa", "#organizacao", "#utilidadesdomesticas", "#decoracao"],
    },
}


# -- a regra, sozinha ---------------------------------------------------------

def test_derivadas_da_categoria_mais_as_fixas_da_marca():
    assert categorias.hashtags(SECAO, ["100630"]) == [
        "#beleza", "#skincare", "#maquiagem", "#autocuidado",
        "#fiscaldapromo", "#achadinhos"]


def test_categoria_desconhecida_cai_nas_fixas_e_NAO_quebra_o_post():
    """A categoria vem de dados de terceiros. Um ID novo da Shopee não pode
    derrubar a legenda — nem inventar uma hashtag que ninguém revisou."""
    assert categorias.hashtags(SECAO, ["99999"]) == ["#fiscaldapromo", "#achadinhos"]
    assert categorias.hashtags(SECAO, [""]) == ["#fiscaldapromo", "#achadinhos"]
    assert categorias.hashtags(SECAO, [None]) == ["#fiscaldapromo", "#achadinhos"]
    assert categorias.hashtags(SECAO, []) == ["#fiscaldapromo", "#achadinhos"]


def test_secao_ausente_ou_malformada_nao_levanta():
    for secao in (None, {}, [], "hashtags", {"por_categoria": "nao e dict"}):
        assert categorias.hashtags(secao, ["100630"]) == []


def test_o_teto_existe_para_que_isto_nunca_vire_um_bloco_de_30():
    secao = {"fixas": [f"#f{i}" for i in range(9)],
             "por_categoria": {"100630": [f"#t{i}" for i in range(20)]}}
    saida = categorias.hashtags(secao, ["100630"])
    assert len(saida) == categorias.MAX_DERIVADAS + categorias.MAX_FIXAS
    assert categorias.MAX_DERIVADAS <= 6 and categorias.MAX_FIXAS == 2


def test_normaliza_o_que_o_config_escreveu_torto():
    """`#` a mais, `#` a menos e espaço no meio — hashtag com espaço não é
    hashtag, e "#" duplicado quebra a busca do Instagram."""
    secao = {"fixas": [], "por_categoria": {"100630": ["beleza", "##skincare",
                                                       "  maquiagem  ", "cuidado pessoal",
                                                       "", None]}}
    assert categorias.hashtags(secao, ["100630"]) == [
        "#beleza", "#skincare", "#maquiagem", "#cuidadopessoal"]


def test_o_carrossel_nao_repete_a_hashtag_de_duas_categorias_iguais():
    """Seis ofertas, duas categorias: as derivadas se somam sem repetir, e o
    teto continua valendo — é a mesma legenda, não seis."""
    saida = categorias.hashtags(SECAO, ["100630", "100636", "100630"])
    assert saida.count("#beleza") == 1
    assert "#casa" in saida
    assert len(saida) == categorias.MAX_DERIVADAS + categorias.MAX_FIXAS


# -- e como isso chega às legendas -------------------------------------------

def _post(**kw) -> Post:
    return Post(offer=make_offer(**{"category": "100630", **kw}), copy=COPY,
                affiliate_link="https://shope.ee/x", verdict=NO_CLAIM)


def _canal(cls, hashtags=SECAO):
    client = httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"id": "1"})))
    return cls("IGUSER", "IGTOKEN", "BOTTOKEN", "OPSCHAT", client=client,
               hashtags=hashtags)


def _legendas(post, hashtags=SECAO) -> dict[str, str]:
    return {
        "feed": _canal(InstagramFeedChannel, hashtags)._build_caption(post),
        "reel": _canal(InstagramReelChannel, hashtags).legenda(post),
        "carrossel": cli.legenda_do_carrossel([post], "1 OFERTA.", "O Fiscal olhou.",
                                              hashtags=hashtags),
    }


@pytest.mark.parametrize("superficie", ["feed", "reel", "carrossel"])
def test_as_hashtags_fecham_a_legenda_DEPOIS_do_bloco_indexavel(superficie):
    """A ordem importa: o bloco indexável é a página de busca do Google (nome
    completo do produto, categoria por nome, janela) e a assinatura é o fecho
    de marca. As hashtags vêm depois — elas são endereçamento, não conteúdo."""
    legenda = _legendas(_post())[superficie]
    assert legenda.rstrip().endswith("#beleza #skincare #maquiagem #autocuidado "
                                     "#fiscaldapromo #achadinhos")
    assert legenda.index(creative.ASSINATURA) < legenda.index("#beleza")


@pytest.mark.parametrize("superficie", ["feed", "reel", "carrossel"])
def test_sem_secao_de_hashtags_a_legenda_e_a_de_antes(superficie):
    """Config sem a seção não pode deixar linha em branco pendurada no fim."""
    legenda = _legendas(_post(), hashtags=None)[superficie]
    assert "#" not in legenda.split(creative.ASSINATURA)[-1]
    assert legenda.rstrip().endswith(creative.ASSINATURA)


@pytest.mark.parametrize("superficie", ["feed", "reel", "carrossel"])
def test_categoria_desconhecida_nao_derruba_a_legenda(superficie):
    legenda = _legendas(_post(category="99999"))[superficie]
    assert legenda.rstrip().endswith("#fiscaldapromo #achadinhos")
    assert "99999" not in legenda


def test_o_config_de_producao_cobre_as_categorias_que_o_projeto_publica():
    """As cinco raízes da Shopee em `selection.category_ids` e as do pool do ML
    (`categorias.NOMES`). Uma raiz sem hashtag publica só as fixas — não
    quebra, mas perde o sinal que a fase existe para dar."""
    cfg = load_config("config.yaml")
    secao = cfg.get("hashtags")
    assert isinstance(secao, dict), "config.yaml sem a seção `hashtags:`"
    assert len(secao["fixas"]) == categorias.MAX_FIXAS
    for cid in cfg["selection"]["category_ids"]["shopee"]:
        derivadas = categorias.hashtags(secao, [cid])[:-categorias.MAX_FIXAS]
        assert 4 <= len(derivadas) <= categorias.MAX_DERIVADAS, cid
    for cid in categorias.NOMES:
        assert categorias.hashtags(secao, [cid])[:-categorias.MAX_FIXAS], cid


def test_os_canais_montados_recebem_a_secao_do_config(monkeypatch, tmp_path):
    """O elo que faltava: sem isto a seção existiria no config e nenhuma
    legenda de produção a veria."""
    from afiliado.state import StateDB
    for nome, valor in (("TELEGRAM_BOT_TOKEN", "tok"), ("TELEGRAM_OPS_CHAT_ID", "999"),
                        ("IG_USER_ID", "IGUSER"), ("IG_ACCESS_TOKEN", "IGTOKEN")):
        monkeypatch.setenv(nome, valor)
    cfg = load_config("config.yaml")
    db = StateDB(tmp_path / "s.db")
    canais, _ = cli._build_channels({**cfg, "channels": {"instagram_feed": True,
                                                         "instagram_story": True}}, db=db)
    db.close()
    for canal in canais:
        assert canal.hashtags == cfg["hashtags"], canal.name

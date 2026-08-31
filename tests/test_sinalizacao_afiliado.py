"""Fase 5U — a conta assume que é afiliada, em TODA superfície que publica.

O motivo está em `docs/superpowers/reviews/2026-08-26-analise-adversarial.md`,
item A7, reescrito em 2026-08-30: o pior caso não é representação no CONAR, é a
Shopee **rescindir o programa unilateralmente** (Termos, 2.3 e 7.2, 7 dias de
aviso) — e com ele vão a comissão, a integração, o pool de links e o histórico.
O guia CONAR de 01/06/2026 exige identificação **visível na primeira
visualização**, não em nota de rodapé nem só no trecho da legenda que o
Instagram esconde atrás do "mais".

Estes testes afirmam duas coisas, e é por elas que a fase existe:

1. **um lugar só decide** o texto (`creative.AFILIADO` / `creative.AFILIADO_NA_ARTE`),
   como `pricing.sem_cupom` faz com o rótulo do preço — trocar a frase é mexer
   em UMA linha e todas as superfícies acompanham;
2. **a frase é a PRIMEIRA linha** de todo texto publicado. É a única colocação
   que sobrevive ao corte do "mais" (~125 caracteres no Instagram) e à prévia
   da notificação do Telegram.
"""

import httpx
import pytest

from afiliado import creative, message
from afiliado.channels.instagram_feed import InstagramFeedChannel
from afiliado.channels.instagram_reel import InstagramReelChannel
from afiliado.models import NO_CLAIM, CopyParts, Post
from tests.test_models import make_offer

COPY = CopyParts(headline="🔥 Achado do dia", description="Vale o clique.",
                 cta="Garanta o seu 👇")

# O que o Instagram mostra antes do "mais" (medida corrente da plataforma). O
# número exato varia com a fonte e o aparelho; o que este teste trava é a
# ORDEM DE GRANDEZA — a frase não pode depender de a legenda ser expandida.
CORTE_DO_MAIS = 125


def _post(**kw) -> Post:
    offer = make_offer(**kw)
    return Post(offer=offer, copy=COPY, affiliate_link="https://shope.ee/x",
                verdict=NO_CLAIM)


def _canal(cls):
    client = httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"id": "1"})))
    return cls("IGUSER", "IGTOKEN", "BOTTOKEN", "OPSCHAT", client=client)


def _textos(post: Post) -> dict[str, str]:
    """Todo texto que vai ao público, por superfície."""
    from afiliado import cli
    return {
        "telegram": message.build_message(post.offer, post.copy,
                                          post.affiliate_link, post.verdict),
        "feed": _canal(InstagramFeedChannel)._build_caption(post),
        "reel": _canal(InstagramReelChannel).legenda(post),
        "carrossel": cli.legenda_do_carrossel([post], "3 OFERTAS. 1 É REAL.",
                                              "O Fiscal olhou o histórico."),
    }


@pytest.mark.parametrize("superficie", ["telegram", "feed", "reel", "carrossel"])
def test_a_frase_de_afiliado_abre_todo_texto_publicado(superficie):
    texto = _textos(_post())[superficie]
    assert texto.startswith(creative.AFILIADO), texto[:120]
    # E ela cabe INTEIRA no que o Instagram mostra antes do "mais".
    assert texto.index(creative.AFILIADO) + len(creative.AFILIADO) <= CORTE_DO_MAIS


def test_a_segunda_linha_do_reel_ainda_e_o_gancho():
    """O Reel mostra DUAS linhas sobre o vídeo. A primeira passa a ser a
    sinalização; a segunda continua sendo o gancho — uma linha em branco ali
    gastaria metade do que o formato deixa ler."""
    linhas = _canal(InstagramReelChannel).legenda(_post()).split("\n")
    assert linhas[0] == creative.AFILIADO
    assert linhas[1] == COPY.headline


def test_um_lugar_so_decide_a_frase(monkeypatch):
    """`pricing.sem_cupom` é o molde: a régua decide num lugar e as superfícies
    importam. Trocar `creative.AFILIADO` tem de mudar as quatro de uma vez —
    se alguma reimplementou a frase, este teste a encontra."""
    monkeypatch.setattr(creative, "AFILIADO", "Isto é publicidade paga")
    for superficie, texto in _textos(_post()).items():
        assert texto.startswith("Isto é publicidade paga"), superficie


def test_a_frase_diz_o_que_o_dono_ganha_e_o_que_o_seguidor_nao_perde():
    """A escolha de TEXTO da fase, e o que ela recusa: `#publi` é jargão de
    influenciador, e esta conta se chama Fiscal — explicar a própria
    remuneração é coerente com ela, o jargão a contradiz."""
    assert "afiliado" in creative.AFILIADO.lower()
    assert "comissão" in creative.AFILIADO.lower()
    assert "#publi" not in creative.AFILIADO.lower()


def test_a_forma_da_arte_e_curta_e_diz_o_essencial():
    """Na arte não cabe a frase inteira sem desestabilizar um layout calibrado
    em pixel — a forma reduzida carrega a parte que identifica."""
    assert creative.AFILIADO_NA_ARTE.lower() == "link de afiliado"
    assert len(creative.AFILIADO_NA_ARTE) < len(creative.AFILIADO)


def test_a_frase_nao_vira_alegacao_de_desconto():
    """Ela entra na legenda de TODO post, inclusive os de modo B — onde
    `copywriter.alega_desconto` proíbe palavra de desconto. Se a frase
    escolhida tropeçasse nessa régua, o post de modo B ficaria inválido."""
    from afiliado import copywriter
    copy = CopyParts(headline=creative.AFILIADO, description="", cta="")
    assert not copywriter.alega_desconto(copy)

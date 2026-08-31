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


def test_a_frase_descreve_o_destino_do_link():
    """O TEXTO EM VIGOR, decidido pelo dono em 2026-08-30 — e ele NÃO é
    sinalização de afiliado.

    A frase anterior dizia "ganho comissão"; o dono a retirou ("afasta a
    possibilidade de compra"). O que ficou descreve para onde o link leva.
    Este teste afirma o que a frase É, para que ninguém a leia como
    conformidade: o risco do A7 segue NÃO mitigado, e está escrito assim no
    documento e na constante."""
    assert "link" in creative.AFILIADO.lower()
    assert "comissão" not in creative.AFILIADO.lower()
    assert "#publi" not in creative.AFILIADO.lower()


def test_o_chip_da_arte_esta_desligado_e_religa_por_uma_constante():
    """Vazio = desligado, no molde de `pricing.MOSTRAR_SEM_CUPOM`.

    Está vazio porque o botão do rodapé já diz "LINK NA SHOPEE" / "LINK NO
    MERCADO LIVRE" — um chip repetindo que o link leva à loja seria a mesma
    informação duas vezes, na faixa de identidade da conta. O teste afirma os
    DOIS estados para que a geometria calibrada na 5U continue guardada."""
    assert creative.AFILIADO_NA_ARTE == ""
    # Desligado, o desenho não acontece — e não estoura por falta de caixa.
    from PIL import Image, ImageDraw
    d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    creative._draw_afiliado_chip(d, {"text": "", "box": (0, 0, 0, 0)})


def test_a_frase_nao_vira_alegacao_de_desconto():
    """Ela entra na legenda de TODO post, inclusive os de modo B — onde
    `copywriter.alega_desconto` proíbe palavra de desconto. Se a frase
    escolhida tropeçasse nessa régua, o post de modo B ficaria inválido."""
    from afiliado import copywriter
    copy = CopyParts(headline=creative.AFILIADO, description="", cta="")
    assert not copywriter.alega_desconto(copy)


# =============================================================================
# A ARTE. O texto viaja na legenda; a PEÇA precisa dizer o mesmo, porque é ela
# que aparece no story (onde não há legenda nenhuma) e é ela que alguém salva,
# recorta ou reposta sem o texto junto.
# =============================================================================

import io

from PIL import Image

from afiliado import creative as c
from afiliado.models import Verdict

CHIP = creative.AFILIADO_NA_ARTE.upper()
SELO = Verdict("B", 0, "🏷️ Menor preço dos últimos 6 meses (verificado)", 180)
NOME_LONGUISSIMO = "Fiscal da Promo do Brasil e Arredores S/A"


def _foto_png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (600, 600), (120, 40, 200)).save(buf, "PNG")
    return buf.getvalue()


def _client_da_foto() -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, content=_foto_png(),
                                 headers={"content-type": "image/png"})))


def _planos():
    offer = make_offer()
    posts = [Post(offer=offer, copy=COPY, affiliate_link="x", verdict=SELO)] * 2
    slides = c.carrossel_plan(posts, "2 OFERTAS. 1 É REAL.", "O Fiscal olhou.")
    return {
        "story": c.story_plan(offer, SELO),
        "feed": c.feed_plan(offer, SELO),
        "reel": c.reel_plan(offer, SELO),
        **{f"carrossel-{i}/{s['tipo']}": s for i, s in enumerate(slides, start=1)},
    }


def test_toda_peca_publicada_carrega_a_sinalizacao():
    """Story, feed, Reel e os TRÊS tipos de slide do carrossel — capa e fecho
    inclusive. A capa é a primeira visualização do álbum: se alguma peça
    pudesse sair sem a marca, seria justamente ela."""
    for nome, plan in _planos().items():
        assert plan.get("afiliado") == CHIP, nome


def test_a_sinalizacao_da_arte_nao_come_o_corpo_da_peca():
    """Ela mora na FAIXA DO CABEÇALHO, acima do card da foto. O corpo
    (título, pill, meta, selo) tem um guarda de overflow que já derrubou meta e
    selo por 30 px — a sinalização não pode disputar aquele orçamento."""
    story = c.story_plan(make_offer(), SELO)
    assert story["afiliado_box"][3] <= c.STORY_CARD_BOX[1]
    feed = c.feed_plan(make_offer(), SELO)
    assert feed["afiliado_box"][3] <= 158            # topo do card do feed


def test_a_sinalizacao_nunca_sai_do_canvas_nem_com_um_nome_de_marca_enorme():
    for largura, pad, plan in (
            (c.STORY_SIZE[0], c.STORY_PAD,
             c.story_plan(make_offer(), SELO, brand_name=NOME_LONGUISSIMO)),
            (c.FEED_SIZE[0], c.FEED_PAD,
             c.feed_plan(make_offer(), SELO, brand_name=NOME_LONGUISSIMO))):
        x0, _, x1, _ = plan["afiliado_box"]
        assert x0 >= 0 and x1 <= largura - pad


def test_a_sinalizacao_nao_encosta_no_contador_do_carrossel():
    """O contador ("2/6") mora no canto superior direito do slide, na mesma
    faixa. Dois elementos disputando aquela linha é como se perde um deles."""
    draw = c.ImageDraw.Draw(Image.new("RGB", (1, 1)))
    contador = c._contador_box(draw, c.CARROSSEL_SIZE[0], 2, 6)
    plan = c.feed_plan(make_offer(), SELO)
    assert plan["afiliado_box"][2] < contador[0]


def test_o_chip_desligado_nao_pinta_nada_na_faixa_do_cabecalho():
    """O inverso do teste da 5U: com `AFILIADO_NA_ARTE` vazio, a caixa onde o
    chip ficaria não tem NENHUMA das duas cores dele. É o que prova que
    desligar é desligar, e não desenhar por baixo de outra coisa."""
    cliente = _client_da_foto()
    for png, plan in ((creative.render_story(make_offer(), COPY, SELO, client=cliente),
                       c.story_plan(make_offer(), SELO)),
                      (creative.render_feed(make_offer(), COPY, SELO, client=cliente),
                       c.feed_plan(make_offer(), SELO))):
        img = Image.open(io.BytesIO(png))
        x0, y0, x1, y1 = (round(v) for v in plan["afiliado_box"])
        cores = set(img.crop((x0, y0, x1 + 1, y1 + 1)).convert("RGB").getcolors(1 << 20) or [])
        cores = {cor for _, cor in cores}
        assert c.PILL_BORDER not in cores
        assert c.SURFACE not in cores


def test_o_grafico_do_flagrante_nao_se_diz_link_de_afiliado():
    """A ÚNICA peça que não leva a marca, e é por honestidade: o flagrante é o
    histórico de preço de um produto, vai ao chat de operações para o dono
    decidir e NÃO carrega link nenhum. Carimbar "link de afiliado" nela seria
    afirmar um link que a peça não tem."""
    from datetime import date, timedelta
    hoje = date(2026, 8, 30)
    historico = [(hoje - timedelta(days=i), 2600) for i in range(30, 0, -1)]
    plan = c.grafico_plan(make_offer(price_ref_cents=2600, price_current_cents=1890),
                          historico, NO_CLAIM)
    assert "afiliado" not in plan

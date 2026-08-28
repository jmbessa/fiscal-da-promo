"""Fase 5D — `creative.render_carrossel`: o motor de retenção.

Carrossel tem o maior save rate do Instagram (0,05%, 9x a imagem única) e é o
formato que retém quem o Reel trouxe; imagem única perdeu 45,98% de engajamento
no ano e sai da grade. Ver `docs/feed.md`.

Como o resto do design system, a composição é testada por `carrossel_plan` —
o que cada slide vai ser, na ordem — e não por pixel.
"""

import io

import httpx
import pytest
from PIL import Image

from afiliado import pricing
from afiliado.creative import (
    ASSINATURA,
    CARROSSEL_MAX_SLIDES,
    CARROSSEL_MIN_OFERTAS,
    CARROSSEL_SIZE,
    CTA_CARROSSEL,
    DEFAULT_HANDLE,
    carrossel_fotos,
    carrossel_plan,
    render_carrossel,
)
from afiliado.errors import SourceError
from afiliado.models import NO_CLAIM, CopyParts, Post, Verdict
from tests.test_models import make_offer, make_offer_ref

COPY = CopyParts(headline="h", description="d", cta="c")
TITULO = "3 OFERTAS. 1 É REAL."
SUBTITULO = "O Fiscal olhou o histórico de preço de cada uma."


def _product_png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (800, 800), (120, 40, 200)).save(buf, "PNG")
    return buf.getvalue()


def _client(handler=None) -> httpx.Client:
    def ok(request):
        return httpx.Response(200, content=_product_png(),
                              headers={"content-type": "image/png"})
    return httpx.Client(transport=httpx.MockTransport(handler or ok))


def _post(n: int = 1, verdict: Verdict = NO_CLAIM, **kw) -> Post:
    kw.setdefault("item_id", f"item{n}")
    kw.setdefault("title", f"Produto {n}")
    # Uma URL por item: é o que deixa o `MockTransport` derrubar a foto de UM
    # produto e não a de todos (a tolerância da rodada de fechamento).
    kw.setdefault("image_url", f"https://cf.shopee.com.br/file/item{n}.jpg")
    return Post(offer=make_offer(**kw), copy=COPY, affiliate_link="", verdict=verdict)


def _posts(n: int) -> list[Post]:
    return [_post(i) for i in range(1, n + 1)]


def _desenha(posts: list[Post], client: httpx.Client | None = None,
             avisos: list[str] | None = None, **kw) -> list[bytes]:
    """As duas etapas do carrossel na ordem em que o CLI as chama: resolver as
    fotos (quem entra na peça) e só então desenhar."""
    return render_carrossel(carrossel_fotos(posts, client or _client(), avisos),
                            TITULO, SUBTITULO, **kw)


# --- A ordem e o teto de slides ----------------------------------------------

def test_carrossel_plan_capa_ofertas_e_fecho_nessa_ordem():
    slides = carrossel_plan(_posts(3), TITULO, SUBTITULO)
    assert [s["tipo"] for s in slides] == ["capa", "oferta", "oferta", "oferta", "fecho"]
    assert [s["item_id"] for s in slides if s["tipo"] == "oferta"] == [
        "item1", "item2", "item3"]
    assert [s["indice"] for s in slides if s["tipo"] == "oferta"] == [1, 2, 3]
    assert all(s["total"] == 3 for s in slides if s["tipo"] == "oferta")


def test_o_slide_de_oferta_leva_o_sem_cupom_da_shopee():
    """O slide de oferta É a arte de feed (`_feed_plan`/`_draw_feed_body`), então
    o rótulo da fase 5K viaja junto — e a legenda do álbum
    (`cli.legenda_do_carrossel`) diz o mesmo, item a item."""
    slides = carrossel_plan([_post(1), _post(2, source="meli")], TITULO, SUBTITULO)
    ofertas = [s for s in slides if s["tipo"] == "oferta"]
    assert [s["sem_cupom"] for s in ofertas] == ["SEM CUPOM", ""]


def test_carrossel_nunca_passa_de_oito_slides():
    """Capa + fecho ocupam dois lugares: sobram seis para ofertas. A pesquisa
    só sustenta "6 a 8 slides" fracamente, e menos slide é mais barato de
    revisar — mas o teto é duro."""
    slides = carrossel_plan(_posts(20), TITULO, SUBTITULO)
    assert len(slides) == CARROSSEL_MAX_SLIDES == 8
    assert sum(1 for s in slides if s["tipo"] == "oferta") == 6
    assert [s["item_id"] for s in slides if s["tipo"] == "oferta"] == [
        f"item{i}" for i in range(1, 7)]


def test_capa_vende_o_conceito_e_nao_o_preco():
    capa = carrossel_plan(_posts(3), TITULO, SUBTITULO)[0]
    assert capa["titulo_linhas"] and " ".join(capa["titulo_linhas"]).startswith("3 OFERTAS")
    assert capa["subtitulo_linhas"]
    # Nada de preço na capa: ela vende o CONCEITO.
    assert "preco" not in capa and "selo" not in capa


def test_fecho_traz_a_assinatura_o_handle_e_o_link_na_bio():
    fecho = carrossel_plan(_posts(2), TITULO, SUBTITULO)[-1]
    assert fecho["assinatura"] == ASSINATURA == "Quem conferiu? O Fiscal."
    assert fecho["handle"] == DEFAULT_HANDLE
    assert fecho["cta"] == CTA_CARROSSEL == "LINK NA BIO"
    assert carrossel_plan(_posts(2), TITULO, SUBTITULO,
                          handle="@outro")[-1]["handle"] == "@outro"


def test_carrossel_sem_oferta_nenhuma_levanta():
    with pytest.raises(SourceError):
        carrossel_plan([], TITULO, SUBTITULO)
    with pytest.raises(SourceError):
        carrossel_fotos([], _client())
    with pytest.raises(SourceError):
        render_carrossel([], TITULO, SUBTITULO)


# --- O veredito, de novo sem recalcular nada ---------------------------------

def test_slide_de_oferta_obedece_ao_veredito_do_post():
    offer = make_offer_ref(2600, price_current_cents=1890, price_floor_cents=1890,
                           price_floor_window_days=90, rating=4.9, sales=30000)
    v = pricing.verdict(offer, 10)
    assert v.mode == "A" and v.discount_pct == 27
    post = Post(offer=offer, copy=COPY, affiliate_link="", verdict=v)
    slide = carrossel_plan([post], TITULO, SUBTITULO)[1]
    assert slide["badge_pct"] == 27
    assert slide["riscado"] == "R$ 26,00"          # a NOSSA referência
    assert slide["selo"] == "MENOR PREÇO VERIFICADO · 3 MESES"
    assert slide["preco"] == "R$ 18,90"

    mudo = carrossel_plan([Post(offer=offer, copy=COPY, affiliate_link="",
                                verdict=NO_CLAIM)], TITULO, SUBTITULO)[1]
    assert mudo["badge_pct"] == 0 and mudo["riscado"] == "" and mudo["selo"] == ""


def test_slide_de_oferta_ignora_o_de_inflado_do_vendedor():
    inflado = make_offer(item_id="x", price_original_cents=35000,
                         price_current_cents=4900, rating=4.9, sales=30000)
    sem_de = make_offer(item_id="x", price_original_cents=4900,
                        price_current_cents=4900, rating=4.9, sales=30000)
    a = _desenha([Post(offer=inflado, copy=COPY, affiliate_link="")])
    b = _desenha([Post(offer=sem_de, copy=COPY, affiliate_link="")])
    assert a == b


# --- Fumaça: os PNGs saem na ordem e no tamanho certo -------------------------

def test_render_carrossel_devolve_um_png_por_slide():
    posts = _posts(3)
    imagens = _desenha(posts)
    assert len(imagens) == len(carrossel_plan(posts, TITULO, SUBTITULO)) == 5
    for png in imagens:
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        img = Image.open(io.BytesIO(png))
        assert img.size == CARROSSEL_SIZE == (1080, 1350)
        assert img.mode == "RGB"
    # capa, ofertas e fecho são artes DIFERENTES entre si
    assert len(set(imagens)) == 5


def test_render_carrossel_respeita_o_teto_de_oito():
    assert len(_desenha(_posts(20))) == CARROSSEL_MAX_SLIDES


# --- Rodada de fechamento (F4): uma foto ruim não derruba o post inteiro ------
#
# `render_carrossel` era tudo-ou-nada, como `render_feed`. Só que com SEIS
# fotos a chance de perder o post é seis vezes a de um post único — e o que se
# perde é o post com seis ofertas, não um slide. O produto cuja foto não baixa
# passa a ser PULADO, com aviso, e o carrossel sai com os que sobraram.

def _sem_a_foto_de(*itens: str):
    """Transport que responde 404 para as fotos destes itens e 200 no resto."""
    def handler(request):
        if any(f"/{item}." in request.url.path for item in itens):
            return httpx.Response(404)
        return httpx.Response(200, content=_product_png(),
                              headers={"content-type": "image/png"})
    return _client(handler)


def test_carrossel_pula_o_produto_cuja_foto_nao_baixa():
    avisos: list[str] = []
    imagens = _desenha(_posts(4), _sem_a_foto_de("item2"), avisos=avisos)
    # capa + 3 ofertas + fecho: o post saiu, sem o item2.
    assert len(imagens) == 5
    assert len(avisos) == 1 and "item2" in avisos[0]


def test_o_contador_conta_os_slides_que_sobraram():
    """"2/4" num carrossel de 3 ofertas seria o desenho mentindo sobre si
    mesmo: o total é o que RESTOU, não o que foi pedido."""
    fotos = carrossel_fotos(_posts(4), _sem_a_foto_de("item2"))
    ofertas = [s for s in carrossel_plan([p for p, _ in fotos], TITULO, SUBTITULO)
               if s["tipo"] == "oferta"]
    assert [s["item_id"] for s in ofertas] == ["item1", "item3", "item4"]
    assert [s["indice"] for s in ofertas] == [1, 2, 3]
    assert all(s["total"] == 3 for s in ofertas)


def test_plano_e_desenho_concordam_por_construcao():
    """O motivo de o tudo-ou-nada ter ficado de pé na 5D: se o desenho pula um
    slide e o plano não, os testes deixam de descrever a peça. Quem decide o
    elenco passou a ser uma etapa ANTES dos dois — os dois recebem a mesma
    lista e não têm como discordar."""
    fotos = carrossel_fotos(_posts(5), _sem_a_foto_de("item1", "item4"))
    sobreviventes = [p for p, _ in fotos]
    imagens = render_carrossel(fotos, TITULO, SUBTITULO)
    assert len(imagens) == len(carrossel_plan(sobreviventes, TITULO, SUBTITULO)) == 5


def test_carrossel_falha_quando_sobra_menos_de_duas_ofertas():
    """A tolerância tem piso: um "termômetro da semana" com uma oferta só não
    é o post que a capa promete. Abaixo de duas, é melhor não publicar."""
    avisos: list[str] = []
    with pytest.raises(SourceError) as exc:
        carrossel_fotos(_posts(4), _sem_a_foto_de("item1", "item2", "item3"), avisos)
    assert "item1" in str(exc.value)
    assert str(CARROSSEL_MIN_OFERTAS) in str(exc.value)
    assert len(avisos) == 3          # cada perda avisada, mesmo com o post morto


def test_o_piso_nao_reprova_um_carrossel_pedido_pequeno():
    """O piso existe contra a DEGRADAÇÃO — pedir seis e ficar com uma. Pedir
    uma e receber uma é decisão de quem chama, e o desenho não a desfaz."""
    assert len(_desenha(_posts(1))) == 3


def test_carrossel_com_todas_as_fotos_quebradas_levanta_com_o_item_no_texto():
    """Sem nenhuma foto não há post nenhum — e o erro diz qual item derrubou o
    quê, para o log do Actions não virar adivinhação."""
    with pytest.raises(SourceError) as exc:
        carrossel_fotos(_posts(2), _sem_a_foto_de("item1", "item2"))
    assert "item1" in str(exc.value) and "item2" in str(exc.value)


def test_render_carrossel_titulo_longo_de_capa_nao_quebra():
    imagens = render_carrossel(carrossel_fotos(_posts(2), _client()),
                               " ".join(["palavra"] * 40), " ".join(["outra"] * 30))
    assert all(Image.open(io.BytesIO(p)).size == (1080, 1350) for p in imagens)


def test_render_carrossel_muda_com_o_handle():
    fotos = carrossel_fotos(_posts(2), _client())
    sem = render_carrossel(fotos, TITULO, SUBTITULO)
    com = render_carrossel(fotos, TITULO, SUBTITULO, handle="@ofiscaldapromo")
    assert sem != com

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
    CARROSSEL_SIZE,
    CTA_CARROSSEL,
    DEFAULT_HANDLE,
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
    return Post(offer=make_offer(**kw), copy=COPY, affiliate_link="", verdict=verdict)


def _posts(n: int) -> list[Post]:
    return [_post(i) for i in range(1, n + 1)]


# --- A ordem e o teto de slides ----------------------------------------------

def test_carrossel_plan_capa_ofertas_e_fecho_nessa_ordem():
    slides = carrossel_plan(_posts(3), TITULO, SUBTITULO)
    assert [s["tipo"] for s in slides] == ["capa", "oferta", "oferta", "oferta", "fecho"]
    assert [s["item_id"] for s in slides if s["tipo"] == "oferta"] == [
        "item1", "item2", "item3"]
    assert [s["indice"] for s in slides if s["tipo"] == "oferta"] == [1, 2, 3]
    assert all(s["total"] == 3 for s in slides if s["tipo"] == "oferta")


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
        render_carrossel([], TITULO, SUBTITULO, client=_client())


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
    cli = _client()
    a = render_carrossel([Post(offer=inflado, copy=COPY, affiliate_link="")],
                         TITULO, SUBTITULO, client=cli)
    b = render_carrossel([Post(offer=sem_de, copy=COPY, affiliate_link="")],
                         TITULO, SUBTITULO, client=cli)
    assert a == b


# --- Fumaça: os PNGs saem na ordem e no tamanho certo -------------------------

def test_render_carrossel_devolve_um_png_por_slide():
    posts = _posts(3)
    imagens = render_carrossel(posts, TITULO, SUBTITULO, client=_client())
    assert len(imagens) == len(carrossel_plan(posts, TITULO, SUBTITULO)) == 5
    for png in imagens:
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        img = Image.open(io.BytesIO(png))
        assert img.size == CARROSSEL_SIZE == (1080, 1350)
        assert img.mode == "RGB"
    # capa, ofertas e fecho são artes DIFERENTES entre si
    assert len(set(imagens)) == 5


def test_render_carrossel_respeita_o_teto_de_oito():
    imagens = render_carrossel(_posts(20), TITULO, SUBTITULO, client=_client())
    assert len(imagens) == CARROSSEL_MAX_SLIDES


def test_render_carrossel_propaga_falha_de_imagem_do_produto():
    """Uma foto que não baixa derruba o carrossel inteiro, com o item no erro —
    é o mesmo contrato de `render_feed`, e é o que mantém `carrossel_plan` e o
    desenho dizendo a MESMA coisa. Quem chama decide o que fazer."""
    def handler(request):
        return httpx.Response(404)

    with pytest.raises(SourceError) as exc:
        render_carrossel(_posts(2), TITULO, SUBTITULO, client=_client(handler))
    assert "item1" in str(exc.value)


def test_render_carrossel_titulo_longo_de_capa_nao_quebra():
    imagens = render_carrossel(_posts(2), " ".join(["palavra"] * 40),
                               " ".join(["outra"] * 30), client=_client())
    assert all(Image.open(io.BytesIO(p)).size == (1080, 1350) for p in imagens)


def test_render_carrossel_muda_com_o_handle():
    posts = _posts(2)
    cli = _client()
    sem = render_carrossel(posts, TITULO, SUBTITULO, client=cli)
    com = render_carrossel(posts, TITULO, SUBTITULO, handle="@ofiscaldapromo", client=cli)
    assert sem != com

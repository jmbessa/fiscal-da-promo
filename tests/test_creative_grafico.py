"""Fase 5D — `creative.render_grafico_preco`: a peça que torna a conta original.

Desde 2026-04-30 o Instagram deixa de recomendar a não-seguidores o perfil que
posta majoritariamente conteúdo que não criou (foto de vendedor com etiqueta de
preço é exatamente isso). O gráfico de histórico que só nós temos é o que
transforma repostagem em conteúdo original — ver `docs/feed.md`.

Como o resto do design system, o desenho é testado por `grafico_plan` (o que
será desenhado), não por pixel; um teste de fumaça garante o PNG.
"""

import io
from datetime import date, timedelta

import pytest
from PIL import Image

from afiliado import pricing
from afiliado.creative import (
    ASSINATURA,
    DEFAULT_HANDLE,
    GRAFICO_SIZE,
    PICO_FATOR,
    ROTULO_MEDIANA,
    ROTULO_P25,
    grafico_plan,
    render_grafico_preco,
)
from afiliado.errors import SourceError
from afiliado.models import NO_CLAIM, Verdict
from tests.test_models import make_offer, make_offer_ref

HOJE = date(2026, 8, 27)


def _serie(precos: list[int], fim: date = HOJE) -> list[tuple[date, int]]:
    """Um preço por dia, terminando em `fim` (o último é o de hoje)."""
    inicio = fim - timedelta(days=len(precos) - 1)
    return [(inicio + timedelta(days=i), p) for i, p in enumerate(precos)]


def _caso_real() -> list[tuple[date, int]]:
    """O caso que a régua existe para pegar: 89 dias a R$ 26,00 e UM dia a
    R$ 68,90 — o "de" que o vendedor usa para anunciar 62% de desconto."""
    precos = [2600] * 90
    precos[60] = 6890
    return _serie(precos)


def _offer(**kw):
    kw.setdefault("price_current_cents", 2600)
    return make_offer_ref(2600, **kw)


# --- Detecção de pico ---------------------------------------------------------

def test_grafico_plan_marca_o_pico_de_um_dia_do_caso_real():
    plan = grafico_plan(_offer(), _caso_real(), NO_CLAIM)
    assert plan["pontos"] == 90
    assert len(plan["picos"]) == 1
    pico = plan["picos"][0]
    assert pico["dias"] == 1
    assert pico["rotulo"] == "1 dia"
    assert pico["cents"] == 6890
    assert plan["rotulos"]["picos"] == ["1 dia"]


def test_grafico_plan_nao_marca_pico_em_serie_estavel():
    # Oscilação normal (26,00 a 29,90) nunca passa de mediana x 1,5.
    precos = [2600 + (i % 7) * 50 for i in range(90)]
    assert max(precos) < 2600 * PICO_FATOR
    assert grafico_plan(_offer(), _serie(precos), NO_CLAIM)["picos"] == []


def test_grafico_plan_ignora_pico_que_dura_mais_de_dois_dias():
    """Preço alto por três dias seguidos não é etiqueta de um dia — é preço.
    A acusação só vale para o pico que não se sustenta."""
    precos = [2600] * 90
    precos[40:43] = [6890, 6890, 6890]
    plan = grafico_plan(_offer(), _serie(precos), NO_CLAIM)
    assert plan["picos"] == []
    # Dois dias ainda são flagrante.
    precos = [2600] * 90
    precos[40:42] = [6890, 6890]
    plan = grafico_plan(_offer(), _serie(precos), NO_CLAIM)
    assert [p["dias"] for p in plan["picos"]] == [2]
    assert plan["rotulos"]["picos"] == ["2 dias"]


def test_grafico_plan_marca_dois_picos_separados():
    precos = [2600] * 90
    precos[20] = 6890
    precos[70] = 7990
    plan = grafico_plan(_offer(), _serie(precos), NO_CLAIM)
    assert [p["cents"] for p in plan["picos"]] == [6890, 7990]


def test_grafico_plan_sem_referencia_nao_inventa_mediana_nem_pico():
    """Sem a nossa referência não há régua: nada de linha da mediana, nada de
    faixa do p25 e NENHUM pico — o pico só existe contra a mediana."""
    plan = grafico_plan(make_offer(price_current_cents=6890), _caso_real(), NO_CLAIM)
    assert plan["y_mediana"] is None
    assert plan["y_p25"] is None
    assert plan["picos"] == []


# --- A régua da casa, desenhada -----------------------------------------------

def test_grafico_plan_desenha_a_mediana_acima_da_faixa_do_p25():
    """p25 <= mediana: preço menor fica MAIS EMBAIXO na tela (y cresce para
    baixo). A faixa "promoção de verdade" vai do p25 ao fundo do gráfico."""
    offer = make_offer(price_ref_cents=2600, price_p25_cents=2000,
                       price_window_days=90, price_current_cents=1890)
    plan = grafico_plan(offer, _serie([2600] * 88 + [2000, 1890]), NO_CLAIM)
    assert plan["y_mediana"] < plan["y_p25"]
    # a faixa começa no p25 e desce até o fundo do gráfico
    assert plan["faixa_p25"][1] == plan["y_p25"]
    assert plan["faixa_p25"][3] == plan["plot"][3] > plan["y_p25"]
    assert plan["rotulos"]["mediana"] == "R$ 26,00"
    assert plan["rotulos"]["sempre"] == ROTULO_MEDIANA == "preço de sempre"
    assert plan["rotulos"]["p25"] == ROTULO_P25 == "promoção de verdade"


def test_grafico_plan_rotula_o_preco_de_hoje():
    plan = grafico_plan(_offer(price_current_cents=1890),
                        _serie([2600] * 89 + [1890]), NO_CLAIM)
    assert plan["rotulos"]["hoje"] == "R$ 18,90"
    assert plan["hoje_cents"] == 1890
    # O ponto de hoje é o último da série, na borda direita do gráfico.
    assert plan["x_hoje"] == plan["plot"][2]


def test_grafico_plan_traz_a_assinatura_e_o_handle_no_rodape():
    plan = grafico_plan(_offer(), _caso_real(), NO_CLAIM)
    assert plan["rotulos"]["assinatura"] == ASSINATURA == "Quem conferiu? O Fiscal."
    assert plan["rotulos"]["handle"] == DEFAULT_HANDLE == "@ofiscaldapromo"
    assert grafico_plan(_offer(), _caso_real(), NO_CLAIM,
                        handle="@outro")["rotulos"]["handle"] == "@outro"


def test_grafico_plan_diz_a_janela_real_da_serie():
    plan = grafico_plan(_offer(), _serie([2600] * 45), NO_CLAIM)
    assert plan["janela_dias"] == 45


# --- O veredito por cima (nunca recalculado) ----------------------------------

def test_grafico_plan_obedece_ao_veredito():
    """Selo e porcentagem vêm de `post.verdict` — o gráfico não recalcula nada.
    Mesma oferta, veredito diferente, desenho diferente."""
    offer = make_offer_ref(2600, price_current_cents=1890, price_floor_cents=1890,
                           price_floor_window_days=90)
    v = pricing.verdict(offer, 10)
    assert v == Verdict("A", 27, "🏷️ Menor preço dos últimos 3 meses (verificado)", 90)
    plan = grafico_plan(offer, _serie([2600] * 89 + [1890]), v)
    assert plan["selo"] == "MENOR PREÇO VERIFICADO · 3 MESES"
    assert plan["badge_pct"] == 27
    mudo = grafico_plan(offer, _serie([2600] * 89 + [1890]), NO_CLAIM)
    assert mudo["selo"] == "" and mudo["badge_pct"] == 0


# --- Fumaça: o PNG sai com o tamanho certo ------------------------------------

def test_render_grafico_preco_sai_no_tamanho_certo():
    png = render_grafico_preco(_offer(), _caso_real(), NO_CLAIM)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    img = Image.open(io.BytesIO(png))
    assert img.size == GRAFICO_SIZE == (1080, 1080)
    assert img.mode == "RGB"


def test_render_grafico_preco_com_dois_pontos_funciona():
    serie = _serie([2600, 1890])
    png = render_grafico_preco(_offer(price_current_cents=1890), serie, NO_CLAIM)
    assert Image.open(io.BytesIO(png)).size == (1080, 1080)


def test_render_grafico_preco_exige_dois_pontos():
    with pytest.raises(SourceError):
        render_grafico_preco(_offer(), _serie([2600]), NO_CLAIM)
    with pytest.raises(SourceError):
        render_grafico_preco(_offer(), [], NO_CLAIM)
    with pytest.raises(SourceError):
        grafico_plan(_offer(), [], NO_CLAIM)


def test_render_grafico_preco_nao_baixa_a_foto_do_produto():
    """O gráfico é 100% desenhado por nós — é justamente por isso que ele
    conta como conteúdo original. Nenhum cliente HTTP, nenhuma imagem de
    terceiro: uma URL impossível não pode atrapalhar."""
    offer = make_offer_ref(2600, price_current_cents=2600,
                           image_url="http://127.0.0.1:1/nao-existe.jpg")
    assert render_grafico_preco(offer, _caso_real(), NO_CLAIM)[:4] == b"\x89PNG"


def test_render_grafico_preco_serie_constante_nao_divide_por_zero():
    png = render_grafico_preco(_offer(), _serie([2600] * 30), NO_CLAIM)
    assert Image.open(io.BytesIO(png)).size == (1080, 1080)


def test_render_grafico_preco_aceita_serie_fora_de_ordem_e_com_dia_repetido():
    """A série vem de quem chama; o desenho não pode depender da ordem, e um
    dia repetido fica com o MENOR preço (a mesma regra conservadora do
    `price_log`)."""
    serie = _serie([2600] * 89 + [1890])
    plan_ordenado = grafico_plan(_offer(price_current_cents=1890), serie, NO_CLAIM)
    embaralhado = list(reversed(serie)) + [(HOJE, 9999)]
    plan = grafico_plan(_offer(price_current_cents=1890), embaralhado, NO_CLAIM)
    assert plan["pontos"] == plan_ordenado["pontos"]
    assert plan["hoje_cents"] == 1890


def test_render_grafico_preco_mostra_o_selo_do_veredito():
    offer = make_offer_ref(2600, price_current_cents=1890, price_floor_cents=1890,
                           price_floor_window_days=90)
    serie = _serie([2600] * 89 + [1890])
    com = render_grafico_preco(offer, serie, pricing.verdict(offer, 10))
    sem = render_grafico_preco(offer, serie, NO_CLAIM)
    assert com != sem

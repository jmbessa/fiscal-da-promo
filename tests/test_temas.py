"""Fase 5W — o carrossel TEMÁTICO, o único conteúdo inteiramente nosso do feed.

Todo o resto que a conta publica é foto do vendedor com a nossa moldura em
volta. Desde 30/04/2026 o Instagram deixou de recomendar a não-seguidores quem
publica majoritariamente conteúdo que não criou — a moldura provavelmente basta,
mas é a parte FRACA do argumento, e hoje é 100% do feed.

Os testes de conteúdo leem o `data/temas.yaml` DE VERDADE: ele é conteúdo
publicado, e a régua que vale para a legenda de uma oferta vale para ele.
"""

import textwrap

import pytest
import yaml

from afiliado import cli, creative, temas
from afiliado.errors import SourceError
from afiliado.state import StateDB


@pytest.fixture
def db(tmp_path):
    banco = StateDB(str(tmp_path / "state.db"), timezone="America/Sao_Paulo")
    yield banco
    banco.close()


def _tema(slug: str, n_slides: int = 3) -> temas.Tema:
    return temas.Tema(slug=slug, titulo="TÍTULO", subtitulo="sub",
                      slides=tuple(temas.Slide(titulo=f"t{i}", corpo=f"c{i}")
                                   for i in range(n_slides)))


def _yaml(tmp_path, conteudo: str):
    caminho = tmp_path / "temas.yaml"
    caminho.write_text(textwrap.dedent(conteudo), encoding="utf-8")
    return caminho


BOM = """
- slug: um
  capa: {titulo: "T", subtitulo: "S"}
  slides:
    - {titulo: "a", corpo: "A"}
    - {titulo: "b", corpo: "B"}
    - {titulo: "c", corpo: "C"}
"""


# -- o carregamento: erro de REDAÇÃO é vermelho, não silêncio ------------------

def test_carrega_o_arquivo_bom(tmp_path):
    (tema,) = temas.carrega(_yaml(tmp_path, BOM))
    assert tema.slug == "um"
    assert tema.titulo == "T" and tema.subtitulo == "S"
    assert [s.titulo for s in tema.slides] == ["a", "b", "c"]


@pytest.mark.parametrize("conteudo,pedaco", [
    ("[]", "esperava uma lista"),
    ("{}", "esperava uma lista"),
    ("- slug: ''\n  capa: {titulo: T, subtitulo: S}\n  slides: []", "slug"),
    ("- slug: x\n  capa: {subtitulo: S}\n  slides: []", "slide"),
    ("- slug: x\n  capa: {titulo: T, subtitulo: S}\n"
     "  slides: [{titulo: a, corpo: A}]", "slide"),
])
def test_arquivo_malformado_levanta_com_o_motivo(tmp_path, conteudo, pedaco):
    """Um tema pela metade geraria um slide em branco — pior do que não sair.
    E a mensagem tem de dizer O QUE está errado: quem conserta é uma pessoa
    editando texto, não um programador lendo stack trace."""
    with pytest.raises(SourceError) as erro:
        temas.carrega(_yaml(tmp_path, conteudo))
    assert pedaco in str(erro.value)


def test_arquivo_ausente_levanta(tmp_path):
    with pytest.raises(SourceError, match="não encontrado"):
        temas.carrega(tmp_path / "nao-existe.yaml")


def test_slug_repetido_e_recusado(tmp_path):
    """Dois temas com o mesmo slug dividiriam a mesma data de publicação, e um
    deles nunca sairia — a rotação quebrada EM SILÊNCIO."""
    with pytest.raises(SourceError, match="slug repetido"):
        temas.carrega(_yaml(tmp_path, BOM + BOM.replace("slug: um", "slug: um")))


def test_slides_demais_sao_recusados(tmp_path):
    """Capa e fecho ocupam dois dos 8 que a Meta aceita: sobram seis."""
    slides = "\n".join(f'    - {{titulo: "t{i}", corpo: "c{i}"}}' for i in range(7))
    with pytest.raises(SourceError, match="carrossel aceita"):
        temas.carrega(_yaml(tmp_path,
                            f'- slug: x\n  capa: {{titulo: T, subtitulo: S}}\n'
                            f'  slides:\n{slides}'))


# -- a rotação -----------------------------------------------------------------

def test_o_tema_nunca_publicado_vem_primeiro(db):
    acervo = [_tema("a"), _tema("b")]
    temas.marca_publicado(db, acervo[0])
    assert temas.escolhe(acervo, db).slug == "b"


def test_sai_o_que_esta_ha_mais_tempo_sem_sair(db):
    """Não há sorteio: com um acervo pequeno o sorteio repete, e repetir antes
    de o acervo inteiro ter ido ao ar desperdiça peça escrita à mão."""
    import datetime as dt

    acervo = [_tema("a"), _tema("b"), _tema("c")]
    temas.marca_publicado(db, acervo[0], dt.date(2026, 8, 10))
    temas.marca_publicado(db, acervo[1], dt.date(2026, 8, 1))
    temas.marca_publicado(db, acervo[2], dt.date(2026, 8, 20))
    assert temas.escolhe(acervo, db).slug == "b"


def test_a_escolha_e_reproduzivel_no_empate(db):
    assert temas.escolhe([_tema("z"), _tema("a")], db).slug == "a"


def test_acervo_vazio_devolve_nada(db):
    assert temas.escolhe([], db) is None


# -- a peça --------------------------------------------------------------------

def test_o_album_e_capa_mais_slides_mais_fecho():
    tema = _tema("x", n_slides=4)
    imagens = creative.render_carrossel_tema(tema, handle="@ofiscaldapromo")
    assert len(imagens) == 6
    assert all(img[:8] == b"\x89PNG\r\n\x1a\n" for img in imagens)


def test_o_slide_tematico_tem_o_tamanho_do_carrossel():
    import io

    from PIL import Image

    imagens = creative.render_carrossel_tema(_tema("x"), handle="@ofiscaldapromo")
    for img in imagens:
        assert Image.open(io.BytesIO(img)).size == creative.CARROSSEL_SIZE


def test_o_desenho_usa_os_MESMOS_maximos_que_o_carregamento_valida(monkeypatch):
    """`_wrap_title` CORTA o que não cabe, em silêncio. Se o desenho aceitasse
    menos linhas do que o carregamento valida, um tema APROVADO sairia
    truncado — a peça mentindo sobre si mesma.

    O teste não compara dois literais: ele espiona a chamada e confere que os
    números que chegaram ao desenho vieram de `afiliado.temas`. Dois literais
    iguais passam a divergir no dia em que alguém muda um só."""
    vistos = []
    original = creative._texto_dims

    def espiao(draw, text, size, width, max_lines, weight=700, altura_linha=1.04):
        vistos.append((size, max_lines))
        return original(draw, text, size, width, max_lines, weight, altura_linha)

    monkeypatch.setattr(creative, "_texto_dims", espiao)
    creative._render_slide_tema("t", "c", 2, 5, "@x", "Fiscal da Promo")
    assert (creative.TEMA_TITULO_SIZE, temas.TITULO_MAX_LINHAS) in vistos
    assert (creative.TEMA_CORPO_SIZE, temas.CORPO_MAX_LINHAS) in vistos


# -- a legenda -----------------------------------------------------------------

def test_a_legenda_NAO_abre_com_a_sinalizacao_de_afiliado():
    """`creative.AFILIADO` diz "o link direciona para a página do produto na
    loja" — e este álbum não tem link de produto nenhum. Repeti-la aqui seria
    afirmar uma coisa falsa para cumprir um hábito."""
    legenda = cli.legenda_do_tema(_tema("x"))
    assert creative.AFILIADO not in legenda
    assert legenda.startswith("TÍTULO")


def test_a_legenda_repete_as_teses_para_o_google():
    tema = temas.Tema(slug="x", titulo="T", subtitulo="S",
                      slides=(temas.Slide("Primeira tese", "O corpo dela."),
                              temas.Slide("Segunda", "Outro corpo."),
                              temas.Slide("Terceira", "Mais um.")))
    legenda = cli.legenda_do_tema(tema)
    assert "1. Primeira tese — O corpo dela." in legenda
    assert legenda.rstrip().endswith(creative.ASSINATURA) or \
        creative.ASSINATURA in legenda


def test_a_legenda_nao_etiqueta_categoria_nenhuma():
    """O álbum não fala de categoria, e etiquetar "Beleza" num post de método
    seria endereçá-lo para quem não o procurou."""
    hashtags = {"marca": ["#fiscaldapromo"], "categorias": {"100630": ["#beleza"]}}
    legenda = cli.legenda_do_tema(_tema("x"), hashtags)
    assert "#beleza" not in legenda


# -- o conteúdo REAL, que é o que vai ao ar -----------------------------------

def test_o_arquivo_de_temas_do_repositorio_carrega():
    acervo = temas.carrega()
    assert len(acervo) >= 2
    assert len({t.slug for t in acervo}) == len(acervo)


def test_nenhum_tema_nomeia_loja_ou_marca_de_produto():
    """A invariante de `2026-08-29-feed-sem-contradicao.md`: acuse a PRÁTICA,
    nunca o produto ou a loja. É ela que permite falar do "de" inflado sem
    virar acusação individual — e um tema que nomeasse um vendedor traria de
    volta exatamente a exposição que tirou o flagrante do feed."""
    proibidas = ("shopee", "mercado livre", "mercadolivre", "amazon",
                 "magalu", "americanas", "aliexpress", "shein")
    for tema in temas.carrega():
        texto = " ".join([tema.titulo, tema.subtitulo,
                          *(s.titulo for s in tema.slides),
                          *(s.corpo for s in tema.slides)]).lower()
        for nome in proibidas:
            assert nome not in texto, f"{tema.slug} nomeia {nome}"


def test_nenhum_tema_pede_curtida_comentario_ou_compartilhamento():
    """A Meta REDUZ a distribuição de quem pede (regra oficial de engagement
    bait), e uma conta sem base de seguidores não tem colchão para pagar isso.
    O fechamento é a frase-assinatura, que o fecho do álbum já traz."""
    pedidos = ("curta", "curtir", "comenta", "comente", "compartilh",
               "marque um amigo", "salve este", "siga para")
    for tema in temas.carrega():
        texto = " ".join([tema.subtitulo, *(s.corpo for s in tema.slides)]).lower()
        for pedido in pedidos:
            assert pedido not in texto, f"{tema.slug} pede: {pedido}"


def test_todo_tema_do_repositorio_cabe_no_desenho():
    """O teste que impede o slide truncado: cada título e cada corpo do arquivo
    real são quebrados com a MESMA fonte e a MESMA largura do desenho, e não
    podem estourar o número de linhas."""
    from PIL import Image, ImageDraw

    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    largura = creative.CARROSSEL_SIZE[0] - 2 * creative.FEED_PAD
    for tema in temas.carrega():
        for slide in tema.slides:
            t = creative._texto_dims(draw, slide.titulo, creative.TEMA_TITULO_SIZE,
                                     largura, temas.TITULO_MAX_LINHAS + 1, 800, 1.08)
            c = creative._texto_dims(draw, slide.corpo, creative.TEMA_CORPO_SIZE,
                                     largura, temas.CORPO_MAX_LINHAS + 1, 500, 1.38)
            assert len(t["lines"]) <= temas.TITULO_MAX_LINHAS, \
                f"{tema.slug}: título '{slide.titulo}' passa de {temas.TITULO_MAX_LINHAS} linhas"
            assert len(c["lines"]) <= temas.CORPO_MAX_LINHAS, \
                f"{tema.slug}: corpo de '{slide.titulo}' passa de {temas.CORPO_MAX_LINHAS} linhas"


def test_a_capa_de_todo_tema_cabe_no_desenho():
    from PIL import Image, ImageDraw

    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    largura = creative.CARROSSEL_SIZE[0] - 2 * creative.FEED_PAD
    for tema in temas.carrega():
        t = creative._texto_dims(draw, tema.titulo, creative.CAPA_TITLE_SIZE,
                                 largura, 4, 800, 1.06)
        s = creative._texto_dims(draw, tema.subtitulo, creative.CAPA_SUB_SIZE,
                                 largura, 3, 500, 1.2)
        assert len(t["lines"]) <= 3, f"{tema.slug}: título da capa passa de 3 linhas"
        assert len(s["lines"]) <= 2, f"{tema.slug}: subtítulo da capa passa de 2 linhas"


def test_o_yaml_e_uma_lista_de_mapas_com_as_chaves_certas():
    """Lido cru, sem passar pelo carregador: é o arquivo que uma pessoa edita,
    e o teste tem de reclamar da FORMA dele, não do objeto que ele virou."""
    with open(temas.CAMINHO, encoding="utf-8") as f:
        bruto = yaml.safe_load(f)
    assert isinstance(bruto, list)
    for item in bruto:
        assert set(item) == {"slug", "capa", "slides"}
        assert set(item["capa"]) == {"titulo", "subtitulo"}
        for slide in item["slides"]:
            assert set(slide) == {"titulo", "corpo"}

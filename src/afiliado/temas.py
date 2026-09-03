"""Fase 5W — o carrossel TEMÁTICO: a única peça 100% nossa do feed.

**Por que ele existe, e o motivo não é alcance.** Desde 30/04/2026 o Instagram
deixou de recomendar a não-seguidores contas que publicam majoritariamente
conteúdo que não criaram nem editaram de forma material. Tudo o que a conta
publica hoje é foto do vendedor com a nossa moldura em volta — a moldura é
gráfico próprio e provavelmente basta, mas é a parte FRACA do argumento e é
100% do feed. Um carrossel temático é integralmente nosso: texto nosso, tese
nossa, zero repostagem.

E, pelo lado do que funciona: o carrossel é o formato mais SALVO (9× a imagem
única, Metricool sobre 24,3 M de posts), e 36% dos brasileiros salvam post para
comprar depois. O que se salva é utilidade transferível — a peça que serve de
novo semana que vem. Um post de produto expira com a oferta; um post de método
não expira. (docs/superpowers/reviews/2026-08-31-reels-e-carrossel.md, §8–§10.)

**A invariante.** Acuse a PRÁTICA, nunca o produto ou a loja. Nenhum tema
nomeia vendedor, marca ou anúncio — é o que permite falar do "de" inflado sem
virar acusação individual, e é a mesma regra de
`2026-08-29-feed-sem-contradicao.md`.

**A rotação é por tema, não por dia.** O que decide qual sai é quem está há
mais tempo sem sair — assim o acervo gira sozinho e nenhum tema repete antes de
todos terem ido ao ar. O tema publicado fica gravado no cursor do `state.db`.

**O que ainda NÃO está aqui:** o tema do agregado semanal ("o que eu reprovei
esta semana"). Ele depende de um veredito que hoje não existe — sem régua,
100% das ofertas caem em modo B, e dizer "14 não passaram" quando nenhuma podia
passar seria a peça mentindo com número verdadeiro. Ele entra quando o painel
de observação (`afiliado.painel`) fechar os 14 dias.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import yaml

from afiliado.errors import SourceError
from afiliado.state import StateDB

__all__ = ["Tema", "Slide", "CAMINHO", "MIN_SLIDES", "MAX_SLIDES",
           "TITULO_MAX_LINHAS", "CORPO_MAX_LINHAS", "DIAS_DE_DESCANSO",
           "carrega", "escolhe", "cobertura", "marca_publicado", "chave_do_cursor"]

CAMINHO = "data/temas.yaml"

# Capa e fecho ocupam dois dos `CARROSSEL_MAX_SLIDES` (8): sobram seis.
MIN_SLIDES = 3
MAX_SLIDES = 6

# O que cabe no desenho. O renderizador CORTA o que passar disso — em silêncio,
# porque `_wrap_title` corta —, então quem reprova é o carregamento, aqui,
# antes de qualquer peça ser montada.
TITULO_MAX_LINHAS = 3
CORPO_MAX_LINHAS = 7


@dataclass(frozen=True)
class Slide:
    titulo: str
    corpo: str


@dataclass(frozen=True)
class Tema:
    slug: str
    titulo: str
    subtitulo: str
    slides: tuple[Slide, ...]


def chave_do_cursor(slug: str) -> str:
    return f"tema_publicado:{slug}"


def _texto(bruto, campo: str, onde: str) -> str:
    valor = (bruto or "").strip() if isinstance(bruto, str) else ""
    if not valor:
        raise SourceError(f"{onde}: campo '{campo}' vazio ou ausente")
    return valor


def carrega(caminho: str | Path = CAMINHO) -> list[Tema]:
    """Os temas do arquivo, validados.

    Levanta `SourceError` com o motivo em vez de devolver uma lista pela
    metade: um tema malformado é erro de REDAÇÃO, e a peça que ele geraria
    sairia com um slide em branco — que é pior do que não sair.
    """
    caminho = Path(caminho)
    if not caminho.is_file():
        raise SourceError(f"arquivo de temas não encontrado: {caminho}")
    bruto = yaml.safe_load(caminho.read_text(encoding="utf-8"))
    if not isinstance(bruto, list) or not bruto:
        raise SourceError(f"{caminho}: esperava uma lista de temas")
    temas: list[Tema] = []
    vistos: set[str] = set()
    for i, item in enumerate(bruto):
        onde = f"{caminho}[{i}]"
        if not isinstance(item, dict):
            raise SourceError(f"{onde}: esperava um mapa com slug/capa/slides")
        slug = _texto(item.get("slug"), "slug", onde)
        if slug in vistos:
            # Slug repetido quebraria a rotação em silêncio: os dois temas
            # dividiriam a mesma data e um deles nunca sairia.
            raise SourceError(f"{onde}: slug repetido ({slug})")
        vistos.add(slug)
        capa = item.get("capa") or {}
        slides_brutos = item.get("slides") or []
        if not MIN_SLIDES <= len(slides_brutos) <= MAX_SLIDES:
            raise SourceError(f"{onde} ({slug}): {len(slides_brutos)} slide(s); "
                              f"o carrossel aceita de {MIN_SLIDES} a {MAX_SLIDES}")
        slides = tuple(
            Slide(titulo=_texto(s.get("titulo") if isinstance(s, dict) else None,
                                "slides[].titulo", f"{onde} ({slug})"),
                  corpo=_texto(s.get("corpo") if isinstance(s, dict) else None,
                               "slides[].corpo", f"{onde} ({slug})"))
            for s in slides_brutos)
        temas.append(Tema(slug=slug,
                          titulo=_texto(capa.get("titulo"), "capa.titulo",
                                        f"{onde} ({slug})"),
                          subtitulo=_texto(capa.get("subtitulo"), "capa.subtitulo",
                                           f"{onde} ({slug})"),
                          slides=slides))
    return temas


# Quantos dias um tema DESCANSA depois de sair.
#
# O dono, em 2026-09-02: "o post de verificação de desconto já saturou". Com o
# acervo em 2 temas e uma vaga por dia, cada um voltaria a cada 2 dias — e um
# post de método relido a cada 48 h não é conteúdo perene, é insistência.
#
# Duas semanas é o intervalo em que a mesma pessoa provavelmente não lembra de
# ter visto. A consequência é deliberada: com 2 temas o carrossel sai 2 dias em
# 14 e fica calado nos outros 12. **Silêncio é melhor do que repetição** — e o
# número de dias calados é exatamente a pressão para o acervo crescer, que é a
# única solução de verdade. O `doctor` diz quantos dias o acervo cobre.
DIAS_DE_DESCANSO = 14


def escolhe(temas: list[Tema], db: StateDB,
            hoje: date | None = None) -> Tema | None:
    """O tema que está há mais tempo sem sair — nunca publicado vem primeiro —,
    ou `None` quando todos ainda estão descansando.

    Não há sorteio: com um acervo pequeno o sorteio repete, e repetir um tema
    antes de o acervo inteiro ter ido ao ar é desperdiçar peça escrita à mão.
    O desempate é pelo slug, para a escolha ser reprodutível no teste.
    """
    if not temas:
        return None
    hoje = hoje or db.local_today()
    limite = (hoje - timedelta(days=DIAS_DE_DESCANSO)).isoformat()
    def quando(tema: Tema) -> tuple[str, str]:
        # "" (nunca publicado) ordena antes de qualquer data ISO.
        return (db.get_cursor(chave_do_cursor(tema.slug), ""), tema.slug)
    descansados = [t for t in temas if quando(t)[0] <= limite]
    return min(descansados, key=quando) if descansados else None


def cobertura(temas: list[Tema]) -> int:
    """Quantos dias de 14 o acervo consegue cobrir. É a medida honesta de "o
    acervo é grande o bastante": com `DIAS_DE_DESCANSO` temas, o carrossel sai
    todo dia; com menos, ele fica calado na diferença."""
    return min(len(temas), DIAS_DE_DESCANSO)


def marca_publicado(db: StateDB, tema: Tema, dia: date | None = None) -> None:
    db.set_cursor(chave_do_cursor(tema.slug),
                  (dia or db.local_today()).isoformat())

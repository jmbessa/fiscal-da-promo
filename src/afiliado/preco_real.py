"""Fase 5P — o preço que o seguidor vê, lido do navegador.

**O problema.** A Shopee cobra dois preços: o de catálogo (o que a API de
afiliados expõe, e o que publicamos) e o de CHECKOUT, menor, que exige cupom
(às vezes cupom mais Pix). A página põe o segundo em vermelho grande e o
primeiro em cinza pequeno, com a frase que os separa:

    R$523,48
    ou R$599,00 sem cupom em outros métodos de pagamento
    12x R$55,21

Nosso número é o de quem NÃO usa cupom — correto, e ~12% acima do que o dono vê
no app. Ele reclamou duas vezes. As seis rotas de SERVIDOR estão fechadas (ver
`docs/runbooks/shopee-preco.md`); a sétima é o navegador, que executa o
JavaScript e renderiza esse bloco.

**Este módulo é a sétima rota, e ela nasce DESLIGADA** (`preco_real.enabled` em
config.yaml). Ela mexe na superfície de maior risco do projeto — o número que o
post afirma —, então ela é feita inteira de recusas:

1. **Falha FECHADA.** Padrão não casou, tempo esgotou, interstício, qualquer
   dúvida: a leitura não acontece e o post publica o preço da API, como hoje.
   `parse_preco` nunca "quase" lê: ou devolve o número COM a frase que o
   qualifica, ou devolve o motivo pelo qual não devolveu número nenhum.
2. **A frase é a âncora.** O número que a frase traz tem de ser exatamente o
   que o `refresh_price` mediu segundos antes. Se não for, estamos lendo outro
   anúncio (ou outra variação, ou um preço que mudou no meio) e a leitura não
   vale.
3. **Desarme por falhas seguidas**, persistente em `StateDB.day_flags`, no
   molde do `instagram_story_link`: N leituras falhando seguidas fecham a
   leitura pelo resto do DIA, com aviso ao chat de operações. Um processo que
   morre não zera o contador.
4. **Perfil de navegador SEPARADO.** O Chrome da máquina está logado na Shopee
   como o dono, e sessenta páginas automatizadas por dia de um perfil logado é
   o padrão que a Shopee caça — perder a conta encerra o lado Shopee, que hoje
   é 100% do que publicamos. `perfil_proibido` recusa, em código, qualquer
   caminho de perfil que caia dentro do Chrome real da máquina.

**O que a medição de 2026-08-28 encontrou, e que o brief não previa.** Num
Chrome de perfil próprio e DESLOGADO (headless e com janela, com aquecimento
pela home, nas duas formas de URL e com user agent de desktop e de celular), a
rota do produto NÃO entrega preço: ela responde o interstício "Login
Necessário" e, depois de algumas requisições, até a home cai em
`/verify/traffic/error`. A leitura que o runbook registrou foi feita no Chrome
LOGADO do dono — a única sessão em que a página abre. Ou seja: hoje, com este
módulo ligado, toda leitura falha FECHADA (o desarme dispara no terceiro item e
o dia segue publicando o preço da API, como sempre). O detalhe está no relatório
da fase e no runbook; o que está aqui é a máquina, pronta e segura, para o dia
em que houver uma sessão que possa ser usada sem arriscar a conta do dono.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

__all__ = ["Leitura", "parse_preco", "COM_CUPOM", "PIX_COM_CUPOM",
           "SEM_FRASE", "INTERSTICIO", "OUTRO_PRECO", "SEM_NUMERO",
           "NAO_E_DESCONTO", "DESCONTO_IMPLAUSIVEL", "SEM_ANCORA",
           "FRACAO_MINIMA", "MARCAS_DE_INTERSTICIO",
           "PERFIL_PADRAO", "perfil_proibido", "PerfilDoDono"]


# -- a condição, que é o que o rótulo publica ---------------------------------
#
# O rótulo da fase 5N ("sem cupom") era a versão NEGATIVA deste mesmo fato e o
# dono o recusou com razão: "produto classificado como 'sem cupom' não atrai em
# nada". Este é o contrário — só aparece quando temos o número REAL, e diz o que
# ele exige. Mesma honestidade, força oposta.
COM_CUPOM = "com cupom"
PIX_COM_CUPOM = "no Pix com cupom"

# Por que o padrão é "com cupom" e não "no Pix com cupom": o runbook renderiza a
# tela como "R$ 611,80 no Pix com cupom", mas isso é a leitura VISUAL de duas
# linhas. O innerText medido não traz a palavra Pix — traz "ou R$X sem cupom em
# outros métodos de pagamento". A condição que o texto PROVA é "com cupom"; o
# "no Pix" só entra quando a própria página o escreve entre o número e a frase.

# -- as recusas ----------------------------------------------------------------
#
# Cada uma com nome próprio, como os quatro SEM_LINK do `instagram_story_link`:
# "não achei a frase" e "a página serviu o interstício" mandam o dono para
# lugares diferentes, e uma mensagem só faria a primeira mudança de redação da
# Shopee ser confundida com um bloqueio de tráfego.
SEM_FRASE = "a página não trouxe a frase 'ou R$ … sem cupom'"
INTERSTICIO = ("a página respondeu o interstício de login/verificação de tráfego, "
               "não o anúncio")
OUTRO_PRECO = "o preço sem cupom da página não é o que a API acabou de medir"
SEM_NUMERO = "achei a frase, mas nenhum preço em destaque antes dela"
NAO_E_DESCONTO = "o preço em destaque não é menor que o preço sem cupom"
DESCONTO_IMPLAUSIVEL = ("o preço em destaque está longe demais do sem cupom — "
                        "provavelmente é uma parcela, não o preço")
SEM_ANCORA = "sem o preço da API não há como verificar o que a página diz"

# O que a página serve quando ela NÃO é o anúncio. Medido em 2026-08-28 com
# perfil próprio e deslogado: a rota do produto devolve "Login Necessário" e,
# depois de algumas requisições, a própria home passa a cair em
# `/verify/traffic/error`. São marcas de CASCA — nada dentro delas é preço.
MARCAS_DE_INTERSTICIO = (
    "login necessário", "login necessario",
    "página indisponível", "pagina indisponivel",
    "verify/traffic", "faça login para continuar", "faca login para continuar",
    "verifique que você é humano", "não sou um robô", "nao sou um robo",
)

# O quanto o preço de checkout pode ficar abaixo do de catálogo antes de a
# leitura virar suspeita. Não é um juízo sobre promoção: é a rede contra ler uma
# PARCELA ("12x R$55,21") como se fosse o preço — 55,21 de 599,00 é 9% do valor,
# e sairia como "91% mais barato". Os dois casos reais medidos ficaram em ~88%
# do preço sem cupom, muito acima deste piso.
FRACAO_MINIMA = 0.40

# Onde o navegador desta rota guarda o perfil dele. NÃO é `data/` (aquela pasta
# é versionada e a produção escreve nela a cada 15 min) e NÃO é o Chrome da
# máquina — ver `perfil_proibido`.
PERFIL_PADRAO = ".cache/chrome-preco-real"

_MOEDA = r"R\$\s*(\d{1,3}(?:\.\d{3})*,\d{2})"
_PRECO = re.compile(_MOEDA)
# A frase que qualifica o número em destaque. É ela que transforma "R$ 523,48"
# em "R$ 523,48 com cupom" — sem ela, não há leitura.
_FRASE = re.compile(r"ou\s+" + _MOEDA + r"\s+sem\s+cupom", re.IGNORECASE)
_PIX = re.compile(r"\bpix\b", re.IGNORECASE)


class Leitura(NamedTuple):
    """O que a página DE FATO disse — ou por que ela não disse nada.

    `price_cents > 0` (`ok`) é o único estado em que alguma coisa muda no post.
    Todo o resto é uma recusa com nome, e a recusa é o comportamento de hoje:
    publicar o preço da API.
    """

    price_cents: int = 0
    condicao: str = ""
    sem_cupom_cents: int = 0
    motivo: str = ""

    @property
    def ok(self) -> bool:
        return self.price_cents > 0


def _cents(texto: str) -> int:
    """"1.234,56" -> 123456. O separador de milhar sai; a vírgula vira ponto."""
    return int(round(float(texto.replace(".", "").replace(",", ".")) * 100))


def tem_intersticio(texto: str) -> bool:
    """A página é a casca de login/verificação em vez do anúncio?

    Vale a pena ser chamado ANTES de esperar pelo preço: esperar 30 s por um
    bloco que nunca vem é o custo mais caro desta rota."""
    baixo = (texto or "").lower()
    return any(marca in baixo for marca in MARCAS_DE_INTERSTICIO)


def parse_preco(texto: str, preco_api_cents: int) -> Leitura:
    """O bloco de preço da página em `Leitura` — ou a recusa, com o motivo.

    A ordem das checagens é deliberada: o interstício vem antes de tudo (o que
    parece preço dentro dele veio da casca), a âncora vem antes do número (sem
    o preço da API não há verificação possível) e a plausibilidade vem por
    último, quando já existem os dois números para comparar.
    """
    if tem_intersticio(texto):
        return Leitura(motivo=INTERSTICIO)
    if preco_api_cents <= 0:
        return Leitura(motivo=SEM_ANCORA)
    frase = _FRASE.search(texto or "")
    if frase is None:
        return Leitura(motivo=SEM_FRASE)
    sem_cupom = _cents(frase.group(1))
    if sem_cupom != int(preco_api_cents):
        return Leitura(sem_cupom_cents=sem_cupom, motivo=OUTRO_PRECO)
    # O número em destaque é o ÚLTIMO preço que aparece antes da frase: a frase
    # é a legenda dele, e o que vier depois (parcela, frete, outro bloco) não é
    # o que ela qualifica.
    antes = [m for m in _PRECO.finditer(texto) if m.end() <= frase.start()]
    if not antes:
        return Leitura(sem_cupom_cents=sem_cupom, motivo=SEM_NUMERO)
    destaque = antes[-1]
    valor = _cents(destaque.group(1))
    if valor >= sem_cupom:
        return Leitura(sem_cupom_cents=sem_cupom, motivo=NAO_E_DESCONTO)
    if valor < sem_cupom * FRACAO_MINIMA:
        return Leitura(sem_cupom_cents=sem_cupom, motivo=DESCONTO_IMPLAUSIVEL)
    # "no Pix" só quando a PÁGINA o escreve entre o número e o fim da frase —
    # é ali que a condição do preço mora. Um "Pix" na lista de meios de
    # pagamento do rodapé não qualifica preço nenhum.
    entre = texto[destaque.start():frase.end()]
    condicao = PIX_COM_CUPOM if _PIX.search(entre) else COM_CUPOM
    return Leitura(price_cents=valor, condicao=condicao, sem_cupom_cents=sem_cupom)


# -- a guarda do perfil --------------------------------------------------------

class PerfilDoDono(RuntimeError):
    """O caminho de perfil aponta para o Chrome real da máquina — o que está
    logado na Shopee como o dono. Não é preferência: é o requisito que impede
    esta fase de custar a conta que sustenta o projeto."""


# Onde o Chrome de verdade guarda os perfis, no Windows e nos dois Unixes. A
# comparação é por CAMINHO, não por nome: `--user-data-dir` aceita qualquer
# coisa, e o erro que mata a conta é apontá-lo para a pasta que já tem a sessão.
_PASTAS_DO_CHROME_REAL = (
    Path("AppData/Local/Google/Chrome/User Data"),
    Path("AppData/Local/Google/Chrome Beta/User Data"),
    Path("AppData/Local/Chromium/User Data"),
    Path("AppData/Local/Microsoft/Edge/User Data"),
    Path(".config/google-chrome"),
    Path(".config/chromium"),
    Path("Library/Application Support/Google/Chrome"),
)


def perfil_proibido(caminho: str | Path, home: Path | None = None) -> str:
    """O motivo pelo qual este perfil não pode ser usado — ou "" se ele pode.

    Proibido é o perfil do NAVEGADOR REAL da máquina (qualquer pasta dentro do
    "User Data" do Chrome/Chromium/Edge do usuário). Um perfil vazio, criado por
    nós, é o que esta rota usa: ele não tem a sessão do dono e não pode ganhar
    uma sem alguém digitar a senha.
    """
    home = home or Path.home()
    try:
        alvo = Path(caminho).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return ""
    for relativa in _PASTAS_DO_CHROME_REAL:
        real = (home / relativa).resolve()
        if alvo == real or real in alvo.parents:
            return (f"perfil {alvo} está dentro do navegador real da máquina "
                    f"({real}) — ele é o que está logado na Shopee como o dono, e "
                    "60 páginas automatizadas por dia a partir dele é o padrão que "
                    "a Shopee caça. Use um perfil próprio (preco_real.profile_dir)")
    return ""

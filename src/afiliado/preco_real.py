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

import dataclasses
import re
import time
from pathlib import Path
from typing import Callable, NamedTuple

from afiliado.models import Offer

__all__ = ["Leitura", "parse_preco", "COM_CUPOM", "PIX_COM_CUPOM",
           "SEM_FRASE", "INTERSTICIO", "OUTRO_PRECO", "SEM_NUMERO",
           "NAO_E_DESCONTO", "DESCONTO_IMPLAUSIVEL", "SEM_ANCORA",
           "FORA_DA_SHOPEE", "SEM_URL", "DESARMADO", "FALHA_DO_NAVEGADOR",
           "FRACAO_MINIMA", "MARCAS_DE_INTERSTICIO", "espera_o_bloco",
           "PERFIL_PADRAO", "perfil_proibido", "PerfilDoDono",
           "LeitorDePreco", "monta", "config_de",
           "CHAVE_DESARMADO", "CHAVE_FALHAS", "MAX_FALHAS_PADRAO",
           "TIMEOUT_PADRAO", "PASSO_PADRAO"]


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
# Recusas que acontecem ANTES de abrir página nenhuma — elas não gastaram nada e
# não contam para o desarme.
FORA_DA_SHOPEE = ("só a Shopee cobra um preço de checkout diferente do de "
                  "catálogo — o do ML é o do anúncio que o nosso link abre")
SEM_URL = "a oferta não tem a URL do anúncio"
DESARMADO = "a leitura está desarmada hoje"
FALHA_DO_NAVEGADOR = "o navegador não entregou a página"

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

# O laço de espera. O bloco de preço chega VAZIO e leva de 10 a 30 s para
# preencher (medido em 2026-08-28: um caso passou de 30 s, outro veio na
# primeira tentativa). Um `get` só não serve, e um laço sem teto trava o run.
TIMEOUT_PADRAO = 30
PASSO_PADRAO = 1.0

# Leituras seguidas sem preço antes de a leitura se desarmar pelo resto do DIA.
# 3, e não 2 como o `instagram_story_link`: aqui uma falha isolada não custa
# nada ao público (o post publica o preço da API, como sempre), então vale a
# pena tolerar mais azar antes de calar o dia inteiro.
MAX_FALHAS_PADRAO = 3

# As marcas do dia local, no `StateDB.day_flags` — mesma tabela e mesma
# semântica do desarme do `instagram_story_link` (fase 5F, C2). A produção roda
# de 15 em 15 min: sem isto o desarme morreria com o processo e o run seguinte
# recomeçaria a martelar a Shopee.
CHAVE_DESARMADO = "preco_real_desarmado"
CHAVE_FALHAS = "preco_real_falhas_seguidas"

AVISO_DESARMADO = ("⚠️ preco_real: {n} leituras seguidas sem preço — leitura "
                   "DESARMADA pelo resto do dia (o post continua publicando o preço "
                   "da API, como sempre). Último motivo: {motivo}")
AVISO_INTERSTICIO = ("⚠️ preco_real: {motivo} — leitura DESARMADA pelo resto do dia na "
                     "primeira ocorrência. Insistir contra um bloqueio é o que custa a "
                     "conta; ver docs/runbooks/shopee-preco.md")
AVISO_SEM_PLAYWRIGHT = ("⚠️ preco_real: playwright não instalado — leitura desarmada, "
                        "rode `pip install -e .[preco]` e `playwright install chromium` "
                        "(ver docs/runbooks/shopee-preco.md)")
AVISO_PERFIL = "⚠️ preco_real NÃO ligado: {motivo}"

SEM_PLAYWRIGHT = ("playwright não instalado — `pip install -e .[preco]` "
                  "(ver docs/runbooks/shopee-preco.md)")

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


# -- o laço de espera ----------------------------------------------------------

def espera_o_bloco(ler: Callable[[], str], pronto: Callable[[str], bool],
                   teto_s: float, passo_s: float,
                   sleep: Callable[[float], None] = time.sleep,
                   relogio: Callable[[], float] = time.monotonic) -> tuple[str, float]:
    """Relê a página até o bloco ficar pronto, com TETO. `(último texto, segundos)`.

    Três saídas, e as três importam:
      - `pronto(texto)` -> devolve na hora (a leitura chegou);
      - `tem_intersticio(texto)` -> devolve na hora TAMBÉM, sem gastar o teto: a
        página já disse que não vai servir o anúncio, e esperar 30 s por um
        bloco que nunca vem é o custo mais caro desta rota;
      - o teto -> devolve o que viu por último, e quem chamou transforma isso
        numa recusa.

    Fica separado do navegador para ser testável sem abrir nada — e é o único
    lugar do módulo que fala de tempo.
    """
    inicio = relogio()
    texto = ""
    while True:
        texto = ler()
        decorrido = relogio() - inicio
        if pronto(texto) or tem_intersticio(texto) or decorrido >= teto_s:
            return texto, decorrido
        sleep(passo_s)


# -- o navegador de verdade ----------------------------------------------------

def navegador_playwright(profile_dir: str | Path, channel: str = "chrome",
                         headless: bool = True, teto_s: float = TIMEOUT_PADRAO,
                         passo_s: float = PASSO_PADRAO):
    """Devolve o `navegador(url, pronto) -> (texto, segundos)` que abre a página
    de verdade — um Chrome com PERFIL PRÓPRIO, criado por nós.

    **Por que Playwright e não CDP contra o Chrome já instalado** (a decisão da
    P3, registrada no relatório da fase): com CDP alguém precisaria subir o
    Chrome com `--remote-debugging-port` e `--user-data-dir`, e o erro de
    apontar esse segundo argumento para o perfil do dono — ou de anexar à
    instância que já está aberta — é justamente o que encerra o lado Shopee.
    `launch_persistent_context` recebe o diretório como argumento obrigatório e
    o perfil passa por `perfil_proibido` antes; a guarda vira código, não
    disciplina. `channel="chrome"` usa o BINÁRIO instalado (menos detectável que
    o Chromium empacotado) com um perfil vazio.

    O import é PREGUIÇOSO: o extra `preco` é opcional e quem não o instalou tem
    o pipeline inteiro funcionando — exatamente como o instagrapi da 5F. A suíte
    roda numa máquina sem ele.
    """
    from playwright.sync_api import sync_playwright

    caminho = Path(profile_dir)

    def navegador(url: str, pronto: Callable[[str], bool]) -> tuple[str, float]:
        caminho.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                str(caminho), channel=channel or None, headless=headless,
                locale="pt-BR", viewport={"width": 1366, "height": 900},
                args=["--disable-blink-features=AutomationControlled"])
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                page.goto(url, wait_until="domcontentloaded",
                          timeout=int(teto_s * 1000))

                def ler() -> str:
                    try:
                        return page.inner_text("body")
                    except Exception:      # noqa: BLE001 - página trocando: tenta de novo
                        return ""

                return espera_o_bloco(ler, pronto, teto_s, passo_s)
            finally:
                ctx.close()

    return navegador


# -- o leitor: a leitura, o desarme e a recusa ---------------------------------

class LeitorDePreco:
    """A leitura de checkout de UMA oferta, com o desarme por falhas seguidas.

    Contrato, e ele é o valor da fase: `aplica` NUNCA levanta e NUNCA inventa.
    Ou devolve a oferta carimbada com um preço que veio junto da frase que o
    qualifica, ou devolve a oferta EXATAMENTE como entrou — que é o pipeline de
    hoje.

    Só é chamado para a oferta que VAI publicar (≈60/dia), nunca para o estoque:
    o custo é uma página de navegador por leitura, e o estoque tem milhares.
    """

    def __init__(self, navegador: Callable[[str, Callable[[str], bool]],
                                           tuple[str, float]],
                 estado=None, max_falhas: int = MAX_FALHAS_PADRAO,
                 timeout_s: float = TIMEOUT_PADRAO):
        self._navegador = navegador
        # `estado` é o StateDB (ou qualquer coisa com day_flag/set_day_flag): é
        # ele que faz o desarme sobreviver ao processo. Sem ele o leitor
        # funciona igual, com memória de um run só.
        self.estado = estado
        self.max_falhas = max(1, int(max_falhas))
        self.timeout_s = timeout_s
        self.disponivel = True
        self.falhas_seguidas = 0
        self.segundos_da_ultima = 0.0
        self.warnings: list[str] = []
        self._le_estado_do_dia()

    # -- a leitura -------------------------------------------------------------

    def aplica(self, offer: Offer) -> tuple[Offer, Leitura]:
        """`(oferta, leitura)`. A oferta sai carimbada só quando a leitura deu
        certo; em todo o resto ela sai IDÊNTICA à que entrou."""
        leitura = self.le(offer)
        if not leitura.ok:
            return offer, leitura
        return (dataclasses.replace(offer, price_checkout_cents=leitura.price_cents,
                                    price_checkout_label=leitura.condicao),
                leitura)

    def le(self, offer: Offer) -> Leitura:
        """O que a página do anúncio disse — ou por que não disse nada.

        As três primeiras recusas acontecem antes de abrir página nenhuma e por
        isso NÃO contam para o desarme: elas não gastaram chamada e a Shopee não
        recusou nada."""
        if not self.disponivel:
            return Leitura(motivo=DESARMADO)
        if offer.source != "shopee":
            return Leitura(motivo=FORA_DA_SHOPEE)
        if not offer.product_url:
            return Leitura(motivo=SEM_URL)

        preco = int(offer.price_current_cents)

        def pronto(texto: str) -> bool:
            return parse_preco(texto, preco).ok

        try:
            texto, segundos = self._navegador(offer.product_url, pronto)
        except _NaoDaPraLer as exc:
            # Biblioteca ausente, perfil proibido: não adianta tentar de novo, e
            # cada tentativa é uma página a mais contra a Shopee.
            self._desarma(exc.aviso)
            return Leitura(motivo=str(exc))
        except Exception as exc:      # noqa: BLE001 - `aplica` NUNCA levanta
            return self._falhou(Leitura(
                motivo=f"{FALHA_DO_NAVEGADOR} ({type(exc).__name__}: {exc})"))
        self.segundos_da_ultima = segundos

        leitura = parse_preco(texto, preco)
        if leitura.ok:
            self._rearma()
            return leitura
        if leitura.motivo == INTERSTICIO:
            # Não é uma leitura que deu errado: é a Shopee recusando a página.
            # Insistir 60 vezes por dia contra um bloqueio é o que custa a conta.
            self._desarma(AVISO_INTERSTICIO.format(motivo=INTERSTICIO))
            return leitura
        return self._falhou(leitura)

    # -- o desarme -------------------------------------------------------------

    def _falhou(self, leitura: Leitura) -> Leitura:
        self.falhas_seguidas += 1
        self._grava(CHAVE_FALHAS, str(self.falhas_seguidas))
        if self.falhas_seguidas >= self.max_falhas:
            self._desarma(AVISO_DESARMADO.format(n=self.falhas_seguidas,
                                                 motivo=leitura.motivo))
        return leitura

    def _rearma(self) -> None:
        """Uma leitura boa apaga a marca do dia — o desarme é por falhas
        SEGUIDAS, e uma página lenta de manhã não soma com outra da tarde."""
        self.falhas_seguidas = 0
        self._grava(CHAVE_FALHAS, "")
        self._grava(CHAVE_DESARMADO, "")

    def _desarma(self, aviso: str) -> None:
        self.disponivel = False
        self._grava(CHAVE_DESARMADO, aviso)
        self._avisa(aviso)

    def _le_estado_do_dia(self) -> None:
        """O que o processo anterior descobriu. A produção roda de 15 em 15 min:
        sem isto o desarme valeria por um run e o próximo recomeçaria."""
        if self.estado is None:
            return
        try:
            self.falhas_seguidas = int(self.estado.day_flag(CHAVE_FALHAS) or 0)
            aviso = self.estado.day_flag(CHAVE_DESARMADO)
        except Exception:      # noqa: BLE001 - banco ausente nunca derruba a leitura
            return
        if aviso:
            self.disponivel = False
            self._avisa(aviso)

    def _grava(self, chave: str, valor: str) -> None:
        if self.estado is None:
            return
        try:
            self.estado.set_day_flag(chave, valor)
        except Exception:      # noqa: BLE001 - nunca derruba a leitura
            pass

    def _avisa(self, texto: str) -> None:
        if texto not in self.warnings:
            self.warnings.append(texto)


class _NaoDaPraLer(Exception):
    """Ler é impossível AGORA e vai continuar sendo (biblioteca ausente, perfil
    proibido). Carrega o aviso com que a leitura se desarma."""

    def __init__(self, mensagem: str, aviso: str):
        super().__init__(mensagem)
        self.aviso = aviso


# -- a montagem: ela nasce DESLIGADA -------------------------------------------

def config_de(cfg: dict) -> dict:
    """A seção `preco_real:` do config.yaml, normalizada. Ausente = desligada,
    que é o estado de hoje e o estado para o qual tudo cai."""
    raw = cfg.get("preco_real")
    raw = dict(raw) if isinstance(raw, dict) else {"enabled": bool(raw)}
    return {
        "enabled": bool(raw.get("enabled", False)),
        "profile_dir": str(raw.get("profile_dir") or PERFIL_PADRAO),
        "browser_channel": str(raw.get("browser_channel", "chrome") or ""),
        "headless": bool(raw.get("headless", True)),
        "timeout_s": float(setting(raw, "timeout_s", TIMEOUT_PADRAO)),
        "passo_s": float(setting(raw, "passo_s", PASSO_PADRAO)),
        "max_falhas": int(setting(raw, "max_falhas", MAX_FALHAS_PADRAO)),
    }


def setting(section: dict, key: str, default):
    """`section.get(key)` que honra `0` — a mesma armadilha que `pricing.setting`
    fecha no resto do config."""
    valor = section.get(key)
    return default if valor is None else valor


def monta(cfg: dict, estado=None) -> tuple[LeitorDePreco | None, list[str]]:
    """`(leitor, avisos)` — e `leitor` é None sempre que a leitura não deve
    acontecer, que é o padrão.

    A recusa por PERFIL é aqui e não no leitor de propósito: ligar a leitura
    apontando para o Chrome do dono não pode simplesmente funcionar, e o aviso
    tem de chegar ao chat de operações no primeiro run.
    """
    opcoes = config_de(cfg)
    if not opcoes["enabled"]:
        return None, []
    motivo = perfil_proibido(opcoes["profile_dir"])
    if motivo:
        return None, [AVISO_PERFIL.format(motivo=motivo)]
    return LeitorDePreco(
        navegador=_navegador_preguicoso(opcoes), estado=estado,
        max_falhas=opcoes["max_falhas"], timeout_s=opcoes["timeout_s"]), []


def _navegador_preguicoso(opcoes: dict):
    """O navegador só é CONSTRUÍDO na primeira leitura: importar playwright na
    montagem faria a ausência do extra derrubar o `afiliado run` inteiro, em vez
    de desarmar uma leitura."""
    def navegador(url: str, pronto: Callable[[str], bool]) -> tuple[str, float]:
        motivo = perfil_proibido(opcoes["profile_dir"])
        if motivo:
            raise _NaoDaPraLer(motivo, AVISO_PERFIL.format(motivo=motivo))
        try:
            real = navegador_playwright(
                opcoes["profile_dir"], channel=opcoes["browser_channel"],
                headless=opcoes["headless"], teto_s=opcoes["timeout_s"],
                passo_s=opcoes["passo_s"])
        except ImportError as exc:
            raise _NaoDaPraLer(SEM_PLAYWRIGHT, AVISO_SEM_PLAYWRIGHT) from exc
        return real(url, pronto)

    return navegador

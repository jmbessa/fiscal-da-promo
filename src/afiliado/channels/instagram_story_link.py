"""Canal `instagram_story_link` (fase 5F): story COM figurinha de link, pela
API PRIVADA do Instagram (instagrapi).

**Por que ele existe.** A Graph API nega figurinhas, com todas as letras, na
referência do endpoint que o `instagram_story` usa: *"Publishing stickers (i.e.
link, poll, location) is not supported"*. O Meta Business Suite oferece link em
story, mas — verificado pelo dono no compositor real, 2026-08-27 — *"os links
serão mostrados apenas nos stories do Facebook"*. Não existe caminho de
primeira parte. O levantamento inteiro está em
`docs/superpowers/reviews/2026-08-27-sticker-de-link.md`.

**Por que a verificação é o coração deste módulo, e não um extra.** O sticker
de link do instagrapi ficou quebrado **em silêncio** de 2025-11-03 a 2026-04-16
(issue #2320): o story ia ao ar, o upload retornava sucesso, e o link
simplesmente não estava lá. Para um pipeline de afiliados esse é o pior modo de
falha que existe — gasta cota, gasta a atenção do seguidor e não converte nada.
Por isso o canal LÊ O STORY DE VOLTA (`story_info`) e distingue três estados,
sem nunca confundir um com o outro:

    COM_LINK        `story.links` traz o webUri pedido  → publicação de verdade
    SEM_LINK        o story respondeu e o link não está → FALHA (e conta para o desarme)
    NÃO_VERIFICADO  `story_info` não respondeu          → FALHA, dizendo que não sabe

O terceiro estado é a lição da 5E: não relate observação que você não fez. Um
504 do Instagram não é evidência de que o instagrapi quebrou, então ele não
mexe no contador de desarme — só o SEM_LINK mexe. E o SEM_LINK diz QUAL dos
quatro ele é (`links` vazio, campo ausente, formato ilegível, outro endereço):
o contrato `links[*].webUri` veio da doc da biblioteca, não de observação
nossa, e com uma mensagem só a primeira renomeação de campo acusaria "quebrou"
com os stories perfeitos no ar.

**Os três freios, e o que cada um protege.**

1. `e_erro_de_sessao` classifica desafio/sessão morta pelo NOME da exceção nos
   TRÊS lugares que falam com o Instagram — login, upload e leitura de volta.
   Com a sessão carregada o `login()` passa direto e o desafio aparece no
   UPLOAD: era ali que ninguém o reconhecia, e o pipeline tentava de novo.
2. Um story que FOI AO AR conta (`PublishResult.publicado`), mesmo sem
   figurinha: ele ocupa um dos 6 do dia e entra no dedupe. Enquanto só o
   sucesso contava, o canal quebrado publicava 2 por run sem gastar teto
   nenhum.
3. O desarme é GRAVADO no dia local (`StateDB.day_flags`): vale para o
   processo seguinte, não só para o run. Rearma na virada do dia, numa
   verificação boa, ou num `afiliado ig-login` bem-sucedido.

**Onde este canal roda.** Na máquina do dono (`afiliado stories`), com IP
residencial e sessão estável. NUNCA no GitHub Actions: IP de datacenter que
muda a cada execução + sessão de app móvel forjada é o padrão que mais dispara
`challenge_required`. E nunca junto com o `instagram_story` (Graph API) na
mesma conta — o `doctor` reclama se os dois estiverem ligados.

**O preço.** É API privada: exige usuário e SENHA (não é token revogável), e o
próprio mantenedor do instagrapi desaconselha produção. A moeda de risco é a
conta. Por isso a senha não sai daqui: nenhuma mensagem, aviso ou exceção deste
módulo a carrega — `_sem_senha` raspa até o texto de exceção de terceiro.
"""

import tempfile
import time
from pathlib import Path
from typing import Callable, NamedTuple

import httpx

from afiliado import creative
from afiliado.channels.base import PublishResult
from afiliado.channels.instagram_common import to_jpeg
from afiliado.errors import SourceError
from afiliado.models import Post

# Sessão persistida: cookies + perfil de device. Reusá-la é o que evita "device
# novo a cada login", que é o que dispara desafio. Está no .gitignore.
DEFAULT_SESSION_PATH = "data/ig_session.json"

# Falhas de verificação SEGUIDAS antes de o canal se desarmar. 2 porque uma
# pode ser azar; duas seguidas são a assinatura de "o instagrapi quebrou de
# novo" — e a fase 5F existe porque isso já durou cinco meses uma vez.
MAX_SEM_LINK_PADRAO = 2

# Exceções do instagrapi que significam "esta sessão morreu". Por NOME: a lista
# muda entre versões (`AccountSuspended` nasceu em jul/2026) e um import direto
# quebraria com a versão errada.
#
# `ChallengeError` é o PAI de toda a família de desafio no instagrapi
# (`ChallengeRedirection`, `ChallengeSelfieCaptcha`, `ChallengeUnknownStep`,
# `RecaptchaChallengeForm`, ...): classificar por ele — e subindo a hierarquia,
# ver `e_erro_de_sessao` — pega a família inteira sem listar nome por nome.
ERROS_DE_SESSAO = ("LoginRequired", "ChallengeError", "ChallengeRequired",
                   "PleaseWaitFewMinutes", "AccountSuspended", "BadPassword",
                   "TwoFactorRequired", "ReloginAttemptExceeded")

# Os três estados da verificação. São excludentes de propósito: o dia em que
# "não consegui ler" virar "não tem link" é o dia em que o canal começa a
# mentir no resumo de operações.
COM_LINK = "com_link"
SEM_LINK = "sem_link"
NAO_VERIFICADO = "nao_verificado"

# Chaves do desarme PERSISTENTE (fase 5F, rodada de correção — C2). Vivem no
# `StateDB`, na tabela `day_flags`: uma linha por chave e DIA LOCAL, podada
# sozinha na virada do dia. Antes o desarme morria com o processo, e o canal
# quebrado recomeçava a publicar a cada run — ~12 stories sem link por dia.
CHAVE_DESARMADO = "story_link_desarmado"
CHAVE_SEM_LINK = "story_link_sem_link_seguidos"

AVISO_DESARMADO = ("⚠️ instagram_story_link: {n} stories sem figurinha — canal "
                   "desarmado, ligue instagram_story (Graph API) como fallback")
AVISO_SESSAO = ("⚠️ instagram_story_link: não consegui entrar no Instagram — canal "
                "desarmado, rode `afiliado ig-login`")
AVISO_SEM_INSTAGRAPI = ("⚠️ instagram_story_link: instagrapi não instalado — canal "
                        "desarmado, rode `pip install -e .[stories]`")
AVISO_SEM_VERIFICACAO = ("⚠️ instagram_story_link: publicando SEM verificação da "
                         "figurinha — ninguém está conferindo se o link foi junto")
AVISO_SEM_PK = ("⚠️ instagram_story_link: o instagrapi não devolveu o pk do story — "
                "ele foi ao ar sem id, e o resumo não sabe dizer qual é")

SEM_INSTAGRAPI = ("instagrapi não instalado — `pip install -e .[stories]` "
                  "(ver docs/runbooks/instagrapi-stories.md)")
SESSAO_INVALIDA = "sessão do Instagram inválida — rode `afiliado ig-login`"

# Os quatro jeitos de NÃO achar a figurinha. Todos são SEM_LINK (a direção
# segura: parar de publicar), mas dizem coisas diferentes ao ops — e o contrato
# `links[*].webUri` veio da doc do instagrapi, não de observação nossa. Com uma
# mensagem só, a primeira renomeação de campo na biblioteca acusaria "quebrou"
# com os stories perfeitos no ar.
LINKS_VAZIO = "o story respondeu com `links` vazio"
SEM_CAMPO_LINKS = "não encontrei o campo `links` na resposta do story_info"
FORMATO_DESCONHECIDO = "os itens de `links` não trazem `webUri` que eu saiba ler"
LINK_OUTRO_ENDERECO = "a figurinha aponta para outro endereço"


class Verificacao(NamedTuple):
    """O que a leitura do story DE FATO observou.

    `estado` é um dos três acima; `detalhe` é o que ajuda a agir (a causa do
    erro, ou "a figurinha aponta para outro endereço"). Nada aqui é inferido:
    tudo veio da resposta — ou da ausência dela.
    """
    estado: str
    detalhe: str = ""


class _NaoDaPraEntrar(Exception):
    """Entrar no Instagram é impossível AGORA (sessão morta, desafio,
    biblioteca ausente). Carrega o aviso com que o canal se desarma: insistir
    em laço é o caminho mais curto para perder a conta."""

    def __init__(self, mensagem: str, aviso: str):
        super().__init__(mensagem)
        self.aviso = aviso


def _instagrapi():
    """Import PREGUIÇOSO do instagrapi: `(Client, StoryLink, erros_de_sessão)`.

    Preguiçoso porque o extra `stories` é opcional — quem não o instalou tem o
    pipeline inteiro funcionando, e a suíte roda numa máquina sem ele. Levanta
    `ImportError` quando falta, e quem chama transforma isso em mensagem
    acionável.
    """
    from instagrapi import Client
    from instagrapi import exceptions as excecoes
    from instagrapi.types import StoryLink

    erros = tuple(e for e in (getattr(excecoes, nome, None) for nome in ERROS_DE_SESSAO)
                  if isinstance(e, type) and issubclass(e, BaseException))
    return Client, StoryLink, erros


def nova_sessao():
    """Um `Client` do instagrapi, ainda sem login. Levanta `ImportError`
    quando o extra `stories` não foi instalado."""
    return _instagrapi()[0]()


def entra(cl, username: str, password: str, session_path: str | Path,
          totp_seed: str = "") -> None:
    """Login com sessão persistida — o caminho ÚNICO de autenticação do
    projeto (canal e `afiliado ig-login` passam por aqui).

    Com a sessão no disco, o instagrapi reaproveita cookies e perfil de device
    e só re-autentica se precisar: é isso que evita "device novo a cada login",
    o padrão que dispara `challenge_required`. 2FA só por TOTP (app
    autenticador) — o instagrapi não faz SMS.

    Levanta o que o instagrapi levantar: quem chama decide o que dizer, e
    ninguém aqui tenta de novo em laço.
    """
    caminho = Path(session_path)
    if caminho.is_file():
        cl.load_settings(caminho)
    if totp_seed:
        cl.login(username, password, verification_code=cl.totp_generate_code(totp_seed))
    else:
        cl.login(username, password)


def guarda_sessao(cl, session_path: str | Path) -> None:
    """`dump_settings` no caminho da sessão, criando a pasta se preciso."""
    caminho = Path(session_path)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    cl.dump_settings(caminho)


def e_erro_de_sessao(exc: BaseException) -> bool:
    """A exceção é uma das que significam "esta sessão morreu / há um desafio"?

    Classifica pelo NOME da classe e de todas as suas ancestrais, sem importar
    instagrapi — e por isso funciona nos TRÊS lugares que falam com o
    Instagram, não só no login. Era essa a lacuna que a revisão mediu: com a
    sessão carregada o `login()` passa direto e o `ChallengeRequired` chega no
    `photo_upload_to_story`, onde ninguém o reconhecia. Resultado: três
    tentativas por run, canal armado, e até 18 chamadas por dia disparando
    desafio contra uma conta já sinalizada.
    """
    return any(cls.__name__ in ERROS_DE_SESSAO for cls in type(exc).__mro__)


def sem_segredos(texto: str, *segredos: str) -> str:
    """Raspa credenciais de qualquer texto antes de ele virar mensagem — vale
    inclusive para o texto de uma exceção de terceiro, que ninguém revisou."""
    for segredo in segredos:
        if segredo and segredo in texto:
            texto = texto.replace(segredo, "***")
    return texto


def story_link(web_uri: str):
    """`StoryLink(webUri=...)` — a figurinha, do jeito que o instagrapi a
    nomeia. Fica numa função para o import continuar preguiçoso e para o teste
    poder injetar um duplo."""
    return _instagrapi()[1](webUri=web_uri)


def mesma_url(a, b) -> bool:
    """O `webUri` volta de um modelo pydantic (`HttpUrl`), que normaliza a URL
    — uma barra final a mais não pode virar "story sem link". Nada de baixar
    caixa: `shope.ee/AbC` e `shope.ee/abc` são links diferentes."""
    return str(a or "").strip().rstrip("/") == str(b or "").strip().rstrip("/")


def _web_uri(link) -> str:
    """O `webUri` de uma figurinha — modelo do instagrapi ou dicionário cru.

    O dicionário existe porque nem toda resposta vira modelo pydantic; lê-lo
    como ausência de link fazia um story SAUDÁVEL contar para o desarme."""
    valor = link.get("webUri") if isinstance(link, dict) else getattr(link, "webUri", "")
    return str(valor or "")


def _pk(media) -> str:
    """O `pk` do story publicado — objeto do instagrapi ou dict, tanto faz.
    Vazio quer dizer "não sei qual story é este", e sem ele não há verificação
    possível."""
    valor = media.get("pk") if isinstance(media, dict) else getattr(media, "pk", None)
    return str(valor) if valor else ""


def _grava_temporario(dados: bytes) -> Path:
    """A arte num arquivo: o instagrapi recebe CAMINHO, não bytes. Quem apaga é
    o `finally` do `publish` — sempre, inclusive quando o upload levanta.

    Falhar AQUI (disco cheio, bytes que não são bytes) também apaga: o arquivo
    já existe no instante em que `NamedTemporaryFile(delete=False)` volta, e o
    `finally` do `publish` ainda não tem o caminho para limpar."""
    arquivo = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    caminho = Path(arquivo.name)
    try:
        with arquivo:
            arquivo.write(dados)
    except BaseException:
        caminho.unlink(missing_ok=True)
        raise
    return caminho


class InstagramStoryLinkChannel:
    name = "instagram_story_link"
    # Um story por run, como o canal oficial: o ritmo diário sai do
    # `max_per_day` do config, distribuído pela janela do `schedule:`.
    max_per_run = 1
    # Pausa entre publicar e ler de volta. Duas chamadas coladas à API privada
    # são rajada de bot; 3 s é barato e não parece robô.
    pausa = 3.0

    def __init__(self, username: str, password: str,
                 session_path: str | Path = DEFAULT_SESSION_PATH,
                 client=None, sleep: Callable[[float], None] = time.sleep,
                 brand_handle: str | None = None, brand_name: str = "Fiscal da Promo",
                 verificar: bool = True, max_sem_link: int = MAX_SEM_LINK_PADRAO,
                 link_factory: Callable[[str], object] | None = None,
                 http_client: httpx.Client | None = None, estado=None):
        self.username = (username or "").strip()
        # A senha só é lida em `cl.login`. Não vai a log, a exceção, a resumo:
        # ver `_sem_senha`, por onde passa TODA mensagem deste canal.
        self.password = password or ""
        self.session_path = Path(session_path)
        # `client` injetado é o duplo do teste (e o cliente já logado, depois do
        # primeiro login); `http_client` é outra coisa — é quem baixa a imagem
        # do produto para a arte.
        self.client = client
        self.sleep = sleep
        self.brand_handle = brand_handle
        self.brand_name = brand_name
        self.verificar = bool(verificar)
        self.max_sem_link = max(1, int(max_sem_link))
        self.link_factory = link_factory or story_link
        self.http_client = http_client
        # `estado` é o StateDB (ou qualquer coisa com `day_flag`/`set_day_flag`):
        # é o que faz o desarme sobreviver ao PROCESSO. Sem ele o canal funciona
        # igual, só que o desarme vale por um run — que foi o defeito C2.
        self.estado = estado
        # Estado do run: o pipeline drena `warnings` depois de cada publish.
        self.disponivel = True
        self.sem_link_seguidos = 0
        self.warnings: list[str] = []
        self._le_estado_do_dia()

    # -- publicação ------------------------------------------------------------

    def publish(self, post: Post) -> PublishResult:
        if not self.disponivel:
            # Desarmado: nem arte, nem rede. O motivo já foi ao resumo.
            return PublishResult(False, error="canal desarmado neste run — ver o aviso acima")

        # A arte é a MESMA do canal oficial, com o MESMO veredito: story pela
        # Graph API e story com figurinha não podem contar histórias diferentes.
        # A conversão e a gravação entram no MESMO try: o temporário nasce
        # antes do `finally` de baixo, e uma falha aqui estouraria para dentro
        # do laço do pipeline — contra o contrato "publish NUNCA levanta".
        try:
            art = creative.render_story(post.offer, post.copy, post.verdict,
                                        client=self.http_client, handle=self.brand_handle,
                                        brand_name=self.brand_name)
            caminho = _grava_temporario(to_jpeg(art))
        except SourceError as exc:
            return PublishResult(False, error=self._sem_senha(
                f"falha ao gerar arte do story: {exc}"))
        except Exception as exc:      # noqa: BLE001 - publish NUNCA levanta
            return PublishResult(False, error=self._sem_senha(
                f"falha ao preparar a arte do story ({type(exc).__name__}: {exc})"))

        try:
            return self._publica(post, caminho)
        finally:
            # Sempre. 6 stories/dia para sempre é lixo suficiente para encher
            # o disco de quem roda isto na própria máquina.
            caminho.unlink(missing_ok=True)

    def _publica(self, post: Post, caminho: Path) -> PublishResult:
        try:
            cl = self._cliente()
        except _NaoDaPraEntrar as exc:
            # Nada de retry: o canal se fecha e diz o que fazer. A biblioteca
            # ausente é a única falha que não prende o canal até amanhã — ela
            # não gasta chamada nenhuma ao Instagram.
            self._desarma(exc.aviso, persiste=exc.aviso != AVISO_SEM_INSTAGRAPI)
            return PublishResult(False, error=self._sem_senha(str(exc)))
        except Exception as exc:      # noqa: BLE001 - publish NUNCA levanta
            self._desarma(AVISO_SESSAO)
            return PublishResult(False, error=self._sem_senha(
                f"falha ao entrar no Instagram ({type(exc).__name__}: {exc}) — "
                "se persistir, rode `afiliado ig-login`"))

        # O link é o de AFILIADO, curto — o mesmo que vai ao Telegram.
        try:
            media = cl.photo_upload_to_story(
                caminho, links=[self.link_factory(post.affiliate_link)])
        except Exception as exc:      # noqa: BLE001 - publish NUNCA levanta
            if e_erro_de_sessao(exc):
                # O caso COMUM (a sessão carregada faz o `login()` passar
                # direto, e o desafio aparece só aqui). Uma tentativa, canal
                # fechado, e a instrução — nunca "tenta de novo".
                self._desarma(AVISO_SESSAO)
                return PublishResult(False, error=self._sem_senha(
                    f"{SESSAO_INVALIDA} ({type(exc).__name__}: {exc})"))
            return PublishResult(False, error=self._sem_senha(
                f"falha ao publicar o story ({type(exc).__name__}: {exc})"))

        # Daqui para baixo o story ESTÁ NA CONTA: todo resultado sai com
        # `publicado=True`, e o pipeline o grava no teto do dia e no dedupe.
        pk = _pk(media)
        if not self.verificar:
            # Existe para depurar. O silêncio é justamente o que custou cinco
            # meses de stories sem link — então ele não é silencioso.
            self._avisa(AVISO_SEM_VERIFICACAO)
            if not pk:
                self._avisa(AVISO_SEM_PK)
            return PublishResult(True, pk, publicado=True)
        if not pk:
            return PublishResult(False, publicado=True, error=(
                "story enviado, mas o instagrapi não devolveu o pk — NÃO foi possível "
                "verificar a figurinha de link"))

        self.sleep(self.pausa)
        return self._resultado(pk, self._verifica(cl, pk, post.affiliate_link))

    # -- verificação (o motivo desta fase) -------------------------------------

    def _verifica(self, cl, pk: str, uri: str) -> Verificacao:
        """Lê o story de volta e diz o que VIU.

        Não confie no retorno do upload: foi exatamente ele que disse "publiquei"
        durante cinco meses enquanto a figurinha não ia junto. E não confunda
        "não consegui ler" com "não tem link" — são estados diferentes, com
        ações diferentes.
        """
        try:
            story = cl.story_info(pk)
        except Exception as exc:      # noqa: BLE001 - qualquer falha é "não sei"
            if e_erro_de_sessao(exc):
                # Ler de volta também é falar com o Instagram: o desafio pode
                # aparecer AQUI. O canal se fecha (mesma classificação do
                # upload e do login) — mas o estado continua NÃO VERIFICADO:
                # não vimos o story, e inventar "sem link" seria mentir.
                self._desarma(AVISO_SESSAO)
                return Verificacao(NAO_VERIFICADO, self._sem_senha(
                    f"{SESSAO_INVALIDA} ({type(exc).__name__}: {exc})"))
            return Verificacao(NAO_VERIFICADO,
                               self._sem_senha(f"{type(exc).__name__}: {exc}"))
        if story is None:
            return Verificacao(NAO_VERIFICADO, "story_info não devolveu o story")
        bruto = getattr(story, "links", None)
        if bruto is None:
            # O campo sumiu (ou nunca se chamou assim). É SEM_LINK — a direção
            # segura —, mas com nome próprio: o dia em que o instagrapi
            # renomear este campo, o ops precisa ler "mudou o formato" e não
            # "o link não foi", que mandaria desligar um canal saudável.
            return Verificacao(SEM_LINK, SEM_CAMPO_LINKS)
        try:
            links = list(bruto)
        except TypeError:
            return Verificacao(SEM_LINK, FORMATO_DESCONHECIDO)
        if not links:
            return Verificacao(SEM_LINK, LINKS_VAZIO)
        uris = [_web_uri(link) for link in links]
        if any(mesma_url(u, uri) for u in uris):
            return Verificacao(COM_LINK)
        if not any(uris):
            return Verificacao(SEM_LINK, FORMATO_DESCONHECIDO)
        # Figurinha apontando para OUTRO endereço é, para o seguidor, a mesma
        # tragédia que figurinha nenhuma.
        return Verificacao(SEM_LINK, LINK_OUTRO_ENDERECO)

    def _resultado(self, pk: str, verificacao: Verificacao) -> PublishResult:
        # Os três estados são falha ou sucesso, mas nos três o story JÁ ESTÁ NA
        # CONTA: `publicado=True` é o que faz o pipeline gravá-lo no teto do dia
        # e no dedupe. Um story sem figurinha não converte — e mesmo assim
        # ocupa um dos 6 do dia, porque o seguidor o viu.
        if verificacao.estado == COM_LINK:
            self._rearma()
            return PublishResult(True, pk, publicado=True)

        if verificacao.estado == NAO_VERIFICADO:
            # O story pode estar perfeito; não sabemos. Isto NÃO conta para o
            # desarme por figurinha: condenar o instagrapi por um 504 do
            # Instagram desligaria o canal por causa da rede. (Um DESAFIO na
            # leitura desarma — mas por sessão, dentro de `_verifica`.)
            return PublishResult(False, pk, publicado=True, error=(
                f"story publicado (pk={pk}), mas NÃO foi possível verificar a figurinha "
                f"de link: {verificacao.detalhe}"))

        # SEM_LINK. O story fica no ar: apagar é destrutivo e o post em si não
        # faz mal — o que ele não faz é converter.
        self._conta_sem_link()
        if self.sem_link_seguidos >= self.max_sem_link:
            self._desarma(AVISO_DESARMADO.format(n=self.sem_link_seguidos))
        detalhe = f" — {verificacao.detalhe};" if verificacao.detalhe else " —"
        return PublishResult(False, pk, publicado=True, error=(
            f"story publicado SEM figurinha de link (pk={pk}){detalhe} "
            "instagrapi provavelmente quebrou"))

    # -- sessão ----------------------------------------------------------------

    def _cliente(self):
        """Cliente logado, preguiçoso e memorizado: um login por processo.

        Com a sessão no disco o instagrapi reaproveita cookies e device e só
        re-autentica se precisar — é o que evita "device novo a cada login".
        """
        if self.client is not None:
            return self.client
        try:
            _, _, erros_de_sessao = _instagrapi()
            cl = nova_sessao()
        except ImportError as exc:
            raise _NaoDaPraEntrar(SEM_INSTAGRAPI, AVISO_SEM_INSTAGRAPI) from exc

        try:
            # Sem `totp_seed` de propósito: 2FA é assunto do `afiliado ig-login`,
            # que o dono roda à mão. Aqui, conta com 2FA e sessão morta vira
            # mensagem acionável — não uma tentativa de adivinhar código.
            entra(cl, self.username, self.password, self.session_path)
        except erros_de_sessao as exc:
            # Desafio, senha trocada, conta suspensa: a sessão morreu e só o
            # dono resolve. Dizer isso é diferente de dizer "deu erro".
            raise _NaoDaPraEntrar(
                f"{SESSAO_INVALIDA} ({type(exc).__name__}: {exc})", AVISO_SESSAO) from exc
        except Exception as exc:      # noqa: BLE001 - vira mensagem acionável
            # Rede, biblioteca, o que for: NÃO afirmamos que a sessão morreu —
            # só que não deu para entrar, e o que fazer se continuar.
            raise _NaoDaPraEntrar(
                f"falha ao entrar no Instagram ({type(exc).__name__}: {exc}) — "
                "se persistir, rode `afiliado ig-login`", AVISO_SESSAO) from exc
        self._guarda_sessao(cl)
        self.client = cl
        return cl

    def _guarda_sessao(self, cl) -> None:
        """`dump_settings` depois de todo login: a sessão que sobrevive é o que
        mantém o device estável. Falhar aqui NÃO cancela a publicação — o login
        já aconteceu; o que se perde é a economia do próximo."""
        try:
            guarda_sessao(cl, self.session_path)
        except Exception as exc:      # noqa: BLE001 - nunca derruba o publish
            self._avisa(f"⚠️ instagram_story_link: não consegui guardar a sessão em "
                        f"{self.session_path} ({type(exc).__name__}) — o próximo run "
                        "vai logar de novo")

    # -- estado do canal -------------------------------------------------------

    def _le_estado_do_dia(self) -> None:
        """Recupera do banco o desarme e o contador do DIA local.

        É o que faz um processo novo saber o que o anterior descobriu. Um canal
        que amanheceu desarmado nasce fechado e repete o aviso — o pipeline o
        recolhe na montagem, e o `warn_once` garante uma mensagem por dia.
        """
        if self.estado is None:
            return
        try:
            self.sem_link_seguidos = int(self.estado.day_flag(CHAVE_SEM_LINK) or 0)
            aviso = self.estado.day_flag(CHAVE_DESARMADO)
        except Exception:      # noqa: BLE001 - banco ausente nunca derruba o canal
            return
        if aviso:
            self.disponivel = False
            self.max_per_run = 0
            self._avisa(aviso)

    def _grava(self, chave: str, valor: str) -> None:
        """Marca o dia no banco. Falhar aqui não pode derrubar a publicação —
        o pior caso é o canal voltar a ter memória de um run só."""
        if self.estado is None:
            return
        try:
            self.estado.set_day_flag(chave, valor)
        except Exception:      # noqa: BLE001 - nunca derruba o publish
            pass

    def _conta_sem_link(self) -> None:
        self.sem_link_seguidos += 1
        self._grava(CHAVE_SEM_LINK, str(self.sem_link_seguidos))

    def _rearma(self) -> None:
        """Uma verificação boa apaga a marca do dia: a figurinha voltou."""
        self.sem_link_seguidos = 0
        self._grava(CHAVE_SEM_LINK, "")
        self._grava(CHAVE_DESARMADO, "")

    def _desarma(self, aviso: str, persiste: bool = True) -> None:
        """Indisponível pelo resto do DIA.

        `max_per_run = 0` é o atributo que o pipeline JÁ lê para decidir se um
        canal ainda pode publicar (`aberto()`): zerá-lo fecha este canal sem
        que o pipeline precise conhecê-lo. `disponivel` é o mesmo fato com
        nome, para quem lê o código (e para o `publish` recusar chamada direta).

        E o desarme é GRAVADO: seis processos por dia insistindo contra uma
        conta sinalizada (ou publicando stories sem link) é exatamente o que
        esta fase existe para não fazer. Rearma na virada do dia local, numa
        verificação boa, ou num `afiliado ig-login` bem-sucedido.

        `persiste=False` só para a biblioteca ausente: isso não gasta nenhuma
        chamada ao Instagram, e prender o canal até amanhã faria o dono
        instalar o extra e não entender por que o dia seguiu mudo.
        """
        self.disponivel = False
        self.max_per_run = 0
        if persiste:
            self._grava(CHAVE_DESARMADO, aviso)
        self._avisa(aviso)

    def _avisa(self, texto: str) -> None:
        texto = self._sem_senha(texto)
        if texto not in self.warnings:
            self.warnings.append(texto)

    def _sem_senha(self, texto: str) -> str:
        """Nenhuma mensagem deste canal carrega `IG_PASSWORD` — nem quando ela
        vem DENTRO do texto de uma exceção de terceiro."""
        return sem_segredos(texto, self.password)

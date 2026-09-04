"""Canal `x` (fase 5Y): o X como superfície de AQUISIÇÃO, sem link nenhum.

**A regra que desenha este canal inteiro: o post NÃO PODE conter URL.** Não é
estilo — são duas penalidades somadas, uma de dinheiro e outra de alcance.

*Dinheiro.* Em 06/02/2026 o X trocou os planos por pay-per-use e acabou com o
tier grátis para desenvolvedor novo. Em 20/04/2026 o preço de um post COM link
foi de US$ 0,01 para **US$ 0,20**, contra **US$ 0,015** de um post sem link —
13× mais caro. Espelhar as 60 ofertas/dia com link daria ~US$ 360/mês; as
mesmas 60 sem link dão ~US$ 27.

*Alcance.* O X reduz a distribuição de post com link para manter quem lê dentro
da plataforma. As medições de 2026 divergem no tamanho (de 30–50% a menos de
alcance inicial até 94% em teste de Q1), mas concordam na direção — e para
conta SEM Premium o engajamento mediano de post com link fica perto de zero.

Somadas, elas dizem a mesma coisa: com link a gente **paga 13× para alcançar
menos**. Então o destino não vai no corpo do post, vai no **link da bio** — que
não é link de post e não sofre nenhuma das duas.

**E isso apaga um risco inteiro.** Sem link de afiliado aqui, não importa se o
X está ou não na lista de canais declarados no painel da Shopee — a busca de
2026-09-02 não confirmou que esteja, e publicar link de afiliado em canal não
declarado custa a monetização, não uma multa.

**A guarda é PORTÃO, não recomendação.** `sem_url` recusa o post antes de
qualquer chamada. Um link que escapasse custaria 13× e seria enterrado — e o
tipo de defeito que só aparece na fatura.

**Autenticação: OAuth 1.0a**, assinado à mão com a biblioteca padrão (hmac +
hashlib + base64). Não entra dependência nova por causa disto, e não há token
para renovar — o par de acesso do dono não expira sozinho, ao contrário do
fluxo OAuth 2.0 com refresh, que precisaria de arquivo de estado e de um
caminho de erro a mais na produção.
"""

import base64
import hashlib
import hmac
import re
import secrets
import time
import urllib.parse

import httpx

from afiliado import creative, pricing
from afiliado.channels.base import PublishResult
from afiliado.models import Post

__all__ = ["XChannel", "API", "LIMITE_CHARS", "URL_NO_TEXTO", "sem_url",
           "limpa_titulo", "monta_texto", "CUSTO_SEM_LINK_USD",
           "CUSTO_COM_LINK_USD"]

API = "https://api.x.com/2/tweets"

# O limite de conta sem Premium. Premium sobe para 25.000, mas a conta não tem
# Premium e escrever para um limite que não temos é como se publica truncado.
LIMITE_CHARS = 280

# Preço por post na API, em dólar (pay-per-use, 20/04/2026). Vive aqui porque é
# o número que justifica a regra do módulo — e porque o `doctor` estima o gasto
# do mês com ele.
CUSTO_SEM_LINK_USD = 0.015
CUSTO_COM_LINK_USD = 0.20

# O que o X trata como URL. Deliberadamente AMPLO: `t.me/fiscaldapromo` sem
# esquema também vira link clicável (e cobrado), e é justamente a forma que
# alguém escreveria sem perceber. Na dúvida, o portão fecha.
URL_NO_TEXTO = re.compile(
    r"https?://|www\.|\b[\w-]+\.(?:com|net|org|br|me|ly|io|co|app|shop|link)\b",
    re.IGNORECASE)

# A chamada para o Telegram, sem endereço nenhum. "link na bio" é texto: o
# campo de site do perfil não conta como link de post e não é penalizado.
CTA = "Todas as ofertas com link no meu Telegram — está na bio."


def sem_url(texto: str) -> bool:
    """O texto está livre de URL? É o portão do canal."""
    return not URL_NO_TEXTO.search(texto or "")


def limpa_titulo(titulo: str) -> str:
    """Tira do título qualquer palavra que o X leria como URL.

    O título é a única parte do post que vem de TERCEIRO — quem escreve é o
    vendedor, no anúncio. E vendedor escreve de tudo: "Kit 3 Cremes t.me da
    marca X" é um nome plausível, e ele sozinho faria o post custar 13× e
    perder alcance.

    Sem isto, o portão de `publish` recusaria a oferta — e recusar é pior que
    limpar: a oferta é boa, o defeito está numa palavra do nome dela. Aqui o
    dado de terceiro é SANEADO, e o portão continua sendo a última linha de
    defesa para o que é nosso.
    """
    palavras = [p for p in (titulo or "").split() if sem_url(p)]
    return " ".join(palavras)


def _corta(titulo: str, sobra: int) -> str:
    """O título reduzido ao que sobra, cortando em PALAVRA e com reticências.

    Cortar no meio de uma palavra é a marca visual de post automático, e é o
    detalhe que o leitor usa para decidir que não vale ler.
    """
    titulo = " ".join(titulo.split())
    if len(titulo) <= sobra:
        return titulo
    if sobra <= 1:
        return ""
    pedaco = titulo[:sobra - 1]
    espaco = pedaco.rfind(" ")
    return (pedaco[:espaco] if espaco > sobra // 2 else pedaco).rstrip(" ,.-") + "…"


def monta_texto(post: Post, limite: int = LIMITE_CHARS) -> str:
    """O post do X: preço, prova social, assinatura e a chamada para o Telegram.

    O TÍTULO é a única parte elástica — ele encolhe para o resto caber inteiro.
    A ordem é essa porque o que sustenta a marca (o preço conferido e a
    assinatura) não pode ser o que some quando o nome do produto é longo, e
    nome de anúncio de marketplace é sempre longo.
    """
    offer, verdict = post.offer, post.verdict
    linha_preco, prova = pricing.price_line(offer, verdict)
    fixos = [linha_preco]
    if prova:
        fixos.append(prova)
    if verdict.seal:
        fixos.append(verdict.seal)
    rodape = f"{creative.ASSINATURA}\n{CTA}"
    # +1 pela quebra de linha depois do título, +2 pela linha em branco.
    reservado = len("\n".join(fixos)) + len(rodape) + 3
    titulo = _corta(limpa_titulo(offer.title), max(0, limite - reservado))
    corpo = "\n".join(([titulo] if titulo else []) + fixos)
    return f"{corpo}\n\n{rodape}"


def _percent(valor: str) -> str:
    """A codificação que o OAuth 1.0a exige: RFC 3986, sem exceções."""
    return urllib.parse.quote(str(valor), safe="-._~")


class XChannel:
    """Publica no X. Texto puro, sem URL, uma oferta por post."""

    name = "x"
    max_per_run = 1

    def __init__(self, api_key: str, api_secret: str, token: str, token_secret: str,
                 client: httpx.Client | None = None):
        self.api_key = api_key
        self.api_secret = api_secret
        self.token = token
        self.token_secret = token_secret
        self.client = client or httpx.Client(timeout=30)

    # -- OAuth 1.0a ------------------------------------------------------------

    def _assinatura(self, metodo: str, url: str, oauth: dict) -> str:
        """A assinatura HMAC-SHA1 do pedido.

        O corpo NÃO entra na base: para `POST /2/tweets` ele é JSON, e o
        OAuth 1.0a só assina parâmetros de formulário e de query. Assinar o
        JSON faria toda chamada voltar 401 — é o erro clássico deste endpoint.
        """
        base = "&".join([
            metodo.upper(), _percent(url),
            _percent("&".join(f"{_percent(k)}={_percent(v)}"
                              for k, v in sorted(oauth.items()))),
        ])
        chave = f"{_percent(self.api_secret)}&{_percent(self.token_secret)}"
        bruto = hmac.new(chave.encode(), base.encode(), hashlib.sha1).digest()
        return base64.b64encode(bruto).decode()

    def _cabecalho(self, metodo: str, url: str) -> str:
        oauth = {
            "oauth_consumer_key": self.api_key,
            "oauth_nonce": secrets.token_hex(16),
            "oauth_signature_method": "HMAC-SHA1",
            "oauth_timestamp": str(int(time.time())),
            "oauth_token": self.token,
            "oauth_version": "1.0",
        }
        oauth["oauth_signature"] = self._assinatura(metodo, url, oauth)
        return "OAuth " + ", ".join(f'{_percent(k)}="{_percent(v)}"'
                                    for k, v in sorted(oauth.items()))

    # -- publicação ------------------------------------------------------------

    def publish(self, post: Post) -> PublishResult:
        texto = monta_texto(post)
        # O PORTÃO. Antes de qualquer chamada: um post com URL custa 13× e é
        # enterrado pelo algoritmo. Recusar é o barato; descobrir na fatura é o
        # caro.
        if not sem_url(texto):
            return PublishResult(False, error=(
                "post do X recusado: o texto contém URL, e post com link custa "
                f"US$ {CUSTO_COM_LINK_USD:.2f} contra US$ {CUSTO_SEM_LINK_USD:.3f} "
                "— além de perder alcance. O destino vai na BIO."))
        if len(texto) > LIMITE_CHARS:
            # `monta_texto` já encolhe o título para caber; chegar aqui é
            # defeito do montador, não do produto — e um 400 do X seria um
            # diagnóstico pior do que esta frase.
            return PublishResult(False, error=(
                f"post do X com {len(texto)} chars (limite {LIMITE_CHARS}) — "
                "o montador não encolheu o título"))
        try:
            r = self.client.post(
                API, json={"text": texto},
                headers={"Authorization": self._cabecalho("POST", API),
                         "Content-Type": "application/json"})
            dados = r.json()
        except httpx.HTTPError as exc:
            return PublishResult(False, error=f"rede: {exc}")
        except ValueError:
            return PublishResult(False, error="resposta não-JSON do X")
        tweet_id = ((dados.get("data") or {}).get("id")
                    if isinstance(dados, dict) else None)
        if not tweet_id:
            erro = ""
            if isinstance(dados, dict):
                erro = (dados.get("detail") or dados.get("title")
                        or (dados.get("errors") or [{}])[0].get("message", ""))
            # `publicado=True` pela mesma razão dos canais da Meta (fase 5X):
            # o pedido foi feito e a resposta não prova que nada saiu. Repetir
            # custa dinheiro por post, então aqui o erro para o lado seguro é
            # ainda mais claro.
            return PublishResult(False, publicado=True,
                                 error=erro or f"X devolveu {r.status_code}")
        return PublishResult(True, str(tweet_id))

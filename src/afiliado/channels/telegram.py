import time
from collections.abc import Callable

import httpx

from afiliado.channels.base import PublishResult
from afiliado.models import Post

API = "https://api.telegram.org"
_ATTEMPTS = 3
# Fase 5A (A4): 429 com `parameters.retry_after` até este valor → dorme e
# tenta UMA vez mais; acima disso devolve a falha (o run seguinte, em 5 min,
# encontra a janela livre). Antes o 429 era ignorado e o pipeline seguia para
# a PRÓXIMA oferta dentro da mesma janela de limite.
MAX_RETRY_AFTER_S = 30
# Fase 5A (C5): sendMessage aceita 4096 chars; acima disso a API devolve 400
# e o resumo de ops — o canal por onde passam TODOS os avisos — sumia em
# silêncio. Mensagens longas são divididas em quebras de linha.
MAX_MESSAGE_CHARS = 4000


def _post_once(client: httpx.Client, url: str, payload: dict) -> dict:
    last = ""
    for _ in range(_ATTEMPTS):
        try:
            r = client.post(url, json=payload)
            return r.json()
        except httpx.HTTPError as exc:
            last = str(exc)
        except ValueError:
            return {"ok": False, "description": "resposta não-JSON"}
        except Exception as exc:
            # Fora da árvore de httpx.HTTPError — ex.: httpx.InvalidURL, que
            # NÃO é subclasse de HTTPError e escaparia se bot_token/chat_id
            # vierem com caractere de controle embutido (ex.: "\n"). Não é um
            # erro de rede transitório, então não faz sentido re-tentar;
            # devolve a falha já no mesmo formato do timeout de tentativas.
            return {"ok": False, "description": f"rede: {exc}"}
    return {"ok": False, "description": f"rede: {last}"}


def retry_after_s(data: dict) -> int | None:
    """`parameters.retry_after` da resposta (429), ou None."""
    params = data.get("parameters") if isinstance(data, dict) else None
    valor = params.get("retry_after") if isinstance(params, dict) else None
    if valor is None:
        return None
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def _post_api(client: httpx.Client, url: str, payload: dict,
              sleep: Callable[[float], None] = time.sleep) -> dict:
    """POST à Bot API com retry de rede (3×) e honra de `retry_after` curto
    (uma repetição). Nunca levanta; devolve o dict da API ou
    {"ok": False, "description": ...}."""
    data = _post_once(client, url, payload)
    espera = retry_after_s(data)
    if not data.get("ok") and espera is not None and espera <= MAX_RETRY_AFTER_S:
        sleep(espera)
        data = _post_once(client, url, payload)
    return data


class TelegramChannel:
    name = "telegram"

    def __init__(self, bot_token: str, chat_id: str, client: httpx.Client | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        # .strip() mata o footgun clássico de token/chat_id colado com
        # espaço/quebra de linha nas pontas (env var, clipboard).
        self.base = f"{API}/bot{bot_token.strip()}"
        self.chat_id = chat_id.strip()
        self.client = client or httpx.Client(timeout=30)
        self.sleep = sleep

    def publish(self, post: Post) -> PublishResult:
        data = _post_api(self.client, f"{self.base}/sendPhoto", {
            "chat_id": self.chat_id,
            "photo": post.offer.image_url,
            "caption": post.message_text,
            "parse_mode": "HTML",
        }, sleep=self.sleep)
        # O fallback para sendMessage existe para "foto recusada"; num rate
        # limit ele só gastaria outra chamada dentro da mesma janela.
        if not data.get("ok") and retry_after_s(data) is None:
            data = _post_api(self.client, f"{self.base}/sendMessage", {
                "chat_id": self.chat_id,
                "text": post.message_text,
                "parse_mode": "HTML",
            }, sleep=self.sleep)
        if data.get("ok"):
            message_id = str(((data.get("result") or {}).get("message_id", "")))
            return PublishResult(True, message_id)
        return PublishResult(False, error=str(data.get("description") or "desconhecido"))


def split_message(text: str, limit: int = MAX_MESSAGE_CHARS) -> list[str]:
    """Divide `text` em partes de até `limit` chars cortando em quebras de
    linha (uma linha maior que o limite sofre corte duro). Juntar as partes
    com "\\n" reconstrói o texto original quando nenhuma linha estourou."""
    if len(text) <= limit:
        return [text]
    partes: list[str] = []
    buf: list[str] = []
    tamanho = 0
    for linha in text.split("\n"):
        while len(linha) > limit:
            if buf:
                partes.append("\n".join(buf))
                buf, tamanho = [], 0
            partes.append(linha[:limit])
            linha = linha[limit:]
        extra = len(linha) + (1 if buf else 0)
        if buf and tamanho + extra > limit:
            partes.append("\n".join(buf))
            buf, tamanho = [], 0
            extra = len(linha)
        buf.append(linha)
        tamanho += extra
    if buf:
        partes.append("\n".join(buf))
    return partes


def send_text(bot_token: str, chat_id: str, text: str,
              client: httpx.Client | None = None,
              sleep: Callable[[float], None] = time.sleep) -> bool:
    """Notificação de ops. Divide em mensagens de até MAX_MESSAGE_CHARS,
    confere `ok` de cada resposta e devolve True só se todas foram aceitas;
    falha vai ao stdout (journal) com o `description` da API — 4xx nunca é
    silencioso. Nunca levanta: a notificação não derruba o run."""
    c = client or httpx.Client(timeout=30)
    tudo_ok = True
    for parte in split_message(text):
        data = _post_api(c, f"{API}/bot{bot_token}/sendMessage",
                         {"chat_id": chat_id, "text": parte}, sleep=sleep)
        if not data.get("ok"):
            tudo_ok = False
            print("⚠️ ops: envio ao chat de operações falhou: "
                  f"{data.get('description') or 'desconhecido'}")
    return tudo_ok


def send_photo_bytes(bot_token: str, chat_id: str, png_bytes: bytes,
                     caption: str = "", client: httpx.Client | None = None,
                     filename: str = "art.png", mime: str = "image/png") -> dict:
    """sendPhoto multipart. Retorna o dict da API; em erro de rede/parse retorna
    {"ok": False, "description": ...}. Nunca levanta."""
    c = client or httpx.Client(timeout=30)
    try:
        r = c.post(f"{API}/bot{bot_token}/sendPhoto",
                   files={"photo": (filename, png_bytes, mime)},
                   data={"chat_id": chat_id, "caption": caption})
        return r.json()
    except ValueError:
        return {"ok": False, "description": "resposta não-JSON"}
    except Exception as exc:
        # Contrato desta função é nunca levantar: cobre httpx.HTTPError (rede)
        # e também casos fora dessa hierarquia como httpx.InvalidURL (token/
        # chat_id com caractere de controle embutido, ex.: "\n" no meio de um
        # segredo colado errado) — nenhum dos dois pode escapar para o canal.
        return {"ok": False, "description": f"rede: {exc}"}


def get_file_url(bot_token: str, file_id: str, client: httpx.Client | None = None) -> str | None:
    """getFile → https://api.telegram.org/file/bot{token}/{file_path}; None em falha."""
    c = client or httpx.Client(timeout=30)
    try:
        r = c.get(f"{API}/bot{bot_token}/getFile", params={"file_id": file_id})
        data = r.json()
    except Exception:
        # Nunca levanta: cobre httpx.HTTPError (rede), ValueError (JSON) e
        # httpx.InvalidURL — que NÃO é subclasse de HTTPError e escaparia se o
        # bot_token vier com um caractere de controle embutido (ex.: "\n").
        return None
    if not data.get("ok"):
        return None
    file_path = (data.get("result") or {}).get("file_path")
    if not file_path:
        return None
    return f"{API}/file/bot{bot_token}/{file_path}"

import httpx

from afiliado.channels.base import PublishResult
from afiliado.models import Post

API = "https://api.telegram.org"
_ATTEMPTS = 3


def _post_api(client: httpx.Client, url: str, payload: dict) -> dict:
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


class TelegramChannel:
    name = "telegram"

    def __init__(self, bot_token: str, chat_id: str, client: httpx.Client | None = None):
        # .strip() mata o footgun clássico de token/chat_id colado com
        # espaço/quebra de linha nas pontas (env var, clipboard).
        self.base = f"{API}/bot{bot_token.strip()}"
        self.chat_id = chat_id.strip()
        self.client = client or httpx.Client(timeout=30)

    def publish(self, post: Post) -> PublishResult:
        data = _post_api(self.client, f"{self.base}/sendPhoto", {
            "chat_id": self.chat_id,
            "photo": post.offer.image_url,
            "caption": post.message_text,
            "parse_mode": "HTML",
        })
        if not data.get("ok"):
            data = _post_api(self.client, f"{self.base}/sendMessage", {
                "chat_id": self.chat_id,
                "text": post.message_text,
                "parse_mode": "HTML",
            })
        if data.get("ok"):
            message_id = str(((data.get("result") or {}).get("message_id", "")))
            return PublishResult(True, message_id)
        return PublishResult(False, error=str(data.get("description") or "desconhecido"))


def send_text(bot_token: str, chat_id: str, text: str,
              client: httpx.Client | None = None) -> None:
    c = client or httpx.Client(timeout=30)
    try:
        c.post(f"{API}/bot{bot_token}/sendMessage",
               json={"chat_id": chat_id, "text": text})
    except httpx.HTTPError:
        pass  # notificação de ops nunca derruba o run


def send_photo_bytes(bot_token: str, chat_id: str, png_bytes: bytes,
                     caption: str = "", client: httpx.Client | None = None) -> dict:
    """sendPhoto multipart. Retorna o dict da API; em erro de rede/parse retorna
    {"ok": False, "description": ...}. Nunca levanta."""
    c = client or httpx.Client(timeout=30)
    try:
        r = c.post(f"{API}/bot{bot_token}/sendPhoto",
                  files={"photo": ("art.png", png_bytes, "image/png")},
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

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
        except ValueError as exc:
            return {"ok": False, "description": "resposta não-JSON"}
    return {"ok": False, "description": f"rede: {last}"}


class TelegramChannel:
    name = "telegram"

    def __init__(self, bot_token: str, chat_id: str, client: httpx.Client | None = None):
        self.base = f"{API}/bot{bot_token}"
        self.chat_id = chat_id
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

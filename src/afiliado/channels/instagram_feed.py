"""Canal `instagram_feed` (fase 2A): post de feed 100% automático via Meta
Graph API. Desligado por padrão em config.yaml até `docs/runbooks/meta-setup.md`
ser concluído (conta business + página vinculada + token de acesso).

A arte gerada por `creative.render_feed` precisa de uma URL pública para a
Meta buscar; como o pipeline não tem hospedagem própria, reaproveitamos o bot
do Telegram como "CDN temporária": enviamos a foto ao chat de operações e
usamos a URL de `getFile`. Essa URL contém o bot token e expira em ~1h, mas a
Meta baixa a imagem na hora da chamada a `/media` — trade-off aceito para não
exigir infraestrutura de hospedagem extra.
"""

import httpx

from afiliado import creative
from afiliado.channels.base import PublishResult
from afiliado.channels.telegram import get_file_url, send_photo_bytes
from afiliado.errors import SourceError
from afiliado.models import Post, format_brl

GRAPH = "https://graph.facebook.com/v21.0"


def _graph_error(resp) -> str:
    if isinstance(resp, dict):
        err = resp.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
    return f"resposta inesperada da Graph API: {resp!r}"


class InstagramFeedChannel:
    name = "instagram_feed"
    max_per_run = 1

    def __init__(self, ig_user_id: str, access_token: str, bot_token: str, ops_chat_id: str,
                 client: httpx.Client | None = None, brand_handle: str | None = None,
                 brand_name: str = "Fiscal da Promo"):
        # .strip() mata o footgun clássico de segredo colado com espaço/quebra
        # de linha nas pontas (env var, clipboard); não cobre caractere de
        # controle NO MEIO da string — para isso, ver o try/except amplo em
        # _graph_post (httpx.InvalidURL não é subclasse de httpx.HTTPError).
        self.ig_user_id = ig_user_id.strip()
        self.access_token = access_token.strip()
        self.bot_token = bot_token.strip()
        self.ops_chat_id = ops_chat_id.strip()
        self.client = client or httpx.Client(timeout=30)
        self.brand_handle = brand_handle
        self.brand_name = brand_name

    def publish(self, post: Post) -> PublishResult:
        try:
            art = creative.render_feed(post.offer, post.copy, price_floor=post.price_floor,
                                       client=self.client, handle=self.brand_handle,
                                       brand_name=self.brand_name)
        except SourceError as exc:
            return PublishResult(False, error=f"falha ao gerar arte do feed: {exc}")

        image_url = self._host_art(art)
        if image_url is None:
            return PublishResult(False, error="falha ao hospedar arte temporariamente")

        caption = self._build_caption(post)

        media_resp = self._graph_post(f"{GRAPH}/{self.ig_user_id}/media", {
            "image_url": image_url,
            "caption": caption,
            "access_token": self.access_token,
        })
        creation_id = media_resp.get("id") if isinstance(media_resp, dict) else None
        if not creation_id:
            return PublishResult(False, error=_graph_error(media_resp))

        publish_resp = self._graph_post(f"{GRAPH}/{self.ig_user_id}/media_publish", {
            "creation_id": creation_id,
            "access_token": self.access_token,
        })
        post_id = publish_resp.get("id") if isinstance(publish_resp, dict) else None
        if not post_id:
            return PublishResult(False, error=_graph_error(publish_resp))

        return PublishResult(True, str(post_id))

    def _host_art(self, art: bytes) -> str | None:
        photo_result = send_photo_bytes(self.bot_token, self.ops_chat_id, art,
                                        caption="hospedagem temporária (feed IG)",
                                        client=self.client)
        if not photo_result.get("ok"):
            return None
        photos = (photo_result.get("result") or {}).get("photo") or []
        if not photos:
            return None
        file_id = photos[-1].get("file_id")
        if not file_id:
            return None
        return get_file_url(self.bot_token, file_id, client=self.client)

    @staticmethod
    def _sanitize_title(title: str) -> str:
        """Corta o título no primeiro "http" (case-insensitive) — o título vem
        de dados de terceiros (a oferta) e não pode carregar um link para o
        caption público do Instagram."""
        idx = title.lower().find("http")
        if idx == -1:
            return title
        return title[:idx].rstrip(" \t\n\r.,;:-–—!?/\\|")

    @classmethod
    def _build_caption(cls, post: Post) -> str:
        offer, copy = post.offer, post.copy
        titulo = cls._sanitize_title(offer.title)
        return (
            f"{copy.headline}\n{copy.description}\n\n"
            f"{titulo}\n"
            f"De {format_brl(offer.price_original_cents)} por "
            f"{format_brl(offer.price_current_cents)} ({offer.discount_pct}% OFF)\n\n"
            f"{copy.cta}\n"
            "🔗 Link na bio e no canal do Telegram"
        )

    def _graph_post(self, url: str, payload: dict) -> dict:
        try:
            r = self.client.post(url, data=payload)
            return r.json()
        except ValueError:
            return {"error": {"message": "resposta não-JSON"}}
        except Exception as exc:
            # Nunca levanta: além de httpx.HTTPError (rede), cobre
            # httpx.InvalidURL — que NÃO é subclasse de HTTPError e escaparia
            # se ig_user_id/access_token vierem com caractere de controle
            # embutido (ex.: "\n" no meio de uma env var mal colada).
            return {"error": {"message": f"rede: {exc}"}}

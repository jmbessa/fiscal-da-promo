"""Canal `story_dispatch` (fase 2A): arte de story pronta + link de afiliado
entregues ao chat de operações do Telegram. A API oficial do Instagram não
suporta o sticker de link em stories, então este canal fica semi-automático:
o dono do projeto posta a arte no app e cola o sticker com o link recebido.
"""

import httpx

from afiliado import creative, message, pricing
from afiliado.channels.base import PublishResult
from afiliado.channels.telegram import API, _post_api, send_photo_bytes
from afiliado.errors import SourceError
from afiliado.models import Post


class StoryDispatchChannel:
    name = "story_dispatch"

    def __init__(self, bot_token: str, ops_chat_id: str, client: httpx.Client | None = None,
                 brand_handle: str | None = None, brand_name: str = "Fiscal da Promo",
                 min_real_discount_pct: int = pricing.DEFAULT_MIN_REAL_DISCOUNT_PCT,
                 seal_tolerance: float = message.DEFAULT_SEAL_TOLERANCE):
        # .strip() mata o footgun clássico de token/chat_id colado com
        # espaço/quebra de linha nas pontas (env var, clipboard).
        self.bot_token = bot_token.strip()
        self.ops_chat_id = ops_chat_id.strip()
        self.client = client or httpx.Client(timeout=30)
        self.brand_handle = brand_handle
        self.brand_name = brand_name
        # Régua honesta (selection.* do config, via cli._build_channels):
        # min_real_discount_pct decide o modo da arte; seal_tolerance fica
        # guardado para quando a arte ganhar o selo do histórico próprio —
        # hoje o selo da arte é só o da watchlist (creative._selo_applicable).
        self.min_real_discount_pct = min_real_discount_pct
        self.seal_tolerance = seal_tolerance

    def publish(self, post: Post) -> PublishResult:
        try:
            art = creative.render_story(post.offer, post.copy, price_floor=post.price_floor,
                                        client=self.client, handle=self.brand_handle,
                                        brand_name=self.brand_name,
                                        min_real_discount_pct=self.min_real_discount_pct)
        except SourceError as exc:
            return PublishResult(False, error=f"falha ao gerar arte do story: {exc}")

        caption = (
            "📲 STORY PRONTO — poste no app e cole o sticker de link\n\n"
            f"{post.offer.title}"
        )
        photo_result = send_photo_bytes(self.bot_token, self.ops_chat_id, art,
                                        caption=caption, client=self.client)
        if not photo_result.get("ok"):
            return PublishResult(
                False, error=str(photo_result.get("description") or "falha ao enviar story"))
        message_id = str((photo_result.get("result") or {}).get("message_id", ""))

        text_result = _post_api(self.client, f"{API}/bot{self.bot_token}/sendMessage", {
            "chat_id": self.ops_chat_id,
            "text": post.affiliate_link,
        })
        if not text_result.get("ok"):
            return PublishResult(
                False, error=str(text_result.get("description") or "falha ao enviar link"))

        return PublishResult(True, message_id)

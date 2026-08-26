"""Canal `story_dispatch` (fase 2A): arte de story pronta + link de afiliado
entregues ao chat de operações do Telegram. A API oficial do Instagram não
suporta o sticker de link em stories, então este canal fica semi-automático:
o dono do projeto posta a arte no app e cola o sticker com o link recebido.
"""

import httpx

from afiliado import creative, pricing
from afiliado.channels.base import PublishResult
from afiliado.channels.telegram import API, _post_api, send_photo_bytes
from afiliado.errors import SourceError
from afiliado.models import Post


class StoryDispatchChannel:
    name = "story_dispatch"

    def __init__(self, bot_token: str, ops_chat_id: str, client: httpx.Client | None = None,
                 brand_handle: str | None = None, brand_name: str = "Fiscal da Promo"):
        # .strip() mata o footgun clássico de token/chat_id colado com
        # espaço/quebra de linha nas pontas (env var, clipboard).
        self.bot_token = bot_token.strip()
        self.ops_chat_id = ops_chat_id.strip()
        self.client = client or httpx.Client(timeout=30)
        self.brand_handle = brand_handle
        self.brand_name = brand_name

    @staticmethod
    def _build_caption(post: Post) -> str:
        """Legenda do despacho: o mesmo bloco de preço/selo que a arte
        desenha e o Telegram publica — o dono vê o que o post alega."""
        linha_preco, prova = pricing.price_line(post.offer, post.verdict)
        bloco = "\n".join(p for p in (linha_preco, prova, post.verdict.seal) if p)
        return (
            "📲 STORY PRONTO — poste no app e cole o sticker de link\n\n"
            f"{post.offer.title}\n{bloco}"
        )

    def publish(self, post: Post) -> PublishResult:
        # A arte recebe o veredito do post — não recalcula modo nem selo.
        try:
            art = creative.render_story(post.offer, post.copy, post.verdict,
                                        client=self.client, handle=self.brand_handle,
                                        brand_name=self.brand_name)
        except SourceError as exc:
            return PublishResult(False, error=f"falha ao gerar arte do story: {exc}")

        photo_result = send_photo_bytes(self.bot_token, self.ops_chat_id, art,
                                        caption=self._build_caption(post), client=self.client)
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

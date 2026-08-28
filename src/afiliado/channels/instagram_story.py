"""Canal `instagram_story` (fase 5E): story publicado 100% automático pela
Meta Graph API.

O projeto acreditou por três fases que story pela API oficial era impossível e
criou o `story_dispatch` (arte no chat de operações, o dono posta à mão — 6
gestos por dia). **A premissa estava errada.** Medido AO VIVO na conta do dono
em 2026-08-27 (ver `docs/runbooks/meta-setup.md`):

1. `POST /{ig_user_id}/media` com `image_url` + `media_type=STORIES` cria o
   container. **Sem `caption`** — story não aceita legenda pela API.
2. `GET /{creation_id}?fields=status_code,status` devolve `IN_PROGRESS` logo
   depois ("Media is still being processed.").
3. `POST /{ig_user_id}/media_publish` com `creation_id` publicou mesmo com o
   container ainda `IN_PROGRESS`. Ainda assim este canal faz polling: com uma
   imagem maior, contar com isso é o tipo de coisa que falha em produção.
4. O resultado sai com `media_product_type: "STORY"` e permalink
   `https://www.instagram.com/stories/<handle>/<id>`.
5. A cota de publicação é COMPARTILHADA com o feed: 100 por 24 h
   (`/{ig_user_id}/content_publishing_limit`). 2 posts + 6 stories usam 8.
6. **Não existe sticker de link pela API.** O story sai sem link clicável — a
   arte já traz o handle e a chamada, e a legenda do feed diz "link na bio e no
   canal do Telegram". Não invente um sticker.

Este canal é publicação de VERDADE: ao contrário do `story_dispatch`, não
declara `manual`, então o pipeline o conta em `summary.published`, em
`day_stats().published` e no heartbeat da manhã.
"""

from afiliado import creative
from afiliado.channels.base import PublishResult
from afiliado.channels.instagram_common import (AVISO_POLLING_CEGO_TMPL,
                                                STATUS_TERMINAIS,
                                                InstagramBase, LeituraDoContainer,
                                                graph_error, to_jpeg)
from afiliado.errors import SourceError
from afiliado.models import Post

__all__ = ["AVISO_POLLING_CEGO", "STATUS_TERMINAIS", "LeituraDoContainer",
           "InstagramStoryChannel"]

# Fase 5D: o polling do container, os estados terminais e o aviso de polling
# cego subiram para `instagram_common` — o carrossel do feed precisa dos
# mesmos. O texto do aviso deste canal continua idêntico ao da 5E.
AVISO_POLLING_CEGO = AVISO_POLLING_CEGO_TMPL.format(canal="instagram_story")


class InstagramStoryChannel(InstagramBase):
    name = "instagram_story"
    max_per_run = 1
    host_caption = "hospedagem temporária (story IG)"

    def publish(self, post: Post) -> PublishResult:
        # A arte recebe o veredito do post (modo + selo) — não recalcula nada,
        # é o que faz arte, texto do Telegram e legenda do feed concordarem.
        try:
            art = creative.render_story(post.offer, post.copy, post.verdict,
                                        client=self.client, handle=self.brand_handle,
                                        brand_name=self.brand_name)
        except SourceError as exc:
            return PublishResult(False, error=f"falha ao gerar arte do story: {exc}")

        image_url = self._host_art(to_jpeg(art))
        if image_url is None:
            return PublishResult(False, error="falha ao hospedar arte temporariamente")

        # Sem `caption`: a API recusa legenda em story (fato 1).
        media_resp = self._graph_post(f"{self.graph}/{self.ig_user_id}/media", {
            "image_url": image_url,
            "media_type": "STORIES",
            "access_token": self.access_token,
        })
        creation_id = media_resp.get("id") if isinstance(media_resp, dict) else None
        if not creation_id:
            return PublishResult(False, error=graph_error(media_resp))

        leitura = self._aguarda_container(str(creation_id))
        if leitura.status_code in STATUS_TERMINAIS:
            # O container morreu: publicar seria uma segunda chamada inútil
            # contra a cota de 100/24 h compartilhada com o feed.
            return PublishResult(False,
                                 error=f"container {leitura.status_code}: {leitura.status}")
        if not leitura.leu:
            self._avisa_polling_cego()

        publish_resp = self._graph_post(f"{self.graph}/{self.ig_user_id}/media_publish", {
            "creation_id": creation_id,
            "access_token": self.access_token,
        })
        media_id = publish_resp.get("id") if isinstance(publish_resp, dict) else None
        if not media_id:
            erro = graph_error(publish_resp)
            detalhe = self._sobre_o_container(leitura)
            if detalhe:
                # A causa provável do publish falho é justamente esta, e sem a
                # linha ela sumia do resumo de operações.
                erro = f"{erro} ({detalhe})"
            return PublishResult(False, error=erro)

        return PublishResult(True, str(media_id))

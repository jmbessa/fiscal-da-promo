"""Canal `instagram_feed` (fase 2A): post de feed 100% automático via Meta
Graph API. Desligado por padrão em config.yaml até `docs/runbooks/meta-setup.md`
ser concluído (conta business + página vinculada + token de acesso).

A arte gerada por `creative.render_feed` precisa de uma URL pública para a
Meta buscar; como o pipeline não tem hospedagem própria, reaproveitamos um bot
do Telegram como "CDN temporária": enviamos a foto ao chat de operações e
usamos a URL de `getFile`.

Essa URL carrega o bot token (`api.telegram.org/file/bot{TOKEN}/...`) — e o
que expira nela é o `file_path`, não o token. Fase 5C (A5): quem hospeda a
arte passa a ser um **bot secundário** (`ART_HOST_BOT_TOKEN`), que só precisa
estar no chat de operações; assim o token do bot ADMINISTRADOR do canal
público nunca é entregue à Meta. Sem essa variável o comportamento é o de
antes — com um aviso diário no resumo de operações (ver `cli._build_channels`).

Fase 5E: construtor, hospedagem da arte e as chamadas à Graph API mudaram de
casa para `instagram_common` — o story virou um segundo canal automático e usa
exatamente as mesmas. Nada do comportamento do feed mudou nessa viagem.
"""

from afiliado import creative, pricing
from afiliado.channels.base import PublishResult
from afiliado.channels.instagram_common import (GRAPH, GRAPH_HOSTS, STATUS_TERMINAIS,
                                                InstagramBase, graph_error, to_jpeg)
from afiliado.errors import SourceError
from afiliado.models import Post

__all__ = ["GRAPH", "GRAPH_HOSTS", "MAX_ITENS_CARROSSEL", "InstagramFeedChannel"]

# Teto da Meta para um álbum. O teto do DESENHO é outro e menor
# (`creative.CARROSSEL_MAX_SLIDES`, 8); este aqui é o da API, e existe para o
# canal recusar antes de gastar N uploads e N containers.
MAX_ITENS_CARROSSEL = 10


class InstagramFeedChannel(InstagramBase):
    name = "instagram_feed"
    max_per_run = 1
    host_caption = "hospedagem temporária (feed IG)"

    def publish(self, post: Post) -> PublishResult:
        # A arte e a legenda recebem o veredito do post (modo + selo) — nada
        # é recalculado aqui; é o que faz texto, arte e legenda concordarem.
        try:
            art = creative.render_feed(post.offer, post.copy, post.verdict,
                                       client=self.client, handle=self.brand_handle,
                                       brand_name=self.brand_name)
        except SourceError as exc:
            return PublishResult(False, error=f"falha ao gerar arte do feed: {exc}")

        image_url = self._host_art(to_jpeg(art))
        if image_url is None:
            return PublishResult(False, error="falha ao hospedar arte temporariamente")

        caption = self._build_caption(post)

        media_resp = self._graph_post(f"{self.graph}/{self.ig_user_id}/media", {
            "image_url": image_url,
            "caption": caption,
            "access_token": self.access_token,
        })
        creation_id = media_resp.get("id") if isinstance(media_resp, dict) else None
        if not creation_id:
            return PublishResult(False, error=graph_error(media_resp))

        publish_resp = self._graph_post(f"{self.graph}/{self.ig_user_id}/media_publish", {
            "creation_id": creation_id,
            "access_token": self.access_token,
        })
        post_id = publish_resp.get("id") if isinstance(publish_resp, dict) else None
        if not post_id:
            return PublishResult(False, error=graph_error(publish_resp))

        return PublishResult(True, str(post_id))

    def publish_carrossel(self, imagens: list[bytes], caption: str) -> PublishResult:
        """Publica N imagens como UM post de carrossel (álbum).

        Três passos da Content Publishing API: um container por imagem com
        `is_carousel_item=true`, um container `media_type=CAROUSEL` com
        `children=<ids separados por vírgula>` e a legenda, e o `media_publish`
        do container pai.

        **A cota de publicação da Meta é de 100 por 24 h e um carrossel conta
        como UM post** — não como oito. É isto que torna o formato barato: seis
        ofertas por uma unidade de cota, contra seis posts de imagem única. Os
        containers filhos não consomem a cota; só o publish do pai.

        Mesma disciplina do story: NUNCA levanta. Qualquer erro — hospedagem,
        filho recusado, pai recusado, container morto, publish negado — vira um
        `PublishResult` falho com a mensagem da Meta. E o polling do container
        pai é o mesmo do story (`InstagramBase._aguarda_container`)."""
        if not imagens:
            return PublishResult(False, error="carrossel sem imagem: nada a publicar")
        if len(imagens) > MAX_ITENS_CARROSSEL:
            return PublishResult(
                False, error=f"carrossel com {len(imagens)} imagens: a Meta aceita "
                             f"no máximo {MAX_ITENS_CARROSSEL}")

        filhos: list[str] = []
        for i, png in enumerate(imagens, start=1):
            image_url = self._host_art(to_jpeg(png))
            if image_url is None:
                return PublishResult(
                    False, error=f"falha ao hospedar temporariamente a imagem {i} "
                                 "do carrossel")
            resp = self._graph_post(f"{self.graph}/{self.ig_user_id}/media", {
                "image_url": image_url,
                "is_carousel_item": "true",
                "access_token": self.access_token,
            })
            filho_id = resp.get("id") if isinstance(resp, dict) else None
            if not filho_id:
                # Sem os filhos todos não existe pai: sair aqui evita criar um
                # álbum incompleto e gastar a cota com ele.
                return PublishResult(
                    False, error=f"imagem {i} do carrossel: {graph_error(resp)}")
            filhos.append(str(filho_id))

        pai_resp = self._graph_post(f"{self.graph}/{self.ig_user_id}/media", {
            "media_type": "CAROUSEL",
            "children": ",".join(filhos),
            "caption": caption,
            "access_token": self.access_token,
        })
        creation_id = pai_resp.get("id") if isinstance(pai_resp, dict) else None
        if not creation_id:
            return PublishResult(False, error=graph_error(pai_resp))

        leitura = self._aguarda_container(str(creation_id))
        if leitura.status_code in STATUS_TERMINAIS:
            return PublishResult(
                False, error=f"container {leitura.status_code}: {leitura.status}")
        if not leitura.leu:
            self._avisa_polling_cego()

        publish_resp = self._graph_post(f"{self.graph}/{self.ig_user_id}/media_publish", {
            "creation_id": creation_id,
            "access_token": self.access_token,
        })
        post_id = publish_resp.get("id") if isinstance(publish_resp, dict) else None
        if not post_id:
            erro = graph_error(publish_resp)
            detalhe = self._sobre_o_container(leitura)
            return PublishResult(False, error=f"{erro} ({detalhe})" if detalhe else erro)
        return PublishResult(True, str(post_id))

    @staticmethod
    def _sanitize_title(title: str) -> str:
        """Corta o título no primeiro "http" (case-insensitive) — o título vem
        de dados de terceiros (a oferta) e não pode carregar um link para o
        caption público do Instagram."""
        idx = title.lower().find("http")
        if idx == -1:
            return title
        return title[:idx].rstrip(" \t\n\r.,;:-–—!?/\\|")

    def _build_caption(self, post: Post) -> str:
        offer, copy = post.offer, post.copy
        titulo = self._sanitize_title(offer.title)
        # Mesmo veredito do texto do Telegram e da arte: linha de preço,
        # prova social e selo vêm de `post.verdict` (ver afiliado.pricing).
        linha_preco, prova_social = pricing.price_line(offer, post.verdict)
        bloco_preco = "\n".join(p for p in (linha_preco, prova_social, post.verdict.seal) if p)
        return (
            f"{copy.headline}\n{copy.description}\n\n"
            f"{titulo}\n"
            f"{bloco_preco}\n\n"
            f"{copy.cta}\n"
            "🔗 Link na bio e no canal do Telegram"
        )

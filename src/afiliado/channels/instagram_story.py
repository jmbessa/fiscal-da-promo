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

import time
from typing import Callable, NamedTuple

from afiliado import creative
from afiliado.channels.base import PublishResult
from afiliado.channels.instagram_common import InstagramBase, graph_error, to_jpeg
from afiliado.errors import SourceError
from afiliado.models import Post

# Estados terminais do container: não adianta continuar esperando.
STATUS_TERMINAIS = ("ERROR", "EXPIRED")

# Polling cego: nenhuma das leituras trouxe `status_code`. O story sai assim
# mesmo (o container existe), mas cada um custa `max_polls` GETs e
# `(max_polls - 1) × poll_interval` de espera — um imposto fixo e invisível,
# todo dia, para sempre. O pipeline recolhe este aviso e o `warn_once` o
# deduplica: uma linha por dia no resumo de operações.
AVISO_POLLING_CEGO = ("⚠️ instagram_story: a Meta não devolveu status_code do "
                      "container — polling cego")


class LeituraDoContainer(NamedTuple):
    """O que o polling DE FATO observou.

    `leu` separa "li IN_PROGRESS cinco vezes" de "nunca consegui ler status
    nenhum" — sem ele o canal relatava `IN_PROGRESS` mesmo quando nenhum GET
    chegou à Meta, e a causa real (rede, resposta sem o campo) era descartada
    em silêncio. `status` é a última coisa lida: o texto da Meta quando houve
    um, o erro da chamada quando não houve.
    """
    status_code: str
    status: str
    leu: bool


class InstagramStoryChannel(InstagramBase):
    name = "instagram_story"
    max_per_run = 1
    host_caption = "hospedagem temporária (story IG)"
    # Polling do container: 5 leituras com 1 s entre elas (4 s de espera no
    # pior caso). Curto de propósito — o run roda a cada 5 min e a publicação
    # com container IN_PROGRESS funcionou ao vivo; o polling existe para o dia
    # em que ela NÃO funcionar, não para segurar o run.
    max_polls = 5
    poll_interval = 1.0

    def __init__(self, *args, sleep: Callable[[float], None] = time.sleep, **kwargs):
        super().__init__(*args, **kwargs)
        # Injetável para o teste não dormir de verdade.
        self.sleep = sleep
        # O que só se descobre PUBLICANDO (hoje: polling cego). O pipeline
        # drena esta lista depois de cada publish e manda pelo `warn` — é o
        # caminho que os avisos de montagem já tinham, e que faltava aqui.
        self.warnings: list[str] = []

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
        if not leitura.leu and AVISO_POLLING_CEGO not in self.warnings:
            self.warnings.append(AVISO_POLLING_CEGO)

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

    def _sobre_o_container(self, leitura: LeituraDoContainer) -> str:
        """O que dizer do container quando o publish falha — e só o que foi
        observado. Container `FINISHED`: nada (a causa está na resposta da
        Meta)."""
        if not leitura.leu:
            ultima = f"; última resposta: {leitura.status}" if leitura.status else ""
            return (f"não consegui ler o status do container em "
                    f"{self.max_polls} tentativas{ultima}")
        if leitura.status_code != "FINISHED":
            return f"container ainda {leitura.status_code} após {self.max_polls} tentativas"
        return ""

    def _aguarda_container(self, creation_id: str) -> LeituraDoContainer:
        """Lê `status_code`/`status` do container até `max_polls` vezes.

        `FINISHED` para na hora; `ERROR`/`EXPIRED` também (não há o que
        esperar). Esgotadas as tentativas ainda em `IN_PROGRESS`, devolve isso
        — e o `publish` tenta publicar assim mesmo, que foi o que funcionou ao
        vivo. Falha de rede aqui vale como "não sei": o container existe, e não
        é motivo para condenar o story.

        O que NÃO se faz mais: inventar `IN_PROGRESS` quando nenhuma leitura
        trouxe `status_code` (rede caída, ou resposta 200 sem o campo). Isso
        vira `leu=False`, e quem lê o erro fica sabendo a diferença.
        """
        status_code, status, leu = "", "", False
        for tentativa in range(self.max_polls):
            resp = self._graph_get(f"{self.graph}/{creation_id}", {
                "fields": "status_code,status",
                "access_token": self.access_token,
            })
            if isinstance(resp, dict):
                lido = str(resp.get("status_code") or "")
                if lido:
                    status_code, leu = lido, True
                status = str(resp.get("status") or "") or graph_error(resp)
            if status_code == "FINISHED" or status_code in STATUS_TERMINAIS:
                return LeituraDoContainer(status_code, status, leu)
            if tentativa < self.max_polls - 1:
                self.sleep(self.poll_interval)
        return LeituraDoContainer(status_code, status, leu)

"""Canal `instagram_reel` (fase 5T): o Reel, publicado pela Meta Graph API.

**Por que este canal existe.** Medido na conta real em 2026-08-29 pela Graph
API: 2 seguidores, 5 posts (3 imagem, 2 carrossel), **0 do tipo REEL**, alcance
de **1 conta** em 7 dias, 0 interações. Conta nova tem alcance baixo — isso é
esperado; alcance 1 com cinco posts publicados, não. O feed serve
principalmente quem JÁ segue; o Reel é o mecanismo que a Meta usa para entregar
a quem NÃO segue. Sem ele o sistema publica para 2 pessoas por construção. A
pesquisa do projeto tinha chegado ao mesmo lugar por outro caminho
(`docs/feed.md`): o Reel tem o maior share rate medido (0,10%) e é "o motor de
AQUISIÇÃO, que não existe: hoje a conta só tem o motor de retenção".

**O que muda em relação ao `instagram_story`**, que é o molde deste canal (os
três passos — criar container, esperar `status_code`, publicar — foram medidos
ao vivo em 2026-08-27, ver `docs/runbooks/meta-setup.md`):

1. `media_type=REELS` e **`video_url`** no lugar de `image_url`;
2. **`caption`** — o Reel aceita legenda, o story não. E ela importa: os posts
   do Instagram são indexados pelo Google desde 10/07/2025, e a legenda é a
   página de busca da peça;
3. **`share_to_feed=true`** — o mesmo Reel aparece também na grade do perfil.
   Uma unidade de cota, duas superfícies;
4. **espera mais**. O container de vídeo não é um JPEG: a Meta TRANSCODIFICA o
   arquivo, e publicar antes do `FINISHED` é o que falha. Onde o story lê 5
   vezes com 1 s, este lê 15 com 2 s;
5. **a cota é PERGUNTADA**, não chutada. A documentação da Meta traz 100 e 50
   na mesma página e a janela é MÓVEL — libera 24 h depois de cada publicação,
   não à meia-noite. A fonte da verdade é
   `GET /{ig_user_id}/content_publishing_limit`, a mesma rota que o `doctor` já
   consulta, e ela é a PRIMEIRA chamada do publish: com a cota estourada não há
   por que pagar os ~3,5 s de ffmpeg e um upload de vídeo.

**O canal nasce DESLIGADO no `config.yaml`.** Quem o liga é o dono, depois de
ver a peça — e é para isso que o `afiliado run --dry-run` grava o `.mp4` em
`.claude/previews/`.

E ele depende de um extra OPCIONAL (`pip install -e .[reel]`, ou um ffmpeg no
PATH): sem ffmpeg o canal não sobe, o run avisa uma vez e o resto do pipeline
segue inteiro — o molde é o `playwright` da fase 5P.
"""

from afiliado import creative, pricing, video
from afiliado.channels.base import PublishResult
from afiliado.channels.instagram_common import (STATUS_TERMINAIS, InstagramBase,
                                                graph_error)
from afiliado.channels.instagram_feed import bloco_indexavel, sanitiza_titulo
from afiliado.errors import SourceError
from afiliado.models import Post

__all__ = ["COTA_ESGOTADA", "VIDEO_GRANDE_DEMAIS", "InstagramReelChannel"]

COTA_ESGOTADA = ("cota de publicação da Meta esgotada: {usadas} de {total} nas "
                 "últimas {horas} h (a janela é móvel — ela libera 24 h depois de "
                 "cada publicação, não à meia-noite)")
VIDEO_GRANDE_DEMAIS = ("o Reel gerado tem {mb:.1f} MB e o bot do Telegram, que é a "
                       "hospedagem, só baixa {limite:.0f} MB — o defeito é do "
                       "gerador, não da hospedagem")


class InstagramReelChannel(InstagramBase):
    name = "instagram_reel"
    max_per_run = 1
    host_caption = "hospedagem temporária (Reel IG)"
    # Vídeo transcodifica: 15 leituras com 2 s entre elas (28 s de espera no
    # pior caso, contra os 4 s do story). É muito para um run de 5 min e é
    # pouco para a Meta; o meio-termo é publicar assim mesmo no fim, como o
    # story faz — e aí o erro da Meta diz que o container não terminou, que é
    # uma informação, não um mistério.
    max_polls = 15
    poll_interval = 2.0

    @classmethod
    def desarme(cls) -> str:
        """Motivo pelo qual este canal NÃO pode ser montado hoje, ou "".

        Lido pelo `cli._monta_instagram`: um canal que não tem como gerar a
        peça não sobe, e o dia diz por quê em vez de acumular falhas.
        """
        return "" if video.tem_ffmpeg() else video.AVISO_SEM_FFMPEG

    def publish(self, post: Post) -> PublishResult:
        # 1. A cota, antes de qualquer coisa cara. Uma cota que não respondeu
        #    não é uma cota estourada: forma estranha ou rede caída seguem em
        #    frente, senão uma mudança de formato da Meta calaria o canal.
        usadas, total, horas = self._cota()
        if usadas is not None and total is not None and usadas >= total:
            return PublishResult(False, error=COTA_ESGOTADA.format(
                usadas=usadas, total=total, horas=horas))

        # 2. A peça. Recebe o veredito do post (modo + selo) — não recalcula
        #    nada, é o que faz arte, texto do Telegram e legenda concordarem.
        try:
            mp4 = creative.render_reel(post.offer, post.copy, post.verdict,
                                       client=self.client, handle=self.brand_handle,
                                       brand_name=self.brand_name)
        except video.SemFFmpeg as exc:
            return PublishResult(False, error=f"sem como gerar o Reel: {exc}")
        except SourceError as exc:
            return PublishResult(False, error=f"falha ao gerar o Reel: {exc}")

        if len(mp4) > video.LIMITE_TELEGRAM_BYTES:
            return PublishResult(False, error=VIDEO_GRANDE_DEMAIS.format(
                mb=len(mp4) / 1024 / 1024,
                limite=video.LIMITE_TELEGRAM_BYTES / 1024 / 1024))

        video_url = self._host_video(mp4)
        if video_url is None:
            return PublishResult(False, error="falha ao hospedar o Reel temporariamente")

        # 3. Container, espera, publicação — os três passos do story.
        media_resp = self._graph_post(f"{self.graph}/{self.ig_user_id}/media", {
            "video_url": video_url,
            "media_type": "REELS",
            "caption": self.legenda(post),
            "share_to_feed": "true",
            "access_token": self.access_token,
        })
        creation_id = media_resp.get("id") if isinstance(media_resp, dict) else None
        if not creation_id:
            return PublishResult(False, error=graph_error(media_resp))

        leitura = self._aguarda_container(str(creation_id))
        if leitura.status_code in STATUS_TERMINAIS:
            # O container morreu: publicar seria uma segunda chamada inútil
            # contra a cota compartilhada com o feed e o story.
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
                erro = f"{erro} ({detalhe})"
            return PublishResult(False, error=erro)

        return PublishResult(True, str(media_id))

    def legenda(self, post: Post) -> str:
        """A legenda do Reel — CURTA em cima, indexável embaixo.

        Ela não é a legenda do feed: o Reel mostra as duas primeiras linhas
        sobre o vídeo e esconde o resto, então o que precisa ser lido de
        relance vem primeiro (o gancho, o produto, o preço, o selo) e o bloco
        que o Google lê fecha o texto. Como no feed, nada aqui pede curtida,
        comentário ou compartilhamento — a Meta rebaixa quem pede, e o que
        constrói reconhecimento é a frase-assinatura repetida em toda peça.
        """
        offer = post.offer
        titulo = sanitiza_titulo(offer.title)
        linha_preco, prova_social = pricing.price_line(offer, post.verdict)
        bloco_preco = "\n".join(p for p in (linha_preco, prova_social,
                                            post.verdict.seal) if p)
        return (
            f"{post.copy.headline}\n\n"
            f"{titulo}\n"
            f"{bloco_preco}\n\n"
            "🔗 Link na bio e no canal do Telegram\n\n"
            f"{bloco_indexavel(titulo, offer, post.verdict)}"
        )

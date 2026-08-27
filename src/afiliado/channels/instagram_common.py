"""Base compartilhada pelos canais do Instagram (fase 5E).

Nasceu de `instagram_feed`, quando o story deixou de ser gesto manual e virou
um segundo canal automático (`instagram_story`): hosts da Graph API, leitura de
erro, conversão da arte para JPEG, hospedagem temporária da arte e as chamadas
HTTP que NUNCA levantam são exatamente as mesmas nos dois. Tudo aqui é público
de propósito — os canais importam desta base, não um do outro, e nenhum nome
privado atravessa módulo.

O que muda entre feed e story é só o que cada canal manda ao endpoint `/media`
(o feed manda `caption`; o story manda `media_type=STORIES` e nenhuma legenda —
a API não aceita) e o que ele faz entre criar e publicar o container.
"""

import io

import httpx
from PIL import Image

from afiliado.channels.telegram import get_file_url, send_photo_bytes

GRAPH_HOSTS = {
    # Conta business vinculada a Página do Facebook; escopos instagram_basic + instagram_content_publish.
    "facebook_login": "https://graph.facebook.com/v21.0",
    # "API do Instagram com Login do Instagram": token gerado no painel, sem Página;
    # escopos instagram_business_basic + instagram_business_content_publish.
    "instagram_login": "https://graph.instagram.com/v21.0",
}
GRAPH = GRAPH_HOSTS["facebook_login"]


def graph_error(resp) -> str:
    if isinstance(resp, dict):
        err = resp.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
    return f"resposta inesperada da Graph API: {resp!r}"


def to_jpeg(png_bytes: bytes, quality: int = 90) -> bytes:
    """A API de publicação do Instagram aceita apenas JPEG; converte a arte PNG."""
    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    out = io.BytesIO()
    img.save(out, "JPEG", quality=quality, optimize=True)
    return out.getvalue()


class InstagramBase:
    """Construtor, hospedagem da arte e chamadas à Graph API dos dois canais.

    Não é um canal: subclasse precisa declarar `name` e `publish`.
    """

    # Legenda da mensagem de hospedagem no chat de operações (só para o dono
    # saber por que aquela arte apareceu lá); cada canal diz a sua.
    host_caption = "hospedagem temporária (IG)"

    def __init__(self, ig_user_id: str, access_token: str, bot_token: str, ops_chat_id: str,
                 client: httpx.Client | None = None, brand_handle: str | None = None,
                 brand_name: str = "Fiscal da Promo", api: str = "facebook_login",
                 art_host_bot_token: str = ""):
        # .strip() mata o footgun clássico de segredo colado com espaço/quebra
        # de linha nas pontas (env var, clipboard); não cobre caractere de
        # controle NO MEIO da string — para isso, ver o try/except amplo em
        # _graph_post (httpx.InvalidURL não é subclasse de httpx.HTTPError).
        self.ig_user_id = ig_user_id.strip()
        self.access_token = access_token.strip()
        self.bot_token = bot_token.strip()
        # Bot que hospeda a arte (A5): o do canal só é usado como último
        # recurso, e nesse caso o cli avisa uma vez por dia.
        self.art_host_bot_token = (art_host_bot_token or "").strip() or self.bot_token
        self.ops_chat_id = ops_chat_id.strip()
        self.client = client or httpx.Client(timeout=30)
        self.brand_handle = brand_handle
        self.brand_name = brand_name
        self.graph = GRAPH_HOSTS.get(api, GRAPH_HOSTS["facebook_login"])

    def _host_art(self, art: bytes) -> str | None:
        """URL pública temporária da arte. O token que aparece nela é o do bot
        de hospedagem — nunca o do bot administrador do canal público, quando há
        um secundário configurado (A5)."""
        token = self.art_host_bot_token
        photo_result = send_photo_bytes(token, self.ops_chat_id, art,
                                        caption=self.host_caption,
                                        client=self.client, filename="art.jpg", mime="image/jpeg")
        if not photo_result.get("ok"):
            return None
        photos = (photo_result.get("result") or {}).get("photo") or []
        if not photos:
            return None
        file_id = photos[-1].get("file_id")
        if not file_id:
            return None
        return get_file_url(token, file_id, client=self.client)

    def _graph_post(self, url: str, payload: dict) -> dict:
        return self._graph_call("post", url, data=payload)

    def _graph_get(self, url: str, params: dict) -> dict:
        """GET com a mesma promessa do POST: devolve dict, nunca levanta.
        Usado pelo polling do container do story (fase 5E)."""
        return self._graph_call("get", url, params=params)

    def _graph_call(self, metodo: str, url: str, **kwargs) -> dict:
        try:
            r = getattr(self.client, metodo)(url, **kwargs)
            return r.json()
        except ValueError:
            return {"error": {"message": "resposta não-JSON"}}
        except Exception as exc:
            # Nunca levanta: além de httpx.HTTPError (rede), cobre
            # httpx.InvalidURL — que NÃO é subclasse de HTTPError e escaparia
            # se ig_user_id/access_token vierem com caractere de controle
            # embutido (ex.: "\n" no meio de uma env var mal colada).
            return {"error": {"message": f"rede: {exc}"}}

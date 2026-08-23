import httpx

from afiliado.errors import ValidationError
from afiliado.models import CopyParts, Offer, Post

MAX_HEADLINE = 60
MAX_DESCRIPTION = 120
MAX_CTA = 40
MIN_IMAGE_BYTES = 5000
_UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}


def _client(client: httpx.Client | None) -> httpx.Client:
    return client or httpx.Client(timeout=20, follow_redirects=True, headers=_UA)


def check_link(url: str, cfg: dict, client: httpx.Client | None = None) -> None:
    allowed = tuple(cfg["validation"]["allowed_domains"])
    try:
        r = _client(client).get(url)
    except httpx.HTTPError as exc:
        raise ValidationError(f"link inacessível: {exc}") from exc
    host = r.url.host or ""
    if not host.endswith(allowed):
        raise ValidationError(f"link resolve para domínio inesperado: {host}")
    if r.status_code >= 500:
        raise ValidationError(f"link retornou status {r.status_code}")


def check_price(offer: Offer, cfg: dict) -> None:
    sel = cfg["selection"]
    if offer.price_current_cents >= offer.price_original_cents:
        raise ValidationError("sem desconto real (atual >= original)")
    if offer.discount_pct < sel["min_discount_pct"]:
        raise ValidationError(f"desconto {offer.discount_pct}% abaixo do mínimo")
    preco_brl = offer.price_current_cents / 100
    if not sel["price_min_brl"] <= preco_brl <= sel["price_max_brl"]:
        raise ValidationError(f"preço R${preco_brl:.2f} fora da faixa")


def check_image(url: str, client: httpx.Client | None = None) -> None:
    try:
        r = _client(client).get(url)
    except httpx.HTTPError as exc:
        raise ValidationError(f"imagem inacessível: {exc}") from exc
    ctype = r.headers.get("content-type", "")
    if r.status_code != 200 or not ctype.startswith("image/"):
        raise ValidationError(f"imagem inválida: status={r.status_code} type={ctype}")
    if len(r.content) < MIN_IMAGE_BYTES:
        raise ValidationError("imagem pequena demais (possivelmente quebrada)")


def check_copy(copy: CopyParts) -> None:
    campos = {"headline": (copy.headline, MAX_HEADLINE),
              "description": (copy.description, MAX_DESCRIPTION),
              "cta": (copy.cta, MAX_CTA)}
    for nome, (valor, limite) in campos.items():
        if not valor.strip():
            raise ValidationError(f"copy.{nome} vazio")
        if len(valor) > limite:
            raise ValidationError(f"copy.{nome} excede {limite} chars")
        if "http" in valor.lower():
            raise ValidationError(f"copy.{nome} contém URL")


def validate_post(post: Post, cfg: dict, client: httpx.Client | None = None) -> None:
    check_copy(post.copy)
    check_price(post.offer, cfg)
    check_link(post.affiliate_link, cfg, client=client)
    check_image(post.offer.image_url, client=client)

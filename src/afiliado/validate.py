from urllib.parse import urlsplit

import httpx

from afiliado.errors import ValidationError
from afiliado.models import CopyParts, Offer, Post, format_brl

MAX_HEADLINE = 60
MAX_DESCRIPTION = 120
MAX_CTA = 40
MIN_IMAGE_BYTES = 5000
_UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}


def _client(client: httpx.Client | None) -> httpx.Client:
    return client or httpx.Client(timeout=20, follow_redirects=True, headers=_UA)


def check_link(url: str, cfg: dict) -> None:
    """Portão de link OFFLINE (fase 5A, C6): `https`, host igual a um dos
    `validation.allowed_domains` ou subdomínio dele, sem espaço nem caractere
    de controle. NENHUMA requisição HTTP.

    Antes, este portão fazia GET no link curto de afiliado seguindo o redirect
    de rastreamento, com User-Agent de navegador — isto é, um clique de
    afiliado feito pelo próprio pipeline, do IP da VPS, segundos após o link
    ser gerado, em todo post e também no --dry-run: assinatura de tráfego
    inválido para os programas (risco de encerramento da conta) e
    contaminação do teste de atribuição do ML. A vitalidade da oferta já foi
    provada minutos antes (descoberta) ou segundos antes (`refresh_price`);
    o link vem do gerador oficial/painel. Não há cliente HTTP na assinatura
    de propósito: sem ele, ninguém reintroduz o GET por engano."""
    allowed = cfg["validation"]["allowed_domains"]
    if not url or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in url):
        raise ValidationError("link vazio ou com espaço/caractere de controle")
    # `https://evil.com\@meli.la/x`: urlsplit mantém a `\` no netloc e lê o
    # host depois do `@` (meli.la); o navegador trata `\` como `/` e vai para
    # evil.com. Barra invertida e qualquer userinfo são rejeitadas (5A, rev.).
    if "\\" in url:
        raise ValidationError(f"link com barra invertida: {url[:80]}")
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise ValidationError(f"link sem https: {url[:80]}")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise ValidationError(f"link com credenciais no host: {url[:80]}")
    host = (parts.hostname or "").lower()
    if not host:
        raise ValidationError(f"link sem host: {url[:80]}")
    if not any(host == d or host.endswith("." + d) for d in allowed):
        raise ValidationError(f"link em domínio inesperado: {host}")


def check_price(offer: Offer, cfg: dict) -> None:
    """Rede de segurança que roda DEPOIS do refresh_price: pega a oferta que
    encareceu entre a busca e a publicação. Não há mais portão de desconto —
    o desconto do vendedor é rótulo, não critério (ver afiliado.pricing)."""
    sel = cfg["selection"]
    if offer.price_ref_cents > 0 and (
            offer.price_current_cents > offer.price_ref_cents * float(sel["max_above_ref"])):
        raise ValidationError(
            f"preço {format_brl(offer.price_current_cents)} acima da referência "
            f"{format_brl(offer.price_ref_cents)}")
    if sel.get("require_price_ref") and offer.price_ref_cents <= 0:
        raise ValidationError("sem referência de preço conhecida")
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


def validate_post(post: Post, cfg: dict, client: httpx.Client | None = None,
                  skip_image: bool = False) -> None:
    """Portões na ordem barato → caro. `client` serve SÓ à imagem (o link é
    checado offline); `skip_image=True` (dry-run, A10) não toca a rede."""
    check_copy(post.copy)
    check_price(post.offer, cfg)
    check_link(post.affiliate_link, cfg)
    if not skip_image:
        check_image(post.offer.image_url, client=client)

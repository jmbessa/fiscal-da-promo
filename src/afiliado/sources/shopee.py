import hashlib
import json
import time
from decimal import Decimal, InvalidOperation

import httpx

from afiliado.errors import SourceError
from afiliado.models import Offer

GRAPHQL_URL = "https://open-api.affiliate.shopee.com.br/graphql"

PRODUCT_OFFER_QUERY = """
query productOfferV2($page: Int, $limit: Int, $sortType: Int, $listType: Int, $productCatId: Int) {
  productOfferV2(page: $page, limit: $limit, sortType: $sortType, listType: $listType, productCatId: $productCatId) {
    nodes {
      itemId productName price priceDiscountRate commissionRate sales
      imageUrl productLink offerLink productCatIds
      priceMin priceMax commission ratingStar periodEndTime
    }
  }
}
"""

GEN_LINK_MUTATION = """
mutation generateShortLink($url: String!) {
  generateShortLink(input: { originUrl: $url }) { shortLink }
}
"""


class ShopeeSource:
    name = "shopee"

    def __init__(self, app_id: str, app_secret: str, client: httpx.Client | None = None):
        self.app_id = app_id
        self.app_secret = app_secret
        self.client = client or httpx.Client(
            timeout=30, transport=httpx.HTTPTransport(retries=3))

    def _post(self, payload: dict) -> dict:
        body = json.dumps(payload, separators=(",", ":"))
        ts = str(int(time.time()))
        sig = hashlib.sha256(
            f"{self.app_id}{ts}{body}{self.app_secret}".encode()).hexdigest()
        headers = {
            "Authorization": f"SHA256 Credential={self.app_id}, Timestamp={ts}, Signature={sig}",
            "Content-Type": "application/json",
        }
        try:
            r = self.client.post(GRAPHQL_URL, content=body, headers=headers)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise SourceError(f"shopee API: {exc}") from exc
        try:
            data = r.json()
        except ValueError as exc:
            raise SourceError(f"shopee API: resposta não é JSON válido: {exc}") from exc
        if data.get("errors"):
            raise SourceError(f"shopee GraphQL: {data['errors']}")
        if "data" not in data:
            raise SourceError(f"shopee GraphQL: resposta sem campo 'data': {data}")
        return data["data"]

    def fetch_offers(self, cfg: dict) -> list[Offer]:
        sh = cfg["shopee"]
        offers: list[Offer] = []
        seen_ids: set[str] = set()
        # Sem productCatId, a API devolve majoritariamente uma única categoria
        # fora da nossa allowlist (medido contra a API real, ver Fase 1.9) —
        # por isso a busca sempre itera por categoria. category_ids vazia/
        # ausente cai em [None]: uma única busca sem productCatId, igual ao
        # comportamento anterior a esta fase.
        category_ids = sh.get("category_ids") or [None]
        for category_id in category_ids:
            for sort_type in sh["sort_types"]:
                for page in range(1, sh["pages"] + 1):
                    variables = {"page": page, "limit": sh["page_size"],
                                 "sortType": sort_type, "listType": sh["list_type"]}
                    if category_id is not None:
                        variables["productCatId"] = int(category_id)
                    data = self._post({
                        "query": PRODUCT_OFFER_QUERY,
                        "variables": variables,
                    })
                    nodes = (data.get("productOfferV2") or {}).get("nodes") or []
                    for node in nodes:
                        offer = _parse_node(node)
                        if offer and offer.item_id not in seen_ids:
                            seen_ids.add(offer.item_id)
                            offers.append(offer)
                    # Página incompleta = fim do estoque desta combinação; pedir
                    # a próxima só gastaria chamada (a cada 5 min isso soma).
                    if len(nodes) < sh["page_size"]:
                        break
        return offers

    def resolve_affiliate_link(self, offer: Offer) -> str:
        try:
            data = self._post({"query": GEN_LINK_MUTATION,
                               "variables": {"url": offer.product_url}})
            link = (data.get("generateShortLink") or {}).get("shortLink") or ""
            if link:
                return link
        except SourceError:
            pass
        if offer.offer_link:
            return offer.offer_link
        raise SourceError(f"sem link de afiliado para item {offer.item_id}")

    def refresh_price(self, offer: Offer) -> Offer:
        """A busca da Shopee já devolve preço ao vivo — nada a atualizar."""
        return offer


def _parse_node(node: dict) -> Offer | None:
    if "itemId" not in node:
        return None
    period_end = node.get("periodEndTime")
    if period_end is not None:
        try:
            fim = float(period_end)
        except (TypeError, ValueError):
            fim = 0.0
        # 0 = validade desconhecida (mesma convenção dos demais campos), não
        # "expirou em 1970"; só descarta quando há um fim real já passado.
        if fim > 0 and fim < time.time():
            return None
    try:
        price_cents = int(Decimal(str(node["price"])) * 100)
    except (KeyError, TypeError, InvalidOperation):
        return None
    rate = node.get("priceDiscountRate") or 0
    if 0 < rate < 90:
        original_cents = round(price_cents / (1 - rate / 100))
    else:
        original_cents = price_cents
    try:
        commission_pct = float(Decimal(str(node.get("commissionRate") or "0")) * 100)
    except InvalidOperation:
        commission_pct = 0.0
    cats = node.get("productCatIds") or []
    return Offer(
        source="shopee",
        item_id=str(node["itemId"]),
        title=str(node.get("productName") or "").strip(),
        price_original_cents=original_cents,
        price_current_cents=price_cents,
        commission_pct=commission_pct,
        image_url=str(node.get("imageUrl") or ""),
        product_url=str(node.get("productLink") or ""),
        offer_link=str(node.get("offerLink") or ""),
        category=str(cats[0]) if cats else "",
        sales=int(node.get("sales") or 0),
        rating=_parse_rating(node.get("ratingStar")),
        price_min_cents=_cents_or_zero(node.get("priceMin")),
        price_max_cents=_cents_or_zero(node.get("priceMax")),
        commission_brl=_commission_brl(node.get("commission")),
    )


def _cents_or_zero(raw) -> int:
    """priceMin/priceMax vêm como string decimal; ausentes, inválidos ou
    zero viram 0 (desconhecido)."""
    try:
        val = Decimal(str(raw))
    except (TypeError, InvalidOperation):
        return 0
    return int(val * 100) if val > 0 else 0


def _commission_brl(raw) -> float:
    """commission (R$ absoluto) vem como string decimal; ausente/inválido vira 0.0."""
    try:
        return float(Decimal(str(raw if raw is not None else "0")))
    except InvalidOperation:
        return 0.0


def _parse_rating(raw) -> float:
    """ratingStar vem como string/número; qualquer coisa inválida vira 0.0 (desconhecida)."""
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return val if 0.0 < val <= 5.0 else 0.0

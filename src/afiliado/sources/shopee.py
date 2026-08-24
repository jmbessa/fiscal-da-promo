import hashlib
import json
import time
from decimal import Decimal, InvalidOperation

import httpx

from afiliado.errors import SourceError
from afiliado.models import Offer

GRAPHQL_URL = "https://open-api.affiliate.shopee.com.br/graphql"

PRODUCT_OFFER_QUERY = """
query productOfferV2($page: Int, $limit: Int, $sortType: Int, $listType: Int) {
  productOfferV2(page: $page, limit: $limit, sortType: $sortType, listType: $listType) {
    nodes {
      itemId productName price priceDiscountRate commissionRate sales
      imageUrl productLink offerLink productCatIds
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
        for sort_type in sh["sort_types"]:
            for page in range(1, sh["pages"] + 1):
                data = self._post({
                    "query": PRODUCT_OFFER_QUERY,
                    "variables": {"page": page, "limit": sh["page_size"],
                                  "sortType": sort_type, "listType": sh["list_type"]},
                })
                nodes = (data.get("productOfferV2") or {}).get("nodes") or []
                for node in nodes:
                    offer = _parse_node(node)
                    if offer and offer.item_id not in seen_ids:
                        seen_ids.add(offer.item_id)
                        offers.append(offer)
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


def _parse_node(node: dict) -> Offer | None:
    if "itemId" not in node:
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
    )

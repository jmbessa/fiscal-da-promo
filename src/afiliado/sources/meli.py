import json
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path

import httpx

from afiliado.errors import SourceError
from afiliado.models import Offer

API_HOST = "https://api.mercadolibre.com"
TOKEN_URL = f"{API_HOST}/oauth/token"
SEARCH_URL = f"{API_HOST}/sites/MLB/search"

TOKEN_EXPIRY_MARGIN_S = 60
THUMBNAIL_SUFFIXES = ("-I.jpg", "-O.jpg")


class MeliSource:
    name = "meli"

    def __init__(self, client_id: str, client_secret: str,
                 refresh_token: str = "", token_path: str | Path = "data/meli_token.json",
                 links_path: str | Path = "data/meli_links.json",
                 client: httpx.Client | None = None):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.token_path = Path(token_path)
        self.links_path = Path(links_path)
        self.client = client or httpx.Client(
            timeout=30, transport=httpx.HTTPTransport(retries=3))
        self._access_token: str | None = None
        self._expires_at: float = 0.0
        self._links_pool: dict[str, str] | None = None

    # -- autenticação ---------------------------------------------------

    def ensure_token(self) -> str:
        """Devolve um access_token válido, reautenticando quando o cache
        expirou (margem de 60s). Público para o `doctor` conseguir testar."""
        now = time.time()
        if self._access_token and now < self._expires_at - TOKEN_EXPIRY_MARGIN_S:
            return self._access_token
        return self._authenticate()

    def _authenticate(self) -> str:
        data = self._post_token({
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        })
        if data and "access_token" in data:
            self._cache_token(data["access_token"], data.get("expires_in"))
            return self._access_token

        refresh_token = self._load_refresh_token()
        if not refresh_token:
            raise SourceError(
                "meli: autenticação falhou (client_credentials recusado e "
                "nenhum refresh_token disponível)")
        data = self._post_token({
            "grant_type": "refresh_token",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": refresh_token,
        })
        if not data or "access_token" not in data:
            raise SourceError(
                "meli: autenticação falhou (client_credentials recusado e "
                "refresh_token inválido/expirado)")
        # O ML rotaciona o refresh_token a cada uso: persiste ANTES de
        # qualquer outra chamada (ex.: a busca), senão a rotação se perde.
        new_refresh = data.get("refresh_token") or refresh_token
        self._persist_token(new_refresh, data["access_token"], data.get("expires_in"))
        self._cache_token(data["access_token"], data.get("expires_in"))
        return self._access_token

    def _post_token(self, payload: dict) -> dict | None:
        try:
            r = self.client.post(TOKEN_URL, json=payload)
        except httpx.HTTPError:
            return None
        if r.status_code != 200:
            return None
        try:
            return r.json()
        except ValueError:
            return None

    def _cache_token(self, access_token: str, expires_in) -> None:
        self._access_token = access_token
        self._expires_at = time.time() + float(expires_in or 0)

    def _load_refresh_token(self) -> str:
        """Arquivo (`token_path`) é a fonte preferencial; a env var (passada
        no construtor) só é usada se o arquivo não existir/não tiver o campo."""
        if self.token_path.is_file():
            try:
                data = json.loads(self.token_path.read_text(encoding="utf-8"))
                token = data.get("refresh_token")
                if token:
                    return str(token)
            except (ValueError, OSError):
                pass
        return self.refresh_token

    def _persist_token(self, refresh_token: str, access_token: str, expires_in) -> None:
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "refresh_token": refresh_token,
            "access_token": access_token,
            "expires_at": time.time() + float(expires_in or 0),
        }
        self.token_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # -- descoberta -------------------------------------------------------

    def fetch_offers(self, cfg: dict) -> list[Offer]:
        me = cfg["meli"]
        token = self.ensure_token()
        min_sold = me.get("min_sold", 0)
        headers = {"Authorization": f"Bearer {token}"}
        offers: list[Offer] = []
        seen_ids: set[str] = set()
        for category_id in me["category_ids"]:
            try:
                r = self.client.get(
                    SEARCH_URL,
                    params={"category": category_id, "sort": "relevance",
                            "limit": me["per_category"]},
                    headers=headers,
                )
                r.raise_for_status()
            except httpx.HTTPError as exc:
                raise SourceError(f"meli API: {exc}") from exc
            try:
                data = r.json()
            except ValueError as exc:
                raise SourceError(f"meli API: resposta não é JSON válido: {exc}") from exc
            for result in data.get("results") or []:
                offer = _parse_result(result, min_sold)
                if offer and offer.item_id not in seen_ids:
                    seen_ids.add(offer.item_id)
                    offers.append(offer)
        return offers

    # -- link de afiliado (pool pré-gerado) --------------------------------

    def resolve_affiliate_link(self, offer: Offer) -> str:
        pool = self._load_links_pool()
        link = pool.get(offer.item_id)
        if not link:
            raise SourceError(f"sem link de afiliado no pool para {offer.item_id}")
        return link

    def _load_links_pool(self) -> dict[str, str]:
        if self._links_pool is None:
            pool: dict[str, str] = {}
            if self.links_path.is_file():
                try:
                    data = json.loads(self.links_path.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        pool = {str(k): str(v) for k, v in data.items()}
                except (ValueError, OSError):
                    pool = {}
            self._links_pool = pool
        return self._links_pool


def _parse_result(r: dict, min_sold: int) -> Offer | None:
    if "id" not in r or not r.get("title") or r.get("price") is None:
        return None
    try:
        price_cents = int(Decimal(str(r["price"])) * 100)
    except InvalidOperation:
        return None
    sold = int(r.get("sold_quantity") or 0)
    if sold < min_sold:
        return None
    original_cents = price_cents
    original = r.get("original_price")
    if original is not None:
        try:
            candidate = int(Decimal(str(original)) * 100)
        except InvalidOperation:
            candidate = price_cents
        if candidate > price_cents:
            original_cents = candidate
    return Offer(
        source="meli",
        item_id=str(r["id"]),
        title=str(r["title"]).strip(),
        price_original_cents=original_cents,
        price_current_cents=price_cents,
        commission_pct=0.0,
        image_url=_larger_thumbnail(str(r.get("thumbnail") or "")),
        product_url=str(r.get("permalink") or ""),
        category=str(r.get("category_id") or ""),
        sales=sold,
        rating=0.0,
    )


def _larger_thumbnail(thumbnail: str) -> str:
    """Troca o sufixo -I.jpg/-O.jpg por -W.jpg (miniatura maior) quando
    possível; sem esses sufixos, devolve o thumbnail cru."""
    for suffix in THUMBNAIL_SUFFIXES:
        if thumbnail.endswith(suffix):
            return thumbnail[: -len(suffix)] + "-W.jpg"
    return thumbnail

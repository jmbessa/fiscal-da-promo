import dataclasses
import json
import os
import time
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

import httpx

from afiliado.errors import SourceError
from afiliado.models import Offer

API_HOST = "https://api.mercadolibre.com"
TOKEN_URL = f"{API_HOST}/oauth/token"

TOKEN_EXPIRY_MARGIN_S = 60

# Fase 3B: `/sites/MLB/search` e `/items/{id}` devolvem 403 na API real —
# a descoberta agora lê um pool curado externamente (ver Mudança 1/2 do
# spec). `/products/{id}` e `/products/{id}/items` seguem liberados e são
# usados só em `refresh_price`, imediatamente antes de publicar.
DEFAULT_OFFERS_PATH = "data/meli_offers.json"
DEFAULT_VALID_DAYS = 30


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
        # Motivo de fetch_offers ter devolvido [] (pool ausente/inválido/vencido);
        # None quando a última leitura teve sucesso. Só informativo (doctor/logs).
        self.pool_warning: str | None = None

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
        # json=payload é intencional, não "esquecido": testado contra o
        # endpoint real com credenciais inválidas, JSON e
        # x-www-form-urlencoded devolvem o mesmo erro (invalid_client) — as
        # duas formas são aceitas. Não trocar por form-encoded.
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
        """Grava em arquivo temporário no mesmo diretório e troca com
        `os.replace` (atômico no mesmo sistema de arquivos): uma interrupção
        no meio nunca deixa `token_path` truncado/corrompido — o pior caso é
        o arquivo temporário sobrar, nunca perder a rotação do refresh_token
        já persistida. Qualquer OSError (permissão, disco cheio) vira
        SourceError em vez de escapar cru — perder a rotação em silêncio
        quebraria a autenticação na próxima execução."""
        payload = {
            "refresh_token": refresh_token,
            "access_token": access_token,
            "expires_at": time.time() + float(expires_in or 0),
        }
        tmp_path = self.token_path.with_name(self.token_path.name + ".tmp")
        try:
            self.token_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp_path, self.token_path)
        except OSError as exc:
            tmp_path.unlink(missing_ok=True)
            raise SourceError(f"meli: falha ao persistir o token rotacionado: {exc}") from exc

    # -- descoberta (pool curado) ------------------------------------------

    def fetch_offers(self, cfg: dict) -> list[Offer]:
        """Lê o pool curado (`cfg["meli"]["offers_path"]`, padrão
        `data/meli_offers.json`) — NENHUMA chamada de rede aqui. Arquivo
        ausente/inválido ou vencido (`generated_at` + `valid_days` no
        passado) devolve lista vazia sem levantar exceção: o pipeline segue
        só com as demais fontes; `self.pool_warning` guarda o motivo."""
        me = cfg.get("meli") or {}
        offers_path = Path(me.get("offers_path") or DEFAULT_OFFERS_PATH)
        commission_pct = float(me.get("commission_pct") or 0.0)
        self.pool_warning = None

        try:
            raw = json.loads(offers_path.read_text(encoding="utf-8"))
            generated_at = date.fromisoformat(str(raw["generated_at"]))
            valid_days = int(raw.get("valid_days", DEFAULT_VALID_DAYS))
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            self.pool_warning = f"pool ausente ou inválido ({offers_path})"
            return []

        if (date.today() - generated_at).days > valid_days:
            self.pool_warning = (
                f"pool vencido: gerado em {generated_at.isoformat()}, "
                f"validade {valid_days}d")
            return []

        offers: list[Offer] = []
        seen_ids: set[str] = set()
        sem_piso = 0
        for item in raw.get("offers") or []:
            if not isinstance(item, dict):
                continue
            historic = item.get("price_historic_min_cents")
            # Entrada sem mínima histórica não entra: antes ela era aceita e
            # desligava o piso em silêncio (achado da revisão).
            if (not isinstance(historic, int) or isinstance(historic, bool)
                    or historic <= 0):
                sem_piso += 1
                continue
            offer = _parse_pool_offer(item, commission_pct, int(historic))
            if offer is None or offer.item_id in seen_ids:
                continue
            seen_ids.add(offer.item_id)
            offers.append(offer)
        if sem_piso:
            self.pool_warning = (
                f"{sem_piso} entrada(s) do pool ignorada(s): "
                "price_historic_min_cents ausente ou não inteiro > 0")
        return offers

    # -- preço ao vivo (imediatamente antes de publicar) -------------------

    def refresh_price(self, offer: Offer) -> Offer:
        """Busca o preço ao vivo em `/products/{item_id}/items` (menor preço
        entre variações `condition == "new"`) e devolve um `Offer` novo
        (dataclass frozen) com `price_current_cents` atualizado. Levanta
        `SourceError` só quando não há preço ao vivo nenhum.

        Fase 4: o ML não tem mais teto de preço próprio — quem decide
        publicabilidade é `selection.max_above_ref` + `validate.check_price`,
        igual para as duas lojas. A mínima histórica do pool viaja na própria
        oferta (`price_floor_cents`, carimbado em `fetch_offers`) e só alimenta
        o selo de menor preço."""
        token = self.ensure_token()
        headers = {"Authorization": f"Bearer {token}"}
        url = f"{API_HOST}/products/{offer.item_id}/items"
        try:
            r = self.client.get(url, headers=headers)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise SourceError(f"meli API: {exc}") from exc
        try:
            data = r.json()
        except ValueError as exc:
            raise SourceError(f"meli API: resposta não é JSON válido: {exc}") from exc

        live_cents = _min_live_price_cents(data.get("results") or [])
        if live_cents is None:
            raise SourceError(f"meli: sem preço ao vivo disponível para {offer.item_id}")

        return dataclasses.replace(offer, price_current_cents=live_cents)

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


def _parse_pool_offer(item: dict, commission_pct: float,
                      historic_min_cents: int) -> Offer | None:
    product_id = str(item.get("product_id") or "").strip()
    title = str(item.get("title") or "").strip()
    if not product_id or not title:
        return None
    try:
        price_ref_cents = int(item["price_ref_cents"])
    except (KeyError, TypeError, ValueError):
        return None
    if price_ref_cents <= 0:
        return None
    return Offer(
        source="meli",
        item_id=product_id,
        title=title,
        price_original_cents=price_ref_cents,
        price_current_cents=price_ref_cents,
        commission_pct=commission_pct,
        image_url=str(item.get("image_url") or ""),
        product_url=f"https://www.mercadolivre.com.br/p/{product_id}",
        category=str(item.get("category") or ""),
        sales=int(item.get("sales") or 0),
        rating=float(item.get("rating") or 0.0),
        price_ref_cents=price_ref_cents,
        price_floor_cents=historic_min_cents,
    )


def _min_live_price_cents(results: list) -> int | None:
    """Menor `price` entre os `results` de `/products/{id}/items` com
    `condition == "new"` e `price` presente. `original_price` é ignorado de
    propósito — vem quase sempre `null` na API real, não dá para calcular
    desconto por aqui (ver Mudança 3 do spec)."""
    best: Decimal | None = None
    for result in results:
        if not isinstance(result, dict) or result.get("condition") != "new":
            continue
        price = result.get("price")
        if price is None:
            continue
        try:
            candidate = Decimal(str(price))
        except InvalidOperation:
            continue
        if best is None or candidate < best:
            best = candidate
    if best is None:
        return None
    return int(best * 100)

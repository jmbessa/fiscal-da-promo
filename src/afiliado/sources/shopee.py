import dataclasses
import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

import httpx

from afiliado.errors import SourceError
from afiliado.models import Offer

GRAPHQL_URL = "https://open-api.affiliate.shopee.com.br/graphql"

# `keyword` e `pageInfo` existem no schema real (introspecção de 2026-08-26,
# `descoberta/introspection.json`): sem `pageInfo { hasNextPage }` o cliente
# só descobria o fim da janela gastando uma chamada na página vazia.
NODE_FIELDS = """
      itemId productName price priceDiscountRate commissionRate sales
      imageUrl productLink offerLink productCatIds
      priceMin priceMax commission ratingStar periodEndTime
"""

PRODUCT_OFFER_QUERY = """
query productOfferV2($page: Int, $limit: Int, $sortType: Int, $listType: Int, $productCatId: Int, $keyword: String) {
  productOfferV2(page: $page, limit: $limit, sortType: $sortType, listType: $listType, productCatId: $productCatId, keyword: $keyword) {
    nodes {%s}
    pageInfo { hasNextPage }
  }
}
""" % NODE_FIELDS

# Preço vivo por item (M1): `itemId` é `Int64` no schema real.
ITEM_OFFER_QUERY = """
query productOfferV2($itemId: Int64) {
  productOfferV2(itemId: $itemId) {
    nodes {%s}
  }
}
""" % NODE_FIELDS

GEN_LINK_MUTATION = """
mutation generateShortLink($url: String!) {
  generateShortLink(input: { originUrl: $url }) { shortLink }
}
"""


# Fase 5A (A4): repetições com backoff em 429, 5xx e erro de conexão/timeout
# — 1 tentativa + uma repetição por atraso. `HTTPTransport(retries=3)` só
# repetia erro de CONEXÃO, sem espera, nunca 5xx/429.
RETRY_DELAYS_S = (0.5, 1.5, 4.0)

# Padrões da varredura rotativa (fase 5C, M1), todos medidos em 2026-08-26
# (147 chamadas reais; ver docs/superpowers/reviews/2026-08-26-descoberta-shopee.md).
DEFAULT_CALLS_PER_RUN = 8          # 5 raízes + 2 subcategorias + 1 keyword
DEFAULT_PAGES = 40                 # a janela por (categoria, sortType): 40 × 50 = 2.000
DEFAULT_SUBCATEGORIES_PER_RUN = 2
DEFAULT_KEYWORDS_PER_RUN = 1
# p1 de subcategoria ≈ topo da raiz (calls 123–124: 0–1 itens inéditos em 50);
# a rotação de subcategoria começa na p2.
DEFAULT_SUBCATEGORY_FIRST_PAGE = 2
DEFAULT_KEYWORD_PAGES = 2          # p1 rende 19 inéditos/chamada, p2 ~21–42 (calls 132–133)


@dataclass
class DiscoveryStats:
    """O que a fatia deste run custou e rendeu — vai ao resumo de ops."""
    calls: int = 0
    nodes: int = 0
    eligible: int = 0


@dataclass(frozen=True)
class _Fatia:
    """Uma chamada da varredura: uma listagem numa página."""
    category_id: str | None
    page: int
    sort_type: int
    keyword: str = ""
    cursor_key: str = ""      # chave do cursor de página (vazio = sem cursor)
    first_page: int = 1       # para onde o cursor volta ao fim da janela


class _CursorEmMemoria:
    """Cursor sem StateDB (doctor, testes): o run vê a rotação, mas ela não
    sobrevive ao processo."""

    def __init__(self):
        self._valores: dict[str, str] = {}

    def get_cursor(self, key: str, default: str = "") -> str:
        return self._valores.get(key, default)

    def set_cursor(self, key: str, value: str) -> None:
        self._valores[key] = str(value)


class ShopeeSource:
    name = "shopee"

    def __init__(self, app_id: str, app_secret: str, client: httpx.Client | None = None,
                 sleep: Callable[[float], None] = time.sleep, db=None):
        self.app_id = app_id
        self.app_secret = app_secret
        self.client = client or httpx.Client(timeout=30)
        self.sleep = sleep
        # Qualquer objeto com get_cursor/set_cursor serve (o StateDB do run).
        self.cursor = db if db is not None else _CursorEmMemoria()
        self.discovery_stats = DiscoveryStats()

    def _headers(self, body: str) -> dict:
        # A assinatura carrega o timestamp: recalculada a cada tentativa.
        ts = str(int(time.time()))
        sig = hashlib.sha256(
            f"{self.app_id}{ts}{body}{self.app_secret}".encode()).hexdigest()
        return {
            "Authorization": f"SHA256 Credential={self.app_id}, Timestamp={ts}, Signature={sig}",
            "Content-Type": "application/json",
        }

    def _post(self, payload: dict) -> dict:
        body = json.dumps(payload, separators=(",", ":"))
        ultimo: SourceError | None = None
        for tentativa in range(1 + len(RETRY_DELAYS_S)):
            if tentativa:
                self.sleep(RETRY_DELAYS_S[tentativa - 1])
            try:
                r = self.client.post(GRAPHQL_URL, content=body, headers=self._headers(body))
            except httpx.TransportError as exc:      # conexão, timeout: transitório
                ultimo = SourceError(f"shopee API: {exc}")
                continue
            except httpx.HTTPError as exc:
                raise SourceError(f"shopee API: {exc}") from exc
            if r.status_code == 429 or r.status_code >= 500:
                ultimo = SourceError(f"shopee API: HTTP {r.status_code} {r.reason_phrase}".rstrip())
                continue
            if r.status_code >= 400:
                raise SourceError(f"shopee API: HTTP {r.status_code} {r.reason_phrase}".rstrip())
            break
        else:
            assert ultimo is not None
            raise ultimo
        try:
            data = r.json()
        except ValueError as exc:
            raise SourceError(f"shopee API: resposta não é JSON válido: {exc}") from exc
        if data.get("errors"):
            raise SourceError(f"shopee GraphQL: {data['errors']}")
        if "data" not in data:
            raise SourceError(f"shopee GraphQL: resposta sem campo 'data': {data}")
        return data["data"]

    # -- varredura rotativa (fase 5C, M1) -----------------------------------

    def fetch_offers(self, cfg: dict) -> list[Offer]:
        """Lê UMA FATIA do espaço da API por run — `calls_per_run` chamadas,
        uma página cada — e deixa um cursor persistido dizendo por onde o
        próximo run continua.

        Antes, todo run relia as mesmas p1–2 das 5 raízes: 244 itens únicos
        por mês, 8 posts/dia sustentáveis com dedupe de 30 (C1). A medição de
        2026-08-26 mostrou que cada (categoria, sortType) é uma janela de 40
        páginas × 50 = 2.000 itens, e que a p1 muda 1–2 itens em 50 a cada
        ~9 h: um run não precisa ver tudo, o DIA precisa. Com 8 chamadas/run e
        192 runs/dia (~1.500 chamadas), as raízes inteiras saem a cada 40 runs
        (~3h20) e o espaço todo em cerca de um dia.

        Quem acumula o resultado das fatias é o estoque de candidatas
        (`StateDB.upsert_candidates`, chamado pelo pipeline)."""
        sh = cfg["shopee"]
        # 0 (ou negativo) = sem teto: roda o plano inteiro. `or DEFAULT`
        # transformaria o 0 explícito em 8 (A11).
        teto = int(_ou_padrao(sh, "calls_per_run", DEFAULT_CALLS_PER_RUN))
        plano = self._plano(sh)
        if teto > 0:
            plano = plano[:teto]
        offers: list[Offer] = []
        seen_ids: set[str] = set()
        stats = DiscoveryStats()
        for fatia in plano:
            nodes, tem_proxima = self._busca(sh, fatia)
            stats.calls += 1
            stats.nodes += len(nodes)
            for node in nodes:
                offer = _parse_node(node)
                if offer and offer.item_id not in seen_ids:
                    seen_ids.add(offer.item_id)
                    offers.append(offer)
                    stats.eligible += 1
            self._avanca_pagina(sh, fatia, tem_proxima, len(nodes))
        self._avanca_indices(sh, plano)
        self.discovery_stats = stats
        return offers

    def _busca(self, sh: dict, fatia: _Fatia) -> tuple[list[dict], bool | None]:
        variables = {"page": fatia.page, "limit": sh["page_size"],
                     "sortType": fatia.sort_type, "listType": sh["list_type"]}
        if fatia.category_id is not None:
            variables["productCatId"] = int(fatia.category_id)
        if fatia.keyword:
            # `keyword` NÃO é filtro estrito: 8% dos nós voltam de outra
            # categoria (calls 107/108/118/133). Quem descarta é o allowlist
            # de `selection`, não esta busca.
            variables["keyword"] = fatia.keyword
        data = self._post({"query": PRODUCT_OFFER_QUERY, "variables": variables})
        bloco = data.get("productOfferV2") or {}
        nodes = bloco.get("nodes") or []
        info = bloco.get("pageInfo") or {}
        return nodes, info.get("hasNextPage")

    def _plano(self, sh: dict) -> list[_Fatia]:
        """As fatias deste run, na ordem: raízes, subcategorias, keywords.
        Só LÊ os cursores — quem os avança é `fetch_offers`, depois de saber
        que a fatia não foi cortada por `calls_per_run`."""
        pages = int(sh.get("pages") or DEFAULT_PAGES)
        sorts = list(sh["sort_types"])
        fatias: list[_Fatia] = []

        # Raízes: sem productCatId a API devolve majoritariamente uma categoria
        # fora do allowlist (fase 1.9), por isso a busca sempre itera por
        # categoria. Chave AUSENTE = uma busca sem filtro (comportamento
        # anterior à fase 1.9); lista VAZIA = nenhuma raiz nesta varredura.
        bruto = sh.get("category_ids")
        for cat in ([None] if bruto is None else [str(c) for c in bruto]):
            for sort_type in sorts:
                chave = f"shopee:root_page:{cat}:{sort_type}"
                fatias.append(_Fatia(None if cat is None else str(cat),
                                     self._pagina(chave, 1, pages), sort_type,
                                     cursor_key=chave, first_page=1))

        subs = [str(s) for s in (sh.get("subcategory_ids") or [])]
        n_sub = int(_ou_padrao(sh, "subcategories_per_run", DEFAULT_SUBCATEGORIES_PER_RUN))
        primeira = int(_ou_padrao(sh, "subcategory_first_page",
                                  DEFAULT_SUBCATEGORY_FIRST_PAGE))
        if subs and n_sub > 0:
            idx = self._inteiro("shopee:subcat_idx", 0)
            for j in range(min(n_sub, len(subs))):
                sub = subs[(idx + j) % len(subs)]
                chave = f"shopee:subcat_page:{sub}"
                fatias.append(_Fatia(sub, self._pagina(chave, primeira, pages), sorts[0],
                                     cursor_key=chave, first_page=primeira))

        termos = _termos(sh.get("keywords"))
        n_kw = int(_ou_padrao(sh, "keywords_per_run", DEFAULT_KEYWORDS_PER_RUN))
        kw_pages = max(1, int(_ou_padrao(sh, "keyword_pages", DEFAULT_KEYWORD_PAGES)))
        if termos and n_kw > 0:
            idx = self._inteiro("shopee:kw_idx", 0)
            total = len(termos) * kw_pages
            for j in range(min(n_kw, total)):
                i = (idx + j) % total
                cat, termo = termos[i % len(termos)]
                fatias.append(_Fatia(cat, i // len(termos) + 1, sorts[0], keyword=termo))
        return fatias

    def _avanca_pagina(self, sh: dict, fatia: _Fatia, tem_proxima: bool | None,
                       n_nodes: int) -> None:
        """Próxima página desta listagem. `hasNextPage: false` (ou o teto de
        `pages`, ou página vazia) volta ao começo da janela daquela categoria."""
        if not fatia.cursor_key:
            return
        pages = int(sh.get("pages") or DEFAULT_PAGES)
        if tem_proxima is None:          # resposta sem pageInfo: heurística antiga
            tem_proxima = n_nodes >= int(sh["page_size"])
        proxima = (fatia.page + 1 if tem_proxima and n_nodes and fatia.page < pages
                   else fatia.first_page)
        self.cursor.set_cursor(fatia.cursor_key, str(proxima))

    def _avanca_indices(self, sh: dict, plano: list[_Fatia]) -> None:
        """Índices que escolhem QUAIS subcategorias/termos vêm no próximo run —
        avançam só pelas fatias que sobreviveram ao teto de `calls_per_run`."""
        subs = [str(s) for s in (sh.get("subcategory_ids") or [])]
        usadas = sum(1 for f in plano if f.cursor_key.startswith("shopee:subcat_page:"))
        if subs and usadas:
            idx = self._inteiro("shopee:subcat_idx", 0)
            self.cursor.set_cursor("shopee:subcat_idx", str((idx + usadas) % len(subs)))
        termos = _termos(sh.get("keywords"))
        kw_pages = max(1, int(_ou_padrao(sh, "keyword_pages", DEFAULT_KEYWORD_PAGES)))
        usados = sum(1 for f in plano if f.keyword)
        if termos and usados:
            idx = self._inteiro("shopee:kw_idx", 0)
            self.cursor.set_cursor("shopee:kw_idx",
                                   str((idx + usados) % (len(termos) * kw_pages)))

    def _inteiro(self, key: str, default: int) -> int:
        try:
            return int(self.cursor.get_cursor(key, str(default)))
        except (TypeError, ValueError):
            return default

    def _pagina(self, key: str, primeira: int, pages: int) -> int:
        pagina = self._inteiro(key, primeira)
        return pagina if primeira <= pagina <= pages else primeira

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
        """Preço e comissão AO VIVO do item, uma chamada (`itemId`), logo
        antes de publicar.

        Com o estoque de candidatas (M1), a oferta pode ter sido descoberta há
        até `candidate_max_age_days` dias: publicar o preço da descoberta seria
        anunciar um preço que pode não existir mais. Item ausente da resposta
        — ou com a oferta expirada — significa que ele saiu da listagem de
        afiliados: `SourceError`, e o pipeline promove a próxima da fila. Quem
        grava o preço vivo no `price_log` é o pipeline, logo após esta chamada."""
        try:
            item_id = int(str(offer.item_id).strip())
        except (TypeError, ValueError) as exc:
            raise SourceError(f"shopee: itemId inválido ({offer.item_id!r})") from exc
        data = self._post({"query": ITEM_OFFER_QUERY, "variables": {"itemId": item_id}})
        nodes = (data.get("productOfferV2") or {}).get("nodes") or []
        vivo = None
        for node in nodes:
            if str(node.get("itemId") or "") == str(offer.item_id):
                vivo = _parse_node(node)     # None se a oferta expirou
                break
        if vivo is None:
            raise SourceError(f"shopee: item {offer.item_id} saiu da listagem")
        return dataclasses.replace(
            offer,
            price_current_cents=vivo.price_current_cents,
            price_original_cents=vivo.price_original_cents,
            commission_pct=vivo.commission_pct,
            commission_brl=vivo.commission_brl,
        )


def _ou_padrao(sh: dict, chave: str, padrao):
    """`sh.get(chave)` que honra 0 — `or padrao` transformaria
    `keywords_per_run: 0` (desligar as keywords) no padrão em silêncio (A11)."""
    valor = sh.get(chave)
    return padrao if valor is None else valor


def _termos(keywords) -> list[tuple[str | None, str]]:
    """`{categoria: [termo, ...]}` achatado em `[(categoria, termo), ...]`, na
    ordem do config. Lista simples (sem categoria) também vale."""
    if not keywords:
        return []
    if isinstance(keywords, dict):
        return [(str(cat), str(termo))
                for cat, termos in keywords.items() for termo in (termos or [])]
    return [(None, str(termo)) for termo in keywords]


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

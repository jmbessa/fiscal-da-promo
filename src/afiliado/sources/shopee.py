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

# -- data feed (fase 5L) -------------------------------------------------------
# `listItemFeeds` e `getItemFeedData` são as outras duas das oito consultas que
# a API expõe, e nunca tinham sido tocadas. Assinaturas da introspecção viva de
# 2026-08-28:
#
#   listItemFeeds(feedMode: FeedMode): ItemFeedListConnection!
#     feeds { datafeedId referenceId datafeedName description totalCount date feedMode }
#   getItemFeedData(datafeedId: String!, offset: Int, limit: Int): ItemFeedDataConnection!
#     rows { columns updateType }   pageInfo { offset limit totalCount hasMore }
#
# `columns` é um JSON de 24 chaves POR LINHA — o catálogo inteiro do vendedor,
# sem passar pela busca. O que ele NÃO traz: `commission` e `sales`. Ver
# `docs/runbooks/shopee-preco.md`.
FEED_LIST_QUERY = """
query listItemFeeds($mode: FeedMode) {
  listItemFeeds(feedMode: $mode) {
    feeds { datafeedId datafeedName totalCount date feedMode }
  }
}
"""

FEED_DATA_QUERY = """
query getItemFeedData($id: String!, $offset: Int, $limit: Int) {
  getItemFeedData(datafeedId: $id, offset: $offset, limit: $limit) {
    rows { columns updateType }
    pageInfo { offset limit totalCount hasMore }
  }
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

# O que `productOfferV2.sales` MEDE: unidades dos últimos ~30 dias, NÃO o
# contador vitalício que o anúncio exibe. Medido em 2026-08-28 contra o cubo
# `ShbMartItem` do JoomPulse, cuja documentação define `sold1y` como o
# "cumulative lifetime sold counter as displayed by Shopee" e `sold30Days`
# como a estimativa do último mês:
#
#   item                            nosso `sales`   sold1y (o anúncio)  sold30Days
#   16692338189 Lençol Micropercal         45.950           2.000.000      50.000
#   22893738408 Lençol Extra Macio         77.344           1.000.000      70.000
#   58256439593 Percarbonato               73.175             100.000      70.000
#   9212570285  Creatina Soldiers          31.077             100.000      30.000
#
# Nos quatro o nosso número bate com a janela de 30 dias e fica 13× a 43×
# abaixo do que o comprador vê no anúncio. Buscar o `sold1y` para enriquecer
# não fecha: a cota do JoomPulse é de ~9 consultas/dia e o pool tem centenas de
# candidatas girando a cada 3 dias. Então o número fica, e o TEXTO passa a
# dizer a janela (`pricing.format_sales`).
SALES_WINDOW_DAYS = 30

# -- padrões do data feed (fase 5L), todos medidos em 2026-08-28 --------------
# Teto por chamada: 500 (`limit: 1000` devolve
# `error [11001] ... the maximum limit is 500`).
FEED_MAX_LIMIT = 500
# NASCE DESLIGADO: quem liga é o config.yaml. Um default > 0 faria toda fonte
# montada em teste/doctor gastar chamadas de feed sem ninguém pedir.
DEFAULT_FEED_CALLS_PER_RUN = 0
DEFAULT_FEED_PAGE_SIZE = FEED_MAX_LIMIT
# Quantas linhas de cada janela entram no ESTOQUE. O teto existe porque o
# gargalo do feed não é a API, é o `state.db`: 32% das linhas passam nos
# portões (medido em 3 janelas de 500 do feed oficial: 161/162/163), e guardar
# tudo seriam ~9.800 candidatas/dia — com `candidate_max_age_days: 3`, ~29 mil
# linhas de ~600 bytes num arquivo binário versionado, contra 60 posts/dia.
DEFAULT_FEED_KEEP_PER_RUN = 10
# Por CURTIDAS, e não por nota: a nota do feed é 5,0 na mediana (165 de 172
# linhas acima de 4,5 — ela não separa nada), enquanto o `like` prevê venda.
# Medido no feed oficial, numa janela de 500: as 12 linhas mais curtidas somam
# 2.152 vendas nos últimos 30 dias (mediana 33) e as 12 menos curtidas somam 2
# (mediana 0). O `like` é contagem da própria Shopee, não alegação do vendedor.
FEED_CURSOR_KEY = "shopee:feed_offset"


@dataclass
class DiscoveryStats:
    """O que a fatia deste run custou e rendeu — vai ao resumo de ops."""
    calls: int = 0
    nodes: int = 0
    eligible: int = 0
    # Aviso de configuração (não de run): `calls_per_run` pequeno demais para o
    # plano. Vazio = nada a dizer. Quem o leva ao chat de ops é o pipeline.
    warning: str = ""
    # Fase 5L: a fatia do DATA FEED, contada à parte da busca — sem isso "8
    # chamadas · 400 nós" não diria qual das duas superfícies rendeu o quê, e a
    # comparação entre elas viraria opinião. Vazio = feed desligado.
    feed: str = ""
    # O feed falhou e a busca continuou. É aviso (notifica uma vez por dia),
    # não número do run.
    feed_warning: str = ""

    def avisos(self) -> list[str]:
        return [a for a in (self.warning, self.feed_warning) if a]


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
    sales_window_days = SALES_WINDOW_DAYS

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
        ~9 h: um run não precisa ver tudo, o DIA precisa. Com 8 chamadas/run, as
        raízes inteiras saem a cada 40 runs — ~3h20 na VPS (192 runs/dia,
        ~1.500 chamadas) e ~2,5 dias no Actions (16 runs/dia, 128 chamadas). A
        margem contra o dedupe de 30 dias vem do TAMANHO do espaço (≈5.460
        elegíveis nas raízes contra 1.800 posts/mês), não da frequência.

        Quem acumula o resultado das fatias é o estoque de candidatas
        (`StateDB.upsert_candidates`, chamado pelo pipeline)."""
        sh = cfg["shopee"]
        # 0 (ou negativo) = sem teto: roda o plano inteiro. `or DEFAULT`
        # transformaria o 0 explícito em 8 (A11).
        teto = int(_ou_padrao(sh, "calls_per_run", DEFAULT_CALLS_PER_RUN))
        plano = self._plano(sh)
        cortadas = plano[teto:] if teto > 0 else []
        if teto > 0:
            plano = plano[:teto]
        offers: list[Offer] = []
        seen_ids: set[str] = set()
        stats = DiscoveryStats(warning=_aviso_de_plano_truncado(teto, plano, cortadas))
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
        offers += self._fatia_do_feed(sh, seen_ids, stats)
        self.discovery_stats = stats
        return offers

    # -- data feed (fase 5L) -------------------------------------------------

    def _fatia_do_feed(self, sh: dict, ja_vistos: set[str],
                       stats: DiscoveryStats) -> list[Offer]:
        """Uma fatia do CATÁLOGO por run, ao lado da varredura — não no lugar
        dela.

        Por que FULL e não DELTA (medido em 2026-08-28, e é o contrário do que
        parecia): o DELTA oficial tem 170.217 linhas contra 100.000 do FULL —
        341 chamadas contra 200 —, e numa janela de 500 dele 229 linhas são
        `DELETE` (item saindo do catálogo) contra 264 `NEW` e 7 `UPDATE`. Ou
        seja, o DELTA custa 70% MAIS chamadas para entregar MENOS linha
        aproveitável. Não existe o caminho barato de manter o estoque fresco
        pelo DELTA nesta conta; o barato é varrer o FULL devagar.

        A varredura do FULL é um cursor de `offset` persistido, `feed_calls_per_run`
        janelas por run — o mesmo teto que a 5C impôs à busca, pela mesma razão:
        as 200 chamadas do catálogo inteiro num run só seriam um martelo contra
        a conta de afiliado.

        O `datafeedId` carrega a DATA (`..._FULL_2026-08-27`) e o arquivo é
        regerado todo dia, então ele é relistado a cada run (1 chamada). O
        preço disso é 1 chamada por run; o preço de cacheá-lo seria varrer um
        arquivo que não existe mais no meio do ciclo. E, pela mesma razão, o
        ciclo de 200 janelas NÃO é uma partição do catálogo: é uma amostra
        rotativa dele.

        Falha do feed NÃO derruba a descoberta: ele é uma superfície A MAIS, e
        trocar as 8 chamadas que funcionam por um erro de uma consulta nova
        seria o pior negócio possível. O erro vira aviso (uma vez por dia)."""
        n = int(_ou_padrao(sh, "feed_calls_per_run", DEFAULT_FEED_CALLS_PER_RUN))
        if n <= 0:
            return []
        try:
            feed = self._escolhe_feed(sh)
            linhas, janelas = self._janelas_do_feed(sh, feed, n)
            chamadas = 1 + janelas
        except SourceError as exc:
            stats.feed_warning = f"⚠️ shopee: data feed indisponível ({exc}) — a busca continua"
            return []
        permitidas = {str(c) for c in (sh.get("category_ids") or [])}
        candidatas: list[tuple[int, Offer]] = []
        vistos = set(ja_vistos)
        for row in linhas:
            par = _parse_feed_row(row)
            if par is None:
                continue
            curtidas, offer = par
            # O feed não tem `productCatId` como argumento: o mesmo allowlist
            # que a busca aplica na API é aplicado aqui, na linha. Sem ele, 35%
            # do feed oficial medido são autopeças (categoria 102187) e o teto
            # por curtidas seria gasto fora das nossas cinco raízes.
            if permitidas and offer.category not in permitidas:
                continue
            if offer.item_id in vistos:
                continue
            vistos.add(offer.item_id)
            candidatas.append((curtidas, offer))
        candidatas.sort(key=lambda par: par[0], reverse=True)
        teto = int(_ou_padrao(sh, "feed_keep_per_run", DEFAULT_FEED_KEEP_PER_RUN))
        mantidas = [o for _, o in (candidatas[:teto] if teto > 0 else candidatas)]
        # `chamadas` inclui o `listItemFeeds`: o custo do feed é o que ele
        # gasta, não o que gostaríamos que gastasse.
        stats.feed = (f"{chamadas} chamadas · {len(linhas)} linhas · "
                      f"{len(candidatas)} elegíveis · {len(mantidas)} mantidas")
        return mantidas

    def _escolhe_feed(self, sh: dict) -> dict:
        """O feed FULL deste run. Sem `feed_name`, o MAIOR — que é o "Shopee
        Oficial BR" (100.000 itens contra 10.000 do "Shopee Brasil"), e é
        também o único cuja coluna `like` vem preenchida (no outro ela é 0 em
        todas as 178 linhas elegíveis da janela medida), sem a qual o teto por
        curtidas escolheria ao acaso."""
        data = self._post({"query": FEED_LIST_QUERY, "variables": {"mode": "FULL"}})
        feeds = [f for f in ((data.get("listItemFeeds") or {}).get("feeds") or [])
                 if isinstance(f, dict) and f.get("datafeedId")]
        nome = str(sh.get("feed_name") or "").strip().lower()
        if nome:
            feeds = [f for f in feeds if nome in str(f.get("datafeedName") or "").lower()]
        if not feeds:
            raise SourceError(
                f"nenhum feed FULL{f' com {nome!r} no nome' if nome else ''} na conta")
        return max(feeds, key=lambda f: _inteiro_ou_zero(f.get("totalCount")))

    def _janelas_do_feed(self, sh: dict, feed: dict, n: int) -> tuple[list[dict], int]:
        """`n` janelas a partir do cursor. `hasMore: false` (ou janela vazia)
        devolve o cursor ao começo — o feed seguinte é outro arquivo."""
        limite = min(int(_ou_padrao(sh, "feed_page_size", DEFAULT_FEED_PAGE_SIZE)),
                     FEED_MAX_LIMIT)
        total = _inteiro_ou_zero(feed.get("totalCount"))
        offset = self._inteiro(FEED_CURSOR_KEY, 0)
        if offset < 0 or (total and offset >= total):
            offset = 0
        linhas: list[dict] = []
        chamadas = 0
        for _ in range(n):
            data = self._post({"query": FEED_DATA_QUERY,
                               "variables": {"id": str(feed["datafeedId"]),
                                             "offset": offset, "limit": limite}})
            bloco = data.get("getItemFeedData") or {}
            rows = [r for r in (bloco.get("rows") or []) if isinstance(r, dict)]
            info = bloco.get("pageInfo") or {}
            chamadas += 1
            linhas += rows
            if rows and info.get("hasMore"):
                offset = _inteiro_ou_zero(info.get("offset")) + len(rows)
            else:
                offset = 0
                break
        self.cursor.set_cursor(FEED_CURSOR_KEY, str(offset))
        return linhas, chamadas

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
        """Preço, comissão e VENDAS ao vivo do item, uma chamada (`itemId`),
        logo antes de publicar.

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
        # `Int64` da Shopee exige STRING no JSON: medido ao vivo em 2026-08-26,
        # `{"itemId": 20595061903}` devolve `wrong type` (code 10010) e
        # `{"itemId": "20595061903"}` devolve o nó. O `int()` acima continua
        # valendo como validação — o que vai na requisição é o texto.
        data = self._post({"query": ITEM_OFFER_QUERY,
                           "variables": {"itemId": str(item_id)}})
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
            # Fase 5L: as VENDAS também. A candidata vinda do data feed chega
            # com `sales == 0` (o feed não tem o campo) e esta é a única
            # chamada que ela recebe antes de ir ao ar — sem isto a arte dela
            # sairia sem prova social para sempre e o `ev_score` perderia o
            # peso de popularidade. Vem com a JANELA junto (fase 5H: o `sales`
            # da Shopee são ~30 dias, não o total que o anúncio exibe).
            sales=vivo.sales,
            sales_window_days=vivo.sales_window_days,
        )


def _aviso_de_plano_truncado(teto: int, plano: list[_Fatia],
                             cortadas: list[_Fatia]) -> str:
    """Aviso quando `calls_per_run` corta o plano (menor da revisão da 5C).

    Com `len(category_ids) × len(sort_types) >= calls_per_run`, as fatias de
    subcategoria e de palavra-chave ficam sempre depois do corte — e o índice
    delas só avança pelas fatias que SOBREVIVERAM ao teto, então elas nunca
    rodam. Silenciosamente: metade do espaço de descoberta desligada por um
    número no config."""
    if not cortadas:
        return ""
    grupos = [
        ("raiz(es)", sum(1 for f in cortadas if f.cursor_key.startswith("shopee:root_page:"))),
        ("subcategoria(s)", sum(1 for f in cortadas
                                if f.cursor_key.startswith("shopee:subcat_page:"))),
        ("keyword(s)", sum(1 for f in cortadas if f.keyword)),
    ]
    perdidas = ", ".join(f"{n} {nome}" for nome, n in grupos if n)
    return (f"⚠️ shopee: calls_per_run={teto} corta o plano de "
            f"{len(plano) + len(cortadas)} chamadas — {perdidas} nunca rodam "
            "(o cursor delas só avança pelas fatias que couberam)")


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
        # Janela de ~30 dias, não o total do anúncio (ver SALES_WINDOW_DAYS).
        # `sales_e_faixa` fica falso: o número é fino, não um balde.
        sales_window_days=SALES_WINDOW_DAYS,
        rating=_parse_rating(node.get("ratingStar")),
        price_min_cents=_cents_or_zero(node.get("priceMin")),
        price_max_cents=_cents_or_zero(node.get("priceMax")),
        commission_brl=_commission_brl(node.get("commission")),
    )


def _parse_feed_row(row: dict) -> tuple[int, Offer] | None:
    """Uma linha do data feed em `(curtidas, Offer)` — ou None quando a linha
    não é aproveitável.

    A oferta nasce com `commission_pct`, `commission_brl` e `sales` ZERADOS, e
    isso é o ponto: o feed não tem esses campos. 0 é "ainda não sei" (a mesma
    convenção do preço desconhecido da 5J), quem os mede é o `refresh_price`
    imediatamente antes de publicar, e `selection.comissao_desconhecida` é o
    que impede o piso de EV de matar a candidata no caminho.

    `price` é o "de" do vendedor e `sale_price` o "por" (medido: diferem em 244
    de 500 linhas, e a diferença bate com `discount_percentage`). Os dois
    entram como em qualquer oferta da Shopee — e, como sempre, o desconto do
    vendedor é RÓTULO: quem decide o que o post alega é a régua honesta."""
    if _e_apagada(row.get("updateType")):
        return None
    try:
        col = json.loads(row.get("columns") or "")
    except (TypeError, ValueError):
        return None
    if not isinstance(col, dict) or _e_apagada(col.get("update_type")):
        return None
    item_id = str(col.get("itemid") or "").strip()
    if not item_id:
        return None
    atual = _cents_or_zero(col.get("sale_price")) or _cents_or_zero(col.get("price"))
    if atual <= 0:
        return None
    original = _cents_or_zero(col.get("price"))
    return _inteiro_ou_zero(col.get("like")), Offer(
        source="shopee",
        item_id=item_id,
        title=str(col.get("title") or "").strip(),
        price_original_cents=max(original, atual),
        price_current_cents=atual,
        commission_pct=0.0,
        image_url=str(col.get("image_link") or "").strip(),
        product_url=str(col.get("product_link") or "").strip(),
        # JÁ É link de afiliado (`utm_medium=affiliates&utm_source=an_...`,
        # medido em 500 de 500 linhas): serve de queda para o
        # `generateShortLink`. Ele NÃO vira o link publicado por padrão — é uma
        # URL de ~700 caracteres, contra os ~30 do `shope.ee`, e trocar o
        # gerador oficial por ela mexeria na atribuição de todo post da loja.
        offer_link=str(col.get("product_short link") or "").strip(),
        category=str(col.get("global_catid1") or "").strip(),
        sales=0,
        sales_window_days=SALES_WINDOW_DAYS,
        rating=_parse_rating(col.get("item_rating")),
        commission_brl=0.0,
    )


def _e_apagada(update_type) -> bool:
    """Linha de DELETE: item saindo do catálogo (229 de 500 numa janela do
    DELTA oficial, medido). Publicá-la seria anunciar o que não existe mais."""
    return str(update_type or "").strip().upper() == "DELETE"


def _inteiro_ou_zero(raw) -> int:
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return 0


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

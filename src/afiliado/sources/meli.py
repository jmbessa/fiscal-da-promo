import dataclasses
import json
import os
import time
from collections import Counter
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
# spec). `/products/{id}/items` segue liberado e é usado só em
# `refresh_price`, imediatamente antes de publicar.
DEFAULT_OFFERS_PATH = "data/meli_offers.json"
DEFAULT_VALID_DAYS = 30
# Páginas de /products/{id}/items percorridas atrás do anúncio do buy box
# (100 anúncios por página; o maior produto visto tinha 38).
MAX_ITEMS_PAGES = 5
# Validade da verificação do buy box (rodada de correção da 5B, Fix 1). Ao
# vivo em 2026-08-26, 3 produtos: `results[0]` de /products/{id}/items bateu
# com a página em 2 de 3 (no 3º a página mostrava `results[1]`), e o anúncio
# do pool de um deles já tinha SUMIDO da lista. Nem a ordem da API nem o
# `buy_box_item_id` do pool reproduzem a página com certeza — o que o loader
# consegue garantir é a IDADE da verificação: `buy_box_checked_at` (ou, na
# falta, `generated_at`) com mais de 7 dias → entrada ignorada com motivo.
BUY_BOX_MAX_AGE_DAYS = 7

# Campos inteiros > 0 obrigatórios em cada entrada do pool (fase 5B, C7d) e
# o motivo, por grupo, que vai ao aviso quando faltam.
CAMPOS_DE_PRECO = (
    ("price_ref_cents", "sem referência"),
    ("price_p25_cents", "sem p25"),
    ("price_window_days", "sem janela da referência"),
    ("price_historic_min_cents", "sem mínima histórica"),
    ("price_min_window_days", "sem janela da mínima"),
)


class MeliSource:
    name = "meli"
    # O preço "atual" com que a oferta sai do pool é a MEDIANA da janela, não
    # uma observação: o pipeline não a grava no price_log (C7c). O que entra
    # no histórico do ML é o preço vivo do buy box, logo após `refresh_price`.
    observes_price_on_discovery = False

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
        # product_id -> item_id do anúncio que vence o buy box (do pool);
        # `refresh_price` só aceita o preço DESSE anúncio.
        self._buy_box_ids: dict[str, str] = {}
        # Motivo de fetch_offers ter devolvido menos do que o pool tem (pool
        # ausente/inválido/vencido, entradas puladas e por quê); None quando
        # a última leitura foi limpa. Vai ao doctor e ao resumo de ops.
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
        só com as demais fontes; `self.pool_warning` guarda o motivo.

        Validação na carga (fase 5B, C7d): cada entrada é PULADA — e contada
        no aviso, por motivo — quando falta qualquer campo de preço inteiro
        > 0 (`CAMPOS_DE_PRECO`), quando `price_ref_cents / 100` sai de
        `selection.price_min_brl..price_max_brl`, quando o p25 passa da
        referência, quando a mínima histórica passa do p25, quando não há
        `buy_box_item_id` (sem ele `refresh_price` nunca teria preço: entrada
        morta por construção), ou quando a verificação do buy box
        (`buy_box_checked_at`; na falta, `generated_at`) tem mais de
        `BUY_BOX_MAX_AGE_DAYS` dias — o vencedor muda e o anúncio do pool some
        da lista (visto ao vivo). Um pool que era foto de um dia (C7) não passa."""
        me = cfg.get("meli") or {}
        sel = cfg.get("selection") or {}
        offers_path = Path(me.get("offers_path") or DEFAULT_OFFERS_PATH)
        commission_pct = float(me.get("commission_pct") or 0.0)
        self.pool_warning = None
        hoje = date.today()

        try:
            raw = json.loads(offers_path.read_text(encoding="utf-8"))
            generated_at = date.fromisoformat(str(raw["generated_at"]))
            valid_days = int(raw.get("valid_days", DEFAULT_VALID_DAYS))
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            self.pool_warning = f"pool ausente ou inválido ({offers_path})"
            return []

        if (hoje - generated_at).days > valid_days:
            self.pool_warning = (
                f"pool vencido: gerado em {generated_at.isoformat()}, "
                f"validade {valid_days}d")
            return []

        offers: list[Offer] = []
        seen_ids: set[str] = set()
        motivos: Counter[str] = Counter()
        for item in raw.get("offers") or []:
            if not isinstance(item, dict):
                motivos["entrada não é objeto"] += 1
                continue
            offer, motivo = _parse_pool_offer(item, commission_pct, sel, generated_at, hoje)
            if offer is None:
                motivos[motivo] += 1
                continue
            if offer.item_id in seen_ids:
                motivos["id repetido"] += 1
                continue
            seen_ids.add(offer.item_id)
            self._buy_box_ids[offer.item_id] = str(item["buy_box_item_id"])
            offers.append(offer)
        if motivos:
            detalhe = ", ".join(f"{n} {motivo}" for motivo, n in
                                sorted(motivos.items(), key=lambda kv: (-kv[1], kv[0])))
            self.pool_warning = (
                f"{sum(motivos.values())} entrada(s) do pool ignorada(s) ({detalhe})")
        return offers

    # -- preço ao vivo (imediatamente antes de publicar) -------------------

    def refresh_price(self, offer: Offer) -> Offer:
        """Preço vivo = o do anúncio que vence o BUY BOX (C7b), nunca o menor
        entre os vendedores: a página de catálogo mostra o vencedor, e o
        post dizia R$ 32 enquanto o clique mostrava R$ 45.

        Caminho escolhido (verificado ao vivo em 2026-08-26 com token de
        aplicação E de usuário, em 3 produtos do pool): `GET /products/{id}`
        traz a chave `buy_box_winner`, mas sempre `null` — o endpoint não
        entrega o vencedor a este app. Então: `GET /products/{id}/items` e o
        item cujo `item_id == buy_box_item_id` do pool (o `buyBoxId` do
        JoomPulse, presente na lista real: MLB7125449388 a R$ 104,90 entre
        37 vendedores cujo menor preço era R$ 58,90). Anúncio ausente da
        lista, sem preço, ou produto sem `buy_box_item_id` → `SourceError`
        ("sem buy box"): a oferta é descartada — nunca cai para o mínimo.

        Rodada de correção (Fix 1): a ORDEM de `/items` também foi conferida
        contra a página real — `results[0]` bateu em 2 de 3 produtos (no 3º
        a página mostrava `results[1]`), logo a lista não é "ordenada por buy
        box" e `results[0]` não substitui o anúncio do pool. O que envelhece
        é tratado na carga: `buy_box_checked_at` com validade de
        `BUY_BOX_MAX_AGE_DAYS` (o skill tem um passo semanal que a renova).

        Devolve um `Offer` novo (dataclass frozen) com `price_current_cents`.
        Publicabilidade continua sendo de `selection.max_above_ref` +
        `validate.check_price`; ref/p25/piso do pool viajam na oferta."""
        buy_box_id = self._buy_box_ids.get(offer.item_id, "")
        if not buy_box_id:
            raise SourceError(f"meli: sem buy box conhecido para {offer.item_id}")
        token = self.ensure_token()
        headers = {"Authorization": f"Bearer {token}"}
        url = f"{API_HOST}/products/{offer.item_id}/items"

        offset, total, vistos = 0, None, 0
        for _ in range(MAX_ITEMS_PAGES):
            try:
                r = self.client.get(url, headers=headers, params={"offset": offset} if offset else None)
                r.raise_for_status()
            except httpx.HTTPError as exc:
                raise SourceError(f"meli API: {exc}") from exc
            try:
                data = r.json()
            except ValueError as exc:
                raise SourceError(f"meli API: resposta não é JSON válido: {exc}") from exc
            results = data.get("results") or []
            vistos += len(results)
            winner = _item_by_id(results, buy_box_id)
            if winner is not None:
                live_cents = _price_cents(winner.get("price"))
                if live_cents is None:
                    raise SourceError(
                        f"meli: buy box {buy_box_id} de {offer.item_id} sem preço")
                return dataclasses.replace(offer, price_current_cents=live_cents)
            paging = data.get("paging") if isinstance(data.get("paging"), dict) else {}
            total = paging.get("total")
            limit = int(paging.get("limit") or len(results) or 0)
            offset += limit
            if not results or total is None or offset >= int(total):
                break
        raise SourceError(
            f"meli: sem buy box — anúncio {buy_box_id} não está entre os "
            f"{vistos} vendedores de {offer.item_id}")

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


def _int_positivo(valor) -> int | None:
    """Inteiro > 0 (bool não conta; float não conta — centavos são inteiros)."""
    if isinstance(valor, bool) or not isinstance(valor, int) or valor <= 0:
        return None
    return valor


def _parse_pool_offer(item: dict, commission_pct: float, sel: dict,
                      generated_at: date, hoje: date) -> tuple[Offer | None, str]:
    """(Offer, "") quando a entrada é válida; (None, motivo) quando é pulada."""
    product_id = str(item.get("product_id") or "").strip()
    title = str(item.get("title") or "").strip()
    if not product_id or not title:
        return None, "sem id ou título"
    valores: dict[str, int] = {}
    for campo, motivo in CAMPOS_DE_PRECO:
        valor = _int_positivo(item.get(campo))
        if valor is None:
            return None, motivo
        valores[campo] = valor
    if not str(item.get("buy_box_item_id") or "").strip():
        return None, "sem buy box"
    idade_buy_box = _dias_desde_a_checagem(item.get("buy_box_checked_at"), generated_at, hoje)
    if idade_buy_box is None:
        return None, "data do buy box inválida"
    if idade_buy_box > BUY_BOX_MAX_AGE_DAYS:
        return None, f"buy box não verificado há {idade_buy_box} dias"
    ref, p25 = valores["price_ref_cents"], valores["price_p25_cents"]
    minima = valores["price_historic_min_cents"]
    preco_min = sel.get("price_min_brl")
    preco_max = sel.get("price_max_brl")
    if ((preco_min is not None and ref / 100 < float(preco_min))
            or (preco_max is not None and ref / 100 > float(preco_max))):
        return None, "fora da faixa de preço"
    if p25 > ref:
        return None, "p25 acima da referência"
    if minima > p25:
        return None, "mínima acima do p25"
    return Offer(
        source="meli",
        item_id=product_id,
        title=title,
        price_original_cents=ref,
        price_current_cents=ref,
        commission_pct=commission_pct,
        image_url=str(item.get("image_url") or ""),
        product_url=f"https://www.mercadolivre.com.br/p/{product_id}",
        category=str(item.get("category") or ""),
        sales=int(item.get("sales") or 0),
        rating=float(item.get("rating") or 0.0),
        price_ref_cents=ref,
        price_p25_cents=p25,
        price_window_days=valores["price_window_days"],
        price_floor_cents=minima,
        price_floor_window_days=valores["price_min_window_days"],
    ), ""


def _dias_desde_a_checagem(checado, generated_at: date, hoje: date) -> int | None:
    """Idade (dias) da verificação do buy box. Campo ausente/nulo → a data de
    geração do pool (gerar o pool É verificar: o Passo 1 do skill devolve o
    `buyBoxId`). Não-string, data inválida ou no futuro → None."""
    if checado is None:
        return (hoje - generated_at).days
    if not isinstance(checado, str):
        return None
    try:
        idade = (hoje - date.fromisoformat(checado)).days
    except ValueError:
        return None
    return idade if idade >= 0 else None


def _item_by_id(results: list, item_id: str) -> dict | None:
    for result in results:
        if isinstance(result, dict) and str(result.get("item_id") or "") == item_id:
            return result
    return None


def _price_cents(price) -> int | None:
    if price is None:
        return None
    try:
        return int(Decimal(str(price)) * 100)
    except InvalidOperation:
        return None

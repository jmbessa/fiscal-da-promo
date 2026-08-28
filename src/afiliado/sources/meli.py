import dataclasses
import json
import os
import time
from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

import httpx

from afiliado import meli_links
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

# Campos inteiros obrigatórios em cada entrada do pool (fase 5B, C7d) e o
# motivo, por grupo, que vai ao aviso quando faltam.
#
# Fase 5J: os cinco podem vir TODOS ZERADOS — a entrada "sem histórico", que a
# onda barata do skill produz (Passo 1 + Passo 2, pulando o Passo 3, que custa
# 4 consultas do JoomPulse a cada 28 produtos contra 1 a cada 50 do resto). Ela
# é publicável em modo B e ganha régua sozinha das NOSSAS medições
# (`pricing.enrich_offers`, degrau 3). Duas coisas NÃO afrouxaram:
#   - campo AUSENTE continua erro: o que se aceita é o zero EXPLÍCITO, e um
#     pool com typo não pode passar a valer;
#   - zero PARCIAL continua erro ("régua parcial"): `ref > 0` com `p25 = 0` é
#     curadoria quebrada, não "sem histórico" — o trio vem do mesmo degrau.
CAMPOS_DE_PRECO = (
    ("price_ref_cents", "sem referência"),
    ("price_p25_cents", "sem p25"),
    ("price_window_days", "sem janela da referência"),
    ("price_historic_min_cents", "sem mínima histórica"),
    ("price_min_window_days", "sem janela da mínima"),
)

# O `sales` do pool é `catalogSales`: o contador VITALÍCIO do próprio Mercado
# Livre, o mesmo "+250 mil vendidos" que aparece no anúncio. Janela 0 = sem
# recorte de tempo. (A estimativa mensal, `catalogOrderCount1m`, já esteve
# neste campo e pôs "5 mil vendidos" num story de um produto com 250 mil —
# ver `.claude/skills/meli-pool-refresh/SKILL.md`.)
SALES_WINDOW_DAYS = 0


class MeliSource:
    name = "meli"
    sales_window_days = SALES_WINDOW_DAYS
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
        self._links_pool: dict[str, dict[str, str]] | None = None
        # Motivo de fetch_offers ter devolvido menos do que o pool tem (pool
        # ausente/inválido/vencido, entradas puladas e por quê); None quando
        # a última leitura foi limpa. Vai ao doctor e ao resumo de ops.
        self.pool_warning: str | None = None
        # Observação sobre o pool que NÃO é problema (entradas sem histórico).
        # Separada do aviso porque o doctor e o resumo tratam as duas de forma
        # diferente: uma pede ação, a outra só informa.
        self.pool_note: str | None = None

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
        # Os DOIS zerados aqui: um retorno antecipado (pool ausente ou vencido)
        # deixaria a nota da leitura anterior de pé, descrevendo um pool que
        # nem foi lido.
        self.pool_warning = None
        self.pool_note = None
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
            offers.append(offer)
        if motivos:
            detalhe = ", ".join(f"{n} {motivo}" for motivo, n in
                                sorted(motivos.items(), key=lambda kv: (-kv[1], kv[0])))
            self.pool_warning = (
                f"{sum(motivos.values())} entrada(s) do pool ignorada(s) ({detalhe})")
        # `pool_note` é INFORMATIVO e mora fora do `pool_warning` de propósito:
        # este significa "voltou menos do que o pool tem", e é sobre ele que a
        # rede contra o zero silencioso afirma `is None`. Um pool inteiro sem
        # histórico é estado SAUDÁVEL — despejá-lo no aviso faria o doctor
        # imprimir ⚠️ num dia normal, e um ⚠️ que está sempre aceso deixa de
        # ser lido: a entrada silenciosamente ignorada se esconderia atrás dele.
        sem_regua = len(offers) - self.ruler_coverage(offers)[0]
        self.pool_note = (
            f"{sem_regua} entrada(s) sem histórico: régua zerada, publicam em "
            "modo B (sem alegar desconto e sem selo) até o nosso price_log "
            "sustentar a régua — a faixa de preço delas é checada no preço "
            "VIVO, depois do refresh, e não na carga") if sem_regua else None
        return offers

    # -- preço ao vivo (imediatamente antes de publicar) -------------------

    def refresh_price(self, offer: Offer) -> Offer:
        """Preço vivo = o do anúncio MAIS BARATO **entre os que temos link**
        (fase 5M). Não é o menor da lista, não é o do buy box: é o preço do
        objeto que o nosso link abre — e por isso o número do post e o número
        que o seguidor vê ao chegar são o mesmo, por construção.

        O que a medição fechou (2026-08-28) e por que não há alternativa:

        - o vencedor do buy box NÃO é obtível: `GET /products/{id}` devolve
          `buy_box_winner: null` (3 produtos em 26/08, de novo em 28/08, com
          token de aplicação e de usuário) e o campo `tier` de
          `/products/{id}/items` veio vazio nos 89 anúncios sondados;
        - o `buy_box_item_id` do pool (o `buyBoxId` do JoomPulse) era só UM
          vendedor, e nos dois stories errados um caro: R$ 80,00 num produto
          cuja página mostrava R$ 39,90 (+100%) e R$ 209,87 num de R$ 113
          (+86%);
        - conferir o preço lendo a página antes de publicar também está
          fechado: `/items/{id}` e `/sites/MLB/search` são 403, e a página
          `/p/{id}` com sessão devolve uma casca de 14,6 KB sem preço.

        Anúncio linkado nenhum na lista viva → `SourceError` e a oferta é
        descartada. **Nunca** cai para um anúncio sem link: publicar o preço
        de um vendedor e o link de outro é o bug que esta fase conserta.

        Devolve um `Offer` novo (frozen) com `price_current_cents`,
        `price_original_cents` (o mesmo: o ML não expõe "de" de vendedor) e
        `anuncio_id` — que é o que `resolve_affiliate_link` lê."""
        linkados = self._load_links_pool().get(offer.item_id) or {}
        if not linkados:
            raise SourceError(f"meli: sem link de anúncio para {offer.item_id}")
        token = self.ensure_token()
        headers = {"Authorization": f"Bearer {token}"}
        url = f"{API_HOST}/products/{offer.item_id}/items"

        candidatos: dict[str, int] = {}
        encontrados: set[str] = set()
        offset, vistos = 0, 0
        for _ in range(MAX_ITEMS_PAGES):
            try:
                r = self.client.get(url, headers=headers,
                                    params={"offset": offset} if offset else None)
                r.raise_for_status()
            except httpx.HTTPError as exc:
                raise SourceError(f"meli API: {exc}") from exc
            try:
                data = r.json()
            except ValueError as exc:
                raise SourceError(f"meli API: resposta não é JSON válido: {exc}") from exc
            results = data.get("results") or []
            vistos += len(results)
            for item in results:
                if not isinstance(item, dict):
                    continue
                item_id = str(item.get("item_id") or "")
                if item_id not in linkados or item_id in encontrados:
                    continue
                encontrados.add(item_id)
                cents = _price_cents(item.get("price"))
                if cents is not None and cents > 0:
                    candidatos[item_id] = cents
            if len(encontrados) == len(linkados):
                # Achados todos os linkados, as páginas seguintes não podem
                # mudar a escolha — e o maior produto do pool tem 277 anúncios.
                break
            paging = data.get("paging") if isinstance(data.get("paging"), dict) else {}
            total = paging.get("total")
            limit = int(paging.get("limit") or len(results) or 0)
            offset += limit
            if not results or total is None or offset >= int(total):
                break
        if not candidatos:
            raise SourceError(
                f"meli: nenhum anúncio linkado de {offer.item_id} está à venda entre "
                f"os {vistos} vendedores")
        # Empate desempatado pelo id: dois anúncios pelo mesmo preço não podem
        # fazer o post mudar de link entre um run e outro.
        anuncio_id, cents = min(candidatos.items(), key=lambda kv: (kv[1], kv[0]))
        return dataclasses.replace(offer, price_current_cents=cents,
                                   price_original_cents=cents, anuncio_id=anuncio_id)

    # -- link de afiliado (pool pré-gerado, por ANÚNCIO) --------------------

    def resolve_affiliate_link(self, offer: Offer) -> str:
        """O link do anúncio que o `refresh_price` escolheu — nunca "algum"
        link do produto: publicar o preço de um vendedor e o link de outro é o
        bug da fase 5M."""
        if not offer.anuncio_id:
            raise SourceError(
                f"meli: sem anúncio escolhido para {offer.item_id} (refresh_price não rodou)")
        link = (self._load_links_pool().get(offer.item_id) or {}).get(offer.anuncio_id)
        if not link:
            raise SourceError(
                f"sem link de afiliado no pool para {offer.item_id}/{offer.anuncio_id}")
        return link

    def link_coverage(self, offers: list[Offer]) -> tuple[int, int]:
        """(quantas das ofertas têm ao menos UM anúncio linkado, total) —
        leitura local, sem rede (fase 5C, M5/A6).

        `data/meli_links.json` nunca foi commitado e não existe em nenhum
        checkout: com `sources.meli: true` num clone limpo, TODA oferta do ML
        virava um descarte "sem link de afiliado no pool" — e o `doctor` dizia
        ✅ mesmo assim. Agora a cobertura é um número que o doctor e o resumo
        do run mostram.

        Fase 5M: um produto que só tem o link ANTIGO (de catálogo, sem
        anúncio) conta como ZERO — ele não publica preço nenhum, e a cobertura
        precisa dizer isso em vez de esconder."""
        pool = self._load_links_pool()
        return sum(1 for o in offers if pool.get(o.item_id)), len(offers)

    def ruler_coverage(self, offers: list[Offer]) -> tuple[int, int]:
        """(quantas ofertas trazem RÉGUA CURADA do pool, total) — fase 5J, J4.

        As demais são as entradas "sem histórico": publicáveis, mas em modo B
        até `ref_min_observations` dias do nosso price_log sustentarem uma
        régua própria. Sem este número no doctor e no resumo de ops, "o ML só
        publica modo B" vira descoberta de semanas depois — e o ponto da fase é
        justamente que essa proporção mude sozinha com o tempo.

        Chamada ANTES do `enrich_offers`, é o que o POOL tem; depois dele, o
        que a oferta tem (o degrau 3 já pode ter carimbado régua própria)."""
        return sum(1 for o in offers if o.price_ref_cents > 0), len(offers)

    @property
    def links_file_exists(self) -> bool:
        return self.links_path.is_file()

    def _load_links_pool(self) -> dict[str, dict[str, str]]:
        """`{product_id: {item_id: link}}` — só os links por ANÚNCIO. O
        `product_link` do formato antigo fica guardado no arquivo (é link
        válido, foi trabalho de painel) mas não entra aqui: ele abre a página
        de catálogo, onde quem escolhe o vendedor é o Mercado Livre."""
        if self._links_pool is None:
            self._links_pool = {
                pid: dict(entrada["items"])
                for pid, entrada in meli_links.ler_pool(self.links_path).items()
                if entrada["items"]}
        return self._links_pool


def _centavos(valor) -> tuple[int | None, str]:
    """(centavos, "") quando o valor é um inteiro >= 0 — inclusive o float
    INTEGRAL que o JSON produz (`2590.0` É 2590: planilha, dump de pandas ou
    uma divisão em Python geram o ponto, e a entrada não pode morrer por
    causa dele).

    O ZERO passa a valer na fase 5J e quer dizer "ainda não medimos". Quem
    decide se ele é legítimo é `_parse_pool_offer`, olhando os CINCO campos
    juntos: zerado sozinho continua sendo régua quebrada.

    (None, "não inteiro") quando é float com fração (`4500.5` centavos não
    existe): o motivo tem de dizer isso, e não "sem referência" — mandar a
    curadoria caçar um campo que está lá é pior do que não avisar.

    (None, "") para o resto (ausente, nulo, texto, bool, negativo): vale o
    motivo do CAMPO."""
    if isinstance(valor, bool):
        return None, ""
    if isinstance(valor, float):
        if not valor.is_integer():          # NaN e inf também caem aqui
            return None, "não inteiro"
        valor = int(valor)
    if not isinstance(valor, int) or valor < 0:
        return None, ""
    return valor, ""


def _parse_pool_offer(item: dict, commission_pct: float, sel: dict,
                      generated_at: date, hoje: date) -> tuple[Offer | None, str]:
    """(Offer, "") quando a entrada é válida; (None, motivo) quando é pulada."""
    product_id = str(item.get("product_id") or "").strip()
    title = str(item.get("title") or "").strip()
    if not product_id or not title:
        return None, "sem id ou título"
    valores: dict[str, int] = {}
    for campo, motivo in CAMPOS_DE_PRECO:
        valor, problema = _centavos(item.get(campo))
        if valor is None:
            return None, problema or motivo
        valores[campo] = valor
    zerados = sum(1 for campo, _ in CAMPOS_DE_PRECO if valores[campo] == 0)
    sem_historico = zerados == len(CAMPOS_DE_PRECO)
    if zerados and not sem_historico:
        # Motivo PRÓPRIO: "sem p25" mandaria a curadoria procurar um campo que
        # está lá, e "sem histórico" absolveria uma régua que está quebrada.
        return None, "régua parcial (uns campos de régua zerados, outros não)"
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
    # A faixa é checada sobre a REFERÊNCIA, e sem histórico não há referência.
    # Verificado (fase 5J) antes de pular: quem barra por preço VIVO é
    # `validate.check_price`, que roda DEPOIS do `refresh_price`, com esta
    # mesma faixa e sobre o preço que vai ao post — a oferta de R$ 3.000 cai
    # lá, e não entra por uma porta que a de R$ 30 não usa. O aviso do pool
    # diz que a checagem foi adiada, para ninguém ler o silêncio como aprovação.
    if not sem_historico and (
            (preco_min is not None and ref / 100 < float(preco_min))
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
        # O preço com que a oferta sai do pool é a MEDIANA da janela — e 0 na
        # entrada sem histórico, que quer dizer "preço ainda desconhecido". Nos
        # dois casos ele é substituído pelo preço VIVO em `refresh_price`, antes
        # de qualquer decisão de publicação; o 0 nunca chega a um post (sem
        # preço vivo a oferta é descartada, "sem buy box").
        price_original_cents=ref,
        price_current_cents=ref,
        commission_pct=commission_pct,
        image_url=str(item.get("image_url") or ""),
        product_url=f"https://www.mercadolivre.com.br/p/{product_id}",
        category=str(item.get("category") or ""),
        sales=int(item.get("sales") or 0),
        # O contador do ML é uma FAIXA ("+250 mil"), não uma contagem: o campo
        # vem de `catalogSales`, que é o balde que o anúncio publica — e é
        # VITALÍCIO, sem recorte de tempo (ver SALES_WINDOW_DAYS).
        sales_e_faixa=True,
        sales_window_days=SALES_WINDOW_DAYS,
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


def _price_cents(price) -> int | None:
    if price is None:
        return None
    try:
        return int(Decimal(str(price)) * 100)
    except InvalidOperation:
        return None

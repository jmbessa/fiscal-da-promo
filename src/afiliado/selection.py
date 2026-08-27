import math
from dataclasses import dataclass

from afiliado import llm, pricing
from afiliado.models import Offer
from afiliado.state import StateDB
from afiliado.watchlist import Watchlist

MAX_CANDIDATES_FOR_PROMPT = 30
# Fase 5C (M3/A8): o slate apresentado ao ranker é a união de três recortes —
# 10 por valor esperado, 10 por vendas, 10 por desconto ALEGÁVEL — com variedade
# de categoria. Antes o LLM via só os 30 maiores EV, que com a comissão crua
# eram os 30 mais caros.
SLATE_POR_CRITERIO = 10
# PISO do limite por categoria (I-5 da revisão da 5C). O limite de 4 era fixo,
# e `offer.category` é o id da categoria RAIZ: com as 5 raízes que o config
# permite, o slate saturava em 20 das 30 vagas, e um estoque concentrado numa
# categoria degradava para QUATRO itens em silêncio. O limite real é
# `max(MIN_POR_CATEGORIA_NO_SLATE, ceil(30 / categorias presentes))`, que sempre
# comporta as 30 vagas: 5 categorias → 6 cada, 3 → 10 cada, 1 → 30.
MIN_POR_CATEGORIA_NO_SLATE = 4
# Expoente que amortece a comissão no EV (1.0 = comissão crua, como antes).
DEFAULT_COMMISSION_EXP = 0.7


@dataclass(frozen=True)
class FilterStats:
    """Quantas ofertas cada portão de `filter_offers` descartou (fase 5A,
    C4a). Sem isto, "50 buscadas → 0 candidatas" era um run vazio,
    indistinguível de "tudo bem"."""
    sem_dados: int = 0      # sem título, imagem ou URL
    categoria: int = 0      # fora do allowlist da fonte
    acima_ref: int = 0      # mais caro que a referência × max_above_ref
    sem_ref: int = 0        # require_price_ref e referência desconhecida
    faixa_preco: int = 0    # fora de price_min_brl..price_max_brl
    dedupe: int = 0         # publicado há menos de dedupe_days
    ev: int = 0             # abaixo de min_ev_brl

    @property
    def total(self) -> int:
        return (self.sem_dados + self.categoria + self.acima_ref + self.sem_ref
                + self.faixa_preco + self.dedupe + self.ev)

    def resumo(self) -> str:
        texto = (f"dedupe: {self.dedupe} · faixa de preço: {self.faixa_preco} · "
                 f"acima da referência: {self.acima_ref} · sem dados: {self.sem_dados} · "
                 f"categoria: {self.categoria} · EV: {self.ev}")
        if self.sem_ref:
            texto += f" · sem referência: {self.sem_ref}"
        return texto


def _allowed_categories(cfg: dict, source: str) -> set[str]:
    """IDs de categoria permitidos para uma fonte, a partir de
    `selection.category_ids`. Aceita lista (formato legado: vale para todas
    as fontes) ou dict por fonte (`{"shopee": [...], "meli": [...]}`). Vazio
    ou ausente = todas as categorias passam para aquela fonte."""
    raw = cfg["selection"].get("category_ids") or []
    if isinstance(raw, dict):
        raw = raw.get(source) or []
    return {str(c) for c in raw}


def filter_offers_with_stats(offers: list[Offer], db: StateDB,
                             cfg: dict) -> tuple[list[Offer], FilterStats]:
    """Portões por regra (sem LLM) e a contagem do que cada um descartou."""
    sel = cfg["selection"]
    cats_by_source: dict[str, set[str]] = {}
    cortes: dict[str, int] = {}
    # Um SELECT para o dedupe do run inteiro, não um por oferta (o estoque de
    # candidatas da fase 5C tem milhares).
    ja_postados = db.recently_posted(sel["dedupe_days"])

    def corta(portao: str) -> None:
        cortes[portao] = cortes.get(portao, 0) + 1

    result = []
    for o in offers:
        if not (o.title and o.image_url and o.product_url):
            corta("sem_dados")
            continue
        allowed_cats = cats_by_source.setdefault(o.source, _allowed_categories(cfg, o.source))
        if allowed_cats and o.category not in allowed_cats:
            corta("categoria")
            continue
        # Régua honesta: o desconto não decide SE publicamos (isso mataria o
        # volume e o ML inteiro) — decide só o que o post ALEGA. O único corte
        # de preço é não anunciar algo mais caro que o típico.
        if o.price_ref_cents > 0 and (
                o.price_current_cents > o.price_ref_cents * float(sel["max_above_ref"])):
            corta("acima_ref")
            continue
        if sel.get("require_price_ref") and o.price_ref_cents <= 0:
            corta("sem_ref")
            continue
        preco_brl = o.price_current_cents / 100
        if not sel["price_min_brl"] <= preco_brl <= sel["price_max_brl"]:
            corta("faixa_preco")
            continue
        if (o.source, o.item_id) in ja_postados:
            corta("dedupe")
            continue
        result.append(o)
    piso = float(sel.get("min_ev_brl") or 0)
    if piso > 0:
        acima_do_piso = [o for o in result if ev_score(o, cfg) >= piso]
        cortes["ev"] = len(result) - len(acima_do_piso)
        result = acima_do_piso
    return result, FilterStats(**cortes)


def filter_offers(offers: list[Offer], db: StateDB, cfg: dict) -> list[Offer]:
    """Assinatura antiga: só a lista. O pipeline usa `filter_offers_with_stats`."""
    return filter_offers_with_stats(offers, db, cfg)[0]


def _teto_do_telegram(cfg: dict) -> int:
    """`channels.telegram.max_per_day` — a meta diária do canal. Entrada em
    bool (ou seção ausente) = sem teto, logo sem meta por fonte."""
    raw = (cfg.get("channels") or {}).get("telegram")
    if not isinstance(raw, dict):
        return 0
    try:
        return max(0, int(raw.get("max_per_day") or 0))
    except (TypeError, ValueError):
        return 0


def source_targets(cfg: dict, sources: list[str]) -> dict[str, int]:
    """Meta de ofertas por fonte no dia: `source_quota` × o teto diário do
    Telegram, normalizada entre as fontes LIGADAS (fase 5C, M2).

    Com as duas lojas ligadas e 60/dia, 50/50 dá 30 e 30. Com uma só, ela fica
    com 100% — a cota reparte um teto, não o reduz. Fonte ligada sem entrada em
    `source_quota` entra com peso igual às demais. Sem teto no Telegram não há
    meta: `{}`, e a fila volta a ser puro ranking."""
    teto = _teto_do_telegram(cfg)
    if teto <= 0 or not sources:
        return {}
    quotas_cfg = (cfg.get("selection") or {}).get("source_quota") or {}
    pesos: dict[str, float] = {}
    for nome in sources:
        try:
            peso = float(quotas_cfg.get(nome, 1.0))
        except (TypeError, ValueError):
            peso = 1.0
        pesos[nome] = max(0.0, peso)
    total = sum(pesos.values())
    if total <= 0:
        return {}
    return {nome: round(teto * peso / total) for nome, peso in pesos.items()}


def next_index_by_quota(fila: list[Offer], metas: dict[str, int],
                        publicados: dict[str, int]) -> int | None:
    """Índice da próxima oferta a publicar: a melhor ranqueada da fonte que
    está proporcionalmente MAIS ATRÁS da meta do dia (`publicados/meta`).

    O desempate pela razão é o que INTERCALA as lojas (menor da revisão da 5C):
    a versão anterior devolvia a primeira da fila cuja fonte estivesse abaixo da
    meta — enquanto as duas estivessem, isso era sempre o índice 0, e a loja
    mais bem ranqueada publicava as 30 dela seguidas antes de a outra aparecer
    no canal. A razão (e não o número absoluto) é o que faz metas diferentes se
    comportarem: 10 de 40 está mais adiantada que 4 de 20.

    Nenhuma fonte abaixo da meta -> a primeira da fila (uma fonte completa a
    outra: a cota reparte o teto, nunca o deixa ocioso). Fila vazia -> None.
    Sem metas, é sempre a ordem do ranking."""
    if not fila:
        return None
    atrasadas = {nome: publicados.get(nome, 0) / meta
                 for nome, meta in metas.items()
                 if meta > 0 and publicados.get(nome, 0) < meta}
    if not atrasadas:
        return 0
    menor = min(atrasadas.values())
    preferidas = {nome for nome, razao in atrasadas.items() if razao == menor}
    for i, offer in enumerate(fila):
        if offer.source in preferidas:
            return i
    # A fonte preferida não tem candidata nesta fila: vale qualquer outra que
    # ainda esteja abaixo da meta, e só então o topo do ranking.
    for i, offer in enumerate(fila):
        if offer.source in atrasadas:
            return i
    return 0


def _desconto_alegavel(offer: Offer, cfg: dict | None = None) -> int:
    """O desconto que o post PODE alegar (`pricing.verdict`) — 0 em modo B.

    O bônus de ranking usava o `real_discount_pct` cru: um item com 27%
    verificáveis mas em modo B (sem p25, janela curta, ou abaixo do mínimo)
    subia na fila por um desconto que o post dele nunca vai dizer. Só desconto
    ALEGÁVEL ranqueia."""
    sel = (cfg or {}).get("selection") or {}
    minimo = int(pricing.setting(sel, "min_real_discount_pct",
                                 pricing.DEFAULT_MIN_REAL_DISCOUNT_PCT))
    return pricing.verdict(offer, minimo).discount_pct


def ev_score(offer: Offer, cfg: dict, watchlist: Watchlist | None = None) -> float:
    """Retorno esperado por post: comissão em R$ (AMORTECIDA) ponderada pela
    popularidade.

    A8: com a comissão crua, o fator dela variava 50× (R$ 20 → R$ 1.000) contra
    2,5× de popularidade e 1,5× de desconto, e o LLM via só os 30 itens mais
    caros. `commission_brl ** commission_exp` (0,7) põe os três fatores na mesma
    ordem de grandeza; expoente 1,0 devolve o comportamento anterior.

    O que o expoente FAZ, medido (I-4 da revisão da 5C — a versão anterior
    deste comentário prometia o que ele não entrega): ele ENCURTA a distância e
    move o cruzamento, não inverte pares distantes. A câmera de R$ 800 a 3% com
    100 vendas continua ganhando da creatina de R$ 30 a 10% com 50 mil vendas
    (14,81 × 5,20) — só que por 2,8× em vez de 5,3×. Contra essa creatina, o
    item de 100 vendas precisava passar de R$ 4,51 de comissão para ganhar;
    com 0,7 precisa passar de R$ 5,38. A inversão só aparece em pares mais
    próximos: R$ 12,50 com 90 mil vendas × R$ 30,00 com 2 vendas vira 14,57 ×
    12,36 (com a comissão crua era 31,08 × 34,29, e o de 2 vendas ganhava).
    Quem garante que o campeão de VENDAS chega ao LLM é o recorte por vendas do
    `build_slate`, não este expoente. Os três casos estão em
    `tests/test_selection.py`."""
    w = cfg["selection"].get("ev_weights") or {}
    wp = float(w.get("popularity", 0.3))
    expoente = float(pricing.setting(w, "commission_exp", DEFAULT_COMMISSION_EXP))
    commission_brl = offer.commission_brl or (
        (offer.price_current_cents / 100) * (offer.commission_pct / 100))
    base = commission_brl ** expoente if commission_brl > 0 else 0.0
    score = base * (1 + wp * math.log10(offer.sales + 1))
    # Bônus só pelo desconto que o VEREDITO autoriza alegar — o "de" inflado
    # do vendedor não vale nada aqui, e o desconto que a régua proíbe alegar
    # também não.
    wd = float(w.get("discount", 0.5))
    score *= (1 + wd * _desconto_alegavel(offer, cfg) / 100)
    if watchlist is not None:
        score *= watchlist.boost_for(offer)
    return score


def order_by_ev(offers: list[Offer], cfg: dict, watchlist: Watchlist | None = None) -> list[Offer]:
    return sorted(offers, key=lambda o: ev_score(o, cfg, watchlist), reverse=True)


def build_slate(candidates: list[Offer], cfg: dict,
                watchlist: Watchlist | None = None) -> list[Offer]:
    """O que o ranker vê: a UNIÃO de três recortes das candidatas, alternando
    a origem — o melhor por valor esperado, o mais vendido, o de maior
    desconto alegável, e de novo (fase 5C, M3/A8).

    Três consequências desenhadas:
    - o campeão de vendas entra mesmo com EV baixo (a creatina de R$ 30 com
      50 mil vendas não some atrás de 30 câmeras);
    - nenhuma categoria ocupa mais que a sua fatia das vagas
      (`_limite_por_categoria`) — um item bloqueado pela cota cede o lugar ao
      próximo do MESMO recorte, e não a um item de outro critério;
    - a ORDEM já é o fallback determinístico: quando o LLM cai, `rank_offers`
      pega os primeiros daqui, que alternam origem em vez de repetir o topo
      do EV.

    E o slate SEMPRE enche até `MAX_CANDIDATES_FOR_PROMPT` quando há candidatas
    (I-5): os três recortes podem não dar 30 — o de desconto fica vazio num dia
    sem modo A, e categorias raras esgotam —, e o prompt promete 30 vagas.
    Sobrando espaço, ele é completado por EV: primeiro respeitando o limite por
    categoria, e só então ignorando-o. Slate menos diverso é ruim; slate de 4
    itens escolhidos entre 13.000, em silêncio, é pior.

    O recorte de desconto só considera o que o veredito autoriza alegar (modo
    A): ranquear por um desconto que o post não vai dizer é o mesmo erro que a
    5B tirou do `ev_score`. Categoria vazia (fonte que não informa) não entra
    na cota — senão TODAS as ofertas sem categoria disputariam 4 vagas."""
    recortes = [
        order_by_ev(candidates, cfg, watchlist),
        sorted(candidates, key=lambda o: o.sales, reverse=True),
        sorted((o for o in candidates if _desconto_alegavel(o, cfg) > 0),
               key=lambda o: _desconto_alegavel(o, cfg), reverse=True),
    ]
    posicoes = [0] * len(recortes)
    restantes = [SLATE_POR_CRITERIO] * len(recortes)
    escolhidos: list[Offer] = []
    vistos: set[tuple[str, str]] = set()
    por_categoria: dict[str, int] = {}
    limite = _limite_por_categoria(candidates)

    def cabe(offer: Offer) -> bool:
        if (offer.source, offer.item_id) in vistos:
            return False
        return not offer.category or por_categoria.get(offer.category, 0) < limite

    def escolhe(offer: Offer) -> None:
        vistos.add((offer.source, offer.item_id))
        if offer.category:
            por_categoria[offer.category] = por_categoria.get(offer.category, 0) + 1
        escolhidos.append(offer)

    while len(escolhidos) < MAX_CANDIDATES_FOR_PROMPT:
        rodada = False
        for i, recorte in enumerate(recortes):
            if restantes[i] <= 0 or len(escolhidos) >= MAX_CANDIDATES_FOR_PROMPT:
                continue
            while posicoes[i] < len(recorte):
                offer = recorte[posicoes[i]]
                posicoes[i] += 1
                if not cabe(offer):
                    continue
                escolhe(offer)
                restantes[i] -= 1
                rodada = True
                break
        if not rodada:
            break

    # Completa as vagas que sobraram (I-5): por EV, primeiro respeitando o
    # limite por categoria e só depois ignorando-o.
    for respeita_limite in (True, False):
        for offer in recortes[0]:
            if len(escolhidos) >= MAX_CANDIDATES_FOR_PROMPT:
                return escolhidos
            if (offer.source, offer.item_id) in vistos:
                continue
            if respeita_limite and not cabe(offer):
                continue
            escolhe(offer)
    return escolhidos


def _limite_por_categoria(candidates: list[Offer]) -> int:
    """Quantas vagas do slate uma mesma categoria pode ocupar: a fatia que
    cabe a ela, nunca menos que `MIN_POR_CATEGORIA_NO_SLATE`.

    `offer.category` é o id da categoria RAIZ (`selection.category_ids` tem 5),
    e não a subcategoria: um limite fixo de 4 tinha teto de 20 dos 30 lugares
    do prompt, e um estoque concentrado numa raiz dava um slate de 4. Ofertas
    sem categoria (fonte que não informa) não entram na conta — senão TODAS
    disputariam as mesmas vagas."""
    categorias = {o.category for o in candidates if o.category}
    if not categorias:
        return MAX_CANDIDATES_FOR_PROMPT
    return max(MIN_POR_CATEGORIA_NO_SLATE,
               math.ceil(MAX_CANDIDATES_FOR_PROMPT / len(categorias)))


def _rank_prompt(candidates: list[Offer], recent_titles: list[str], n: int,
                 watchlist: Watchlist | None = None, cfg: dict | None = None) -> str:
    linhas = "\n".join(
        f"- id={o.item_id} | {o.title} | categoria={o.category} | "
        f"desconto verificado={_desconto_alegavel(o, cfg)}% | vendas={o.sales} | "
        f"comissão=R${(o.price_current_cents / 100) * (o.commission_pct / 100):.2f} "
        f"({o.commission_pct:.1f}%)"
        + (" | em alta: sim" if watchlist is not None and o.item_id in watchlist.hot_items else "")
        for o in candidates)
    recentes = "\n".join(f"- {t}" for t in recent_titles) or "(nenhum)"
    return (
        "Você seleciona ofertas para um canal de promoções brasileiro (achadinhos).\n"
        f"Escolha as {n} melhores ofertas da lista, priorizando maior retorno esperado "
        "(comissão × chance de venda), apelo popular e variedade de categorias entre si "
        "e vs. posts recentes.\n"
        "Desconto 0% não é defeito — significa apenas que não há desconto "
        "verificado; o post desse item destaca prova social em vez de preço.\n"
        f"Candidatas:\n{linhas}\n\nPosts recentes:\n{recentes}\n\n"
        'Responda APENAS com JSON no formato {"chosen": ["id1", "id2", ...]}'
    )


def rank_offers(candidates: list[Offer], recent_titles: list[str], cfg: dict,
                watchlist: Watchlist | None = None) -> list[Offer]:
    n = cfg["selection"]["posts_per_run"]
    if len(candidates) <= n:
        return list(candidates)
    presented = build_slate(candidates, cfg, watchlist)
    data = llm.ask_json(_rank_prompt(presented, recent_titles, n, watchlist, cfg),
                        model=cfg["llm"]["model"])
    # Só uma LISTA vale: `{"chosen": null}` levantava TypeError fora de
    # qualquer try e derrubava o run a cada 5 min (A1); uma string ("id1")
    # seria iterada caractere a caractere. Qualquer outra forma cai no
    # ranking determinístico.
    chosen = data.get("chosen") if isinstance(data, dict) else None
    if isinstance(chosen, list):
        by_id = {o.item_id: o for o in presented}
        ids = list(dict.fromkeys(str(i) for i in chosen))
        picked = [by_id[i] for i in ids if i in by_id][:n]
        if len(picked) == n:
            return picked
    # Fallback determinístico: a própria união, na ordem em que ela alterna
    # EV → vendas → desconto (M3) — não o topo do EV outra vez.
    return presented[:n]

import math

from afiliado import llm
from afiliado.models import Offer
from afiliado.state import StateDB
from afiliado.watchlist import Watchlist

MAX_CANDIDATES_FOR_PROMPT = 30


def _allowed_categories(cfg: dict, source: str) -> set[str]:
    """IDs de categoria permitidos para uma fonte, a partir de
    `selection.category_ids`. Aceita lista (formato legado: vale para todas
    as fontes) ou dict por fonte (`{"shopee": [...], "meli": [...]}`). Vazio
    ou ausente = todas as categorias passam para aquela fonte."""
    raw = cfg["selection"].get("category_ids") or []
    if isinstance(raw, dict):
        raw = raw.get(source) or []
    return {str(c) for c in raw}


def filter_offers(offers: list[Offer], db: StateDB, cfg: dict) -> list[Offer]:
    sel = cfg["selection"]
    cats_by_source: dict[str, set[str]] = {}
    result = []
    for o in offers:
        if not (o.title and o.image_url and o.product_url):
            continue
        allowed_cats = cats_by_source.setdefault(o.source, _allowed_categories(cfg, o.source))
        if allowed_cats and o.category not in allowed_cats:
            continue
        # Régua honesta: o desconto não decide SE publicamos (isso mataria o
        # volume e o ML inteiro) — decide só o que o post ALEGA. O único corte
        # de preço é não anunciar algo mais caro que o típico.
        if o.price_ref_cents > 0 and (
                o.price_current_cents > o.price_ref_cents * float(sel["max_above_ref"])):
            continue
        if sel.get("require_price_ref") and o.price_ref_cents <= 0:
            continue
        preco_brl = o.price_current_cents / 100
        if not sel["price_min_brl"] <= preco_brl <= sel["price_max_brl"]:
            continue
        if db.was_posted_recently(o.source, o.item_id, sel["dedupe_days"]):
            continue
        result.append(o)
    piso = float(sel.get("min_ev_brl") or 0)
    if piso > 0:
        result = [o for o in result if ev_score(o, cfg) >= piso]
    return result


def ev_score(offer: Offer, cfg: dict, watchlist: Watchlist | None = None) -> float:
    """Retorno esperado por post: comissão em R$ ponderada pela popularidade."""
    w = cfg["selection"].get("ev_weights") or {}
    wp = float(w.get("popularity", 0.3))
    commission_brl = offer.commission_brl or (
        (offer.price_current_cents / 100) * (offer.commission_pct / 100))
    score = commission_brl * (1 + wp * math.log10(offer.sales + 1))
    # Bônus só por desconto VERIFICADO contra a nossa referência — o "de"
    # inflado do vendedor não vale nada aqui.
    wd = float(w.get("discount", 0.5))
    score *= (1 + wd * offer.real_discount_pct / 100)
    if watchlist is not None:
        score *= watchlist.boost_for(offer)
    return score


def order_by_ev(offers: list[Offer], cfg: dict, watchlist: Watchlist | None = None) -> list[Offer]:
    return sorted(offers, key=lambda o: ev_score(o, cfg, watchlist), reverse=True)


def _rank_prompt(candidates: list[Offer], recent_titles: list[str], n: int,
                 watchlist: Watchlist | None = None) -> str:
    linhas = "\n".join(
        f"- id={o.item_id} | {o.title} | categoria={o.category} | "
        f"desconto verificado={o.real_discount_pct}% | vendas={o.sales} | "
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
    presented = order_by_ev(candidates, cfg, watchlist)[:MAX_CANDIDATES_FOR_PROMPT]
    data = llm.ask_json(_rank_prompt(presented, recent_titles, n, watchlist),
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
    return order_by_ev(presented, cfg, watchlist)[:n]

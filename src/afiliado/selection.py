import math

from afiliado import llm
from afiliado.models import Offer
from afiliado.state import StateDB

MAX_CANDIDATES_FOR_PROMPT = 30


def filter_offers(offers: list[Offer], db: StateDB, cfg: dict) -> list[Offer]:
    sel = cfg["selection"]
    allowed_cats = {str(c) for c in sel.get("category_ids") or []}
    result = []
    for o in offers:
        if not (o.title and o.image_url and o.product_url):
            continue
        if allowed_cats and o.category not in allowed_cats:
            continue
        if o.discount_pct < sel["min_discount_pct"]:
            continue
        preco_brl = o.price_current_cents / 100
        if not sel["price_min_brl"] <= preco_brl <= sel["price_max_brl"]:
            continue
        if db.was_posted_recently(o.source, o.item_id, sel["dedupe_days"]):
            continue
        result.append(o)
    return result


def ev_score(offer: Offer, cfg: dict) -> float:
    """Retorno esperado por post: comissão em R$ ponderada pela popularidade."""
    w = cfg["selection"].get("ev_weights") or {}
    wp = float(w.get("popularity", 0.3))
    commission_brl = (offer.price_current_cents / 100) * (offer.commission_pct / 100)
    return commission_brl * (1 + wp * math.log10(offer.sales + 1))


def order_by_ev(offers: list[Offer], cfg: dict) -> list[Offer]:
    return sorted(offers, key=lambda o: ev_score(o, cfg), reverse=True)


def _rank_prompt(candidates: list[Offer], recent_titles: list[str], n: int) -> str:
    linhas = "\n".join(
        f"- id={o.item_id} | {o.title} | categoria={o.category} | "
        f"desconto={o.discount_pct}% | vendas={o.sales} | "
        f"comissão=R${(o.price_current_cents / 100) * (o.commission_pct / 100):.2f} "
        f"({o.commission_pct:.1f}%)"
        for o in candidates)
    recentes = "\n".join(f"- {t}" for t in recent_titles) or "(nenhum)"
    return (
        "Você seleciona ofertas para um canal de promoções brasileiro (achadinhos).\n"
        f"Escolha as {n} melhores ofertas da lista, priorizando maior retorno esperado "
        "(comissão × chance de venda), apelo popular e variedade de categorias entre si "
        "e vs. posts recentes.\n"
        f"Candidatas:\n{linhas}\n\nPosts recentes:\n{recentes}\n\n"
        'Responda APENAS com JSON no formato {"chosen": ["id1", "id2", ...]}'
    )


def rank_offers(candidates: list[Offer], recent_titles: list[str], cfg: dict) -> list[Offer]:
    n = cfg["selection"]["posts_per_run"]
    if len(candidates) <= n:
        return list(candidates)
    presented = order_by_ev(candidates, cfg)[:MAX_CANDIDATES_FOR_PROMPT]
    data = llm.ask_json(_rank_prompt(presented, recent_titles, n),
                        model=cfg["llm"]["model"])
    if isinstance(data, dict):
        by_id = {o.item_id: o for o in presented}
        ids = list(dict.fromkeys(str(i) for i in data.get("chosen", [])))
        picked = [by_id[i] for i in ids if i in by_id][:n]
        if len(picked) == n:
            return picked
    return order_by_ev(presented, cfg)[:n]

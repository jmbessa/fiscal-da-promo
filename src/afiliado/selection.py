from afiliado import llm
from afiliado.models import Offer
from afiliado.state import StateDB


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


def order_by_discount(offers: list[Offer]) -> list[Offer]:
    return sorted(offers, key=lambda o: o.discount_pct, reverse=True)


def _rank_prompt(candidates: list[Offer], recent_titles: list[str], n: int) -> str:
    linhas = "\n".join(
        f"- id={o.item_id} | {o.title} | categoria={o.category} | "
        f"desconto={o.discount_pct}% | vendas={o.sales}"
        for o in candidates)
    recentes = "\n".join(f"- {t}" for t in recent_titles) or "(nenhum)"
    return (
        "Você seleciona ofertas para um canal de promoções brasileiro (achadinhos).\n"
        f"Escolha as {n} melhores ofertas da lista, priorizando apelo popular, "
        "bom desconto e variedade de categorias entre si e vs. posts recentes.\n"
        f"Candidatas:\n{linhas}\n\nPosts recentes:\n{recentes}\n\n"
        'Responda APENAS com JSON no formato {"chosen": ["id1", "id2", ...]}'
    )


def rank_offers(candidates: list[Offer], recent_titles: list[str], cfg: dict) -> list[Offer]:
    n = cfg["selection"]["posts_per_run"]
    if len(candidates) <= n:
        return list(candidates)
    data = llm.ask_json(_rank_prompt(candidates, recent_titles, n),
                        model=cfg["llm"]["model"])
    if isinstance(data, dict):
        by_id = {o.item_id: o for o in candidates}
        ids = list(dict.fromkeys(str(i) for i in data.get("chosen", [])))
        picked = [by_id[i] for i in ids if i in by_id][:n]
        if len(picked) == n:
            return picked
    return order_by_discount(candidates)[:n]

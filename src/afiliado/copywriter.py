from afiliado import llm, validate
from afiliado.errors import ValidationError
from afiliado.models import CopyParts, Offer

# Sem desconto verificado contra a NOSSA referência, qualquer palavra de
# desconto é mentira — o "de" do vendedor não conta (ver afiliado.pricing).
SEM_DESCONTO_VERIFICADO = (
    "IMPORTANTE: este item NÃO tem desconto verificado. NÃO use palavras de "
    "desconto (off, baixou, caiu, promoção relâmpago, menor preço). Destaque "
    "utilidade, popularidade ou nota.\n"
)


def _copy_prompt(offer: Offer, cfg: dict) -> str:
    aviso = SEM_DESCONTO_VERIFICADO if offer.real_discount_pct == 0 else ""
    return (
        "Escreva a copy de um post de promoção para canal brasileiro de ofertas.\n"
        f"Produto: {offer.title}\nCategoria: {offer.category or 'geral'}\n"
        f"Desconto verificado: {offer.real_discount_pct}%\nTom: {cfg['copy']['tone']}\n"
        f"{aviso}"
        "NÃO inclua preço nem link — eles são adicionados pelo sistema.\n"
        "Responda APENAS com JSON: {\"headline\": \"até 60 chars, com 1 emoji\", "
        "\"description\": \"até 120 chars\", \"cta\": \"até 40 chars\"}"
    )


def fallback_copy(offer: Offer) -> CopyParts:
    if offer.real_discount_pct:
        return CopyParts(
            headline=f"🔥 Oferta: {offer.real_discount_pct}% OFF",
            description="Promoção por tempo limitado, aproveite enquanto dura.",
            cta="Garanta o seu 👇",
        )
    return CopyParts(
        headline="🔥 Achado do dia",
        description="Vale o clique: confira os detalhes e a avaliação.",
        cta="Garanta o seu 👇",
    )


def write_copy(offer: Offer, cfg: dict) -> CopyParts:
    for _ in range(2):
        data = llm.ask_json(_copy_prompt(offer, cfg), model=cfg["llm"]["model"])
        if not isinstance(data, dict):
            continue
        copy = CopyParts(
            headline=str(data.get("headline") or "").strip(),
            description=str(data.get("description") or "").strip(),
            cta=str(data.get("cta") or "").strip(),
        )
        try:
            validate.check_copy(copy)
        except ValidationError:
            continue
        return copy
    return fallback_copy(offer)

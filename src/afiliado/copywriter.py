import re

from afiliado import llm, validate
from afiliado.errors import ValidationError
from afiliado.models import CopyParts, Offer, Verdict

# Sem desconto verificado contra a NOSSA referência (modo B do veredito),
# qualquer palavra de desconto é mentira — o "de" do vendedor não conta e o
# `real_discount_pct` abaixo do mínimo também não (C10: a copy dizia "4% OFF"
# sobre um bloco de preço sem De/Por).
SEM_DESCONTO_VERIFICADO = (
    "IMPORTANTE: este item está SEM desconto verificado. NÃO use palavras de "
    "desconto (off, %, baixou, caiu, promoção, promoção relâmpago, menor preço). "
    "Destaque utilidade, popularidade ou nota.\n"
)
# O que o LLM não pode escrever em modo B — se escrever, a resposta é
# descartada como inválida (tenta de novo; depois cai no fallback neutro).
_PALAVRAS_DE_DESCONTO = re.compile(
    r"%|\b(?:off|baixou|caiu|promo(?:ção|cao)?|desconto|menor preço)\b", re.IGNORECASE)


def alega_desconto(copy: CopyParts) -> bool:
    """True se algum campo da copy usa palavra de desconto."""
    return any(_PALAVRAS_DE_DESCONTO.search(campo)
               for campo in (copy.headline, copy.description, copy.cta))


def _copy_prompt(offer: Offer, cfg: dict, verdict: Verdict) -> str:
    if verdict.mode == "A":
        regua = f"Desconto verificado: {verdict.discount_pct}%\n"
    else:
        regua = "SEM desconto verificado.\n" + SEM_DESCONTO_VERIFICADO
    return (
        "Escreva a copy de um post de oferta para canal brasileiro de achadinhos.\n"
        f"Produto: {offer.title}\nCategoria: {offer.category or 'geral'}\n"
        f"{regua}"
        f"Tom: {cfg['copy']['tone']}\n"
        "NÃO inclua preço nem link — eles são adicionados pelo sistema.\n"
        "Responda APENAS com JSON: {\"headline\": \"até 60 chars, com 1 emoji\", "
        "\"description\": \"até 120 chars\", \"cta\": \"até 40 chars\"}"
    )


def fallback_copy(verdict: Verdict) -> CopyParts:
    if verdict.mode == "A":
        return CopyParts(
            headline=f"🔥 Oferta: {verdict.discount_pct}% OFF",
            description="Promoção por tempo limitado, aproveite enquanto dura.",
            cta="Garanta o seu 👇",
        )
    return CopyParts(
        headline="🔥 Achado do dia",
        description="Vale o clique: confira os detalhes e a avaliação.",
        cta="Garanta o seu 👇",
    )


def write_copy(offer: Offer, cfg: dict, verdict: Verdict) -> CopyParts:
    """Copy do post a partir do veredito JÁ decidido (`pricing.verdict`). O
    `discount_pct` do vendedor e o `real_discount_pct` cru da oferta não
    entram aqui: só o que o veredito autoriza alegar."""
    for _ in range(2):
        data = llm.ask_json(_copy_prompt(offer, cfg, verdict), model=cfg["llm"]["model"])
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
        if verdict.mode != "A" and alega_desconto(copy):
            continue
        return copy
    return fallback_copy(verdict)

import html

from afiliado import pricing
from afiliado.models import CopyParts, Offer, Verdict


def build_message(offer: Offer, copy: CopyParts, link: str, verdict: Verdict) -> str:
    """Texto do post (HTML do Telegram). O bloco de preço tem dois modos e
    quem decidiu foi `pricing.verdict`, uma vez, antes de qualquer canal:
    modo A sai "De/Por" contra a NOSSA referência (riscada, preço em
    negrito); modo B sai só o preço em negrito, com a prova social em texto
    puro logo abaixo. O selo, quando o veredito o traz, é a última linha do
    bloco — o mesmo selo que a arte desenha e as legendas repetem. O "de" do
    vendedor (price_original_cents) nunca aparece."""
    linha_preco, prova_social = pricing.price_line_html(offer, verdict)
    bloco = [linha_preco]
    if prova_social:
        bloco.append(prova_social)
    if verdict.seal:
        bloco.append(verdict.seal)
    return (
        f"{html.escape(copy.headline)}\n"
        f"{html.escape(copy.description)}\n"
        f"\n"
        f"{html.escape(offer.title)}\n"
        + "\n".join(bloco) + "\n"
        f"\n"
        f"{html.escape(copy.cta)}\n"
        f"👉 {link}"
    )

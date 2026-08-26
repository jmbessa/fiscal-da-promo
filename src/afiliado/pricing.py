"""Régua honesta de preço (fase 4).

O desconto deixou de ser um PORTÃO (decidir se publicamos) e virou um RÓTULO
(decidir o que o post alega). Este módulo concentra as duas metades disso:

- a REFERÊNCIA própria (`enrich_offers`/`record_observations`): a mediana do
  nosso histórico de preços, não o "de" do vendedor — que é inflado (caso real:
  item que custa ~R$ 26 há 90 dias recebe um "de R$ 68,90" por um dia só);
- o TEXTO (`price_line`): modo A com "De/Por" quando o desconto é verificável
  contra a nossa referência, modo B com preço + prova social quando não é.

`price_line` é o único lugar que decide como o preço aparece — message.py,
channels/instagram_feed.py e creative.py consomem daqui.
"""

import dataclasses

from afiliado.models import Offer, format_brl
from afiliado.state import StateDB
from afiliado.watchlist import Watchlist

DEFAULT_REF_WINDOW_DAYS = 90
DEFAULT_REF_MIN_OBSERVATIONS = 5
DEFAULT_MIN_REAL_DISCOUNT_PCT = 10


def median_cents(valores: list[int]) -> int:
    """Mediana inteira (média dos dois centrais quando o total é par). 0 se vazio."""
    if not valores:
        return 0
    ordenados = sorted(valores)
    meio = len(ordenados) // 2
    if len(ordenados) % 2 == 1:
        return int(ordenados[meio])
    # Divisão inteira em centavos: nada de float em dinheiro. O truncamento
    # para baixo é conservador (referência menor = menos desconto alegado).
    return (int(ordenados[meio - 1]) + int(ordenados[meio])) // 2


def format_sales(sales: int) -> str:
    """>= 1000 -> '30 mil vendidos'; >= 1 -> '850 vendidos'; 0 -> ''."""
    if sales >= 1000:
        return f"{sales // 1000} mil vendidos"
    if sales >= 1:
        return f"{sales} vendidos"
    return ""


def _social_proof(offer: Offer) -> str:
    """Só o que é conhecido: nota (> 0) e vendas (> 0). Nada conhecido -> ''."""
    partes = []
    if offer.rating > 0:
        partes.append("⭐ " + f"{offer.rating:.1f}".replace(".", ","))
    vendas = format_sales(offer.sales)
    if vendas:
        partes.append(vendas)
    return " · ".join(partes)


def price_line(offer: Offer, min_real_discount_pct: int) -> tuple[str, str]:
    """Devolve (linha_de_preco, linha_de_prova_social) já formatadas em texto puro.

    Modo A (desconto verificado >= min_real_discount_pct):
        ("De: R$ 26,00 | Por: R$ 18,90 (27% OFF)", "")
    Modo B (sem referência, ou desconto abaixo do mínimo):
        ("R$ 33,90", "⭐ 4,9 · 30 mil vendidos")
    Nunca inventa desconto. A prova social só inclui o que é conhecido
    (rating > 0, sales > 0); se nada for conhecido, devolve string vazia."""
    desconto = offer.real_discount_pct
    # `desconto > 0` também cobre min_real_discount_pct=0: sem referência o
    # desconto verificado é 0 e o post NUNCA pode alegar "0% OFF".
    if desconto > 0 and desconto >= min_real_discount_pct:
        return (f"De: {format_brl(offer.price_ref_cents)} | "
                f"Por: {format_brl(offer.price_current_cents)} ({desconto}% OFF)", "")
    return format_brl(offer.price_current_cents), _social_proof(offer)


def record_observations(db: StateDB, offers: list[Offer]) -> None:
    """Registra o preço atual de cada oferta no price_log (um por dia)."""
    db.record_prices([(o.source, o.item_id, o.price_current_cents) for o in offers])


def enrich_offers(offers: list[Offer], db: StateDB, watchlist: Watchlist | None,
                  cfg: dict) -> list[Offer]:
    """Carimba price_ref_cents/price_floor_cents nas ofertas que ainda não têm.

    Ordem de precedência para a REFERÊNCIA (primeira que resolver vence):
      1. valor já presente na oferta (o ML traz do pool curado)
      2. watchlist.price_refs[item_id].ref_cents   (semente do JoomPulse)
      3. mediana do price_log do StateDB nos últimos cfg.selection.ref_window_days
         dias, exigindo >= cfg.selection.ref_min_observations dias distintos
      4. 0 (desconhecida — a oferta continua publicável, mas sem alegar desconto)

    Mesma ordem para o PISO (mínima histórica):
      1. valor já presente  2. watchlist.price_floors[item_id].min_price_cents
      3. menor preço do price_log na janela  4. 0

    O degrau 3 do piso exige as mesmas `ref_min_observations` da referência: o
    run de hoje já gravou o preço de hoje (ver `record_observations`), então um
    histórico de um dia só faria toda oferta parecer "menor preço já registrado".

    Usa dataclasses.replace (Offer é frozen)."""
    sel = cfg.get("selection") or {}
    janela = int(sel.get("ref_window_days") or DEFAULT_REF_WINDOW_DAYS)
    minimo_obs = int(sel.get("ref_min_observations") or DEFAULT_REF_MIN_OBSERVATIONS)

    resultado: list[Offer] = []
    for offer in offers:
        ref, piso = offer.price_ref_cents, offer.price_floor_cents
        historico: list[int] | None = None

        if ref <= 0 and watchlist is not None:
            wl_ref = watchlist.price_ref(offer.item_id)
            if wl_ref is not None and wl_ref.ref_cents > 0:
                ref = int(wl_ref.ref_cents)
        if ref <= 0:
            historico = db.price_history(offer.source, offer.item_id, janela)
            if len(historico) >= minimo_obs:
                ref = median_cents(historico)

        if piso <= 0 and watchlist is not None:
            wl_piso = watchlist.price_floor(offer.item_id)
            if wl_piso is not None and wl_piso.min_price_cents > 0:
                piso = int(wl_piso.min_price_cents)
        if piso <= 0:
            if historico is None:
                historico = db.price_history(offer.source, offer.item_id, janela)
            if len(historico) >= minimo_obs:
                piso = min(historico)

        if ref == offer.price_ref_cents and piso == offer.price_floor_cents:
            resultado.append(offer)
            continue
        resultado.append(dataclasses.replace(
            offer, price_ref_cents=ref, price_floor_cents=piso))
    return resultado

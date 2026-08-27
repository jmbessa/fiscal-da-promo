"""Régua honesta de preço (fase 4; fase 5B: "a régua diz a verdade").

O desconto deixou de ser um PORTÃO (decidir se publicamos) e virou um RÓTULO
(decidir o que o post alega). Este módulo concentra as três metades disso:

- a REFERÊNCIA própria (`enrich_offers`/`record_observations`): a mediana da
  janela de preços que nós (ou o JoomPulse) medimos — nunca o "de" do
  vendedor, que é inflado (item que custa ~R$ 26 há 90 dias recebe um
  "de R$ 68,90" por um dia só). Junto dela viajam o 25º percentil (p25) e
  o tamanho real da janela em dias distintos;
- o VEREDITO (`verdict`): a regra do quartil, aprovada pelo dono — o post só
  alega desconto quando o preço de hoje está ABAIXO do quartil mais barato da
  janela (`current < p25`, estrito: ver a docstring de `verdict`), a janela
  tem >= 14 dias distintos e o desconto contra a mediana atinge
  `min_real_discount_pct`. O selo de menor preço é ESTRITO (`current <=
  piso`, sem tolerância) e diz a janela que mediu;
- o TEXTO (`price_line`): modo A com "De/Por", modo B com preço + prova
  social — sempre a partir de um `Verdict` já decidido.

`verdict` é o ÚNICO lugar que decide o que o post alega. message.py,
creative.py, channels/instagram_feed.py, channels/story_dispatch.py e
copywriter.py recebem o `Verdict` pronto e não recalculam nada.
"""

import dataclasses

from afiliado.models import NO_CLAIM, Offer, Verdict, format_brl
from afiliado.state import StateDB
from afiliado.watchlist import Watchlist

DEFAULT_REF_WINDOW_DAYS = 90
# Regra do quartil: mediana e p25 só valem sobre >= 14 dias distintos — com
# 5 observações "mais da metade dos dias" eram 3 dias (C8).
DEFAULT_REF_MIN_OBSERVATIONS = 14
DEFAULT_MIN_REAL_DISCOUNT_PCT = 10
MIN_WINDOW_DAYS = 14

__all__ = ["Verdict", "NO_CLAIM", "verdict", "price_line", "price_line_html",
           "enrich_offers", "record_observations", "median_cents", "p25_cents",
           "window_text", "format_sales", "setting", "MIN_WINDOW_DAYS",
           "DEFAULT_REF_WINDOW_DAYS", "DEFAULT_REF_MIN_OBSERVATIONS",
           "DEFAULT_MIN_REAL_DISCOUNT_PCT"]


def setting(section: dict, key: str, default):
    """`section.get(key)` que honra `0`/`0.0`: só o valor AUSENTE (ou nulo)
    cai no default. `sel.get(k) or DEFAULT` transformava `min_real_discount_pct:
    0` em 10 e `ref_min_observations: 0` em 5 em silêncio (A11)."""
    value = section.get(key)
    return default if value is None else value


def median_cents(valores: list[int]) -> int:
    """Mediana inteira pelo método "menor dos dois centrais": com total par
    devolve o MENOR dos dois do meio — sempre um preço que existiu, nunca a
    média (R$ 47,45 de [26,00 ×3, 68,90 ×3] nunca foi preço de ninguém e
    virava o "De:" do post, C8). 0 se vazio."""
    if not valores:
        return 0
    ordenados = sorted(int(v) for v in valores)
    return ordenados[(len(ordenados) - 1) // 2]


def p25_cents(valores: list[int]) -> int:
    """25º percentil inteiro, sempre para baixo — a posição floor(0,25·(n−1))
    da lista ordenada, o topo do quartil mais barato da janela. 0 se vazio."""
    if not valores:
        return 0
    ordenados = sorted(int(v) for v in valores)
    return ordenados[(len(ordenados) - 1) // 4]


def window_text(days: int) -> str:
    """"45 dias", ou a partir de 60 dias "M meses" com M = dias // 30 (para
    baixo: o texto nunca promete uma janela maior do que a medida)."""
    if days >= 60:
        return f"{days // 30} meses"
    return f"{days} dias"


def verdict(offer: Offer, min_real_discount_pct: int) -> Verdict:
    """A regra, formalmente (única fonte de verdade):

    mode == "A" sse `price_ref_cents > 0` E `price_p25_cents > 0` E
    `price_window_days >= MIN_WINDOW_DAYS` E `current < p25` E
    `real_discount_pct >= min_real_discount_pct` (e > 0: sem referência o
    desconto verificado é 0 e o post nunca alega "0% OFF").

    O quartil é ESTRITO (`<`, não `<=`): preços são discretos e repetidos, e
    um preço que ocupa 40% dos dias É o próprio p25 — com `<=`, "68,90 em 54
    dias / 26,00 em 36" ganharia "62% OFF verificado", exatamente o padrão
    "tabela alta + promoção recorrente" que a regra existe para não
    certificar (C8). Com `<`, hoje precisa ser mais barato que o topo do
    quartil: promoção rara passa, promoção recorrente (>= 1/4 dos dias) e
    preço alternado caem.

    selo sse `price_floor_cents > 0` E `current <= price_floor_cents`
    (ESTRITO — a tolerância de 5% que dizia "menor preço já registrado" para
    um preço acima do registrado morreu aqui, C9) E a janela da mínima é
    conhecida (> 0): o texto diz "últimos N dias" e não pode inventar N."""
    seal, seal_days = "", 0
    if (offer.price_floor_cents > 0 and offer.price_floor_window_days > 0
            and offer.price_current_cents <= offer.price_floor_cents):
        seal_days = offer.price_floor_window_days
        seal = f"🏷️ Menor preço dos últimos {window_text(seal_days)} (verificado)"
    desconto = offer.real_discount_pct
    modo_a = (offer.price_ref_cents > 0 and offer.price_p25_cents > 0
              and offer.price_window_days >= MIN_WINDOW_DAYS
              and offer.price_current_cents < offer.price_p25_cents
              and desconto > 0 and desconto >= min_real_discount_pct)
    if modo_a:
        return Verdict("A", desconto, seal, seal_days)
    return Verdict("B", 0, seal, seal_days)


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


def price_line(offer: Offer, verdict: Verdict) -> tuple[str, str]:
    """Devolve (linha_de_preco, linha_de_prova_social) em texto puro, a
    partir do veredito JÁ decidido.

    Modo A:  ("De: R$ 26,00 | Por: R$ 18,90 (27% OFF)", "")
    Modo B:  ("R$ 33,90", "⭐ 4,9 · 30 mil vendidos")
    A prova social só inclui o que é conhecido (rating > 0, sales > 0)."""
    if verdict.mode == "A":
        return (f"De: {format_brl(offer.price_ref_cents)} | "
                f"Por: {format_brl(offer.price_current_cents)} ({verdict.discount_pct}% OFF)", "")
    return format_brl(offer.price_current_cents), _social_proof(offer)


def price_line_html(offer: Offer, verdict: Verdict) -> tuple[str, str]:
    """`price_line` com a marcação HTML do Telegram — mesmo veredito.

    Modo A: ("De: <s>R$ 26,00</s> | Por: <b>R$ 18,90</b> (27% OFF)", "")
    Modo B: ("<b>R$ 33,90</b>", "⭐ 4,9 · 30 mil vendidos") — o preço é o herói.
    `format_brl` não produz caractere especial de HTML: nada precisa de escape."""
    if verdict.mode == "A":
        return (f"De: <s>{format_brl(offer.price_ref_cents)}</s> | "
                f"Por: <b>{format_brl(offer.price_current_cents)}</b> ({verdict.discount_pct}% OFF)", "")
    return f"<b>{format_brl(offer.price_current_cents)}</b>", _social_proof(offer)


def record_observations(db: StateDB, offers: list[Offer]) -> None:
    """Registra o preço atual de cada oferta no price_log (um por dia)."""
    db.record_prices([(o.source, o.item_id, o.price_current_cents) for o in offers])


def enrich_offers(offers: list[Offer], db: StateDB, watchlist: Watchlist | None,
                  cfg: dict) -> list[Offer]:
    """Carimba referência (ref, p25, janela) e piso (mínima, janela) nas
    ofertas que ainda não têm.

    Precedência da REFERÊNCIA (o trio ref/p25/janela vem sempre do mesmo degrau):
      1. valor já presente na oferta (o ML traz do pool curado)
      2. watchlist.price_refs[item_id] -> ref_cents, p25_cents, window_days
         (semente do JoomPulse; entrada sem p25 carrega 0 -> nunca modo A)
      3. price_log do StateDB nos últimos cfg.selection.ref_window_days dias:
         ref = mediana, p25 = 25º percentil (os dois "para baixo"), janela =
         nº de dias distintos — exigindo >= cfg.selection.ref_min_observations
      4. 0 (desconhecida — a oferta continua publicável, sem alegar desconto)

    Mesma ordem para o PISO (mínima histórica + janela):
      1. já presente  2. watchlist.price_floors[item_id]  3. menor preço do
      price_log na janela, com os mesmos dias mínimos  4. 0

    O degrau 3 do piso exige as mesmas `ref_min_observations`: o run de hoje já
    gravou o preço de hoje (ver `record_observations`), então um histórico de
    um dia só faria toda oferta parecer "menor preço dos últimos 1 dias".

    O piso CURADO (degraus 1 e 2) ainda passa pelo price_log, mas só num
    sentido: a observação própria pode BAIXÁ-LO, nunca subi-lo, e para isso
    não exige `ref_min_observations` — um preço que nós vimos existiu, e negá-lo
    é que seria invenção. Sem isso o piso envelhecia sem limite (uma mínima
    curada há meses carimbava "menor preço dos últimos 12 meses" num preço que
    nós mesmos já tínhamos visto mais barato). Quando o piso desce, a janela do
    selo é a MAIOR das duas — a nossa medida cobre o que a curada cobria.

    Usa dataclasses.replace (Offer é frozen)."""
    sel = cfg.get("selection") or {}
    janela = int(setting(sel, "ref_window_days", DEFAULT_REF_WINDOW_DAYS))
    minimo_obs = int(setting(sel, "ref_min_observations", DEFAULT_REF_MIN_OBSERVATIONS))

    # O price_log de TODAS as ofertas em poucas consultas: com o estoque de
    # candidatas (fase 5C) isto era uma ida ao SQLite por oferta, milhares por
    # run. O conteúdo é o mesmo de `db.price_history` item a item.
    por_fonte: dict[str, list[str]] = {}
    for offer in offers:
        por_fonte.setdefault(offer.source, []).append(offer.item_id)
    historicos = {(fonte, item_id): serie
                  for fonte, ids in por_fonte.items()
                  for item_id, serie in db.price_histories(fonte, ids, janela).items()}

    resultado: list[Offer] = []
    for offer in offers:
        ref, p25, dias = offer.price_ref_cents, offer.price_p25_cents, offer.price_window_days
        piso, dias_piso = offer.price_floor_cents, offer.price_floor_window_days
        historico: list[int] | None = None

        if ref <= 0 and watchlist is not None:
            wl_ref = watchlist.price_ref(offer.item_id)
            if wl_ref is not None and wl_ref.ref_cents > 0:
                ref, p25, dias = int(wl_ref.ref_cents), int(wl_ref.p25_cents), int(wl_ref.window_days)
        if ref <= 0:
            historico = historicos.get((offer.source, offer.item_id), [])
            if len(historico) >= minimo_obs:
                ref, p25, dias = median_cents(historico), p25_cents(historico), len(historico)

        if piso <= 0 and watchlist is not None:
            wl_piso = watchlist.price_floor(offer.item_id)
            if wl_piso is not None and wl_piso.min_price_cents > 0:
                piso, dias_piso = int(wl_piso.min_price_cents), int(wl_piso.window_days)
        if historico is None:
            historico = historicos.get((offer.source, offer.item_id), [])
        if piso <= 0:
            if len(historico) >= minimo_obs:
                piso, dias_piso = min(historico), len(historico)
        elif historico and min(historico) < piso:
            # Piso curado que envelheceu: a observação própria só desce.
            piso, dias_piso = min(historico), max(dias_piso, len(historico))

        novo = dataclasses.replace(
            offer, price_ref_cents=ref, price_p25_cents=p25, price_window_days=dias,
            price_floor_cents=piso, price_floor_window_days=dias_piso)
        resultado.append(offer if novo == offer else novo)
    return resultado

"""Detector de flagrante (fase 5D): o "de" do vendedor que não se sustenta.

O dado já estava no `price_log` — o que faltava era a consulta e o
ranqueamento. Um produto é FLAGRANTE quando o desconto que o vendedor ALEGA
não tem como ser verdade contra o histórico que nós medimos:

1. `offer.discount_pct >= DESCONTO_ALEGADO_MINIMO` — o vendedor alega desconto
   grande (é o `discount_pct` do VENDEDOR, o "de" dele; a nossa régua é outra
   coisa e continua sendo `pricing.verdict`);
2. o nosso histórico tem pelo menos `selection.ref_min_observations` dias — sem
   isso não há o que provar, e nem gráfico para anexar;
3. `offer.price_original_cents > mediana × pricing.PICO_FATOR` — o "de" está
   muito acima do preço típico;
4. o preço passou no máximo `pricing.PICO_MAX_DIAS` dias acima desse mesmo
   limiar na janela — isto é, o "de" foi uma etiqueta, não um preço.

A `gravidade` = `(price_original_cents / mediana) × (desconto_alegado / 100)`
ordena do mais escandaloso para o menos.

**Só a Shopee tem `price_original_cents` de vendedor** — o Mercado Livre não
expõe "de" nenhum, então `discount_pct` é 0 e nenhuma oferta do ML passa no
gate 1. Na prática este módulo roda sobre ofertas da Shopee. A ausência do "de"
NÃO é erro: é só uma oferta que não é flagrante.

A mediana é lida de `offer.price_ref_cents` — nunca recalculada aqui. Quem a
decide é `pricing.enrich_offers`, uma vez, para o pipeline inteiro; é o que
garante que a linha que o gráfico desenha e o limiar que esta consulta usa
sejam o MESMO número.

Nada aqui publica: `cli.feed --tipo flagrante` manda a peça ao chat de
operações e espera o "ok" do dono. Nomear um vendedor específico com base em
dado automatizado é risco jurídico, e isso não se automatiza (`docs/feed.md`).
"""

from dataclasses import dataclass
from datetime import date

from afiliado import pricing
from afiliado.models import Offer
from afiliado.state import StateDB

# O vendedor precisa estar alegando desconto GRANDE: abaixo disso o "de" pode
# ser só desatualizado, e a acusação não se sustenta sozinha.
DESCONTO_ALEGADO_MINIMO = 30


@dataclass(frozen=True)
class Flagrante:
    offer: Offer
    historico: list[tuple[date, int]]   # pronto para `creative.render_grafico_preco`
    pico_cents: int                     # o preço inflado que o vendedor usou; 0 = nunca existiu
    dias_no_pico: int                   # dias observados acima de mediana × PICO_FATOR
    desconto_alegado_pct: int           # o que o vendedor anuncia
    gravidade: float                    # para ranquear


def _tempo_no_pico(historico: list[tuple[date, int]],
                   limiar: float) -> tuple[int, int]:
    """(dias observados acima do limiar, maior preço observado acima dele).

    `price_log` guarda um preço por dia, então contar pontos é contar DIAS.
    Pico 0 significa que aquele preço nunca existiu na nossa janela — o caso
    mais grave, e o que o gráfico mostra como uma linha reta sem pico nenhum."""
    acima = [cents for _, cents in historico if cents > limiar]
    return len(acima), max(acima, default=0)


def encontra(offers: list[Offer], db: StateDB, cfg: dict) -> list[Flagrante]:
    """Os flagrantes entre `offers`, do mais escandaloso para o menos.

    As ofertas precisam chegar com a régua já carimbada (`pricing.enrich_offers`):
    é de `offer.price_ref_cents` que sai a mediana, e sem ela não há acusação.
    O histórico datado vem do `price_log` na janela de `selection.ref_window_days`
    e viaja junto no `Flagrante` — é o mesmo que vai virar gráfico."""
    sel = cfg.get("selection") or {}
    janela = int(pricing.setting(sel, "ref_window_days", pricing.DEFAULT_REF_WINDOW_DAYS))
    minimo_obs = int(pricing.setting(sel, "ref_min_observations",
                                     pricing.DEFAULT_REF_MIN_OBSERVATIONS))

    achados: list[Flagrante] = []
    for offer in offers:
        mediana = offer.price_ref_cents
        alegado = offer.discount_pct
        if mediana <= 0 or alegado < DESCONTO_ALEGADO_MINIMO:
            continue
        limiar = mediana * pricing.PICO_FATOR
        if offer.price_original_cents <= limiar:
            continue                       # o "de" é plausível: desconto de verdade
        historico = db.price_history_dated(offer.source, offer.item_id, janela)
        if len(historico) < minimo_obs:
            continue                       # sem prova não há acusação
        dias, pico = _tempo_no_pico(historico, limiar)
        if dias > pricing.PICO_MAX_DIAS:
            continue                       # ficou dias lá em cima: é preço, não etiqueta
        achados.append(Flagrante(
            offer=offer, historico=historico, pico_cents=pico, dias_no_pico=dias,
            desconto_alegado_pct=alegado,
            gravidade=(offer.price_original_cents / mediana) * (alegado / 100),
        ))
    return sorted(achados, key=lambda f: f.gravidade, reverse=True)

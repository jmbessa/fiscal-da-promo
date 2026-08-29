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
  social — sempre a partir de um `Verdict` já decidido;
- o RÓTULO do preço (`sem_cupom`/`preco_publicado`, fase 5K): o número que
  publicamos da Shopee é o preço SEM CUPOM, e a peça podia dizê-lo. Ele existe
  e está DESLIGADO desde a fase 5N — ver `MOSTRAR_SEM_CUPOM`, que é o único
  lugar a mexer para religá-lo.

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

# Fase 5D — a régua do PICO INFLADO, que vale para o gráfico (creative) e para
# o detector de flagrante. Fica aqui, com o resto da régua, porque é uma regra
# de preço e não de desenho: um preço acima de `mediana × PICO_FATOR` que dure
# até `PICO_MAX_DIAS` é a etiqueta que o vendedor pendura para justificar o
# "de" — não é preço. O caso real que deu origem a ela: 89 dias a R$ 26,00 e
# UM dia a R$ 68,90, anunciado como "62% OFF".
PICO_FATOR = 1.5
PICO_MAX_DIAS = 2

# Fase 5K — o rótulo que diz QUE PREÇO é esse. Ver
# `docs/runbooks/shopee-preco.md`: a Shopee cobra um preço menor no Pix COM
# cupom e o exibe em vermelho grande, com o preço sem cupom em cinza pequeno.
# O desconto é de CHECKOUT e nenhuma das cinco superfícies da API de afiliados
# o expõe (todas medidas e fechadas no runbook), então o número que
# publicamos é — e sempre foi — o preço SEM CUPOM. Dizê-lo transforma a
# aparente contradição ("o anúncio está mais barato que o post") em serviço.
# A frase é verdadeira mesmo quando não há cupom nenhum disponível: o preço
# sem cupom é aquele. Ela NÃO afirma que existe cupom.
SEM_CUPOM = "sem cupom"

# DESLIGADO em 2026-08-28, decisão do dono, com o motivo dele: "o que importa
# para o usuário é se o produto está com o desconto, e ver isso atrai
# automaticamente; produto classificado como 'sem cupom' não atrai em nada".
#
# O que ele custa e o que ele compra, para quem for reavaliar:
#  - COMPRA: o seguidor que abre o anúncio e vê um preço MENOR entende por quê,
#    em vez de concluir que o nosso número está velho (foi o que aconteceu).
#  - CUSTA: é uma ressalva colada ao preço, e ela pesa mais justamente na peça
#    mais fraca — a de modo B, que não tem desconto para mostrar.
#
# Desligá-lo NÃO torna o post desonesto: R$ 689,99 é o preço que qualquer um
# paga sem cupom, e nunca afirmamos ser o menor jeito de pagar. É diferente do
# caso do ML, onde publicávamos o preço de um vendedor que não vence o buy box
# — lá o número não era o de ninguém.
# O risco que fica: o seguidor pode achar o produto mais barato do que o post
# diz. Erro para o lado que não frustra.
MOSTRAR_SEM_CUPOM = False

__all__ = ["Verdict", "NO_CLAIM", "verdict", "price_line", "price_line_html",
           "enrich_offers", "record_observations", "median_cents", "p25_cents",
           "window_text", "format_sales", "sales_window_text", "setting",
           "sem_cupom", "rotulo_do_preco", "preco_publicado", "SEM_CUPOM",
           "MIN_WINDOW_DAYS",
           "DEFAULT_REF_WINDOW_DAYS", "DEFAULT_REF_MIN_OBSERVATIONS",
           "DEFAULT_MIN_REAL_DISCOUNT_PCT", "PICO_FATOR", "PICO_MAX_DIAS"]


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

    `current` aqui é sempre o preço de CATÁLOGO (`price_current_cents`), mesmo
    quando a fase 5P leu um preço de checkout mais barato: a série que produziu
    a mediana e o p25 é de preços de catálogo, e comparar um preço de cupom com
    ela faria "abaixo do quartil mais barato" valer todo dia em que houvesse
    cupom. A leitura de checkout NUNCA abre uma alegação que o catálogo não
    ganhou — ela só muda o número (e o percentual) que a peça imprime.

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
        # O PORTÃO acima é decidido com o preço de CATÁLOGO, contra uma série
        # de preços de catálogo. O percentual que sai daqui é o do número
        # EXIBIDO (fase 5P: com leitura de checkout eles diferem), para que
        # "De: X | Por: Y (N% OFF)" feche a conta na peça.
        return Verdict("A", offer.published_discount_pct, seal, seal_days)
    return Verdict("B", 0, seal, seal_days)


def sales_window_text(window_days: int) -> str:
    """O complemento que diz QUE JANELA o número de vendas mede — "" para o
    contador vitalício (janela 0), que é o que o anúncio já exibe.

    30 dias vira "no último mês" e não "nos últimos 30 dias": foi a forma que
    o dono definiu quando o bug do ML apareceu ("5 mil vendidos no ultimo mes
    ou mais de 250 mil unidades vendidas"). Qualquer outra janela sai por
    extenso — o texto nunca chama de "mês" uma janela que não é."""
    if window_days <= 0:
        return ""
    if window_days == 30:
        return " no último mês"
    return f" nos últimos {window_text(window_days)}"


def format_sales(sales: int, faixa: bool = False, window_days: int = 0) -> str:
    """>= 1000 -> '30 mil vendidos'; >= 1 -> '850 vendidos'; 0 -> ''.

    `faixa=True` quando o número é um BALDE e não uma contagem: o Mercado Livre
    publica só a faixa e o anúncio escreve "+250 mil vendidos". Aí o texto sai
    com o "+", porque o que sabemos é "pelo menos isso".

    `window_days` é o que o número MEDE (`Offer.sales_window_days`): 0 = o
    contador vitalício do anúncio, 30 = o último mês -> "45 mil vendidos no
    último mês". Fase 5H: o `sales` da Shopee sempre foi a janela de ~30 dias
    (medido em 2026-08-28: 45.950 nossos contra os 2.000.000 que o anúncio
    exibe), e o texto o apresentava como se fosse o total. Os dois flags são
    independentes: um balde de 30 dias sairia "+45 mil vendidos no último
    mês"."""
    prefixo = "+" if faixa else ""
    sufixo = sales_window_text(window_days)
    if sales >= 1_000_000:
        milhoes = sales / 1_000_000
        inteiro = f"{milhoes:.0f}" if milhoes == int(milhoes) else f"{milhoes:.1f}".replace(".", ",")
        unidade = "milhão" if inteiro == "1" else "milhões"
        return f"{prefixo}{inteiro} {unidade} vendidos{sufixo}"
    if sales >= 1000:
        return f"{prefixo}{sales // 1000} mil vendidos{sufixo}"
    if sales >= 1:
        return f"{prefixo}{sales} vendidos{sufixo}"
    return ""


def _social_proof(offer: Offer) -> str:
    """Só o que é conhecido: nota (> 0) e vendas (> 0). Nada conhecido -> ''."""
    partes = []
    if offer.rating > 0:
        partes.append("⭐ " + f"{offer.rating:.1f}".replace(".", ","))
    vendas = format_sales(offer.sales, offer.sales_e_faixa, offer.sales_window_days)
    if vendas:
        partes.append(vendas)
    return " · ".join(partes)


def sem_cupom(offer: Offer, mostrar: bool | None = None) -> str:
    """`SEM_CUPOM` para a Shopee, "" para o resto — e "" para todos enquanto
    `MOSTRAR_SEM_CUPOM` estiver desligado (ver o porquê lá em cima).

    SÓ a Shopee: o preço que publicamos do Mercado Livre é o do anúncio que
    vence o buy box, que é exatamente o que a página mostra — rotular lá seria
    ruído sobre um preço que ninguém vai contestar. Este é o ÚNICO lugar que
    decide isso; arte (`creative`) e textos importam daqui.

    `mostrar` existe para o teste exercitar os DOIS estados sem mexer no
    módulo: quem chama em produção não passa nada."""
    if not (MOSTRAR_SEM_CUPOM if mostrar is None else mostrar):
        return ""
    return SEM_CUPOM if offer.source == "shopee" else ""


def rotulo_do_preco(offer: Offer) -> str:
    """A frase que qualifica o número publicado — UMA, nunca duas.

    Fase 5P: quando o navegador leu o preço de checkout, o rótulo é a condição
    que a página deu a ele ("com cupom", "no Pix com cupom") e ela tem
    precedência sobre o "sem cupom" da 5N em qualquer estado do interruptor —
    publicar "R$ 523,48 sem cupom" diria o contrário do que o número significa.

    Sem leitura, tudo é como na 5N: o "sem cupom" da Shopee, se ligado; nada no
    resto. Este é o ÚNICO lugar que decide qual dos dois é; arte, texto do
    Telegram e legendas importam daqui."""
    if offer.price_checkout_cents > 0:
        return offer.price_checkout_label
    return sem_cupom(offer)


def _com_rotulo(offer: Offer, preco: str) -> str:
    """O preço já marcado (texto puro ou `<b>…</b>`) com o rótulo colado
    depois dele — fora de qualquer marcação, porque o negrito é do NÚMERO."""
    rotulo = rotulo_do_preco(offer)
    return f"{preco} {rotulo}" if rotulo else preco


def preco_publicado(offer: Offer) -> str:
    """O preço como ele vai ao público: o número (`published_price_cents` — o de
    checkout quando houve leitura, o de catálogo quando não) e a frase que o
    qualifica. É o que a legenda do carrossel usa, e é por existir aqui que ela
    não reimplementa a regra."""
    return _com_rotulo(offer, format_brl(offer.published_price_cents))


def price_line(offer: Offer, verdict: Verdict) -> tuple[str, str]:
    """Devolve (linha_de_preco, linha_de_prova_social) em texto puro, a
    partir do veredito JÁ decidido.

    Modo A:  ("De: R$ 26,00 | Por: R$ 18,90 (27% OFF)", "")
    Modo B:  ("R$ 33,90", "⭐ 4,9 · 30 mil vendidos")
    A prova social só inclui o que é conhecido (rating > 0, sales > 0).

    Com `MOSTRAR_SEM_CUPOM` ligado sai "R$ 18,90 sem cupom": o rótulo cola no
    preço ATUAL (nunca na referência riscada, que é uma mediana nossa e não um
    preço de checkout) e some fora da Shopee — a mesma colocação que a pill da
    arte usa, para que arte e texto não discordem."""
    if verdict.mode == "A":
        return (f"De: {format_brl(offer.price_ref_cents)} | "
                f"Por: {preco_publicado(offer)} ({verdict.discount_pct}% OFF)", "")
    return preco_publicado(offer), _social_proof(offer)


def price_line_html(offer: Offer, verdict: Verdict) -> tuple[str, str]:
    """`price_line` com a marcação HTML do Telegram — mesmo veredito.

    Modo A: ("De: <s>R$ 26,00</s> | Por: <b>R$ 18,90</b> (27% OFF)", "")
    Modo B: ("<b>R$ 33,90</b>", "⭐ 4,9 · 30 mil vendidos")
    Com o rótulo ligado ele entra depois do preço e FORA do negrito — o herói
    do bloco é o número.
    `format_brl` não produz caractere especial de HTML: nada precisa de escape."""
    preco = _com_rotulo(offer, f"<b>{format_brl(offer.published_price_cents)}</b>")
    if verdict.mode == "A":
        return (f"De: <s>{format_brl(offer.price_ref_cents)}</s> | "
                f"Por: {preco} ({verdict.discount_pct}% OFF)", "")
    return preco, _social_proof(offer)


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

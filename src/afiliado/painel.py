"""Fase 5V — o painel de observação: profundidade de série, não largura.

**O defeito que este módulo existe para consertar.** A régua honesta só entra
em modo A com `selection.ref_min_observations` (14) DIAS DISTINTOS do mesmo
item. A descoberta rotativa nunca ia entregar isso: ela é otimizada para
largura e faz isso bem — medido em 2026-08-31, **16.523 itens distintos em 6
dias** — mas na mesma medição **nenhum** item tinha sido visto nos 6 dias:

    5 dias distintos:     14 itens
    4 dias distintos:    397 itens
    3 dias distintos:  2.921 itens
    2 dias distintos:  6.539 itens
    1 dia   distinto:  6.666 itens

Ou seja, `price_refs` não estava esperando o tempo passar — ele estava
esperando uma coisa que não ia acontecer. Isso travava, ao mesmo tempo: o modo
A (0% dos posts hoje), o selo, o carrossel do termômetro reenquadrado, o
flagrante com dado real e o Reel do gráfico de histórico.

**O que o painel faz.** Mantém uma lista ESTÁVEL de itens e lê o preço deles
todo dia, sejam eles publicáveis naquele dia ou não. Em
`ref_min_observations` dias, esses itens têm régua medida por nós — sem
JoomPulse, sem cota de terceiro, sem nada curado à mão.

**Por que estável.** Trocar os membros é voltar a medir largura. Item entra e
não sai, exceto por falhar `max_falhas` leituras SEGUIDAS — o que significa que
ele saiu da listagem de afiliados e não volta.

**O custo, medido contra o que já se sabe.** Uma leitura é uma chamada
(`refresh_price`, um `itemId` por vez). Um painel de 200 itens custa 200
chamadas/dia contra as ~608 que o pipeline já faz — e a VPS da 5C rodava a
~1.920/dia sem um único 429.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from afiliado.errors import SourceError
from afiliado.state import StateDB
from afiliado.watchlist import Watchlist

__all__ = ["Resultado", "config_de", "completa", "observa", "progresso", "FONTE"]

# Só a Shopee. O pool do Mercado Livre é CURADO e pequeno (52 anúncios), e o
# preço dele já vem com referência do próprio pool — gastar leitura diária ali
# seria pagar por uma régua que já existe.
FONTE = "shopee"

PADRAO = {
    "enabled": True,
    # 200 itens: cabe folgado no orçamento de chamadas e é mais do que a fila
    # que o pipeline publica num dia (~60). Subir isto não acelera a régua de
    # um item — o que a acelera é o TEMPO, e ele é o mesmo para 20 ou 2.000.
    "tamanho": 200,
    # Três leituras seguidas falhando: um item pode sumir da listagem por um
    # dia (a Shopee rotaciona a própria oferta de afiliado) e voltar. Sair na
    # primeira falha esvaziaria o painel a cada soluço da API.
    "max_falhas": 3,
}


def config_de(cfg: dict) -> dict:
    return {**PADRAO, **(cfg.get("painel") or {})}


@dataclass
class Resultado:
    """O que a passada do dia fez — é o que o comando imprime e o que o teste
    lê. Nenhum campo é estimativa: todos são contagem."""
    entraram: int = 0
    saíram: list[str] = field(default_factory=list)
    lidos: int = 0
    falharam: int = 0
    tamanho: int = 0
    prontos: int = 0          # itens que já alcançaram o mínimo da régua
    minimo: int = 0
    avisos: list[str] = field(default_factory=list)


def completa(db: StateDB, cfg: dict, watchlist: Watchlist | None,
             fonte: str = FONTE) -> int:
    """Enche o painel até `tamanho` com o topo da fila que o pipeline
    PUBLICARIA, e devolve quantos entraram.

    A fila é a mesma do `/shopee-regua-refresh` e do preço de checkout
    (`shopee_regua.fila`): estoque vivo, portões do run, ordem de EV. É de
    propósito — a régua que vale a pena construir é a dos itens que a gente
    publicaria, não a de um item qualquer.

    Não toca a rede: o estoque de candidatas já está no `state.db`.
    """
    from afiliado import shopee_regua        # noqa: PLC0415 - ciclo: shopee_regua
                                             # importa pipeline, que importa este.
    tamanho = int(config_de(cfg)["tamanho"])
    faltam = tamanho - len(db.painel(fonte))
    if faltam <= 0:
        return 0
    ja_estao = {i for i, _, _ in db.painel(fonte)}
    novos = [o.item_id for o in shopee_regua.fila(db, cfg, watchlist, fonte)
             if o.item_id not in ja_estao][:faltam]
    return db.painel_incluir(fonte, novos)


def observa(db: StateDB, source, cfg: dict, fonte: str = FONTE,
            dry_run: bool = False) -> tuple[int, int]:
    """Lê o preço vivo de cada item do painel e grava no `price_log`.

    Devolve `(lidos, falharam)`. Um item que saiu da listagem levanta
    `SourceError` — ele conta como falha e é o próprio `refresh_price` quem
    diz isso; o painel não adivinha.

    **Grava um por um, e não em lote no fim.** A passada são centenas de
    chamadas de rede: se ela morrer no meio (máquina desligada, token
    expirado), o que já foi lido tem de estar no banco. Uma série de preço com
    um buraco é pior do que uma série mais curta.
    """
    from afiliado.models import Offer        # noqa: PLC0415 - só para o molde

    lidos = falharam = 0
    for item_id, _entrou, _falhas in db.painel(fonte):
        molde = Offer(source=fonte, item_id=item_id, title="",
                      price_original_cents=0, price_current_cents=0,
                      commission_pct=0.0, image_url="", product_url="")
        try:
            vivo = source.refresh_price(molde)
        except SourceError:
            falharam += 1
            if not dry_run:
                db.painel_leitura(fonte, item_id, ok=False)
            continue
        if vivo.price_current_cents <= 0:
            falharam += 1
            if not dry_run:
                db.painel_leitura(fonte, item_id, ok=False)
            continue
        lidos += 1
        if not dry_run:
            db.record_price(fonte, item_id, vivo.price_current_cents)
            db.painel_leitura(fonte, item_id, ok=True)
    return lidos, falharam


def progresso(db: StateDB, cfg: dict, fonte: str = FONTE) -> tuple[int, int, int]:
    """`(tamanho, prontos, minimo)` — quantos itens o painel tem, quantos já
    alcançaram o mínimo de dias que a régua exige, e qual é esse mínimo.

    É a única medida honesta de "o painel está funcionando": chamada no dia 1
    ela devolve 0 prontos, e é isso mesmo que se espera.
    """
    from afiliado import pricing              # noqa: PLC0415 - evita ciclo no import
    sel = cfg.get("selection") or {}
    minimo = int(pricing.setting(sel, "ref_min_observations",
                                 pricing.DEFAULT_REF_MIN_OBSERVATIONS))
    janela = int(pricing.setting(sel, "ref_window_days",
                                 pricing.DEFAULT_REF_WINDOW_DAYS))
    itens = [i for i, _, _ in db.painel(fonte)]
    dias = db.dias_observados(fonte, itens, janela)
    return len(itens), sum(1 for n in dias.values() if n >= minimo), minimo

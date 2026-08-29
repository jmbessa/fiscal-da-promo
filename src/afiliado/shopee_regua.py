"""A régua da Shopee semeada com o histórico do JoomPulse (fase 5O).

O problema, medido em 2026-08-28: ZERO das 4.799 candidatas da Shopee podia
alegar desconto. A `data/watchlist.json` tinha 23 `price_floors` e nenhuma
`price_refs`, e o `price_log` próprio tinha 3 dias distintos contra os 14 que a
regra do quartil exige — o modo A só voltaria sozinho por volta de 11/09.

A tubulação já existia: `pricing.enrich_offers` lê
`watchlist.price_refs[item_id]` e `price_floors[item_id]` antes de recorrer ao
histórico próprio. O que faltava era ENCHER o arquivo. Este módulo faz a conta
que transforma as linhas do cubo `ShbModelsPricesDaily` em entradas dele, e as
guardas que decidem quando NÃO gravar. Quem roda a coleta é o skill
`/shopee-regua-refresh`, com o conector na sessão — aqui não há rede.

O grão do cubo é um INTERVALO `(itemId, priceStart, priceEnd, modelPrice)`, não
um ponto por dia. Duas consequências que este módulo trata:

- **expandir o intervalo em dias** é o que dá peso ao preço que durou 60 dias
  contra o que durou 1 (é assim que o "de" inflado do vendedor não vira
  referência);
- **dia sem linha é dia NÃO OBSERVADO** e não entra na janela. A janela é o que
  o selo publica ("menor preço dos últimos N dias"): esticá-la até a borda do
  intervalo seguinte seria mentir sobre quantos dias nós medimos.

Semear NÃO faz o modo A aparecer de imediato — a regra do quartil exige preço
abaixo do quartil mais barato, e na maioria dos dias ele não está. O que a
semeadura entrega desde o primeiro dia é o selo voltar a ser POSSÍVEL e,
principalmente, o `selection.max_above_ref` passar a filtrar de verdade: sem
referência, uma bicicleta a R$ 504,90 com típico de R$ 410,01 é publicada como
se fosse oferta, porque não há com o que comparar.
"""

import argparse
import json
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_FLOOR, Decimal, InvalidOperation
from pathlib import Path

from afiliado import joompulse, pipeline, pricing, selection
from afiliado.state import StateDB
from afiliado.watchlist import PriceFloor, PriceRef, Watchlist, load_watchlist

CUBO = "ShbModelsPricesDaily"
# O teto de linhas por consulta do cubo (medido em 2026-08-28). Vale como
# palpite quando a resposta salva não traz o `query.limit` dela.
LIMITE_PADRAO = joompulse.LIMITE_PADRAO
JANELA_PADRAO = pricing.DEFAULT_REF_WINDOW_DAYS
WATCHLIST_PADRAO = "data/watchlist.json"
SECOES_DE_FATO = ("price_refs", "price_floors")

__all__ = ["Regua", "centavos", "dias_observados", "regua_do_item", "reguas_do_bruto",
           "mesclar", "escrever", "alvos", "main", "JANELA_PADRAO", "CUBO"]


def centavos(valor) -> int | None:
    """BRL -> centavos com `Decimal` e ROUND_FLOOR; None para o que não é preço.

    Sempre para BAIXO, como no resto do projeto: referência ou piso arredondado
    para cima vira desconto (ou selo) inventado. Zero e negativo não são preço —
    devolvem None, e a linha é descartada em vez de virar uma mínima de R$ 0,00.
    """
    try:
        bruto = Decimal(str(valor)) * 100
    except (InvalidOperation, ValueError, TypeError):
        return None
    if bruto <= 0:
        return None
    return int(bruto.to_integral_value(rounding=ROUND_FLOOR))


# A data de um `priceStart`/`priceEnd`, e as linhas cruas: os dois formatos
# (colunar e lista de dicionários) são lidos em `afiliado.joompulse`, uma vez
# só, para os dois cubos.
_dia = joompulse.dia


def dias_observados(intervalos: list[tuple[date, date | None, int]], hoje: date,
                    janela_dias: int = JANELA_PADRAO) -> dict[date, int]:
    """Expande os intervalos nos DIAS que eles cobrem, dentro da janela.

    Os intervalos são `(início, fim, CENTAVOS)` — a conversão de BRL acontece
    uma vez só, em `centavos()`, na leitura da linha do cubo.

    Devolve `{dia: centavos}` — um dia por observação, e só por observação:
    dia sem linha simplesmente não está no mapa. O recorte na borda da janela
    acontece ANTES da contagem, para a janela nunca ser maior que o medido.

    Intervalo sem `priceEnd` (ausente ou ilegível) vale **um dia**, o do
    `priceStart`: a linha prova que o preço existiu naquele dia, e esticá-la
    até hoje seria inventar observação — que é o erro que este módulo existe
    para não cometer.

    Sobreposição não deveria existir; se existir, o dia fica com o preço MENOR.
    É a escolha conservadora nos três números: mediana e p25 menores autorizam
    menos desconto, e piso menor dificulta o selo.
    """
    primeiro = hoje - timedelta(days=max(1, janela_dias) - 1)
    dias: dict[date, int] = {}
    for inicio, fim, preco in intervalos:
        if inicio is None or preco is None or preco <= 0:
            continue
        fim = fim or inicio
        if fim < inicio:
            continue                                   # linha incoerente: fora
        de, ate = max(inicio, primeiro), min(fim, hoje)
        dia, preco = de, int(preco)
        while dia <= ate:
            atual = dias.get(dia)
            dias[dia] = preco if atual is None else min(atual, preco)
            dia += timedelta(days=1)
    return dias


@dataclass(frozen=True)
class Regua:
    """A régua de UM item: os três números e a janela que os mediu."""
    item_id: str
    ref_cents: int
    p25_cents: int
    min_cents: int
    window_days: int
    medido_em: date

    def price_ref(self) -> PriceRef:
        return PriceRef(self.ref_cents, self.window_days, self.p25_cents, self.medido_em)

    def price_floor(self) -> PriceFloor:
        return PriceFloor(self.min_cents, self.window_days, self.medido_em)


def regua_do_item(item_id: str, intervalos: list[tuple[date, date | None, int]],
                  hoje: date, janela_dias: int = JANELA_PADRAO,
                  min_dias: int = pricing.MIN_WINDOW_DAYS) -> tuple[Regua | None, str]:
    """A régua do item, ou `(None, motivo)`. As guardas são o valor da fase:

    - **sem linhas, sem entrada.** Silêncio, não zero: o item que o cubo não
      conhece não ganha régua nenhuma (e continua publicável, em modo B);
    - **menos de `min_dias` observados, sem entrada.** Régua curta é PIOR que
      régua ausente — ela autoriza alegação sobre uma janela que não mediu
      nada. `min_dias` é o mesmo `pricing.MIN_WINDOW_DAYS` da regra do quartil;
    - **mínima nunca acima do p25**, e **p25 nunca acima da referência**. Pela
      construção (os três saem da MESMA expansão) isso não acontece; a guarda
      é a mesma que o leitor do pool do ML aplica, e pelo mesmo motivo: mínima
      alta demais vira selo inventado.

    Mediana e percentil vêm de `pricing.median_cents`/`pricing.p25_cents` — a
    régua tem uma implementação só, e ela é a que o pipeline usa.
    """
    dias = dias_observados(intervalos, hoje, janela_dias)
    if not dias:
        return None, "sem linha no cubo na janela"
    if len(dias) < min_dias:
        return None, (f"janela curta: {len(dias)} dia(s) observado(s), "
                      f"mínimo {min_dias}")
    precos = list(dias.values())
    ref, p25, minimo = pricing.median_cents(precos), pricing.p25_cents(precos), min(precos)
    if p25 > ref:
        return None, "p25 acima da referência"
    if minimo > p25:
        return None, "mínima acima do p25"
    return Regua(item_id, ref, p25, minimo, len(dias), hoje), ""


def _campo(linha: dict, nome: str):
    return joompulse.campo(linha, CUBO, nome)


def intervalos_do_bruto(brutos: list) -> tuple[dict[str, list], int, set[str]]:
    """Agrupa as linhas cruas por item. Devolve `(por_item, ignoradas, cortados)`.

    O item aparece no mapa mesmo quando NENHUMA linha dele é legível — assim
    ele é recusado com motivo em vez de sumir do relatório.

    `cortados` é a defesa contra a página CHEIA. O grão do cubo é o intervalo e
    cabem ~4 itens em 100 linhas: a página cheia é o caso normal, e nela o
    ÚLTIMO item vem cortado no meio da série — faltam justamente os intervalos
    mais recentes, porque a ordem é `(itemId, priceStart)`. Uma régua feita
    disso mediria uma janela que não existiu. A página seguinte, quando existe,
    absolve o item: as linhas que faltavam chegam nela.
    """
    por_item: dict[str, list] = {}
    ignoradas = 0
    cortados: set[str] = set()
    for bruto in brutos:
        linhas, limite = joompulse.linhas(bruto)
        ultimo = ""
        for linha in linhas:
            item_id = str(_campo(linha, "itemId") or "").strip()
            if not item_id:
                ignoradas += 1
                continue
            ultimo = item_id
            cortados.discard(item_id)         # esta página continua o que faltava
            inicio = _dia(_campo(linha, "priceStart"))
            preco = centavos(_campo(linha, "modelPrice"))
            por_item.setdefault(item_id, [])
            if inicio is None or preco is None:
                ignoradas += 1
                continue
            por_item[item_id].append((inicio, _dia(_campo(linha, "priceEnd")), preco))
        if ultimo and len(linhas) >= limite:
            cortados.add(ultimo)
    return por_item, ignoradas, cortados


def reguas_do_bruto(brutos: list, hoje: date, janela_dias: int = JANELA_PADRAO,
                    min_dias: int = pricing.MIN_WINDOW_DAYS
                    ) -> tuple[dict[str, Regua], dict[str, str]]:
    """`(réguas aceitas, {item: motivo da recusa})` das respostas do cubo."""
    por_item, _, cortados = intervalos_do_bruto(brutos)
    aceitas: dict[str, Regua] = {}
    recusadas: dict[str, str] = {}
    for item_id, intervalos in por_item.items():
        if item_id in cortados:
            recusadas[item_id] = ("linhas cortadas no fim da página cheia — "
                                  "pagine com offset e junte as duas respostas")
            continue
        regua, motivo = regua_do_item(item_id, intervalos, hoje, janela_dias, min_dias)
        if regua is None:
            recusadas[item_id] = motivo
        else:
            aceitas[item_id] = regua
    return aceitas, recusadas


def _congela(secao: dict, data_da_secao: str) -> dict:
    """Carimba `measured_at` nas entradas que não têm, com a data que elas
    tinham por herança. Sem isto, avançar a data da seção envelheceria junto
    entradas medidas em dias diferentes — o piso de 23/08 passaria a dizer que
    foi medido hoje."""
    congelada = {}
    for item_id, entrada in secao.items():
        if isinstance(entrada, dict):
            entrada = {**entrada}
            entrada.setdefault("measured_at", data_da_secao)
        congelada[item_id] = entrada
    return congelada


def mesclar(atual: dict, reguas: dict[str, Regua], hoje: date) -> dict:
    """O conteúdo novo do watchlist.json: a MESMA opinião, a régua nova.

    `generated_at`, `category_boosts` e `hot_items` saem intactos — semear a
    régua não revisa boost nenhum, e regravar a data do arquivo afirmaria que
    sim. Quem passa a dizer "hoje" é `section_dates.price_refs/price_floors`,
    e as entradas que já estavam lá levam a data delas em `measured_at`.

    As entradas anteriores são preservadas (a semeadura é feita em ondas): um
    item semeado hoje sobrescreve o que ele tinha; os outros ficam onde estão.
    """
    novo = json.loads(json.dumps(atual))              # cópia; não mexe no original
    dia = hoje.isoformat()
    section_dates = dict(novo.get("section_dates") or {})
    for secao, entradas in (("price_refs", {k: r.price_ref() for k, r in reguas.items()}),
                            ("price_floors", {k: r.price_floor() for k, r in reguas.items()})):
        anterior = novo.get(secao)
        anterior = anterior if isinstance(anterior, dict) else {}
        herdada = section_dates.get(secao) or novo.get("generated_at") or dia
        novo[secao] = _congela(anterior, str(herdada))
        for item_id, entrada in entradas.items():
            novo[secao][item_id] = _entrada(entrada)
        section_dates[secao] = dia
    novo["section_dates"] = section_dates
    return novo


def _entrada(valor: PriceRef | PriceFloor) -> dict:
    if isinstance(valor, PriceRef):
        campos = {"ref_cents": valor.ref_cents, "p25_cents": valor.p25_cents,
                  "window_days": valor.window_days}
    else:
        campos = {"min_price_cents": valor.min_price_cents,
                  "window_days": valor.window_days}
    if valor.measured_at is not None:
        campos["measured_at"] = valor.measured_at.isoformat()
    return campos


def escrever(path: str | Path, conteudo: dict) -> None:
    Path(path).write_text(json.dumps(conteudo, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")


def alvos(db: StateDB, cfg: dict, watchlist: Watchlist | None, n: int = 36,
          fonte: str = "shopee") -> list[str]:
    """Os itens que valem uma consulta: o topo de `selection.order_by_ev` sobre
    o estoque vivo, sem quem já tem referência na watchlist.

    É a lista que o pipeline PUBLICARIA, medida com os mesmos portões do run
    (`filter_offers`) e a mesma ordem (`order_by_ev`) — e não `rank_offers`,
    que é dimensionado por `posts_per_run` e devolve um punhado. Não toca a
    rede: o estoque de candidatas já está no `state.db`.
    """
    ofertas = db.load_candidates(fonte, pipeline.candidate_max_age_days(cfg, fonte))
    ofertas = pricing.enrich_offers(ofertas, db, watchlist, cfg)
    ordenadas = selection.order_by_ev(selection.filter_offers(ofertas, db, cfg),
                                      cfg, watchlist)
    ja_tem = set(watchlist.price_refs) if watchlist else set()
    return [o.item_id for o in ordenadas if o.item_id not in ja_tem][:n]


def _semear(args, parser: argparse.ArgumentParser) -> int:
    watchlist = Path(args.watchlist)
    if not watchlist.exists():
        parser.error(f"watchlist não encontrada: {watchlist}")
    hoje = date.fromisoformat(args.hoje) if args.hoje else date.today()
    reguas, recusadas = reguas_do_bruto(joompulse.carrega(args.bruto), hoje,
                                        args.janela, args.min_dias)
    for item_id, regua in sorted(reguas.items()):
        print(f"✅ {item_id}: {regua.window_days} dias observados · "
              f"ref {regua.ref_cents} · p25 {regua.p25_cents} · mín {regua.min_cents}")
    for item_id, motivo in sorted(recusadas.items()):
        print(f"⏭️  {item_id}: {motivo}")
    if not reguas:
        print("nenhuma régua aceita — arquivo intocado")
        return 0
    if args.dry_run:
        print(f"(dry-run) {len(reguas)} régua(s) NÃO gravadas em {watchlist}")
        return 0
    atual = json.loads(watchlist.read_text(encoding="utf-8"))
    escrever(watchlist, mesclar(atual, reguas, hoje))
    print(f"{len(reguas)} régua(s) gravadas em {watchlist} "
          f"(section_dates.price_refs = {hoje.isoformat()})")
    return 0


def _alvos(args) -> int:
    from afiliado import cli                        # noqa: PLC0415 - só na CLI
    from afiliado.config import load_config

    cfg = load_config(args.config)
    watchlist = load_watchlist(args.watchlist)
    db = StateDB(cli.state_path(cfg))
    try:
        ids = alvos(db, cfg, watchlist, args.n, args.fonte)
    finally:
        db.close()
    for item_id in ids:
        print(item_id)
    print(json.dumps(ids, ensure_ascii=False))
    return 0


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="afiliado.shopee_regua",
                                description="régua da Shopee a partir do JoomPulse")
    sub = p.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("semear", help="lê os JSONs crus do cubo e grava a régua")
    ps.add_argument("bruto", nargs="+", help="arquivos ou diretórios com as respostas")
    ps.add_argument("--watchlist", default=WATCHLIST_PADRAO)
    ps.add_argument("--hoje", default="", help="AAAA-MM-DD (padrão: hoje)")
    ps.add_argument("--janela", type=int, default=JANELA_PADRAO)
    ps.add_argument("--min-dias", type=int, default=pricing.MIN_WINDOW_DAYS,
                    dest="min_dias")
    ps.add_argument("--dry-run", action="store_true")

    pa = sub.add_parser("alvos", help="os itens que o pipeline publicaria")
    pa.add_argument("--config", default="config.yaml")
    pa.add_argument("--watchlist", default=WATCHLIST_PADRAO)
    pa.add_argument("--n", type=int, default=36)
    pa.add_argument("--fonte", default="shopee")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    return _semear(args, parser) if args.cmd == "semear" else _alvos(args)


if __name__ == "__main__":                                    # pragma: no cover
    raise SystemExit(main())

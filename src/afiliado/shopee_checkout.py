"""A OITAVA rota do preço da Shopee: o cubo `ShbMartItem` do JoomPulse (fase 5R).

**O problema é o da 5P e não mudou.** A Shopee cobra dois preços — o de
catálogo, que a API de afiliados expõe e que publicamos, e o de CHECKOUT, menor,
que exige cupom (às vezes cupom mais Pix). A página põe o segundo em vermelho
grande. O dono abriu o anúncio, viu o número menor e concluiu que o nosso estava
velho.

**O que mudou é a ORIGEM do dado.** As sete rotas do
`docs/runbooks/shopee-preco.md` estão fechadas ou exigem sessão. A oitava não:
`ShbMartItem.price` do JoomPulse É o preço EXIBIDO, com cupom. Medido em
2026-08-29:

    item 16892189215 — nossa API R$ 689,99 · cubo 611,80 · a página: R$ 611,80
    item 23598844177 — nossa API R$ 599,00 · cubo 523,48 · a página: R$ 523,48

Sem navegador, sem sessão, sem risco para a conta do dono — e 100 itens por
consulta, contra os ~4 do cubo de histórico da 5O.

**A fase 5P não foi perdida.** O carimbo (`Offer.price_checkout_cents`), o
rótulo "com cupom", a precedência sobre o "sem cupom" da 5N e a renderização
continuam valendo, e a leitura do navegador continua ganhando desta quando
existir: ela é VIVA e ancorada na frase da própria página; esta é uma foto de
até três dias atrás.

**A guarda, que é o valor desta fase.** O cubo é reconstruído uma vez por dia,
mas a raspagem de um item NÃO é diária: medido em 2026-08-29 sobre 100 itens do
topo da nossa fila, `itemLastSeenDate` tinha mediana de 4 dias e máximo de 30 —
só 27 dos 100 tinham sido vistos nas últimas 24 h. O preço da API é vivo. Então
o preço do cubo só vai à peça quando ele é MENOR que o vivo, está a uma
distância sã dele e é RECENTE; fora disso publica-se o preço da API, como hoje.
Silêncio, não adivinhação — ver `avalia`, que é onde cada recusa tem nome.

Não há rede aqui. Quem consulta o conector é o skill `/shopee-checkout-refresh`,
com a sessão do dono; este módulo lê o bruto salvo, faz a conta e grava.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from afiliado import joompulse
from afiliado.models import Offer
from afiliado.preco_real import COM_CUPOM
from afiliado.pricing import setting
from afiliado.state import StateDB
from afiliado.watchlist import CheckoutPrice, Watchlist, load_watchlist

CUBO = "ShbMartItem"
SECAO = "checkout_prices"
WATCHLIST_PADRAO = "data/watchlist.json"

# A condição que o rótulo publica. É a MESMA constante da fase 5P — o rótulo
# não é reinventado aqui, só a origem do número é outra. E é "com cupom", não
# "no Pix com cupom": o cubo entrega o preço, não a frase que a página escreveu
# ao lado dele, e afirmar "no Pix" sem tê-la lido seria inventar a condição.
CONDICAO = COM_CUPOM

# -- os três limiares, com os números que os escolheram ------------------------
#
# Medido em 2026-08-29, 100 itens do topo da fila real (uma consulta), o preço
# do cubo contra o `price_current_cents` que o estoque tinha:
#
#   88 itens com o cubo MENOR; 12 com ele maior ou igual (esses não são
#   desconto nenhum — são preço velho de item que ficou mais barato);
#   dos 88, a distribuição tem um COTOVELO claro:
#
#     ... 14,03 · 14,26 · 14,50 ×6 · 14,57 | 15,34 · 16,13 · 16,99 · 17,77 ...
#
#   70 dos 88 (80%) ficam em até 15%, e eles se empilham nos degraus de cupom
#   da Shopee: 14 em 4,99–5,00%, 18 em 9,75–10,00%, 6 em exatamente 14,50%.
#   Acima do cotovelo os valores são espalhados (15,34; 16,13; 16,99; 17,77;
#   18,09; 18,27; 19,19; 19,54) e a cauda vai a 54,70% e 55,84% — R$ 199,90
#   virando R$ 88,27 não é cupom, é o preço tendo mudado desde a raspagem.
#
# A medição independente do dono, em 10 itens conferidos contra a tela, deu a
# mesma faixa: 5% a 14,5%.
#
# 15% é, portanto, o topo do degrau mais alto que sabemos existir. Errar por
# baixo custa uma peça sem o preço com cupom (ela publica o da API, como
# sempre); errar por cima custa prometer um preço que ninguém paga — e é este
# o erro que a conta não sobrevive.
GAP_MAX = 0.15
# Abaixo de 1% não é cupom: é ruído de arredondamento de uma medida `avg` ou o
# preço tendo mudado alguns centavos. Um "com cupom" colado a uma diferença de
# centavos afirma um cupom que provavelmente não existe. Nenhum dos 100 itens
# medidos é excluído por este piso — o menor gap positivo foi 1,45%.
GAP_MIN = 0.01
# A idade máxima da RASPAGEM (`itemLastSeenDate`), não do arquivo. O brief
# supunha 24 h ("o cubo é reconstruído uma vez por dia"); a medição desmentiu:
# o cubo é reconstruído todo dia, mas com a última raspagem de CADA item, e ela
# tinha mediana de 4 dias na amostra. 3 dias é o mesmo teto que o projeto já
# aceita para uma candidata (`shopee.candidate_max_age_days`), e cobre 36 dos
# 100 itens medidos contra 22 com o teto de 1 dia.
IDADE_MAX_DIAS = 3

# -- as recusas, cada uma com nome próprio -------------------------------------
FORA_DA_SHOPEE = ("só a Shopee cobra um preço de checkout diferente do de "
                  "catálogo — o do ML é o do anúncio que o nosso link abre")
SEM_ENTRADA = "o cubo não tem preço de checkout para este anúncio"
SEM_IDADE = ("a entrada não diz quando a Shopee foi raspada — sem a idade, o "
             "preço seria apresentado como se fosse de hoje")
VELHO = ("o preço do cubo foi raspado há {dias} dia(s), acima do teto de "
         "{teto} — o preço da API é vivo e vence")
NAO_E_MENOR = ("o preço do cubo não é menor que o preço vivo — não é desconto de "
               "checkout, é preço velho de um anúncio que ficou mais barato")
PERTO_DEMAIS = ("a diferença é menor que {piso:.0%} do preço vivo — é ruído de "
                "arredondamento, não cupom")
LONGE_DEMAIS = ("o preço do cubo está {gap:.1%} abaixo do vivo, além do teto de "
                "{teto:.0%} — nesta distância não é cupom, é o preço tendo mudado "
                "desde a raspagem")
JA_LIDO = ("a leitura do navegador (fase 5P) já carimbou o preço de checkout — "
           "ela é viva e ancorada na frase da página, e vence a foto do cubo")

AVISO_SEM_PRECO = (
    "⚠️ preco_checkout: ligado, mas NENHUM dos {total} preço(s) de "
    "`checkout_prices` está dentro do teto de {teto} dia(s) — os posts de hoje "
    "publicam o preço da API, como sempre. Rode /shopee-checkout-refresh")
AVISO_SECAO_VAZIA = (
    "⚠️ preco_checkout: ligado e a seção `checkout_prices` da watchlist está "
    "vazia — a coleta nunca rodou. Os posts publicam o preço da API, como "
    "sempre. Rode /shopee-checkout-refresh")

__all__ = ["PrecoDeCheckout", "precos_do_bruto", "pagina_cheia", "mesclar",
           "avalia", "aplica", "monta", "config_de", "alvos", "frescos", "main",
           "CUBO", "SECAO", "CONDICAO", "GAP_MAX", "GAP_MIN", "IDADE_MAX_DIAS",
           "FORA_DA_SHOPEE", "SEM_ENTRADA", "SEM_IDADE", "VELHO", "NAO_E_MENOR",
           "PERTO_DEMAIS", "LONGE_DEMAIS", "JA_LIDO"]


# -- R1: a coleta ---------------------------------------------------------------

@dataclass(frozen=True)
class PrecoDeCheckout:
    """O preço exibido de UM anúncio e o dia em que a Shopee foi raspada."""
    item_id: str
    price_cents: int
    medido_em: date

    def entrada(self) -> CheckoutPrice:
        return CheckoutPrice(self.price_cents, self.medido_em)


def _campo(linha: dict, nome: str):
    return joompulse.campo(linha, CUBO, nome)


def precos_do_bruto(brutos: list, hoje: date
                    ) -> tuple[dict[str, PrecoDeCheckout], dict[str, str]]:
    """`(preços aceitos, {item: motivo da recusa})` das respostas do cubo.

    O grão de `ShbMartItem` é o ITEM — uma linha por anúncio —, então não há o
    problema do último item cortado que o cubo de histórico tem (5O): a página
    cheia aqui só quer dizer que sobraram itens para a consulta seguinte, e é
    `pagina_cheia` que avisa disso a quem coleta.

    Item sem `itemId` é ignorado em silêncio (não há a quem atribuir a recusa);
    tudo o mais é recusado COM MOTIVO, para a coleta poder dizer o que caiu.
    """
    aceitos: dict[str, PrecoDeCheckout] = {}
    recusados: dict[str, str] = {}
    for bruto in brutos:
        linhas, _ = joompulse.linhas(bruto)
        for linha in linhas:
            item_id = str(_campo(linha, "itemId") or "").strip()
            if not item_id:
                continue
            preco = joompulse.centavos(_campo(linha, "price"))
            if preco is None:
                recusados[item_id] = "o cubo não devolveu um preço utilizável"
                continue
            visto = joompulse.dia(_campo(linha, "itemLastSeenDate"))
            if visto is None:
                recusados[item_id] = SEM_IDADE
                continue
            if visto > hoje:
                recusados[item_id] = (f"a raspagem está no futuro ({visto.isoformat()}) — "
                                      "relógio nosso ou do cubo fora de lugar")
                continue
            recusados.pop(item_id, None)
            aceitos[item_id] = PrecoDeCheckout(item_id, preco, visto)
    return aceitos, recusados


def pagina_cheia(brutos: list) -> bool:
    """Alguma das respostas veio no teto de linhas? Então sobraram itens para a
    consulta seguinte — pagine com `offset`. Nenhuma entrada é recusada por
    isto: o grão é o item, e o item que veio veio inteiro."""
    return any(len(linhas) >= limite
               for linhas, limite in (joompulse.linhas(b) for b in brutos))


def mesclar(atual: dict, precos: dict[str, PrecoDeCheckout], hoje: date) -> dict:
    """O conteúdo novo do watchlist.json com a seção `checkout_prices`.

    Mesma disciplina da 5O: `generated_at`, `category_boosts` e `hot_items`
    saem intactos (coletar preço de checkout não revisa boost nenhum), quem
    passa a dizer "hoje" é `section_dates.checkout_prices`, e cada entrada leva
    a data da RASPAGEM dela em `measured_at` — que aqui não é a data do run: é
    o `itemLastSeenDate` que o cubo devolveu.
    """
    novo = json.loads(json.dumps(atual))              # cópia; não mexe no original
    anterior = novo.get(SECAO)
    novo[SECAO] = dict(anterior) if isinstance(anterior, dict) else {}
    for item_id, preco in precos.items():
        novo[SECAO][item_id] = {"price_cents": preco.price_cents,
                                "measured_at": preco.medido_em.isoformat()}
    section_dates = dict(novo.get("section_dates") or {})
    section_dates[SECAO] = hoje.isoformat()
    novo["section_dates"] = section_dates
    return novo


# -- R2: a guarda, e é ela que decide -------------------------------------------

def avalia(offer: Offer, entrada: CheckoutPrice | None, hoje: date,
           gap_min: float = GAP_MIN, gap_max: float = GAP_MAX,
           idade_max_dias: int = IDADE_MAX_DIAS) -> tuple[int, str, int]:
    """`(centavos a publicar, motivo da recusa, idade do dado em dias)`.

    Publicar é o caso RARO: `price_cents > 0` só quando as seis portas abrem.
    A ordem é deliberada — primeiro o que não custa nada checar (fonte, entrada,
    leitura anterior), depois a idade (é ela que faz um preço velho parecer de
    hoje) e por último a distância, quando já existem os dois números.
    """
    if offer.source != "shopee":
        return 0, FORA_DA_SHOPEE, 0
    if offer.price_checkout_cents > 0:
        return 0, JA_LIDO, 0
    if entrada is None or entrada.price_cents <= 0:
        return 0, SEM_ENTRADA, 0
    if entrada.measured_at is None:
        return 0, SEM_IDADE, 0
    idade = (hoje - entrada.measured_at).days
    if idade > idade_max_dias:
        return 0, VELHO.format(dias=idade, teto=idade_max_dias), idade
    vivo = int(offer.price_current_cents)
    cubo = int(entrada.price_cents)
    if vivo <= 0 or cubo >= vivo:
        return 0, NAO_E_MENOR, idade
    gap = (vivo - cubo) / vivo
    if gap < gap_min:
        return 0, PERTO_DEMAIS.format(piso=gap_min), idade
    if gap > gap_max:
        return 0, LONGE_DEMAIS.format(gap=gap, teto=gap_max), idade
    return cubo, "", idade


def aplica(offer: Offer, entrada: CheckoutPrice | None, hoje: date,
           **limiares) -> tuple[Offer, str]:
    """`(oferta, motivo)`. A oferta sai carimbada só quando `avalia` abriu as
    portas; em todo o resto ela sai IDÊNTICA à que entrou — que é o pipeline de
    hoje. Nunca levanta, nunca inventa: é o mesmo contrato do
    `preco_real.LeitorDePreco.aplica`."""
    cents, motivo, _idade = avalia(offer, entrada, hoje, **limiares)
    if not cents:
        return offer, motivo
    return dataclasses.replace(offer, price_checkout_cents=cents,
                               price_checkout_label=CONDICAO), ""


class PrecoDoCubo:
    """O carimbador de UM run: a watchlist já carregada, a data de hoje e a
    conta de quantas ofertas ganharam preço de checkout.

    Ele é chamado para a oferta que VAI publicar, depois do `refresh_price` —
    o preço vivo é a âncora, e sem ele não há guarda.
    """

    def __init__(self, watchlist: Watchlist, hoje: date, limiares: dict):
        self.watchlist = watchlist
        self.hoje = hoje
        self.limiares = limiares
        self.aplicados = 0
        self.recusas: dict[str, int] = {}
        self.idade_maxima = 0
        self.warnings: list[str] = []
        self._avisa_se_nao_ha_o_que_publicar()

    def aplica(self, offer: Offer) -> tuple[Offer, str]:
        entrada = self.watchlist.checkout_price(offer.item_id)
        cents, motivo, idade = avalia(offer, entrada, self.hoje, **self.limiares)
        if not cents:
            self.recusas[motivo] = self.recusas.get(motivo, 0) + 1
            return offer, motivo
        self.aplicados += 1
        self.idade_maxima = max(self.idade_maxima, idade)
        return dataclasses.replace(offer, price_checkout_cents=cents,
                                   price_checkout_label=CONDICAO), ""

    def _avisa_se_nao_ha_o_que_publicar(self) -> None:
        """A coleta nunca rodou, ou parou? O run diz — uma vez por dia, pelo
        mesmo `warn_once` dos canais. Sem isto a seção envelheceria em silêncio
        e as peças voltariam ao preço de catálogo sem ninguém saber por quê,
        que é a classe de defeito que este projeto persegue desde a 5J."""
        entradas = self.watchlist.checkout_prices
        teto = self.limiares.get("idade_max_dias", IDADE_MAX_DIAS)
        if not entradas:
            self.warnings.append(AVISO_SECAO_VAZIA)
        elif not frescos(entradas, self.hoje, teto):
            self.warnings.append(AVISO_SEM_PRECO.format(total=len(entradas), teto=teto))


def frescos(entradas: dict, hoje: date, teto: int) -> set:
    """Os itens cuja raspagem ainda cabe no teto de idade. É a conta que o
    `doctor`, o aviso do run e a escolha dos alvos fazem — uma só."""
    return {item_id for item_id, e in entradas.items()
            if e.measured_at is not None and (hoje - e.measured_at).days <= teto}


def config_de(cfg: dict) -> dict:
    """A seção `preco_checkout:` do config.yaml, normalizada. Ausente =
    desligada, como o `preco_real` — o interruptor é explícito."""
    raw = cfg.get("preco_checkout")
    raw = dict(raw) if isinstance(raw, dict) else {"enabled": bool(raw)}
    return {
        "enabled": bool(raw.get("enabled", False)),
        "gap_min": float(setting(raw, "gap_min_pct", GAP_MIN * 100)) / 100,
        "gap_max": float(setting(raw, "gap_max_pct", GAP_MAX * 100)) / 100,
        "idade_max_dias": int(setting(raw, "idade_max_dias", IDADE_MAX_DIAS)),
    }


def monta(cfg: dict, watchlist: Watchlist | None, hoje: date) -> PrecoDoCubo | None:
    """`PrecoDoCubo` ou None — e None é o pipeline de hoje, inteiro.

    None quando o interruptor está desligado ou quando não há watchlist: sem o
    arquivo não há preço de checkout nenhum, e montar um carimbador que só sabe
    recusar só serviria para poluir o relatório do run.
    """
    opcoes = config_de(cfg)
    if not opcoes["enabled"] or watchlist is None:
        return None
    limiares = {k: opcoes[k] for k in ("gap_min", "gap_max", "idade_max_dias")}
    return PrecoDoCubo(watchlist, hoje, limiares)


# -- quais itens coletar --------------------------------------------------------

def alvos(db: StateDB, cfg: dict, watchlist: Watchlist | None, hoje: date,
          n: int = 100, fonte: str = "shopee") -> list[str]:
    """Os itens que valem uma consulta: o topo da fila que o pipeline
    PUBLICARIA, sem quem já tem um preço de checkout ainda dentro do teto de
    idade — reconsultá-lo não mudaria nada hoje.

    Não toca a rede: o estoque de candidatas já está no `state.db`. É a mesma
    fila do `/shopee-regua-refresh`, com outro filtro de "já tem".
    """
    from afiliado import shopee_regua           # noqa: PLC0415 - o pipeline importa
                                                #  este módulo; a fila importa o
                                                #  pipeline. O ciclo só não existe
                                                #  porque este import é preguiçoso.
    teto = config_de(cfg)["idade_max_dias"]
    servidos = frescos(watchlist.checkout_prices, hoje, teto) if watchlist else set()
    return [o.item_id for o in shopee_regua.fila(db, cfg, watchlist, fonte)
            if o.item_id not in servidos][:n]


# -- a CLI ----------------------------------------------------------------------

def _coletar(args, parser: argparse.ArgumentParser) -> int:
    watchlist = Path(args.watchlist)
    if not watchlist.exists():
        parser.error(f"watchlist não encontrada: {watchlist}")
    hoje = date.fromisoformat(args.hoje) if args.hoje else date.today()
    brutos = joompulse.carrega(args.bruto)
    precos, recusados = precos_do_bruto(brutos, hoje)
    for item_id, preco in sorted(precos.items()):
        idade = (hoje - preco.medido_em).days
        print(f"✅ {item_id}: {preco.price_cents} centavos · raspado em "
              f"{preco.medido_em.isoformat()} ({idade} dia(s))")
    for item_id, motivo in sorted(recusados.items()):
        print(f"⏭️  {item_id}: {motivo}")
    if pagina_cheia(brutos):
        print("ℹ️  alguma página veio no teto de linhas — sobraram itens para a "
              "consulta seguinte (pagine com offset)")
    if not precos:
        print("nenhum preço aceito — arquivo intocado")
        return 0
    if args.dry_run:
        print(f"(dry-run) {len(precos)} preço(s) NÃO gravados em {watchlist}")
        return 0
    from afiliado import shopee_regua           # noqa: PLC0415 - ver `alvos`
    atual = json.loads(watchlist.read_text(encoding="utf-8"))
    shopee_regua.escrever(watchlist, mesclar(atual, precos, hoje))
    print(f"{len(precos)} preço(s) de checkout gravados em {watchlist} "
          f"(section_dates.{SECAO} = {hoje.isoformat()})")
    return 0


def _alvos(args) -> int:
    from afiliado import cli                        # noqa: PLC0415 - só na CLI
    from afiliado.config import load_config

    cfg = load_config(args.config)
    watchlist = load_watchlist(args.watchlist)
    hoje = date.fromisoformat(args.hoje) if args.hoje else date.today()
    db = StateDB(cli.state_path(cfg))
    try:
        ids = alvos(db, cfg, watchlist, hoje, args.n, args.fonte)
    finally:
        db.close()
    for item_id in ids:
        print(item_id)
    print(json.dumps(ids, ensure_ascii=False))
    return 0


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="afiliado.shopee_checkout",
        description="o preço de checkout da Shopee a partir do cubo ShbMartItem")
    sub = p.add_subparsers(dest="cmd", required=True)

    pc = sub.add_parser("coletar", help="lê os JSONs crus do cubo e grava a seção")
    pc.add_argument("bruto", nargs="+", help="arquivos ou diretórios com as respostas")
    pc.add_argument("--watchlist", default=WATCHLIST_PADRAO)
    pc.add_argument("--hoje", default="", help="AAAA-MM-DD (padrão: hoje)")
    pc.add_argument("--dry-run", action="store_true")

    pa = sub.add_parser("alvos", help="os itens que o pipeline publicaria")
    pa.add_argument("--config", default="config.yaml")
    pa.add_argument("--watchlist", default=WATCHLIST_PADRAO)
    pa.add_argument("--hoje", default="", help="AAAA-MM-DD (padrão: hoje)")
    pa.add_argument("--n", type=int, default=100)
    pa.add_argument("--fonte", default="shopee")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    return _coletar(args, parser) if args.cmd == "coletar" else _alvos(args)


if __name__ == "__main__":                                    # pragma: no cover
    raise SystemExit(main())

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import partial

import httpx

from afiliado import copywriter, llm, message, pricing, selection, state, validate
from afiliado.channels.base import Channel
from afiliado.errors import SourceError
from afiliado.models import Post
from afiliado.sources.base import Source
from afiliado.state import StateDB
from afiliado.watchlist import Watchlist

# Fase 5A (C3): janela diária de operação, no fuso local. O teto de cada
# canal é distribuído ao longo dela (ver `pacing_budget`); fora dela o
# orçamento é 0 — um run de madrugada não publica.
DEFAULT_SCHEDULE = {"timezone": state.DEFAULT_TIMEZONE,
                    "window_start": "08:00", "window_end": "23:55"}


# Valores que variam entre descartes com o MESMO motivo ("R$ 33,90", "MLB123").
_VALOR = re.compile(r"R\$\s?[\d.,]+|\d+")
# Acima disto, descartes iguais viram uma linha só (C5: 37 linhas já
# estouravam os 4096 chars do Telegram e o resumo sumia em silêncio).
AGRUPA_DESCARTES_A_PARTIR_DE = 4
# Canal que falha tantas vezes SEGUIDAS no mesmo run (bot removido, chat id
# errado) fecha até o próximo run — a variante "canal falhando" do C2: sem
# isto cada oferta da fila pagava LLM + link para uma publicação que ia falhar.
MAX_FALHAS_SEGUIDAS_POR_CANAL = 3


def _motivo(descarte: str) -> str:
    """`"<rótulo>: <motivo>"` → motivo sem os valores que variam por item."""
    _, sep, resto = descarte.partition(": ")
    return " ".join(_VALOR.sub("", resto if sep else descarte).split())


@dataclass
class RunSummary:
    published: list[str] = field(default_factory=list)
    discarded: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def _linhas_de_descarte(self) -> list[str]:
        grupos: dict[str, list[str]] = {}
        for d in self.discarded:
            grupos.setdefault(_motivo(d), []).append(d)
        linhas: list[str] = []
        for motivo, itens in grupos.items():
            if len(itens) >= AGRUPA_DESCARTES_A_PARTIR_DE:
                exemplo = itens[0].partition(": ")[0]
                linhas.append(f"• {len(itens)}× {motivo} (ex.: {exemplo})")
            else:
                linhas += [f"• {d}" for d in itens]
        return linhas

    def text(self, header: str | None = None) -> str:
        linhas = [f"{header or '✅ Run concluído'} — Publicados ({len(self.published)}):"]
        linhas += [f"• {p}" for p in self.published] or ["• (nenhum)"]
        linhas.append(f"Descartados ({len(self.discarded)}):")
        linhas += self._linhas_de_descarte() or ["• (nenhum)"]
        if self.warnings:
            linhas.append("Avisos:")
            linhas += [f"• {w}" for w in self.warnings]
        return "\n".join(linhas)


class RunAborted(RuntimeError):
    """Nenhuma fonte devolveu nada E todas falharam (fase 5A, A4). Carrega o
    resumo — com os avisos de qual fonte falhou e por quê — para o cli
    mandar ao ops antes de sair com erro."""

    def __init__(self, summary: RunSummary, motivo: str):
        super().__init__(motivo)
        self.summary = summary


def schedule_settings(cfg: dict) -> dict:
    """Seção `schedule:` do config com os defaults de `DEFAULT_SCHEDULE`."""
    raw = cfg.get("schedule") or {}
    return {k: (raw.get(k) or v) for k, v in DEFAULT_SCHEDULE.items()}


def _minutos(hhmm: str) -> int:
    horas, minutos = str(hhmm).split(":")
    return int(horas) * 60 + int(minutos)


def pacing_budget(max_per_day: int, now_local: datetime,
                  window_start: str = DEFAULT_SCHEDULE["window_start"],
                  window_end: str = DEFAULT_SCHEDULE["window_end"]) -> int:
    """Quantos posts o canal PODE ter acumulado até `now_local` (hora local):

        min(max_per_day, floor(max_per_day × fração_decorrida) + 1)

    com fração_decorrida = (agora − início) / (fim − início) recortada em
    [0, 1]. Fora da janela → 0. Exemplos (60/dia, 08:00–23:55): 08:00 → 1,
    12:00 → 16, 23:55 → 60. Sem isso o teto era consumido pelos primeiros N
    slots do dia (feed do IG sempre no mesmo horário, 6 stories em bloco).
    A comparação é por minuto: um disparo às 23:55:20 ainda está na janela."""
    inicio, fim = _minutos(window_start), _minutos(window_end)
    agora = now_local.hour * 60 + now_local.minute
    if agora < inicio or agora > fim:
        return 0
    fracao = (agora - inicio) / (fim - inicio) if fim > inicio else 1.0
    return min(int(max_per_day), int(max_per_day * fracao) + 1)


class _Warner:
    """Todo aviso do run passa aqui (fase 5A, A3): entra em
    `summary.warnings` só na PRIMEIRA vez no dia local. A chave é o texto sem
    dígitos ("LLM indisponível em 3 de 4" e "N buscadas, 0 candidatas"
    colapsam), salvo chave explícita. Em dry-run não grava — e não esconde."""

    def __init__(self, db: StateDB, summary: RunSummary, dry_run: bool):
        self.db, self.summary, self.dry_run = db, summary, dry_run

    def __call__(self, texto: str, key: str | None = None) -> bool:
        chave = key if key is not None else re.sub(r"\d+", "", texto)
        if not self.dry_run and not self.db.warn_once(chave):
            return False
        self.summary.warnings.append(texto)
        return True


def _finish(summary: RunSummary, db: StateDB, dry_run: bool, sel: dict,
            warn: "_Warner") -> RunSummary:
    if llm.stats.falhas:
        warn(f"ℹ️ LLM indisponível em {llm.stats.falhas} de {llm.stats.chamadas} chamadas"
             " — ranking/copy de fallback")
    if not dry_run:
        db.record_run(len(summary.published), len(summary.discarded),
                      ref_window_days=int(pricing.setting(
                          sel, "ref_window_days", pricing.DEFAULT_REF_WINDOW_DAYS)))
    return summary


def run(cfg: dict, sources: list[Source], channels: list[Channel], db: StateDB,
        dry_run: bool = False, validator=None, watchlist: Watchlist | None = None,
        warnings_iniciais: list[str] | None = None) -> RunSummary:
    if validator is None:
        # Dry-run (A10): nada de rede além de fetch_offers/refresh_price —
        # a imagem não é baixada (o link já é checado offline, C6).
        validator = (partial(validate.validate_post, skip_image=True) if dry_run
                     else validate.validate_post)
    summary = RunSummary()
    warn = _Warner(db, summary, dry_run)
    sel = cfg["selection"]
    llm.stats.reset()

    # Heartbeat (fase 5A): o primeiro run do dia local diz bom dia com a
    # contagem de ontem. Vai SEMPRE ao ops (é um aviso, e aviso notifica) —
    # uma VPS morta deixa de ser indistinguível de "sem oferta boa".
    if not dry_run:
        ontem = db.day_stats(db.local_today() - timedelta(days=1))
        warn(f"☀️ Bom dia — ontem: {ontem.published} publicados, "
             f"{ontem.discarded} descartados em {ontem.runs} runs", key="heartbeat")

    # Avisos de quem montou fontes/canais (ex.: canal ligado sem env) — antes
    # eram só um print no journal e o chat de ops via "✅ Run concluído".
    for aviso in warnings_iniciais or []:
        warn(aviso)

    if watchlist is None:
        warn("ℹ️ Sem watchlist — ranking sem boosts")
    elif watchlist.is_stale():
        warn(f"⚠️ Watchlist vencida há {watchlist.days_old()} dias — rode /watchlist-refresh")
        watchlist = None

    offers = []
    fontes_com_falha = 0
    for src in sources:
        # Cada fonte isolada (A4): a Shopee em 5xx não derruba mais o ML.
        try:
            src_offers = src.fetch_offers(cfg)
        except (SourceError, httpx.HTTPError) as exc:
            fontes_com_falha += 1
            warn(f"⚠️ fonte {src.name} falhou: {exc}")
            continue
        pool_warning = getattr(src, "pool_warning", None) if src.name == "meli" else None
        if not src_offers:
            # Fonte HABILITADA devolvendo zero é evento, não silêncio (C4).
            if src.name == "meli":
                warn("⚠️ meli: 0 ofertas buscadas — "
                     + (pool_warning or "pool vazio ou vencido — rode /meli-links-refresh"))
            else:
                warn(f"⚠️ {src.name}: 0 ofertas buscadas")
        elif pool_warning:
            warn(f"ℹ️ meli: {pool_warning}")
        offers.extend(src_offers)

    if sources and fontes_com_falha == len(sources):
        # Só aqui o run aborta — e mesmo assim o resumo (com os avisos) vai
        # ao ops, via cli.
        raise RunAborted(summary, "todas as fontes falharam")

    # A observação de hoje entra no histórico ANTES de a mediana ser lida.
    # Dry-run não escreve no banco (A10).
    if not dry_run:
        pricing.record_observations(db, offers)

    # -- Fase 5A (C2/C3): há canal aberto para publicar? -----------------------
    # Orçamento por canal = teto diário distribuído pela janela (ritmo).
    # Canal sem max_per_day não tem ritmo nem teto. Sem canal aberto o run
    # termina AQUI: nenhuma oferta paga refresh_price, link, copy (LLM) ou
    # validação sem ter onde ser publicada — antes, com todos no teto, cada
    # run varria a fila inteira (195 chamadas LLM, 97 links, 0 posts).
    horario = schedule_settings(cfg)
    agora = db.local_now()
    orcamento: dict[str, int | None] = {}
    for ch in channels:
        cap = getattr(ch, "max_per_day", None)
        orcamento[ch.name] = None if cap is None else pacing_budget(
            int(cap), agora, horario["window_start"], horario["window_end"])
    usados: dict[str, int] = {}
    usados_dia = {ch.name: db.count_posts_today(ch.name)
                  for ch in channels if orcamento[ch.name] is not None}
    falhas_seguidas: dict[str, int] = {}
    fechados: set[str] = set()

    def aberto(ch) -> bool:
        if ch.name in fechados:
            return False
        orc = orcamento[ch.name]
        if orc is not None and usados_dia.get(ch.name, 0) >= orc:
            return False
        limit = getattr(ch, "max_per_run", None)
        return limit is None or usados.get(ch.name, 0) < limit

    def no_teto(ch) -> bool:
        cap = getattr(ch, "max_per_day", None)
        return cap is not None and usados_dia.get(ch.name, 0) >= int(cap)

    if not dry_run:
        if not channels:
            warn("⚠️ nenhum canal disponível — nada a publicar")
            return _finish(summary, db, dry_run, sel, warn)
        if not any(aberto(ch) for ch in channels):
            warn("ℹ️ teto diário atingido em todos os canais")
            return _finish(summary, db, dry_run, sel, warn)

    offers = pricing.enrich_offers(offers, db, watchlist, cfg)
    candidates, cortes = selection.filter_offers_with_stats(offers, db, cfg)
    if offers and not candidates:
        # O quinto zero silencioso (C4a): N entraram, 0 sobraram, e por quê.
        warn(f"⚠️ {len(offers)} ofertas buscadas, 0 candidatas — {cortes.resumo()}")
    ranked = selection.rank_offers(candidates, db.recent_titles(), cfg, watchlist)
    reserva = [o for o in selection.order_by_ev(candidates, cfg, watchlist) if o not in ranked]
    fila = ranked + reserva

    by_name = {s.name: s for s in sources}
    target = sel["posts_per_run"]
    count = 0
    tetos_atingidos: set[str] = set()

    for offer in fila:
        if count >= target:
            break
        # O rótulo definitivo só existe DEPOIS do refresh: antes dele o
        # desconto seria o do preço velho.
        rotulo = offer.title[:40]
        try:
            src = by_name[offer.source]
            refresh = getattr(src, "refresh_price", None)
            if refresh is not None:
                offer = refresh(offer)
            rotulo = (f"{offer.title[:40]} ({offer.real_discount_pct}% OFF)"
                      if offer.real_discount_pct else offer.title[:40])
            link = src.resolve_affiliate_link(offer)
            copy = copywriter.write_copy(offer, cfg)
            price_floor = watchlist.price_floor(offer.item_id) if watchlist is not None else None
            text = message.build_message(
                offer, copy, link, price_floor=price_floor,
                min_real_discount_pct=int(pricing.setting(
                    sel, "min_real_discount_pct", pricing.DEFAULT_MIN_REAL_DISCOUNT_PCT)),
                seal_tolerance=float(pricing.setting(
                    sel, "seal_tolerance", message.DEFAULT_SEAL_TOLERANCE)))
            post = Post(offer=offer, copy=copy, affiliate_link=link, message_text=text,
                        price_floor=price_floor)
            validator(post, cfg)
        except Exception as exc:
            summary.discarded.append(f"{rotulo}: {exc}")
            continue

        if dry_run:
            print(f"--- DRY-RUN: post que seria publicado ---\n{post.message_text}\n")
            summary.published.append(f"[dry] {rotulo}")
            count += 1
            continue

        published_any = False
        for ch in channels:
            if not aberto(ch):
                if no_teto(ch):
                    tetos_atingidos.add(ch.name)   # teto de verdade: vira aviso
                continue                            # ritmo/max_per_run: silêncio
            res = ch.publish(post)
            if res.ok:
                usados[ch.name] = usados.get(ch.name, 0) + 1
                if orcamento[ch.name] is not None:
                    usados_dia[ch.name] = usados_dia.get(ch.name, 0) + 1
                db.record_post(post, ch.name, res.message_id)
                published_any = True
                falhas_seguidas[ch.name] = 0
            else:
                summary.discarded.append(f"{rotulo}: publicação falhou em {ch.name}: {res.error}")
                falhas_seguidas[ch.name] = falhas_seguidas.get(ch.name, 0) + 1
                if falhas_seguidas[ch.name] >= MAX_FALHAS_SEGUIDAS_POR_CANAL:
                    fechados.add(ch.name)
                    warn(f"⚠️ {ch.name}: {MAX_FALHAS_SEGUIDAS_POR_CANAL} falhas seguidas — "
                         "canal fechado neste run")
        if published_any:
            summary.published.append(rotulo)
            count += 1
        if not any(aberto(ch) for ch in channels):
            break   # ninguém mais pode publicar: a próxima oferta não paga nada

    for ch in channels:
        if ch.name in tetos_atingidos:
            warn(f"ℹ️ {ch.name}: teto diário ({getattr(ch, 'max_per_day', None)}) atingido")

    return _finish(summary, db, dry_run, sel, warn)

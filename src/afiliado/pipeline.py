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
# Rodada de correção da 5C (C1): os dois freios da FILA de publicação.
# Com o estoque de candidatas, `fila` é o estoque inteiro (milhares) e
# `refresh_price` virou chamada de API real: um `SourceError` por oferta
# produzia um descarte e uma chamada por item, sem parar — medido, 5.000
# descartes e 5.000 chamadas num único run. Com o backoff de 0,5+1,5+4,0 s do
# `_post`, uma API que começa a limitar vira um martelo de horas contra a conta
# de afiliado. Espelham o freio de canal acima:
#   - `selection.max_descartes_por_run` encerra a fila no N-ésimo descarte
#     (qualquer motivo); 0 desliga o teto.
#   - `MAX_FALHAS_SEGUIDAS_POR_FONTE` erros de FONTE seguidos da mesma loja
#     tiram aquela loja da fila até o próximo run.
DEFAULT_MAX_DESCARTES_POR_RUN = 50
MAX_FALHAS_SEGUIDAS_POR_FONTE = 10
# Fase 5C (A12): canal `manual` (story_dispatch) entrega a arte ao chat de
# operações; quem posta no Instagram é o dono. Quando SÓ canais manuais
# aceitaram a oferta, ela vai para `summary.dispatched` — uma SEÇÃO própria do
# resumo — e não para `published`. A revisão pegou o A12 pela metade: o rótulo
# dizia "despachado" mas `len(summary.published)` e `day_stats().published`
# continuavam contando a arte como post, e o heartbeat da manhã relatava um dia
# melhor do que o dia foi.
DESPACHO_MANUAL = "📤 Despachados p/ ops — postar no app"


def _motivo(motivo: str) -> str:
    """Motivo sem os valores que variam por item (R$ …, dígitos) — a chave
    do agrupamento. O descarte é guardado como `(rótulo, motivo)`: dividir
    uma string única no primeiro ": " fazia o título "Kit: 3 peças" virar o
    motivo "peças: …" (revisão da 5A)."""
    return " ".join(_VALOR.sub("", motivo).split())


@dataclass
class RunSummary:
    published: list[str] = field(default_factory=list)
    # A12: ofertas que só foram para canais MANUAIS (arte no chat de ops).
    # Ainda não são posts — quem posta é o dono, à mão.
    dispatched: list[str] = field(default_factory=list)
    discarded: list[tuple[str, str]] = field(default_factory=list)   # (rótulo, motivo)
    warnings: list[str] = field(default_factory=list)
    # Fase 5C (M1): o que a fatia de descoberta deste run custou e rendeu.
    # NÃO é aviso (não passa pelo warn_once — cada run tem números próprios) e
    # NÃO faz o run notificar sozinho: só acompanha um resumo que já ia sair.
    discovery: list[str] = field(default_factory=list)

    def _linhas_de_descarte(self) -> list[str]:
        grupos: dict[str, list[tuple[str, str]]] = {}
        for rotulo, motivo in self.discarded:
            grupos.setdefault(_motivo(motivo), []).append((rotulo, motivo))
        linhas: list[str] = []
        for motivo, itens in grupos.items():
            if len(itens) >= AGRUPA_DESCARTES_A_PARTIR_DE:
                linhas.append(f"• {len(itens)}× {motivo} (ex.: {itens[0][0]})")
            else:
                linhas += [f"• {rotulo}: {m}" for rotulo, m in itens]
        return linhas

    def text(self, header: str | None = None) -> str:
        linhas = [f"{header or '✅ Run concluído'} — Publicados ({len(self.published)}):"]
        linhas += [f"• {p}" for p in self.published] or ["• (nenhum)"]
        if self.dispatched:
            linhas.append(f"{DESPACHO_MANUAL} ({len(self.dispatched)}):")
            linhas += [f"• {d}" for d in self.dispatched]
        linhas.append(f"Descartados ({len(self.discarded)}):")
        linhas += self._linhas_de_descarte() or ["• (nenhum)"]
        linhas += self.discovery
        if self.warnings:
            linhas.append("Avisos:")
            linhas += [f"• {w}" for w in self.warnings]
        return "\n".join(linhas)


class RunAborted(RuntimeError):
    """Nenhuma fonte devolveu nada E todas falharam (fase 5A, A4). Carrega o
    resumo para o cli mandar ao ops antes de sair com erro. A causa (erro de
    cada fonte) vai no PRÓPRIO motivo: o aviso por fonte é deduplicado pelo
    `warn_once` e, a partir do 2º run do dia, o resumo sairia sem ela."""

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


def _drena_avisos(ch) -> list[str]:
    """Tira do canal os avisos que ele juntou publicando e esvazia a lista.

    `warnings` é OPCIONAL (só o `instagram_story` tem hoje): canal sem a lista
    — ou com outra coisa no lugar — não pode quebrar o run."""
    avisos = getattr(ch, "warnings", None)
    if not isinstance(avisos, list) or not avisos:
        return []
    drenados = list(avisos)
    avisos.clear()
    return drenados


def candidate_max_age_days(cfg: dict, source: str) -> int:
    """`<fonte>.candidate_max_age_days` do config: por quantos dias uma
    candidata descoberta continua elegível. 0/ausente = a fonte não usa o
    estoque (o pool do ML, por exemplo, já é relido inteiro a cada run)."""
    secao = cfg.get(source)
    if not isinstance(secao, dict):
        return 0
    try:
        return max(0, int(secao.get("candidate_max_age_days") or 0))
    except (TypeError, ValueError):
        return 0


def _candidatas_do_run(cfg: dict, db: StateDB, sources: list[Source],
                       frescas: dict[str, list], summary: RunSummary,
                       dry_run: bool, warn: "_Warner") -> list:
    """Candidatas do run = ESTOQUE persistido ∪ a fatia recém descoberta
    (fase 5C, C1).

    Antes, cada run ranqueava só o que tinha acabado de ver — e via sempre as
    mesmas duas páginas. Agora a descoberta é rotativa (uma fatia por run) e o
    estoque acumula o que as fatias anteriores acharam; dedupe, filtros e
    ranking rodam sobre a união. A fatia de hoje SOBRESCREVE o que o estoque
    tinha do mesmo item: o preço mais novo vence."""
    resultado: dict[tuple[str, str], object] = {}
    for src in sources:
        lote = frescas.get(src.name)
        if lote is None:                       # fonte que falhou: nada a somar
            continue
        idade = candidate_max_age_days(cfg, src.name)
        if idade <= 0:
            for o in lote:
                resultado[(o.source, o.item_id)] = o
            continue
        estoque = db.load_candidates(src.name, idade)
        conhecidos = {o.item_id for o in estoque}
        novos = len({o.item_id for o in lote} - conhecidos)
        for o in estoque:
            resultado[(o.source, o.item_id)] = o
        for o in lote:
            resultado[(o.source, o.item_id)] = o
        if not dry_run:
            db.upsert_candidates(lote)
            db.prune_candidates(idade, source=src.name)
        stats = getattr(src, "discovery_stats", None)
        if stats is not None:
            summary.discovery.append(
                f"🔎 {src.name}: {stats.calls} chamadas · {stats.nodes} nós · "
                f"{stats.eligible} elegíveis · {novos} novos no estoque "
                f"({len(conhecidos | {o.item_id for o in lote})} no total)")
            # Erro de CONFIG da varredura (ex.: `calls_per_run` que corta o
            # plano e desliga subcategorias/keywords em silêncio). É aviso, não
            # número do run: passa pelo warn_once e notifica uma vez por dia.
            if getattr(stats, "warning", ""):
                warn(stats.warning)
    return list(resultado.values())


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
    db.somente_leitura = dry_run     # A10: nem o cursor da descoberta avança
    warn = _Warner(db, summary, dry_run)
    sel = cfg["selection"]
    llm.stats.reset()

    # Heartbeat (fase 5A): o primeiro run do dia local diz bom dia com a
    # contagem de ontem. Vai SEMPRE ao ops (é um aviso, e aviso notifica) —
    # uma VPS morta deixa de ser indistinguível de "sem oferta boa".
    if not dry_run:
        ontem = db.day_stats(db.local_today() - timedelta(days=1))
        # A12: "publicados" é só o que foi a canal automático; as artes que
        # esperaram o dono postar aparecem como "despachados", e só quando
        # houve alguma (não poluir o bom dia de quem não usa o story_dispatch).
        despachos = f"{ontem.dispatched} despachados, " if ontem.dispatched else ""
        warn(f"☀️ Bom dia — ontem: {ontem.published} publicados, {despachos}"
             f"{ontem.discarded} descartados em {ontem.runs} runs", key="heartbeat")

    # Avisos de quem montou fontes/canais (ex.: canal ligado sem env) — antes
    # eram só um print no journal e o chat de ops via "✅ Run concluído".
    for aviso in warnings_iniciais or []:
        warn(aviso)

    if watchlist is None:
        warn("ℹ️ Sem watchlist — ranking sem boosts")
    elif watchlist.is_stale():
        # C11: vencida perde só os boosts. Referências e pisos são fatos
        # datados e continuam na régua — antes a watchlist inteira sumia e o
        # "De:" e o selo trocavam de número de um dia para o outro.
        warn(f"⚠️ Watchlist vencida há {watchlist.days_old()} dias — rode /watchlist-refresh")
        watchlist = watchlist.facts_only()

    frescas: dict[str, list] = {}
    erros_de_fonte: list[str] = []
    for src in sources:
        # Cada fonte isolada (A4): a Shopee em 5xx não derruba mais o ML.
        try:
            src_offers = src.fetch_offers(cfg)
        except (SourceError, httpx.HTTPError) as exc:
            erros_de_fonte.append(f"{src.name}: {exc}")
            warn(f"⚠️ fonte {src.name} falhou: {exc}")
            continue
        frescas[src.name] = src_offers
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
        # A6: pool de links vazio/incompleto é o que faz o ML descartar tudo
        # em silêncio. Aviso uma vez por dia, com o número.
        cobertura = getattr(src, "link_coverage", None)
        if cobertura is not None and src_offers:
            com_link, total = cobertura(src_offers)
            if com_link * 2 < total:
                warn(f"⚠️ {src.name}: só {com_link} de {total} produtos têm link — "
                     "rode /meli-links-refresh")

    if sources and len(erros_de_fonte) == len(sources):
        # Só aqui o run aborta — e mesmo assim o resumo vai ao ops, via cli,
        # com a causa no cabeçalho (os avisos podem já ter sido deduplicados).
        raise RunAborted(summary, "todas as fontes falharam — " + "; ".join(erros_de_fonte))

    # A observação de hoje entra no histórico ANTES de a mediana ser lida —
    # para as fontes cujo preço de descoberta É uma observação (Shopee), e só
    # para a fatia RECÉM buscada: o preço de uma candidata que veio do estoque
    # tem até `candidate_max_age_days` dias e gravá-lo como "hoje" inventaria
    # observação. O ML chega com a mediana do pool como preço "atual": não é
    # observação; o que entra no price_log dele é o preço vivo, logo após o
    # refresh (C7c). Dry-run não escreve no banco (A10).
    by_name = {s.name: s for s in sources}
    if not dry_run:
        pricing.record_observations(db, [
            o for nome, lote in frescas.items() for o in lote
            if getattr(by_name.get(nome), "observes_price_on_discovery", True)])

    offers = _candidatas_do_run(cfg, db, sources, frescas, summary, dry_run, warn)

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
            # Aviso só quando algum canal bateu o max_per_day de verdade.
            # Fechado SÓ pelo ritmo (08:05: orçamento 1, já usado) é o
            # normal de todo dia — encerra em silêncio (revisão da 5A).
            nos_tetos = [ch.name for ch in channels if no_teto(ch)]
            if len(nos_tetos) == len(channels):
                warn("ℹ️ teto diário atingido em todos os canais")
            elif nos_tetos:
                warn("ℹ️ teto diário atingido em " + ", ".join(nos_tetos))
            return _finish(summary, db, dry_run, sel, warn)

    offers = pricing.enrich_offers(offers, db, watchlist, cfg)
    candidates, cortes = selection.filter_offers_with_stats(offers, db, cfg)
    if offers and not candidates:
        # O quinto zero silencioso (C4a): N entraram, 0 sobraram, e por quê.
        warn(f"⚠️ {len(offers)} ofertas buscadas, 0 candidatas — {cortes.resumo()}")
    ranked = selection.rank_offers(candidates, db.recent_titles(), cfg, watchlist)
    # Comparação por IDENTIDADE da oferta, não por igualdade de dataclass: com
    # o estoque de candidatas a fila tem milhares de itens e `o not in ranked`
    # era O(n × m) de `__eq__` de frozen dataclass — e duas ofertas iguais em
    # todos os campos se anulariam.
    escolhidos = {(o.source, o.item_id) for o in ranked}
    reserva = [o for o in selection.order_by_ev(candidates, cfg, watchlist)
               if (o.source, o.item_id) not in escolhidos]
    fila = ranked + reserva

    target = sel["posts_per_run"]
    count = 0
    tetos_atingidos: set[str] = set()
    minimo_pct = int(pricing.setting(
        sel, "min_real_discount_pct", pricing.DEFAULT_MIN_REAL_DISCOUNT_PCT))

    # Cota por fonte (M2): a meta do dia de cada loja, e quanto ela já
    # publicou hoje. A fila continua ordenada pelo ranking; a cota só escolhe,
    # entre as candidatas, quem vai primeiro — e nunca deixa o teto ocioso.
    metas = selection.source_targets(cfg, [s.name for s in sources])
    publicados_hoje = db.posted_today_by_source() if metas else {}

    # Freios da fila (C1 da revisão): teto de descartes e circuito por fonte.
    teto_descartes = int(pricing.setting(sel, "max_descartes_por_run",
                                         DEFAULT_MAX_DESCARTES_POR_RUN))
    falhas_por_fonte: dict[str, int] = {}
    fontes_fechadas: set[str] = set()
    fila_interrompida = False

    while count < target:
        # Este teste estava no FIM do laço e todo `continue` (descarte) pulava
        # por cima dele: com a fila do tamanho do estoque, um run com todos os
        # canais fechados varria milhares de ofertas pagando refresh/link/LLM.
        if not dry_run and not any(aberto(ch) for ch in channels):
            break   # ninguém mais pode publicar: a próxima oferta não paga nada
        indice = selection.next_index_by_quota(fila, metas, publicados_hoje)
        if indice is None:
            break
        offer = fila.pop(indice)
        # O veredito (e o rótulo) só existem DEPOIS do refresh: antes dele o
        # desconto seria o do preço velho.
        rotulo = offer.title[:40]
        try:
            src = by_name[offer.source]
            refresh = getattr(src, "refresh_price", None)
            if refresh is not None:
                offer = refresh(offer)
                if not dry_run:
                    # C7c: o preço VIVO é a observação do dia (o ML gravava
                    # o do pool todo dia — o histórico dele era uma constante).
                    db.record_price(offer.source, offer.item_id, offer.price_current_cents)
            # Uma decisão, tomada uma vez: texto, copy, arte e legendas
            # recebem este veredito e não recalculam nada (C9/C10).
            veredito = pricing.verdict(offer, minimo_pct)
            rotulo = (f"{offer.title[:40]} ({veredito.discount_pct}% OFF)"
                      if veredito.mode == "A" else offer.title[:40])
            link = src.resolve_affiliate_link(offer)
            copy = copywriter.write_copy(offer, cfg, veredito)
            text = message.build_message(offer, copy, link, veredito)
            post = Post(offer=offer, copy=copy, affiliate_link=link, message_text=text,
                        verdict=veredito)
            validator(post, cfg)
            falhas_por_fonte[offer.source] = 0     # o circuito conta SEGUIDAS
        except Exception as exc:
            summary.discarded.append((rotulo, str(exc)))
            if isinstance(exc, (SourceError, httpx.HTTPError)):
                # Erro que veio da API da loja (refresh_price, link): é ela que
                # está recusando, e insistir item a item é o que queima a conta.
                seguidas = falhas_por_fonte.get(offer.source, 0) + 1
                falhas_por_fonte[offer.source] = seguidas
                if (seguidas >= MAX_FALHAS_SEGUIDAS_POR_FONTE
                        and offer.source not in fontes_fechadas):
                    fontes_fechadas.add(offer.source)
                    warn(f"⚠️ fonte {offer.source}: {MAX_FALHAS_SEGUIDAS_POR_FONTE} "
                         "falhas seguidas — fonte fechada neste run")
                    fila = [o for o in fila if o.source not in fontes_fechadas]
                    fila_interrompida = True
            if 0 < teto_descartes <= len(summary.discarded):
                fila_interrompida = True
                break
            continue

        if dry_run:
            print(f"--- DRY-RUN: post que seria publicado ---\n{post.message_text}\n")
            summary.published.append(f"[dry] {rotulo}")
            publicados_hoje[offer.source] = publicados_hoje.get(offer.source, 0) + 1
            count += 1
            continue

        published_any = False
        so_manuais = True
        for ch in channels:
            if not aberto(ch):
                if no_teto(ch):
                    tetos_atingidos.add(ch.name)   # teto de verdade: vira aviso
                continue                            # ritmo/max_per_run: silêncio
            res = ch.publish(post)
            # Aviso que só existe DEPOIS de publicar (fase 5E: a Meta não
            # devolveu `status_code` do container e o polling ficou cego — 5
            # GETs e 4 s de espera em todo story). Os avisos de MONTAGEM já
            # tinham caminho (`warnings_iniciais`); este não tinha nenhum e
            # morria dentro do canal. Sai pelo mesmo `warn`: uma vez por dia.
            for aviso in _drena_avisos(ch):
                warn(aviso)
            if res.ok:
                usados[ch.name] = usados.get(ch.name, 0) + 1
                if orcamento[ch.name] is not None:
                    usados_dia[ch.name] = usados_dia.get(ch.name, 0) + 1
                manual = bool(getattr(ch, "manual", False))
                db.record_post(post, ch.name, res.message_id, manual=manual)
                published_any = True
                so_manuais = so_manuais and manual
                falhas_seguidas[ch.name] = 0
            else:
                summary.discarded.append((rotulo, f"publicação falhou em {ch.name}: {res.error}"))
                falhas_seguidas[ch.name] = falhas_seguidas.get(ch.name, 0) + 1
                if falhas_seguidas[ch.name] >= MAX_FALHAS_SEGUIDAS_POR_CANAL:
                    fechados.add(ch.name)
                    warn(f"⚠️ {ch.name}: {MAX_FALHAS_SEGUIDAS_POR_CANAL} falhas seguidas — "
                         "canal fechado neste run")
        if published_any:
            # A12: quando os únicos canais que aceitaram a oferta são manuais
            # (story_dispatch), o que aconteceu foi um DESPACHO — a arte está
            # no chat de ops esperando o dono postar. Não entra em `published`
            # (nem no resumo, nem na contagem do dia, nem no heartbeat).
            (summary.dispatched if so_manuais else summary.published).append(rotulo)
            publicados_hoje[offer.source] = publicados_hoje.get(offer.source, 0) + 1
            count += 1

    if fila_interrompida:
        # Um dos dois freios cortou a fila: o resumo diz quantos descartes
        # houve ANTES de o run parar — sem isto o run terminava "normal" com
        # 5.000 descartes agrupados numa linha só.
        warn(f"⚠️ {len(summary.discarded)} descartes no run — fila interrompida")

    for ch in channels:
        if ch.name in tetos_atingidos:
            warn(f"ℹ️ {ch.name}: teto diário ({getattr(ch, 'max_per_day', None)}) atingido")

    return _finish(summary, db, dry_run, sel, warn)

from dataclasses import dataclass, field

from afiliado import copywriter, message, selection, validate
from afiliado.channels.base import Channel
from afiliado.models import Post
from afiliado.sources.base import Source
from afiliado.state import StateDB
from afiliado.watchlist import Watchlist


@dataclass
class RunSummary:
    published: list[str] = field(default_factory=list)
    discarded: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def text(self) -> str:
        linhas = [f"✅ Run concluído — Publicados ({len(self.published)}):"]
        linhas += [f"• {p}" for p in self.published] or ["• (nenhum)"]
        linhas.append(f"Descartados ({len(self.discarded)}):")
        linhas += [f"• {d}" for d in self.discarded] or ["• (nenhum)"]
        if self.warnings:
            linhas.append("Avisos:")
            linhas += [f"• {w}" for w in self.warnings]
        return "\n".join(linhas)


def run(cfg: dict, sources: list[Source], channels: list[Channel], db: StateDB,
        dry_run: bool = False, validator=None, watchlist: Watchlist | None = None) -> RunSummary:
    validator = validator or validate.validate_post
    summary = RunSummary()

    if watchlist is None:
        summary.warnings.append("ℹ️ Sem watchlist — ranking sem boosts")
    elif watchlist.is_stale():
        summary.warnings.append(
            f"⚠️ Watchlist vencida há {watchlist.days_old()} dias — rode /watchlist-refresh")
        watchlist = None

    offers = []
    meli_offer_count = None
    for src in sources:
        src_offers = src.fetch_offers(cfg)  # SourceError propaga: aborta o run
        if src.name == "meli":
            meli_offer_count = len(src_offers)
        offers.extend(src_offers)

    if meli_offer_count == 0:
        summary.warnings.append(
            "ℹ️ meli: pool vazio ou vencido — rode /meli-links-refresh")

    candidates = selection.filter_offers(offers, db, cfg)
    ranked = selection.rank_offers(candidates, db.recent_titles(), cfg, watchlist)
    reserva = [o for o in selection.order_by_ev(candidates, cfg, watchlist) if o not in ranked]
    fila = ranked + reserva

    by_name = {s.name: s for s in sources}
    target = cfg["selection"]["posts_per_run"]
    count = 0
    usados: dict[str, int] = {}
    usados_dia = {ch.name: db.count_posts_today(ch.name)
                 for ch in channels if getattr(ch, "max_per_day", None) is not None}
    tetos_atingidos: set[str] = set()

    for offer in fila:
        if count >= target:
            break
        rotulo = f"{offer.title[:40]} ({offer.discount_pct}% OFF)"
        try:
            src = by_name[offer.source]
            refresh = getattr(src, "refresh_price", None)
            if refresh is not None:
                offer = refresh(offer)
            link = src.resolve_affiliate_link(offer)
            copy = copywriter.write_copy(offer, cfg)
            price_floor = watchlist.price_floor(offer.item_id) if watchlist is not None else None
            text = message.build_message(offer, copy, link, price_floor=price_floor)
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
            cap_dia = getattr(ch, "max_per_day", None)
            if cap_dia is not None and usados_dia.get(ch.name, 0) >= cap_dia:
                tetos_atingidos.add(ch.name)
                continue   # teto diário atingido: pula em silêncio, não é falha
            limit = getattr(ch, "max_per_run", None)
            if limit is not None and usados.get(ch.name, 0) >= limit:
                continue
            res = ch.publish(post)
            if res.ok:
                usados[ch.name] = usados.get(ch.name, 0) + 1
                if cap_dia is not None:
                    usados_dia[ch.name] = usados_dia.get(ch.name, 0) + 1
                db.record_post(post, ch.name, res.message_id)
                published_any = True
            else:
                summary.discarded.append(f"{rotulo}: publicação falhou em {ch.name}: {res.error}")
        if published_any:
            summary.published.append(rotulo)
            count += 1

    for ch in channels:
        if ch.name in tetos_atingidos:
            cap = getattr(ch, "max_per_day", None)
            summary.warnings.append(f"ℹ️ {ch.name}: teto diário ({cap}) atingido")

    if not dry_run:
        db.record_run(len(summary.published), len(summary.discarded))
    return summary

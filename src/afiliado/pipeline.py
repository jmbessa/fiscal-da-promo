from dataclasses import dataclass, field

from afiliado import copywriter, message, selection, validate
from afiliado.channels.base import Channel
from afiliado.models import Post
from afiliado.sources.base import Source
from afiliado.state import StateDB


@dataclass
class RunSummary:
    published: list[str] = field(default_factory=list)
    discarded: list[str] = field(default_factory=list)

    def text(self) -> str:
        linhas = [f"✅ Run concluído — Publicados ({len(self.published)}):"]
        linhas += [f"• {p}" for p in self.published] or ["• (nenhum)"]
        linhas.append(f"Descartados ({len(self.discarded)}):")
        linhas += [f"• {d}" for d in self.discarded] or ["• (nenhum)"]
        return "\n".join(linhas)


def run(cfg: dict, sources: list[Source], channels: list[Channel], db: StateDB,
        dry_run: bool = False, validator=None) -> RunSummary:
    validator = validator or validate.validate_post
    summary = RunSummary()

    offers = []
    for src in sources:
        offers.extend(src.fetch_offers(cfg))  # SourceError propaga: aborta o run

    candidates = selection.filter_offers(offers, db, cfg)
    ranked = selection.rank_offers(candidates, db.recent_titles(), cfg)
    reserva = [o for o in selection.order_by_ev(candidates, cfg) if o not in ranked]
    fila = ranked + reserva

    by_name = {s.name: s for s in sources}
    target = cfg["selection"]["posts_per_run"]
    count = 0

    for offer in fila:
        if count >= target:
            break
        rotulo = f"{offer.title[:40]} ({offer.discount_pct}% OFF)"
        try:
            link = by_name[offer.source].resolve_affiliate_link(offer)
            copy = copywriter.write_copy(offer, cfg)
            text = message.build_message(offer, copy, link)
            post = Post(offer=offer, copy=copy, affiliate_link=link, message_text=text)
            validator(post, cfg)
        except Exception as exc:
            summary.discarded.append(f"{rotulo}: {exc}")
            continue

        if dry_run:
            print(f"--- DRY-RUN: post que seria publicado ---\n{post.message_text}\n")
            summary.published.append(f"[dry] {rotulo}")
            count += 1
            continue

        for ch in channels:
            res = ch.publish(post)
            if res.ok:
                db.record_post(post, ch.name, res.message_id)
                summary.published.append(rotulo)
                count += 1
            else:
                summary.discarded.append(f"{rotulo}: publicação falhou: {res.error}")

    if not dry_run:
        db.record_run(len(summary.published), len(summary.discarded))
    return summary

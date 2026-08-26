"""Reprodução: max_per_day não espalha — consome os primeiros N slots do 'dia' UTC.
Mostra em que horas BRT o instagram_feed (2/dia) e o story_dispatch (6/dia) publicam.

Ajuste da fase 5A: o portão do pipeline passou a ser o orçamento de ritmo
(pipeline.pacing_budget: teto distribuído pela janela, no dia local) — a
simulação usa a mesma função, com o teto do config (telegram 60/dia)."""
import sys
sys.path.insert(0, r"G:\Biblioteca\Documentos\Projetos\Afiliado\.claude\worktrees\fase3b-meli-hibrido\src")
import tempfile, os
from datetime import datetime, timedelta, timezone
from afiliado import pipeline, state
from afiliado.state import StateDB
from afiliado.models import Offer, Post, CopyParts

BRT = timezone(timedelta(hours=-3))
CAPS = {"telegram": 60, "story_dispatch": 6, "instagram_feed": 2}
tmp = tempfile.mkdtemp()
db = StateDB(os.path.join(tmp, "s.db"))

def offer(i):
    return Offer(source="shopee", item_id=str(i), title="t", price_original_cents=1,
                 price_current_cents=1, commission_pct=1, image_url="i", product_url="p")

n = 0
for day in range(1, 4):
    horas = {ch: [] for ch in CAPS}
    t = datetime(2026, 9, day, 8, 0, tzinfo=BRT)
    end = datetime(2026, 9, day, 23, 55, tzinfo=BRT)
    while t <= end:
        state._now = lambda t=t: t.astimezone(timezone.utc)
        n += 1
        for ch, cap in CAPS.items():          # mesma lógica do pipeline.run: 1 oferta, todos os canais
            if db.count_posts_today(ch) < pipeline.pacing_budget(cap, t):
                db.record_post(Post(offer=offer(n), copy=CopyParts("h", "d", "c"),
                                    affiliate_link="l", message_text="m"), ch, "x")
                horas[ch].append(t.strftime("%H:%M"))
        t += timedelta(minutes=5)
    print(f"dia BRT {day}:")
    for ch in ("instagram_feed", "story_dispatch"):
        print(f"   {ch:15s} {len(horas[ch])} posts em: {horas[ch]}")
db.close()

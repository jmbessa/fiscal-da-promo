"""Reprodução: teto diário contado em dia UTC + timer 08h-23h55 BRT.
Simula o pipeline publicando 1 post por slot de 5 min enquanto count_posts_today < 100,
com posted_at real (UTC) — mostra em que hora BRT o canal cala e quando volta."""
import sys
sys.path.insert(0, r"G:\Biblioteca\Documentos\Projetos\Afiliado\.claude\worktrees\fase3b-meli-hibrido\src")
import tempfile, os
from datetime import datetime, timedelta, timezone
from afiliado import state
from afiliado.state import StateDB
from afiliado.models import Offer, Post, CopyParts

BRT = timezone(timedelta(hours=-3))
CAP = 100
tmp = tempfile.mkdtemp()
db = StateDB(os.path.join(tmp, "s.db"))

def offer(i):
    return Offer(source="shopee", item_id=str(i), title="t", price_original_cents=1,
                 price_current_cents=1, commission_pct=1, image_url="i", product_url="p")

n = 0
for day in range(1, 4):
    posted_today, silenced_at, resumed_at, first_slot = 0, None, None, None
    t = datetime(2026, 9, day, 8, 0, tzinfo=BRT)
    end = datetime(2026, 9, day, 23, 55, tzinfo=BRT)
    prev_blocked = False
    while t <= end:
        state._now = lambda t=t: t.astimezone(timezone.utc)   # relogio do StateDB
        used = db.count_posts_today("telegram")
        if used < CAP:
            n += 1
            db.record_post(Post(offer=offer(n), copy=CopyParts("h", "d", "c"),
                                affiliate_link="l", message_text="m"), "telegram", "x")
            posted_today += 1
            if prev_blocked and resumed_at is None:
                resumed_at = t.strftime("%H:%M")
            prev_blocked = False
        else:
            if silenced_at is None:
                silenced_at = t.strftime("%H:%M")
            prev_blocked = True
        t += timedelta(minutes=5)
    print(f"dia BRT {day}: publicados={posted_today:3d}  canal cala às {silenced_at} BRT  volta às {resumed_at} BRT")
db.close()

"""EV sensitivity of the ML pool to meli.commission_pct (1 / 4 / 8 %), the
min_ev_brl floor, and where a Shopee item would need to be to outrank."""
import copy
import math
import tempfile
from pathlib import Path

import httpx

from afiliado import pricing, selection
from afiliado.config import load_config
from afiliado.sources.meli import MeliSource
from afiliado.state import StateDB
from tests.test_models import make_offer

base = load_config("config.yaml")
wp = base["selection"]["ev_weights"]["popularity"]
piso = base["selection"]["min_ev_brl"]


def pool_at(pct):
    cfg = copy.deepcopy(base)
    cfg["meli"]["commission_pct"] = pct
    tmp = Path(tempfile.mkdtemp())
    src = MeliSource("a", "b", token_path=tmp / "t", links_path=tmp / "l",
                     client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    offers = src.fetch_offers(cfg)
    db = StateDB(tmp / "s.db")
    offers = pricing.enrich_offers(offers, db, None, cfg)
    evs = sorted((selection.ev_score(o, cfg), o) for o in offers)
    surv = selection.filter_offers(offers, db, cfg)
    db.close()
    return cfg, evs, surv


print(f"min_ev_brl={piso}  popularity weight={wp}")
for pct in (1.0, 2.0, 4.0, 8.0):
    cfg, evs, surv = pool_at(pct)
    vals = [e for e, _ in evs]
    print(f"commission_pct={pct:>4}: EV min={vals[0]:.2f} median={vals[len(vals)//2]:.2f} "
          f"max={vals[-1]:.2f} | sobrevivem ao piso {piso}: {len(surv)}/{len(evs)}")
    if pct in (1.0, 4.0):
        lowest = [f"{o.item_id} R${o.price_current_cents/100:.0f} sales={o.sales} EV={e:.2f}"
                  for e, o in evs[:3]]
        print("   3 menores:", lowest)

# Shopee side: what a typical target-category item (R$25-150) scores.
cfg = base
for price_brl, rate, sales in [(30, 0.05, 500), (30, 0.10, 3000), (60, 0.08, 1500),
                               (100, 0.12, 10000), (150, 0.15, 20000)]:
    o = make_offer(price_current_cents=price_brl * 100, commission_pct=rate * 100,
                   commission_brl=price_brl * rate, sales=sales)
    print(f"Shopee R${price_brl} rate={rate:.0%} sales={sales}: EV={selection.ev_score(o, cfg):.2f}")

# Rank position of the ML pool top item vs those Shopee examples at each pct:
for pct in (1.0, 4.0, 8.0):
    cfg, evs, _ = pool_at(pct)
    top = evs[-1][0]
    med = evs[len(evs)//2][0]
    print(f"pct={pct}: ML top EV={top:.2f}, ML mediana EV={med:.2f}")

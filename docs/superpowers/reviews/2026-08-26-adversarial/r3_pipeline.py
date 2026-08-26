"""Pipeline side effects with the REAL MeliSource + real config + real pool:
 A) links file absent  -> how many ML API calls + discards per run
 B) links present, live price = ref + 1 cent -> cascade of discards
 C) links present, live price = ref -> price_log stores the POOL ref, not live
 D) dry_run=True still GETs the affiliate link (a click) per validated post
"""
import json
import sys
import tempfile
from pathlib import Path

import httpx

from afiliado import llm, pipeline, validate
from afiliado.channels.base import PublishResult
from afiliado.config import load_config
from afiliado.sources.meli import MeliSource
from afiliado.state import StateDB
from tests.test_models import make_offer

llm.ask_json = lambda *a, **k: None  # no Claude CLI

POOL = json.load(open("data/meli_offers.json", encoding="utf-8"))["offers"]
IDS = [o["product_id"] for o in POOL]
REF = {o["product_id"]: o["price_ref_cents"] for o in POOL}


class FakeShopee:
    name = "shopee"
    def fetch_offers(self, cfg):
        # EV 2.71 — below the ML pool median (5.32 at 4%), so ML ranks first.
        return [make_offer(item_id="S1", category="100630", sales=500,
                           price_current_cents=3000, price_original_cents=3000,
                           commission_pct=5.0, commission_brl=1.5,
                           offer_link="https://s.shopee.com.br/xyz")]
    def resolve_affiliate_link(self, offer):
        return offer.offer_link
    def refresh_price(self, offer):
        return offer


class FakeChannel:
    name = "telegram"
    def __init__(self): self.sent = []
    def publish(self, post):
        self.sent.append(post); return PublishResult(True, "1")


def build(tmp, links_present: bool, live_delta_cents: int):
    calls = {"token": 0, "items": 0, "link_get": [], "img_get": 0}

    def meli_api(r: httpx.Request):
        if r.url.path == "/oauth/token":
            calls["token"] += 1
            return httpx.Response(200, json={"access_token": "T", "expires_in": 21600})
        if r.url.path.startswith("/products/"):
            calls["items"] += 1
            pid = r.url.path.split("/")[2]
            price = (REF[pid] + live_delta_cents) / 100
            return httpx.Response(200, json={"results": [{"condition": "new", "price": price}]})
        return httpx.Response(404)

    links_path = tmp / "meli_links.json"
    if links_present:
        links_path.write_text(json.dumps({pid: f"https://meli.la/{pid}" for pid in IDS}))
    src = MeliSource("CID", "CS", token_path=tmp / "tok.json", links_path=links_path,
                     client=httpx.Client(transport=httpx.MockTransport(meli_api)))

    def val_api(r: httpx.Request):
        h = r.url.host
        if h in ("meli.la", "s.shopee.com.br"):
            calls["link_get"].append(str(r.url))
            return httpx.Response(302, headers={"location": "https://www.mercadolivre.com.br/p/X"}) \
                if h == "meli.la" else httpx.Response(302, headers={"location": "https://shopee.com.br/p/1"})
        if h in ("www.mercadolivre.com.br", "shopee.com.br"):
            return httpx.Response(200, text="ok")
        calls["img_get"] += 1
        return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=b"x" * 6000)
    vclient = httpx.Client(transport=httpx.MockTransport(val_api), follow_redirects=True)
    validator = lambda post, cfg: validate.validate_post(post, cfg, client=vclient)
    return src, validator, calls


def scenario(title, links_present, live_delta, dry_run):
    tmp = Path(tempfile.mkdtemp())
    cfg = load_config("config.yaml")
    cfg["sources"] = {"shopee": True, "meli": True}
    db = StateDB(tmp / "s.db")
    src, validator, calls = build(tmp, links_present, live_delta)
    ch = FakeChannel()
    s = pipeline.run(cfg, [FakeShopee(), src], [ch], db, dry_run=dry_run,
                     validator=validator, watchlist=None)
    ml_disc = [d for d in s.discarded]
    print(f"=== {title} ===")
    print(f"  published={s.published}")
    print(f"  discarded={len(s.discarded)}  (primeiros 2: {s.discarded[:2]})")
    print(f"  ML /products/{{id}}/items calls={calls['items']}  token calls={calls['token']}")
    print(f"  GETs no link de afiliado (cliques)={len(calls['link_get'])} -> {calls['link_get'][:2]}")
    print(f"  warnings={s.warnings}")
    rows = db.conn.execute("SELECT source,item_id,price_cents FROM price_log WHERE source='meli' LIMIT 2").fetchall()
    print(f"  price_log(meli) amostra={rows}  (ref do pool: {[REF[r[1]] for r in rows]})")
    db.close()
    return s, calls


scenario("A) links ausentes, publish", links_present=False, live_delta=0, dry_run=False)
scenario("B) links presentes, preco vivo = ref + 1 centavo, publish", True, +1, False)
scenario("C) links presentes, preco vivo = ref - 1000 (queda), publish", True, -1000, False)
scenario("D) links presentes, preco vivo = ref, DRY-RUN", True, 0, True)

# E) 40 runs on ONE db: when does ML go quiet, and does any warning say so?
print("=== E) 40 runs seguidos, mesmo state.db, links ok, preco vivo = ref ===")
tmp = Path(tempfile.mkdtemp())
cfg = load_config("config.yaml"); cfg["sources"] = {"shopee": True, "meli": True}
db = StateDB(tmp / "s.db")
src, validator, calls = build(tmp, True, 0)
seq = []
first_shopee_run = None
for i in range(1, 41):
    s = pipeline.run(cfg, [FakeShopee(), src], [FakeChannel()], db, validator=validator, watchlist=None)
    who = "ML" if s.published and s.published[0] != "Tênis Nike SB" else ("SH" if s.published else "--")
    seq.append(who)
    if who != "ML" and first_shopee_run is None:
        first_shopee_run = (i, s.published, s.warnings, len(s.discarded))
print("  sequencia:", "".join("M" if w == "ML" else ("S" if w == "SH" else ".") for w in seq))
print("  ML publicados:", seq.count("ML"), "| primeiro run sem ML:", first_shopee_run)
n_ml_posted = db.conn.execute("SELECT COUNT(*) FROM posted WHERE source='meli'").fetchone()[0]
print("  posted(meli) no db:", n_ml_posted, "| ML API calls total:", calls["items"])
db.close()

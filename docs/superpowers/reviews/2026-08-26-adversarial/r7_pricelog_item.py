"""After publishing an ML item whose LIVE price was ref-1000, what did price_log store?"""
import json, tempfile
from pathlib import Path
import httpx
from afiliado import llm, pipeline, validate
from afiliado.channels.base import PublishResult
from afiliado.config import load_config
from afiliado.sources.meli import MeliSource
from afiliado.state import StateDB

llm.ask_json = lambda *a, **k: None
POOL = json.load(open("data/meli_offers.json", encoding="utf-8"))["offers"]
REF = {o["product_id"]: o["price_ref_cents"] for o in POOL}
tmp = Path(tempfile.mkdtemp())
(tmp / "links.json").write_text(json.dumps({p: f"https://meli.la/{p}" for p in REF}))

def api(r):
    if r.url.path == "/oauth/token":
        return httpx.Response(200, json={"access_token": "T", "expires_in": 9999})
    pid = r.url.path.split("/")[2]
    return httpx.Response(200, json={"results": [{"condition": "new", "price": (REF[pid] - 1000) / 100}]})
src = MeliSource("a", "b", token_path=tmp / "t", links_path=tmp / "links.json",
                 client=httpx.Client(transport=httpx.MockTransport(api)))

def val(r):
    if r.url.host == "meli.la":
        return httpx.Response(302, headers={"location": "https://www.mercadolivre.com.br/p/x"})
    if r.url.host == "www.mercadolivre.com.br":
        return httpx.Response(200)
    return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=b"x" * 6000)
vc = httpx.Client(transport=httpx.MockTransport(val), follow_redirects=True)

class Ch:
    name = "telegram"; sent = []
    def publish(self, post): self.sent.append(post); return PublishResult(True, "1")

cfg = load_config("config.yaml"); cfg["sources"] = {"shopee": False, "meli": True}
db = StateDB(tmp / "s.db")
s = pipeline.run(cfg, [src], [Ch()], db, validator=lambda p, c: validate.validate_post(p, c, client=vc), watchlist=None)
post = Ch.sent[0]
pid = post.offer.item_id
row = db.conn.execute("SELECT price_cents FROM price_log WHERE source='meli' AND item_id=?", (pid,)).fetchone()
print(f"publicado {pid}: preco VIVO publicado={post.offer.price_current_cents} | ref do pool={REF[pid]} | price_log gravou={row[0]}")
print("posted.price_cents:", db.conn.execute("SELECT price_cents FROM posted WHERE item_id=?", (pid,)).fetchone()[0])

"""Reprodução: com TODOS os canais no teto diário, o laço do pipeline percorre
a fila inteira chamando LLM (copy) + resolve_affiliate_link + validador para
CADA candidata, sem publicar nada — a cada run de 5 min."""
import sys
sys.path.insert(0, r"G:\Biblioteca\Documentos\Projetos\Afiliado\.claude\worktrees\fase3b-meli-hibrido\src")
import tempfile, os
from afiliado import llm, pipeline
from afiliado.channels.base import PublishResult
from afiliado.models import Offer, Post, CopyParts
from afiliado.state import StateDB

N = 97  # candidatas por run (ordem de grandeza medida pelo dono)

CFG = {
    "selection": {"posts_per_run": 1, "price_min_brl": 20, "price_max_brl": 1000,
                  "dedupe_days": 30, "category_ids": [], "max_above_ref": 1.00,
                  "require_price_ref": False, "min_real_discount_pct": 10,
                  "ref_window_days": 90, "ref_min_observations": 5,
                  "seal_tolerance": 1.05, "min_ev_brl": 0.50,
                  "ev_weights": {"popularity": 0.3, "discount": 0.5}},
    "llm": {"model": "haiku"}, "copy": {"tone": "x"},
    "validation": {"allowed_domains": ["shope.ee"]},
}

def offer(i):
    return Offer(source="shopee", item_id=str(i), title=f"item {i}", price_original_cents=5000,
                 price_current_cents=5000, commission_pct=8.0, image_url="https://x/i.jpg",
                 product_url="https://shopee.com.br/p", offer_link="https://shope.ee/x",
                 category="", sales=1000, commission_brl=4.0)

calls = {"llm": 0, "link": 0, "validate": 0}

def fake_ask_json(prompt, model="haiku", timeout=120):
    calls["llm"] += 1
    return None  # LLM "fora": fallback silencioso

llm.ask_json = fake_ask_json

class Src:
    name = "shopee"
    def fetch_offers(self, cfg): return [offer(i) for i in range(N)]
    def resolve_affiliate_link(self, o):
        calls["link"] += 1
        return "https://shope.ee/ok"
    def refresh_price(self, o): return o

class Ch:
    def __init__(self, name, cap):
        self.name, self.max_per_day, self.sent = name, cap, []
    def publish(self, post):
        self.sent.append(post); return PublishResult(True, "1")

def validator(post, cfg):
    calls["validate"] += 1

tmp = tempfile.mkdtemp()
db = StateDB(os.path.join(tmp, "s.db"))
chs = [Ch("telegram", 100), Ch("story_dispatch", 6), Ch("instagram_feed", 2)]
# Simula o dia já no teto: 100/6/2 posts gravados hoje (itens fora da fila).
for ch in chs:
    for k in range(ch.max_per_day):
        p = Post(offer=offer(10_000 + k), copy=CopyParts("h", "d", "c"),
                 affiliate_link="l", message_text="m")
        db.record_post(p, ch.name, "x")

summary = pipeline.run(CFG, [Src()], chs, db, validator=validator)
print("publicados:", len(summary.published))
print("chamadas LLM neste run (copy):", calls["llm"], " (esperado se o laço parasse: 1 ranking + 0 copy)")
print("generateShortLink neste run:", calls["link"])
print("validações (2 GETs de rede cada):", calls["validate"])
print("avisos:", summary.warnings)
print("descartados:", len(summary.discarded))
db.close()

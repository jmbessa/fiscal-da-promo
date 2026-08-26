"""Repro: LLM devolve {"chosen": null} (ou int) -> rank_offers levanta TypeError
-> pipeline.run nao tem try/except em volta (pipeline.py:61) -> run inteiro aborta.
Rodar: PYTHONPATH=<repo>/src python repro_rank_none.py
"""
import sys
import traceback

from afiliado import llm, selection
from afiliado.models import Offer

CFG = {"selection": {"posts_per_run": 1}, "llm": {"model": "haiku"}}


def offer(i):
    return Offer(source="shopee", item_id=f"id{i}", title=f"item {i}",
                 price_original_cents=1000, price_current_cents=1000,
                 commission_pct=10.0, image_url="https://x/i.jpg",
                 product_url="https://shopee.com.br/p", sales=10 * i)


for resposta in ({"chosen": None}, {"chosen": 5}, {"chosen": {"a": 1}}, {"chosen": "id1"}):
    llm.ask_json = lambda *a, **k: resposta
    try:
        out = selection.rank_offers([offer(1), offer(2), offer(3)], [], CFG)
        print(f"{resposta!r:28} -> OK, picked {[o.item_id for o in out]}")
    except Exception as exc:
        print(f"{resposta!r:28} -> {type(exc).__name__}: {exc}")

"""How far the curated price_ref_cents (single-day price at curation) sits
above price_historic_min_cents — i.e. how big a 'De/Por' the post would claim
if the live price is near the historic minimum."""
import json
from afiliado.models import format_brl

pool = json.load(open("data/meli_offers.json", encoding="utf-8"))["offers"]
rows = []
for o in pool:
    ref, mn = o["price_ref_cents"], o["price_historic_min_cents"]
    rows.append((ref / mn, o["product_id"], ref, mn, o["title"][:38]))
rows.sort(reverse=True)
ratios = [r[0] for r in rows]
print(f"n={len(rows)} ref/min: max={max(ratios):.2f} mediana={sorted(ratios)[len(ratios)//2]:.2f} "
      f"min={min(ratios):.2f}; >=1.5x: {sum(r>=1.5 for r in ratios)}; >=2x: {sum(r>=2 for r in ratios)}")
print("Top 8 (se o preco vivo estiver na minima historica, o post alega):")
for ratio, pid, ref, mn, title in rows[:8]:
    disc = round((1 - mn / ref) * 100)
    print(f"  {pid} {title:<38} De {format_brl(ref)} Por {format_brl(mn)} ({disc}% OFF)  ratio={ratio:.2f}")
print("\nE se o preco vivo estiver 1 centavo ACIMA de price_ref_cents -> max_above_ref=1.00 descarta.")

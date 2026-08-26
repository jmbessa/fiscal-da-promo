import json, math, statistics, collections
ROOT = r"G:\Biblioteca\Documentos\Projetos\Afiliado\.claude\worktrees\fase3b-meli-hibrido"

d = json.load(open(ROOT + r"\data\meli_offers.json", encoding="utf-8"))
print("meli_offers top-level:", {k: (v if k != "offers" else f"<{len(v)} items>") for k, v in d.items()})
offs = d["offers"]
print("n offers:", len(offs))
print("sample:", json.dumps(offs[0], ensure_ascii=False, indent=1))
prices = [o.get("price_ref_cents", 0) / 100 for o in offs]
mins = [(o.get("price_historic_min_cents") or 0) / 100 for o in offs]
sales = [o.get("sales", 0) or 0 for o in offs]
print("price_ref min/median/max:", min(prices), statistics.median(prices), max(prices))
print("hist_min min/median/max:", min(mins), statistics.median(mins), max(mins))
print("sales min/median/max:", min(sales), statistics.median(sales), max(sales))
print("categories:", collections.Counter(o.get("category") for o in offs))
print("with sales>0:", sum(1 for s in sales if s > 0), " rating>0:", sum(1 for o in offs if (o.get("rating") or 0) > 0))
print("ref==min count:", sum(1 for o in offs if o.get("price_ref_cents") == o.get("price_historic_min_cents")))
print("ref>1000 BRL:", sum(1 for p in prices if p > 1000), " ref<20:", sum(1 for p in prices if p < 20))
print()
print("EV (commission 4%, popularity 0.3, no discount at ranking time since current==ref):")
rows = []
for o in offs:
    s = o.get("sales", 0) or 0
    ev = (o["price_ref_cents"] / 100) * 0.04 * (1 + 0.3 * math.log10(s + 1))
    rows.append((ev, o))
rows.sort(key=lambda r: -r[0])
for ev, o in rows:
    print(f'{o["product_id"]:>14} ref={o["price_ref_cents"]/100:>8.2f} min={(o.get("price_historic_min_cents") or 0)/100:>8.2f} sales={o.get("sales",0) or 0:>7} ev={ev:6.2f} {o["title"][:55]}')
print("EV >= 0.50:", sum(1 for ev, _ in rows if ev >= 0.5), "of", len(rows))
print("EV median:", statistics.median([ev for ev, _ in rows]))

print()
print("=== watchlist.json ===")
w = json.load(open(ROOT + r"\data\watchlist.json", encoding="utf-8"))
for k, v in w.items():
    if isinstance(v, (list, dict)):
        print(k, type(v).__name__, len(v))
    else:
        print(k, "=", v)
for k in ("category_boosts", "hot_items", "price_floors", "price_refs"):
    v = w.get(k)
    if v is None:
        print("---", k, "ABSENT")
        continue
    s = json.dumps(v, ensure_ascii=False)
    print("---", k, s[:2500])
# check overlap between hot_items and price_floors
hi = set((w.get("hot_items") or {}).keys())
pf = set((w.get("price_floors") or {}).keys())
print("hot_items ∩ price_floors:", len(hi & pf))

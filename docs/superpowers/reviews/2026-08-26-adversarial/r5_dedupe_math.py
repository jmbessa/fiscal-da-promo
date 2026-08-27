"""Pool size vs dedupe_days vs daily target, straight from config.yaml."""
import json
from afiliado.config import load_config

cfg = load_config("config.yaml")
sel, sh = cfg["selection"], cfg["shopee"]
ml_pool = len(json.load(open("data/meli_offers.json", encoding="utf-8"))["offers"])
shopee_max = len(sh["category_ids"]) * len(sh["sort_types"]) * sh["pages"] * sh["page_size"]
dedupe = sel["dedupe_days"]
tg_cap = cfg["channels"]["telegram"]["max_per_day"]
runs_per_day = (24 - 8) * 12  # timer 08:00-23:55 a cada 5 min
max_posts_day = min(tg_cap, runs_per_day * sel["posts_per_run"])

print(f"pool ML = {ml_pool} | pool Shopee MAX teorico = {shopee_max} "
      f"({len(sh['category_ids'])} cat x {len(sh['sort_types'])} sort x {sh['pages']} pag x {sh['page_size']})")
print(f"dedupe_days = {dedupe} | teto telegram/dia = {tg_cap} | runs/dia = {runs_per_day} "
      f"| posts/dia max = {max_posts_day}")
total = ml_pool + shopee_max
print(f"taxa SUSTENTAVEL (pool unico / dedupe_days) = {total}/{dedupe} = {total/dedupe:.1f} posts/dia")
for target in (50, 100):
    print(f"meta {target}/dia: pool esgota em {total/target:.1f} dias; "
          f"ML sozinho esgota em {ml_pool/target:.2f} dias se ranquear no topo")
print(f"Para sustentar 50/dia com dedupe {dedupe}: pool >= {50*dedupe} itens unicos; "
      f"100/dia: >= {100*dedupe}")

"""ML seal: pool floor goes through the TOLERANT branch (<= floor*1.05) and the
text says 'Menor preço já registrado (verificado)' while the price is ABOVE the
registered minimum. The art (creative._selo_applicable) disagrees."""
import json
from afiliado import creative, message
from afiliado.config import load_config
from afiliado.models import CopyParts, Offer, format_brl

cfg = load_config("config.yaml")
sel = cfg["selection"]
pool = json.load(open("data/meli_offers.json", encoding="utf-8"))["offers"]
item = pool[0]
floor = item["price_historic_min_cents"]
live = int(floor * 1.049)  # 4.9% ABOVE the historic minimum

offer = Offer(source="meli", item_id=item["product_id"], title=item["title"],
              price_original_cents=item["price_ref_cents"], price_current_cents=live,
              commission_pct=4.0, image_url=item["image_url"],
              product_url="https://www.mercadolivre.com.br/p/" + item["product_id"],
              category=item["category"], sales=item["sales"], rating=item["rating"],
              price_ref_cents=item["price_ref_cents"], price_floor_cents=floor)
copy = CopyParts("h", "d", "c")
txt = message.build_message(offer, copy, "https://meli.la/x", price_floor=None,
                            min_real_discount_pct=sel["min_real_discount_pct"],
                            seal_tolerance=sel["seal_tolerance"])
print(f"item {item['product_id']} floor={format_brl(floor)} live={format_brl(live)} "
      f"(+{(live/floor-1)*100:.1f}% acima da minima)")
print("--- texto Telegram ---")
print(txt)
print("--- arte (creative._selo_applicable, price_floor=None como o pipeline passa p/ ML) ---")
print("selo na arte:", creative._selo_applicable(offer, None))
print()
# And the De/Por claim uses the single-day curation price as 'De':
print("real_discount_pct alegado vs price_ref (preco do dia da curadoria):",
      offer.real_discount_pct, "%", "->", "De", format_brl(item["price_ref_cents"]),
      "Por", format_brl(live))

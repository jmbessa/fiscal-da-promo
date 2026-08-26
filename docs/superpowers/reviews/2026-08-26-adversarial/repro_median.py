"""Reprodução: a mediana de DIAS certifica o 'de' inflado do vendedor que
alterna preço. pricing.median_cents + Offer.real_discount_pct + price_line."""
import sys
sys.path.insert(0, r"G:\Biblioteca\Documentos\Projetos\Afiliado\.claude\worktrees\fase3b-meli-hibrido\src")
from afiliado.pricing import median_cents, price_line
from afiliado.models import Offer

def mk(cur, ref):
    return Offer(source="shopee", item_id="9212570285", title="Creatina Soldiers 300g",
                 price_original_cents=6890, price_current_cents=cur, commission_pct=8,
                 image_url="i", product_url="p", sales=30000, rating=4.9, price_ref_cents=ref)

cases = {
    "A) vendedor alterna 68,90 / 26,00 dia sim dia nao (4 dias)": [6890, 2600, 6890, 2600],
    "B) vendedor segura 68,90 em 3 de cada 5 dias, vende a 26,00 nos outros 2": [6890, 6890, 6890, 2600, 2600],
    "C) 'de' inflado 60% dos dias em 90d (54 dias a 68,90 / 36 a 26,00)": [6890] * 54 + [2600] * 36,
    "D) caso do docstring do pricing.py (26 por 89 dias, 68,90 por 1 dia)": [2600] * 89 + [6890],
    "E) item em promocao PERMANENTE: 68,90 por 44 dias, depois 48,00 por 46 dias": [6890] * 44 + [4800] * 46,
}
for nome, hist in cases.items():
    ref = median_cents(hist)
    cur = min(hist) if "E)" not in nome else 4800
    o = mk(cur, ref)
    linha, prova = price_line(o, 10)
    print(f"{nome}\n   mediana(ref)={ref/100:.2f}  hoje={cur/100:.2f}  real_discount_pct={o.real_discount_pct}%  -> post: {linha!r}\n")

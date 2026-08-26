"""Modelo: fração de itens Shopee que (a) passam max_above_ref=1.00 contra a
mediana das mínimas diárias e (b) ganham modo A (>=10% abaixo da mediana),
para séries de preço sintéticas. Não é dado real — é aritmética do critério.
Também: estoque Shopee dia a dia com dedupe 30d e teto 100."""
import random, statistics
random.seed(7)

def serie(dias, sigma, p_flash, flash):
    base = 5000
    out = []
    for _ in range(dias):
        p = base * (1 + random.gauss(0, sigma))
        if random.random() < p_flash:
            p *= (1 - flash)
        out.append(int(p))
    return out

def avalia(sigma, p_flash, flash, n=20000, obs=30):
    passa = modo_a = 0
    for _ in range(n):
        hist = serie(obs, sigma, p_flash, flash)      # mínimas diárias já observadas
        hoje = hist[-1]
        ref = int(statistics.median(hist))
        if hoje > ref:            # selection.filter_offers / validate.check_price
            continue
        passa += 1
        disc = round((1 - hoje / ref) * 100) if hoje < ref else 0
        if disc >= 10:
            modo_a += 1
    return passa / n, modo_a / n

print("modelo de preço                              | passa max_above_ref | modo A (>=10% OFF)")
for sigma, pf, fl, nome in [(0.0, 0.0, 0.0, "preço fixo"),
                            (0.03, 0.0, 0.0, "ruído ±3%, sem flash"),
                            (0.05, 0.10, 0.20, "ruído ±5%, flash -20% em 10% dos dias"),
                            (0.10, 0.10, 0.25, "ruído ±10%, flash -25% em 10% dos dias")]:
    p, a = avalia(sigma, pf, fl)
    print(f"{nome:44s} | {p:6.0%}              | {a:6.0%}")

print()
print("Estoque Shopee dia a dia: 97 candidatas/run (dry-run do dono), turnover T itens novos/dia,")
print("dedupe 30d, teto 100/dia, 192 slots/dia. Publicações por dia:")
for T in (0, 5, 10, 20):
    posted_days = {}
    pool_seen_day = {}
    posts = []
    next_id = 0
    listing = list(range(97)); next_id = 97
    for day in range(1, 61):
        # turnover: T itens saem, T entram
        for _ in range(T):
            listing.pop(0); listing.append(next_id); next_id += 1
        elig = [i for i in listing if i not in posted_days or day - posted_days[i] >= 30]
        hoje = elig[:100]
        for i in hoje:
            posted_days[i] = day
        posts.append(len(hoje))
    print(f"  T={T:2d}: dias 1-7 = {posts[:7]}  | média dias 2-30 = {statistics.mean(posts[1:30]):.1f}/dia | dia 31 = {posts[30]} | média 60d = {statistics.mean(posts):.1f}/dia")

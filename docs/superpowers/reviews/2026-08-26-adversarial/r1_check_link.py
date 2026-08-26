"""check_link: does it distinguish an ATTRIBUTED link from a plain one? Does a
dead link that answers 403 pass?"""
import httpx
from afiliado import validate
from afiliado.config import load_config

cfg = load_config("config.yaml")


def run(name, url, handler):
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    try:
        validate.check_link(url, cfg, client=client)
        print(f"PASS   {name}: {url}")
    except Exception as exc:
        print(f"REJECT {name}: {url} -> {exc}")


# 1. Plain product URL (what Offer.product_url is for ML) — zero attribution.
run("ML plain /p/ (no ref, no matt_*)",
    "https://www.mercadolivre.com.br/p/MLB66637233",
    lambda r: httpx.Response(200, text="ok"))

# 2. Plain Shopee product URL (Offer.product_url) — zero attribution.
run("Shopee plain productLink",
    "https://shopee.com.br/product/1/123456",
    lambda r: httpx.Response(200, text="ok"))

# 3. meli.la short link whose vitrine hop STRIPS the ref (simulated) — final
#    host still allowed.
def vitrine_strips_ref(r):
    if r.url.host == "meli.la":
        return httpx.Response(302, headers={"location":
            "https://www.mercadolivre.com.br/social/jmbessa?matt_word=jmbessa&ref=ENC"})
    if r.url.path.startswith("/social/"):
        return httpx.Response(302, headers={"location":
            "https://www.mercadolivre.com.br/p/MLB66637233"})  # ref gone
    return httpx.Response(200, text="produto")
run("meli.la -> vitrine -> /p/ sem ref", "https://meli.la/abc", vitrine_strips_ref)

# 4. Dead / blocked short link: 403 without redirect.
run("meli.la responde 403 sem redirect", "https://meli.la/dead",
    lambda r: httpx.Response(403, text="blocked"))

# 5. Shortener returns 200 with an error page (link deactivated) — passes.
run("meli.la 200 'link nao encontrado'", "https://meli.la/gone",
    lambda r: httpx.Response(200, text="<html>Link não encontrado</html>"))

# 6. Redirect to an ALLOWED host but a competitor's affiliate ref.
def other_affiliate(r):
    if r.url.host == "meli.la":
        return httpx.Response(302, headers={"location":
            "https://www.mercadolivre.com.br/p/MLB66637233?matt_word=OUTRO&matt_tool=999"})
    return httpx.Response(200, text="ok")
run("meli.la -> /p/ com matt_word de OUTRO afiliado", "https://meli.la/x", other_affiliate)

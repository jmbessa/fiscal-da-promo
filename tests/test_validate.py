import httpx
import pytest

from afiliado import validate
from afiliado.errors import ValidationError
from afiliado.models import CopyParts, Post
from tests.test_models import make_offer

CFG = {
    "selection": {"min_discount_pct": 20, "price_min_brl": 20, "price_max_brl": 1000},
    "validation": {"allowed_domains": ["shopee.com.br", "shope.ee"]},
}


def client_for(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_check_link_accepts_redirect_to_allowed_domain():
    def handler(request):
        if request.url.host == "shope.ee":
            return httpx.Response(302, headers={"location": "https://shopee.com.br/p/1"})
        return httpx.Response(200, text="ok")
    validate.check_link("https://shope.ee/x", CFG, client=client_for(handler))


def test_check_link_rejects_wrong_domain():
    def handler(request):
        return httpx.Response(200, text="ok")
    with pytest.raises(ValidationError):
        validate.check_link("https://evil.com/x", CFG, client=client_for(handler))


def test_check_price_rules():
    validate.check_price(make_offer(), CFG)
    with pytest.raises(ValidationError):  # sem desconto real
        validate.check_price(make_offer(price_original_cents=24999), CFG)
    with pytest.raises(ValidationError):  # desconto abaixo do mínimo
        validate.check_price(make_offer(price_original_cents=26000), CFG)
    with pytest.raises(ValidationError):  # acima da faixa
        validate.check_price(
            make_offer(price_current_cents=150_000, price_original_cents=300_000), CFG)


def test_check_image():
    def handler(request):
        return httpx.Response(200, headers={"content-type": "image/jpeg"},
                              content=b"x" * 6000)
    validate.check_image("https://cf.shopee.com.br/file/a.jpg", client=client_for(handler))

    def bad(request):
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"x")
    with pytest.raises(ValidationError):
        validate.check_image("https://cf.shopee.com.br/file/a.jpg", client=client_for(bad))


def test_check_copy():
    validate.check_copy(CopyParts("🔥 50% OFF", "Bom e barato.", "Corre 👇"))
    with pytest.raises(ValidationError):  # vazio
        validate.check_copy(CopyParts("", "d", "c"))
    with pytest.raises(ValidationError):  # URL dentro do texto
        validate.check_copy(CopyParts("veja http://x.com", "d", "c"))
    with pytest.raises(ValidationError):  # headline longa demais
        validate.check_copy(CopyParts("a" * 61, "d", "c"))


def test_check_link_rejects_confusable_domain():
    def handler(request):
        if request.url.host == "shope.ee":
            return httpx.Response(302, headers={"location": "https://evilshopee.com.br/x"})
        return httpx.Response(200, text="ok")
    with pytest.raises(ValidationError):
        validate.check_link("https://shope.ee/x", CFG, client=client_for(handler))


def test_check_link_accepts_403_on_allowed_domain():
    def handler(request):
        return httpx.Response(403, text="Anti-bot")
    validate.check_link("https://shopee.com.br/p/1", CFG, client=client_for(handler))


def test_validate_post_checks_copy_before_network():
    def handler(request):
        raise AssertionError("Network request made when copy should have failed first")
    post = Post(
        offer=make_offer(),
        copy=CopyParts("", "valid description", "valid cta"),  # empty headline
        affiliate_link="https://shopee.com.br/p/1",
    )
    with pytest.raises(ValidationError):
        validate.validate_post(post, CFG, client=client_for(handler))

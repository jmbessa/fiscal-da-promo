import httpx
import pytest

from afiliado import validate
from afiliado.errors import ValidationError
from afiliado.models import CopyParts, Post
from tests.test_models import make_offer

CFG = {
    "selection": {"price_min_brl": 20, "price_max_brl": 1000,
                  "max_above_ref": 1.00, "require_price_ref": False},
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
    with pytest.raises(ValidationError):  # acima da faixa
        validate.check_price(
            make_offer(price_current_cents=150_000, price_original_cents=300_000), CFG)
    with pytest.raises(ValidationError):  # abaixo da faixa
        validate.check_price(make_offer(price_current_cents=999), CFG)


def test_check_price_aceita_oferta_sem_desconto():
    # O portão de desconto matava o ML inteiro (discount_pct == 0 por
    # construção) e qualquer post de volume. Agora passa.
    validate.check_price(make_offer(price_original_cents=24999), CFG)
    validate.check_price(make_offer(price_original_cents=26000), CFG)
    validate.check_price(
        make_offer(source="meli", price_original_cents=7890, price_current_cents=7890,
                   price_ref_cents=7890), CFG)


def test_check_price_rejeita_acima_da_referencia():
    # Rede de segurança que roda DEPOIS do refresh_price: pega a oferta que
    # encareceu entre a busca e a publicação.
    with pytest.raises(ValidationError, match="acima da referência"):
        validate.check_price(
            make_offer(price_ref_cents=2600, price_current_cents=3390), CFG)
    # no preço da referência (ou abaixo) segue publicável
    validate.check_price(make_offer(price_ref_cents=2600, price_current_cents=2600), CFG)


def test_check_price_max_above_ref_com_folga():
    cfg = {**CFG, "selection": {**CFG["selection"], "max_above_ref": 1.10}}
    validate.check_price(make_offer(price_ref_cents=2600, price_current_cents=2860), cfg)
    with pytest.raises(ValidationError):
        validate.check_price(make_offer(price_ref_cents=2600, price_current_cents=2861), cfg)


def test_check_price_require_price_ref():
    cfg = {**CFG, "selection": {**CFG["selection"], "require_price_ref": True}}
    with pytest.raises(ValidationError, match="sem referência"):
        validate.check_price(make_offer(), cfg)
    validate.check_price(make_offer(price_ref_cents=2600, price_current_cents=2500), cfg)


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


def test_check_link_rejects_404_on_allowed_domain():
    def handler(request):
        return httpx.Response(404, text="Not Found")
    with pytest.raises(ValidationError):
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

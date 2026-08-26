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


def _sem_rede(request):
    raise AssertionError(f"nenhuma requisição é esperada aqui: {request.url}")


@pytest.fixture
def rede_proibida(monkeypatch):
    """Qualquer requisição httpx (cliente novo ou injetado) explode: prova que
    o portão de link não toca a rede — nem no link de afiliado (C6)."""
    monkeypatch.setattr(httpx.Client, "send", lambda self, request, **kw: _sem_rede(request))


# -- check_link: portão OFFLINE (C6) ------------------------------------------

def test_check_link_aceita_dominio_permitido_e_subdominio(rede_proibida):
    validate.check_link("https://shope.ee/x", CFG)
    validate.check_link("https://s.shopee.com.br/xyz", CFG)
    validate.check_link("https://shopee.com.br/product/1/123", CFG)


def test_check_link_nao_faz_rede(rede_proibida):
    # Um clique do próprio pipeline no link de afiliado (IP de datacenter, UA
    # falso, segundos após a geração) é a assinatura de tráfego inválido — e
    # contaminava o teste de atribuição do ML em cada --dry-run.
    validate.check_link("https://shope.ee/x", CFG)


def test_check_link_rejects_wrong_domain(rede_proibida):
    with pytest.raises(ValidationError):
        validate.check_link("https://evil.com/x", CFG)


def test_check_link_rejects_confusable_domain(rede_proibida):
    with pytest.raises(ValidationError):
        validate.check_link("https://evilshopee.com.br/x", CFG)
    with pytest.raises(ValidationError):
        validate.check_link("https://shopee.com.br.evil.com/x", CFG)


def test_check_link_exige_https(rede_proibida):
    with pytest.raises(ValidationError, match="https"):
        validate.check_link("http://shope.ee/x", CFG)
    with pytest.raises(ValidationError):
        validate.check_link("shope.ee/x", CFG)


def test_check_link_rejeita_espaco_e_caractere_de_controle(rede_proibida):
    for url in ("https://shope.ee/x y", "https://shope.ee/x\n", "https://shope.ee/\tx",
                "https://shope.ee/x\x00", ""):
        with pytest.raises(ValidationError):
            validate.check_link(url, CFG)


def test_check_link_nao_aceita_mais_client():
    # O parâmetro saiu de propósito: sem cliente HTTP não há como alguém
    # reintroduzir o GET no link por engano.
    with pytest.raises(TypeError):
        validate.check_link("https://shope.ee/x", CFG, client=httpx.Client())


# -- check_price ----------------------------------------------------------------

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


# -- check_image (a única checagem que ainda vai à rede) -------------------------

def test_check_image():
    def handler(request):
        return httpx.Response(200, headers={"content-type": "image/jpeg"},
                              content=b"x" * 6000)
    validate.check_image("https://cf.shopee.com.br/file/a.jpg", client=client_for(handler))

    def bad(request):
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"x")
    with pytest.raises(ValidationError):
        validate.check_image("https://cf.shopee.com.br/file/a.jpg", client=client_for(bad))


# -- check_copy -------------------------------------------------------------------

def test_check_copy():
    validate.check_copy(CopyParts("🔥 50% OFF", "Bom e barato.", "Corre 👇"))
    with pytest.raises(ValidationError):  # vazio
        validate.check_copy(CopyParts("", "d", "c"))
    with pytest.raises(ValidationError):  # URL dentro do texto
        validate.check_copy(CopyParts("veja http://x.com", "d", "c"))
    with pytest.raises(ValidationError):  # headline longa demais
        validate.check_copy(CopyParts("a" * 61, "d", "c"))


# -- validate_post ------------------------------------------------------------------

def test_validate_post_checks_copy_before_network():
    post = Post(
        offer=make_offer(),
        copy=CopyParts("", "valid description", "valid cta"),  # empty headline
        affiliate_link="https://shopee.com.br/p/1",
    )
    with pytest.raises(ValidationError):
        validate.validate_post(post, CFG, client=client_for(_sem_rede))


def test_validate_post_so_a_imagem_usa_o_client():
    chamadas = []

    def handler(request):
        chamadas.append(request.url.host)
        return httpx.Response(200, headers={"content-type": "image/jpeg"},
                              content=b"x" * 6000)

    post = Post(offer=make_offer(), copy=CopyParts("h", "d", "c"),
                affiliate_link="https://shope.ee/x")
    validate.validate_post(post, CFG, client=client_for(handler))
    assert chamadas == ["cf.shopee.com.br"]   # imagem sim, link de afiliado nunca


def test_validate_post_skip_image_nao_toca_a_rede():
    # Dry-run (A10): nada de rede além de fetch_offers/refresh_price.
    post = Post(offer=make_offer(), copy=CopyParts("h", "d", "c"),
                affiliate_link="https://shope.ee/x")
    validate.validate_post(post, CFG, client=client_for(_sem_rede), skip_image=True)

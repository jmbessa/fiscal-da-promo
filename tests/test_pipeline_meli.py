"""Teste de integração fim a fim: pipeline.run -> selection -> validate REAIS
para uma oferta do Mercado Livre. Só a fonte e o canal são dublês; o HTTP da
validação de link/imagem vai por httpx.MockTransport (nunca a rede real).

Existe porque nenhum outro teste exercitava esse caminho — foi a ausência
dele que deixou passar, por 156 testes verdes, dois bugs Critical que faziam
o ML publicar zero ofertas em silêncio: `selection.category_ids` só
reconhecia categorias da Shopee, e `validation.allowed_domains` não incluía
domínios do ML."""

import httpx

from afiliado import llm, pipeline, validate
from afiliado.channels.base import PublishResult
from tests.test_models import make_offer
from afiliado.state import StateDB

# Espelha as partes relevantes do config.yaml real (category_ids por fonte e
# allowed_domains com os domínios do ML) — o resto é o mínimo necessário para
# o pipeline rodar sem depender de rede/LLM real.
CFG = {
    "selection": {
        "posts_per_run": 1, "min_discount_pct": 20, "price_min_brl": 20,
        "price_max_brl": 1000, "dedupe_days": 30,
        "category_ids": {"shopee": ["100630", "100636"], "meli": []},
        "ev_weights": {"popularity": 0.3},
    },
    "llm": {"model": "haiku"},
    "copy": {"tone": "empolgado, direto, sem exageros enganosos, pt-BR"},
    "validation": {"allowed_domains": ["shopee.com.br", "shope.ee",
                                        "mercadolivre.com.br", "mercadolivre.com", "meli.la"]},
}


class FakeMeliSource:
    name = "meli"

    def __init__(self, offer):
        self._offer = offer

    def fetch_offers(self, cfg):
        return [self._offer]

    def resolve_affiliate_link(self, offer):
        return "https://mercadolivre.com/sec/abc123"


class FakeChannel:
    name = "telegram"

    def __init__(self):
        self.sent = []

    def publish(self, post):
        self.sent.append(post)
        return PublishResult(True, str(len(self.sent)))


def _mock_client() -> httpx.Client:
    def handler(request: httpx.Request):
        if request.url.host == "mercadolivre.com":
            return httpx.Response(200, text="ok")  # link de afiliado
        if request.url.host == "http2.mlstatic.com":
            return httpx.Response(
                200, headers={"content-type": "image/jpeg"}, content=b"x" * 6000)  # imagem
        raise AssertionError(f"host inesperado no teste: {request.url.host}")
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_pipeline_publica_oferta_meli_ponta_a_ponta(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)  # sem rede/CLI real; força fallback

    offer = make_offer(
        source="meli", item_id="MLB123456", category="MLB1000",
        title="Fone de Ouvido Bluetooth XYZ", commission_pct=0.0,
        price_current_cents=14990, price_original_cents=19990,
        image_url="https://http2.mlstatic.com/D_NQ_NP_123-W.jpg",
        product_url="https://produto.mercadolivre.com.br/MLB-123456-fone-de-ouvido",
    )
    db = StateDB(tmp_path / "s.db")
    ch = FakeChannel()

    def validator(post, cfg):
        return validate.validate_post(post, cfg, client=_mock_client())

    summary = pipeline.run(CFG, [FakeMeliSource(offer)], [ch], db, validator=validator)

    assert summary.discarded == []
    assert len(ch.sent) == 1
    assert ch.sent[0].offer.item_id == "MLB123456"
    assert len(summary.published) == 1
    db.close()

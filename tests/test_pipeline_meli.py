"""Teste de integração fim a fim: pipeline.run -> selection -> validate REAIS
para uma oferta do Mercado Livre. Só a fonte e o canal são dublês; o HTTP da
validação de link/imagem vai por httpx.MockTransport (nunca a rede real).

Existe porque nenhum outro teste exercitava esse caminho — foi a ausência
dele que deixou passar, por 156 testes verdes, bugs Critical que faziam o ML
publicar zero ofertas em silêncio: `selection.category_ids` só reconhecia
categorias da Shopee, `validation.allowed_domains` não incluía domínios do
ML, e `commission_pct` fixo em 0.0 zerava o `ev_score` de toda oferta do ML
— com `selection.min_ev_brl` real (0.50) ativo, ela nunca sobreviveria ao
piso (nem competiria no ranking contra a Shopee, mesmo sem piso)."""

import httpx

from afiliado import llm, pipeline, validate
from afiliado.channels.base import PublishResult
from tests.test_models import make_offer
from afiliado.state import StateDB

# Mesmo valor de config.yaml -> meli.commission_pct (comissão média estimada,
# já que a busca não traz comissão por item).
MELI_COMMISSION_PCT = 4.0

# Espelha as partes relevantes do config.yaml real (category_ids por fonte,
# allowed_domains com os domínios do ML, e min_ev_brl — o piso real de
# produção, para provar que a oferta do ML sobrevive a ele) — o resto é o
# mínimo necessário para o pipeline rodar sem depender de rede/LLM real.
CFG = {
    "selection": {
        "posts_per_run": 1, "price_min_brl": 20,
        "price_max_brl": 1000, "dedupe_days": 30,
        "category_ids": {"shopee": ["100630", "100636"], "meli": []},
        "max_above_ref": 1.00, "require_price_ref": False,
        "min_real_discount_pct": 10, "ref_window_days": 90,
        "ref_min_observations": 14,
        "ev_weights": {"popularity": 0.3, "discount": 0.5},
        "min_ev_brl": 0.50,
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
        title="Fone de Ouvido Bluetooth XYZ", commission_pct=MELI_COMMISSION_PCT,
        price_current_cents=14990, price_original_cents=19990,
        image_url="https://http2.mlstatic.com/D_NQ_NP_123-W.jpg",
        product_url="https://produto.mercadolivre.com.br/MLB-123456-fone-de-ouvido",
    )
    db = StateDB(tmp_path / "s.db")
    ch = FakeChannel()

    def validator(post, cfg):
        return validate.validate_post(post, cfg, client=_mock_client())

    summary = pipeline.run(CFG, [FakeMeliSource(offer)], [ch], db, validator=validator)

    # Com commission_pct=0.0 (regressão), ev_score=0.0 < min_ev_brl=0.50 e a
    # oferta é descartada por selection.filter_offers ANTES do try/except que
    # alimenta summary.discarded em pipeline.run — o mesmo "some em silêncio"
    # das outras duas correções. Ver verificação manual no relatório.
    assert summary.discarded == []
    assert len(ch.sent) == 1
    assert ch.sent[0].offer.item_id == "MLB123456"
    assert len(summary.published) == 1
    db.close()


def test_pipeline_publica_a_entrada_sem_historico_em_modo_b(tmp_path, monkeypatch):
    """Fase 5J ponta a ponta, com a fonte de PRODUÇÃO: uma entrada de pool com
    a régua toda zerada atravessa `fetch_offers` -> `enrich_offers` ->
    `filter_offers` -> `refresh_price` -> `verdict` -> validação e é publicada
    — em modo B, com o preço VIVO e sem alegar desconto nenhum.

    Este é o teste que prova a fase inteira: cada portão do caminho já matou
    uma fonte inteira em silêncio alguma vez, e o preço só existe a partir do
    `refresh_price`, que roda no fim."""
    from afiliado.sources.meli import MeliSource
    from tests.test_meli import SEM_HISTORICO, _anuncio, write_links, write_pool

    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    pool = write_pool(tmp_path / "pool.json", [
        {"product_id": "MLB99", "title": "Creatina Monohidratada 500g", **SEM_HISTORICO,
         "image_url": "https://http2.mlstatic.com/D_NQ_NP_123-W.jpg",
         "category": "MLB264586", "buy_box_item_id": "MLB777",
         "sales": 250000, "rating": 4.9}])
    # Fase 5M: o link é do ANÚNCIO. O vendedor a R$ 30,90 não tem link e não
    # pode virar preço publicado — quem publica é o linkado mais barato.
    links = write_links(tmp_path / "links.json",
                        {"MLB99": {"MLB777": "https://mercadolivre.com/sec/abc123",
                                   "MLB888": "https://mercadolivre.com/sec/def456"}})

    def api(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "TOK", "expires_in": 21600})
        if request.url.path == "/products/MLB99/items":
            return httpx.Response(200, json={"results": [
                _anuncio("MLB666", 30.90), _anuncio("MLB777", 33.90),
                _anuncio("MLB888", 39.90)]})
        raise AssertionError(f"caminho inesperado: {request.url.path}")

    src = MeliSource("cid", "sec", token_path=tmp_path / "t.json", links_path=links,
                     client=httpx.Client(transport=httpx.MockTransport(api)))
    cfg = {**CFG, "meli": {"offers_path": str(pool), "commission_pct": MELI_COMMISSION_PCT}}
    db = StateDB(tmp_path / "s.db")
    ch = FakeChannel()

    summary = pipeline.run(cfg, [src], [ch], db,
                           validator=lambda post, c: validate.validate_post(
                               post, c, client=_mock_client()))

    assert summary.discarded == [], summary.discarded
    assert len(ch.sent) == 1
    post = ch.sent[0]
    assert post.offer.price_current_cents == 3390        # o preço VIVO do anúncio linkado
    assert post.offer.anuncio_id == "MLB777"             # ...e é dele o link do post
    assert post.affiliate_link == "https://mercadolivre.com/sec/abc123"
    assert post.verdict.mode == "B"
    assert post.verdict.discount_pct == 0 and post.verdict.seal == ""
    assert "OFF" not in post.message_text and "<s>" not in post.message_text
    assert "R$ 33,90" in post.message_text
    # E o resumo diz que este pool está 100% em modo B.
    assert ("🏷️ meli: 0 de 1 com régua curada; 1 em modo B esperando histórico"
            in summary.discovery)
    # O preço vivo virou a PRIMEIRA observação do histórico próprio: é assim
    # que a régua começa a se formar (14 dias distintos e ela gradua).
    assert db.price_history("meli", "MLB99", 1) == [3390]
    db.close()

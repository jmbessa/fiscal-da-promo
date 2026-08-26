"""Reproduções adversariais — RÉGUA HONESTA E O QUINTO ZERO SILENCIOSO.

Cada teste AFIRMA o comportamento defeituoso: passar == achado reproduzido.
Roda fora do repo (somente leitura no código).
"""
import dataclasses
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO = Path(r"G:\Biblioteca\Documentos\Projetos\Afiliado\.claude\worktrees\fase3b-meli-hibrido")
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO))

import httpx  # noqa: E402
import pytest  # noqa: E402

from afiliado import cli, creative, llm, message, pipeline, pricing, selection, validate  # noqa: E402
from afiliado.channels.base import PublishResult  # noqa: E402
from afiliado.channels.instagram_feed import InstagramFeedChannel  # noqa: E402
from afiliado.config import load_config  # noqa: E402
from afiliado.models import CopyParts, Offer, Post  # noqa: E402
from afiliado.sources import meli as meli_mod  # noqa: E402
from afiliado.sources.meli import MeliSource  # noqa: E402
from afiliado.state import StateDB  # noqa: E402
from afiliado.watchlist import PriceFloor, PriceRef, Watchlist  # noqa: E402
from tests.test_models import make_offer  # noqa: E402

CONFIG_REAL = REPO / "config.yaml"
POOL_REAL = REPO / "data/meli_offers.json"

CFG = {
    "selection": {"posts_per_run": 1, "price_min_brl": 20,
                  "price_max_brl": 1000, "dedupe_days": 30, "category_ids": [],
                  "max_above_ref": 1.00, "require_price_ref": False,
                  "min_real_discount_pct": 10, "ref_window_days": 90,
                  "ref_min_observations": 5, "seal_tolerance": 1.05,
                  "ev_weights": {"popularity": 0.3, "discount": 0.5}},
    "llm": {"model": "haiku"},
    "copy": {"tone": "pt-BR"},
    "validation": {"allowed_domains": ["shope.ee"]},
}


def hoje_utc() -> date:
    return datetime.now(timezone.utc).date()


def seed(db: StateDB, cents: list[int], source="shopee", item_id="123456", start_days_ago=1):
    """cents[0] = start_days_ago dias atrás, cents[1] = um dia antes, ..."""
    h = hoje_utc()
    for i, v in enumerate(cents):
        db.record_price(source, item_id, v, day=(h - timedelta(days=start_days_ago + i)).isoformat())


class FakeSource:
    name = "shopee"

    def __init__(self, offers):
        self._offers = offers

    def fetch_offers(self, cfg):
        return self._offers

    def resolve_affiliate_link(self, offer):
        return "https://shope.ee/ok"


class FakeMeli:
    name = "meli"
    pool_warning = None

    def __init__(self, offers, live_fn):
        self._offers = offers
        self._live_fn = live_fn

    def fetch_offers(self, cfg):
        return self._offers

    def refresh_price(self, offer):
        return dataclasses.replace(offer, price_current_cents=self._live_fn(offer))

    def resolve_affiliate_link(self, offer):
        return "https://www.mercadolivre.com.br/x"


class FakeChannel:
    name = "telegram"

    def __init__(self):
        self.sent = []

    def publish(self, post):
        self.sent.append(post)
        return PublishResult(True, str(len(self.sent)))


def no_validate(post, cfg, client=None):
    return None


def _sem_rede(request):
    raise AssertionError(f"rede inesperada: {request.url}")


@pytest.fixture
def pool_no_prazo(monkeypatch):
    gerado = date.fromisoformat(json.loads(POOL_REAL.read_text(encoding="utf-8"))["generated_at"])

    class _D(date):
        @classmethod
        def today(cls):
            return gerado
    monkeypatch.setattr(meli_mod, "date", _D)
    return gerado


def _meli_real(tmp_path) -> MeliSource:
    return MeliSource("CID", "CSECRET", token_path=tmp_path / "t.json",
                      links_path=tmp_path / "l.json",
                      client=httpx.Client(transport=httpx.MockTransport(_sem_rede)))


def _cfg_real() -> dict:
    cfg = load_config(CONFIG_REAL)
    cfg["meli"]["offers_path"] = str(POOL_REAL)   # rodamos fora do repo
    return cfg


WL_FRESCA = Watchlist(generated_at=date.today(), valid_days=14)   # sem aviso "Sem watchlist"


# ---------------------------------------------------------------------------
# 1. Watchlist vencida: a régua inteira muda de número (e de veredito) num dia
# ---------------------------------------------------------------------------

def test_1a_stale_watchlist_inverte_veredito_do_selo(tmp_path, monkeypatch):
    """Mesma oferta, mesmo histórico: watchlist FRESCA diz 'sem selo' (24999 >
    piso curado 24000); no dia em que a watchlist vence, watchlist=None e o
    texto passa a dizer 'Menor preço já registrado (verificado)' — para um
    preço 4,2% ACIMA do piso que a watchlist acabou de dizer que existe."""
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    offer = make_offer(price_current_cents=24999)

    def run(wl):
        db = StateDB(tmp_path / f"s{id(wl)}.db")
        # 24000 x2 + 26000 x3 (+ hoje 24999) -> mediana 25499 >= 24999 (passa no
        # max_above_ref), piso do log = 24000. (Com 24000 x5 a mediana seria 24000
        # e o próprio price_log REJEITARIA a oferta — ver achado 3c.)
        seed(db, [24000] * 2 + [26000] * 3)
        ch = FakeChannel()
        pipeline.run(CFG, [FakeSource([offer])], [ch], db, validator=no_validate, watchlist=wl)
        db.close()
        return ch.sent[0].message_text

    fresca = Watchlist(generated_at=date.today(), valid_days=14,
                       price_floors={"123456": PriceFloor(24000, 191)})
    vencida = Watchlist(generated_at=date.today() - timedelta(days=15), valid_days=14,
                        price_floors={"123456": PriceFloor(24000, 191)})
    t_fresca, t_vencida = run(fresca), run(vencida)
    assert "Menor preço" not in t_fresca                      # veredito curado: NÃO é mínima
    assert "Menor preço já registrado (verificado)" in t_vencida   # dia seguinte: vira mínima


def test_1b_stale_watchlist_troca_o_De_e_a_porcentagem(tmp_path, monkeypatch):
    """Watchlist fresca com price_ref 2600 -> 'De: R$ 26,00 (27% OFF)'.
    Vencida -> referência cai para a mediana do price_log (2200) -> 'De: R$
    22,00 (14% OFF)'. Dois 'De' diferentes para o mesmo produto em dias
    consecutivos, sem nenhum aviso além de 'Watchlist vencida'."""
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    offer = make_offer(price_current_cents=2190)

    def run(wl):
        db = StateDB(tmp_path / f"r{id(wl)}.db")
        seed(db, [2500] * 5)
        ch = FakeChannel()
        s = pipeline.run(CFG, [FakeSource([offer])], [ch], db, validator=no_validate, watchlist=wl)
        db.close()
        return ch.sent[0].message_text, s

    fresca = Watchlist(generated_at=date.today(), valid_days=14,
                       price_refs={"123456": PriceRef(3000, 90)})
    vencida = Watchlist(generated_at=date.today() - timedelta(days=15), valid_days=14,
                        price_refs={"123456": PriceRef(3000, 90)})
    t1, _ = run(fresca)
    t2, s2 = run(vencida)
    assert "De: <s>R$ 30,00</s>" in t1 and "(27% OFF)" in t1
    assert "De: <s>R$ 25,00</s>" in t2 and "(12% OFF)" in t2
    assert not any("referência" in w for w in s2.warnings)


def test_1c_stale_watchlist_sem_price_log_derruba_para_modo_B(tmp_path, monkeypatch):
    """Cenário real de hoje (price_log nasce vazio): watchlist vence ->
    toda alegação de desconto/selo da Shopee some de um dia para o outro."""
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    offer = make_offer(price_current_cents=2190)
    wl_ok = Watchlist(generated_at=date.today(), valid_days=14,
                      price_refs={"123456": PriceRef(3000, 90)},
                      price_floors={"123456": PriceFloor(2190, 191)})
    wl_old = dataclasses.replace(wl_ok, generated_at=date.today() - timedelta(days=15))
    out = {}
    for nome, wl in (("ok", wl_ok), ("old", wl_old)):
        db = StateDB(tmp_path / f"{nome}.db")
        ch = FakeChannel()
        pipeline.run(CFG, [FakeSource([offer])], [ch], db, validator=no_validate, watchlist=wl)
        db.close()
        out[nome] = ch.sent[0].message_text
    assert "27% OFF" in out["ok"] and "Menor preço dos últimos 6 meses" in out["ok"]
    assert "OFF" not in out["old"] and "Menor preço" not in out["old"]


# ---------------------------------------------------------------------------
# 2. Config = 0 vira o default em silêncio
# ---------------------------------------------------------------------------

def test_2a_min_real_discount_pct_zero_vira_10():
    cfg = {"selection": {"min_real_discount_pct": 0, "seal_tolerance": 1.05}}
    assert cli._regua(cfg)["min_real_discount_pct"] == 10


def test_2b_pipeline_ignora_min_real_discount_pct_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    cfg = json.loads(json.dumps(CFG))
    cfg["selection"]["min_real_discount_pct"] = 0
    offer = make_offer(price_ref_cents=2600, price_current_cents=2500)   # 4% verificado
    db = StateDB(tmp_path / "s.db")
    ch = FakeChannel()
    pipeline.run(cfg, [FakeSource([offer])], [ch], db, validator=no_validate)
    db.close()
    texto = ch.sent[0].message_text
    assert "De: <s>" not in texto and "(4% OFF)" not in texto   # dono pediu 0, régua usou 10
    # ...e a copy de fallback ignora a régua: headline alega "4% OFF" sobre um
    # bloco de preço em modo B (sem De/Por). Texto contraditório.
    assert "🔥 Oferta: 4% OFF" in texto


def test_2c_ref_min_observations_zero_vira_5(tmp_path):
    cfg = {"selection": {"ref_min_observations": 0, "ref_window_days": 90}}
    db = StateDB(tmp_path / "s.db")
    seed(db, [2600, 2500, 2400])
    (out,) = pricing.enrich_offers([make_offer()], db, None, cfg)
    db.close()
    assert out.price_ref_cents == 0                   # 3 obs >= 0 deveria valer


def test_2d_ref_window_days_zero_vira_90(tmp_path):
    cfg = {"selection": {"ref_window_days": 0, "ref_min_observations": 5}}
    db = StateDB(tmp_path / "s.db")
    seed(db, [2600] * 6, start_days_ago=60)
    (out,) = pricing.enrich_offers([make_offer()], db, None, cfg)
    db.close()
    assert out.price_ref_cents == 2600                # janela 0 aceitou obs de 60 dias


def test_2e_seal_tolerance_zero_vira_1_05():
    assert cli._regua({"selection": {"seal_tolerance": 0}})["seal_tolerance"] == 1.05


def test_2f_posts_per_run_zero_publica_nada_sem_aviso(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    cfg = json.loads(json.dumps(CFG))
    cfg["selection"]["posts_per_run"] = 0
    db = StateDB(tmp_path / "s.db")
    ch = FakeChannel()
    s = pipeline.run(cfg, [FakeSource([make_offer(item_id=str(i)) for i in range(5)])],
                     [ch], db, validator=no_validate, watchlist=WL_FRESCA)
    db.close()
    assert ch.sent == [] and s.published == [] and s.discarded == [] and s.warnings == []


# ---------------------------------------------------------------------------
# 3. A mediana fabrica um "De" que nunca foi preço; rampa vira referência
# ---------------------------------------------------------------------------

def test_3a_mediana_par_inventa_um_De_que_nunca_existiu(tmp_path):
    db = StateDB(tmp_path / "s.db")
    seed(db, [2600, 2600, 6890, 6890, 6890])          # 5 dias anteriores
    offer = make_offer(price_current_cents=2600)
    pricing.record_observations(db, [offer])          # hoje: 2600 -> 6 obs (par)
    hist = db.price_history("shopee", "123456", 90)
    (out,) = pricing.enrich_offers([offer], db, None, CFG)
    db.close()
    assert out.price_ref_cents == 4745
    assert 4745 not in hist                           # nunca foi preço de ninguém
    linha, _ = pricing.price_line(out, 10)
    assert linha == "De: R$ 47,45 | Por: R$ 26,00 (45% OFF)"


def test_3b_rampa_de_preco_vira_referencia_e_promo_falsa_e_verificada(tmp_path):
    """Preço real R$100 por 5 dias; vendedor sobe para R$150 e segura 7 dias;
    hoje 'promoção' a R$120. A régua diz 'De: R$ 150 | Por: R$ 120 (20% OFF)'
    — 20% ACIMA do preço de duas semanas atrás, carimbado como verificado."""
    db = StateDB(tmp_path / "s.db")
    seed(db, [15000] * 7 + [10000] * 5)              # ontem..7 dias: 150; 8..12 dias: 100
    offer = make_offer(price_current_cents=12000)
    pricing.record_observations(db, [offer])
    (out,) = pricing.enrich_offers([offer], db, None, CFG)
    assert out.price_ref_cents == 15000
    assert out.real_discount_pct == 20
    assert selection.filter_offers([out], db, CFG) == [out]
    db.close()
    assert pricing.price_line(out, 10)[0] == "De: R$ 150,00 | Por: R$ 120,00 (20% OFF)"


def test_3c_preco_subindo_e_segurando_passa_no_max_above_ref(tmp_path):
    """Subida estritamente crescente É bloqueada (não refutado); mas basta o
    preço novo segurar > metade da janela observada que ele vira 'típico'."""
    db = StateDB(tmp_path / "s.db")
    seed(db, [10400, 10300, 10200, 10100])           # subindo
    sobe = make_offer(price_current_cents=10500)
    pricing.record_observations(db, [sobe])
    (o1,) = pricing.enrich_offers([sobe], db, None, CFG)
    assert selection.filter_offers([o1], db, CFG) == []      # bloqueado: acima da mediana
    db.close()

    db = StateDB(tmp_path / "s2.db")
    seed(db, [15000] * 6 + [10000] * 5)
    plato = make_offer(price_current_cents=15000)
    pricing.record_observations(db, [plato])
    (o2,) = pricing.enrich_offers([plato], db, None, CFG)
    assert o2.price_ref_cents == 15000
    assert selection.filter_offers([o2], db, CFG) == [o2]    # +50% virou "o típico"
    db.close()


# ---------------------------------------------------------------------------
# 4. Arredondamento: 9,5x% vira "10% OFF" e passa no mínimo 10
# ---------------------------------------------------------------------------

def test_4_round_deixa_9_5_passar_como_10():
    pior = None
    for ref in range(2000, 20001):
        # menor cur tal que o desconto real esteja em [9.5, 10)
        cur = -(-ref * 905 // 1000)  # ceil(ref*0.905) -> desconto real <= 9.5
        for c in (cur - 1, cur, cur + 1):
            if c <= 0:
                continue
            real = (1 - c / ref) * 100
            o = make_offer(price_ref_cents=ref, price_current_cents=c)
            if real < 10 and o.real_discount_pct == 10:
                if pior is None or real < pior[0]:
                    pior = (real, ref, c)
    assert pior is not None
    real, ref, c = pior
    linha, _ = pricing.price_line(make_offer(price_ref_cents=ref, price_current_cents=c), 10)
    assert "(10% OFF)" in linha
    print(f"\n[4] menor desconto real exibido como 10% OFF: {real:.4f}% (ref={ref} cur={c}) -> {linha}")


# ---------------------------------------------------------------------------
# 5. ML: referência é foto de um dia; +1 centavo mata as 38 e queima LLM
# ---------------------------------------------------------------------------

def test_5a_mais_um_por_cento_rejeita_38_de_38(tmp_path, pool_no_prazo):
    cfg = _cfg_real()
    offers =_meli_real(tmp_path).fetch_offers(cfg)
    assert len(offers) == 38
    rej_1pct = rej_1cent = 0
    for o in offers:
        for delta, bucket in ((int(o.price_ref_cents * 0.01), "pct"), (1, "cent")):
            live = dataclasses.replace(o, price_current_cents=o.price_ref_cents + delta)
            try:
                validate.check_price(live, cfg)
            except Exception:
                if bucket == "pct":
                    rej_1pct += 1
                else:
                    rej_1cent += 1
    assert rej_1pct == 38 and rej_1cent == 38
    # exatamente na referência passa (tolerância ZERO)... exceto UM item do
    # pool que já nasce morto: MLB36931922 ref R$19,90 < price_min_brl 20.
    # Só publica se o preço ao vivo for >= 20,00 E <= 19,90: impossível.
    mortos = []
    for o in offers:
        try:
            validate.check_price(o, cfg)
        except Exception as exc:
            mortos.append((o.item_id, str(exc)))
    assert mortos == [("MLB36931922", "preço R$19.90 fora da faixa")]


def test_5b_run_com_ML_1_centavo_acima_queima_38_chamadas_LLM_e_publica_zero(tmp_path, pool_no_prazo, monkeypatch):
    cfg = _cfg_real()
    offers =_meli_real(tmp_path).fetch_offers(cfg)
    chamadas = []
    monkeypatch.setattr(llm, "ask_json", lambda prompt, **k: chamadas.append(prompt[:40]) or None)
    src = FakeMeli(offers, live_fn=lambda o: o.price_ref_cents + 1)
    db = StateDB(tmp_path / "s.db")
    ch = FakeChannel()
    s = pipeline.run(cfg, [src], [ch], db,
                     validator=lambda post, cfg, client=None: validate.check_price(post.offer, cfg))
    db.close()
    copias = [c for c in chamadas if not c.startswith("Você seleciona")]
    # 37, não 38: MLB36931922 (R$19,90 < price_min_brl) some em filter_offers
    # sem entrar em `discarded` nem em `warnings` — descarte silencioso.
    assert s.published == [] and len(s.discarded) == 37
    assert not any("MLB36931922" in d or "Cordão" in d for d in s.discarded)
    # write_copy tenta 2x quando o LLM falha: 37 ofertas x 2 = 74 chamadas de
    # copy ANTES de check_price, todas jogadas fora. + 1 de ranking = 75/run.
    assert len(copias) == 74
    print(f"\n[5b] LLM calls no run: {len(chamadas)} (ranking 1 + copy {len(copias)}), publicados 0")


def test_5c_ML_registra_no_price_log_o_preco_do_pool_nunca_o_ao_vivo(tmp_path, pool_no_prazo, monkeypatch):
    cfg = _cfg_real()
    offers =_meli_real(tmp_path).fetch_offers(cfg)[:1]
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    src = FakeMeli(offers, live_fn=lambda o: 6990)           # ao vivo: 69,90
    db = StateDB(tmp_path / "s.db")
    ch = FakeChannel()
    pipeline.run(cfg, [src], [ch], db, validator=no_validate)
    hist = db.price_history("meli", offers[0].item_id, 1)
    db.close()
    assert ch.sent[0].offer.price_current_cents == 6990
    assert hist == [offers[0].price_ref_cents]                 # 7890 gravado; 6990 descartado
    assert 6990 not in hist


def test_5d_min_ev_nao_mata_nenhum_item_do_pool(tmp_path, pool_no_prazo):
    cfg = _cfg_real()
    offers =_meli_real(tmp_path).fetch_offers(cfg)
    evs = sorted(selection.ev_score(o, cfg) for o in offers)
    print(f"\n[5d] min ev do pool = {evs[0]:.3f} (piso 0.50) -> NÃO refutado")
    assert evs[0] >= 0.5


# ---------------------------------------------------------------------------
# 6. Pool: diagnóstico errado e descarte parcial silencioso
# ---------------------------------------------------------------------------

def _pool(tmp_path, offers):
    p = tmp_path / "pool.json"
    p.write_text(json.dumps({"generated_at": date.today().isoformat(), "valid_days": 30,
                             "offers": offers}), encoding="utf-8")
    return p


def test_6a_pool_inteiro_sem_piso_vira_aviso_errado(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    p = _pool(tmp_path, [{"product_id": f"MLB{i}", "title": f"t{i}", "price_ref_cents": 5000}
                         for i in range(3)])
    src = MeliSource("c", "s", token_path=tmp_path / "t", links_path=tmp_path / "l",
                     client=httpx.Client(transport=httpx.MockTransport(_sem_rede)))
    cfg = {**CFG, "meli": {"offers_path": str(p), "commission_pct": 4.0}}
    db = StateDB(tmp_path / "s.db")
    s = pipeline.run(cfg, [src], [FakeChannel()], db, validator=no_validate)
    db.close()
    assert src.pool_warning.startswith("3 entrada(s) do pool ignorada(s)")
    assert any("pool vazio ou vencido — rode /meli-links-refresh" in w for w in s.warnings)
    assert not any("ignorada" in w for w in s.warnings)        # motivo real nunca chega ao ops


def test_6b_pool_sem_price_ref_cents_descarta_em_silencio(tmp_path):
    p = _pool(tmp_path, [
        {"product_id": "MLB1", "title": "ok", "price_ref_cents": 5000, "price_historic_min_cents": 4000},
        {"product_id": "MLB2", "title": "sem ref", "price_historic_min_cents": 4000},
        {"product_id": "MLB3", "title": "ref str", "price_ref_cents": "R$ 50", "price_historic_min_cents": 4000},
    ])
    src = MeliSource("c", "s", token_path=tmp_path / "t", links_path=tmp_path / "l",
                     client=httpx.Client(transport=httpx.MockTransport(_sem_rede)))
    offers = src.fetch_offers({"meli": {"offers_path": str(p), "commission_pct": 4.0}})
    assert len(offers) == 1
    assert src.pool_warning is None                             # 2 de 3 sumiram sem aviso


# ---------------------------------------------------------------------------
# 7. O QUINTO ZERO: filtro zera N ofertas e o ops não recebe NADA
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("nome,fabrica", [
    ("acima_da_ref_1_centavo", lambda i: make_offer(item_id=str(i), price_ref_cents=2000, price_current_cents=2001)),
    ("abaixo_do_price_min", lambda i: make_offer(item_id=str(i), price_current_cents=1999)),
    ("sem_imagem", lambda i: make_offer(item_id=str(i), image_url="")),
])
def test_7a_filtro_zera_50_ofertas_e_o_resumo_fica_vazio(tmp_path, monkeypatch, nome, fabrica):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    offers = [fabrica(i) for i in range(50)]
    db = StateDB(tmp_path / "s.db")
    ch = FakeChannel()
    s = pipeline.run(CFG, [FakeSource(offers)], [ch], db, validator=no_validate, watchlist=WL_FRESCA)
    db.close()
    assert ch.sent == []
    assert s.published == [] and s.discarded == [] and s.warnings == []
    # cli.main: houve_algo = published or discarded or warnings -> False -> não envia
    houve_algo = s.published or s.discarded or s.warnings
    assert not houve_algo, "ops receberia o resumo"


def test_7b_dedupe_esgota_o_feed_e_silencia_por_30_dias(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    offers = [make_offer(item_id=str(i)) for i in range(5)]
    db = StateDB(tmp_path / "s.db")
    for o in offers:
        db.record_post(Post(offer=o, copy=CopyParts("h", "d", "c"), affiliate_link="x"), "telegram", "1")
    s = pipeline.run(CFG, [FakeSource(offers)], [FakeChannel()], db, validator=no_validate,
                     watchlist=WL_FRESCA)
    db.close()
    assert s.published == [] and s.discarded == [] and s.warnings == []


def test_7c_watchlist_vencida_gera_aviso_em_TODO_run_288_msgs_por_dia(tmp_path, monkeypatch):
    """O oposto do silêncio: a partir do dia em que a watchlist vence
    (2026-09-07 com o arquivo atual), CADA run de 5 min carrega o aviso ->
    houve_algo=True -> cli manda o resumo ao ops 288x/dia, mesmo sem nada."""
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    vencida = Watchlist(generated_at=date.today() - timedelta(days=15), valid_days=14)
    db = StateDB(tmp_path / "s.db")
    s = pipeline.run(CFG, [FakeSource([])], [FakeChannel()], db, validator=no_validate,
                     watchlist=vencida)
    db.close()
    assert s.published == [] and s.discarded == []
    assert any("Watchlist vencida" in w for w in s.warnings)
    assert bool(s.published or s.discarded or s.warnings)      # cli: envia


def test_2g_copy_ignora_min_real_discount_pct(monkeypatch):
    """A régua (min 10) vale para price_line e para a arte, mas NÃO para a copy:
    o prompt diz 'Desconto verificado: 4%' sem a proibição de palavras de
    desconto (só entra quando é 0), e o fallback escreve '4% OFF' na headline."""
    from afiliado import copywriter
    offer = make_offer(price_ref_cents=2600, price_current_cents=2500)   # 4%
    prompt = copywriter._copy_prompt(offer, {"copy": {"tone": "x"}})
    assert "Desconto verificado: 4%" in prompt
    assert copywriter.SEM_DESCONTO_VERIFICADO not in prompt
    assert copywriter.fallback_copy(offer).headline == "🔥 Oferta: 4% OFF"
    assert pricing.price_line(offer, 10)[0] == "R$ 25,00"                 # modo B


# ---------------------------------------------------------------------------
# 8. Selo: texto mente 5%, arte discorda do texto, IG nunca tem selo
# ---------------------------------------------------------------------------

def test_8a_texto_da_selo_5pct_acima_da_minima_registrada_e_arte_nao(tmp_path):
    offer = make_offer(price_floor_cents=24000, price_current_cents=24999)
    texto = message.build_message(offer, CopyParts("h", "d", "c"), "x", price_floor=None)
    assert "🏷️ Menor preço já registrado (verificado)" in texto      # registrado: 24000 < 24999
    assert creative._selo_applicable(offer, None) is False           # story/feed: sem selo
    ig = InstagramFeedChannel("u", "t", "b", "o", client=httpx.Client(transport=httpx.MockTransport(_sem_rede)))
    caption = ig._build_caption(Post(offer=offer, copy=CopyParts("h", "d", "c"), affiliate_link="x"))
    assert "Menor preço" not in caption                              # legenda IG: nunca tem selo


def test_8b_ML_real_De_de_um_dia_mais_selo_de_minima_que_nao_e_minima(tmp_path, pool_no_prazo):
    cfg = _cfg_real()
    offers ={o.item_id: o for o in _meli_real(tmp_path).fetch_offers(cfg)}
    o = offers["MLB66637233"]                     # ref 7890 (foto de um dia), hist min 3051
    live = dataclasses.replace(o, price_current_cents=3200)       # 4,9% acima da mínima
    validate.check_price(live, cfg)
    texto = message.build_message(live, CopyParts("h", "d", "c"), "x", price_floor=None,
                                  min_real_discount_pct=10, seal_tolerance=1.05)
    assert "De: <s>R$ 78,90</s> | Por: <b>R$ 32,00</b> (59% OFF)" in texto
    assert "🏷️ Menor preço já registrado (verificado)" in texto
    assert live.price_current_cents > live.price_floor_cents
    assert creative._selo_applicable(live, None) is False           # arte do ML: nunca


def test_8c_watchlist_floor_envelhece_e_o_texto_promete_janela_que_nao_cobre():
    wl = Watchlist(generated_at=date.today() - timedelta(days=13), valid_days=14,
                   price_floors={"123456": PriceFloor(24999, 191)})
    assert not wl.is_stale()
    offer = make_offer(price_current_cents=24999)
    texto = message.build_message(offer, CopyParts("h", "d", "c"), "x",
                                  price_floor=wl.price_floor("123456"))
    assert "Menor preço dos últimos 6 meses (verificado)" in texto
    assert wl.days_old() == 13                   # os últimos 13 dias não foram medidos


# ---------------------------------------------------------------------------
# 9. Janela e fuso
# ---------------------------------------------------------------------------

def test_9a_janela_de_90_dias_tem_91_dias(tmp_path):
    db = StateDB(tmp_path / "s.db")
    h = hoje_utc()
    db.record_price("shopee", "x", 1000, day=(h - timedelta(days=90)).isoformat())
    db.record_price("shopee", "x", 1000, day=(h - timedelta(days=91)).isoformat())
    assert db.price_history("shopee", "x", 90) == [1000]        # dia -90 conta
    db.prune_price_log(90)
    assert db.price_history("shopee", "x", 3650) == [1000]      # e sobrevive à poda
    db.close()


def test_9b_watchlist_e_pool_usam_date_today_local_e_price_log_usa_utc():
    import inspect
    from afiliado import state, watchlist as wl_mod
    assert "date.today()" in inspect.getsource(wl_mod.Watchlist.days_old)
    assert "date.today()" in inspect.getsource(meli_mod.MeliSource.fetch_offers)
    assert "timezone.utc" in inspect.getsource(state._now)

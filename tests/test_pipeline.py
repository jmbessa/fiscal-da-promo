from datetime import date, datetime, timedelta, timezone

import pytest

from afiliado import llm, pipeline, pricing, state, validate
from afiliado.channels.base import PublishResult
from afiliado.errors import SourceError, ValidationError
from afiliado.models import CopyParts, Post
from afiliado.state import StateDB
from afiliado.watchlist import PriceFloor, PriceRef, Watchlist
from tests.test_models import make_offer, make_offer_ref

BRT = timezone(timedelta(hours=-3))


def _congela(monkeypatch, hh: int, mm: int, dia: int = 26) -> None:
    """Fixa o relógio do pipeline/StateDB num horário BRT de 2026-08-<dia>."""
    instante = datetime(2026, 8, dia, hh, mm, tzinfo=BRT).astimezone(timezone.utc)
    monkeypatch.setattr(state, "_now", lambda: instante)


def _ja_postados(db: StateDB, canal: str, n: int) -> None:
    for k in range(n):
        db.record_post(Post(offer=make_offer(item_id=f"{canal}-{k}"),
                            copy=CopyParts("h", "d", "c"), affiliate_link="l"), canal, "x")

CFG = {
    "selection": {"posts_per_run": 2, "price_min_brl": 20,
                  "price_max_brl": 1000, "dedupe_days": 30, "category_ids": [],
                  "max_above_ref": 1.00, "require_price_ref": False,
                  "min_real_discount_pct": 10, "ref_window_days": 90,
                  "ref_min_observations": 5,
                  "ev_weights": {"popularity": 0.3, "discount": 0.5}},
    "llm": {"model": "haiku"},
    "copy": {"tone": "pt-BR"},
    "validation": {"allowed_domains": ["shope.ee"]},
}


class FakeSource:
    name = "shopee"

    def __init__(self, offers):
        self._offers = offers

    def fetch_offers(self, cfg):
        return self._offers

    def resolve_affiliate_link(self, offer):
        return "https://shope.ee/ok"


class FakeChannel:
    name = "telegram"

    def __init__(self):
        self.sent = []

    def publish(self, post):
        self.sent.append(post)
        return PublishResult(True, str(len(self.sent)))


class NamedFakeChannel:
    def __init__(self, name, max_per_run=None, always_fail=False):
        self.name = name
        self.sent = []
        self.max_per_run = max_per_run
        self.always_fail = always_fail

    def publish(self, post):
        self.sent.append(post)
        if self.always_fail:
            return PublishResult(False, error=f"{self.name} sempre falha")
        return PublishResult(True, str(len(self.sent)))


def no_network_validator(post, cfg, client=None):
    return None


def test_run_publishes_and_records(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)  # força fallbacks
    db = StateDB(tmp_path / "s.db")
    offers = [make_offer(item_id=str(i)) for i in range(3)]
    ch = FakeChannel()
    summary = pipeline.run(CFG, [FakeSource(offers)], [ch], db,
                           validator=no_network_validator)
    assert len(ch.sent) == 2                       # posts_per_run
    assert len(summary.published) == 2
    assert db.was_posted_recently("shopee", ch.sent[0].offer.item_id, 30)
    db.close()


def test_run_discards_and_promotes_next(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    offers = [make_offer(item_id=str(i)) for i in range(3)]

    def validator(post, cfg, client=None):
        if post.offer.item_id == "0":
            raise ValidationError("link morto")

    ch = FakeChannel()
    summary = pipeline.run(CFG, [FakeSource(offers)], [ch], db, validator=validator)
    assert len(ch.sent) == 2
    assert "0" not in [p.offer.item_id for p in ch.sent]
    assert len(summary.discarded) == 1
    db.close()


def test_dry_run_does_not_publish_nor_record(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    ch = FakeChannel()
    pipeline.run(CFG, [FakeSource([make_offer()])], [ch], db, dry_run=True,
                 validator=no_network_validator)
    assert ch.sent == []
    assert not db.was_posted_recently("shopee", "123456", 30)
    assert "DRY-RUN" in capsys.readouterr().out
    db.close()


def test_summary_text():
    s = pipeline.RunSummary(published=["a"], discarded=[("b", "x")])
    assert "Publicados (1)" in s.text() and "Descartados (1)" in s.text()
    assert "• b: x" in s.text()


def test_summary_text_agrupa_descartes_com_o_mesmo_motivo():
    # C5: 60 linhas de descarte estouravam os 4096 chars do Telegram e o
    # resumo era descartado em silêncio — justamente no run de falha em massa.
    descartes = [(f"Produto {i}", f"preço R$ {30 + i},00 acima da referência R$ 26,00")
                 for i in range(31)]
    descartes += [("Kit A", "publicação falhou em telegram: Bad Request: chat not found"),
                  ("Kit B", "publicação falhou em telegram: Bad Request: chat not found"),
                  ("Kit C", "publicação falhou em telegram: Bad Request: chat not found")]
    text = pipeline.RunSummary(discarded=descartes).text()
    assert "Descartados (34)" in text
    assert "• 31× preço acima da referência (ex.: Produto 0)" in text
    assert text.count("acima da referência") == 1
    assert text.count("chat not found") == 3          # até 3 iguais: listados um a um
    assert len(text) < 1000


def test_summary_text_agrupa_a_partir_de_quatro():
    descartes = [("Item %d" % i, "sem link de afiliado no pool para MLB%d" % i) for i in range(4)]
    text = pipeline.RunSummary(discarded=descartes).text()
    assert "• 4× sem link de afiliado no pool para MLB (ex.: Item 0)" in text
    assert "MLB0" not in text


def test_summary_text_agrupa_sem_buy_box():
    # Rodada de correção da 5B (Fix 1): o "sem buy box" de refresh_price varia
    # só nos ids e na contagem de vendedores — continua agrupado no resumo.
    descartes = [(f"Produto {i}", f"meli: sem buy box — anúncio MLB712544938{i} não está "
                                  f"entre os {30 + i} vendedores de MLB6663723{i}")
                 for i in range(4)]
    text = pipeline.RunSummary(discarded=descartes).text()
    assert ("• 4× meli: sem buy box — anúncio MLB não está entre os vendedores de MLB "
            "(ex.: Produto 0)") in text
    assert "MLB7125449380" not in text


def test_descartes_guardam_rotulo_e_motivo_separados(tmp_path, monkeypatch):
    # M0-3 (revisão da 5A): `_motivo` dividia a string no PRIMEIRO ": " — um
    # título "Kit: 3 peças" virava motivo "peças: …" e o agrupamento errava.
    # O descarte é guardado como (rótulo, motivo).
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    offers = [make_offer(item_id=str(i), title=f"Kit: {i} peças") for i in range(4)]

    def validator(post, cfg, client=None):
        raise ValidationError("link em domínio inesperado: evil.com")

    summary = pipeline.run(CFG, [FakeSource(offers)], [FakeChannel()], db, validator=validator)
    assert summary.discarded[0] == ("Kit: 0 peças", "link em domínio inesperado: evil.com")
    assert "• 4× link em domínio inesperado: evil.com (ex.: Kit: 0 peças)" in summary.text()
    db.close()


def test_summary_text_includes_warnings():
    s = pipeline.RunSummary(published=["a"], discarded=[], warnings=["⚠️ aviso 1", "⚠️ aviso 2"])
    text = s.text()
    assert "⚠️ aviso 1" in text
    assert "⚠️ aviso 2" in text


def test_run_warns_without_watchlist(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    offers = [make_offer(item_id=str(i)) for i in range(3)]
    ch = FakeChannel()
    summary = pipeline.run(CFG, [FakeSource(offers)], [ch], db,
                           validator=no_network_validator)
    assert "Sem watchlist" in summary.text()
    db.close()


def test_run_stale_watchlist_no_boost_and_warns(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    offers = [
        make_offer(item_id="0", commission_pct=20.0),
        make_offer(item_id="1", commission_pct=12.0),
        make_offer(item_id="2", commission_pct=5.0),
    ]

    db1 = StateDB(tmp_path / "s1.db")
    ch1 = FakeChannel()
    summary_no_wl = pipeline.run(CFG, [FakeSource(offers)], [ch1], db1,
                                 validator=no_network_validator)
    db1.close()

    stale_wl = Watchlist(generated_at=date.today() - timedelta(days=30), valid_days=14,
                         hot_items={"2": 5.0})
    db2 = StateDB(tmp_path / "s2.db")
    ch2 = FakeChannel()
    summary_stale = pipeline.run(CFG, [FakeSource(offers)], [ch2], db2,
                                 validator=no_network_validator, watchlist=stale_wl)
    db2.close()

    assert "Watchlist vencida" in summary_stale.text()
    assert [p.offer.item_id for p in ch1.sent] == [p.offer.item_id for p in ch2.sent]


def test_run_hot_item_jumps_queue(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    offers = [make_offer(item_id=str(i)) for i in range(3)]  # EV igual entre si
    wl = Watchlist(generated_at=date.today(), valid_days=14, hot_items={"2": 2.0})
    ch = FakeChannel()
    pipeline.run(CFG, [FakeSource(offers)], [ch], db,
                validator=no_network_validator, watchlist=wl)
    assert ch.sent[0].offer.item_id == "2"
    db.close()


def test_run_counts_per_offer_with_two_channels(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    offers = [make_offer(item_id=str(i)) for i in range(3)]
    ch_a = NamedFakeChannel("A")
    ch_b = NamedFakeChannel("B")
    summary = pipeline.run(CFG, [FakeSource(offers)], [ch_a, ch_b], db,
                           validator=no_network_validator)
    assert len(summary.published) == 2   # posts_per_run=2, contagem por oferta
    assert len(ch_a.sent) == 2
    assert len(ch_b.sent) == 2
    db.close()


def test_run_respects_channel_max_per_run(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    offers = [make_offer(item_id=str(i)) for i in range(3)]
    ch_a = NamedFakeChannel("A")
    ch_b = NamedFakeChannel("B", max_per_run=1)
    summary = pipeline.run(CFG, [FakeSource(offers)], [ch_a, ch_b], db,
                           validator=no_network_validator)
    assert len(summary.published) == 2
    assert len(ch_a.sent) == 2      # sem limite: recebe todas as ofertas publicadas
    assert len(ch_b.sent) == 1      # max_per_run=1: só a primeira
    db.close()


def test_run_respects_channel_max_per_day(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    _congela(monkeypatch, 23, 55)   # fim da janela: orçamento de ritmo = teto
    db = StateDB(tmp_path / "s.db")
    offers = [make_offer(item_id=str(i)) for i in range(3)]
    ch_s = NamedFakeChannel("s")
    ch_s.max_per_day = 2
    ch_t = NamedFakeChannel("t")

    ja_postado = Post(offer=make_offer(item_id="ja-postado"),
                      copy=CopyParts(headline="h", description="d", cta="c"),
                      affiliate_link="https://shope.ee/x", message_text="msg")
    db.record_post(ja_postado, channel="s", message_id="x")

    cfg = {**CFG, "selection": {**CFG["selection"], "posts_per_run": 3}}
    summary = pipeline.run(cfg, [FakeSource(offers)], [ch_s, ch_t], db,
                           validator=no_network_validator)
    assert len(ch_s.sent) == 1       # teto diário 2: 1 já feito hoje + 1 neste run
    assert len(ch_t.sent) == 3       # sem teto: recebe todas
    assert len(summary.published) == 3   # ofertas seguem contando via "t"
    assert any("teto diário" in w for w in summary.warnings)
    db.close()


def test_run_refresh_price_failure_discards_and_promotes_next(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    offers = [make_offer(item_id=str(i)) for i in range(3)]

    class FlakySource(FakeSource):
        def refresh_price(self, offer):
            if offer.item_id == "0":
                raise SourceError("preço acima da mínima histórica")
            return offer

    ch = FakeChannel()
    summary = pipeline.run(CFG, [FlakySource(offers)], [ch], db, validator=no_network_validator)
    assert len(ch.sent) == 2               # posts_per_run=2, oferta "0" descartada
    assert "0" not in [p.offer.item_id for p in ch.sent]
    assert len(summary.discarded) == 1
    assert "mínima histórica" in summary.discarded[0][1]
    db.close()


def test_run_source_without_refresh_price_still_works(tmp_path, monkeypatch):
    # FakeSource (usado no resto deste arquivo) não implementa refresh_price
    # -- getattr(src, "refresh_price", None) deve simplesmente pular a
    # chamada, sem quebrar fontes que não o implementam.
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    assert getattr(FakeSource(offers=[]), "refresh_price", None) is None
    db = StateDB(tmp_path / "s.db")
    offers = [make_offer(item_id=str(i)) for i in range(2)]
    ch = FakeChannel()
    summary = pipeline.run(CFG, [FakeSource(offers)], [ch], db, validator=no_network_validator)
    assert len(summary.published) == 2
    db.close()


def test_run_warns_when_meli_source_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")

    class EmptyMeliSource:
        name = "meli"

        def fetch_offers(self, cfg):
            return []

        def resolve_affiliate_link(self, offer):
            return "x"

    ch = FakeChannel()
    summary = pipeline.run(CFG, [EmptyMeliSource()], [ch], db, validator=no_network_validator)
    assert any("meli" in w and "pool" in w for w in summary.warnings)
    db.close()


def test_run_no_meli_warning_when_meli_source_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    ch = FakeChannel()
    summary = pipeline.run(CFG, [FakeSource([])], [ch], db, validator=no_network_validator)
    assert not any("meli" in w for w in summary.warnings)
    db.close()


def test_run_no_meli_warning_when_meli_source_has_offers(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")

    class NonEmptyMeliSource:
        name = "meli"

        def fetch_offers(self, cfg):
            return [make_offer(item_id="m1", source="meli")]

        def resolve_affiliate_link(self, offer):
            return "https://mercadolivre.com/sec/x"

    ch = FakeChannel()
    summary = pipeline.run(CFG, [NonEmptyMeliSource()], [ch], db, validator=no_network_validator)
    assert not any("meli" in w for w in summary.warnings)
    db.close()


def test_run_offer_counts_when_one_channel_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    offers = [make_offer(item_id=str(i)) for i in range(3)]
    ch_a = NamedFakeChannel("A")
    ch_b = NamedFakeChannel("B", always_fail=True)
    summary = pipeline.run(CFG, [FakeSource(offers)], [ch_a, ch_b], db,
                           validator=no_network_validator)
    assert len(summary.published) == 2      # A publicou; oferta conta como publicada
    assert len(ch_a.sent) == 2
    assert len(ch_b.sent) == 2
    assert len(summary.discarded) == 2      # uma linha de falha de B por oferta
    assert all("B" in motivo for _, motivo in summary.discarded)
    db.close()


# --- Fase 4: régua honesta no pipeline ---------------------------------------

class RefreshingSource(FakeSource):
    """Fonte com refresh_price, para provar que o rótulo é recalculado DEPOIS
    do preço ao vivo (antes ele mostrava o desconto do preço velho)."""

    def __init__(self, offers, novo_preco_cents):
        super().__init__(offers)
        self._novo = novo_preco_cents

    def refresh_price(self, offer):
        import dataclasses
        return dataclasses.replace(offer, price_current_cents=self._novo)


def test_run_rotulo_usa_o_desconto_depois_do_refresh(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    # Referência 100,00; na busca custava 99,00 (1%), no refresh cai para 50,00.
    offer = make_offer_ref(10000, item_id="x", price_current_cents=9900)
    ch = FakeChannel()
    summary = pipeline.run(CFG, [RefreshingSource([offer], 5000)], [ch], db,
                           validator=no_network_validator)
    assert summary.published == ["Tênis Nike SB (50% OFF)"]
    assert ch.sent[0].verdict.mode == "A" and ch.sent[0].verdict.discount_pct == 50
    assert "(50% OFF)" in ch.sent[0].message_text
    db.close()


def test_run_rotulo_e_texto_seguem_o_veredito_nao_o_desconto_cru(tmp_path, monkeypatch):
    # 27% verificável mas janela de 13 dias: veredito B -> rótulo sem OFF,
    # texto sem De/Por, copy de fallback neutra. Nada no run recalcula.
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    offer = make_offer(item_id="x", price_ref_cents=26000, price_p25_cents=26000,
                       price_window_days=13, price_current_cents=18900)
    assert offer.real_discount_pct == 27
    ch = FakeChannel()
    summary = pipeline.run(CFG, [FakeSource([offer])], [ch], db, validator=no_network_validator)
    assert summary.published == ["Tênis Nike SB"]
    post = ch.sent[0]
    assert post.verdict == pricing.NO_CLAIM
    assert "OFF" not in post.message_text and "<s>" not in post.message_text
    assert post.copy.headline == "🔥 Achado do dia"
    db.close()


def test_run_rotulo_sem_desconto_verificado_e_so_o_titulo(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    ch = FakeChannel()
    summary = pipeline.run(CFG, [FakeSource([make_offer(item_id="x")])], [ch], db,
                           validator=no_network_validator)
    assert summary.published == ["Tênis Nike SB"]
    db.close()


def test_run_grava_o_historico_de_precos(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    offers = [make_offer(item_id="a", price_current_cents=2600),
              make_offer(item_id="b", price_current_cents=3390)]
    pipeline.run(CFG, [FakeSource(offers)], [FakeChannel()], db,
                 validator=no_network_validator)
    assert db.price_history("shopee", "a", days=1) == [2600]
    assert db.price_history("shopee", "b", days=1) == [3390]
    db.close()


def test_run_usa_o_historico_como_referencia(tmp_path, monkeypatch):
    # Com histórico suficiente, a mediana vira referência e a oferta que hoje
    # está MAIS CARA que o típico não é publicada.
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    hoje = date.today()
    for i in range(1, 7):
        db.record_price("shopee", "caro", 2600, day=(hoje - timedelta(days=i)).isoformat())
    offers = [make_offer(item_id="caro", price_current_cents=3390),
              make_offer(item_id="normal", price_current_cents=2500)]
    ch = FakeChannel()
    summary = pipeline.run(CFG, [FakeSource(offers)], [ch], db,
                           validator=no_network_validator)
    assert [p.offer.item_id for p in ch.sent] == ["normal"]
    assert len(summary.published) == 1
    db.close()


# --- Fase 5A: o sistema não pode se matar --------------------------------------

class ContadorSource(FakeSource):
    """Conta o que cada oferta 'paga' antes de existir canal para ela."""

    def __init__(self, offers):
        super().__init__(offers)
        self.links = 0
        self.refreshes = 0

    def resolve_affiliate_link(self, offer):
        self.links += 1
        return "https://shope.ee/ok"

    def refresh_price(self, offer):
        self.refreshes += 1
        return offer


def _canal(name, cap):
    ch = NamedFakeChannel(name)
    ch.max_per_day = cap
    return ch


def test_run_todos_no_teto_nao_varre_a_fila(tmp_path, monkeypatch):
    # C2: com os 3 canais no teto, cada run varria a fila inteira pagando
    # refresh_price + generateShortLink + copy (LLM) + validação por oferta —
    # 195 chamadas LLM e 97 links por run, 0 publicados, até o SIGTERM.
    _congela(monkeypatch, 23, 55)
    chamadas_llm = []
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: chamadas_llm.append(1) or None)
    db = StateDB(tmp_path / "s.db")
    src = ContadorSource([make_offer(item_id=str(i)) for i in range(60)])
    canais = [_canal("telegram", 60), _canal("story_dispatch", 6), _canal("instagram_feed", 2)]
    for ch in canais:
        _ja_postados(db, ch.name, ch.max_per_day)

    summary = pipeline.run(CFG, [src], canais, db, validator=no_network_validator)

    assert chamadas_llm == []
    assert src.links == 0 and src.refreshes == 0
    assert all(ch.sent == [] for ch in canais)
    assert summary.published == [] and summary.discarded == []
    assert [w for w in summary.warnings if "teto" in w] == [
        "ℹ️ teto diário atingido em todos os canais"]
    db.close()


def test_run_fechado_so_pelo_ritmo_encerra_em_silencio(tmp_path, monkeypatch):
    # M0-1 (revisão da 5A): às 08:05 o orçamento do ritmo é 1 e já foi usado —
    # o canal está fechado SÓ pelo ritmo, mas o run avisava "teto diário
    # atingido em todos os canais": uma linha falsa no ops todo dia.
    _congela(monkeypatch, 8, 5)
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    ch = _canal("telegram", 60)
    _ja_postados(db, "telegram", 1)
    src = ContadorSource([make_offer(item_id=str(i)) for i in range(3)])
    summary = pipeline.run(CFG, [src], [ch], db, validator=no_network_validator)
    assert ch.sent == [] and src.links == 0          # continua sem varrer a fila
    assert not any("teto" in w for w in summary.warnings)
    db.close()


def test_run_teto_de_verdade_avisa_mesmo_com_outro_canal_so_no_ritmo(tmp_path, monkeypatch):
    _congela(monkeypatch, 8, 5)
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    no_ritmo, no_teto = _canal("telegram", 60), _canal("instagram_feed", 2)
    _ja_postados(db, "telegram", 1)
    _ja_postados(db, "instagram_feed", 2)
    summary = pipeline.run(CFG, [FakeSource([make_offer()])], [no_ritmo, no_teto], db,
                           validator=no_network_validator)
    assert [w for w in summary.warnings if "teto" in w] == [
        "ℹ️ teto diário atingido em instagram_feed"]
    db.close()


def test_run_sem_canais_nao_gasta_a_fila(tmp_path, monkeypatch):
    # Caso degenerado do mesmo defeito: nenhum canal construído (todas as envs
    # ausentes) e o laço pagava LLM/link para cada oferta sem ter onde publicar.
    _congela(monkeypatch, 12, 0)
    chamadas_llm = []
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: chamadas_llm.append(1) or None)
    db = StateDB(tmp_path / "s.db")
    src = ContadorSource([make_offer(item_id=str(i)) for i in range(5)])
    summary = pipeline.run(CFG, [src], [], db, validator=no_network_validator)
    assert chamadas_llm == [] and src.links == 0
    assert any("nenhum canal" in w for w in summary.warnings)
    db.close()


def test_run_canal_que_so_falha_fecha_apos_tres_falhas_seguidas(tmp_path, monkeypatch):
    # Variante "canal falhando" do C2 (bot removido, chat id errado): o canal
    # está "aberto" mas toda publicação falha — e cada oferta da fila pagava
    # LLM + link. Três falhas seguidas fecham o canal neste run.
    _congela(monkeypatch, 23, 55)
    chamadas_llm = []
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: chamadas_llm.append(1) or None)
    db = StateDB(tmp_path / "s.db")
    src = ContadorSource([make_offer(item_id=str(i)) for i in range(20)])
    ch = NamedFakeChannel("telegram", always_fail=True)
    cfg = {**CFG, "selection": {**CFG["selection"], "posts_per_run": 10}}
    summary = pipeline.run(cfg, [src], [ch], db, validator=no_network_validator)
    assert len(ch.sent) == 3
    assert src.links == 3
    assert len(chamadas_llm) == 1 + 3 * 2          # ranking + 2 tentativas de copy × 3
    assert len(summary.discarded) == 3
    assert any("3 falhas seguidas" in w for w in summary.warnings)
    db.close()


def test_run_falha_isolada_nao_fecha_o_canal(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")

    class FalhaNaSegunda(NamedFakeChannel):
        def publish(self, post):
            self.sent.append(post)
            if len(self.sent) == 2:
                return PublishResult(False, error="oscilou")
            return PublishResult(True, str(len(self.sent)))

    ch = FalhaNaSegunda("telegram")
    cfg = {**CFG, "selection": {**CFG["selection"], "posts_per_run": 3}}
    summary = pipeline.run(cfg, [FakeSource([make_offer(item_id=str(i)) for i in range(4)])],
                           [ch], db, validator=no_network_validator)
    assert len(summary.published) == 3 and len(ch.sent) == 4
    assert not any("falhas seguidas" in w for w in summary.warnings)
    db.close()


def test_pacing_budget_exemplos_do_brief():
    def em(hh, mm):
        return datetime(2026, 8, 26, hh, mm, tzinfo=BRT)
    # Telegram 60/dia, janela 08:00–23:55 (955 min)
    assert pipeline.pacing_budget(60, em(8, 0)) == 1
    assert pipeline.pacing_budget(60, em(12, 0)) == 16     # ≈25% da janela
    assert pipeline.pacing_budget(60, em(23, 55)) == 60
    assert pipeline.pacing_budget(60, em(7, 59)) == 0      # fora da janela
    assert pipeline.pacing_budget(60, em(23, 56)) == 0
    assert pipeline.pacing_budget(60, em(3, 0)) == 0       # run de madrugada
    # Story 6/dia: um a cada ~2h40
    assert pipeline.pacing_budget(6, em(8, 0)) == 1
    assert pipeline.pacing_budget(6, em(10, 39)) == 1
    assert pipeline.pacing_budget(6, em(10, 40)) == 2
    assert pipeline.pacing_budget(6, em(23, 55)) == 6
    # Feed 2/dia: 1 às 08:00, o 2º só a partir da metade da janela
    assert pipeline.pacing_budget(2, em(8, 0)) == 1
    assert pipeline.pacing_budget(2, em(15, 56)) == 1
    assert pipeline.pacing_budget(2, em(15, 58)) == 2
    # Janela configurável
    assert pipeline.pacing_budget(10, em(9, 0), "09:00", "10:00") == 1
    assert pipeline.pacing_budget(10, em(9, 30), "09:00", "10:00") == 6
    assert pipeline.pacing_budget(10, em(8, 59), "09:00", "10:00") == 0


def test_run_fora_da_janela_publica_zero(tmp_path, monkeypatch):
    # Persistent=true disparava um run às 03:00 ao religar a VPS — e ele
    # publicava. Fora da janela o orçamento é 0 e o LLM nem é chamado.
    _congela(monkeypatch, 3, 0)
    chamadas_llm = []
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: chamadas_llm.append(1) or None)
    db = StateDB(tmp_path / "s.db")
    ch = _canal("telegram", 60)
    summary = pipeline.run(CFG, [FakeSource([make_offer(item_id=str(i)) for i in range(3)])],
                           [ch], db, validator=no_network_validator)
    assert ch.sent == [] and summary.published == []
    assert chamadas_llm == []
    db.close()


def test_run_ritmo_as_8h_publica_um_so(tmp_path, monkeypatch):
    # 60/dia às 08:00 → orçamento 1, mesmo com posts_per_run 3 e 3 candidatas;
    # depois da publicação nenhum canal segue aberto e o laço para.
    _congela(monkeypatch, 8, 0)
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    ch = _canal("telegram", 60)
    cfg = {**CFG, "selection": {**CFG["selection"], "posts_per_run": 3}}
    src = ContadorSource([make_offer(item_id=str(i)) for i in range(3)])
    summary = pipeline.run(cfg, [src], [ch], db, validator=no_network_validator)
    assert len(ch.sent) == 1 and len(summary.published) == 1
    assert src.links == 1            # a 2ª oferta não pagou link/copy
    assert not any("teto" in w for w in summary.warnings)   # ritmo não é teto
    db.close()


def test_run_ritmo_ao_meio_dia_respeita_o_orcamento(tmp_path, monkeypatch):
    _congela(monkeypatch, 12, 0)     # orçamento 16 para 60/dia
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    ch = _canal("telegram", 60)
    _ja_postados(db, "telegram", 16)
    summary = pipeline.run(CFG, [FakeSource([make_offer(item_id="x")])], [ch], db,
                           validator=no_network_validator)
    assert ch.sent == [] and summary.published == []

    db2 = StateDB(tmp_path / "s2.db")
    _ja_postados(db2, "telegram", 15)
    ch2 = _canal("telegram", 60)
    summary2 = pipeline.run(CFG, [FakeSource([make_offer(item_id="x")])], [ch2], db2,
                            validator=no_network_validator)
    assert len(ch2.sent) == 1 and len(summary2.published) == 1
    db.close()
    db2.close()


def test_run_canal_sem_max_per_day_nao_tem_ritmo(tmp_path, monkeypatch):
    _congela(monkeypatch, 3, 0)      # fora da janela
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    ch = FakeChannel()               # sem max_per_day
    summary = pipeline.run(CFG, [FakeSource([make_offer(item_id=str(i)) for i in range(3)])],
                           [ch], db, validator=no_network_validator)
    assert len(ch.sent) == 2 and len(summary.published) == 2
    db.close()


def test_run_post_as_22h_brt_conta_no_dia_brt(tmp_path, monkeypatch):
    # Teste obrigatório 3: publicado às 22:00 BRT; às 23:00 BRT ainda conta
    # (o canal com teto 1 fica fechado); às 08:00 BRT do dia seguinte não.
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    _congela(monkeypatch, 22, 0, dia=25)
    ch = _canal("telegram", 1)
    pipeline.run(CFG, [FakeSource([make_offer(item_id="a")])], [ch], db,
                 validator=no_network_validator)
    assert len(ch.sent) == 1
    _congela(monkeypatch, 23, 0, dia=25)
    pipeline.run(CFG, [FakeSource([make_offer(item_id="b")])], [ch], db,
                 validator=no_network_validator)
    assert len(ch.sent) == 1                       # mesmo dia BRT: teto 1 atingido
    _congela(monkeypatch, 8, 0, dia=26)
    pipeline.run(CFG, [FakeSource([make_offer(item_id="c")])], [ch], db,
                 validator=no_network_validator)
    assert len(ch.sent) == 2                       # dia BRT seguinte: reabre
    db.close()


def test_run_aviso_persistente_uma_vez_por_dia(tmp_path, monkeypatch):
    # A3: watchlist vencida gerava a mesma linha em todos os 192 runs do dia.
    _congela(monkeypatch, 12, 0)
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    vencida = Watchlist(generated_at=date.today() - timedelta(days=30), valid_days=14)
    avisos = []
    for _ in range(3):
        s = pipeline.run(CFG, [FakeSource([])], [FakeChannel()], db,
                         validator=no_network_validator, watchlist=vencida)
        avisos += [w for w in s.warnings if "vencida" in w]
    assert len(avisos) == 1
    db.close()


def test_run_aviso_volta_no_dia_local_seguinte(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    vencida = Watchlist(generated_at=date.today() - timedelta(days=30), valid_days=14)
    _congela(monkeypatch, 23, 50, dia=25)
    s1 = pipeline.run(CFG, [FakeSource([])], [FakeChannel()], db,
                      validator=no_network_validator, watchlist=vencida)
    _congela(monkeypatch, 8, 0, dia=26)
    s2 = pipeline.run(CFG, [FakeSource([])], [FakeChannel()], db,
                      validator=no_network_validator, watchlist=vencida)
    assert any("vencida" in w for w in s1.warnings)
    assert any("vencida" in w for w in s2.warnings)
    db.close()


def test_dry_run_nao_escreve_no_banco_nem_baixa_imagem(tmp_path, monkeypatch):
    # A10: --dry-run gravava price_log (e, com C6, clicava no link). Agora:
    # nenhuma tabela muda e a imagem não é baixada — validador PADRÃO.
    _congela(monkeypatch, 12, 0)
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)

    def imagem_proibida(*a, **k):
        raise AssertionError("dry-run baixou a imagem")

    monkeypatch.setattr(validate, "check_image", imagem_proibida)
    db = StateDB(tmp_path / "s.db")
    # `discovery_cursor` entrou na lista depois do primeiro dry-run REAL: ele
    # era a única tabela que a simulação ainda mexia, empurrando a rotação da
    # descoberta e fazendo a produção pular uma fatia do ciclo.
    tabelas = ("posted", "runs", "price_log", "warned", "discovery_cursor")

    def contagens():
        return {t: db.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tabelas}

    antes = contagens()
    summary = pipeline.run(CFG, [FakeSource([make_offer(item_id=str(i)) for i in range(3)])],
                           [], db, dry_run=True)
    assert len(summary.published) == 2
    assert summary.discarded == []
    assert contagens() == antes
    db.close()


# --- Fase 5A (M12): heartbeat -------------------------------------------------

def test_run_heartbeat_so_no_primeiro_run_do_dia(tmp_path, monkeypatch):
    # Teste obrigatório 14: uma VPS morta (Oracle recolhe VM ociosa) era
    # indistinguível de "sem oferta boa". O primeiro run do dia local diz
    # bom dia com a contagem de ontem; os 191 seguintes não repetem.
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    _congela(monkeypatch, 22, 0, dia=25)          # ontem, dia BRT 25/08
    _ja_postados(db, "telegram", 2)
    db.record_run(published=2, discarded=3)
    db.record_run(published=0, discarded=0)
    _congela(monkeypatch, 8, 0, dia=26)
    s1 = pipeline.run(CFG, [FakeSource([])], [FakeChannel()], db, validator=no_network_validator)
    s2 = pipeline.run(CFG, [FakeSource([])], [FakeChannel()], db, validator=no_network_validator)
    assert s1.warnings[0] == "☀️ Bom dia — ontem: 2 publicados, 3 descartados em 2 runs"
    assert not any("Bom dia" in w for w in s2.warnings)
    _congela(monkeypatch, 8, 0, dia=27)
    s3 = pipeline.run(CFG, [FakeSource([])], [FakeChannel()], db, validator=no_network_validator)
    assert any(w.startswith("☀️ Bom dia — ontem: 0 publicados, 0 descartados em 2 runs")
               for w in s3.warnings)
    db.close()


def test_run_heartbeat_aparece_mesmo_com_todos_no_teto(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    _congela(monkeypatch, 23, 55)
    db = StateDB(tmp_path / "s.db")
    ch = _canal("telegram", 1)
    _ja_postados(db, "telegram", 1)
    summary = pipeline.run(CFG, [FakeSource([make_offer()])], [ch], db,
                           validator=no_network_validator)
    assert any("Bom dia" in w for w in summary.warnings)
    assert any("teto diário atingido em todos os canais" in w for w in summary.warnings)
    db.close()


def test_dry_run_nao_emite_heartbeat(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    summary = pipeline.run(CFG, [FakeSource([])], [], db, dry_run=True,
                           validator=no_network_validator)
    assert not any("Bom dia" in w for w in summary.warnings)
    db.close()


# --- Fase 5A (M8): isolamento de fontes ---------------------------------------

class FonteQuebrada:
    def __init__(self, name, exc):
        self.name, self.exc = name, exc

    def fetch_offers(self, cfg):
        raise self.exc

    def resolve_affiliate_link(self, offer):
        return "x"


class FakeMeli(FakeSource):
    name = "meli"

    def resolve_affiliate_link(self, offer):
        return "https://mercadolivre.com/sec/x"


def test_run_fonte_que_falha_vira_aviso_e_as_outras_publicam(tmp_path, monkeypatch):
    # Teste obrigatório 10: Shopee em 5xx abortava o run inteiro, inclusive o ML.
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    ch = FakeChannel()
    summary = pipeline.run(
        CFG, [FonteQuebrada("shopee", SourceError("shopee API: HTTP 503 Service Unavailable")),
              FakeMeli([make_offer(item_id="m", source="meli")])],
        [ch], db, validator=no_network_validator)
    assert len(summary.published) == 1 and len(ch.sent) == 1
    assert any(w.startswith("⚠️ fonte shopee falhou: ") and "503" in w for w in summary.warnings)
    db.close()


def test_run_erro_httpx_na_fonte_tambem_e_isolado(tmp_path, monkeypatch):
    import httpx
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    summary = pipeline.run(
        CFG, [FonteQuebrada("shopee", httpx.ConnectError("down")),
              FakeMeli([make_offer(item_id="m", source="meli")])],
        [FakeChannel()], db, validator=no_network_validator)
    assert len(summary.published) == 1
    assert any("fonte shopee falhou" in w for w in summary.warnings)
    db.close()


def test_run_aborta_so_quando_todas_as_fontes_falham_e_carrega_o_resumo(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    with pytest.raises(pipeline.RunAborted, match="todas as fontes") as info:
        pipeline.run(CFG, [FonteQuebrada("shopee", SourceError("503")),
                           FonteQuebrada("meli", SourceError("token"))],
                     [FakeChannel()], db, validator=no_network_validator)
    avisos = info.value.summary.warnings
    assert any("fonte shopee falhou" in w for w in avisos)
    assert any("fonte meli falhou" in w for w in avisos)
    db.close()


def test_run_abortado_repetido_leva_a_causa_no_proprio_motivo(tmp_path, monkeypatch):
    # M0-4 (revisão da 5A): no 2º run do dia o aviso por fonte já foi
    # deduplicado pelo warn_once e os 191 "Run abortado" seguintes saíam sem
    # a causa. O erro de cada fonte vai no próprio motivo do RunAborted.
    _congela(monkeypatch, 12, 0)
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    fontes = [FonteQuebrada("shopee", SourceError("HTTP 503")),
              FonteQuebrada("meli", SourceError("token"))]
    for _ in range(2):
        with pytest.raises(pipeline.RunAborted) as info:
            pipeline.run(CFG, fontes, [FakeChannel()], db, validator=no_network_validator)
    assert info.value.summary.warnings == []          # 2º run: tudo já avisado hoje
    assert str(info.value) == "todas as fontes falharam — shopee: HTTP 503; meli: token"
    db.close()


def test_run_fonte_vazia_mais_fonte_quebrada_nao_aborta(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    summary = pipeline.run(CFG, [FakeSource([]), FonteQuebrada("meli", SourceError("token"))],
                           [FakeChannel()], db, validator=no_network_validator)
    assert summary.published == []
    assert any("shopee: 0 ofertas buscadas" in w for w in summary.warnings)
    assert any("fonte meli falhou" in w for w in summary.warnings)
    db.close()


def test_summary_text_aceita_cabecalho_proprio():
    s = pipeline.RunSummary(warnings=["⚠️ fonte shopee falhou: 503"])
    text = s.text(header="❌ Run abortado: todas as fontes falharam")
    assert text.startswith("❌ Run abortado: todas as fontes falharam")
    assert "✅" not in text and "fonte shopee falhou" in text


# --- Fase 5A (M3): zero silencioso vira relatório ---------------------------

def test_run_avisa_quando_o_filtro_zera_tudo(tmp_path, monkeypatch):
    # Teste obrigatório 4: 50 buscadas → 0 candidatas → aviso com a contagem
    # por portão (antes: run vazio, indistinguível de "tudo bem").
    _congela(monkeypatch, 12, 0)
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    offers = [make_offer(item_id=str(i), price_current_cents=999) for i in range(50)]
    summary = pipeline.run(CFG, [FakeSource(offers)], [FakeChannel()], db,
                           validator=no_network_validator)
    aviso = next(w for w in summary.warnings if "0 candidatas" in w)
    assert aviso == ("⚠️ 50 ofertas buscadas, 0 candidatas — dedupe: 0 · faixa de preço: 50 · "
                     "acima da referência: 0 · sem dados: 0 · categoria: 0 · EV: 0")
    assert summary.published == [] and summary.discarded == []
    db.close()


def test_run_nao_avisa_zero_candidatas_quando_ha_candidata(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    summary = pipeline.run(CFG, [FakeSource([make_offer()])], [FakeChannel()], db,
                           validator=no_network_validator)
    assert not any("candidatas" in w for w in summary.warnings)
    db.close()


def test_run_avisa_fonte_habilitada_com_zero_ofertas(tmp_path, monkeypatch):
    # C4: o aviso de "0 buscadas" existia só para o meli; a Shopee vazia era
    # um run vazio silencioso.
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    summary = pipeline.run(CFG, [FakeSource([])], [FakeChannel()], db,
                           validator=no_network_validator)
    assert "⚠️ shopee: 0 ofertas buscadas" in summary.warnings
    assert not any("candidatas" in w for w in summary.warnings)   # 0 buscadas ≠ filtro
    db.close()


def test_run_meli_zero_ofertas_traz_o_motivo_do_pool(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")

    class MeliVencido(FakeSource):
        name = "meli"
        pool_warning = "pool vencido: gerado em 2026-07-01, validade 30d"

    summary = pipeline.run(CFG, [MeliVencido([])], [FakeChannel()], db,
                           validator=no_network_validator)
    assert any(w.startswith("⚠️ meli: 0 ofertas buscadas") and "pool vencido" in w
               for w in summary.warnings)
    db.close()


def test_run_warnings_iniciais_entram_no_resumo_uma_vez_por_dia(tmp_path, monkeypatch):
    # Teste obrigatório 5: "canal ligado sem env" era só um print no journal.
    _congela(monkeypatch, 12, 0)
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    aviso = "⚠️ canal instagram_feed ignorado: IG_ACCESS_TOKEN ausente"
    s1 = pipeline.run(CFG, [FakeSource([])], [FakeChannel()], db,
                      validator=no_network_validator, warnings_iniciais=[aviso])
    s2 = pipeline.run(CFG, [FakeSource([])], [FakeChannel()], db,
                      validator=no_network_validator, warnings_iniciais=[aviso])
    assert aviso in s1.warnings
    assert aviso not in s2.warnings
    db.close()


def test_run_avisa_llm_indisponivel(tmp_path, monkeypatch):
    # C4c: LLM fora → 100 posts com a MESMA headline, e o resumo dizia
    # "✅ Publicados (1)" igual a um run saudável.
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)   # ninguém conta
    db = StateDB(tmp_path / "s.db")

    def caiu(*a, **k):
        llm.stats.chamadas += 1
        llm.stats.falhas += 1
        return None

    monkeypatch.setattr(llm, "ask_json", caiu)
    llm.stats.chamadas, llm.stats.falhas = 99, 99      # lixo de outro run: é zerado
    summary = pipeline.run(CFG, [FakeSource([make_offer(item_id=str(i)) for i in range(3)])],
                           [FakeChannel()], db, validator=no_network_validator)
    # 1 ranking + 2 tentativas de copy × 2 ofertas publicadas = 5
    assert "ℹ️ LLM indisponível em 5 de 5 chamadas — ranking/copy de fallback" in summary.warnings
    assert len(summary.published) == 2
    db.close()


def test_run_nao_avisa_llm_quando_nao_houve_falha(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    summary = pipeline.run(CFG, [FakeSource([make_offer()])], [FakeChannel()], db,
                           validator=no_network_validator)
    assert not any("LLM" in w for w in summary.warnings)
    db.close()


def test_run_config_zero_chega_ao_veredito(tmp_path, monkeypatch):
    # A11: `min_real_discount_pct: 0` no config era trocado pelo default
    # antes de chegar ao veredito do post.
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    capturado = []
    original = pricing.verdict

    def espiao(offer, minimo):
        capturado.append(minimo)
        return original(offer, minimo)

    monkeypatch.setattr(pricing, "verdict", espiao)
    cfg = {**CFG, "selection": {**CFG["selection"], "min_real_discount_pct": 0}}
    db = StateDB(tmp_path / "s.db")
    pipeline.run(cfg, [FakeSource([make_offer()])], [FakeChannel()], db,
                 validator=no_network_validator)
    # o veredito é consultado no ranking (bônus de EV) e no post; o config
    # zero chega aos dois — nenhum caminho troca o 0 pelo default.
    assert capturado and all(minimo == 0 for minimo in capturado)
    db.close()


# --- Fase 5B: a régua diz a verdade ------------------------------------------

def test_run_watchlist_vencida_mantem_referencias_e_pisos(tmp_path, monkeypatch):
    # Teste obrigatório 7 (C11): vencida perde só os boosts; o "De:" e o selo
    # continuam os mesmos do dia anterior — antes a watchlist inteira virava
    # None e a régua trocava de número (e de veredito) de um dia para o outro.
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    offer = make_offer(item_id="x", price_current_cents=2190)
    fatos = dict(price_refs={"x": PriceRef(3000, 90, 2800)},
                 price_floors={"x": PriceFloor(2190, 191)})
    textos = {}
    for nome, gerada in (("fresca", date.today()), ("vencida", date.today() - timedelta(days=15))):
        wl = Watchlist(generated_at=gerada, valid_days=14, hot_items={"x": 5.0}, **fatos)
        db = StateDB(tmp_path / f"{nome}.db")
        ch = FakeChannel()
        pipeline.run(CFG, [FakeSource([offer])], [ch], db, validator=no_network_validator,
                     watchlist=wl)
        db.close()
        textos[nome] = ch.sent[0].message_text
    assert textos["fresca"] == textos["vencida"]
    assert "De: <s>R$ 30,00</s> | Por: <b>R$ 21,90</b> (27% OFF)" in textos["vencida"]
    assert "🏷️ Menor preço dos últimos 6 meses (verificado)" in textos["vencida"]


def test_run_watchlist_vencida_perde_os_boosts(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    offers = [make_offer(item_id="0", commission_pct=20.0),
              make_offer(item_id="2", commission_pct=5.0)]
    vencida = Watchlist(generated_at=date.today() - timedelta(days=30), valid_days=14,
                        hot_items={"2": 50.0})
    db = StateDB(tmp_path / "s.db")
    ch = FakeChannel()
    pipeline.run(CFG, [FakeSource(offers)], [ch], db, validator=no_network_validator,
                 watchlist=vencida)
    assert ch.sent[0].offer.item_id == "0"          # sem boost o EV maior vence
    db.close()


class LiveMeli(FakeSource):
    """Fonte com preço vivo no refresh e cujo preço de descoberta NÃO é uma
    observação (é a mediana do pool) — como o MeliSource."""
    name = "meli"
    observes_price_on_discovery = False

    def __init__(self, offers, vivo):
        super().__init__(offers)
        self.vivo = vivo

    def refresh_price(self, offer):
        import dataclasses
        return dataclasses.replace(offer, price_current_cents=self.vivo)

    def resolve_affiliate_link(self, offer):
        return "https://mercadolivre.com/sec/x"


def test_run_price_log_recebe_o_preco_vivo_e_nao_o_do_pool(tmp_path, monkeypatch):
    # Teste obrigatório 10 (C7c): o ML gravava o preço do pool todo dia — o
    # "histórico próprio" dele era uma constante. Agora entra o preço vivo,
    # mesmo quando é MAIOR que o do pool (a descoberta não grava nada).
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    pool = make_offer(source="meli", item_id="MLB1", price_current_cents=7890,
                      price_ref_cents=7890, price_p25_cents=7000, price_window_days=91)
    cfg = {**CFG, "selection": {**CFG["selection"], "max_above_ref": 1.20}}
    ch = FakeChannel()
    pipeline.run(cfg, [LiveMeli([pool], 8500)], [ch], db, validator=no_network_validator)
    assert ch.sent[0].offer.price_current_cents == 8500
    assert db.price_history("meli", "MLB1", 1) == [8500]
    db.close()


def test_dry_run_nao_grava_o_preco_vivo(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    pool = make_offer(source="meli", item_id="MLB1", price_current_cents=7890, price_ref_cents=7890)
    pipeline.run(CFG, [LiveMeli([pool], 6990)], [], db, dry_run=True,
                 validator=no_network_validator)
    assert db.price_history("meli", "MLB1", 1) == []
    db.close()


def test_run_expoe_o_aviso_do_pool_do_meli(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")

    class MeliComAviso(FakeSource):
        name = "meli"
        pool_warning = "2 entrada(s) do pool ignorada(s): price_historic_min_cents ausente"

    summary = pipeline.run(CFG, [MeliComAviso([make_offer(item_id="x", source="meli")])],
                           [FakeChannel()], db, validator=no_network_validator)
    assert any("entrada(s) do pool ignorada(s)" in w for w in summary.warnings)
    db.close()


# =============================================================================
# Fase 5C (M1/C1) — estoque de candidatas: descoberta desacoplada da publicação
# =============================================================================

CFG_ESTOQUE = {**CFG, "shopee": {"candidate_max_age_days": 3}}


class FatiaDeDescoberta(FakeSource):
    """Fonte que devolve uma fatia diferente a cada run, como a varredura
    rotativa da Shopee."""

    def __init__(self, fatias):
        self._fatias = list(fatias)
        self.discovery_stats = pipeline_stats(0, 0, 0)

    def fetch_offers(self, cfg):
        lote = self._fatias.pop(0) if self._fatias else []
        self.discovery_stats = pipeline_stats(8, len(lote) * 2, len(lote))
        return lote


def pipeline_stats(calls, nodes, eligible):
    from afiliado.sources.shopee import DiscoveryStats
    return DiscoveryStats(calls, nodes, eligible)


def test_a_candidata_de_um_run_anterior_continua_publicavel(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    fonte = FatiaDeDescoberta([[make_offer(item_id="a")], [make_offer(item_id="b")]])
    cfg = {**CFG_ESTOQUE, "selection": {**CFG["selection"], "posts_per_run": 1}}
    ch = FakeChannel()
    pipeline.run(cfg, [fonte], [ch], db, validator=no_network_validator)
    assert [p.offer.item_id for p in ch.sent] == ["a"]
    # 2º run: a fatia só traz "b", mas "a" segue no estoque — e o dedupe é que
    # decide, não o esquecimento.
    pipeline.run(cfg, [fonte], [ch], db, validator=no_network_validator)
    assert [p.offer.item_id for p in ch.sent] == ["a", "b"]
    assert {o.item_id for o in db.load_candidates("shopee", 3)} == {"a", "b"}
    db.close()


def test_resumo_registra_a_fatia_de_descoberta(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    fonte = FatiaDeDescoberta([[make_offer(item_id=str(i)) for i in range(3)]])
    summary = pipeline.run(CFG_ESTOQUE, [fonte], [FakeChannel()], db,
                           validator=no_network_validator)
    assert summary.discovery == [
        "🔎 shopee: 8 chamadas · 6 nós · 3 elegíveis · 3 novos no estoque (3 no total)"]
    assert "🔎 shopee: 8 chamadas" in summary.text()
    db.close()


def test_o_preco_de_uma_candidata_do_estoque_nao_vira_observacao_de_hoje(tmp_path, monkeypatch):
    """Só a fatia RECÉM buscada entra no price_log: gravar o preço de uma
    candidata de 3 dias atrás como "hoje" inventaria uma observação."""
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    velha = make_offer(item_id="velha", price_current_cents=5000)
    db.upsert_candidates([velha])
    fonte = FatiaDeDescoberta([[make_offer(item_id="nova", price_current_cents=7000)]])
    pipeline.run(CFG_ESTOQUE, [fonte], [], db, dry_run=False,
                 validator=no_network_validator)
    assert db.price_history("shopee", "nova", 1) == [7000]
    assert db.price_history("shopee", "velha", 1) == []
    db.close()


def test_dry_run_nao_grava_o_estoque(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    fonte = FatiaDeDescoberta([[make_offer(item_id="a")]])
    pipeline.run(CFG_ESTOQUE, [fonte], [], db, dry_run=True,
                 validator=no_network_validator)
    assert db.load_candidates("shopee", 3) == []
    db.close()


def test_fonte_sem_candidate_max_age_days_nao_usa_estoque(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    fonte = FatiaDeDescoberta([[make_offer(item_id="a")], []])
    ch = FakeChannel()
    pipeline.run(CFG, [fonte], [ch], db, validator=no_network_validator)   # sem shopee:
    pipeline.run(CFG, [fonte], [ch], db, validator=no_network_validator)
    assert db.conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 0
    assert [p.offer.item_id for p in ch.sent] == ["a"]
    db.close()


# =============================================================================
# Fase 5C (M5/A6 e M6/A12)
# =============================================================================

def test_pool_de_links_do_meli_pela_metade_vira_aviso(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")

    class MeliComPoucosLinks(FakeSource):
        name = "meli"

        def link_coverage(self, offers):
            return 1, 10

    summary = pipeline.run(CFG, [MeliComPoucosLinks([make_offer(item_id="x", source="meli")])],
                           [FakeChannel()], db, validator=no_network_validator)
    assert ("⚠️ meli: só 1 de 10 produtos têm link — rode /meli-links-refresh"
            in summary.warnings)
    db.close()


def test_pool_de_links_completo_nao_avisa(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")

    class MeliCompleto(FakeSource):
        name = "meli"

        def link_coverage(self, offers):
            return 10, 10

    summary = pipeline.run(CFG, [MeliCompleto([make_offer(item_id="x", source="meli")])],
                           [FakeChannel()], db, validator=no_network_validator)
    assert not any("meli-links-refresh" in w for w in summary.warnings)
    db.close()


class CanalManual(NamedFakeChannel):
    manual = True


def test_so_canal_manual_e_despacho_nao_publicacao(tmp_path, monkeypatch):
    """A12, agora inteiro: despacho manual sai da lista de PUBLICADOS — do
    resumo e da contagem do dia. Antes `len(summary.published)` e
    `day_stats().published` contavam a arte que ninguém postou ainda, e o
    heartbeat da manhã dizia que o dia tinha publicado o que não publicou."""
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    ch = CanalManual("story_dispatch")
    summary = pipeline.run(CFG, [FakeSource([make_offer(item_id="a")])], [ch], db,
                           validator=no_network_validator)
    assert summary.published == []
    assert summary.dispatched == ["Tênis Nike SB"]
    assert f"{pipeline.DESPACHO_MANUAL} (1):" in summary.text()
    assert db.day_stats(db.local_today()).published == 0
    assert db.day_stats(db.local_today()).dispatched == 1
    assert db.count_posts_today("story_dispatch") == 1     # continua contando p/ o teto
    db.close()


# --- Fase 5F (C2): o post que FOI ao ar apesar do `ok=False` ------------------
#
# Um story sem figurinha é falha (não converte), mas ele ESTÁ na conta e o
# público vê. Enquanto `usados`/`usados_dia`/`record_post` só avançavam com
# `res.ok`, esse story não consumia `max_per_run` nem `max_per_day` e não
# entrava no dedupe: cada run publicava 2 stories quebrados e o teto de 6/dia
# não via nenhum deles.

class CanalQuePublicaEFalha(NamedFakeChannel):
    """O canal publicou de verdade e a verificação reprovou: `ok=False` com
    `publicado=True` e o id do post que está no ar."""

    def publish(self, post):
        self.sent.append(post)
        return PublishResult(False, f"PK-{len(self.sent)}",
                             error="story publicado SEM figurinha de link",
                             publicado=True)


def test_post_publicado_com_ok_false_conta_para_o_teto_e_para_o_dedupe(tmp_path,
                                                                       monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    _congela(monkeypatch, 20, 0)
    db = StateDB(tmp_path / "s.db")
    ch = CanalQuePublicaEFalha("instagram_story_link", max_per_run=1)
    ch.max_per_day = 6
    offers = [make_offer(item_id=str(i)) for i in range(3)]

    summary = pipeline.run(CFG, [FakeSource(offers)], [ch], db,
                           validator=no_network_validator)

    # UM upload, não dois: o story que foi ao ar gastou o `max_per_run`.
    assert len(ch.sent) == 1
    # Ele está no banco — logo conta para o teto do dia e para o dedupe...
    assert db.count_posts_today("instagram_story_link") == 1
    assert db.was_posted_recently("shopee", "0", days=30)
    # ...e mesmo assim NÃO é sucesso: a oferta foi descartada, com o motivo.
    assert summary.published == []
    assert len(summary.discarded) == 1
    assert "SEM figurinha de link" in summary.discarded[0][1]
    db.close()


def test_post_que_nao_foi_ao_ar_nao_conta(tmp_path, monkeypatch):
    """A contraprova: falha sem publicação (o upload levantou) não grava nada —
    senão o teto do dia seria gasto por posts que não existem."""
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    ch = NamedFakeChannel("instagram_story_link", max_per_run=1, always_fail=True)
    summary = pipeline.run(CFG, [FakeSource([make_offer(item_id="a")])], [ch], db,
                           validator=no_network_validator)
    assert db.count_posts_today("instagram_story_link") == 0
    assert not db.was_posted_recently("shopee", "a", days=30)
    assert len(summary.discarded) == 1
    db.close()


class CanalQueAvisa(NamedFakeChannel):
    """Canal que só descobre um problema PUBLICANDO (fase 5E: a Meta não
    devolveu `status_code` do container e o polling ficou cego) e o deixa em
    `warnings` para o pipeline recolher."""

    def __init__(self, name, aviso):
        super().__init__(name)
        self.warnings: list[str] = []
        self.aviso = aviso

    def publish(self, post):
        self.warnings.append(self.aviso)
        return super().publish(post)


def test_aviso_que_o_canal_descobre_publicando_entra_no_resumo(tmp_path, monkeypatch):
    """Avisos de montagem (canal sem env) já chegavam ao ops pelo
    `warnings_iniciais`. O que o canal descobre DEPOIS, publicando, não tinha
    caminho nenhum — ficava numa lista dentro do objeto. Agora sai pelo mesmo
    `warn`: uma vez por dia, e a lista do canal é drenada."""
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    ch = CanalQueAvisa("instagram_story", "⚠️ instagram_story: polling cego")
    summary = pipeline.run(CFG, [FakeSource([make_offer(item_id="a")])], [ch], db,
                           validator=no_network_validator)
    assert "⚠️ instagram_story: polling cego" in summary.warnings
    assert ch.warnings == []
    db.close()


def test_canal_sem_lista_de_avisos_continua_publicando(tmp_path, monkeypatch):
    """`warnings` é opcional: os outros canais não têm e não podem quebrar."""
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    ch = NamedFakeChannel("telegram")
    summary = pipeline.run(CFG, [FakeSource([make_offer(item_id="a")])], [ch], db,
                           validator=no_network_validator)
    assert summary.published == ["Tênis Nike SB"]
    db.close()


def test_heartbeat_separa_despachos_de_publicacoes(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    _congela(monkeypatch, 12, 0, dia=25)
    canais = [NamedFakeChannel("telegram"), CanalManual("story_dispatch")]
    pipeline.run(CFG, [FakeSource([make_offer(item_id="a"), make_offer(item_id="b")])],
                 canais, db, validator=no_network_validator)
    _congela(monkeypatch, 8, 0, dia=26)
    summary = pipeline.run(CFG, [FakeSource([make_offer(item_id="c")])],
                           [NamedFakeChannel("telegram")], db,
                           validator=no_network_validator)
    assert ("☀️ Bom dia — ontem: 2 publicados, 2 despachados, 0 descartados em 1 runs"
            in summary.warnings)
    db.close()


def test_com_canal_automatico_junto_o_resumo_diz_publicado(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    canais = [NamedFakeChannel("telegram"), CanalManual("story_dispatch")]
    summary = pipeline.run(CFG, [FakeSource([make_offer(item_id="a")])], canais, db,
                           validator=no_network_validator)
    assert summary.published == ["Tênis Nike SB"]
    db.close()


# =============================================================================
# Fase 5C (M2) — cota por fonte
# =============================================================================

CFG_COTA = {**CFG,
            "selection": {**CFG["selection"], "posts_per_run": 1,
                          "source_quota": {"shopee": 0.5, "meli": 0.5}},
            "channels": {"telegram": {"enabled": True, "max_per_day": 60}}}


class FonteChamada(FakeSource):
    def __init__(self, name, offers):
        self.name = name
        self._offers = offers


def test_ml_abaixo_da_cota_publica_antes_da_shopee(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    # 30 ofertas da Shopee já publicadas hoje: a cota dela (30 de 60) acabou.
    for k in range(30):
        db.record_post(Post(offer=make_offer(item_id=f"ontem-{k}"),
                            copy=CopyParts("h", "d", "c"), affiliate_link="l"),
                       "telegram", "x")
    shopee = FonteChamada("shopee", [make_offer(item_id="s", commission_pct=50.0)])
    meli = FonteChamada("meli", [make_offer(item_id="MLB1", source="meli",
                                            commission_pct=1.0)])
    ch = FakeChannel()
    pipeline.run(CFG_COTA, [shopee, meli], [ch], db, validator=no_network_validator)
    assert [p.offer.source for p in ch.sent] == ["meli"]   # apesar do EV menor
    db.close()


def test_sem_candidata_do_ml_a_shopee_completa(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    for k in range(40):
        db.record_post(Post(offer=make_offer(item_id=f"ontem-{k}"),
                            copy=CopyParts("h", "d", "c"), affiliate_link="l"),
                       "telegram", "x")
    shopee = FonteChamada("shopee", [make_offer(item_id="s")])
    meli = FonteChamada("meli", [])                 # pool vazio
    ch = FakeChannel()
    pipeline.run(CFG_COTA, [shopee, meli], [ch], db, validator=no_network_validator)
    assert [p.offer.item_id for p in ch.sent] == ["s"]
    db.close()


def test_trinta_dias_com_cota_meio_a_meio(tmp_path, monkeypatch):
    """Teste obrigatório (2): 60/dia, cota 50/50, pool ML de 200 e estoque
    Shopee de 5.000 — nenhum item repete em 30 dias e o ML fica em ~50%
    enquanto tem candidata."""
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    shopee = FonteChamada("shopee", [make_offer(item_id=f"s{i}") for i in range(5000)])
    meli = FonteChamada("meli", [make_offer(item_id=f"MLB{i}", source="meli")
                                 for i in range(200)])
    cfg = {**CFG_COTA, "selection": {**CFG_COTA["selection"], "posts_per_run": 60}}
    ch = NamedFakeChannel("telegram")
    ch.max_per_day = 60
    publicados = []
    for dia in range(1, 31):
        _congela(monkeypatch, 23, 50, dia=dia)
        n = len(ch.sent)
        pipeline.run(cfg, [shopee, meli], [ch], db, validator=no_network_validator)
        publicados.append([p.offer.source for p in ch.sent[n:]])
    todos = [p.offer.item_id for p in ch.sent]
    assert len(todos) == 30 * 60 == len(set(todos))       # nada repete em 30 dias
    do_ml = [d.count("meli") for d in publicados]
    # os primeiros dias esvaziam o pool do ML (200 itens = 3 dias e meio a 60/dia)
    assert do_ml[0] == 30 and do_ml[1] == 30
    assert sum(do_ml) == 200                              # o ML entregou tudo que tinha
    assert all(len(d) == 60 for d in publicados)          # a Shopee completou o resto
    db.close()


def test_candidata_vencida_sai_do_estoque(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    _congela(monkeypatch, 12, 0, dia=20)
    db.upsert_candidates([make_offer(item_id="velha")])
    _congela(monkeypatch, 12, 0, dia=26)
    fonte = FatiaDeDescoberta([[make_offer(item_id="nova")]])
    pipeline.run(CFG_ESTOQUE, [fonte], [], db, validator=no_network_validator)
    assert [o.item_id for o in db.load_candidates("shopee", 3)] == ["nova"]
    assert db.conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 1
    db.close()


# =============================================================================
# Rodada de correção da 5C (C1) — freios da fila de publicação
# =============================================================================


class FonteQueSoErra(FakeSource):
    """Fonte cujo `refresh_price` sempre levanta `SourceError` — a API que
    começou a recusar. Conta as chamadas, que é o que sangra a conta."""

    def __init__(self, offers, name="shopee"):
        super().__init__(offers)
        self.name = name
        self.chamadas = 0

    def refresh_price(self, offer):
        self.chamadas += 1
        raise SourceError(f"{self.name}: item {offer.item_id} saiu da listagem")


def test_estoque_gigante_com_refresh_quebrado_nao_martela_a_api(tmp_path, monkeypatch):
    """C1 da revisão: `fila` virou o estoque INTEIRO e `refresh_price` uma
    chamada de API real. Um `SourceError` por oferta dava 5.000 descartes e
    5.000 chamadas num run só — com o backoff de 0,5+1,5+4,0 s do `_post`, um
    martelo de horas contra a conta de afiliado."""
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    fonte = FonteQueSoErra([make_offer(item_id=str(i)) for i in range(5000)])
    summary = pipeline.run(CFG, [fonte], [FakeChannel()], db,
                           validator=no_network_validator)
    assert fonte.chamadas <= 50
    assert len(summary.discarded) <= 50
    assert any("fonte shopee: 10 falhas seguidas — fonte fechada neste run" in w
               for w in summary.warnings)
    assert any("descartes no run — fila interrompida" in w for w in summary.warnings)
    db.close()


def test_o_teto_de_descartes_encerra_a_fila(tmp_path, monkeypatch):
    """O outro freio: descarte que NÃO é erro de fonte (validação, link) não
    aciona o circuito, e sem teto varreria o estoque inteiro."""
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")

    def validator(post, cfg, client=None):
        raise ValidationError("imagem fora do ar")

    fonte = FakeSource([make_offer(item_id=str(i)) for i in range(5000)])
    summary = pipeline.run(CFG, [fonte], [FakeChannel()], db, validator=validator)
    assert len(summary.discarded) == pipeline.DEFAULT_MAX_DESCARTES_POR_RUN == 50
    assert "⚠️ 50 descartes no run — fila interrompida" in summary.warnings
    db.close()


def test_teto_de_descartes_vem_do_config(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")

    def validator(post, cfg, client=None):
        raise ValidationError("imagem fora do ar")

    cfg = {**CFG, "selection": {**CFG["selection"], "max_descartes_por_run": 5}}
    fonte = FakeSource([make_offer(item_id=str(i)) for i in range(100)])
    summary = pipeline.run(cfg, [fonte], [FakeChannel()], db, validator=validator)
    assert len(summary.discarded) == 5
    db.close()


def test_o_circuito_fecha_so_a_fonte_que_falha(tmp_path, monkeypatch):
    """Espelha `MAX_FALHAS_SEGUIDAS_POR_CANAL`: a fonte que erra 10 vezes
    seguidas sai da fila deste run — as candidatas da OUTRA continuam."""
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    shopee = FonteQueSoErra([make_offer(item_id=f"s{i}") for i in range(100)])
    meli = FonteChamada("meli", [make_offer(item_id="MLB1", source="meli")])
    cfg = {**CFG, "selection": {**CFG["selection"], "posts_per_run": 1}}
    ch = FakeChannel()
    summary = pipeline.run(cfg, [shopee, meli], [ch], db, validator=no_network_validator)
    assert shopee.chamadas == pipeline.MAX_FALHAS_SEGUIDAS_POR_FONTE == 10
    assert [p.offer.item_id for p in ch.sent] == ["MLB1"]
    assert not any("fonte meli" in w for w in summary.warnings)
    db.close()


def test_falhas_alternadas_nao_fecham_a_fonte(tmp_path, monkeypatch):
    """O circuito conta falhas SEGUIDAS: itens que saem da listagem no meio de
    uma fila saudável não podem fechar a fonte."""
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")

    class QuebraNosPares(FakeSource):
        def refresh_price(self, offer):
            if int(offer.item_id) % 2 == 0:
                raise SourceError(f"shopee: item {offer.item_id} saiu da listagem")
            return offer

    cfg = {**CFG, "selection": {**CFG["selection"], "posts_per_run": 20}}
    fonte = QuebraNosPares([make_offer(item_id=str(i)) for i in range(40)])
    ch = FakeChannel()
    summary = pipeline.run(cfg, [fonte], [ch], db, validator=no_network_validator)
    assert len(ch.sent) == 20                      # os 20 ímpares publicaram
    assert not any("fonte fechada" in w for w in summary.warnings)
    db.close()

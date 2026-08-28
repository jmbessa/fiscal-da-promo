from datetime import datetime, timedelta, timezone

from afiliado import state
from afiliado.models import NO_CLAIM, CopyParts, Post, Verdict
from afiliado.state import StateDB
from tests.test_models import make_offer

BRT = timezone(timedelta(hours=-3))


def _congela(monkeypatch, instante_brt: datetime) -> None:
    """Fixa o relógio do StateDB num instante dado em BRT (guardado em UTC)."""
    monkeypatch.setattr(state, "_now", lambda: instante_brt.astimezone(timezone.utc))


def make_post(verdict: Verdict = NO_CLAIM, **offer_kw) -> Post:
    return Post(
        offer=make_offer(**offer_kw),
        copy=CopyParts(headline="h", description="d", cta="c"),
        affiliate_link="https://shope.ee/x",
        message_text="msg",
        verdict=verdict,
    )


def test_record_and_dedupe(tmp_path):
    db = StateDB(tmp_path / "state.db")
    assert not db.was_posted_recently("shopee", "123456", days=30)
    db.record_post(make_post(), channel="telegram", message_id="42")
    assert db.was_posted_recently("shopee", "123456", days=30)
    assert not db.was_posted_recently("shopee", "999", days=30)
    db.close()


def test_recent_titles(tmp_path):
    db = StateDB(tmp_path / "state.db")
    db.record_post(make_post(), channel="telegram", message_id="1")
    assert db.recent_titles(days=7) == ["Tênis Nike SB"]
    db.close()


def test_record_run(tmp_path):
    db = StateDB(tmp_path / "state.db")
    db.record_run(published=3, discarded=1, notes="ok")
    db.close()  # sem exceção = schema e insert funcionam


def test_record_price_mantem_o_menor_do_dia(tmp_path):
    # Duas observações no mesmo dia: a referência fica com a MENOR (a mais
    # conservadora — menos desconto alegado depois).
    db = StateDB(tmp_path / "state.db")
    db.record_price("shopee", "123456", 3390, day="2026-08-20")
    db.record_price("shopee", "123456", 2600, day="2026-08-20")
    db.record_price("shopee", "123456", 6890, day="2026-08-20")
    assert db.price_history("shopee", "123456", days=30) == [2600]
    db.close()


def test_record_price_ignora_valores_nao_positivos(tmp_path):
    db = StateDB(tmp_path / "state.db")
    db.record_price("shopee", "123456", 0, day="2026-08-20")
    db.record_price("shopee", "123456", -100, day="2026-08-21")
    assert db.price_history("shopee", "123456", days=3650) == []
    db.close()


def test_price_history_respeita_a_janela_e_ordena_do_mais_recente(tmp_path):
    from datetime import date, timedelta

    db = StateDB(tmp_path / "state.db")
    hoje = db.local_today()      # o dia da OPERAÇÃO, não o da máquina (CI roda em UTC)
    for delta, cents in ((0, 3390), (10, 2600), (100, 9999)):
        db.record_price("shopee", "123456", cents,
                        day=(hoje - timedelta(days=delta)).isoformat())
    assert db.price_history("shopee", "123456", days=90) == [3390, 2600]
    assert db.price_history("shopee", "123456", days=365) == [3390, 2600, 9999]
    assert db.price_history("shopee", "outro", days=365) == []
    db.close()


def test_prune_price_log_apaga_o_que_saiu_da_janela(tmp_path):
    from datetime import date, timedelta

    db = StateDB(tmp_path / "state.db")
    hoje = db.local_today()      # o dia da OPERAÇÃO, não o da máquina (CI roda em UTC)
    for delta, cents in ((0, 3390), (10, 2600), (100, 9999)):
        db.record_price("shopee", "123456", cents,
                        day=(hoje - timedelta(days=delta)).isoformat())
    db.prune_price_log(days=90)
    assert db.price_history("shopee", "123456", days=3650) == [3390, 2600]
    db.close()


def test_record_run_poda_o_price_log(tmp_path):
    from datetime import date, timedelta

    db = StateDB(tmp_path / "state.db")
    hoje = db.local_today()      # o dia da OPERAÇÃO, não o da máquina (CI roda em UTC)
    db.record_price("shopee", "123456", 9999,
                    day=(hoje - timedelta(days=100)).isoformat())
    db.record_price("shopee", "123456", 2600, day=hoje.isoformat())
    db.record_run(published=1, discarded=0, ref_window_days=90)
    assert db.price_history("shopee", "123456", days=3650) == [2600]
    db.close()


def test_count_posts_today(tmp_path):
    from datetime import date, timedelta

    db = StateDB(tmp_path / "state.db")
    db.record_post(make_post(item_id="1"), channel="a", message_id="1")
    db.record_post(make_post(item_id="2"), channel="a", message_id="2")
    db.record_post(make_post(item_id="3"), channel="b", message_id="3")

    ontem = db.local_today() - timedelta(days=1)
    db.conn.execute(
        "INSERT OR REPLACE INTO posted (source, item_id, channel, title, price_cents, "
        "message_id, posted_at) VALUES (?,?,?,?,?,?,?)",
        ("shopee", "999", "a", "Tênis de ontem", 1000, "9",
         f"{ontem.isoformat()}T12:00:00"),
    )
    db.conn.commit()

    assert db.count_posts_today("a") == 2
    assert db.count_posts_today("b") == 1
    assert db.count_posts_today("c") == 0
    db.close()


# --- Fase 5A: dia LOCAL (C3) --------------------------------------------------

def test_count_posts_today_conta_o_dia_local_nao_o_utc(tmp_path, monkeypatch):
    # Post às 22:00 BRT de 25/08 é 01:00 UTC de 26/08. Conta no dia BRT 25/08
    # — antes contava no "dia" seguinte e o canal calava das 13:20 às 21:00.
    db = StateDB(tmp_path / "s.db", timezone="America/Sao_Paulo")
    _congela(monkeypatch, datetime(2026, 8, 25, 22, 0, tzinfo=BRT))
    db.record_post(make_post(item_id="1"), channel="telegram", message_id="1")
    assert db.count_posts_today("telegram") == 1
    _congela(monkeypatch, datetime(2026, 8, 25, 23, 55, tzinfo=BRT))
    assert db.count_posts_today("telegram") == 1
    _congela(monkeypatch, datetime(2026, 8, 26, 8, 0, tzinfo=BRT))
    assert db.count_posts_today("telegram") == 0
    db.close()


def test_count_posts_today_aceita_um_instante_explicito(tmp_path, monkeypatch):
    db = StateDB(tmp_path / "s.db", timezone="America/Sao_Paulo")
    _congela(monkeypatch, datetime(2026, 8, 25, 22, 0, tzinfo=BRT))
    db.record_post(make_post(item_id="1"), channel="telegram", message_id="1")
    assert db.count_posts_today("telegram", now=datetime(2026, 8, 26, 8, 0, tzinfo=BRT)) == 0
    assert db.count_posts_today("telegram", now=datetime(2026, 8, 25, 23, 0, tzinfo=BRT)) == 1
    db.close()


def test_count_posts_today_em_utc_reproduz_o_bug_antigo(tmp_path, monkeypatch):
    # Contraste: com o fuso em UTC o post das 22:00 BRT ainda "é de hoje" às
    # 08:00 BRT do dia seguinte — exatamente o C3.
    db = StateDB(tmp_path / "s.db", timezone="UTC")
    _congela(monkeypatch, datetime(2026, 8, 25, 22, 0, tzinfo=BRT))
    db.record_post(make_post(item_id="1"), channel="telegram", message_id="1")
    _congela(monkeypatch, datetime(2026, 8, 26, 8, 0, tzinfo=BRT))
    assert db.count_posts_today("telegram") == 1
    db.close()


def test_state_db_fuso_padrao_e_sao_paulo(tmp_path):
    db = StateDB(tmp_path / "s.db")
    assert str(db.tz) == "America/Sao_Paulo"
    db.close()


def test_record_price_grava_o_dia_local(tmp_path, monkeypatch):
    db = StateDB(tmp_path / "s.db", timezone="America/Sao_Paulo")
    _congela(monkeypatch, datetime(2026, 8, 25, 22, 0, tzinfo=BRT))   # 01:00 UTC de 26/08
    db.record_price("shopee", "1", 2600)
    assert db.conn.execute("SELECT day FROM price_log").fetchone()[0] == "2026-08-25"
    assert db.price_history("shopee", "1", days=0) == [2600]     # "hoje" local
    db.close()


# --- Fase 5A: avisos uma vez por dia (A3) ------------------------------------

def test_warn_once_uma_vez_por_dia_local(tmp_path, monkeypatch):
    db = StateDB(tmp_path / "s.db", timezone="America/Sao_Paulo")
    _congela(monkeypatch, datetime(2026, 8, 25, 22, 0, tzinfo=BRT))
    assert db.warn_once("watchlist vencida") is True
    assert db.warn_once("watchlist vencida") is False
    # 23:59 BRT já é o dia seguinte em UTC, mas ainda o mesmo dia local.
    _congela(monkeypatch, datetime(2026, 8, 25, 23, 59, tzinfo=BRT))
    assert db.warn_once("watchlist vencida") is False
    assert db.warn_once("outro aviso") is True
    _congela(monkeypatch, datetime(2026, 8, 26, 8, 0, tzinfo=BRT))
    assert db.warn_once("watchlist vencida") is True
    db.close()


def test_record_run_poda_os_avisos_de_dias_anteriores(tmp_path, monkeypatch):
    # M0-6 (revisão da 5A): `warned` nunca era podada. Só o dia local de hoje
    # interessa ao warn_once; o resto sai junto com a poda do price_log.
    db = StateDB(tmp_path / "s.db", timezone="America/Sao_Paulo")
    _congela(monkeypatch, datetime(2026, 8, 25, 12, 0, tzinfo=BRT))
    assert db.warn_once("a") is True
    _congela(monkeypatch, datetime(2026, 8, 26, 12, 0, tzinfo=BRT))
    assert db.warn_once("b") is True
    db.record_run(published=0, discarded=0)
    rows = db.conn.execute("SELECT key, day FROM warned ORDER BY day").fetchall()
    assert rows == [("b", "2026-08-26")]
    assert db.warn_once("b") is False           # o de hoje continua valendo
    db.close()


# --- Fase 5F (C2): marcas do DIA que sobrevivem ao processo -------------------
#
# O desarme do `instagram_story_link` vivia só na instância: um story sem
# figurinha fechava o canal até o fim do run e o processo seguinte começava
# armado de novo. Seis runs/dia = ~12 stories sem link por dia, para sempre.
# `day_flag`/`set_day_flag` são o mesmo "uma vez por dia local" da tabela
# `warned`, só que guardando um VALOR (o aviso) em vez de um carimbo.

def test_day_flag_guarda_e_devolve_no_mesmo_dia_local(tmp_path, monkeypatch):
    db = StateDB(tmp_path / "s.db", timezone="America/Sao_Paulo")
    _congela(monkeypatch, datetime(2026, 8, 25, 22, 0, tzinfo=BRT))
    assert db.day_flag("story_link_desarmado") == ""
    db.set_day_flag("story_link_desarmado", "⚠️ canal desarmado")

    # Outro processo, mesmo banco, mesmo dia local: a marca continua lá.
    outro = StateDB(tmp_path / "s.db", timezone="America/Sao_Paulo")
    assert outro.day_flag("story_link_desarmado") == "⚠️ canal desarmado"
    outro.close()

    # 23:59 BRT já é o dia seguinte em UTC — e ainda é o mesmo dia local.
    _congela(monkeypatch, datetime(2026, 8, 25, 23, 59, tzinfo=BRT))
    assert db.day_flag("story_link_desarmado") == "⚠️ canal desarmado"
    # O dia vira e a marca some sozinha: o canal amanhece rearmado.
    _congela(monkeypatch, datetime(2026, 8, 26, 8, 0, tzinfo=BRT))
    assert db.day_flag("story_link_desarmado") == ""
    db.close()


def test_day_flag_apagada_e_reescrita(tmp_path, monkeypatch):
    db = StateDB(tmp_path / "s.db", timezone="America/Sao_Paulo")
    _congela(monkeypatch, datetime(2026, 8, 25, 22, 0, tzinfo=BRT))
    db.set_day_flag("k", "1")
    db.set_day_flag("k", "2")
    assert db.day_flag("k") == "2"
    db.set_day_flag("k", "")                 # vazio = apaga (rearma)
    assert db.day_flag("k") == ""
    db.close()


def test_day_flag_nao_escreve_em_dry_run(tmp_path, monkeypatch):
    """A10: em `--dry-run` nada é gravado — nem o desarme."""
    db = StateDB(tmp_path / "s.db", timezone="America/Sao_Paulo")
    _congela(monkeypatch, datetime(2026, 8, 25, 22, 0, tzinfo=BRT))
    db.somente_leitura = True
    db.set_day_flag("k", "x")
    assert db.day_flag("k") == ""
    db.close()


def test_record_run_poda_as_marcas_de_dias_anteriores(tmp_path, monkeypatch):
    db = StateDB(tmp_path / "s.db", timezone="America/Sao_Paulo")
    _congela(monkeypatch, datetime(2026, 8, 25, 12, 0, tzinfo=BRT))
    db.set_day_flag("a", "ontem")
    _congela(monkeypatch, datetime(2026, 8, 26, 12, 0, tzinfo=BRT))
    db.set_day_flag("b", "hoje")
    db.record_run(published=0, discarded=0)
    rows = db.conn.execute("SELECT key, day FROM day_flags ORDER BY day").fetchall()
    assert rows == [("b", "2026-08-26")]
    db.close()


# --- Fase 5A: heartbeat (contagem de ontem) ----------------------------------

def test_day_stats_de_ontem(tmp_path, monkeypatch):
    db = StateDB(tmp_path / "s.db", timezone="America/Sao_Paulo")
    _congela(monkeypatch, datetime(2026, 8, 25, 22, 0, tzinfo=BRT))
    db.record_post(make_post(item_id="a"), channel="telegram", message_id="1")
    db.record_post(make_post(item_id="a"), channel="story_dispatch", message_id="2",
                   manual=True)                                          # mesma oferta
    db.record_post(make_post(item_id="b"), channel="telegram", message_id="3")
    db.record_run(published=2, discarded=3)
    db.record_run(published=0, discarded=1)
    _congela(monkeypatch, datetime(2026, 8, 26, 8, 0, tzinfo=BRT))
    ontem = db.day_stats(db.local_today() - timedelta(days=1))
    assert (ontem.published, ontem.discarded, ontem.runs) == (2, 4, 2)
    assert ontem.dispatched == 1
    assert db.day_stats(db.local_today()) == state.DayStats(0, 0, 0)
    db.close()


def test_banco_sem_a_coluna_manual_e_migrado(tmp_path):
    """`CREATE TABLE IF NOT EXISTS` não acrescenta coluna: um `state.db` criado
    antes desta rodada precisa do ALTER TABLE, e as linhas dele valem como
    publicação (era a semântica que tinham)."""
    import sqlite3
    caminho = tmp_path / "velho.db"
    velho = sqlite3.connect(caminho)
    velho.executescript(
        "CREATE TABLE posted (source TEXT NOT NULL, item_id TEXT NOT NULL, "
        "channel TEXT NOT NULL, title TEXT NOT NULL, price_cents INTEGER NOT NULL, "
        "message_id TEXT NOT NULL DEFAULT '', posted_at TEXT NOT NULL, "
        "PRIMARY KEY (source, item_id, channel)) WITHOUT ROWID;")
    velho.execute("INSERT INTO posted VALUES ('shopee','1','telegram','t',100,'1',?)",
                  (datetime.now(timezone.utc).isoformat(),))
    velho.commit()
    velho.close()

    db = StateDB(caminho, timezone="America/Sao_Paulo")
    hoje = db.day_stats(db.local_today())
    assert (hoje.published, hoje.dispatched) == (1, 0)
    db.record_post(make_post(item_id="2"), channel="story_dispatch", message_id="2",
                   manual=True)
    assert db.day_stats(db.local_today()).dispatched == 1
    db.close()


def test_day_stats_nao_conta_despacho_manual_como_publicacao(tmp_path, monkeypatch):
    """A12 (rodada de correção): a arte que foi só para o chat de operações
    ainda não é um post — quem posta é o dono, à mão. Contá-la como publicada
    fazia o heartbeat da manhã relatar um dia melhor do que o dia foi."""
    db = StateDB(tmp_path / "s.db", timezone="America/Sao_Paulo")
    _congela(monkeypatch, datetime(2026, 8, 26, 12, 0, tzinfo=BRT))
    db.record_post(make_post(item_id="so-arte"), channel="story_dispatch",
                   message_id="1", manual=True)
    hoje = db.day_stats(db.local_today())
    assert (hoje.published, hoje.dispatched) == (0, 1)
    # ...e o teto do canal continua contando a arte (é esforço manual do dia)
    assert db.count_posts_today("story_dispatch") == 1
    db.close()


# --- Fase 5C (M1/C1): estoque de candidatas e cursor de descoberta -----------

def test_upsert_e_load_candidates(tmp_path):
    db = StateDB(tmp_path / "s.db")
    novos = db.upsert_candidates([make_offer(item_id="a"), make_offer(item_id="b")])
    assert novos == 2
    # regravar as mesmas não conta como novo, mas atualiza o payload
    assert db.upsert_candidates([make_offer(item_id="a", title="outro")]) == 0
    estoque = db.load_candidates("shopee", max_age_days=3)
    assert {o.item_id for o in estoque} == {"a", "b"}
    assert [o.title for o in estoque if o.item_id == "a"] == ["outro"]
    assert db.load_candidates("meli", max_age_days=3) == []
    db.close()


def test_load_candidates_preserva_a_regua_da_oferta(tmp_path):
    db = StateDB(tmp_path / "s.db")
    db.upsert_candidates([make_offer(item_id="a", price_ref_cents=3000,
                                     price_p25_cents=2800, price_window_days=90,
                                     price_floor_cents=2500, price_floor_window_days=365,
                                     commission_brl=4.5, rating=4.8, sales=1200)])
    (o,) = db.load_candidates("shopee", max_age_days=3)
    assert (o.price_ref_cents, o.price_p25_cents, o.price_window_days) == (3000, 2800, 90)
    assert (o.price_floor_cents, o.price_floor_window_days) == (2500, 365)
    assert (o.commission_brl, o.rating, o.sales) == (4.5, 4.8, 1200)
    db.close()


def test_candidatas_velhas_saem_do_estoque(tmp_path, monkeypatch):
    db = StateDB(tmp_path / "s.db")
    _congela(monkeypatch, datetime(2026, 8, 20, 12, 0, tzinfo=BRT))
    db.upsert_candidates([make_offer(item_id="velha")])
    _congela(monkeypatch, datetime(2026, 8, 26, 12, 0, tzinfo=BRT))
    db.upsert_candidates([make_offer(item_id="nova")])
    assert {o.item_id for o in db.load_candidates("shopee", 3)} == {"nova"}
    db.prune_candidates(3)
    assert db.conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 1
    db.close()


def test_prune_candidates_por_fonte(tmp_path, monkeypatch):
    db = StateDB(tmp_path / "s.db")
    _congela(monkeypatch, datetime(2026, 8, 20, 12, 0, tzinfo=BRT))
    db.upsert_candidates([make_offer(item_id="a"), make_offer(item_id="m", source="meli")])
    _congela(monkeypatch, datetime(2026, 8, 26, 12, 0, tzinfo=BRT))
    db.prune_candidates(3, source="shopee")
    fontes = [r[0] for r in db.conn.execute("SELECT source FROM candidates").fetchall()]
    assert fontes == ["meli"]
    db.close()


def test_payload_corrompido_nao_derruba_o_estoque(tmp_path):
    db = StateDB(tmp_path / "s.db")
    db.upsert_candidates([make_offer(item_id="ok")])
    db.conn.execute("INSERT INTO candidates VALUES ('shopee','lixo',?,'{')",
                    (state._now().isoformat(),))
    db.conn.commit()
    assert [o.item_id for o in db.load_candidates("shopee", 3)] == ["ok"]
    db.close()


def test_tabelas_de_linha_curta_sao_without_rowid(tmp_path):
    """O Actions commita o state.db a cada run, e o estoque de candidatas +
    90 dias de price_log fazem o arquivo passar de 50 MB. WITHOUT ROWID nas
    tabelas de linha curta corta o price_log pela metade (47 MB → 22 MB com
    630 mil linhas). `candidates` (payload JSON longo) e `runs`
    (AUTOINCREMENT) ficam de fora de propósito."""
    db = StateDB(tmp_path / "s.db")
    sql = {nome: texto for nome, texto in db.conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='table'").fetchall()}
    for tabela in ("posted", "price_log", "warned", "discovery_cursor"):
        assert "WITHOUT ROWID" in sql[tabela], tabela
    for tabela in ("candidates", "runs"):
        assert "WITHOUT ROWID" not in sql[tabela], tabela
    db.close()


def test_cursor_de_descoberta_persiste(tmp_path):
    db = StateDB(tmp_path / "s.db")
    assert db.get_cursor("shopee:root_page:100630", "1") == "1"
    db.set_cursor("shopee:root_page:100630", "7")
    assert db.get_cursor("shopee:root_page:100630", "1") == "7"
    db.set_cursor("shopee:root_page:100630", "8")
    assert db.get_cursor("shopee:root_page:100630", "1") == "8"
    db.close()


def test_recently_posted_e_price_histories_batem_com_a_versao_item_a_item(tmp_path):
    """As duas consultas em lote (fase 5C) existem só porque o estoque tem
    milhares de itens — o RESULTADO tem de ser idêntico ao item a item."""
    db = StateDB(tmp_path / "s.db")
    db.record_post(make_post(item_id="a"), channel="telegram", message_id="1")
    db.record_post(make_post(item_id="m", source="meli"), channel="telegram", message_id="2")
    assert db.recently_posted(30) == {("shopee", "a"), ("meli", "m")}
    assert db.recently_posted(0) == set()

    db.record_price("shopee", "a", 1000, day="2026-08-24")
    db.record_price("shopee", "a", 900, day="2026-08-25")
    db.record_price("shopee", "b", 700, day="2026-08-25")
    lote = db.price_histories("shopee", ["a", "b", "sem_historico"], 3650)
    assert lote == {"a": db.price_history("shopee", "a", 3650),
                    "b": db.price_history("shopee", "b", 3650)}
    assert lote["a"] == [900, 1000]                 # mais recente primeiro
    db.close()


def test_price_histories_em_lotes_de_mais_de_500_ids(tmp_path):
    db = StateDB(tmp_path / "s.db")
    db.record_prices([("shopee", f"i{n}", 100 + n) for n in range(1200)])
    lote = db.price_histories("shopee", [f"i{n}" for n in range(1200)], 30)
    assert len(lote) == 1200 and lote["i1199"] == [1299]
    db.close()


# --- Fase 5C (M2): cota por fonte -------------------------------------------

def test_posted_today_by_source(tmp_path, monkeypatch):
    db = StateDB(tmp_path / "s.db", timezone="America/Sao_Paulo")
    _congela(monkeypatch, datetime(2026, 8, 25, 22, 0, tzinfo=BRT))
    db.record_post(make_post(item_id="a"), channel="telegram", message_id="1")
    db.record_post(make_post(item_id="a"), channel="story_dispatch", message_id="2")
    db.record_post(make_post(item_id="m", source="meli"), channel="telegram", message_id="3")
    assert db.posted_today_by_source() == {"shopee": 1, "meli": 1}
    _congela(monkeypatch, datetime(2026, 8, 26, 8, 0, tzinfo=BRT))
    assert db.posted_today_by_source() == {}
    db.close()

"""Reproducoes adversariais - area OPERACAO E CONTINUIDADE.

Rodar da raiz do worktree:
  PYTHONPATH="$PWD/src" python <este arquivo>
Nao toca na rede: fontes/canais falsos, httpx.MockTransport para o Telegram.

Ajustes da fase 5A (API mudou, o cenario e o mesmo): o relogio dos cenarios
1-2 e congelado as 23:55 BRT (o ritmo diario fecharia os canais fora da
janela por outro motivo); _build_channels/_build_sources devolvem (lista,
avisos) no cenario 9.
"""
import json
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from afiliado import cli, copywriter, llm, pipeline, state
from afiliado.channels.base import PublishResult
from afiliado.channels.telegram import TelegramChannel, send_text
from afiliado.models import Offer
from afiliado.state import StateDB
from afiliado.watchlist import Watchlist
from datetime import date

CFG = {
    "selection": {"posts_per_run": 1, "price_min_brl": 20, "price_max_brl": 1000,
                  "dedupe_days": 30, "category_ids": [], "max_above_ref": 1.00,
                  "require_price_ref": False, "min_real_discount_pct": 10,
                  "ref_window_days": 90, "ref_min_observations": 5,
                  "seal_tolerance": 1.05, "min_ev_brl": 0,
                  "ev_weights": {"popularity": 0.3, "discount": 0.5}},
    "llm": {"model": "haiku"},
    "copy": {"tone": "pt-BR"},
    "validation": {"allowed_domains": ["shope.ee"]},
}


def make_offer(i: int) -> Offer:
    return Offer(source="shopee", item_id=str(i), title=f"Produto numero {i} com nome longo",
                 price_original_cents=10000, price_current_cents=5000, commission_pct=5.0,
                 image_url="https://img/x.jpg", product_url="https://shopee.com.br/p",
                 offer_link="https://shope.ee/x", sales=1000, commission_brl=2.5)


class FakeSource:
    name = "shopee"

    def __init__(self, offers):
        self._offers = offers
        self.link_calls = 0

    def fetch_offers(self, cfg):
        return self._offers

    def resolve_affiliate_link(self, offer):
        self.link_calls += 1
        return "https://shope.ee/ok"


class FakeChannel:
    def __init__(self, name, max_per_day=None, fail=False):
        self.name = name
        self.max_per_day = max_per_day
        self.fail = fail
        self.calls = 0

    def publish(self, post):
        self.calls += 1
        if self.fail:
            return PublishResult(False, error="Bad Request: chat not found")
        return PublishResult(True, str(self.calls))


def noop_validator(post, cfg, client=None):
    return None


def sep(title):
    print("\n" + "=" * 78 + f"\n{title}\n" + "=" * 78)


BRT = timezone(timedelta(hours=-3))
_relogio_real = state._now


def congela(hh, mm):
    state._now = lambda: datetime(2026, 8, 26, hh, mm, tzinfo=BRT).astimezone(timezone.utc)


# ---------------------------------------------------------------------------
sep("1. TETO DIARIO ATINGIDO -> o loop varre a fila inteira gastando LLM/links")
congela(23, 55)
llm_calls = {"n": 0}
orig_ask = llm.ask_json
llm.ask_json = lambda *a, **k: llm_calls.__setitem__("n", llm_calls["n"] + 1) or None

with tempfile.TemporaryDirectory() as tmp:
    db = StateDB(Path(tmp) / "s.db")
    n_offers = 60
    src = FakeSource([make_offer(i) for i in range(n_offers)])
    tg = FakeChannel("telegram", max_per_day=100)
    st = FakeChannel("story_dispatch", max_per_day=6)
    ig = FakeChannel("instagram_feed", max_per_day=2)
    # simula o dia ja no teto dos 3 canais
    for ch, cap in ((tg, 100), (st, 6), (ig, 2)):
        for j in range(cap):
            db.conn.execute("INSERT INTO posted VALUES (?,?,?,?,?,?,?)",
                            ("shopee", f"old{j}", ch.name, "t", 100, "", state._now().isoformat()))
    db.conn.commit()
    llm_calls["n"] = 0
    summary = pipeline.run(CFG, [src], [tg, st, ig], db, validator=noop_validator)
    print(f"ofertas na fila: {n_offers}")
    print(f"chamadas llm.ask_json neste run: {llm_calls['n']}  "
          f"(1 rank + 2 por oferta na copy quando o LLM devolve None)")
    print(f"chamadas resolve_affiliate_link (POST generateShortLink): {src.link_calls}")
    print(f"publicados: {len(summary.published)}  descartados: {len(summary.discarded)}")
    print(f"avisos: {summary.warnings}")
    db.close()

# ---------------------------------------------------------------------------
sep("2. CANAL FALHANDO (ex.: bot removido do canal) -> mesma varredura + resumo gigante")
with tempfile.TemporaryDirectory() as tmp:
    db = StateDB(Path(tmp) / "s.db")
    n_offers = 60
    src = FakeSource([make_offer(i) for i in range(n_offers)])
    tg = FakeChannel("telegram", max_per_day=100, fail=True)
    llm_calls["n"] = 0
    summary = pipeline.run(CFG, [src], [tg], db, validator=noop_validator)
    texto = summary.text()
    print(f"tentativas de publish no telegram: {tg.calls} (uma por oferta da fila)")
    print(f"chamadas llm.ask_json: {llm_calls['n']}")
    print(f"linhas descartadas: {len(summary.discarded)}")
    print(f"len(summary.text()) = {len(texto)} chars  (limite sendMessage do Telegram = 4096)")
    print("exemplo de linha:", repr(summary.discarded[0]))
    print("avisos:", summary.warnings)
    db.close()

llm.ask_json = orig_ask
state._now = _relogio_real

# ---------------------------------------------------------------------------
sep("3. send_text IGNORA a resposta da API: 400 'message is too long' vira silencio")
seen = {}


def handler(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    seen["len"] = len(body["text"])
    if len(body["text"]) > 4096:
        return httpx.Response(400, json={"ok": False, "error_code": 400,
                                         "description": "Bad Request: message is too long"})
    return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})


client = httpx.Client(transport=httpx.MockTransport(handler))
ret = send_text("123:ABC", "-100", "x" * 5000, client=client)
print(f"ultima parte enviada com {seen['len']} chars (5000 no total); send_text devolveu {ret!r} "
      "(antes: 5000 chars numa mensagem -> 400 'message is too long' -> None, sem log)")

# ---------------------------------------------------------------------------
sep("4. TELEGRAM 429: sem retry_after, 2 chamadas imediatas e falha")
calls = []


def h429(request: httpx.Request) -> httpx.Response:
    calls.append(request.url.path.rsplit("/", 1)[-1])
    return httpx.Response(429, json={"ok": False, "error_code": 429,
                                     "description": "Too Many Requests: retry after 35",
                                     "parameters": {"retry_after": 35}})


ch = TelegramChannel("123:ABC", "-100", client=httpx.Client(transport=httpx.MockTransport(h429)))
post_stub = type("P", (), {})()
post_stub.offer = make_offer(1)
post_stub.message_text = "oi"
res = ch.publish(post_stub)
print(f"chamadas feitas: {calls}  ok={res.ok} erro={res.error!r}")
print("-> nenhum sleep(retry_after); o pipeline marca 'descartado' e tenta a PROXIMA oferta "
      "na mesma janela de rate limit")

# ---------------------------------------------------------------------------
sep("5. AVISO A CADA RUN: watchlist vencida -> summary.warnings != [] -> send_text em TODO run")
with tempfile.TemporaryDirectory() as tmp:
    db = StateDB(Path(tmp) / "s.db")
    wl = Watchlist(generated_at=date(2026, 8, 23), valid_days=14)
    hoje = date(2026, 9, 8)
    print(f"watchlist gerada 2026-08-23, valid_days=14; em {hoje} is_stale={wl.is_stale(hoje)}")
    orig_ask = llm.ask_json
    llm.ask_json = lambda *a, **k: None
    envios = 0
    for run in range(3):  # 3 runs vazios (sem ofertas) com watchlist vencida
        wl_run = Watchlist(generated_at=date(2026, 8, 23), valid_days=14)
        # simula is_stale() no futuro
        Watchlist.is_stale = lambda self, today=None, _h=hoje: self.days_old(_h) > self.valid_days
        Watchlist.days_old = lambda self, today=None, _h=hoje: (_h - self.generated_at).days
        s = pipeline.run(CFG, [FakeSource([])], [], db, validator=noop_validator, watchlist=wl_run)
        houve_algo = bool(s.published or s.discarded or s.warnings)
        envios += houve_algo
        print(f"  run {run}: published={len(s.published)} discarded={len(s.discarded)} "
              f"warnings={s.warnings} -> houve_algo={houve_algo}")
    llm.ask_json = orig_ask
    print(f"envios ao chat de ops em 3 runs vazios: {envios}  (x192 runs/dia = 192 mensagens iguais)")
    db.close()

# ---------------------------------------------------------------------------
sep("6. DIA UTC x JANELA 08h-23h55 BRT: tetos de IG/story sao consumidos as 21h BRT")
with tempfile.TemporaryDirectory() as tmp:
    db = StateDB(Path(tmp) / "s.db")
    orig_now = state._now
    # 25/08 21:00 e 21:05 BRT == 26/08 00:00 e 00:05 UTC: dois posts no feed do IG
    for t in ("2026-08-26T00:00:30+00:00", "2026-08-26T00:05:30+00:00"):
        db.conn.execute("INSERT INTO posted VALUES (?,?,?,?,?,?,?)",
                        ("shopee", "i" + t, "instagram_feed", "t", 100, "", t))
    db.conn.commit()
    # manha seguinte, 26/08 08:00 BRT == 11:00 UTC
    state._now = lambda: datetime(2026, 8, 26, 11, 0, tzinfo=timezone.utc)
    n = db.count_posts_today("instagram_feed")
    print(f"posts IG feitos as 21:00/21:05 BRT de 25/08; as 08:00 BRT de 26/08 "
          f"count_posts_today('instagram_feed') = {n} (cap 2) -> IG bloqueado ate 21:00 BRT")
    # 26/08 20:59 BRT == 23:59 UTC ainda e' o mesmo dia UTC
    state._now = lambda: datetime(2026, 8, 26, 23, 59, tzinfo=timezone.utc)
    print(f"as 20:59 BRT de 26/08 ainda conta {db.count_posts_today('instagram_feed')}; "
          f"as 21:00 BRT zera -> os 2 feeds + 6 stories do 'dia' saem sempre entre 21:00 e ~21:30 BRT")
    state._now = orig_now
    db.close()

# ---------------------------------------------------------------------------
sep("7. --dry-run ESCREVE no state.db (price_log)")
with tempfile.TemporaryDirectory() as tmp:
    p = Path(tmp) / "s.db"
    db = StateDB(p)
    orig_ask = llm.ask_json
    llm.ask_json = lambda *a, **k: None
    pipeline.run(CFG, [FakeSource([make_offer(i) for i in range(5)])], [], db,
                 dry_run=True, validator=noop_validator)
    llm.ask_json = orig_ask
    rows = db.conn.execute("SELECT COUNT(*) FROM price_log").fetchone()[0]
    runs = db.conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    print(f"apos run --dry-run: price_log={rows} linhas, runs={runs}  -> arquivo binario alterado; "
          "no repo com state.db commitado pelo Actions isso vira conflito binario no pull")
    db.close()

# ---------------------------------------------------------------------------
sep("8. FALLBACK DA COPY e' publicavel e IDENTICO para todo post; nenhum aviso")
orig_ask = llm.ask_json
llm.ask_json = lambda *a, **k: None
c1 = copywriter.write_copy(make_offer(1), CFG)
c2 = copywriter.write_copy(make_offer(2), CFG)
llm.ask_json = orig_ask
from afiliado import validate
validate.check_copy(c1)
print(f"copy fallback (LLM fora): {c1}")
print(f"identica para outro produto: {c1 == c2}; passa em check_copy: sim; "
      "summary.warnings nao ganha nenhuma linha sobre isso")

# ---------------------------------------------------------------------------
sep("9. CANAL/FONTE ligado sem env: so print() no stdout, nada no resumo de ops")
import io, contextlib, os
cfg_ch = {**CFG, "channels": {"telegram": True, "instagram_feed": {"enabled": True, "max_per_day": 2}},
          "sources": {"shopee": True, "meli": True}, "shopee": {}}
for k in ("IG_USER_ID", "IG_ACCESS_TOKEN", "MELI_CLIENT_ID", "MELI_CLIENT_SECRET"):
    os.environ.pop(k, None)
os.environ["TELEGRAM_BOT_TOKEN"] = "1:x"; os.environ["TELEGRAM_CHANNEL_ID"] = "-1"
os.environ["SHOPEE_APP_ID"] = "a"; os.environ["SHOPEE_APP_SECRET"] = "b"
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    chans, avisos_ch = cli._build_channels(cfg_ch)
    srcs, avisos_src = cli._build_sources(cfg_ch)
print("stdout (journal):", buf.getvalue().strip().splitlines())
print("canais construidos:", [c.name for c in chans], "| fontes:", [s.name for s in srcs])
print("avisos devolvidos para o resumo de ops:", avisos_ch + avisos_src)
print("-> instagram_feed e meli somem; pipeline.run nao recebe nada que gere warning; "
      "resumo de ops = '✅ Run concluído' normal")

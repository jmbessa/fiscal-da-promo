import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from afiliado.models import Post

_SCHEMA = """
CREATE TABLE IF NOT EXISTS posted (
    source TEXT NOT NULL,
    item_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    title TEXT NOT NULL,
    price_cents INTEGER NOT NULL,
    message_id TEXT NOT NULL DEFAULT '',
    posted_at TEXT NOT NULL,
    PRIMARY KEY (source, item_id, channel)
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    finished_at TEXT NOT NULL,
    published INTEGER NOT NULL,
    discarded INTEGER NOT NULL,
    notes TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS price_log (
    source TEXT NOT NULL,
    item_id TEXT NOT NULL,
    day TEXT NOT NULL,
    price_cents INTEGER NOT NULL,
    PRIMARY KEY (source, item_id, day)
);
CREATE TABLE IF NOT EXISTS warned (
    key TEXT NOT NULL,
    day TEXT NOT NULL,
    PRIMARY KEY (key, day)
);
"""

DEFAULT_REF_WINDOW_DAYS = 90
# Fase 5A (C3): o "dia" do teto, do price_log e dos avisos é o dia LOCAL da
# operação, não o UTC — a fronteira UTC cai às 21:00 BRT e fazia o canal calar
# das 13:20 às 21:00 e furar o teto no dia 1. `posted_at`/`finished_at`
# continuam ISO UTC; só a CONTAGEM por dia muda de fuso.
DEFAULT_TIMEZONE = "America/Sao_Paulo"


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class DayStats:
    """Contagem de um dia local: ofertas publicadas (distintas em `posted`),
    descartes e runs (de `runs`). Alimenta o heartbeat da manhã."""
    published: int
    discarded: int
    runs: int


class StateDB:
    def __init__(self, path: str | Path, timezone: str = DEFAULT_TIMEZONE):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.tz = ZoneInfo(timezone)
        self.conn = sqlite3.connect(path)
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    # -- relógio local ------------------------------------------------------

    def local_now(self) -> datetime:
        return _now().astimezone(self.tz)

    def local_today(self) -> date:
        return self.local_now().date()

    def _day_bounds_utc(self, day: date) -> tuple[str, str]:
        """[início, fim) do dia local `day`, como ISO UTC — comparável com
        `posted_at`/`finished_at` por ordem de string."""
        inicio = datetime.combine(day, time.min, tzinfo=self.tz)
        fim = datetime.combine(day + timedelta(days=1), time.min, tzinfo=self.tz)
        return (inicio.astimezone(timezone.utc).isoformat(),
                fim.astimezone(timezone.utc).isoformat())

    # -- dedupe e teto --------------------------------------------------------

    def was_posted_recently(self, source: str, item_id: str, days: int) -> bool:
        cutoff = (_now() - timedelta(days=days)).isoformat()
        row = self.conn.execute(
            "SELECT 1 FROM posted WHERE source=? AND item_id=? AND posted_at>=? LIMIT 1",
            (source, item_id, cutoff),
        ).fetchone()
        return row is not None

    def recent_titles(self, days: int = 7, limit: int = 30) -> list[str]:
        cutoff = (_now() - timedelta(days=days)).isoformat()
        rows = self.conn.execute(
            "SELECT title FROM posted WHERE posted_at>=? ORDER BY posted_at DESC LIMIT ?",
            (cutoff, limit),
        ).fetchall()
        return [r[0] for r in rows]

    def count_posts_today(self, channel: str, now: datetime | None = None) -> int:
        """Posts gravados para o canal no dia LOCAL (fuso do construtor) que
        contém `now` (padrão: agora). `posted_at` é ISO UTC; o dia local é
        convertido para o intervalo UTC correspondente."""
        dia = (now or _now()).astimezone(self.tz).date()
        inicio, fim = self._day_bounds_utc(dia)
        row = self.conn.execute(
            "SELECT COUNT(*) FROM posted WHERE channel=? AND posted_at>=? AND posted_at<?",
            (channel, inicio, fim),
        ).fetchone()
        return row[0] if row else 0

    def record_post(self, post: Post, channel: str, message_id: str) -> None:
        o = post.offer
        self.conn.execute(
            "INSERT OR REPLACE INTO posted VALUES (?,?,?,?,?,?,?)",
            (o.source, o.item_id, channel, o.title, o.price_current_cents,
             message_id, _now().isoformat()),
        )
        self.conn.commit()

    # -- histórico próprio de preços (fase 4: régua honesta) ----------------

    def record_price(self, source: str, item_id: str, price_cents: int,
                     day: str | None = None) -> None:
        """Grava a observação de preço do dia. Em conflito no mesmo dia,
        mantém o MENOR preço — referência mais conservadora, menos desconto
        alegado depois. Ignora `price_cents <= 0`."""
        self.record_prices([(source, item_id, price_cents)], day=day)

    def record_prices(self, rows: list[tuple[str, str, int]], day: str | None = None) -> None:
        """Versão em lote de `record_price`: uma única transação para todas as
        observações do run (nada de commit por linha em laço). O dia é o
        LOCAL — em UTC, os 288 runs de um dia BRT viravam dois "dias"."""
        dia = day or self.local_today().isoformat()
        valores = [(source, item_id, dia, int(price_cents))
                   for source, item_id, price_cents in rows
                   if price_cents and int(price_cents) > 0]
        if not valores:
            return
        self.conn.executemany(
            "INSERT INTO price_log (source, item_id, day, price_cents) VALUES (?,?,?,?) "
            "ON CONFLICT(source,item_id,day) DO UPDATE SET "
            "price_cents=MIN(price_cents,excluded.price_cents)",
            valores,
        )
        self.conn.commit()

    def price_history(self, source: str, item_id: str, days: int) -> list[int]:
        """Preços dos últimos N dias locais (um por dia), mais recentes primeiro."""
        cutoff = (self.local_today() - timedelta(days=days)).isoformat()
        rows = self.conn.execute(
            "SELECT price_cents FROM price_log WHERE source=? AND item_id=? AND day>=? "
            "ORDER BY day DESC",
            (source, item_id, cutoff),
        ).fetchall()
        return [r[0] for r in rows]

    def prune_price_log(self, days: int) -> None:
        """Apaga observações anteriores ao corte da janela."""
        cutoff = (self.local_today() - timedelta(days=days)).isoformat()
        self.conn.execute("DELETE FROM price_log WHERE day<?", (cutoff,))
        self.conn.commit()

    # -- avisos uma vez por dia (fase 5A, A3) ---------------------------------

    def warn_once(self, key: str, day: str | None = None) -> bool:
        """True na PRIMEIRA vez que `key` é vista no dia local (e registra);
        False nas seguintes. Um estado persistente (watchlist vencida, pool
        vencido, teto) virava a mesma mensagem 192×/dia no chat de ops."""
        dia = day or self.local_today().isoformat()
        cur = self.conn.execute("INSERT OR IGNORE INTO warned (key, day) VALUES (?,?)",
                                (key, dia))
        self.conn.commit()
        return cur.rowcount == 1

    # -- runs e heartbeat ----------------------------------------------------

    def record_run(self, published: int, discarded: int, notes: str = "",
                   ref_window_days: int = DEFAULT_REF_WINDOW_DAYS) -> None:
        self.conn.execute(
            "INSERT INTO runs (finished_at, published, discarded, notes) VALUES (?,?,?,?)",
            (_now().isoformat(), published, discarded, notes),
        )
        self.conn.commit()
        self.prune_price_log(ref_window_days)

    def day_stats(self, day: date) -> DayStats:
        """Contagem de um dia local: ofertas distintas em `posted` (uma
        oferta em 3 canais conta 1), descartes e número de runs em `runs`."""
        inicio, fim = self._day_bounds_utc(day)
        publicados = self.conn.execute(
            "SELECT COUNT(DISTINCT source || '|' || item_id) FROM posted "
            "WHERE posted_at>=? AND posted_at<?", (inicio, fim)).fetchone()[0]
        runs, descartados = self.conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(discarded), 0) FROM runs "
            "WHERE finished_at>=? AND finished_at<?", (inicio, fim)).fetchone()
        return DayStats(int(publicados), int(descartados), int(runs))

    def close(self) -> None:
        self.conn.close()

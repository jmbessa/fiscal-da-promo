import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

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
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


class StateDB:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

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

    def count_posts_today(self, channel: str, today: date | None = None) -> int:
        """Posts gravados para o canal desde 00:00 UTC do dia (posted_at é ISO UTC).

        O dia é contado em UTC, não BRT — a fronteira de 3h entre os dois
        fusos é aceita (um post às 21h-23:59 BRT do dia D já conta para o dia
        D+1 em UTC)."""
        cutoff = f"{(today or date.today()).isoformat()}T00:00:00"
        row = self.conn.execute(
            "SELECT COUNT(*) FROM posted WHERE channel=? AND posted_at>=?",
            (channel, cutoff),
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

    def record_run(self, published: int, discarded: int, notes: str = "") -> None:
        self.conn.execute(
            "INSERT INTO runs (finished_at, published, discarded, notes) VALUES (?,?,?,?)",
            (_now().isoformat(), published, discarded, notes),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

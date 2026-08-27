import dataclasses
import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from afiliado.models import Offer, Post

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
CREATE TABLE IF NOT EXISTS candidates (
    source TEXT NOT NULL,
    item_id TEXT NOT NULL,
    seen_at TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (source, item_id)
);
CREATE TABLE IF NOT EXISTS discovery_cursor (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Campos que existem HOJE em Offer: um payload gravado por uma versão anterior
# (sem `price_p25_cents`, por exemplo) continua carregável, e um campo que
# sumiu do dataclass é ignorado em vez de derrubar o estoque inteiro.
_CAMPOS_DE_OFERTA = frozenset(f.name for f in dataclasses.fields(Offer))

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

    def recently_posted(self, days: int) -> set[tuple[str, str]]:
        """Todos os `(fonte, item)` publicados nos últimos N dias, de uma vez.

        `was_posted_recently` por oferta virou 5.000 idas ao SQLite por run
        quando o estoque de candidatas (fase 5C) passou a ter milhares de
        itens; o dedupe é o mesmo, a consulta é uma."""
        cutoff = (_now() - timedelta(days=days)).isoformat()
        return {(r[0], r[1]) for r in self.conn.execute(
            "SELECT DISTINCT source, item_id FROM posted WHERE posted_at>=?",
            (cutoff,)).fetchall()}

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

    def posted_today_by_source(self, now: datetime | None = None) -> dict[str, int]:
        """Ofertas DISTINTAS publicadas hoje (dia local) por fonte — a mesma
        oferta em 3 canais conta 1. Alimenta a cota por fonte (fase 5C, M2):
        a fila prefere quem ainda está abaixo da meta do dia."""
        dia = (now or _now()).astimezone(self.tz).date()
        inicio, fim = self._day_bounds_utc(dia)
        rows = self.conn.execute(
            "SELECT source, COUNT(DISTINCT item_id) FROM posted "
            "WHERE posted_at>=? AND posted_at<? GROUP BY source",
            (inicio, fim),
        ).fetchall()
        return {r[0]: int(r[1]) for r in rows}

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
        LOCAL — em UTC, os 192 runs de um dia BRT viravam dois "dias"."""
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

    def price_histories(self, source: str, item_ids: list[str],
                        days: int) -> dict[str, list[int]]:
        """`price_history` de vários itens de uma vez (mais recentes primeiro).

        Mesma razão de `recently_posted`: com o estoque de candidatas, uma
        consulta por oferta eram milhares por run. Os IDs vão em lotes de 500
        para não esbarrar no teto de parâmetros do SQLite."""
        cutoff = (self.local_today() - timedelta(days=days)).isoformat()
        historicos: dict[str, list[int]] = {}
        unicos = list(dict.fromkeys(item_ids))
        for i in range(0, len(unicos), 500):
            lote = unicos[i:i + 500]
            marcadores = ",".join("?" * len(lote))
            for item_id, price in self.conn.execute(
                    f"SELECT item_id, price_cents FROM price_log WHERE source=? "
                    f"AND day>=? AND item_id IN ({marcadores}) ORDER BY day DESC",
                    (source, cutoff, *lote)).fetchall():
                historicos.setdefault(item_id, []).append(price)
        return historicos

    def prune_price_log(self, days: int) -> None:
        """Apaga observações anteriores ao corte da janela."""
        cutoff = (self.local_today() - timedelta(days=days)).isoformat()
        self.conn.execute("DELETE FROM price_log WHERE day<?", (cutoff,))
        self.conn.commit()

    # -- estoque de candidatas (fase 5C, C1) ---------------------------------

    def upsert_candidates(self, offers: list[Offer]) -> int:
        """Grava as ofertas no estoque, uma linha por (fonte, item), com a
        `Offer` inteira serializada em JSON. Devolve quantas eram INÉDITAS.

        A descoberta deixou de ser refeita por run: cada run lê uma fatia do
        espaço da API e o estoque acumula o resto (C1 — 2 páginas relidas a
        cada 5 min davam 244 itens únicos/mês, 8/dia a dedupe 30). Reencontrar
        um item atualiza `seen_at` e o payload: o preço mais novo vence."""
        if not offers:
            return 0
        fontes = sorted({o.source for o in offers})
        marcadores = ",".join("?" * len(fontes))
        existentes = {
            (r[0], r[1]) for r in self.conn.execute(
                f"SELECT source, item_id FROM candidates WHERE source IN ({marcadores})",
                tuple(fontes)).fetchall()}
        novos = len({(o.source, o.item_id) for o in offers} - existentes)
        agora = _now().isoformat()
        self.conn.executemany(
            "INSERT INTO candidates (source, item_id, seen_at, payload) VALUES (?,?,?,?) "
            "ON CONFLICT(source,item_id) DO UPDATE SET "
            "seen_at=excluded.seen_at, payload=excluded.payload",
            [(o.source, o.item_id, agora, _serializa_oferta(o)) for o in offers],
        )
        self.conn.commit()
        return novos

    def load_candidates(self, source: str, max_age_days: int) -> list[Offer]:
        """Candidatas da fonte vistas nos últimos N dias, mais recentes
        primeiro. Payload ilegível é PULADO (nunca derruba o run)."""
        cutoff = (_now() - timedelta(days=max_age_days)).isoformat()
        rows = self.conn.execute(
            "SELECT payload FROM candidates WHERE source=? AND seen_at>=? "
            "ORDER BY seen_at DESC",
            (source, cutoff),
        ).fetchall()
        ofertas = (_oferta_de_payload(r[0]) for r in rows)
        return [o for o in ofertas if o is not None]

    def prune_candidates(self, max_age_days: int, source: str | None = None) -> None:
        """Apaga candidatas mais velhas que a janela (de uma fonte, ou de todas)."""
        cutoff = (_now() - timedelta(days=max_age_days)).isoformat()
        if source is None:
            self.conn.execute("DELETE FROM candidates WHERE seen_at<?", (cutoff,))
        else:
            self.conn.execute("DELETE FROM candidates WHERE source=? AND seen_at<?",
                              (source, cutoff))
        self.conn.commit()

    # -- cursor da varredura rotativa (fase 5C, M1) ---------------------------

    def get_cursor(self, key: str, default: str = "") -> str:
        """Valor do cursor de descoberta (qual fatia do espaço vem agora)."""
        row = self.conn.execute(
            "SELECT value FROM discovery_cursor WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_cursor(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO discovery_cursor (key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)))
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

    def prune_warned(self) -> None:
        """Só o dia local de hoje interessa ao `warn_once`; os anteriores
        saem (a tabela nunca era podada — revisão da 5A)."""
        self.conn.execute("DELETE FROM warned WHERE day<?", (self.local_today().isoformat(),))
        self.conn.commit()

    # -- runs e heartbeat ----------------------------------------------------

    def record_run(self, published: int, discarded: int, notes: str = "",
                   ref_window_days: int = DEFAULT_REF_WINDOW_DAYS) -> None:
        self.conn.execute(
            "INSERT INTO runs (finished_at, published, discarded, notes) VALUES (?,?,?,?)",
            (_now().isoformat(), published, discarded, notes),
        )
        self.conn.commit()
        self.prune_price_log(ref_window_days)
        self.prune_warned()

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


def _serializa_oferta(offer: Offer) -> str:
    return json.dumps(dataclasses.asdict(offer), ensure_ascii=False)


def _oferta_de_payload(payload: str) -> Offer | None:
    """`Offer` de volta do JSON, ou None quando a linha não é aproveitável —
    JSON quebrado, payload que não é objeto, campo obrigatório que sumiu."""
    try:
        data = json.loads(payload)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        return Offer(**{k: v for k, v in data.items() if k in _CAMPOS_DE_OFERTA})
    except TypeError:
        return None

import dataclasses
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from afiliado.models import Offer

# As seções de OPINIÃO — o que a semana achou. São elas que vencem
# (`is_stale`) e as únicas que `facts_only` descarta; `price_refs` e
# `price_floors` são FATOS datados e sobrevivem ao vencimento (C11).
SECOES_DE_OPINIAO = ("category_boosts", "hot_items")


def _data(valor) -> date | None:
    """`date` a partir de uma string ISO; None para ausente/inválido.

    Data inválida NÃO derruba o arquivo nem inventa um dia: a seção (ou a
    entrada) simplesmente volta a herdar a data de quem está acima dela."""
    if not isinstance(valor, str):
        return None
    try:
        return date.fromisoformat(valor)
    except ValueError:
        return None


@dataclass(frozen=True)
class PriceFloor:
    """Mínima curada com a janela que a mediu. `window_days` ausente no
    arquivo carrega 0 (não 365): o selo diz "últimos N dias" e não pode
    inventar N — com 0, `pricing.verdict` não emite selo nenhum.

    `measured_at` é o dia em que ESTA entrada foi medida (fase 5O). None
    significa "herda a data da seção" — a seção é semeada em ondas, e uma
    data só por seção envelheceria junto entradas medidas em dias
    diferentes."""
    min_price_cents: int
    window_days: int
    measured_at: date | None = None


@dataclass(frozen=True)
class PriceRef:
    """Referência curada: mediana (`ref_cents`), topo do quartil mais barato
    (`p25_cents`) e a janela real em dias. Entrada sem p25 carrega 0 — e
    sem p25 o post nunca alega desconto (conservador por construção). Idem
    para `window_days`: ausente vira 0 (não 90) e a regra do quartil, que
    exige >= 14 dias MEDIDOS, nunca dispara por um default silencioso.

    `measured_at`: ver `PriceFloor`."""
    ref_cents: int
    window_days: int
    p25_cents: int = 0
    measured_at: date | None = None


@dataclass(frozen=True)
class Watchlist:
    generated_at: date
    valid_days: int
    category_boosts: dict[str, float] = field(default_factory=dict)
    hot_items: dict[str, float] = field(default_factory=dict)      # item_id -> boost
    price_floors: dict[str, PriceFloor] = field(default_factory=dict)
    price_refs: dict[str, PriceRef] = field(default_factory=dict)
    section_dates: dict[str, date] = field(default_factory=dict)

    def section_date(self, secao: str) -> date:
        """A data da seção — `generated_at` quando ela não tem a sua.

        Fase 5O: o arquivo tinha UMA data e proveniência misturada. Semear a
        régua da Shopee não revisa os `hot_items`, e regravar `generated_at`
        afirmaria que sim. Cada seção passa a poder dizer o dia em que foi
        feita; arquivo sem `section_dates` (o formato antigo, o que está em
        produção) se comporta exatamente como antes."""
        return self.section_dates.get(secao, self.generated_at)

    def days_old(self, today: date | None = None) -> int:
        """A idade da OPINIÃO: a seção de boost mais velha.

        É o número que vira "Watchlist vencida há N dias" e o que decide
        `is_stale` — e por isso não pode ser a data de `price_refs`. Se
        fosse, semear a régua (que não olha para os boosts) renovaria a
        validade dos boosts em silêncio, que é justamente o problema que a
        data por seção existe para não ter."""
        mais_velha = min(self.section_date(s) for s in SECOES_DE_OPINIAO)
        return ((today or date.today()) - mais_velha).days

    def is_stale(self, today: date | None = None) -> bool:
        return self.days_old(today) > self.valid_days

    def facts_only(self) -> "Watchlist":
        """Cópia sem `category_boosts`/`hot_items`. Watchlist vencida perde só
        os boosts (opinião da semana); referências e pisos são FATOS datados
        e continuam alimentando a régua com a janela real (C11: antes a
        watchlist inteira virava None e a régua trocava de número — e de
        veredito — de um dia para o outro, sem trocar de aviso)."""
        return dataclasses.replace(self, category_boosts={}, hot_items={})

    def boost_for(self, offer: Offer) -> float:
        return (self.category_boosts.get(offer.category, 1.0)
                * self.hot_items.get(offer.item_id, 1.0))

    def price_floor(self, item_id: str) -> PriceFloor | None:
        return self.price_floors.get(item_id)

    def price_ref(self, item_id: str) -> PriceRef | None:
        return self.price_refs.get(item_id)


def load_watchlist(path: str | Path) -> Watchlist | None:
    """None se o arquivo não existe ou é inválido — o pipeline segue sem watchlist.

    Seções com formato inesperado (ex.: `hot_items` sendo uma string ou lista em
    vez de um objeto) degradam para vazio individualmente — o restante do
    arquivo, se válido, continua utilizável. Só retorna None quando nem isso é
    possível (arquivo ausente, JSON inválido, ou faltando `generated_at`).
    """
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        raw_category_boosts = raw.get("category_boosts")
        raw_category_boosts = raw_category_boosts if isinstance(raw_category_boosts, dict) else {}
        raw_hot_items = raw.get("hot_items")
        raw_hot_items = raw_hot_items if isinstance(raw_hot_items, dict) else {}
        raw_price_floors = raw.get("price_floors")
        raw_price_floors = raw_price_floors if isinstance(raw_price_floors, dict) else {}
        raw_price_refs = raw.get("price_refs")
        raw_price_refs = raw_price_refs if isinstance(raw_price_refs, dict) else {}
        raw_section_dates = raw.get("section_dates")
        raw_section_dates = raw_section_dates if isinstance(raw_section_dates, dict) else {}
        return Watchlist(
            generated_at=date.fromisoformat(raw["generated_at"]),
            valid_days=int(raw.get("valid_days", 14)),
            category_boosts={str(k): float(v) for k, v in raw_category_boosts.items()},
            hot_items={str(k): float(v.get("boost", 1.0)) if isinstance(v, dict) else float(v)
                       for k, v in raw_hot_items.items()},
            price_floors={str(k): PriceFloor(int(v["min_price_cents"]),
                                             int(v.get("window_days") or 0),
                                             _data(v.get("measured_at")))
                          for k, v in raw_price_floors.items()
                          if isinstance(v, dict) and "min_price_cents" in v},
            price_refs={str(k): PriceRef(int(v["ref_cents"]), int(v.get("window_days") or 0),
                                         int(v.get("p25_cents") or 0),
                                         _data(v.get("measured_at")))
                        for k, v in raw_price_refs.items()
                        if isinstance(v, dict) and "ref_cents" in v},
            section_dates={str(k): _data(v) for k, v in raw_section_dates.items()
                           if _data(v) is not None},
        )
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None

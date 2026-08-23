# Fase 1 — Shopee → Telegram: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pipeline Python totalmente automático que busca ofertas na Shopee Affiliate API, seleciona as melhores (regras + LLM), escreve copy com LLM, valida e publica no canal do Telegram, com estado em SQLite e agendamento via GitHub Actions.

**Architecture:** Pipeline determinístico batch (`afiliado run`) com LLM em exatamente dois pontos (ranqueamento e copy) via Claude Code headless. Fontes e canais são plugins atrás de interfaces (`sources/base.py`, `channels/base.py`). Preço e link nunca vêm do LLM — são injetados dos dados da API no template.

**Tech Stack:** Python 3.12+, httpx, PyYAML, sqlite3 (stdlib), pytest, Claude Code CLI (`claude -p`), Telegram Bot API, Shopee Affiliate Open API (GraphQL), GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-08-23-afiliado-design.md`

## Global Constraints

- Python >= 3.12; dependências runtime apenas `httpx>=0.27` e `PyYAML>=6.0`; dev: `pytest>=8.0`.
- O LLM nunca escreve preço nem link: valores monetários e URLs entram no template a partir de dados estruturados (spec §4.1).
- Preços armazenados em **centavos (int)**; conversão de string decimal via `decimal.Decimal`, nunca float.
- Copy em pt-BR; limites: headline ≤ 60 chars, description ≤ 120, cta ≤ 40; nenhum campo de copy pode conter "http".
- Segredos só via variáveis de ambiente: `SHOPEE_APP_ID`, `SHOPEE_APP_SECRET`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHANNEL_ID`, `TELEGRAM_OPS_CHAT_ID`, `CLAUDE_CODE_OAUTH_TOKEN`.
- `data/state.db` é versionado no repo (commitado de volta pelo Actions); nunca no `.gitignore`.
- Testes unitários não tocam a rede: HTTP via `httpx.MockTransport`, LLM via monkeypatch de `llm.ask_json`, subprocess via monkeypatch.
- Todos os arquivos em UTF-8 (repo roda em Windows local e Linux no CI).
- Mensagens de commit: convencional (`feat:`, `test:`, `chore:`, `docs:`), terminando com `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: Scaffolding do projeto + carregador de config

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `config.yaml`, `data/.gitkeep`, `src/afiliado/__init__.py`, `src/afiliado/config.py`, `src/afiliado/errors.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nada (primeira task)
- Produces: `afiliado.config.load_config(path) -> dict`; exceções `afiliado.errors.SourceError`, `afiliado.errors.ValidationError`; `config.yaml` com as chaves usadas por todas as tasks seguintes

- [ ] **Step 1: Criar arquivos de projeto**

`pyproject.toml`:

```toml
[project]
name = "afiliado"
version = "0.1.0"
description = "Pipeline automático de divulgação de ofertas com link de afiliado"
requires-python = ">=3.12"
dependencies = ["httpx>=0.27", "PyYAML>=6.0"]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[project.scripts]
afiliado = "afiliado.cli:main"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

`.gitignore`:

```
__pycache__/
*.egg-info/
.venv/
.pytest_cache/
.env
```

`config.yaml`:

```yaml
state:
  path: data/state.db

llm:
  model: haiku

selection:
  posts_per_run: 3
  min_discount_pct: 20
  price_min_brl: 20
  price_max_brl: 1000
  dedupe_days: 30
  category_ids: []        # vazio = todas as categorias

shopee:
  sort_type: 5            # valor a confirmar na doc da API (Task 14, doctor)
  list_type: 0
  pages: 2
  page_size: 50

validation:
  allowed_domains: ["shopee.com.br", "shope.ee"]

copy:
  tone: "empolgado, direto, sem exageros enganosos, pt-BR"
```

`src/afiliado/__init__.py`: vazio. `data/.gitkeep`: vazio.

`src/afiliado/errors.py`:

```python
class SourceError(Exception):
    """Falha ao consultar uma fonte de ofertas ou gerar link de afiliado."""


class ValidationError(Exception):
    """Post reprovado em um portão de validação pré-publicação."""
```

- [ ] **Step 2: Instalar em modo editável**

Run: `pip install -e .[dev]`
Expected: instala afiliado, httpx, PyYAML, pytest sem erro.

- [ ] **Step 3: Escrever teste que falha**

`tests/test_config.py`:

```python
import pytest

from afiliado.config import load_config


def test_load_config_reads_project_yaml():
    cfg = load_config("config.yaml")
    assert cfg["selection"]["posts_per_run"] == 3
    assert cfg["llm"]["model"] == "haiku"


def test_load_config_rejects_missing_keys(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("llm:\n  model: haiku\n", encoding="utf-8")
    with pytest.raises(ValueError, match="obrigat"):
        load_config(p)
```

- [ ] **Step 4: Rodar e ver falhar**

Run: `pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError`/`ImportError` (config.py ainda não existe).

- [ ] **Step 5: Implementar `src/afiliado/config.py`**

```python
from pathlib import Path

import yaml

REQUIRED_TOP_KEYS = ("state", "llm", "selection", "shopee", "validation", "copy")


def load_config(path: str | Path = "config.yaml") -> dict:
    cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    missing = [k for k in REQUIRED_TOP_KEYS if k not in cfg]
    if missing:
        raise ValueError(f"config sem chaves obrigatórias: {missing}")
    return cfg
```

- [ ] **Step 6: Rodar e ver passar**

Run: `pytest tests/test_config.py -v`
Expected: 2 passed.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml .gitignore config.yaml data/.gitkeep src tests
git commit -m "feat: scaffolding do projeto e carregador de config"
```

---

### Task 2: Modelos de dados (`models.py`)

**Files:**
- Create: `src/afiliado/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: nada
- Produces:
  - `Offer(source: str, item_id: str, title: str, price_original_cents: int, price_current_cents: int, commission_pct: float, image_url: str, product_url: str, offer_link: str = "", category: str = "", sales: int = 0)` — frozen dataclass; propriedade `discount_pct -> int`
  - `CopyParts(headline: str, description: str, cta: str)` — frozen dataclass
  - `Post(offer: Offer, copy: CopyParts, affiliate_link: str, message_text: str = "")` — dataclass
  - `format_brl(cents: int) -> str` (ex.: `24999 -> "R$ 249,99"`)

- [ ] **Step 1: Escrever testes que falham**

`tests/test_models.py`:

```python
from afiliado.models import CopyParts, Offer, Post, format_brl


def make_offer(**kw) -> Offer:
    base = dict(
        source="shopee",
        item_id="123456",
        title="Tênis Nike SB",
        price_original_cents=49999,
        price_current_cents=24999,
        commission_pct=12.0,
        image_url="https://cf.shopee.com.br/file/abc.jpg",
        product_url="https://shopee.com.br/product/1/123456",
    )
    base.update(kw)
    return Offer(**base)


def test_format_brl():
    assert format_brl(24999) == "R$ 249,99"
    assert format_brl(1234567) == "R$ 12.345,67"
    assert format_brl(500) == "R$ 5,00"


def test_discount_pct():
    assert make_offer().discount_pct == 50
    assert make_offer(price_original_cents=0).discount_pct == 0


def test_post_holds_parts():
    post = Post(
        offer=make_offer(),
        copy=CopyParts(headline="h", description="d", cta="c"),
        affiliate_link="https://shope.ee/x",
    )
    assert post.message_text == ""
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `pytest tests/test_models.py -v`
Expected: FAIL — ImportError.

- [ ] **Step 3: Implementar `src/afiliado/models.py`**

```python
from dataclasses import dataclass, field


def format_brl(cents: int) -> str:
    reais, centavos = divmod(cents, 100)
    return f"R$ {reais:,}".replace(",", ".") + f",{centavos:02d}"


@dataclass(frozen=True)
class Offer:
    source: str
    item_id: str
    title: str
    price_original_cents: int
    price_current_cents: int
    commission_pct: float
    image_url: str
    product_url: str
    offer_link: str = ""
    category: str = ""
    sales: int = 0

    @property
    def discount_pct(self) -> int:
        if self.price_original_cents <= 0:
            return 0
        return round((1 - self.price_current_cents / self.price_original_cents) * 100)


@dataclass(frozen=True)
class CopyParts:
    headline: str
    description: str
    cta: str


@dataclass
class Post:
    offer: Offer
    copy: CopyParts
    affiliate_link: str
    message_text: str = ""
```

- [ ] **Step 4: Rodar e ver passar**

Run: `pytest tests/test_models.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/afiliado/models.py tests/test_models.py
git commit -m "feat: modelos Offer, CopyParts, Post e format_brl"
```

---

### Task 3: Estado em SQLite (`state.py`)

**Files:**
- Create: `src/afiliado/state.py`
- Test: `tests/test_state.py`

**Interfaces:**
- Consumes: `Post` de `afiliado.models`
- Produces: classe `StateDB`:
  - `__init__(self, path: str | Path)` — cria diretório pai e schema se não existirem
  - `was_posted_recently(self, source: str, item_id: str, days: int) -> bool`
  - `recent_titles(self, days: int = 7, limit: int = 30) -> list[str]`
  - `record_post(self, post: Post, channel: str, message_id: str) -> None`
  - `record_run(self, published: int, discarded: int, notes: str = "") -> None`
  - `close(self) -> None`

- [ ] **Step 1: Escrever testes que falham**

`tests/test_state.py`:

```python
from afiliado.models import CopyParts, Post
from afiliado.state import StateDB
from tests.test_models import make_offer


def make_post(**offer_kw) -> Post:
    return Post(
        offer=make_offer(**offer_kw),
        copy=CopyParts(headline="h", description="d", cta="c"),
        affiliate_link="https://shope.ee/x",
        message_text="msg",
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
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `pytest tests/test_state.py -v`
Expected: FAIL — ImportError.

- [ ] **Step 3: Implementar `src/afiliado/state.py`**

```python
import sqlite3
from datetime import datetime, timedelta, timezone
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
```

- [ ] **Step 4: Rodar e ver passar**

Run: `pytest tests/test_state.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/afiliado/state.py tests/test_state.py
git commit -m "feat: StateDB com dedupe e histórico de runs em SQLite"
```

---

### Task 4: Wrapper do LLM (`llm.py`)

**Files:**
- Create: `src/afiliado/llm.py`
- Test: `tests/test_llm.py`

**Interfaces:**
- Consumes: CLI `claude` no PATH (Claude Code); autenticação via ambiente (`CLAUDE_CODE_OAUTH_TOKEN` no CI ou login local; `ANTHROPIC_API_KEY` também funciona — o próprio CLI resolve)
- Produces:
  - `ask_json(prompt: str, model: str = "haiku", timeout: int = 120) -> dict | list | None` — None em qualquer falha (CLI ausente, timeout, exit != 0, JSON inválido)
  - `parse_json_block(text: str) -> dict | list | None` — extrai o primeiro bloco `{...}`/`[...]` do texto, tolerando cercas markdown e prosa ao redor

- [ ] **Step 1: Escrever testes que falham**

`tests/test_llm.py`:

```python
import subprocess

from afiliado import llm


def _fake_run(stdout: str, returncode: int = 0):
    def fake(*args, **kwargs):
        return subprocess.CompletedProcess(args=args, returncode=returncode,
                                           stdout=stdout, stderr="")
    return fake


def test_parse_json_block_with_fences():
    assert llm.parse_json_block('Claro!\n```json\n{"a": 1}\n```\n') == {"a": 1}


def test_parse_json_block_invalid():
    assert llm.parse_json_block("sem json aqui") is None
    assert llm.parse_json_block("{quebrado") is None


def test_ask_json_success(monkeypatch):
    monkeypatch.setattr(llm.shutil, "which", lambda _: "claude")
    monkeypatch.setattr(llm.subprocess, "run", _fake_run('{"chosen": ["1"]}'))
    assert llm.ask_json("x") == {"chosen": ["1"]}


def test_ask_json_cli_failure_returns_none(monkeypatch):
    monkeypatch.setattr(llm.shutil, "which", lambda _: "claude")
    monkeypatch.setattr(llm.subprocess, "run", _fake_run("erro", returncode=1))
    assert llm.ask_json("x") is None


def test_ask_json_cli_missing_returns_none(monkeypatch):
    monkeypatch.setattr(llm.shutil, "which", lambda _: None)
    assert llm.ask_json("x") is None
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `pytest tests/test_llm.py -v`
Expected: FAIL — ImportError.

- [ ] **Step 3: Implementar `src/afiliado/llm.py`**

```python
import json
import re
import shutil
import subprocess

_JSON_RE = re.compile(r"\{.*\}|\[.*\]", re.DOTALL)


def parse_json_block(text: str):
    m = _JSON_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def ask_json(prompt: str, model: str = "haiku", timeout: int = 120):
    exe = shutil.which("claude")
    if not exe:
        return None
    try:
        proc = subprocess.run(
            [exe, "-p", prompt, "--model", model, "--output-format", "text"],
            capture_output=True, text=True, encoding="utf-8", timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    return parse_json_block(proc.stdout or "")
```

- [ ] **Step 4: Rodar e ver passar**

Run: `pytest tests/test_llm.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/afiliado/llm.py tests/test_llm.py
git commit -m "feat: wrapper claude -p com parse tolerante de JSON"
```

---

### Task 5: Portões de validação (`validate.py`)

**Files:**
- Create: `src/afiliado/validate.py`
- Test: `tests/test_validate.py`

**Interfaces:**
- Consumes: `Offer`, `CopyParts`, `Post` de `models`; `ValidationError` de `errors`; cfg dict
- Produces:
  - `check_link(url: str, cfg: dict, client: httpx.Client | None = None) -> None` — segue redirects; passa se o host final termina com um dos `cfg["validation"]["allowed_domains"]` E status < 500 (403 anti-bot da Shopee ainda prova que a cadeia de redirect chegou ao domínio certo); senão levanta `ValidationError`
  - `check_price(offer: Offer, cfg: dict) -> None` — exige `atual < original`, `discount_pct >= min_discount_pct`, preço atual dentro de `[price_min_brl, price_max_brl]`
  - `check_image(url: str, client: httpx.Client | None = None) -> None` — content-type `image/*` e corpo >= 5000 bytes
  - `check_copy(copy: CopyParts) -> None` — campos não vazios, limites 60/120/40, sem "http"
  - `validate_post(post: Post, cfg: dict, client: httpx.Client | None = None) -> None` — chama os quatro na ordem copy → price → link → image (baratos primeiro)

- [ ] **Step 1: Escrever testes que falham**

`tests/test_validate.py`:

```python
import httpx
import pytest

from afiliado import validate
from afiliado.errors import ValidationError
from afiliado.models import CopyParts
from tests.test_models import make_offer

CFG = {
    "selection": {"min_discount_pct": 20, "price_min_brl": 20, "price_max_brl": 1000},
    "validation": {"allowed_domains": ["shopee.com.br", "shope.ee"]},
}


def client_for(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_check_link_accepts_redirect_to_allowed_domain():
    def handler(request):
        if request.url.host == "shope.ee":
            return httpx.Response(302, headers={"location": "https://shopee.com.br/p/1"})
        return httpx.Response(200, text="ok")
    validate.check_link("https://shope.ee/x", CFG, client=client_for(handler))


def test_check_link_rejects_wrong_domain():
    def handler(request):
        return httpx.Response(200, text="ok")
    with pytest.raises(ValidationError):
        validate.check_link("https://evil.com/x", CFG, client=client_for(handler))


def test_check_price_rules():
    validate.check_price(make_offer(), CFG)
    with pytest.raises(ValidationError):  # sem desconto real
        validate.check_price(make_offer(price_original_cents=24999), CFG)
    with pytest.raises(ValidationError):  # desconto abaixo do mínimo
        validate.check_price(make_offer(price_original_cents=26000), CFG)
    with pytest.raises(ValidationError):  # acima da faixa
        validate.check_price(
            make_offer(price_current_cents=150_000, price_original_cents=300_000), CFG)


def test_check_image():
    def handler(request):
        return httpx.Response(200, headers={"content-type": "image/jpeg"},
                              content=b"x" * 6000)
    validate.check_image("https://cf.shopee.com.br/file/a.jpg", client=client_for(handler))

    def bad(request):
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"x")
    with pytest.raises(ValidationError):
        validate.check_image("https://cf.shopee.com.br/file/a.jpg", client=client_for(bad))


def test_check_copy():
    validate.check_copy(CopyParts("🔥 50% OFF", "Bom e barato.", "Corre 👇"))
    with pytest.raises(ValidationError):  # vazio
        validate.check_copy(CopyParts("", "d", "c"))
    with pytest.raises(ValidationError):  # URL dentro do texto
        validate.check_copy(CopyParts("veja http://x.com", "d", "c"))
    with pytest.raises(ValidationError):  # headline longa demais
        validate.check_copy(CopyParts("a" * 61, "d", "c"))
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `pytest tests/test_validate.py -v`
Expected: FAIL — ImportError.

- [ ] **Step 3: Implementar `src/afiliado/validate.py`**

```python
import httpx

from afiliado.errors import ValidationError
from afiliado.models import CopyParts, Offer, Post

MAX_HEADLINE = 60
MAX_DESCRIPTION = 120
MAX_CTA = 40
MIN_IMAGE_BYTES = 5000
_UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}


def _client(client: httpx.Client | None) -> httpx.Client:
    return client or httpx.Client(timeout=20, follow_redirects=True, headers=_UA)


def check_link(url: str, cfg: dict, client: httpx.Client | None = None) -> None:
    allowed = tuple(cfg["validation"]["allowed_domains"])
    try:
        r = _client(client).get(url)
    except httpx.HTTPError as exc:
        raise ValidationError(f"link inacessível: {exc}") from exc
    host = r.url.host or ""
    if not host.endswith(allowed):
        raise ValidationError(f"link resolve para domínio inesperado: {host}")
    if r.status_code >= 500:
        raise ValidationError(f"link retornou status {r.status_code}")


def check_price(offer: Offer, cfg: dict) -> None:
    sel = cfg["selection"]
    if offer.price_current_cents >= offer.price_original_cents:
        raise ValidationError("sem desconto real (atual >= original)")
    if offer.discount_pct < sel["min_discount_pct"]:
        raise ValidationError(f"desconto {offer.discount_pct}% abaixo do mínimo")
    preco_brl = offer.price_current_cents / 100
    if not sel["price_min_brl"] <= preco_brl <= sel["price_max_brl"]:
        raise ValidationError(f"preço R${preco_brl:.2f} fora da faixa")


def check_image(url: str, client: httpx.Client | None = None) -> None:
    try:
        r = _client(client).get(url)
    except httpx.HTTPError as exc:
        raise ValidationError(f"imagem inacessível: {exc}") from exc
    ctype = r.headers.get("content-type", "")
    if r.status_code != 200 or not ctype.startswith("image/"):
        raise ValidationError(f"imagem inválida: status={r.status_code} type={ctype}")
    if len(r.content) < MIN_IMAGE_BYTES:
        raise ValidationError("imagem pequena demais (possivelmente quebrada)")


def check_copy(copy: CopyParts) -> None:
    campos = {"headline": (copy.headline, MAX_HEADLINE),
              "description": (copy.description, MAX_DESCRIPTION),
              "cta": (copy.cta, MAX_CTA)}
    for nome, (valor, limite) in campos.items():
        if not valor.strip():
            raise ValidationError(f"copy.{nome} vazio")
        if len(valor) > limite:
            raise ValidationError(f"copy.{nome} excede {limite} chars")
        if "http" in valor.lower():
            raise ValidationError(f"copy.{nome} contém URL")


def validate_post(post: Post, cfg: dict, client: httpx.Client | None = None) -> None:
    check_copy(post.copy)
    check_price(post.offer, cfg)
    check_link(post.affiliate_link, cfg, client=client)
    check_image(post.offer.image_url, client=client)
```

- [ ] **Step 4: Rodar e ver passar**

Run: `pytest tests/test_validate.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/afiliado/validate.py tests/test_validate.py
git commit -m "feat: portões de validação de link, preço, imagem e copy"
```

---

### Task 6: Fonte Shopee (`sources/base.py`, `sources/shopee.py`)

**Files:**
- Create: `src/afiliado/sources/__init__.py`, `src/afiliado/sources/base.py`, `src/afiliado/sources/shopee.py`
- Test: `tests/test_shopee.py`, `tests/fixtures/shopee_product_offer.json`

**Interfaces:**
- Consumes: `Offer` de `models`, `SourceError` de `errors`
- Produces:
  - Protocolo `Source` (`base.py`): atributo `name: str`; `fetch_offers(cfg: dict) -> list[Offer]`; `resolve_affiliate_link(offer: Offer) -> str` (levanta `SourceError`)
  - `ShopeeSource(app_id: str, app_secret: str, client: httpx.Client | None = None)` implementando o protocolo, `name = "shopee"`

- [ ] **Step 1: Criar fixture**

`tests/fixtures/shopee_product_offer.json` (formato documentado da Shopee Affiliate Open API; a Task 14 confere contra a resposta real e ajusta o parse se necessário):

```json
{
  "data": {
    "productOfferV2": {
      "nodes": [
        {
          "itemId": 123456,
          "productName": "Tênis Nike SB Chron 2",
          "price": "249.99",
          "priceDiscountRate": 50,
          "commissionRate": "0.12",
          "sales": 1500,
          "imageUrl": "https://cf.shopee.com.br/file/abc.jpg",
          "productLink": "https://shopee.com.br/product/1/123456",
          "offerLink": "https://s.shopee.com.br/xyz",
          "productCatIds": [100636]
        },
        {
          "itemId": 777,
          "productName": "Sem preço (deve ser ignorado)",
          "price": null,
          "priceDiscountRate": 10,
          "commissionRate": "0.05",
          "sales": 3,
          "imageUrl": "https://cf.shopee.com.br/file/def.jpg",
          "productLink": "https://shopee.com.br/product/1/777",
          "offerLink": "",
          "productCatIds": []
        }
      ]
    }
  }
}
```

- [ ] **Step 2: Escrever testes que falham**

`tests/test_shopee.py`:

```python
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from afiliado.errors import SourceError
from afiliado.sources.shopee import ShopeeSource

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "shopee_product_offer.json")
    .read_text(encoding="utf-8"))

CFG = {"shopee": {"sort_type": 5, "list_type": 0, "pages": 1, "page_size": 50}}


def source_with(handler) -> ShopeeSource:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return ShopeeSource("APPID", "SECRET", client=client)


def test_signature_header_matches_formula():
    captured = {}

    def handler(request):
        captured["auth"] = request.headers["authorization"]
        captured["body"] = request.content.decode()
        return httpx.Response(200, json=FIXTURE)

    source_with(handler).fetch_offers(CFG)
    # Formato: SHA256 Credential=<id>, Timestamp=<ts>, Signature=<sig>
    parts = dict(p.strip().split("=", 1)
                 for p in captured["auth"].removeprefix("SHA256 ").split(","))
    esperado = hashlib.sha256(
        f"APPID{parts['Timestamp']}{captured['body']}SECRET".encode()).hexdigest()
    assert parts["Signature"] == esperado


def test_fetch_offers_parses_and_skips_bad_nodes():
    offers = source_with(lambda r: httpx.Response(200, json=FIXTURE)).fetch_offers(CFG)
    assert len(offers) == 1  # nó sem preço é ignorado
    o = offers[0]
    assert o.item_id == "123456"
    assert o.price_current_cents == 24999
    assert o.price_original_cents == 49998  # derivado de price e priceDiscountRate
    assert o.commission_pct == 12.0
    assert o.category == "100636"
    assert o.offer_link == "https://s.shopee.com.br/xyz"


def test_resolve_affiliate_link_short_link():
    def handler(request):
        return httpx.Response(200, json={
            "data": {"generateShortLink": {"shortLink": "https://shope.ee/abc"}}})
    src = source_with(handler)
    offers = [o for o in FIXTURE["data"]["productOfferV2"]["nodes"]]
    from tests.test_models import make_offer
    assert src.resolve_affiliate_link(make_offer()) == "https://shope.ee/abc"


def test_resolve_affiliate_link_falls_back_to_offer_link():
    def handler(request):
        return httpx.Response(200, json={"errors": [{"message": "quota"}]})
    from tests.test_models import make_offer
    offer = make_offer(offer_link="https://s.shopee.com.br/xyz")
    assert source_with(handler).resolve_affiliate_link(offer) == "https://s.shopee.com.br/xyz"


def test_resolve_affiliate_link_raises_without_fallback():
    def handler(request):
        return httpx.Response(200, json={"errors": [{"message": "quota"}]})
    from tests.test_models import make_offer
    with pytest.raises(SourceError):
        source_with(handler).resolve_affiliate_link(make_offer(offer_link=""))
```

- [ ] **Step 3: Rodar e ver falhar**

Run: `pytest tests/test_shopee.py -v`
Expected: FAIL — ImportError.

- [ ] **Step 4: Implementar**

`src/afiliado/sources/__init__.py`: vazio.

`src/afiliado/sources/base.py`:

```python
from typing import Protocol

from afiliado.models import Offer


class Source(Protocol):
    name: str

    def fetch_offers(self, cfg: dict) -> list[Offer]: ...

    def resolve_affiliate_link(self, offer: Offer) -> str: ...
```

`src/afiliado/sources/shopee.py`:

```python
import hashlib
import json
import time
from decimal import Decimal, InvalidOperation

import httpx

from afiliado.errors import SourceError
from afiliado.models import Offer

GRAPHQL_URL = "https://open-api.affiliate.shopee.com.br/graphql"

PRODUCT_OFFER_QUERY = """
query productOfferV2($page: Int, $limit: Int, $sortType: Int, $listType: Int) {
  productOfferV2(page: $page, limit: $limit, sortType: $sortType, listType: $listType) {
    nodes {
      itemId productName price priceDiscountRate commissionRate sales
      imageUrl productLink offerLink productCatIds
    }
  }
}
"""

GEN_LINK_MUTATION = """
mutation generateShortLink($url: String!) {
  generateShortLink(input: { originUrl: $url }) { shortLink }
}
"""


class ShopeeSource:
    name = "shopee"

    def __init__(self, app_id: str, app_secret: str, client: httpx.Client | None = None):
        self.app_id = app_id
        self.app_secret = app_secret
        self.client = client or httpx.Client(timeout=30)

    def _post(self, payload: dict) -> dict:
        body = json.dumps(payload, separators=(",", ":"))
        ts = str(int(time.time()))
        sig = hashlib.sha256(
            f"{self.app_id}{ts}{body}{self.app_secret}".encode()).hexdigest()
        headers = {
            "Authorization": f"SHA256 Credential={self.app_id}, Timestamp={ts}, Signature={sig}",
            "Content-Type": "application/json",
        }
        try:
            r = self.client.post(GRAPHQL_URL, content=body, headers=headers)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise SourceError(f"shopee API: {exc}") from exc
        data = r.json()
        if data.get("errors"):
            raise SourceError(f"shopee GraphQL: {data['errors']}")
        return data["data"]

    def fetch_offers(self, cfg: dict) -> list[Offer]:
        sh = cfg["shopee"]
        offers: list[Offer] = []
        for page in range(1, sh["pages"] + 1):
            data = self._post({
                "query": PRODUCT_OFFER_QUERY,
                "variables": {"page": page, "limit": sh["page_size"],
                              "sortType": sh["sort_type"], "listType": sh["list_type"]},
            })
            nodes = (data.get("productOfferV2") or {}).get("nodes") or []
            offers.extend(o for o in (_parse_node(n) for n in nodes) if o)
        return offers

    def resolve_affiliate_link(self, offer: Offer) -> str:
        try:
            data = self._post({"query": GEN_LINK_MUTATION,
                               "variables": {"url": offer.product_url}})
            link = (data.get("generateShortLink") or {}).get("shortLink") or ""
            if link:
                return link
        except SourceError:
            pass
        if offer.offer_link:
            return offer.offer_link
        raise SourceError(f"sem link de afiliado para item {offer.item_id}")


def _parse_node(node: dict) -> Offer | None:
    try:
        price_cents = int(Decimal(str(node["price"])) * 100)
    except (KeyError, TypeError, InvalidOperation):
        return None
    rate = node.get("priceDiscountRate") or 0
    if 0 < rate < 90:
        original_cents = round(price_cents / (1 - rate / 100))
    else:
        original_cents = price_cents
    try:
        commission_pct = float(Decimal(str(node.get("commissionRate") or "0")) * 100)
    except InvalidOperation:
        commission_pct = 0.0
    cats = node.get("productCatIds") or []
    return Offer(
        source="shopee",
        item_id=str(node["itemId"]),
        title=str(node.get("productName") or "").strip(),
        price_original_cents=original_cents,
        price_current_cents=price_cents,
        commission_pct=commission_pct,
        image_url=str(node.get("imageUrl") or ""),
        product_url=str(node.get("productLink") or ""),
        offer_link=str(node.get("offerLink") or ""),
        category=str(cats[0]) if cats else "",
        sales=int(node.get("sales") or 0),
    )
```

- [ ] **Step 5: Rodar e ver passar**

Run: `pytest tests/test_shopee.py -v`
Expected: 5 passed. (Conferir o valor 49998: `round(24999 / 0.5) = 49998`.)

- [ ] **Step 6: Commit**

```bash
git add src/afiliado/sources tests/test_shopee.py tests/fixtures
git commit -m "feat: fonte Shopee com assinatura, parse de ofertas e link curto"
```

---

### Task 7: Seleção — filtros e ranqueamento (`selection.py`)

**Files:**
- Create: `src/afiliado/selection.py`
- Test: `tests/test_selection.py`

**Interfaces:**
- Consumes: `Offer`, `StateDB`, `llm.ask_json`
- Produces:
  - `filter_offers(offers: list[Offer], db: StateDB, cfg: dict) -> list[Offer]` — remove: já postado (`dedupe_days`), desconto < mínimo, preço fora da faixa, categoria fora de `category_ids` (lista vazia = todas), título/imagem/URL vazios
  - `rank_offers(candidates: list[Offer], recent_titles: list[str], cfg: dict) -> list[Offer]` — top N (`posts_per_run`) via LLM; fallback determinístico
  - `order_by_discount(offers: list[Offer]) -> list[Offer]` — ordena por desconto desc (usada pelo pipeline como fila reserva)

- [ ] **Step 1: Escrever testes que falham**

`tests/test_selection.py`:

```python
from afiliado import llm, selection
from afiliado.state import StateDB
from tests.test_models import make_offer
from tests.test_state import make_post

CFG = {
    "selection": {"posts_per_run": 2, "min_discount_pct": 20, "price_min_brl": 20,
                  "price_max_brl": 1000, "dedupe_days": 30, "category_ids": []},
    "llm": {"model": "haiku"},
}


def test_filter_offers(tmp_path):
    db = StateDB(tmp_path / "s.db")
    db.record_post(make_post(item_id="dup"), channel="telegram", message_id="1")
    offers = [
        make_offer(item_id="ok"),
        make_offer(item_id="dup"),                                  # já postado
        make_offer(item_id="caro", price_current_cents=200_000,
                   price_original_cents=400_000),                    # fora da faixa
        make_offer(item_id="pouco", price_original_cents=26_000),    # desconto < 20%
        make_offer(item_id="semtitulo", title=""),                   # inválido
    ]
    result = selection.filter_offers(offers, db, CFG)
    assert [o.item_id for o in result] == ["ok"]
    db.close()


def test_filter_offers_category_allowlist(tmp_path):
    db = StateDB(tmp_path / "s.db")
    cfg = {**CFG, "selection": {**CFG["selection"], "category_ids": ["100636"]}}
    offers = [make_offer(item_id="a", category="100636"),
              make_offer(item_id="b", category="999")]
    assert [o.item_id for o in selection.filter_offers(offers, db, cfg)] == ["a"]
    db.close()


def test_rank_offers_uses_llm_choice(monkeypatch):
    cands = [make_offer(item_id=str(i)) for i in range(5)]
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: {"chosen": ["3", "1"]})
    assert [o.item_id for o in selection.rank_offers(cands, [], CFG)] == ["3", "1"]


def test_rank_offers_fallback_on_llm_failure(monkeypatch):
    cands = [make_offer(item_id="a", price_original_cents=30_000),   # ~17%... reprovado? não: filtro já passou; aqui só ordena
             make_offer(item_id="b", price_original_cents=100_000)]  # 75%
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    ranked = selection.rank_offers(cands + [make_offer(item_id="c")], [], CFG)
    assert ranked[0].item_id == "b"  # maior desconto primeiro
    assert len(ranked) == 2


def test_rank_offers_skips_llm_when_few_candidates(monkeypatch):
    called = []
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: called.append(1))
    cands = [make_offer(item_id="a")]
    assert selection.rank_offers(cands, [], CFG) == cands
    assert not called
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `pytest tests/test_selection.py -v`
Expected: FAIL — ImportError.

- [ ] **Step 3: Implementar `src/afiliado/selection.py`**

```python
from afiliado import llm
from afiliado.models import Offer
from afiliado.state import StateDB


def filter_offers(offers: list[Offer], db: StateDB, cfg: dict) -> list[Offer]:
    sel = cfg["selection"]
    allowed_cats = {str(c) for c in sel.get("category_ids") or []}
    result = []
    for o in offers:
        if not (o.title and o.image_url and o.product_url):
            continue
        if allowed_cats and o.category not in allowed_cats:
            continue
        if o.discount_pct < sel["min_discount_pct"]:
            continue
        preco_brl = o.price_current_cents / 100
        if not sel["price_min_brl"] <= preco_brl <= sel["price_max_brl"]:
            continue
        if db.was_posted_recently(o.source, o.item_id, sel["dedupe_days"]):
            continue
        result.append(o)
    return result


def order_by_discount(offers: list[Offer]) -> list[Offer]:
    return sorted(offers, key=lambda o: o.discount_pct, reverse=True)


def _rank_prompt(candidates: list[Offer], recent_titles: list[str], n: int) -> str:
    linhas = "\n".join(
        f"- id={o.item_id} | {o.title} | categoria={o.category} | "
        f"desconto={o.discount_pct}% | vendas={o.sales}"
        for o in candidates)
    recentes = "\n".join(f"- {t}" for t in recent_titles) or "(nenhum)"
    return (
        "Você seleciona ofertas para um canal de promoções brasileiro (achadinhos).\n"
        f"Escolha as {n} melhores ofertas da lista, priorizando apelo popular, "
        "bom desconto e variedade de categorias entre si e vs. posts recentes.\n"
        f"Candidatas:\n{linhas}\n\nPosts recentes:\n{recentes}\n\n"
        'Responda APENAS com JSON no formato {"chosen": ["id1", "id2", ...]}'
    )


def rank_offers(candidates: list[Offer], recent_titles: list[str], cfg: dict) -> list[Offer]:
    n = cfg["selection"]["posts_per_run"]
    if len(candidates) <= n:
        return list(candidates)
    data = llm.ask_json(_rank_prompt(candidates, recent_titles, n),
                        model=cfg["llm"]["model"])
    if isinstance(data, dict):
        by_id = {o.item_id: o for o in candidates}
        picked = [by_id[str(i)] for i in data.get("chosen", []) if str(i) in by_id][:n]
        if len(picked) == n:
            return picked
    return order_by_discount(candidates)[:n]
```

- [ ] **Step 4: Rodar e ver passar**

Run: `pytest tests/test_selection.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/afiliado/selection.py tests/test_selection.py
git commit -m "feat: filtros por regra e ranqueamento LLM com fallback"
```

---

### Task 8: Copywriter (`copywriter.py`)

**Files:**
- Create: `src/afiliado/copywriter.py`
- Test: `tests/test_copywriter.py`

**Interfaces:**
- Consumes: `Offer`, `CopyParts`, `llm.ask_json`, `validate.check_copy`
- Produces:
  - `write_copy(offer: Offer, cfg: dict) -> CopyParts` — até 2 tentativas de LLM; se ambas inválidas, retorna `fallback_copy(offer)`; nunca levanta exceção
  - `fallback_copy(offer: Offer) -> CopyParts` — copy de template sem LLM

- [ ] **Step 1: Escrever testes que falham**

`tests/test_copywriter.py`:

```python
from afiliado import copywriter, llm
from afiliado.models import CopyParts
from tests.test_models import make_offer

CFG = {"llm": {"model": "haiku"}, "copy": {"tone": "empolgado, pt-BR"}}

VALID = {"headline": "🔥 Nike com 50% OFF", "description": "Clássico por metade do preço.",
         "cta": "Corre que acaba 👇"}


def test_write_copy_success(monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: VALID)
    copy = copywriter.write_copy(make_offer(), CFG)
    assert copy == CopyParts(**VALID)


def test_write_copy_retries_then_succeeds(monkeypatch):
    respostas = iter([{"headline": "", "description": "", "cta": ""}, VALID])
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: next(respostas))
    assert copywriter.write_copy(make_offer(), CFG) == CopyParts(**VALID)


def test_write_copy_falls_back(monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    copy = copywriter.write_copy(make_offer(), CFG)
    assert copy == copywriter.fallback_copy(make_offer())
    assert "50%" in copy.headline
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `pytest tests/test_copywriter.py -v`
Expected: FAIL — ImportError.

- [ ] **Step 3: Implementar `src/afiliado/copywriter.py`**

```python
from afiliado import llm, validate
from afiliado.errors import ValidationError
from afiliado.models import CopyParts, Offer


def _copy_prompt(offer: Offer, cfg: dict) -> str:
    return (
        "Escreva a copy de um post de promoção para canal brasileiro de ofertas.\n"
        f"Produto: {offer.title}\nCategoria: {offer.category or 'geral'}\n"
        f"Desconto: {offer.discount_pct}%\nTom: {cfg['copy']['tone']}\n"
        "NÃO inclua preço nem link — eles são adicionados pelo sistema.\n"
        "Responda APENAS com JSON: {\"headline\": \"até 60 chars, com 1 emoji\", "
        "\"description\": \"até 120 chars\", \"cta\": \"até 40 chars\"}"
    )


def fallback_copy(offer: Offer) -> CopyParts:
    return CopyParts(
        headline=f"🔥 Oferta: {offer.discount_pct}% OFF",
        description="Promoção por tempo limitado, aproveite enquanto dura.",
        cta="Garanta o seu 👇",
    )


def write_copy(offer: Offer, cfg: dict) -> CopyParts:
    for _ in range(2):
        data = llm.ask_json(_copy_prompt(offer, cfg), model=cfg["llm"]["model"])
        if not isinstance(data, dict):
            continue
        copy = CopyParts(
            headline=str(data.get("headline") or "").strip(),
            description=str(data.get("description") or "").strip(),
            cta=str(data.get("cta") or "").strip(),
        )
        try:
            validate.check_copy(copy)
        except ValidationError:
            continue
        return copy
    return fallback_copy(offer)
```

- [ ] **Step 4: Rodar e ver passar**

Run: `pytest tests/test_copywriter.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/afiliado/copywriter.py tests/test_copywriter.py
git commit -m "feat: copywriter LLM com retry e fallback de template"
```

---

### Task 9: Montagem da mensagem (`message.py`)

**Files:**
- Create: `src/afiliado/message.py`
- Test: `tests/test_message.py`

**Interfaces:**
- Consumes: `Offer`, `CopyParts`, `format_brl`
- Produces: `build_message(offer: Offer, copy: CopyParts, link: str) -> str` — texto em HTML do Telegram (`parse_mode=HTML`); título e copy escapados com `html.escape`; preços e link injetados programaticamente

- [ ] **Step 1: Escrever golden test que falha**

`tests/test_message.py`:

```python
from afiliado.message import build_message
from afiliado.models import CopyParts
from tests.test_models import make_offer

ESPERADO = """🚨 Promo Nike: 50% OFF
Nike SB com custo benefício.

Tênis Nike SB Chron 2 &quot;Black White&quot;
De: <s>R$ 499,99</s> | Por: <b>R$ 249,99</b> (50% OFF)

Corre que acaba rápido 👇
👉 https://shope.ee/abc123"""


def test_build_message_golden():
    offer = make_offer(title='Tênis Nike SB Chron 2 "Black White"')
    copy = CopyParts(headline="🚨 Promo Nike: 50% OFF",
                     description="Nike SB com custo benefício.",
                     cta="Corre que acaba rápido 👇")
    assert build_message(offer, copy, "https://shope.ee/abc123") == ESPERADO
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `pytest tests/test_message.py -v`
Expected: FAIL — ImportError.

- [ ] **Step 3: Implementar `src/afiliado/message.py`**

```python
import html

from afiliado.models import CopyParts, Offer, format_brl


def build_message(offer: Offer, copy: CopyParts, link: str) -> str:
    return (
        f"{html.escape(copy.headline)}\n"
        f"{html.escape(copy.description)}\n"
        f"\n"
        f"{html.escape(offer.title)}\n"
        f"De: <s>{format_brl(offer.price_original_cents)}</s> | "
        f"Por: <b>{format_brl(offer.price_current_cents)}</b> "
        f"({offer.discount_pct}% OFF)\n"
        f"\n"
        f"{html.escape(copy.cta)}\n"
        f"👉 {link}"
    )
```

- [ ] **Step 4: Rodar e ver passar**

Run: `pytest tests/test_message.py -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add src/afiliado/message.py tests/test_message.py
git commit -m "feat: montagem da mensagem com golden test"
```

---

### Task 10: Canal Telegram (`channels/base.py`, `channels/telegram.py`)

**Files:**
- Create: `src/afiliado/channels/__init__.py`, `src/afiliado/channels/base.py`, `src/afiliado/channels/telegram.py`
- Test: `tests/test_telegram.py`

**Interfaces:**
- Consumes: `Post` de `models`
- Produces:
  - `PublishResult(ok: bool, message_id: str = "", error: str = "")` — dataclass em `channels/base.py`
  - Protocolo `Channel`: atributo `name: str`; `publish(post: Post) -> PublishResult`
  - `TelegramChannel(bot_token: str, chat_id: str, client: httpx.Client | None = None)`, `name = "telegram"` — sendPhoto com caption HTML; se falhar, fallback sendMessage; 3 tentativas em erro de rede
  - Função de módulo `send_text(bot_token: str, chat_id: str, text: str, client: httpx.Client | None = None) -> None` — notificações de operações; nunca levanta exceção

- [ ] **Step 1: Escrever testes que falham**

`tests/test_telegram.py`:

```python
import httpx

from afiliado.channels.telegram import TelegramChannel, send_text
from tests.test_state import make_post


def channel_with(handler) -> TelegramChannel:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return TelegramChannel("TOKEN", "@canal", client=client)


def test_publish_send_photo_ok():
    def handler(request):
        assert request.url.path.endswith("/sendPhoto")
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})
    res = channel_with(handler).publish(make_post())
    assert res.ok and res.message_id == "42"


def test_publish_falls_back_to_send_message():
    def handler(request):
        if request.url.path.endswith("/sendPhoto"):
            return httpx.Response(400, json={"ok": False, "description": "bad photo"})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})
    res = channel_with(handler).publish(make_post())
    assert res.ok and res.message_id == "7"


def test_publish_total_failure():
    def handler(request):
        return httpx.Response(400, json={"ok": False, "description": "nope"})
    res = channel_with(handler).publish(make_post())
    assert not res.ok and "nope" in res.error


def test_publish_retries_network_error():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("down")
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
    res = channel_with(handler).publish(make_post())
    assert res.ok and calls["n"] == 3


def test_send_text_never_raises():
    def handler(request):
        raise httpx.ConnectError("down")
    client = httpx.Client(transport=httpx.MockTransport(handler))
    send_text("TOKEN", "123", "oi", client=client)  # não deve explodir
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `pytest tests/test_telegram.py -v`
Expected: FAIL — ImportError.

- [ ] **Step 3: Implementar**

`src/afiliado/channels/__init__.py`: vazio.

`src/afiliado/channels/base.py`:

```python
from dataclasses import dataclass
from typing import Protocol

from afiliado.models import Post


@dataclass
class PublishResult:
    ok: bool
    message_id: str = ""
    error: str = ""


class Channel(Protocol):
    name: str

    def publish(self, post: Post) -> PublishResult: ...
```

`src/afiliado/channels/telegram.py`:

```python
import httpx

from afiliado.channels.base import PublishResult
from afiliado.models import Post

API = "https://api.telegram.org"
_ATTEMPTS = 3


def _post_api(client: httpx.Client, url: str, payload: dict) -> dict:
    last = ""
    for _ in range(_ATTEMPTS):
        try:
            r = client.post(url, json=payload)
            return r.json()
        except httpx.HTTPError as exc:
            last = str(exc)
    return {"ok": False, "description": f"rede: {last}"}


class TelegramChannel:
    name = "telegram"

    def __init__(self, bot_token: str, chat_id: str, client: httpx.Client | None = None):
        self.base = f"{API}/bot{bot_token}"
        self.chat_id = chat_id
        self.client = client or httpx.Client(timeout=30)

    def publish(self, post: Post) -> PublishResult:
        data = _post_api(self.client, f"{self.base}/sendPhoto", {
            "chat_id": self.chat_id,
            "photo": post.offer.image_url,
            "caption": post.message_text,
            "parse_mode": "HTML",
        })
        if not data.get("ok"):
            data = _post_api(self.client, f"{self.base}/sendMessage", {
                "chat_id": self.chat_id,
                "text": post.message_text,
                "parse_mode": "HTML",
            })
        if data.get("ok"):
            return PublishResult(True, str(data["result"]["message_id"]))
        return PublishResult(False, error=str(data.get("description") or "desconhecido"))


def send_text(bot_token: str, chat_id: str, text: str,
              client: httpx.Client | None = None) -> None:
    c = client or httpx.Client(timeout=30)
    try:
        c.post(f"{API}/bot{bot_token}/sendMessage",
               json={"chat_id": chat_id, "text": text})
    except httpx.HTTPError:
        pass  # notificação de ops nunca derruba o run
```

- [ ] **Step 4: Rodar e ver passar**

Run: `pytest tests/test_telegram.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/afiliado/channels tests/test_telegram.py
git commit -m "feat: canal Telegram com fallback e retry"
```

---

### Task 11: Orquestração (`pipeline.py`)

**Files:**
- Create: `src/afiliado/pipeline.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: tudo das tasks anteriores
- Produces:
  - `RunSummary(published: list[str], discarded: list[str])` — dataclass com método `text() -> str` (resumo legível para o chat de operações)
  - `run(cfg: dict, sources: list[Source], channels: list[Channel], db: StateDB, dry_run: bool = False, validator=None) -> RunSummary` — `validator` default é `validate.validate_post` (injetável para teste); `SourceError` de `fetch_offers` propaga (aborta o run); falha por oferta descarta e promove a próxima; `dry_run` imprime em vez de publicar e não grava estado

- [ ] **Step 1: Escrever testes que falham**

`tests/test_pipeline.py`:

```python
from afiliado import llm, pipeline
from afiliado.channels.base import PublishResult
from afiliado.errors import ValidationError
from afiliado.state import StateDB
from tests.test_models import make_offer

CFG = {
    "selection": {"posts_per_run": 2, "min_discount_pct": 20, "price_min_brl": 20,
                  "price_max_brl": 1000, "dedupe_days": 30, "category_ids": []},
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
    s = pipeline.RunSummary(published=["a"], discarded=["b: x"])
    assert "Publicados (1)" in s.text() and "Descartados (1)" in s.text()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `pytest tests/test_pipeline.py -v`
Expected: FAIL — ImportError.

- [ ] **Step 3: Implementar `src/afiliado/pipeline.py`**

```python
from dataclasses import dataclass, field

from afiliado import copywriter, message, selection, validate
from afiliado.channels.base import Channel
from afiliado.models import Post
from afiliado.sources.base import Source
from afiliado.state import StateDB


@dataclass
class RunSummary:
    published: list[str] = field(default_factory=list)
    discarded: list[str] = field(default_factory=list)

    def text(self) -> str:
        linhas = [f"✅ Run concluído — Publicados ({len(self.published)}):"]
        linhas += [f"• {p}" for p in self.published] or ["• (nenhum)"]
        linhas.append(f"Descartados ({len(self.discarded)}):")
        linhas += [f"• {d}" for d in self.discarded] or ["• (nenhum)"]
        return "\n".join(linhas)


def run(cfg: dict, sources: list[Source], channels: list[Channel], db: StateDB,
        dry_run: bool = False, validator=None) -> RunSummary:
    validator = validator or validate.validate_post
    summary = RunSummary()

    offers = []
    for src in sources:
        offers.extend(src.fetch_offers(cfg))  # SourceError propaga: aborta o run

    candidates = selection.filter_offers(offers, db, cfg)
    ranked = selection.rank_offers(candidates, db.recent_titles(), cfg)
    reserva = [o for o in selection.order_by_discount(candidates) if o not in ranked]
    fila = ranked + reserva

    by_name = {s.name: s for s in sources}
    target = cfg["selection"]["posts_per_run"]
    count = 0

    for offer in fila:
        if count >= target:
            break
        rotulo = f"{offer.title[:40]} ({offer.discount_pct}% OFF)"
        try:
            link = by_name[offer.source].resolve_affiliate_link(offer)
            copy = copywriter.write_copy(offer, cfg)
            text = message.build_message(offer, copy, link)
            post = Post(offer=offer, copy=copy, affiliate_link=link, message_text=text)
            validator(post, cfg)
        except Exception as exc:
            summary.discarded.append(f"{rotulo}: {exc}")
            continue

        if dry_run:
            print(f"--- DRY-RUN: post que seria publicado ---\n{post.message_text}\n")
            summary.published.append(f"[dry] {rotulo}")
            count += 1
            continue

        for ch in channels:
            res = ch.publish(post)
            if res.ok:
                db.record_post(post, ch.name, res.message_id)
                summary.published.append(rotulo)
                count += 1
            else:
                summary.discarded.append(f"{rotulo}: publicação falhou: {res.error}")

    if not dry_run:
        db.record_run(len(summary.published), len(summary.discarded))
    return summary
```

- [ ] **Step 4: Rodar e ver passar**

Run: `pytest tests/test_pipeline.py -v`
Expected: 4 passed.

- [ ] **Step 5: Rodar a suíte inteira**

Run: `pytest -v`
Expected: todos os testes de todas as tasks passando.

- [ ] **Step 6: Commit**

```bash
git add src/afiliado/pipeline.py tests/test_pipeline.py
git commit -m "feat: orquestração do pipeline com fila reserva e dry-run"
```

---

### Task 12: CLI (`cli.py`) com `run`, `--dry-run` e `doctor`

**Files:**
- Create: `src/afiliado/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: tudo; env vars de segredos (ver Global Constraints)
- Produces: entry point `afiliado` (definido no pyproject da Task 1):
  - `afiliado run [--dry-run] [--config config.yaml]`
  - `afiliado doctor [--config config.yaml]` — smoke checks com credenciais reais
  - `main(argv: list[str] | None = None) -> int`

- [ ] **Step 1: Escrever teste que falha**

`tests/test_cli.py`:

```python
from afiliado import cli, pipeline


def test_run_dry_invokes_pipeline(monkeypatch, tmp_path):
    monkeypatch.setenv("SHOPEE_APP_ID", "id")
    monkeypatch.setenv("SHOPEE_APP_SECRET", "secret")
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        (open("config.yaml", encoding="utf-8").read()
         .replace("data/state.db", str(tmp_path / "s.db").replace("\\", "/"))),
        encoding="utf-8")

    chamado = {}

    def fake_run(cfg, sources, channels, db, dry_run=False, validator=None):
        chamado.update(dry_run=dry_run, n_sources=len(sources), n_channels=len(channels))
        return pipeline.RunSummary()

    monkeypatch.setattr(pipeline, "run", fake_run)
    assert cli.main(["run", "--dry-run", "--config", str(cfg_file)]) == 0
    assert chamado == {"dry_run": True, "n_sources": 1, "n_channels": 0}
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL — ImportError.

- [ ] **Step 3: Implementar `src/afiliado/cli.py`**

```python
import argparse
import os

from afiliado import config, llm, pipeline
from afiliado.channels.telegram import TelegramChannel, send_text
from afiliado.sources.shopee import ShopeeSource
from afiliado.state import StateDB


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="afiliado")
    sub = p.add_subparsers(dest="cmd", required=True)
    prun = sub.add_parser("run", help="executa o pipeline")
    prun.add_argument("--dry-run", action="store_true",
                      help="APIs reais, mas imprime em vez de publicar")
    prun.add_argument("--config", default="config.yaml")
    pdoc = sub.add_parser("doctor", help="verifica credenciais e dependências")
    pdoc.add_argument("--config", default="config.yaml")
    return p


def _shopee() -> ShopeeSource:
    return ShopeeSource(os.environ["SHOPEE_APP_ID"], os.environ["SHOPEE_APP_SECRET"])


def doctor(cfg: dict) -> int:
    ok = True
    try:
        offers = _shopee().fetch_offers({**cfg, "shopee": {**cfg["shopee"], "pages": 1}})
        print(f"✅ Shopee: {len(offers)} ofertas; primeira: "
              f"{offers[0] if offers else '(vazio — confira sort_type/list_type)'}")
    except Exception as exc:
        ok = False
        print(f"❌ Shopee: {exc}")
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    ops = os.environ.get("TELEGRAM_OPS_CHAT_ID", "")
    if token and ops:
        send_text(token, ops, "🩺 doctor: bot funcionando")
        print("✅ Telegram: mensagem de teste enviada ao chat de operações")
    else:
        ok = False
        print("❌ Telegram: TELEGRAM_BOT_TOKEN/TELEGRAM_OPS_CHAT_ID ausentes")
    resp = llm.ask_json('Responda APENAS com JSON: {"ok": true}',
                        model=cfg["llm"]["model"])
    if resp == {"ok": True}:
        print("✅ Claude CLI: respondendo")
    else:
        ok = False
        print("❌ Claude CLI: sem resposta (claude instalado? autenticado?)")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    cfg = config.load_config(args.config)
    if args.cmd == "doctor":
        return doctor(cfg)

    db = StateDB(cfg["state"]["path"])
    sources = [_shopee()]
    channels = []
    if not args.dry_run:
        channels = [TelegramChannel(os.environ["TELEGRAM_BOT_TOKEN"],
                                    os.environ["TELEGRAM_CHANNEL_ID"])]
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    ops = os.environ.get("TELEGRAM_OPS_CHAT_ID", "")
    try:
        summary = pipeline.run(cfg, sources, channels, db, dry_run=args.dry_run)
    except Exception as exc:
        if not args.dry_run and token and ops:
            send_text(token, ops, f"❌ Run abortado: {exc}")
        raise
    finally:
        db.close()

    if args.dry_run:
        print(summary.text())
    elif token and ops:
        send_text(token, ops, summary.text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Rodar e ver passar**

Run: `pytest tests/test_cli.py -v` e depois `pytest -v` (suíte completa)
Expected: tudo passando.

- [ ] **Step 5: Commit**

```bash
git add src/afiliado/cli.py tests/test_cli.py
git commit -m "feat: CLI com run, dry-run e doctor"
```

---

### Task 13: GitHub Actions + README de setup

**Files:**
- Create: `.github/workflows/publish.yml`, `.github/workflows/tests.yml`, `README.md`

**Interfaces:**
- Consumes: entry point `afiliado run`; secrets do repositório (nomes exatos nas Global Constraints)
- Produces: publicação agendada 3x/dia com estado commitado de volta; testes em todo push

- [ ] **Step 1: Criar `.github/workflows/tests.yml`**

```yaml
name: tests
on: [push, pull_request]
jobs:
  pytest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e .[dev]
      - run: pytest -v
```

- [ ] **Step 2: Criar `.github/workflows/publish.yml`**

Horários: 09:00, 12:30 e 19:30 BRT = 12:00, 15:30 e 22:30 UTC.

```yaml
name: publish
on:
  schedule:
    - cron: "0 12 * * *"
    - cron: "30 15 * * *"
    - cron: "30 22 * * *"
  workflow_dispatch:
    inputs:
      dry_run:
        description: "dry-run (não publica)"
        type: boolean
        default: false
concurrency:
  group: publish
  cancel-in-progress: false
permissions:
  contents: write
jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
      - run: npm install -g @anthropic-ai/claude-code
      - run: pip install -e .
      - name: Executar pipeline
        run: afiliado run ${{ inputs.dry_run && '--dry-run' || '' }}
        env:
          SHOPEE_APP_ID: ${{ secrets.SHOPEE_APP_ID }}
          SHOPEE_APP_SECRET: ${{ secrets.SHOPEE_APP_SECRET }}
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHANNEL_ID: ${{ secrets.TELEGRAM_CHANNEL_ID }}
          TELEGRAM_OPS_CHAT_ID: ${{ secrets.TELEGRAM_OPS_CHAT_ID }}
          CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
      - name: Commitar estado
        if: ${{ !inputs.dry_run }}
        run: |
          git config user.name "afiliado-bot"
          git config user.email "afiliado-bot@users.noreply.github.com"
          git add data/state.db
          git diff --cached --quiet || git commit -m "chore: atualiza estado após run [skip ci]"
          git push
```

- [ ] **Step 3: Criar `README.md`**

```markdown
# Afiliado

Pipeline automático de divulgação de ofertas com link de afiliado
(Shopee → Telegram). Spec: `docs/superpowers/specs/2026-08-23-afiliado-design.md`.

## Setup local

1. `pip install -e .[dev]` (Python 3.12+)
2. `pytest` — a suíte não toca a rede.
3. Instalar Claude Code e logar (assinatura Max): `npm i -g @anthropic-ai/claude-code && claude`

## Credenciais (variáveis de ambiente)

| Variável | Como obter |
|---|---|
| `SHOPEE_APP_ID` / `SHOPEE_APP_SECRET` | Portal Shopee Afiliados BR → área de API aberta (Open API) → solicitar credenciais |
| `TELEGRAM_BOT_TOKEN` | Falar com o @BotFather no Telegram → `/newbot` |
| `TELEGRAM_CHANNEL_ID` | Criar canal público, adicionar o bot como administrador; usar `@nomedocanal` |
| `TELEGRAM_OPS_CHAT_ID` | Mandar `/start` para o bot no privado; pegar o `chat.id` em `https://api.telegram.org/bot<TOKEN>/getUpdates` |
| `CLAUDE_CODE_OAUTH_TOKEN` | Rodar `claude setup-token` na sua máquina (usa a assinatura Max) |

No GitHub: Settings → Secrets and variables → Actions → criar os 6 secrets acima.

## Comandos

- `afiliado doctor` — verifica Shopee, Telegram e Claude CLI com credenciais reais.
- `afiliado run --dry-run` — pipeline completo (APIs reais) imprimindo os posts sem publicar.
- `afiliado run` — executa e publica de verdade.

## Agendamento

`.github/workflows/publish.yml` roda 3x/dia (09:00, 12:30, 19:30 BRT) e commita
`data/state.db` de volta. Disparo manual: aba Actions → publish → Run workflow
(com opção dry-run).

## VPS (futuro)

O sistema não depende do Actions: numa VPS basta clonar, exportar as mesmas
variáveis num `.env`/profile e agendar `afiliado run` no cron. O `state.db`
local persiste sozinho (sem commit).
```

- [ ] **Step 4: Validar sintaxe dos workflows**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/publish.yml', encoding='utf-8')); yaml.safe_load(open('.github/workflows/tests.yml', encoding='utf-8')); print('yaml ok')"`
Expected: `yaml ok`.

- [ ] **Step 5: Commit**

```bash
git add .github README.md
git commit -m "chore: workflows de publicação e testes + README de setup"
```

---

### Task 14: Verificação end-to-end assistida (credenciais reais)

**Files:**
- Modify (se a API real divergir da fixture): `src/afiliado/sources/shopee.py`, `tests/fixtures/shopee_product_offer.json`, `config.yaml`

**Interfaces:**
- Consumes: todo o sistema + credenciais reais fornecidas pelo usuário
- Produces: primeiro post real no canal; workflows ativos no GitHub

Esta task é executada COM o usuário (há passos que só ele pode fazer). Checklist:

- [ ] **Step 1: Usuário cria as credenciais** — seguir a tabela do README: bot no BotFather, canal com bot admin, chat de ops, credenciais da Shopee Open API, `claude setup-token`. Definir as 6 variáveis no ambiente local.

- [ ] **Step 2: Rodar `afiliado doctor`**

Expected: 3 linhas ✅. Se a Shopee retornar erro de query/campo: comparar a resposta impressa com a fixture, ajustar `PRODUCT_OFFER_QUERY`/`_parse_node` e a fixture juntos, rodar `pytest tests/test_shopee.py` até passar, commitar `fix: ajusta parse à resposta real da Shopee`. Se retornar 0 ofertas: testar valores de `sort_type`/`list_type` conforme a documentação no portal da Shopee e atualizar `config.yaml`.

- [ ] **Step 3: Rodar `afiliado run --dry-run`**

Expected: até 3 posts impressos com copy, preços `De:/Por:` coerentes e link `shope.ee`/domínio Shopee. Conferir manualmente 1 link no navegador (produto certo?).

- [ ] **Step 4: Primeiro run real local: `afiliado run`**

Expected: posts no canal do Telegram com foto + legenda formatada; resumo no chat de operações; `data/state.db` modificado. Commitar o estado: `git add data/state.db && git commit -m "chore: estado após primeiro run"`.

- [ ] **Step 5: Publicar no GitHub** — criar repositório **privado**, cadastrar os 6 secrets, `git push`. Disparar `publish` manualmente com dry-run=true na aba Actions e conferir o log. Depois disparar sem dry-run e conferir o canal.

- [ ] **Step 6: Confirmar agendamento** — deixar os crons ativos; no dia seguinte, conferir no chat de operações se os 3 runs rodaram.

---

## Self-review do plano (executado na escrita)

- **Cobertura do spec (fase 1):** §4 estrutura → Tasks 1–12 (creative.py, instagram.py, whatsapp.py, meli.py são fases 2–4, fora deste plano; `message.py` foi adicionado ao spec §4 como módulo de montagem). §5 fluxo → Task 11. §8 portões → Task 5. §9 falhas → Tasks 5, 10, 11 (retry de fonte com backoff simplificado para retry no nível HTTP do httpx + abort com notificação — comportamento do spec preservado). §10 testes → todas as tasks + dry-run na Task 12. §11 segredos → Tasks 12–13.
- **Placeholders:** nenhum TBD/TODO; todo step de código tem o código.
- **Consistência de tipos:** assinaturas de `Offer`, `StateDB`, `Source`, `Channel`, `PublishResult`, `RunSummary`, `ask_json`, `validate_post` conferidas entre tasks (blocos "Interfaces" de cada task).
- **Risco conhecido e mitigado:** nomes exatos de campos/enums da Shopee API podem divergir da fixture — a Task 14 (doctor) foi desenhada para detectar e corrigir isso com a suíte protegendo o parse.

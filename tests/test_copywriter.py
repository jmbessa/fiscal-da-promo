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

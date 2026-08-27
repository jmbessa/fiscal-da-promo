from afiliado import copywriter, llm
from afiliado.models import CopyParts
from tests.test_models import make_offer

CFG = {"llm": {"model": "haiku"}, "copy": {"tone": "empolgado, pt-BR"}}

VALID = {"headline": "🔥 Nike com 50% OFF", "description": "Clássico por metade do preço.",
         "cta": "Corre que acaba 👇"}


def _com_desconto(**kw):
    return make_offer(price_ref_cents=49998, price_current_cents=24999, **kw)


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
    offer = _com_desconto()
    copy = copywriter.write_copy(offer, CFG)
    assert copy == copywriter.fallback_copy(offer)
    assert "50%" in copy.headline


def test_prompt_usa_desconto_verificado():
    # O "de" do vendedor (350,00 -> 249,99 = 29%) não pode virar promessa: só
    # a NOSSA referência conta.
    offer = make_offer(price_original_cents=35000, price_ref_cents=49998,
                       price_current_cents=24999)
    prompt = copywriter._copy_prompt(offer, CFG)
    assert "Desconto verificado: 50%" in prompt
    assert "Desconto: " not in prompt
    assert "NÃO tem desconto verificado" not in prompt


def test_prompt_proibe_palavras_de_desconto_sem_desconto_verificado():
    prompt = copywriter._copy_prompt(make_offer(), CFG)
    assert "Desconto verificado: 0%" in prompt
    assert "IMPORTANTE: este item NÃO tem desconto verificado" in prompt
    assert "NÃO use palavras de desconto" in prompt


def test_fallback_copy_sem_desconto_verificado_nao_alega_desconto():
    copy = copywriter.fallback_copy(make_offer(price_original_cents=35000))
    assert copy.headline == "🔥 Achado do dia"
    assert "OFF" not in copy.headline
    assert "%" not in copy.headline
    assert "romoção" not in copy.description


def test_fallback_copy_com_desconto_verificado():
    assert copywriter.fallback_copy(_com_desconto()).headline == "🔥 Oferta: 50% OFF"

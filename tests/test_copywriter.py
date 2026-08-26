from afiliado import copywriter, llm, pricing
from afiliado.models import NO_CLAIM, CopyParts, Verdict
from tests.test_models import make_offer, make_offer_ref

CFG = {"llm": {"model": "haiku"}, "copy": {"tone": "empolgado, pt-BR"}}

VALID = {"headline": "🔥 Nike com 50% OFF", "description": "Clássico por metade do preço.",
         "cta": "Corre que acaba 👇"}
NEUTRA = {"headline": "✨ Tênis clássico da Nike", "description": "Conforto para o dia todo.",
          "cta": "Garanta o seu 👇"}
PALAVRAS_PROIBIDAS = ("OFF", "%", "baixou", "promoção", "caiu", "desconto")


def _com_desconto(**kw):
    return make_offer_ref(49998, price_current_cents=24999, **kw)


def _v(offer, minimo=10) -> Verdict:
    return pricing.verdict(offer, minimo)


def test_write_copy_success(monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: VALID)
    copy = copywriter.write_copy(_com_desconto(), CFG, _v(_com_desconto()))
    assert copy == CopyParts(**VALID)


def test_write_copy_retries_then_succeeds(monkeypatch):
    respostas = iter([{"headline": "", "description": "", "cta": ""}, NEUTRA])
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: next(respostas))
    assert copywriter.write_copy(make_offer(), CFG, NO_CLAIM) == CopyParts(**NEUTRA)


def test_write_copy_falls_back(monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    offer = _com_desconto()
    copy = copywriter.write_copy(offer, CFG, _v(offer))
    assert copy == copywriter.fallback_copy(_v(offer))
    assert "50%" in copy.headline


def test_prompt_modo_a_usa_o_desconto_do_veredito():
    # O "de" do vendedor (350,00 -> 249,99 = 29%) não pode virar promessa;
    # e nem o real_discount_pct cru: só o número que o veredito autoriza.
    offer = make_offer(price_original_cents=35000, price_ref_cents=49998,
                       price_current_cents=24999)               # 50% cru, mas sem p25/janela
    prompt = copywriter._copy_prompt(offer, CFG, Verdict("A", 27, ""))
    assert "Desconto verificado: 27%" in prompt
    assert "50" not in prompt and "29" not in prompt
    assert "SEM desconto verificado" not in prompt


def test_prompt_modo_b_diz_sem_desconto_e_proibe_palavras_de_desconto():
    # C10: 4% verificado com mínimo 10 é modo B — o prompt dizia "Desconto
    # verificado: 4%" sem a proibição, e a copy vinha com "4% OFF".
    offer = make_offer_ref(2600, price_current_cents=2500)
    assert offer.real_discount_pct == 3
    prompt = copywriter._copy_prompt(offer, CFG, _v(offer))
    assert "SEM desconto verificado" in prompt
    assert "IMPORTANTE: este item está SEM desconto verificado" in prompt
    assert "NÃO use palavras de desconto" in prompt
    assert "Desconto verificado:" not in prompt
    assert "3%" not in prompt


def test_fallback_copy_modo_b_nao_alega_desconto():
    # Teste obrigatório 6.
    copy = copywriter.fallback_copy(NO_CLAIM)
    assert copy.headline == "🔥 Achado do dia"
    for campo in (copy.headline, copy.description, copy.cta):
        for palavra in PALAVRAS_PROIBIDAS:
            assert palavra.lower() not in campo.lower()
    assert not copywriter.alega_desconto(copy)


def test_fallback_copy_modo_a():
    assert copywriter.fallback_copy(Verdict("A", 50, "")).headline == "🔥 Oferta: 50% OFF"
    assert copywriter.fallback_copy(_v(_com_desconto())).headline == "🔥 Oferta: 50% OFF"


def test_write_copy_modo_b_rejeita_copy_do_llm_que_alega_desconto(monkeypatch):
    # O LLM ignora o prompt e escreve "Baixou: 4% OFF" num item em modo B:
    # a resposta é descartada; sem outra válida, sai o fallback neutro.
    respostas = iter([{"headline": "🔥 Baixou: 4% OFF", "description": "d", "cta": "c"},
                      {"headline": "Promoção do dia", "description": "d", "cta": "c"}])
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: next(respostas))
    copy = copywriter.write_copy(make_offer(), CFG, NO_CLAIM)
    assert copy.headline == "🔥 Achado do dia"


def test_write_copy_modo_b_aceita_copy_neutra(monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: NEUTRA)
    assert copywriter.write_copy(make_offer(), CFG, NO_CLAIM) == CopyParts(**NEUTRA)


def test_alega_desconto_por_palavra_inteira():
    assert copywriter.alega_desconto(CopyParts("50% OFF", "d", "c"))
    assert copywriter.alega_desconto(CopyParts("h", "o preço baixou", "c"))
    assert copywriter.alega_desconto(CopyParts("h", "d", "promoção relâmpago"))
    assert copywriter.alega_desconto(CopyParts("Menor preço do ano", "d", "c"))
    assert not copywriter.alega_desconto(CopyParts("Coffee maker", "office", "c"))
    assert not copywriter.alega_desconto(CopyParts("✨ Achado do dia", "vale o clique", "vai"))


def test_write_copy_nao_le_o_desconto_da_oferta(monkeypatch):
    # Quem manda é o veredito: oferta com 50% verificável e NO_CLAIM -> o
    # prompt é o de modo B e o fallback é o neutro.
    prompts = []
    monkeypatch.setattr(llm, "ask_json", lambda prompt, **k: prompts.append(prompt) or None)
    copy = copywriter.write_copy(_com_desconto(), CFG, NO_CLAIM)
    assert copy.headline == "🔥 Achado do dia"
    assert all("SEM desconto verificado" in p and "50" not in p for p in prompts)

from afiliado import pricing
from afiliado.message import build_message
from afiliado.models import NO_CLAIM, CopyParts, Verdict
from tests.test_models import make_offer, make_offer_ref

TITULO = 'Tênis Nike SB Chron 2 "Black White"'
LINK = "https://shope.ee/abc123"

# Modo A: desconto verificado contra a NOSSA referência (499,98 -> 249,99),
# com a marcação HTML do Telegram: referência riscada, preço em negrito.
ESPERADO_MODO_A = """🚨 Promo Nike: 50% OFF
Nike SB com custo benefício.

Tênis Nike SB Chron 2 &quot;Black White&quot;
De: <s>R$ 499,98</s> | Por: <b>R$ 249,99</b> (50% OFF)

Corre que acaba rápido 👇
👉 https://shope.ee/abc123"""

# Modo B: sem referência conhecida — o preço é o herói (negrito), prova
# social em texto puro logo abaixo, sem alegar desconto.
ESPERADO_MODO_B = """🚨 Promo Nike: 50% OFF
Nike SB com custo benefício.

Tênis Nike SB Chron 2 &quot;Black White&quot;
<b>R$ 249,99</b>
⭐ 4,8 · 12 mil vendidos

Corre que acaba rápido 👇
👉 https://shope.ee/abc123"""

ESPERADO_COM_SELO = """🚨 Promo Nike: 50% OFF
Nike SB com custo benefício.

Tênis Nike SB Chron 2 &quot;Black White&quot;
De: <s>R$ 499,98</s> | Por: <b>R$ 249,99</b> (50% OFF)
🏷️ Menor preço dos últimos 12 meses (verificado)

Corre que acaba rápido 👇
👉 https://shope.ee/abc123"""


def _copy():
    return CopyParts(headline="🚨 Promo Nike: 50% OFF",
                     description="Nike SB com custo benefício.",
                     cta="Corre que acaba rápido 👇")


def _com_desconto(**kw):
    return make_offer_ref(49998, title=TITULO, price_current_cents=24999, **kw)


def _texto(offer, minimo=10, copy=None):
    return build_message(offer, copy or _copy(), LINK, pricing.verdict(offer, minimo))


def test_build_message_modo_a_desconto_verificado():
    assert _texto(_com_desconto()) == ESPERADO_MODO_A


def test_build_message_modo_b_sem_referencia():
    offer = make_offer(title=TITULO, price_current_cents=24999, rating=4.8, sales=12000)
    assert _texto(offer) == ESPERADO_MODO_B


def test_build_message_sem_referencia_e_publicavel_e_nao_alega_desconto():
    # Teste obrigatório 3: oferta sem referência continua publicável e o texto
    # não contém "OFF" nem preço riscado.
    offer = make_offer(title=TITULO, price_original_cents=49999,
                       price_current_cents=24999)
    assert offer.price_ref_cents == 0
    texto = _texto(offer)
    corpo = texto.replace(_copy().headline, "")   # a copy é responsabilidade do copywriter
    assert "OFF" not in corpo
    assert "<s>" not in texto
    assert "R$ 499,99" not in texto               # o "de" do vendedor não aparece
    assert "R$ 249,99" in texto


def test_build_message_usa_a_nossa_referencia_nunca_o_de_do_vendedor():
    # Teste obrigatório 4: 26,00 -> 18,90 = 27% contra a NOSSA referência,
    # com o vendedor alegando "de R$ 350,00".
    offer = make_offer_ref(2600, title=TITULO, price_original_cents=35000,
                           price_current_cents=1890)
    texto = _texto(offer)
    assert "De: <s>R$ 26,00</s> | Por: <b>R$ 18,90</b> (27% OFF)" in texto
    assert "R$ 350,00" not in texto


def test_build_message_shopee_com_de_inflado_nao_alega_desconto():
    # Teste obrigatório 8: original 350,00 (inflado), atual 49,00, referência
    # real 52,00 -> 5% verificado, abaixo do mínimo: modo B.
    offer = make_offer_ref(5200, title=TITULO, price_original_cents=35000,
                           price_current_cents=4900, rating=4.9, sales=30000)
    texto = _texto(offer)
    assert "R$ 350,00" not in texto
    assert "R$ 52,00" not in texto
    assert "R$ 49,00" in texto
    assert "⭐ 4,9 · 30 mil vendidos" in texto
    corpo = texto.replace(_copy().headline, "")
    assert "OFF" not in corpo
    assert "<s>" not in texto


def test_build_message_min_real_discount_pct_configuravel():
    offer = make_offer_ref(5200, title=TITULO, price_current_cents=4900)
    neutra = CopyParts(headline="Achado do dia", description="d", cta="c")
    assert "OFF" not in _texto(offer, minimo=10, copy=neutra)
    assert "(5% OFF)" in _texto(offer, minimo=5, copy=neutra)


def test_build_message_com_selo():
    offer = _com_desconto(price_floor_cents=24999, price_floor_window_days=365)
    assert _texto(offer) == ESPERADO_COM_SELO


def test_build_message_selo_e_estrito_um_centavo_acima_nao_ganha():
    # C9: piso 24000, preço 24999 ganhava "Menor preço já registrado
    # (verificado)" pela tolerância de 5%. Agora só preço <= piso.
    assert "Menor preço" not in _texto(
        _com_desconto(price_floor_cents=24000, price_floor_window_days=365))
    assert "Menor preço" not in _texto(
        _com_desconto(price_floor_cents=24998, price_floor_window_days=365))
    assert "🏷️" not in _texto(_com_desconto(price_floor_cents=24998, price_floor_window_days=365))
    assert "🏷️ Menor preço dos últimos 12 meses (verificado)" in _texto(
        _com_desconto(price_floor_cents=24999, price_floor_window_days=365))


def test_build_message_selo_diz_a_janela_real():
    assert "🏷️ Menor preço dos últimos 45 dias (verificado)" in _texto(
        _com_desconto(price_floor_cents=24999, price_floor_window_days=45))
    assert "🏷️ Menor preço dos últimos 6 meses (verificado)" in _texto(
        _com_desconto(price_floor_cents=24999, price_floor_window_days=191))
    assert "já registrado" not in _texto(
        _com_desconto(price_floor_cents=24999, price_floor_window_days=191))


def test_build_message_obedece_ao_veredito_e_nao_recalcula():
    # Mesma oferta com 50% verificável: com NO_CLAIM o texto é modo B, sem
    # selo; com um veredito A + selo, o texto tem os dois. Quem decide é
    # `pricing.verdict`, uma vez — o consumidor só obedece.
    offer = _com_desconto(price_floor_cents=24999, price_floor_window_days=365)
    modo_b = build_message(offer, _copy(), LINK, NO_CLAIM)
    assert "<s>" not in modo_b and "Menor preço" not in modo_b and "R$ 249,99" in modo_b
    v = Verdict("A", 50, "🏷️ Menor preço dos últimos 12 meses (verificado)", 365)
    assert build_message(offer, _copy(), LINK, v) == ESPERADO_COM_SELO


def test_build_message_escapa_titulo_e_copy():
    offer = make_offer(title="<b>Tênis</b> & cia", price_current_cents=24999)
    copy = CopyParts(headline="<script>", description="a & b", cta="clique <aqui>")
    texto = build_message(offer, copy, LINK, NO_CLAIM)
    assert "&lt;b&gt;Tênis&lt;/b&gt; &amp; cia" in texto
    assert "&lt;script&gt;" in texto
    assert "clique &lt;aqui&gt;" in texto

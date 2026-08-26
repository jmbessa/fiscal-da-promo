from afiliado.message import build_message
from afiliado.models import CopyParts
from afiliado.watchlist import PriceFloor
from tests.test_models import make_offer

TITULO = 'Tênis Nike SB Chron 2 "Black White"'

# Modo A: desconto verificado contra a NOSSA referência (499,98 -> 249,99).
ESPERADO_MODO_A = """🚨 Promo Nike: 50% OFF
Nike SB com custo benefício.

Tênis Nike SB Chron 2 &quot;Black White&quot;
De: R$ 499,98 | Por: R$ 249,99 (50% OFF)

Corre que acaba rápido 👇
👉 https://shope.ee/abc123"""

# Modo B: sem referência conhecida — preço + prova social, sem alegar desconto.
ESPERADO_MODO_B = """🚨 Promo Nike: 50% OFF
Nike SB com custo benefício.

Tênis Nike SB Chron 2 &quot;Black White&quot;
R$ 249,99
⭐ 4,8 · 12 mil vendidos

Corre que acaba rápido 👇
👉 https://shope.ee/abc123"""

ESPERADO_COM_SELO = """🚨 Promo Nike: 50% OFF
Nike SB com custo benefício.

Tênis Nike SB Chron 2 &quot;Black White&quot;
De: R$ 499,98 | Por: R$ 249,99 (50% OFF)
🏷️ Menor preço dos últimos 12 meses (verificado)

Corre que acaba rápido 👇
👉 https://shope.ee/abc123"""


def _copy():
    return CopyParts(headline="🚨 Promo Nike: 50% OFF",
                     description="Nike SB com custo benefício.",
                     cta="Corre que acaba rápido 👇")


def _com_desconto(**kw):
    return make_offer(title=TITULO, price_ref_cents=49998,
                      price_current_cents=24999, **kw)


def test_build_message_modo_a_desconto_verificado():
    assert build_message(_com_desconto(), _copy(), "https://shope.ee/abc123") == ESPERADO_MODO_A


def test_build_message_modo_b_sem_referencia():
    offer = make_offer(title=TITULO, price_current_cents=24999, rating=4.8, sales=12000)
    assert build_message(offer, _copy(), "https://shope.ee/abc123") == ESPERADO_MODO_B


def test_build_message_sem_referencia_e_publicavel_e_nao_alega_desconto():
    # Teste obrigatório 3: oferta sem referência continua publicável e o texto
    # não contém "OFF" nem preço riscado.
    offer = make_offer(title=TITULO, price_original_cents=49999,
                       price_current_cents=24999)
    assert offer.price_ref_cents == 0
    texto = build_message(offer, _copy(), "https://shope.ee/abc123")
    corpo = texto.replace(_copy().headline, "")   # a copy é responsabilidade do copywriter
    assert "OFF" not in corpo
    assert "<s>" not in texto
    assert "R$ 499,99" not in texto               # o "de" do vendedor não aparece
    assert "R$ 249,99" in texto


def test_build_message_usa_a_nossa_referencia_nunca_o_de_do_vendedor():
    # Teste obrigatório 4: 26,00 -> 18,90 = 27% contra a NOSSA referência,
    # com o vendedor alegando "de R$ 350,00".
    offer = make_offer(title=TITULO, price_original_cents=35000,
                       price_ref_cents=2600, price_current_cents=1890)
    texto = build_message(offer, _copy(), "https://shope.ee/abc123")
    assert "De: R$ 26,00 | Por: R$ 18,90 (27% OFF)" in texto
    assert "R$ 350,00" not in texto


def test_build_message_shopee_com_de_inflado_nao_alega_desconto():
    # Teste obrigatório 8: original 350,00 (inflado), atual 49,00, referência
    # real 52,00 -> 6% verificado, abaixo do mínimo: modo B.
    offer = make_offer(title=TITULO, price_original_cents=35000,
                       price_current_cents=4900, price_ref_cents=5200,
                       rating=4.9, sales=30000)
    texto = build_message(offer, _copy(), "https://shope.ee/abc123")
    assert "R$ 350,00" not in texto
    assert "R$ 52,00" not in texto
    assert "R$ 49,00" in texto
    assert "⭐ 4,9 · 30 mil vendidos" in texto
    corpo = texto.replace(_copy().headline, "")
    assert "OFF" not in corpo
    assert "<s>" not in texto


def test_build_message_min_real_discount_pct_configuravel():
    offer = make_offer(title=TITULO, price_ref_cents=5200, price_current_cents=4900)
    neutra = CopyParts(headline="Achado do dia", description="d", cta="c")
    assert "OFF" not in build_message(offer, neutra, "x", min_real_discount_pct=10)
    assert "(6% OFF)" in build_message(offer, neutra, "x", min_real_discount_pct=5)


def test_build_message_with_price_floor_badge():
    floor = PriceFloor(min_price_cents=24999, window_days=365)
    result = build_message(_com_desconto(), _copy(), "https://shope.ee/abc123",
                           price_floor=floor)
    assert result == ESPERADO_COM_SELO


def test_build_message_no_badge_when_price_above_floor():
    floor = PriceFloor(min_price_cents=19999, window_days=365)
    result = build_message(_com_desconto(), _copy(), "https://shope.ee/abc123",
                           price_floor=floor)
    assert result == ESPERADO_MODO_A


def test_build_message_selo_do_piso_proprio_com_tolerancia():
    # Sem watchlist, o piso vem do nosso próprio histórico (price_floor_cents).
    offer = _com_desconto(price_floor_cents=24000)   # 24999 <= 24000 * 1.05
    texto = build_message(offer, _copy(), "https://shope.ee/abc123")
    assert "🏷️ Menor preço já registrado (verificado)" in texto


def test_build_message_selo_do_piso_proprio_fora_da_tolerancia():
    offer = _com_desconto(price_floor_cents=20000)   # 24999 > 20000 * 1.05
    assert "Menor preço já registrado" not in build_message(
        offer, _copy(), "https://shope.ee/abc123")


def test_build_message_watchlist_tem_precedencia_sobre_o_piso_proprio():
    offer = _com_desconto(price_floor_cents=24000)
    floor = PriceFloor(min_price_cents=24999, window_days=365)
    texto = build_message(offer, _copy(), "https://shope.ee/abc123", price_floor=floor)
    assert "🏷️ Menor preço dos últimos 12 meses (verificado)" in texto
    assert "Menor preço já registrado" not in texto


def test_build_message_escapa_titulo_e_copy():
    offer = make_offer(title="<b>Tênis</b> & cia", price_current_cents=24999)
    copy = CopyParts(headline="<script>", description="a & b", cta="clique <aqui>")
    texto = build_message(offer, copy, "https://shope.ee/abc123")
    assert "&lt;b&gt;Tênis&lt;/b&gt; &amp; cia" in texto
    assert "&lt;script&gt;" in texto
    assert "clique &lt;aqui&gt;" in texto

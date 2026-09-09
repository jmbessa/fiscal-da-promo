"""Fase 5Y — o canal `x`: texto puro, sem URL nenhuma.

**Por que sem link.** Em 06/02/2026 o X trocou os planos por pay-per-use e
acabou com o tier grátis; em 20/04/2026 o post COM link foi de US$ 0,01 para
**US$ 0,20**, contra US$ 0,015 de um post sem link. E o X reduz a distribuição
de post com link para segurar quem lê na plataforma — em conta sem Premium, o
engajamento mediano de post com link fica perto de zero.

Somadas: com link a gente paga 13× para alcançar menos. O destino vai no link
da BIO, que não é link de post.

Nada aqui toca a rede — o X é um `httpx.MockTransport`.
"""

import httpx
import pytest

from afiliado import creative
from afiliado.channels import x as canal_x
from afiliado.channels.x import XChannel, monta_texto, sem_url
from afiliado.models import NO_CLAIM, CopyParts, Offer, Post, Verdict

SELO = Verdict("B", 0, "🏷️ Menor preço dos últimos 3 meses (verificado)", 90)


def _post(titulo: str = "Fone de Ouvido Bluetooth TWS", cents: int = 8990,
          verdict: Verdict = NO_CLAIM, source: str = "shopee") -> Post:
    offer = Offer(source=source, item_id="1", title=titulo,
                  price_original_cents=cents, price_current_cents=cents,
                  commission_pct=8.0, image_url="", product_url="https://loja/x",
                  rating=4.8, sales=1200)
    return Post(offer=offer, copy=CopyParts("h", "d", "c"),
                affiliate_link="https://s.shopee.com.br/abc",
                message_text="", verdict=verdict)


def _canal(handler) -> XChannel:
    return XChannel("k", "ks", "t", "ts",
                    client=httpx.Client(transport=httpx.MockTransport(handler)))


def _ok(request):
    return httpx.Response(201, json={"data": {"id": "1937", "text": "..."}})


# -- o portão: nenhuma URL, nunca ---------------------------------------------

@pytest.mark.parametrize("texto", [
    "olha https://s.shopee.com.br/abc",
    "olha http://x.com/a",
    "acesse www.fiscaldapromo.com",
    "no telegram: t.me/fiscaldapromo",
    "veja em bit.ly/abc",
    "fiscaldapromo.com.br tem tudo",
])
def test_o_detector_de_url_e_amplo_de_proposito(texto):
    """`t.me/fiscaldapromo` sem esquema também vira link clicável — e cobrado —,
    e é justamente a forma que alguém escreveria sem perceber. Na dúvida, o
    portão fecha."""
    assert not sem_url(texto)


@pytest.mark.parametrize("texto", [
    "R$ 89,90 no meu Telegram, está na bio",
    "Quem conferiu? O Fiscal.",
    "Creatina 300g por R$ 45,90 · 27% OFF",
    "",
])
def test_texto_sem_url_passa(texto):
    assert sem_url(texto)


def test_um_post_com_url_e_RECUSADO_antes_de_qualquer_chamada(monkeypatch):
    """O caro não é o 400 do X: é o post sair. Ele custaria 13× e seria
    enterrado pelo algoritmo — o tipo de defeito que só aparece na fatura."""
    chamou = []
    canal = _canal(lambda r: chamou.append(r) or _ok(r))
    monkeypatch.setattr(canal_x, "CTA", "compre em https://s.shopee.com.br/abc")
    res = canal.publish(_post())
    assert res.ok is False
    assert "URL" in res.error and "0.20" in res.error
    assert chamou == []            # nem chegou a falar com o X


def test_o_texto_montado_NUNCA_tem_url():
    for titulo in ("Fone TWS", "Kit 3 Cremes t.me da marca X",
                   "Produto www com nome estranho"):
        assert sem_url(monta_texto(_post(titulo)))


# -- o texto -------------------------------------------------------------------

def test_o_post_traz_preco_assinatura_e_a_chamada_para_o_telegram():
    texto = monta_texto(_post())
    assert "R$ 89,90" in texto
    assert creative.ASSINATURA in texto
    assert "Telegram" in texto and "bio" in texto


def test_o_selo_entra_quando_o_veredito_o_traz():
    assert "Menor preço dos últimos 3 meses" in monta_texto(_post(verdict=SELO))


def test_o_titulo_encolhe_para_o_RESTO_caber_inteiro():
    """O que sustenta a marca — o preço conferido e a assinatura — não pode ser
    o que some quando o nome do produto é longo. E nome de anúncio de
    marketplace é sempre longo."""
    longo = "Produto " + "Muito Comprido " * 40
    texto = monta_texto(_post(longo))
    assert len(texto) <= canal_x.LIMITE_CHARS
    assert "R$ 89,90" in texto
    assert creative.ASSINATURA in texto
    assert canal_x.CTA in texto
    assert texto.startswith("Produto Muito")


def test_o_corte_respeita_a_palavra():
    """Cortar no meio de uma palavra é a marca visual de post automático, e é o
    detalhe que faz o leitor decidir que não vale ler."""
    texto = monta_texto(_post("Cadeira " + "Ergonomica " * 30))
    primeira = texto.splitlines()[0]
    assert primeira.endswith("…")
    assert not primeira[:-1].endswith(" ")
    # Nenhuma palavra cortada pela metade antes das reticências.
    assert primeira[:-1].split()[-1] in ("Cadeira", "Ergonomica")


def test_o_post_cabe_no_limite_de_conta_SEM_premium():
    """280, e não os 25.000 do Premium: a conta não tem Premium, e escrever
    para um limite que não temos é como se publica truncado."""
    assert canal_x.LIMITE_CHARS == 280
    for titulo in ("a", "Fone TWS", "Produto " * 60):
        assert len(monta_texto(_post(titulo))) <= 280


# -- a publicação ---------------------------------------------------------------

def test_publica_e_devolve_o_id():
    res = _canal(_ok).publish(_post())
    assert res.ok and res.message_id == "1937"


def test_a_requisicao_leva_OAuth_1_e_o_texto_no_corpo():
    vistos = {}

    def handler(request):
        vistos["auth"] = request.headers.get("Authorization", "")
        vistos["body"] = request.content.decode()
        vistos["url"] = str(request.url)
        return _ok(request)

    _canal(handler).publish(_post())
    assert vistos["url"] == canal_x.API
    assert vistos["auth"].startswith("OAuth ")
    for campo in ("oauth_consumer_key", "oauth_nonce", "oauth_signature",
                  "oauth_signature_method", "oauth_timestamp", "oauth_token"):
        assert campo in vistos["auth"]
    assert '"text"' in vistos["body"]


def test_a_assinatura_muda_a_cada_pedido():
    """O `oauth_nonce` e o `oauth_timestamp` entram na base assinada: dois
    pedidos iguais não podem produzir o mesmo cabeçalho, senão o X trata o
    segundo como repetição."""
    canal = _canal(_ok)
    a = canal._cabecalho("POST", canal_x.API)
    b = canal._cabecalho("POST", canal_x.API)
    assert a != b


def test_erro_do_X_marca_PUBLICADO_pelo_mesmo_motivo_da_fase_5X():
    """O pedido foi feito e a resposta não prova que nada saiu. Aqui repetir
    custa dinheiro por post, então errar para o lado seguro é ainda mais
    claro."""
    res = _canal(lambda r: httpx.Response(
        403, json={"detail": "duplicate content"})).publish(_post())
    assert res.ok is False
    assert res.publicado is True
    assert "duplicate" in res.error


def test_rede_caida_nao_levanta():
    def cai(request):
        raise httpx.ConnectError("sem rede")

    res = _canal(cai).publish(_post())
    assert res.ok is False and "rede" in res.error


# -- o preço da coisa, que é o que justifica o desenho -------------------------

def test_os_precos_da_api_estao_no_codigo_e_o_com_link_e_13x():
    """Eles vivem aqui porque são o número que justifica a regra do módulo — e
    porque o `doctor` estima o gasto do mês com eles."""
    assert canal_x.CUSTO_SEM_LINK_USD == 0.015
    assert canal_x.CUSTO_COM_LINK_USD == 0.20
    assert canal_x.CUSTO_COM_LINK_USD / canal_x.CUSTO_SEM_LINK_USD > 13


def test_o_canal_nasce_desligado_no_config_real():
    """Ele custa dinheiro por post e depende de um app que o dono precisa criar.
    Ligar sem isso seria acumular falha a cada 15 min."""
    import yaml

    with open("config.yaml", encoding="utf-8") as f:
        entrada = yaml.safe_load(f)["channels"]["x"]
    assert entrada["enabled"] is False
    assert entrada["max_per_day"] >= 1


def test_o_titulo_do_vendedor_e_SANEADO_e_nao_recusado():
    """O título é a única parte do post que vem de TERCEIRO. "Kit 3 Cremes t.me
    da marca X" é um nome plausível de anúncio, e ele sozinho faria o post
    custar 13× e perder alcance.

    Recusar seria pior que limpar: a oferta é boa, o defeito está numa palavra
    do nome dela. Aqui o dado de terceiro é saneado — e o portão de `publish`
    continua sendo a última linha de defesa para o que é NOSSO."""
    assert canal_x.limpa_titulo("Kit 3 Cremes t.me da marca X") == "Kit 3 Cremes da marca X"
    assert canal_x.limpa_titulo("Fone https://x.com/a TWS") == "Fone TWS"
    assert canal_x.limpa_titulo("Fone de Ouvido TWS") == "Fone de Ouvido TWS"
    # E o post inteiro sai limpo, com a oferta preservada.
    texto = monta_texto(_post("Kit 3 Cremes t.me da marca X"))
    assert sem_url(texto)
    assert "Kit 3 Cremes da marca X" in texto
    assert "R$ 89,90" in texto

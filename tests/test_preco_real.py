"""Fase 5P — a leitura do preço de checkout no navegador.

O que estes testes protegem é o `falhar fechado`: a superfície de maior risco
do projeto é publicar um número que não veio com a frase que o qualifica. Cada
recusa do parser tem teste PRÓPRIO, com o motivo por extenso, porque é a
mensagem do motivo que o dono vai ler no `afiliado preco-real`.

Nenhum teste aqui abre navegador nem toca a rede: o leitor recebe um duplo.
"""

from datetime import timedelta

from afiliado import preco_real
from afiliado.state import StateDB
from tests.test_models import make_offer

# O DOM medido em 2026-08-28 (docs/runbooks/shopee-preco.md), tal como veio:
# o número em destaque, a frase que o qualifica e a parcela, nesta ordem.
BLOCO = "R$523,48\nou R$599,00 sem cupom em outros métodos de pagamento\n12x R$55,21"
API = 59900


# -- o caminho feliz -----------------------------------------------------------

def test_le_o_numero_e_a_condicao_do_bloco_medido():
    leitura = preco_real.parse_preco(BLOCO, API)
    assert leitura.ok
    assert leitura.price_cents == 52348
    assert leitura.sem_cupom_cents == 59900
    assert leitura.motivo == ""


def test_a_condicao_padrao_e_com_cupom_porque_o_dom_nao_diz_pix():
    """O runbook RENDERIZA "R$ 611,80 no Pix com cupom", mas o DOM medido não
    tem a palavra Pix em lugar nenhum do bloco — só "sem cupom em outros
    métodos de pagamento". Escrever "no Pix" seria inventar a condição, que é
    exatamente o que a fase existe para não fazer."""
    assert preco_real.parse_preco(BLOCO, API).condicao == preco_real.COM_CUPOM


def test_a_condicao_vira_no_pix_quando_a_pagina_diz_pix():
    """Só então. O Pix tem de estar ENTRE o número em destaque e o fim da frase
    que o qualifica — um "Pix" na lista de meios de pagamento no rodapé da
    página não qualifica preço nenhum."""
    bloco = ("R$523,48 no Pix com cupom\n"
             "ou R$599,00 sem cupom em outros métodos de pagamento")
    assert preco_real.parse_preco(bloco, API).condicao == preco_real.PIX_COM_CUPOM


def test_pix_longe_do_preco_nao_vira_condicao():
    texto = BLOCO + "\nFormas de pagamento: Pix, boleto, cartão"
    assert preco_real.parse_preco(texto, API).condicao == preco_real.COM_CUPOM


def test_le_preco_com_milhar():
    bloco = "R$1.234,56\nou R$1.999,99 sem cupom em outros métodos de pagamento"
    leitura = preco_real.parse_preco(bloco, 199999)
    assert (leitura.price_cents, leitura.sem_cupom_cents) == (123456, 199999)


# -- as recusas, uma a uma -----------------------------------------------------

def test_sem_a_frase_nao_ha_leitura():
    """Um número solto não é preço qualificado. É a recusa mais importante do
    módulo: o preço grande da Shopee é o COM cupom, e publicá-lo sem a
    condição é publicar um preço que quase ninguém paga."""
    leitura = preco_real.parse_preco("R$523,48\n12x R$55,21", API)
    assert not leitura.ok
    assert leitura.motivo == preco_real.SEM_FRASE


def test_texto_vazio_nao_ha_leitura():
    assert preco_real.parse_preco("", API).motivo == preco_real.SEM_FRASE


def test_intersticio_de_verificacao_nao_ha_leitura():
    """Medido em 2026-08-28 num Chrome de perfil próprio e DESLOGADO: a rota do
    produto responde com este interstício, e depois de algumas requisições até
    a home cai em `/verify/traffic/error`. Ele não tem preço nenhum — mas o
    motivo precisa dizer que foi o interstício, e não "a página mudou de
    redação", senão o dono vai caçar o bug errado."""
    texto = ("Login Necessário\nParece que você ainda não está logado. "
             "Faça login para continuar ou volte para a página inicial.")
    assert preco_real.parse_preco(texto, API).motivo == preco_real.INTERSTICIO


def test_intersticio_vence_ate_com_a_frase_presente():
    """Ordem importa: se a página serviu o interstício, o que quer que pareça
    preço nele veio da casca, não do anúncio."""
    texto = "Página indisponível\n" + BLOCO
    assert preco_real.parse_preco(texto, API).motivo == preco_real.INTERSTICIO


def test_preco_sem_cupom_diferente_do_da_api_nao_ha_leitura():
    """A frase é a ÂNCORA: o número que ela traz tem de ser o mesmo que o
    `refresh_price` acabou de medir. Diferente quer dizer que estamos lendo
    outro anúncio, outra variação, ou um preço que mudou no meio — e nos três
    casos o certo é publicar o da API, como hoje."""
    leitura = preco_real.parse_preco(BLOCO, 68999)
    assert not leitura.ok
    assert leitura.motivo == preco_real.OUTRO_PRECO


def test_frase_sem_numero_em_destaque_antes_dela():
    texto = "ou R$599,00 sem cupom em outros métodos de pagamento"
    assert preco_real.parse_preco(texto, API).motivo == preco_real.SEM_NUMERO


def test_destaque_maior_ou_igual_ao_sem_cupom_nao_ha_leitura():
    """Preço de checkout que não é menor que o de catálogo não é desconto — é
    leitura errada. Publicar o maior seria pior que não ler nada."""
    texto = "R$599,00\nou R$599,00 sem cupom em outros métodos de pagamento"
    assert preco_real.parse_preco(texto, API).motivo == preco_real.NAO_E_DESCONTO


def test_destaque_barato_demais_e_provavel_parcela():
    """`12x R$55,21` antes da frase, num layout que a Shopee ainda não usa, viraria
    "R$ 55,21 com cupom" num produto de R$ 599 — 91% de desconto. O piso existe
    para essa classe de erro, não para julgar promoção."""
    texto = "12x R$55,21\nou R$599,00 sem cupom em outros métodos de pagamento"
    leitura = preco_real.parse_preco(texto, API)
    assert not leitura.ok
    assert leitura.motivo == preco_real.DESCONTO_IMPLAUSIVEL


def test_o_piso_de_plausibilidade_deixa_passar_o_limite():
    """Exatamente `FRACAO_MINIMA` passa: o piso recusa o absurdo, não a
    promoção agressiva."""
    limite = int(API * preco_real.FRACAO_MINIMA)
    texto = (f"R$ {limite // 100},{limite % 100:02d}\n"
             "ou R$599,00 sem cupom em outros métodos de pagamento")
    assert preco_real.parse_preco(texto, API).price_cents == limite


def test_preco_da_api_desconhecido_nao_ha_leitura():
    """Sem âncora não há verificação possível."""
    assert preco_real.parse_preco(BLOCO, 0).motivo == preco_real.SEM_ANCORA


# -- a guarda do perfil: o requisito que protege a conta -----------------------

def test_o_perfil_do_chrome_real_e_recusado(tmp_path):
    """A guarda mais importante da fase. O Chrome da máquina está logado na
    Shopee como o dono; sessenta páginas automatizadas por dia a partir dele é
    o padrão que a Shopee caça, e perder a conta encerra o lado Shopee — que
    hoje é 100% do que publicamos."""
    chrome = tmp_path / "AppData/Local/Google/Chrome/User Data"
    chrome.mkdir(parents=True)
    motivo = preco_real.perfil_proibido(chrome, home=tmp_path)
    assert motivo
    assert "preco_real.profile_dir" in motivo


def test_um_perfil_de_dentro_do_chrome_real_tambem_e_recusado(tmp_path):
    """"Default", "Profile 1": a sessão do dono mora num SUBDIRETÓRIO do User
    Data, e é ele que alguém apontaria por engano."""
    perfil = tmp_path / "AppData/Local/Google/Chrome/User Data/Default"
    perfil.mkdir(parents=True)
    assert preco_real.perfil_proibido(perfil, home=tmp_path)


def test_o_perfil_proprio_passa(tmp_path):
    assert preco_real.perfil_proibido(tmp_path / ".cache/chrome-preco-real",
                                      home=tmp_path) == ""


def test_o_perfil_padrao_nao_fica_em_data():
    """`data/` é versionada e a produção escreve nela a cada 15 min — um perfil
    de navegador ali viraria conflito binário a cada `git pull`."""
    assert not preco_real.PERFIL_PADRAO.startswith("data/")


# -- o laço de espera ----------------------------------------------------------
#
# O bloco de preço chega VAZIO e leva de 10 a 30 s para preencher (medido em
# 2026-08-28) — um caso passou de 30 s e outro veio na primeira tentativa. Um
# `get` só não serve; um laço sem teto seria um run travado.

def _relogio(passos):
    """Um relógio de mentira que avança `passo` a cada consulta."""
    marcas = iter(passos)
    return lambda: next(marcas)


def test_espera_devolve_assim_que_o_bloco_fica_pronto():
    leituras = iter(["", "", BLOCO])
    texto, segundos = preco_real.espera_o_bloco(
        lambda: next(leituras), lambda t: "sem cupom" in t,
        teto_s=30, passo_s=1.0, sleep=lambda _: None,
        relogio=_relogio([0.0, 0.0, 1.0, 2.0, 2.0]))
    assert texto == BLOCO
    assert segundos == 2.0


def test_espera_desiste_no_teto_e_devolve_o_ultimo_que_viu():
    """Desistir é o comportamento certo: quem chama transforma isso numa recusa
    e o post publica o preço da API."""
    texto, segundos = preco_real.espera_o_bloco(
        lambda: "carregando", lambda t: False,
        teto_s=3, passo_s=1.0, sleep=lambda _: None,
        relogio=_relogio([0.0, 0.0, 1.0, 2.0, 3.0, 3.0]))
    assert texto == "carregando"
    assert segundos == 3.0


def test_espera_para_no_intersticio_sem_gastar_o_teto():
    """Esperar 30 s por um bloco que nunca vem é o custo mais caro desta rota, e
    a página já disse que não vai vir."""
    texto, segundos = preco_real.espera_o_bloco(
        lambda: "Login Necessário\nFaça login para continuar", lambda t: False,
        teto_s=30, passo_s=1.0, sleep=lambda _: None,
        relogio=_relogio([0.0, 0.5]))
    assert "Login" in texto
    assert segundos == 0.5


# -- o leitor: falha FECHADA e desarme persistente -----------------------------


def _leitor(paginas, estado=None, **kw):
    """Leitor com um navegador de mentira: `paginas` é o que cada chamada
    devolve (texto, ou uma exceção para levantar)."""
    fila = list(paginas)

    def navegador(url, pronto):
        item = fila.pop(0) if fila else fila_vazia()
        if isinstance(item, BaseException):
            raise item
        return item, 1.5

    def fila_vazia():
        raise AssertionError("o leitor abriu mais páginas do que o teste previu")

    kw.setdefault("max_falhas", 3)
    return preco_real.LeitorDePreco(navegador=navegador, estado=estado, **kw)


def _oferta(**kw):
    base = dict(price_current_cents=API,
                product_url="https://shopee.com.br/product/1/123456")
    base.update(kw)
    return make_offer(**base)


def test_a_leitura_boa_carimba_o_preco_e_a_condicao():
    leitor = _leitor([BLOCO])
    offer, leitura = leitor.aplica(_oferta())
    assert leitura.ok
    assert offer.price_checkout_cents == 52348
    assert offer.price_checkout_label == "com cupom"
    assert offer.price_current_cents == API   # o de catálogo não se mexe


def test_a_leitura_ruim_devolve_a_oferta_intacta():
    """Falhar fechado é literalmente isto: a oferta sai daqui igual a como
    entrou, e o post publica o preço da API, como hoje."""
    offer = _oferta()
    saida, leitura = _leitor(["a página mudou de layout"]).aplica(offer)
    assert saida is offer
    assert not leitura.ok


def test_o_leitor_nao_levanta_quando_o_navegador_explode():
    """Uma exceção do navegador dentro do laço de publicação derrubaria a
    oferta inteira — e ela ia publicar bem, com o preço da API."""
    offer = _oferta()
    saida, leitura = _leitor([RuntimeError("chrome morreu")]).aplica(offer)
    assert saida is offer
    assert "chrome morreu" in leitura.motivo


def test_o_ml_nao_e_lido_e_nao_gasta_navegador():
    """O preço que publicamos do Mercado Livre é o do anúncio que o nosso link
    abre — exatamente o que a página mostra. Não há o que ler, e abrir uma
    página para descobrir isso seria desperdício."""
    leitor = _leitor([])
    _, leitura = leitor.aplica(_oferta(source="meli"))
    assert leitura.motivo == preco_real.FORA_DA_SHOPEE
    assert leitor.falhas_seguidas == 0


def test_oferta_sem_url_nao_gasta_navegador():
    leitor = _leitor([])
    _, leitura = leitor.aplica(_oferta(product_url=""))
    assert leitura.motivo == preco_real.SEM_URL
    assert leitor.falhas_seguidas == 0


def test_n_falhas_seguidas_desarmam_a_leitura_e_avisam():
    leitor = _leitor(["nada", "nada", "nada"], max_falhas=3)
    for _ in range(3):
        leitor.aplica(_oferta())
    assert not leitor.disponivel
    assert any("DESARMADA" in a for a in leitor.warnings)
    # e desarmado ele não abre mais nenhuma página
    _, leitura = leitor.aplica(_oferta())
    assert leitura.motivo == preco_real.DESARMADO


def test_uma_leitura_boa_zera_o_contador():
    """Igual ao `instagram_story_link`: o desarme é por falhas SEGUIDAS. Uma
    página lenta no meio do dia não pode somar com outra de três horas antes."""
    leitor = _leitor(["nada", "nada", BLOCO, "nada"], max_falhas=3)
    for _ in range(4):
        leitor.aplica(_oferta())
    assert leitor.disponivel
    assert leitor.falhas_seguidas == 1


def test_o_intersticio_desarma_na_hora():
    """Ele não é uma leitura que deu errado: é a Shopee dizendo que não vai
    servir a página. Insistir 60 vezes por dia contra isso é o comportamento que
    esta fase existe para não ter."""
    leitor = _leitor(["Login Necessário\nFaça login para continuar"], max_falhas=3)
    leitor.aplica(_oferta())
    assert not leitor.disponivel
    assert any(preco_real.INTERSTICIO in a for a in leitor.warnings)


def test_o_desarme_sobrevive_ao_processo(tmp_path):
    """Como o `day_flags` da 5F: um processo que morre não pode zerar o
    contador — a produção roda de 15 em 15 min e recomeçaria a martelar."""
    db = StateDB(tmp_path / "s.db")
    primeiro = _leitor(["nada", "nada"], estado=db, max_falhas=3)
    primeiro.aplica(_oferta())
    primeiro.aplica(_oferta())
    assert primeiro.disponivel          # 2 de 3

    segundo = _leitor(["nada"], estado=db, max_falhas=3)
    assert segundo.falhas_seguidas == 2  # herdou o contador
    segundo.aplica(_oferta())
    assert not segundo.disponivel

    terceiro = _leitor([], estado=db, max_falhas=3)
    assert not terceiro.disponivel       # nasce fechado, sem abrir nada
    assert any("DESARMADA" in a for a in terceiro.warnings)
    db.close()


def test_o_desarme_amanhece_rearmado(tmp_path):
    """A marca é do DIA LOCAL: `day_flag` só enxerga hoje, e um desarme de
    ontem não fecha a leitura de hoje. Mesma semântica do canal de story — o
    dia amanhece rearmado sem ninguém limpar nada."""
    db = StateDB(tmp_path / "s.db")
    ontem = (db.local_today() - timedelta(days=1)).isoformat()
    db.set_day_flag(preco_real.CHAVE_DESARMADO, "desarmada ontem", day=ontem)
    db.set_day_flag(preco_real.CHAVE_FALHAS, "9", day=ontem)
    leitor = _leitor([BLOCO], estado=db)
    assert leitor.disponivel and leitor.falhas_seguidas == 0
    assert leitor.aplica(_oferta())[1].ok
    db.close()


def test_banco_quebrado_nunca_derruba_a_leitura():
    """O desarme é uma rede, não um requisito: um banco que não responde faz o
    leitor perder a memória entre processos, não parar de funcionar."""
    class Quebrado:
        def day_flag(self, *a, **kw):
            raise RuntimeError("sem banco")

        def set_day_flag(self, *a, **kw):
            raise RuntimeError("sem banco")

    leitor = _leitor([BLOCO], estado=Quebrado())
    offer, leitura = leitor.aplica(_oferta())
    assert leitura.ok and offer.price_checkout_cents == 52348


# -- a montagem: nasce DESLIGADA ----------------------------------------------

def test_sem_secao_no_config_nao_ha_leitor():
    """O estado de hoje, e o estado para o qual tudo cai: nenhum navegador é
    aberto e o pipeline é o de sempre."""
    leitor, avisos = preco_real.monta({}, estado=None)
    assert leitor is None and avisos == []


def test_desligada_no_config_nao_ha_leitor():
    leitor, _ = preco_real.monta({"preco_real": {"enabled": False}}, estado=None)
    assert leitor is None


def test_ligada_monta_o_leitor_com_o_perfil_do_config(tmp_path):
    cfg = {"preco_real": {"enabled": True, "profile_dir": str(tmp_path / "perfil"),
                          "max_falhas": 5, "timeout_s": 12}}
    leitor, avisos = preco_real.monta(cfg, estado=None)
    assert leitor is not None and avisos == []
    assert leitor.max_falhas == 5
    assert leitor.timeout_s == 12


def test_ligada_com_o_perfil_do_dono_recusa_montar(tmp_path, monkeypatch):
    """A recusa mais importante do config. Ligar a leitura apontando para o
    Chrome do dono não pode simplesmente funcionar."""
    monkeypatch.setattr(preco_real.Path, "home", staticmethod(lambda: tmp_path))
    chrome = tmp_path / "AppData/Local/Google/Chrome/User Data"
    chrome.mkdir(parents=True)
    leitor, avisos = preco_real.monta(
        {"preco_real": {"enabled": True, "profile_dir": str(chrome)}}, estado=None)
    assert leitor is None
    assert avisos and "navegador real" in avisos[0]

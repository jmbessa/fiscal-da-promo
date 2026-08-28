"""Fase 5P — a leitura do preço de checkout no navegador.

O que estes testes protegem é o `falhar fechado`: a superfície de maior risco
do projeto é publicar um número que não veio com a frase que o qualifica. Cada
recusa do parser tem teste PRÓPRIO, com o motivo por extenso, porque é a
mensagem do motivo que o dono vai ler no `afiliado preco-real`.

Nenhum teste aqui abre navegador nem toca a rede: o leitor recebe um duplo.
"""

from afiliado import preco_real

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

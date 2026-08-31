"""A narração do Reel — o áudio ORIGINAL embutido no arquivo.

Ela existe porque a Content Publishing API da Meta **não anexa áudio da
biblioteca do Instagram**: nem som em alta, nem catálogo. Num pipeline que
publica por API, ou o som vem dentro do `.mp4` ou não há som
(docs/superpowers/reviews/2026-08-31-reels-e-carrossel.md, §3).

**Nada aqui exige voz instalada.** Os testes de número e de roteiro são
aritmética e string; os que precisam do sintetizador dizem isso no `skipif`, e
o teste que importa — que a peça continua correta SEM voz — é o que roda em
qualquer máquina, inclusive no Linux do CI.
"""

import wave

import pytest

from afiliado import creative, narracao, video
from afiliado.copywriter import _PALAVRAS_DE_DESCONTO
from afiliado.models import NO_CLAIM, Offer, Verdict

SEM_VOZ = pytest.mark.skipif(
    not narracao.voz_disponivel(),
    reason="sem voz pt-BR nesta máquina — o Reel sai mudo e o pipeline segue")


def _offer(cents: int, source: str = "shopee") -> Offer:
    return Offer(source=source, item_id="1", title="Serum PDRN Copper Peptide 30ml",
                 price_original_cents=cents, price_current_cents=cents,
                 commission_pct=10.0, image_url="", product_url="https://x")


# -- o número por extenso ------------------------------------------------------

@pytest.mark.parametrize("cents,esperado", [
    (1, "um centavo"),
    (99, "noventa e nove centavos"),
    (100, "um real"),
    (199, "um real e noventa e nove centavos"),
    (2000, "vinte reais"),
    (4590, "quarenta e cinco reais e noventa centavos"),
    (10000, "cem reais"),
    (10100, "cento e um reais"),
    (11399, "cento e treze reais e noventa e nove centavos"),
    (15000, "cento e cinquenta reais"),
    (100000, "mil reais"),
    (120000, "mil e duzentos reais"),
    (123456, "mil duzentos e trinta e quatro reais e cinquenta e seis centavos"),
])
def test_o_preco_e_escrito_por_extenso_em_portugues(cents, esperado):
    """Escrito à mão, e não deixado para o sintetizador: a leitura de número é
    a parte que mais varia entre vozes, e "R$ 113,99" vira coisa diferente em
    cada uma. Aqui é determinístico — e as regras do "e" do português (cem vs
    cento, mil E duzentos vs mil duzentos e trinta) estão travadas."""
    assert narracao.reais_por_extenso(cents) == esperado


def test_preco_zero_ou_negativo_nao_quebra_a_frase():
    """Preço ilegível é defeito de outro lugar; aqui ele não pode virar uma
    exceção que derruba o Reel nem uma frase sem substantivo."""
    assert narracao.reais_por_extenso(0) == "zero reais"
    assert narracao.reais_por_extenso(-500) == "zero reais"


# -- o roteiro -----------------------------------------------------------------

def test_o_roteiro_diz_preco_loja_e_assinatura():
    texto = narracao.roteiro(_offer(11399), NO_CLAIM)
    assert "cento e treze reais e noventa e nove centavos" in texto
    assert "na Shopee" in texto
    assert texto.endswith(creative.ASSINATURA)


def test_o_roteiro_sabe_de_qual_loja_e_a_oferta():
    assert "no Mercado Livre" in narracao.roteiro(_offer(4590, "meli"), NO_CLAIM)
    assert "na Shopee" in narracao.roteiro(_offer(4590, "shopee"), NO_CLAIM)


def test_o_roteiro_NAO_le_o_titulo_do_anuncio():
    """Nome de anúncio de marketplace lido em voz alta é ruído — e ele já está
    na tela, em corpo grande. O que a voz acrescenta é o preço e a assinatura."""
    offer = _offer(11399)
    assert "PDRN" not in narracao.roteiro(offer, NO_CLAIM)
    assert "Copper" not in narracao.roteiro(offer, NO_CLAIM)


def test_em_modo_B_a_voz_NAO_alega_desconto():
    """A régua vale para a voz igual vale para a legenda: sem referência
    medida, não se afirma desconto. Este teste usa o MESMO regex do
    `copywriter` — se a lista de palavras proibidas crescer, ela cresce para a
    narração junto, sem ninguém precisar lembrar."""
    texto = narracao.roteiro(_offer(11399), NO_CLAIM)
    assert not _PALAVRAS_DE_DESCONTO.search(texto)
    assert "por cento" not in texto


def test_em_modo_A_a_voz_diz_a_porcentagem_DO_VEREDITO():
    """E ela é a do veredito, nunca a do vendedor — a mesma regra do badge da
    arte e da linha "De/Por" do texto."""
    texto = narracao.roteiro(_offer(11399), Verdict("A", 22, "", 90))
    assert "vinte e dois por cento abaixo da nossa referência" in texto


def test_modo_A_sem_porcentagem_nao_inventa_a_frase():
    """`discount_pct == 0` em modo A é estado impossível pela `pricing`, mas se
    chegar aqui a voz cala a alegação em vez de dizer "zero por cento"."""
    texto = narracao.roteiro(_offer(11399), Verdict("A", 0, "", 90))
    assert "por cento" not in texto


# -- a peça sem voz: o caminho que TEM de funcionar em qualquer máquina --------

def test_sem_narracao_o_clipe_tem_a_duracao_de_sempre():
    assert creative.duracao_do_clipe(None) == creative.REEL_DURACAO_S
    assert creative.duracao_do_clipe(b"") == creative.REEL_DURACAO_S
    # WAV ilegível também: degrada para o piso, não levanta.
    assert creative.duracao_do_clipe(b"nao sou um wav") == creative.REEL_DURACAO_S


def test_o_comando_sem_audio_continua_pedindo_a_faixa_silenciosa():
    """O Reel mudo continua sendo um Reel correto: a especificação de Reels da
    Meta lista AAC entre os requisitos e não diz o que acontece sem faixa
    nenhuma, e 2 kb/s é barato demais para descobrir do jeito caro."""
    cmd = video.comando_h264("/bin/ffmpeg", 1080, 1920, 24, "saida.mp4")
    assert "anullsrc=channel_layout=stereo:sample_rate=48000" in cmd
    assert "-af" not in cmd


# -- a peça COM voz ------------------------------------------------------------

def _wav(segundos: float) -> bytes:
    """Um WAV de silêncio com a duração pedida — dublê, para os testes de
    dimensionamento não dependerem de voz instalada."""
    import io
    buf = io.BytesIO()
    with wave.open(buf, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(22050)
        f.writeframes(b"\x00\x00" * int(22050 * segundos))
    return buf.getvalue()


def test_a_fala_dimensiona_o_clipe_e_nao_o_contrario():
    """A direção que importa: espremer a locução num clipe de tamanho fixo é
    como se corta uma sílaba. O clipe cresce para caber a fala mais os dois
    respiros — o da frente, que o `adelay` aplica, e o do fim."""
    fala = 7.5
    esperado = video.RESPIRO_ANTES_DA_VOZ_S + fala + creative.REEL_RESPIRO_DEPOIS_S
    assert creative.duracao_do_clipe(_wav(fala)) == pytest.approx(esperado)
    assert esperado > creative.REEL_DURACAO_S       # esta fala não cabia no piso


def test_uma_fala_curta_nao_encolhe_o_clipe_abaixo_do_piso():
    """8 s é o piso porque o watch time medido de Reel é 8,5 s e o sinal de
    ranqueamento é completion: a peça cabe dentro da atenção média e entra em
    loop. Uma fala de 3 s não é motivo para um clipe de 4,3 s."""
    assert creative.duracao_do_clipe(_wav(3.0)) == creative.REEL_DURACAO_S


def test_uma_fala_longa_demais_bate_no_teto():
    """Acima de 15 s a conclusão despenca. Se um roteiro pedir mais que isso, o
    defeito é do ROTEIRO — e o clipe não vai atrás dele."""
    assert creative.duracao_do_clipe(_wav(60.0)) == creative.REEL_DURACAO_MAX_S


def test_o_comando_com_audio_normaliza_atrasa_e_estende_a_voz():
    """`loudnorm` porque o WAV cru do SAPI sai com pico MEDIDO em -8,4 dB e a
    peça seria a única do feed que exige subir o volume. `adelay` porque a voz
    no frame zero perde a primeira sílaba. `apad` porque sem ele o `-shortest`
    encerraria o arquivo quando a VOZ acabasse, e o áudio passaria a mandar no
    vídeo — o contrário do que se quer.

    A ORDEM é parte do contrato: `apad` depois do `loudnorm`, senão a
    normalização mediria um áudio cheio de silêncio de enchimento."""
    cmd = video.comando_h264("/bin/ffmpeg", 1080, 1920, 24, "saida.mp4",
                             audio="narracao.wav")
    assert "anullsrc" not in " ".join(cmd)
    filtro = cmd[cmd.index("-af") + 1]
    assert filtro == (f"loudnorm=I={video.VOZ_LUFS}:TP={video.VOZ_PICO_DB}:LRA=11,"
                      f"adelay={round(video.RESPIRO_ANTES_DA_VOZ_S * 1000)}:all=1,apad")
    assert filtro.index("loudnorm") < filtro.index("apad")
    assert "-shortest" in cmd
    assert cmd[cmd.index("-i", cmd.index("-i") + 1) + 1] == "narracao.wav"


def test_a_duracao_do_mp4_e_lida_do_arquivo_sem_ffprobe():
    """O `imageio-ffmpeg` entrega o ffmpeg e mais nada: depender do ffprobe
    seria trocar um extra que existe por um binário que pode não existir."""
    assert video.duracao_mp4(b"") == 0.0
    assert video.duracao_mp4(b"nao sou um mp4") == 0.0


# -- e a voz de verdade, quando a máquina tem uma ------------------------------

def test_o_interruptor_da_voz_esta_desligado_e_o_codigo_diz_por_que():
    """Reprovada pelo dono em 2026-08-31, no dia em que ligou — e por DUAS
    razões, das quais a segunda é a que pesa: timbre artificial (que um motor
    melhor resolve) e, principalmente, "somente o post de anúncio do produto
    com uma voz não é chamativo" (que motor nenhum resolve).

    O módulo fica inteiro: roteiro, número por extenso e dimensionamento do
    clipe pela fala valem para qualquer motor. O que sai é o interruptor — e
    com ele desligado o Reel usa o mesmo caminho de uma máquina sem voz."""
    assert narracao.VOZ_LIGADA is False
    assert narracao.narra(_offer(11399), NO_CLAIM) is None


@SEM_VOZ
def test_a_voz_sintetiza_o_roteiro_num_wav_legivel(monkeypatch):
    """O motor continua funcionando por baixo do interruptor — é o que permite
    religar sem redescobrir nada."""
    monkeypatch.setattr(narracao, "VOZ_LIGADA", True)
    wav = narracao.narra(_offer(11399), NO_CLAIM)
    assert wav is not None
    duracao = video.duracao_wav(wav)
    # Cabe no teto do clipe com os dois respiros — se não coubesse, todo Reel
    # sairia no teto de 15 s com a voz cortada.
    assert 0 < duracao < creative.REEL_DURACAO_MAX_S


@SEM_VOZ
def test_texto_vazio_nao_vira_wav():
    assert narracao.sintetiza("") is None
    assert narracao.sintetiza("   ") is None

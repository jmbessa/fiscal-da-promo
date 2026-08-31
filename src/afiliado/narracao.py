"""A narração do Reel — áudio ORIGINAL, embutido no arquivo antes do upload.

Por que isto existe, e por que é a única forma possível: **a Content Publishing
API da Meta não anexa áudio da biblioteca do Instagram.** Nem som em alta, nem
catálogo, nem efeito. Se o Reel precisa de som, ele vem dentro do `.mp4`. Ou
seja, a faixa de descoberta por áudio — a página do som, que é um canal de
distribuição próprio — está fechada para qualquer pipeline automatizado, e
música licenciada embutida à mão seria problema de licença nosso.

O que sobra é áudio original, e ele não é um prêmio de consolação: voz própria
é justamente o que a Meta nomeia como edição material quando fala da regra de
originalidade de 30/04/2026, e as fontes convergem em que áudio original não é
rebaixado. Silêncio não é penalizado — só não entrega nada.
(docs/superpowers/reviews/2026-08-31-reels-e-carrossel.md, §3.)

**A voz é a do sistema, e isso é de propósito.** SAPI5 no Windows, pelo
`System.Speech` que já vem com a máquina: offline, sem custo, sem chave, sem
chamada de rede a serviço nenhum e sem dúvida de licença. Nada disso vale para
as alternativas neurais — a boa (`edge-tts`) é um endpoint não oficial da
Microsoft e a ótima é paga.

**Degrada como o ffmpeg degrada.** Sem voz pt-BR instalada, fora do Windows, ou
com o PowerShell mudo, `sintetiza` devolve `None` e o Reel sai como sempre saiu:
com a faixa silenciosa. Nenhum teste da suíte precisa de voz para passar, e
nenhum caminho aqui derruba um run.
"""

from __future__ import annotations

import functools
import subprocess
import tempfile
from pathlib import Path

from afiliado import creative
from afiliado.models import Offer, Verdict

__all__ = ["roteiro", "reais_por_extenso", "voz_disponivel", "sintetiza", "narra",
           "VELOCIDADE"]


# A velocidade do SAPI, de -10 a 10. Medido nesta máquina com a voz
# `Microsoft Maria Desktop`, sobre o roteiro real: 0 dá 8,0 s e estoura o clipe
# de 8 s; 2 dá 6,5 s e cabe com respiro; 3 dá 5,8 s e já soa apressado. O 2 é o
# ponto em que a frase inteira cabe sem a locução virar corrida.
VELOCIDADE = 2

# Quanto tempo o PowerShell tem para sintetizar. Uma frase de ~7 s leva menos
# de 2 s para gerar; 30 s é folga para uma máquina ocupada, e o estouro vira
# `None` (Reel mudo), nunca run derrubado.
TIMEOUT_S = 30

_UNIDADES = ("zero", "um", "dois", "três", "quatro", "cinco", "seis", "sete", "oito",
             "nove", "dez", "onze", "doze", "treze", "catorze", "quinze", "dezesseis",
             "dezessete", "dezoito", "dezenove")
_DEZENAS = ("", "", "vinte", "trinta", "quarenta", "cinquenta", "sessenta",
            "setenta", "oitenta", "noventa")
_CENTENAS = ("", "cento", "duzentos", "trezentos", "quatrocentos", "quinhentos",
             "seiscentos", "setecentos", "oitocentos", "novecentos")


def _ate_999(n: int) -> str:
    if n == 100:
        return "cem"          # "cento" só existe acompanhado: cento e um
    partes = []
    centenas, resto = divmod(n, 100)
    if centenas:
        partes.append(_CENTENAS[centenas])
    dezenas, unidades = divmod(resto, 10)
    if dezenas >= 2:
        partes.append(_DEZENAS[dezenas] + (f" e {_UNIDADES[unidades]}" if unidades else ""))
    elif resto:
        partes.append(_UNIDADES[resto])
    return " e ".join(partes)


def _inteiro(n: int) -> str:
    if n < 1000:
        return _ate_999(n)
    milhares, resto = divmod(n, 1000)
    texto = "mil" if milhares == 1 else f"{_ate_999(milhares)} mil"
    if not resto:
        return texto
    # O "e" do português: entra quando o resto é menor que cem ou é centena
    # redonda (mil e duzentos), e não entra no resto composto (mil duzentos e
    # trinta).
    liga = " e " if resto < 100 or resto % 100 == 0 else " "
    return texto + liga + _ate_999(resto)


def reais_por_extenso(cents: int) -> str:
    """`11399` -> "cento e treze reais e noventa e nove centavos".

    Escrito à mão, e não deixado para o sintetizador, porque a leitura de
    número é a parte que MAIS varia entre vozes e engines — a mesma string
    "R$ 113,99" vira "erre cifrão cento e treze vírgula noventa e nove" numa
    voz e outra coisa na seguinte. Aqui ela é determinística e testável.
    """
    if cents < 0:
        cents = 0
    reais, centavos = divmod(cents, 100)
    partes = []
    if reais:
        partes.append(f"{_inteiro(reais)} {'real' if reais == 1 else 'reais'}")
    if centavos:
        partes.append(f"{_inteiro(centavos)} {'centavo' if centavos == 1 else 'centavos'}")
    if not partes:
        return "zero reais"
    return " e ".join(partes)


def roteiro(offer: Offer, verdict: Verdict) -> str:
    """O que a voz diz — o preço, a loja e a assinatura da marca.

    O que ela NÃO diz é o título do produto: um nome de anúncio de marketplace
    ("[NEW] DR.REJUALL Advanced PDRN Copper Peptide Serum, 30ml") lido em voz
    alta é ruído, e ele já está escrito na tela em corpo grande.

    A alegação segue a mesma régua de todo o resto: a porcentagem só é dita em
    modo A, onde ela é a do VEREDITO — nunca a do vendedor. Em modo B a voz não
    afirma desconto nenhum, exatamente como a legenda não afirma.
    """
    loja = "no Mercado Livre" if offer.source == "meli" else "na Shopee"
    frases = []
    if verdict.mode == "A" and verdict.discount_pct > 0:
        frases.append(f"{_inteiro(verdict.discount_pct)} por cento abaixo "
                      f"da nossa referência.")
    frases.append(f"{reais_por_extenso(offer.published_price_cents)}, {loja}.")
    frases.append(creative.ASSINATURA)
    return " ".join(frases)


# O script do PowerShell. O TEXTO vai pela entrada padrão, nunca interpolado no
# comando: título de produto tem aspas, cifrão e crase, e qualquer um deles
# dentro de um `-Command` vira execução ou erro de parser.
_PS = """
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$voz = $s.GetInstalledVoices() |
    Where-Object { $_.VoiceInfo.Culture.Name -like 'pt*' } |
    Select-Object -First 1
if (-not $voz) { exit 3 }
$s.SelectVoice($voz.VoiceInfo.Name)
$s.Rate = %(rate)d
$s.SetOutputToWaveFile('%(destino)s')
$s.Speak([Console]::In.ReadToEnd())
$s.SetOutputToNull()
$s.Dispose()
"""


def _powershell(texto: str, destino: Path, rate: int) -> bool:
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             _PS % {"rate": rate, "destino": str(destino).replace("'", "''")}],
            input=texto, capture_output=True, text=True, encoding="utf-8",
            timeout=TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return False
    return proc.returncode == 0 and destino.is_file() and destino.stat().st_size > 44


@functools.lru_cache(maxsize=1)
def voz_disponivel() -> bool:
    """Existe voz pt-BR nesta máquina?

    Em cache: a resposta não muda durante um run e descobri-la custa um
    processo de PowerShell inteiro — caro demais para repetir a cada peça.
    """
    return sintetiza("Teste.") is not None


def sintetiza(texto: str) -> bytes | None:
    """Os bytes do WAV com `texto` falado, ou `None` quando não dá.

    `None` não é erro: é "esta máquina não fala". Quem chama monta o Reel com a
    faixa silenciosa de sempre.
    """
    if not texto.strip():
        return None
    with tempfile.TemporaryDirectory(prefix="afiliado-tts-") as tmp:
        destino = Path(tmp) / "narracao.wav"
        if not _powershell(texto, destino, VELOCIDADE):
            return None
        return destino.read_bytes()


def narra(offer: Offer, verdict: Verdict) -> bytes | None:
    """O WAV da peça, do roteiro à síntese — ou `None` quando não há voz.

    É esta a função que os canais chamam: `roteiro` e `sintetiza` continuam
    públicas porque o teste as usa separadas, mas quem publica não precisa
    saber que são duas.
    """
    return sintetiza(roteiro(offer, verdict))

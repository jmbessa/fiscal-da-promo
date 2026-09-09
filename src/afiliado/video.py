"""Fase 5T — o encode H.264, a única parte do Reel que não é Pillow.

O projeto desenha tudo com Pillow e não vai parar: os frames do Reel são a
mesma arte de story que o design system já produz (`creative.reel_frames`).
O que Pillow não faz é EMPACOTAR esses frames num `.mp4` que a aba Reels
aceite — 9:16, entre 5 e 90 s, H.264 ou HEVC. Isso é ffmpeg.

**Este módulo é um extra OPCIONAL, no molde do `playwright` da fase 5P.** O
`imageio-ffmpeg` entra em `pyproject.toml` como `[reel]`, o import é
preguiçoso e quem não o instalar tem o pipeline INTEIRO funcionando: o canal
`instagram_reel` não sobe, o run avisa uma vez, e nada mais muda. Nenhum teste
da suíte precisa de ffmpeg para passar.

Duas fontes de binário, nesta ordem:

1. o do extra (`imageio_ffmpeg.get_ffmpeg_exe()`) — versão conhecida, baixada
   pelo pip, igual na máquina do dono e em qualquer CI;
2. um `ffmpeg` no PATH — quem já tem um ffmpeg do sistema não precisa baixar
   outro de 25 MB só para rodar este canal.

O comando é montado à mão (e não pelo `write_frames` do imageio-ffmpeg) por
dois motivos que valem o custo: a trilha de ÁUDIO SILENCIOSA e o `-movflags
+faststart`, que põe o `moov` na frente para a Meta não precisar baixar o
arquivo todo antes de começar.

A faixa silenciosa é SEGURO, não fato medido: a especificação de Reels da Meta
lista AAC entre os requisitos de áudio e não diz o que acontece com um arquivo
sem faixa nenhuma. Custa 2 kb/s não descobrir isso do jeito caro.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Iterable

__all__ = ["SemFFmpeg", "SEM_FFMPEG", "AVISO_SEM_FFMPEG", "LIMITE_TELEGRAM_BYTES",
           "RESPIRO_ANTES_DA_VOZ_S", "ffmpeg_exe", "tem_ffmpeg", "comando_h264",
           "duracao_wav", "duracao_mp4", "encode_h264"]


SEM_FFMPEG = ("ffmpeg não encontrado — `pip install -e .[reel]` (ou um ffmpeg no "
              "PATH); ver docs/runbooks/meta-setup.md")
AVISO_SEM_FFMPEG = ("⚠️ canal instagram_reel ignorado: ffmpeg não encontrado — rode "
                    "`pip install -e .[reel]` (ou ponha um ffmpeg no PATH); ver "
                    "docs/runbooks/meta-setup.md")

# O bot do Telegram — que é a nossa hospedagem temporária (`sendVideo` +
# `getFile`, o mesmo caminho da arte de imagem) — só BAIXA arquivos de até
# 20 MB. Um clipe de 8 s em 1080×1920 H.264 fica uma ordem de grandeza abaixo;
# se algum dia passar disso, o defeito é do GERADOR, não da hospedagem.
LIMITE_TELEGRAM_BYTES = 20 * 1024 * 1024

# Qualidade do H.264. `crf 23` é o padrão do x264 e o ponto em que um clipe de
# 8 s desta arte (fundo chapado, tipografia grande) sai com poucos MB; `preset
# veryfast` porque o gargalo aqui é o tempo de RENDER dos frames, não o do
# encode, e um preset lento só faria o run esperar mais pelo mesmo arquivo.
CRF = "23"
PRESET = "veryfast"

# O silêncio na frente da narração. A voz começando no frame zero perde a
# primeira sílaba em quase todo player, e a peça já está inteira na tela nesse
# instante: quem chega tem o preço para ler antes de a voz falar.
RESPIRO_ANTES_DA_VOZ_S = 0.3

# Normalização de volume da voz (EBU R128, uma passada). Sem ela o WAV que o
# SAPI devolve sai com pico MEDIDO em -8,4 dB e volume médio em -28,6 dB — uma
# peça audível só no talo, ao lado de Reels que a plataforma entrega perto de
# -14 LUFS. O alvo é esse; `TP=-1.5` guarda margem de pico para o AAC, que
# pode passar do original ao recodificar.
VOZ_LUFS = "-14"
VOZ_PICO_DB = "-1.5"


class SemFFmpeg(RuntimeError):
    """Não há ffmpeg nesta máquina.

    Não é um erro do vídeo: é a ausência de um extra opcional. Quem chama
    transforma isso em canal desarmado com aviso — nunca em run derrubado.
    """


def _do_extra() -> str:
    """O ffmpeg que veio com `pip install -e .[reel]`, ou "".

    Função separada (e não um `try/import` embutido) para o teste poder dizer
    "esta máquina não tem o extra" sem desinstalar nada.
    """
    try:
        import imageio_ffmpeg
    except ImportError:
        return ""
    try:
        caminho = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:      # noqa: BLE001 - binário não baixado/plataforma sem build
        return ""
    return caminho if caminho and Path(caminho).exists() else ""


def ffmpeg_exe() -> str:
    """O caminho do ffmpeg. Levanta `SemFFmpeg` quando não há nenhum."""
    do_extra = _do_extra()
    if do_extra:
        return do_extra
    do_path = shutil.which("ffmpeg")
    if do_path:
        return do_path
    raise SemFFmpeg(SEM_FFMPEG)


def tem_ffmpeg() -> bool:
    """Dá para gerar Reel nesta máquina? É o que arma ou desarma o canal."""
    try:
        ffmpeg_exe()
    except SemFFmpeg:
        return False
    return True


def duracao_wav(wav: bytes) -> float:
    """A duração de um WAV, lida do cabeçalho. `0.0` quando ilegível.

    Mora aqui, e não no `afiliado.narracao`, porque quem precisa dela é quem
    dimensiona o CLIPE (`creative.render_reel`) — e `creative` não importa
    `narracao`: é `narracao` que importa `creative`, e o contrário fecharia o
    ciclo.
    """
    try:
        with wave.open(io.BytesIO(wav), "rb") as f:
            taxa = f.getframerate()
            return f.getnframes() / taxa if taxa else 0.0
    except (wave.Error, EOFError, OSError):
        return 0.0


def duracao_mp4(mp4: bytes) -> float:
    """A duração do `.mp4`, lida da caixa `mvhd`. `0.0` quando ilegível.

    Sem ffprobe de propósito: o `imageio-ffmpeg` entrega o **ffmpeg** e mais
    nada, então depender do ffprobe seria trocar um extra opcional que existe
    por um binário que pode não existir. A `mvhd` tem `timescale` e `duration`
    em posição fixa, e o `+faststart` já a põe na frente do arquivo.
    """
    i = mp4.find(b"mvhd")
    if i < 0 or len(mp4) < i + 32:
        return 0.0
    versao = mp4[i + 4]
    # Depois de "mvhd" vêm versão(1) + flags(3); então as datas, cujo tamanho é
    # que muda entre as versões.
    base = i + 8 + (16 if versao == 1 else 8)
    largo = 8 if versao == 1 else 4
    if len(mp4) < base + 4 + largo:
        return 0.0
    escala = int.from_bytes(mp4[base:base + 4], "big")
    duracao = int.from_bytes(mp4[base + 4:base + 4 + largo], "big")
    return duracao / escala if escala else 0.0


def comando_h264(exe: str, largura: int, altura: int, fps: int, destino,
                 audio=None) -> list[str]:
    """O comando que transforma frames RGB24 crus (pela entrada padrão) no
    `.mp4` que a aba Reels aceita.

    `yuv420p` e não o 4:4:4 que o libx264 escolheria sozinho para uma entrada
    RGB: é o único subamostramento que todo player reproduz, e um Reel que não
    abre no celular de quem viu não vale nada. `-profile:v high -level 4.0` é a
    combinação que a Meta documenta para 1080p.

    **A faixa de áudio tem dois modos.** Com `audio` (o WAV da narração) ela é
    a voz; sem ele, a faixa SILENCIOSA de sempre (`anullsrc`) — ver o cabeçalho
    do módulo: é seguro barato contra um requisito que a Meta lista e não
    explica. O Reel mudo continua sendo um Reel correto.

    Os três filtros da narração não são enfeite:

    - `loudnorm` põe a voz no volume em que a plataforma entrega o resto. O WAV
      cru do SAPI sai com pico medido em -8,4 dB; sem normalizar, a peça é a
      única do feed que exige subir o volume do aparelho.
    - `adelay` dá a ela um respiro na frente. A locução começando no frame zero
      corta a primeira sílaba em quase todo player, e a peça já está inteira na
      tela nesse instante — a voz não precisa competir com ela.
    - `apad` a estende com silêncio até o fim do vídeo. Sem ele, `-shortest`
      encerraria o arquivo quando a VOZ acabasse, e o clipe seria truncado no
      meio da arte — o áudio mandaria no vídeo, que é o contrário do que se
      quer. Por isso ele vem DEPOIS do `loudnorm`: normalizar um áudio já
      cheio de silêncio de enchimento mediria a coisa errada.
    """
    entrada_de_audio = (["-i", str(audio)] if audio else
                        ["-f", "lavfi", "-i",
                         "anullsrc=channel_layout=stereo:sample_rate=48000"])
    filtro = ["-af", f"loudnorm=I={VOZ_LUFS}:TP={VOZ_PICO_DB}:LRA=11,"
                     f"adelay={round(RESPIRO_ANTES_DA_VOZ_S * 1000)}:all=1,apad"] if audio else []
    return [
        exe, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{largura}x{altura}",
        "-r", str(fps), "-i", "-",
        *entrada_de_audio,
        *filtro,
        "-shortest",
        "-c:v", "libx264", "-preset", PRESET, "-crf", CRF,
        "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.0",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        str(destino),
    ]


def encode_h264(frames: Iterable[bytes], size: tuple[int, int], fps: int,
                exe: str | None = None, narracao: bytes | None = None) -> bytes:
    """Os bytes do `.mp4`. `frames` são RGB24 crus, um por frame.

    Os frames vão para a entrada padrão do ffmpeg à medida que o gerador os
    produz — nunca todos de uma vez. Um clipe de 8 s a 24 fps em 1080×1920 são
    192 × 6,2 MB = **1,2 GB** se alguém os materializar numa lista; assim, a
    memória de pico é um frame.

    O arquivo é escrito em disco (temporário) e lido de volta em vez de sair
    pela saída padrão: `+faststart` reescreve o `moov` no começo do arquivo e
    para isso precisa de uma saída com `seek` — num pipe, o ffmpeg calaria o
    faststart ou pediria fragmentação.
    """
    exe = exe or ffmpeg_exe()
    largura, altura = size
    with tempfile.TemporaryDirectory(prefix="afiliado-reel-") as tmp:
        destino = Path(tmp) / "reel.mp4"
        # A narração vai a DISCO e não pela entrada padrão: essa já é dos
        # frames, e o ffmpeg precisa de `seek` no WAV para ler o cabeçalho.
        audio = None
        if narracao:
            audio = Path(tmp) / "narracao.wav"
            audio.write_bytes(narracao)
        proc = subprocess.Popen(comando_h264(exe, largura, altura, fps, destino, audio),
                                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE)
        erro = b""
        try:
            for frame in frames:
                proc.stdin.write(frame)
        except BrokenPipeError:
            # O ffmpeg morreu no meio: a mensagem dele é a causa, não o pipe.
            pass
        finally:
            try:
                proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass
            erro = proc.stderr.read() or b""
            proc.stderr.close()
            codigo = proc.wait()
        if codigo != 0 or not destino.is_file():
            texto = erro.decode("utf-8", "replace").strip() or f"código {codigo}"
            raise SemFFmpeg(f"ffmpeg falhou ao gerar o vídeo: {texto}")
        return destino.read_bytes()

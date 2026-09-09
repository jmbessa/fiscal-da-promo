"""Fase 5T — o Reel: a arte do story, animada, em .mp4 H.264.

Por que este formato existe: em 2026-08-29 o perfil tinha 5 posts, **0 do tipo
REEL** e alcance de **1 conta** em 7 dias. O feed serve quem já segue; o Reel é
o mecanismo que entrega a quem NÃO segue. Sem ele o sistema publica para 2
pessoas por construção (ver `docs/feed.md`).

Nada aqui toca a rede: a foto do produto vem por `httpx.MockTransport`. O
encode chama o ffmpeg LOCAL (o do extra `reel` ou o do PATH) — os testes que
dependem dele dizem isso no `skipif`, e os outros provam o comando e os frames
sem ffmpeg nenhum.
"""

import io

import httpx
import pytest
from PIL import Image, ImageChops, ImageDraw

from afiliado import video
from afiliado.creative import (
    DEFAULT_BRAND_NAME,
    REEL_DURACAO_S,
    REEL_FPS,
    REEL_PULSO_DURACAO,
    REEL_PULSO_ESCALA,
    REEL_PULSO_INICIO,
    REEL_SIZE,
    REEL_ZOOM,
    STORY_SIZE,
    _corpo_do_reel,
    _pulso,
    _story_canvas,
    _story_plan,
    reel_frames,
    reel_plan,
    render_reel,
    render_story,
    story_plan,
)
from afiliado.models import NO_CLAIM, CopyParts, Verdict
from tests.test_models import make_offer, make_offer_ref

COPY = CopyParts(headline="Confira essa oferta", description="Aproveite agora", cta="Compre já")
SELO_6M = Verdict("B", 0, "🏷️ Menor preço dos últimos 6 meses (verificado)", 180)

SEM_FFMPEG = pytest.mark.skipif(
    not video.tem_ffmpeg(),
    reason="sem ffmpeg nesta máquina (extra opcional `reel`) — o canal se desarma, "
           "e a suíte roda sem ele")


def _product_png() -> bytes:
    img = Image.new("RGB", (800, 800), (120, 40, 200))
    # Um quadrado no canto para o zoom ter o que mover: foto de cor chapada é
    # idêntica em qualquer escala e não provaria animação nenhuma.
    img.paste(Image.new("RGB", (200, 200), (255, 255, 0)), (40, 40))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _client() -> httpx.Client:
    def handler(request):
        return httpx.Response(200, content=_product_png(),
                              headers={"content-type": "image/png"})

    return httpx.Client(transport=httpx.MockTransport(handler))


def _frames(indices, **kw) -> dict[int, Image.Image]:
    """Só os frames pedidos, materializados. `reel_frames` é um GERADOR de
    propósito: 192 frames de 1080×1920 são 1,2 GB se virarem lista."""
    alvo = set(indices)
    saida: dict[int, Image.Image] = {}
    for i, frame in enumerate(reel_frames(make_offer_ref(29999), SELO_6M,
                                          client=_client(), handle="@ofiscaldapromo", **kw)):
        if i in alvo:
            saida[i] = frame.copy()
        if len(saida) == len(alvo):
            break
    return saida


def _mudou(a: Image.Image, b: Image.Image, caixa) -> bool:
    return ImageChops.difference(a.crop(caixa), b.crop(caixa)).getbbox() is not None


# -- 1. o formato: 9:16, 5 a 90 s ----------------------------------------------

def test_o_reel_e_9x16_no_tamanho_do_story():
    """Requisito de elegibilidade da aba Reels. Reaproveita o tamanho do story
    porque ele JÁ é 1080×1920 — não se inventa formato novo."""
    assert REEL_SIZE == STORY_SIZE == (1080, 1920)
    plan = reel_plan(make_offer_ref(29999), SELO_6M, handle="@ofiscaldapromo")
    assert (plan["width"], plan["height"]) == (1080, 1920)
    assert plan["width"] * 16 == plan["height"] * 9


def test_a_duracao_cabe_na_faixa_da_aba_reels():
    """5 a 90 s. 7-8 s é o alvo do brief: tempo de dizer a oferta e voltar ao
    começo — o loop é o que a aba premia."""
    assert 5 <= REEL_DURACAO_S <= 90
    assert 7 <= REEL_DURACAO_S <= 8
    plan = reel_plan(make_offer_ref(29999), SELO_6M)
    assert plan["duracao_s"] == REEL_DURACAO_S
    assert plan["frames"] == round(REEL_FPS * REEL_DURACAO_S)
    assert plan["codec"] == "h264"


def test_o_plano_do_reel_e_o_do_story_sem_um_pixel_de_diferenca():
    """"Não invente layout novo": o design system foi calibrado em pixel várias
    vezes. O que o Reel acrescenta é TEMPO, não desenho — selo, badge,
    riscado, rótulo, linhas do título e meta saem idênticos ao story."""
    for offer, verdict in ((make_offer_ref(29999), SELO_6M),
                           (make_offer(), NO_CLAIM),
                           (make_offer(source="meli", title="A" * 90), SELO_6M)):
        do_story = story_plan(offer, verdict, handle="@ofiscaldapromo")
        do_reel = reel_plan(offer, verdict, handle="@ofiscaldapromo")
        assert {k: do_reel[k] for k in do_story} == do_story


# -- 2. os frames ---------------------------------------------------------------

def test_reel_frames_e_um_gerador_de_imagens_1080x1920():
    frames = reel_frames(make_offer_ref(29999), SELO_6M, client=_client())
    assert not isinstance(frames, list)         # 192 × 6,2 MB não cabem na memória
    primeiro = next(iter(frames))
    assert primeiro.size == REEL_SIZE
    assert primeiro.mode == "RGB"               # rgb24 cru é o que o ffmpeg recebe


def test_o_numero_de_frames_e_o_da_duracao_vezes_o_fps():
    n = sum(1 for _ in reel_frames(make_offer_ref(29999), SELO_6M, client=_client(),
                                   duracao_s=0.5, fps=10))
    assert n == 5


def test_a_foto_do_produto_da_um_zoom_lento_ao_longo_do_clipe():
    """1,00 → 1,08. É o movimento que corre o clipe inteiro — o pulso da pill
    é o outro, e ele dura 0,6 s."""
    assert REEL_ZOOM == 1.08
    ultimo = round(REEL_FPS * REEL_DURACAO_S) - 1
    frames = _frames([0, 1, ultimo])
    card = (72, 224, 72 + 936, 224 + 790)
    assert _mudou(frames[0], frames[1], card)          # já se mexe no 2º frame
    assert _mudou(frames[0], frames[ultimo], card)


def test_o_frame_zero_JA_E_a_peca_inteira():
    """**A trava desta peça.** O frame zero tem de ser a arte de story completa
    — título, preço, prova social e selo todos no lugar —, e não a peça se
    montando.

    Até 2026-08-31 não era: o corpo entrava em cascata e o PREÇO, que é o único
    motivo pelo qual alguém pararia, só terminava de aparecer em 1,4 s. Isso
    joga fora exatamente a janela que a Meta passou a medir — ela trocou
    `view rate` por `skip rate` (os 3 primeiros segundos) em abril/2026, e
    watch time + completion é o sinal de ranqueamento que Mosseri nomeou em
    janeiro/2026.

    O frame zero é também a CAPA na grade do perfil quando nenhuma é escolhida,
    então uma peça vazia ali custa duas vezes.
    """
    frame = _frames([0])[0]
    arte = Image.open(io.BytesIO(render_story(
        make_offer_ref(29999), COPY, SELO_6M, client=_client(),
        handle="@ofiscaldapromo"))).convert("RGB")
    # O corpo inteiro, do título ao selo — idêntico à arte de story no INSTANTE
    # ZERO. (A foto não entra: ela abre em zoom 1,00 e a arte parada também,
    # mas o card tem o seu próprio teste.)
    assert not _mudou(frame, arte, (0, 1030, 1080, 1600))


def test_o_pulso_da_pill_comeca_e_termina_em_um():
    """Meia onda de seno: 1,0 nas bordas, com derivada zero, e o pico no meio.
    Rampa linear daria solavanco ao entrar e ao sair."""
    assert _pulso(REEL_PULSO_INICIO) == 1.0
    assert _pulso(REEL_PULSO_INICIO + REEL_PULSO_DURACAO) == 1.0
    assert _pulso(0.0) == 1.0 and _pulso(REEL_DURACAO_S) == 1.0
    meio = REEL_PULSO_INICIO + REEL_PULSO_DURACAO / 2
    assert _pulso(meio) == pytest.approx(REEL_PULSO_ESCALA)
    # Nunca encolhe: o pulso chama atenção, não esconde.
    assert all(_pulso(i / 100) >= 1.0 for i in range(0, int(REEL_DURACAO_S * 100)))


def test_a_pill_pulsa_e_o_resto_da_peca_nao_se_mexe():
    """O pulso é ÊNFASE, não entrada: ele mexe na pill e em mais nada. Título,
    prova social e selo ficam parados — se eles se mexessem, teríamos trocado
    uma animação de carregamento por outra.

    As faixas saem da GEOMETRIA da peça, não de números escritos à mão: a
    calibragem do layout muda, e um teste com `y` fixo passaria a medir a faixa
    errada em silêncio (foi o que aconteceu na primeira versão deste teste — a
    faixa do "título" cobria metade da pill)."""
    offer, verdict = make_offer_ref(29999), SELO_6M
    base = _story_canvas()
    plan = _story_plan(ImageDraw.Draw(base), offer, verdict, "@ofiscaldapromo",
                       brand_name=DEFAULT_BRAND_NAME)
    corpo = _corpo_do_reel(REEL_SIZE[0], plan)
    topo_pill = corpo.preco[1]
    base_pill = topo_pill + plan["price"]["height"]
    # O pulso cresce a partir do CENTRO, então ele sangra para os dois lados.
    folga = plan["price"]["height"] * (REEL_PULSO_ESCALA - 1)

    meio = round(REEL_FPS * (REEL_PULSO_INICIO + REEL_PULSO_DURACAO / 2))
    frames = _frames([0, meio])
    assert _mudou(frames[0], frames[meio],
                  (0, int(topo_pill - folga), REEL_SIZE[0], int(base_pill + folga)))
    assert not _mudou(frames[0], frames[meio],
                      (0, int(corpo.titulo_y), REEL_SIZE[0], int(topo_pill - folga) - 1))
    assert not _mudou(frames[0], frames[meio],
                      (0, int(base_pill + folga) + 1, REEL_SIZE[0], 1600))


def test_o_rodape_e_o_cabecalho_nao_se_mexem():
    """"Rodapé estático com handle e chamada" — é a única coisa da tela que diz
    o que fazer; ela não pisca."""
    ultimo = round(REEL_FPS * REEL_DURACAO_S) - 1
    frames = _frames([0, ultimo])
    rodape = (0, 1620, 1080, 1920)
    cabecalho = (0, 100, 1080, 200)
    assert not _mudou(frames[0], frames[ultimo], rodape)
    assert not _mudou(frames[0], frames[ultimo], cabecalho)


def test_no_fim_do_clipe_o_corpo_esta_onde_a_arte_de_story_o_poe():
    """No fim nada está a meio caminho: título, preço, meta e selo pousam
    exatamente nas coordenadas do story. É o guarda contra a animação
    "quase" chegar ao layout calibrado."""
    ultimo = round(REEL_FPS * REEL_DURACAO_S) - 1
    frame = _frames([ultimo])[ultimo]
    arte = Image.open(io.BytesIO(render_story(
        make_offer_ref(29999), COPY, SELO_6M, client=_client(),
        handle="@ofiscaldapromo"))).convert("RGB")
    # A foto do produto está com zoom no fim; o CORPO (título..selo) não.
    corpo = (0, 1030, 1080, 1600)
    assert not _mudou(frame, arte, corpo)


# -- 3. o encode: H.264, e o que fazer quando não há ffmpeg ---------------------

def test_o_comando_do_ffmpeg_pede_h264_yuv420p_no_tamanho_e_no_fps_certos():
    cmd = video.comando_h264("/bin/ffmpeg", 1080, 1920, 24, "saida.mp4")
    assert cmd[0] == "/bin/ffmpeg"
    assert "1080x1920" in cmd
    assert cmd[cmd.index("-r") + 1] == "24"
    assert cmd[cmd.index("-c:v") + 1] == "libx264"
    assert cmd[cmd.index("-pix_fmt", cmd.index("-c:v")) + 1] == "yuv420p"
    assert cmd[-1] == "saida.mp4"


def test_sem_ffmpeg_o_encode_levanta_com_o_caminho_da_instalacao(monkeypatch):
    monkeypatch.setattr(video, "_do_extra", lambda: "")
    monkeypatch.setattr(video.shutil, "which", lambda nome: None)
    assert not video.tem_ffmpeg()
    with pytest.raises(video.SemFFmpeg) as exc:
        video.ffmpeg_exe()
    assert ".[reel]" in str(exc.value)


def test_com_ffmpeg_no_path_o_modulo_o_encontra(monkeypatch):
    monkeypatch.setattr(video, "_do_extra", lambda: "")
    monkeypatch.setattr(video.shutil, "which", lambda nome: "/usr/bin/ffmpeg")
    assert video.tem_ffmpeg()
    assert video.ffmpeg_exe() == "/usr/bin/ffmpeg"


def test_render_reel_sem_ffmpeg_levanta_semffmpeg(monkeypatch):
    monkeypatch.setattr(video, "_do_extra", lambda: "")
    monkeypatch.setattr(video.shutil, "which", lambda nome: None)
    with pytest.raises(video.SemFFmpeg):
        render_reel(make_offer_ref(29999), COPY, SELO_6M, client=_client())


# -- 4. o .mp4 de verdade -------------------------------------------------------
#
# Parser de caixas MP4 em Python puro: `ffprobe` não vem com o extra, e chamar
# o ffmpeg de novo só para ler o que ele acabou de escrever é ler a própria
# opinião. Aqui o teste lê o ARQUIVO.

def _caixas(dados: bytes, inicio: int = 0, fim: int | None = None):
    fim = len(dados) if fim is None else fim
    i = inicio
    while i + 8 <= fim:
        tam = int.from_bytes(dados[i:i + 4], "big")
        tipo = dados[i + 4:i + 8].decode("latin-1")
        corpo = i + 8
        if tam == 1:
            tam = int.from_bytes(dados[i + 8:i + 16], "big")
            corpo = i + 16
        elif tam == 0:
            tam = fim - i
        if tam < 8:
            return
        yield tipo, corpo, i + tam
        i += tam


def _acha(dados: bytes, caminho: tuple[str, ...], inicio=0, fim=None):
    """O corpo da primeira caixa em `caminho` — ("moov", "trak", "tkhd")."""
    alvo, resto = caminho[0], caminho[1:]
    for tipo, corpo, termina in _caixas(dados, inicio, fim):
        if tipo != alvo:
            continue
        if not resto:
            return corpo, termina
        achado = _acha(dados, resto, corpo, termina)
        if achado:
            return achado
    return None


def _dimensoes(mp4: bytes) -> tuple[int, int]:
    corpo, _ = _acha(mp4, ("moov", "trak", "tkhd"))
    # v0: 4 (versão+flags) + 20 + 8 + 8 + 36 de matriz -> largura em 16.16
    largura = int.from_bytes(mp4[corpo + 76:corpo + 80], "big") >> 16
    altura = int.from_bytes(mp4[corpo + 80:corpo + 84], "big") >> 16
    return largura, altura


def _duracao_s(mp4: bytes) -> float:
    corpo, _ = _acha(mp4, ("moov", "mvhd"))
    escala = int.from_bytes(mp4[corpo + 12:corpo + 16], "big")
    duracao = int.from_bytes(mp4[corpo + 16:corpo + 20], "big")
    return duracao / escala


def _codecs(mp4: bytes) -> list[str]:
    saida = []
    corpo_moov, fim_moov = _acha(mp4, ("moov",))
    for tipo, corpo, fim in _caixas(mp4, corpo_moov, fim_moov):
        if tipo != "trak":
            continue
        achado = _acha(mp4, ("mdia", "minf", "stbl", "stsd"), corpo, fim)
        if achado:
            c, _ = achado
            saida.append(mp4[c + 12:c + 16].decode("latin-1"))
    return saida


@pytest.fixture(scope="module")
def mp4() -> bytes:
    if not video.tem_ffmpeg():
        pytest.skip("sem ffmpeg (extra opcional `reel`)")
    return render_reel(make_offer_ref(29999), COPY, SELO_6M, client=_client(),
                       handle="@ofiscaldapromo")


@SEM_FFMPEG
def test_o_arquivo_e_um_mp4(mp4):
    assert mp4[4:8] == b"ftyp"


@SEM_FFMPEG
def test_o_arquivo_tem_1080x1920(mp4):
    assert _dimensoes(mp4) == (1080, 1920)


@SEM_FFMPEG
def test_o_arquivo_dura_entre_5_e_90_segundos(mp4):
    assert 5 <= _duracao_s(mp4) <= 90
    assert abs(_duracao_s(mp4) - REEL_DURACAO_S) < 0.5


@SEM_FFMPEG
def test_o_video_e_h264(mp4):
    """`avc1` é o nome do H.264 na caixa `stsd`. A aba Reels aceita H.264 ou
    HEVC; o projeto entrega H.264, que é o que toda conta reproduz."""
    assert "avc1" in _codecs(mp4)


@SEM_FFMPEG
def test_o_arquivo_cabe_no_limite_de_download_de_bot_do_telegram(mp4):
    """A hospedagem é o `sendVideo` + `getFile` do Telegram, e o bot só baixa
    até 20 MB. Passar disso é defeito do GERADOR, não da hospedagem."""
    assert len(mp4) < video.LIMITE_TELEGRAM_BYTES

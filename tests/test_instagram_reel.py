"""Fase 5T — canal `instagram_reel`: o Reel vai ao ar pela API oficial.

Mesmos três passos do `instagram_story` medidos ao vivo em 2026-08-27 (criar
container -> polling do `status_code` -> `media_publish`), com o que só o Reel
muda: `media_type=REELS`, `video_url` no lugar de `image_url`, `caption` (Reel
aceita; story não) e `share_to_feed=true`.

E uma coisa que nenhum outro canal faz: **a cota é PERGUNTADA à Meta**. A
documentação dela traz 100 e 50 na mesma página, e a janela é MÓVEL — libera
24 h depois de cada publicação, não à meia-noite. Chutar um número aqui é
publicar contra um limite imaginário; a fonte da verdade é
`GET /{ig_user_id}/content_publishing_limit`, a mesma rota que o `doctor` já
consulta.

Nenhum teste toca a rede (`httpx.MockTransport`) e nenhum gera vídeo de verdade
— `creative.render_reel` entra por dublê, senão cada teste custaria 3,5 s de
ffmpeg.
"""

import io
from urllib.parse import parse_qs

import httpx
import pytest
from PIL import Image

from afiliado import creative, video
from afiliado.channels import instagram_reel as mod
from afiliado.channels.instagram_common import AVISO_POLLING_CEGO_TMPL
from afiliado.channels.instagram_reel import InstagramReelChannel
from afiliado.channels.telegram import send_video_bytes
from afiliado.errors import SourceError
from tests.test_state import make_post

MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 512


@pytest.fixture(autouse=True)
def sem_ffmpeg_de_verdade(monkeypatch):
    """Nenhum teste deste arquivo roda o ffmpeg: o Reel de verdade custa 3,5 s
    por peça e o que se testa aqui é o CANAL, não o desenho (esse é o
    test_creative_reel.py)."""
    monkeypatch.setattr(creative, "render_reel", lambda *a, **k: MP4)


def _product_png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (600, 600), (10, 20, 30)).save(buf, "PNG")
    return buf.getvalue()


_STATUS_TEXTO = {
    "IN_PROGRESS": "Media is still being processed.",
    "FINISHED": "Finished",
    "ERROR": "The media you are trying to publish is not valid.",
    "EXPIRED": "The container has expired.",
}


def _handler(status_codes=("FINISHED",), corpos=None, cota=(3, 100),
             publish_id="reel321", creation_id="creation999"):
    corpos = corpos if corpos is not None else []
    status = list(status_codes)

    def handler(request):
        host, path, url = request.url.host, request.url.path, str(request.url)
        if host == "cf.shopee.com.br":
            return httpx.Response(200, content=_product_png(),
                                  headers={"content-type": "image/png"})
        if host == "api.telegram.org":
            if path.endswith("/sendVideo"):
                corpos.append(("sendVideo", request.content))
                return httpx.Response(200, json={"ok": True, "result": {
                    "message_id": 7, "video": {"file_id": "vid42"}}})
            if path.endswith("/getFile"):
                return httpx.Response(200, json={
                    "ok": True, "result": {"file_path": "videos/file_7.mp4"}})
        if host == "graph.facebook.com":
            if path.endswith("/content_publishing_limit"):
                corpos.append(("cota", url))
                if cota is None:
                    return httpx.Response(200, json={"data": []})
                usadas, total = cota
                return httpx.Response(200, json={"data": [{
                    "quota_usage": usadas,
                    "config": {"quota_total": total, "quota_duration": 86400}}]})
            if path.endswith("/media_publish"):
                corpos.append(("publish", request.content))
                return httpx.Response(200, json={"id": publish_id})
            if path.endswith("/media"):
                corpos.append(("media", request.content))
                return httpx.Response(200, json={"id": creation_id})
            corpos.append(("status", url))
            idx = min(len([c for c in corpos if c[0] == "status"]) - 1, len(status) - 1)
            code = status[idx]
            return httpx.Response(200, json={"status_code": code,
                                             "status": _STATUS_TEXTO[code]})
        return httpx.Response(404)

    return handler


class _Relogio:
    def __init__(self):
        self.pausas = []

    def __call__(self, segundos):
        self.pausas.append(segundos)


def _canal(handler, sleep=None, **kw) -> InstagramReelChannel:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return InstagramReelChannel("IGUSER", "IGTOKEN", "BOTTOKEN", "OPSCHAT",
                                client=client, sleep=sleep or _Relogio(), **kw)


def _corpo(corpos, etapa) -> dict:
    return parse_qs(next(c for k, c in corpos if k == etapa).decode(),
                    keep_blank_values=True)


# -- 1. os três passos ---------------------------------------------------------

def test_o_container_do_reel_manda_media_type_reels_video_url_e_share_to_feed():
    corpos = []
    res = _canal(_handler(corpos=corpos)).publish(make_post())
    assert res.ok and res.message_id == "reel321"
    corpo = _corpo(corpos, "media")
    assert corpo["media_type"] == ["REELS"]
    assert corpo["video_url"][0].startswith("https://api.telegram.org/file/bot")
    assert "image_url" not in corpo               # Reel é vídeo, não imagem
    assert corpo["share_to_feed"] == ["true"]     # o Reel também vira post de feed
    assert corpo["access_token"] == ["IGTOKEN"]
    assert _corpo(corpos, "publish")["creation_id"] == ["creation999"]


def test_o_reel_leva_legenda_e_o_story_nao():
    """A diferença de API entre os dois: `POST /media` com `media_type=STORIES`
    recusa `caption`; com `REELS`, aceita — e a legenda é a página que o Google
    indexa desde 10/07/2025."""
    corpos = []
    _canal(_handler(corpos=corpos)).publish(make_post())
    caption = _corpo(corpos, "media")["caption"][0]
    assert "Tênis Nike SB" in caption
    assert creative.ASSINATURA in caption
    assert "Link na bio" in caption


def test_a_legenda_nunca_leva_link_do_titulo_do_vendedor():
    corpos = []
    _canal(_handler(corpos=corpos)).publish(
        make_post(title="Fone bom http://spam.example/x"))
    caption = _corpo(corpos, "media")["caption"][0]
    assert "http://spam.example" not in caption


def test_nome_do_canal_e_max_per_run():
    assert InstagramReelChannel.name == "instagram_reel"
    assert InstagramReelChannel.max_per_run == 1


def test_o_canal_nao_declara_manual():
    """Publicação de VERDADE, como o feed e o story oficial: entra em
    `summary.published`, não em `dispatched`."""
    assert not getattr(InstagramReelChannel, "manual", False)


# -- 2. o polling do container, e o caminho de erro dele -----------------------

def test_o_polling_espera_o_container_ficar_pronto_antes_de_publicar():
    """Aqui ele importa mais que no story: o container de VÍDEO leva tempo de
    verdade — a Meta transcodifica o arquivo. Publicar cedo é o que falha."""
    corpos, relogio = [], _Relogio()
    res = _canal(_handler(("IN_PROGRESS", "IN_PROGRESS", "FINISHED"), corpos=corpos),
                 sleep=relogio).publish(make_post())
    assert res.ok
    assert [k for k, _ in corpos] == ["cota", "sendVideo", "media",
                                      "status", "status", "status", "publish"]
    assert relogio.pausas == [InstagramReelChannel.poll_interval] * 2   # nada dormiu


def test_o_reel_espera_mais_que_o_story_porque_video_transcodifica():
    assert InstagramReelChannel.max_polls > 5
    assert InstagramReelChannel.max_polls * InstagramReelChannel.poll_interval >= 30


@pytest.mark.parametrize("code", ["ERROR", "EXPIRED"])
def test_container_morto_nao_chega_a_publicar(code):
    corpos, relogio = [], _Relogio()
    res = _canal(_handler((code,), corpos=corpos), sleep=relogio).publish(make_post())
    assert not res.ok
    assert code in res.error and _STATUS_TEXTO[code] in res.error
    assert not any(k == "publish" for k, _ in corpos)
    assert relogio.pausas == []          # desiste na hora


def test_container_ainda_em_progresso_no_fim_publica_assim_mesmo_e_explica_a_falha():
    def handler(request):
        if request.url.path.endswith("/media_publish"):
            return httpx.Response(400, json={"error": {"message": "sem permissão"}})
        return _handler(("IN_PROGRESS",))(request)

    res = _canal(handler).publish(make_post())
    assert not res.ok
    assert "sem permissão" in res.error and "IN_PROGRESS" in res.error


def test_polling_cego_avisa_com_o_nome_do_canal():
    def handler(request):
        if (request.url.host == "graph.facebook.com" and "fields=" in str(request.url)):
            return httpx.Response(200, json={"id": "creation999"})
        return _handler()(request)

    ch = _canal(handler)
    assert ch.publish(make_post()).ok
    assert ch.warnings == [AVISO_POLLING_CEGO_TMPL.format(canal="instagram_reel")]


@pytest.mark.parametrize("rota", ["sendVideo", "getFile", "media", "status",
                                  "media_publish", "cota"])
def test_falha_de_rede_em_qualquer_etapa_nunca_levanta(rota):
    # A rota da FOTO do produto não está aqui porque o render entra por dublê
    # neste arquivo; ela é o `test_falha_ao_baixar_a_foto_do_produto...`.
    base = _handler(("IN_PROGRESS", "FINISHED"))

    def handler(request):
        host, path, url = request.url.host, request.url.path, str(request.url)
        if rota == "cota" and path.endswith("/content_publishing_limit"):
            raise httpx.ConnectError("sem rede")
        if rota in ("sendVideo", "getFile", "media_publish") and path.endswith("/" + rota):
            raise httpx.ConnectError("sem rede")
        if rota == "media" and path.endswith("/media"):
            raise httpx.ConnectError("sem rede")
        if rota == "status" and host == "graph.facebook.com" and "fields=" in url:
            raise httpx.ConnectError("sem rede")
        return base(request)

    res = _canal(handler).publish(make_post())
    if rota in ("status", "cota"):
        # Nem o polling nem a leitura da cota condenam a peça: o container
        # existe, e uma cota que não respondeu não é uma cota estourada.
        assert res.ok
    else:
        assert not res.ok and res.error


# -- 3. a cota, que não é chutada ----------------------------------------------

def test_a_cota_e_perguntada_a_meta_antes_de_gastar_qualquer_coisa():
    """Primeira chamada do publish, antes de gerar vídeo e de hospedar: se a
    cota estourou, não há por que pagar 3,5 s de ffmpeg e um upload."""
    corpos = []
    _canal(_handler(corpos=corpos)).publish(make_post())
    assert corpos[0][0] == "cota"
    assert "content_publishing_limit" in corpos[0][1]


def test_cota_estourada_recusa_sem_publicar_e_diz_os_numeros_da_meta():
    corpos = []
    res = _canal(_handler(corpos=corpos, cota=(100, 100))).publish(make_post())
    assert not res.ok
    assert "100" in res.error and "24" in res.error
    assert [k for k, _ in corpos] == ["cota"]     # nada de vídeo, nada de upload


def test_a_cota_nao_e_um_numero_deste_codigo():
    """A documentação da Meta traz 100 e 50 na mesma página, e a janela é
    MÓVEL. O canal recusa com 50 de 50 exatamente como recusa com 100 de 100 —
    porque quem diz o teto é a resposta, não uma constante daqui."""
    assert not _canal(_handler(cota=(50, 50))).publish(make_post()).ok
    assert _canal(_handler(cota=(49, 50))).publish(make_post()).ok


def test_cota_que_a_meta_nao_informou_nao_bloqueia_o_reel():
    """Forma estranha da resposta não vira recusa: isso faria uma mudança de
    formato da Meta calar o canal em silêncio. O doctor já toma a mesma
    decisão."""
    assert _canal(_handler(cota=None)).publish(make_post()).ok


# -- 4. a hospedagem do vídeo --------------------------------------------------

def test_o_video_e_hospedado_por_sendvideo_e_a_url_e_a_do_getfile():
    corpos = []
    assert _canal(_handler(corpos=corpos)).publish(make_post()).ok
    corpo = next(c for k, c in corpos if k == "sendVideo")
    assert b"reel.mp4" in corpo and b"video/mp4" in corpo
    assert _corpo(corpos, "media")["video_url"] == [
        "https://api.telegram.org/file/botBOTTOKEN/videos/file_7.mp4"]


def test_o_video_sai_pelo_bot_secundario_quando_existe():
    """A5: a URL de hospedagem carrega o token de quem enviou, e é ela que vai
    à Meta. Nunca o token do bot ADMINISTRADOR do canal público."""
    corpos = []
    ch = _canal(_handler(corpos=corpos), art_host_bot_token="BOTDEARTE")
    assert ch.publish(make_post()).ok
    assert "botBOTDEARTE" in _corpo(corpos, "media")["video_url"][0]


def test_hospedagem_falha_sem_criar_container():
    corpos = []

    def handler(request):
        if request.url.path.endswith("/sendVideo"):
            return httpx.Response(200, json={"ok": False, "description": "bot fora do chat"})
        return _handler(corpos=corpos)(request)

    res = _canal(handler).publish(make_post())
    assert not res.ok and "hospedar" in res.error
    assert not any(k == "media" for k, _ in corpos)


def test_video_maior_que_o_limite_de_download_do_bot_recusa_antes_de_enviar(monkeypatch):
    """O bot do Telegram só BAIXA 20 MB. Um arquivo maior seria enviado, a Meta
    receberia uma URL que devolve erro e a peça falharia com a mensagem errada
    — a causa é o gerador, e é isso que o erro precisa dizer."""
    monkeypatch.setattr(creative, "render_reel",
                        lambda *a, **k: b"\x00" * (video.LIMITE_TELEGRAM_BYTES + 1))
    corpos = []
    res = _canal(_handler(corpos=corpos)).publish(make_post())
    assert not res.ok and "MB" in res.error
    assert not any(k == "sendVideo" for k, _ in corpos)


# -- 5. sem ffmpeg, e sem foto -------------------------------------------------

def test_sem_ffmpeg_o_canal_recusa_com_o_caminho_da_instalacao(monkeypatch):
    monkeypatch.setattr(creative, "render_reel",
                        lambda *a, **k: (_ for _ in ()).throw(video.SemFFmpeg(video.SEM_FFMPEG)))
    corpos = []
    res = _canal(_handler(corpos=corpos)).publish(make_post())
    assert not res.ok and ".[reel]" in res.error
    assert not any(k == "sendVideo" for k, _ in corpos)


def test_o_desarme_do_canal_e_a_ausencia_de_ffmpeg(monkeypatch):
    monkeypatch.setattr(video, "_do_extra", lambda: "")
    monkeypatch.setattr(video.shutil, "which", lambda nome: None)
    assert "ffmpeg" in InstagramReelChannel.desarme()
    monkeypatch.setattr(video.shutil, "which", lambda nome: "/usr/bin/ffmpeg")
    assert InstagramReelChannel.desarme() == ""


def test_falha_ao_baixar_a_foto_do_produto_vira_resultado_falho(monkeypatch):
    monkeypatch.setattr(creative, "render_reel",
                        lambda *a, **k: (_ for _ in ()).throw(SourceError("404")))
    res = _canal(_handler()).publish(make_post())
    assert not res.ok and "Reel" in res.error


# -- 6. o Reel conta como publicação, e é a hospedagem do projeto --------------

def test_o_reel_conta_como_publicacao_no_pipeline(tmp_path, monkeypatch):
    from afiliado import llm, pipeline
    from afiliado.state import StateDB
    from tests.test_models import make_offer
    from tests.test_pipeline import CFG, FakeSource, no_network_validator

    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    ch = _canal(_handler())
    summary = pipeline.run(CFG, [FakeSource([make_offer(item_id="a")])], [ch], db,
                           validator=no_network_validator)
    assert summary.published == ["Tênis Nike SB"]
    assert summary.dispatched == []
    assert db.count_posts_today("instagram_reel") == 1
    db.close()


def test_send_video_bytes_manda_multipart_e_nunca_levanta():
    vistos = {}

    def handler(request):
        assert request.url.path.endswith("/sendVideo")
        vistos["body"] = request.content
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 3}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert send_video_bytes("TOKEN", "123", MP4, caption="oi", client=client)["ok"]
    assert b"reel.mp4" in vistos["body"] and b"video/mp4" in vistos["body"]

    def cai(request):
        raise httpx.ConnectError("down")

    caiu = send_video_bytes("TOKEN", "123", MP4,
                            client=httpx.Client(transport=httpx.MockTransport(cai)))
    assert caiu["ok"] is False and caiu["description"]


def test_o_telegram_devolvendo_documento_em_vez_de_video_ainda_hospeda():
    """O Telegram entrega `document` quando decide não tratar o arquivo como
    vídeo. A URL de `getFile` é a mesma, e recusar aqui seria perder a peça por
    um detalhe do formato da resposta."""
    corpos = []

    def handler(request):
        if request.url.path.endswith("/sendVideo"):
            return httpx.Response(200, json={"ok": True, "result": {
                "message_id": 7, "document": {"file_id": "vid42"}}})
        return _handler(corpos=corpos)(request)

    assert _canal(handler).publish(make_post()).ok
    assert _corpo(corpos, "media")["video_url"][0].endswith("videos/file_7.mp4")


def test_a_hospedagem_diz_no_chat_de_ops_que_aquilo_e_temporario():
    assert "Reel" in mod.InstagramReelChannel.host_caption


# -- 7. montagem e preview (fase 5T, T2 e T4) ----------------------------------
#
# O canal nasce DESLIGADO. Quem o liga é o dono, depois de ver a peça — e é
# para isso que o `afiliado run --dry-run` grava o .mp4.

def _envs_do_instagram(monkeypatch):
    for k, v in {"TELEGRAM_BOT_TOKEN": "TOKDOCANAL", "TELEGRAM_OPS_CHAT_ID": "999",
                 "IG_USER_ID": "178", "IG_ACCESS_TOKEN": "igtok"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("ART_HOST_BOT_TOKEN", raising=False)


def test_o_canal_nasce_desligado_no_config_real():
    """T2: "nasce desligado — quem liga é o dono depois de ver a peça". Se
    algum dia isto virar `true` sem o dono mandar, o teste conta."""
    import yaml

    with open("config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    entrada = cfg["channels"]["instagram_reel"]
    assert entrada["enabled"] is False
    assert entrada["max_per_day"] >= 1        # o teto existe desde já


def test_build_channels_monta_o_reel_quando_ligado(monkeypatch):
    from afiliado import cli

    _envs_do_instagram(monkeypatch)
    monkeypatch.setattr(video, "_do_extra", lambda: "")
    monkeypatch.setattr(video.shutil, "which", lambda nome: "/usr/bin/ffmpeg")
    canais, avisos = cli._build_channels(
        {"channels": {"instagram_reel": {"enabled": True, "max_per_day": 3}}})
    assert [c.name for c in canais] == ["instagram_reel"]
    assert canais[0].max_per_day == 3
    assert avisos == [cli.ART_HOST_AVISO_TMPL.format(canal="instagram_reel")]


def test_sem_ffmpeg_o_canal_nao_sobe_e_o_run_diz_por_que(monkeypatch, capsys):
    """Molde do `playwright` da 5P: o extra é opcional, e a ausência dele
    desarma UM canal com aviso — nunca derruba o run."""
    from afiliado import cli

    _envs_do_instagram(monkeypatch)
    monkeypatch.setattr(video, "_do_extra", lambda: "")
    monkeypatch.setattr(video.shutil, "which", lambda nome: None)
    canais, avisos = cli._build_channels(
        {"channels": {"telegram": False, "instagram_reel": True}})
    assert canais == []
    assert len(avisos) == 1 and ".[reel]" in avisos[0]
    assert avisos[0] in capsys.readouterr().out


def test_o_desarme_do_reel_nao_atrapalha_os_outros_canais(monkeypatch):
    from afiliado import cli

    _envs_do_instagram(monkeypatch)
    monkeypatch.setattr(video, "_do_extra", lambda: "")
    monkeypatch.setattr(video.shutil, "which", lambda nome: None)
    canais, _ = cli._build_channels(
        {"channels": {"instagram_feed": True, "instagram_story": True,
                      "instagram_reel": True}})
    assert [c.name for c in canais] == ["instagram_feed", "instagram_story"]


def test_o_dry_run_grava_o_mp4_do_reel_em_previews(monkeypatch, tmp_path, capsys):
    """T4: o critério de aceite da fase. O preview NÃO depende de o canal estar
    ligado — o canal nasce desligado de propósito, e quem o liga é o dono
    depois de ver a peça. Se o preview exigisse o canal ligado, ligar seria às
    cegas."""
    from afiliado import cli

    monkeypatch.setattr(cli, "PREVIEWS_DIR", tmp_path / "previews")
    monkeypatch.setattr(video, "_do_extra", lambda: "")
    monkeypatch.setattr(video.shutil, "which", lambda nome: "/usr/bin/ffmpeg")
    preview = cli._preview_do_reel({"brand": {"handle": "@ofiscaldapromo"}}, [])
    preview(make_post())
    caminho = tmp_path / "previews" / cli.PREVIEW_DO_REEL
    assert caminho.is_file() and caminho.read_bytes() == MP4
    assert str(caminho) in capsys.readouterr().out


def test_o_dry_run_grava_um_mp4_so_por_run(monkeypatch, tmp_path):
    """Um Reel por run: gerar seis vídeos de 3,5 s para o dono olhar UM não é
    preview, é espera."""
    from afiliado import cli

    monkeypatch.setattr(cli, "PREVIEWS_DIR", tmp_path / "previews")
    monkeypatch.setattr(video, "_do_extra", lambda: "")
    monkeypatch.setattr(video.shutil, "which", lambda nome: "/usr/bin/ffmpeg")
    feitos = []
    monkeypatch.setattr(creative, "render_reel",
                        lambda *a, **k: feitos.append(1) or MP4)
    preview = cli._preview_do_reel({}, [])
    for _ in range(3):
        preview(make_post())
    assert feitos == [1]


def test_sem_ffmpeg_o_dry_run_nao_tem_preview_e_avisa(monkeypatch):
    from afiliado import cli

    monkeypatch.setattr(video, "_do_extra", lambda: "")
    monkeypatch.setattr(video.shutil, "which", lambda nome: None)
    avisos = []
    assert cli._preview_do_reel({}, avisos) is None
    assert len(avisos) == 1 and ".[reel]" in avisos[0]


def test_o_preview_que_falha_nao_derruba_o_dry_run(monkeypatch, tmp_path, capsys):
    from afiliado import cli

    monkeypatch.setattr(cli, "PREVIEWS_DIR", tmp_path / "previews")
    monkeypatch.setattr(video, "_do_extra", lambda: "")
    monkeypatch.setattr(video.shutil, "which", lambda nome: "/usr/bin/ffmpeg")
    monkeypatch.setattr(creative, "render_reel",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    cli._preview_do_reel({}, [])(make_post())
    assert "boom" in capsys.readouterr().out
    assert not (tmp_path / "previews" / cli.PREVIEW_DO_REEL).exists()


def test_o_pipeline_em_dry_run_chama_o_preview_com_o_post(tmp_path, monkeypatch):
    """O caminho que liga `afiliado run --dry-run` ao arquivo: sem este gancho
    o dry-run não tem `Post` nenhum na mão de quem desenha."""
    from afiliado import llm, pipeline
    from afiliado.state import StateDB
    from tests.test_models import make_offer
    from tests.test_pipeline import CFG, FakeSource, no_network_validator

    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    vistos = []
    pipeline.run(CFG, [FakeSource([make_offer(item_id="a")])], [], db, dry_run=True,
                 validator=no_network_validator, preview=vistos.append)
    assert [p.offer.item_id for p in vistos] == ["a"]
    db.close()


def test_fora_do_dry_run_o_pipeline_nao_desenha_preview_nenhum(tmp_path, monkeypatch):
    from afiliado import llm, pipeline
    from afiliado.state import StateDB
    from tests.test_models import make_offer
    from tests.test_pipeline import CFG, FakeChannel, FakeSource, no_network_validator

    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    vistos = []
    pipeline.run(CFG, [FakeSource([make_offer(item_id="a")])], [FakeChannel()], db,
                 validator=no_network_validator, preview=vistos.append)
    assert vistos == []
    db.close()

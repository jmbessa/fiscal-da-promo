"""Fase 5E (Mudança 1): o que os dois canais do Instagram compartilham mora em
`afiliado.channels.instagram_common` — público, nada de importar nome privado de
um módulo para o outro. O feed continua se comportando exatamente como antes
(`tests/test_instagram_feed.py` passa sem uma linha mudada)."""

import io

from PIL import Image

from afiliado.channels import instagram_common as common
from afiliado.channels import instagram_feed
from afiliado.channels.instagram_feed import InstagramFeedChannel


def test_hosts_e_helpers_sao_publicos_no_modulo_comum():
    assert set(common.GRAPH_HOSTS) == {"facebook_login", "instagram_login"}
    assert common.GRAPH == common.GRAPH_HOSTS["facebook_login"]
    assert callable(common.graph_error)
    assert callable(common.to_jpeg)


def test_o_feed_usa_os_hosts_do_modulo_comum():
    # O cli importa `GRAPH_HOSTS` (doctor + `_instagram_api`); depois da mudança
    # é o MESMO dicionário, não uma segunda cópia que pode divergir.
    assert instagram_feed.GRAPH_HOSTS is common.GRAPH_HOSTS


def test_o_feed_herda_a_base_comum():
    assert issubclass(InstagramFeedChannel, common.InstagramBase)


def test_graph_error_devolve_a_mensagem_da_api():
    assert common.graph_error({"error": {"message": "bad image_url"}}) == "bad image_url"
    assert "resposta inesperada" in common.graph_error(["não é dict"])
    assert "resposta inesperada" in common.graph_error({"sem": "erro"})


def test_to_jpeg_converte_a_arte_png():
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), (1, 2, 3)).save(buf, "PNG")
    assert common.to_jpeg(buf.getvalue()).startswith(bytes.fromhex("ffd8ff"))

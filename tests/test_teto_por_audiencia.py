"""Fase 5U (U3) — o teto que a AUDIÊNCIA autoriza.

Medido na conta real em 2026-08-29 pela Graph API: **2 seguidores**, 5 posts,
alcance de 1 conta em 7 dias. Publicar 60 stories por dia para duas pessoas não
gera alcance: gasta cota da Meta (100/24 h, compartilhada por feed, story e
Reel), enche o `state.db` de dedupe e queima o estoque de candidatas.

    teto efetivo = min(max_per_day, max(piso, seguidores ÷ divisor))

`piso` e `divisor` são CHUTE e por isso moram no `config.yaml`, por canal —
número chutado dentro do código é número que ninguém revisa. E a leitura falha
ABERTA: sem o número de seguidores (API fora, cache vazio, canal sem
credencial) vale o `max_per_day` do config. Um erro de leitura não pode calar a
conta.
"""

import httpx
import pytest

from afiliado import cli, pipeline
from afiliado.channels.instagram_common import le_seguidores


# -- a conta, sozinha ---------------------------------------------------------

@pytest.mark.parametrize("seguidores,piso,divisor,teto,esperado", [
    # O caso de hoje: 2 seguidores, story piso 3 / divisor 100.
    (2, 3, 100, 60, 3),
    # O feed: piso 2 / divisor 500 — 2 seguidores não chegam a 1.
    (2, 2, 500, 2, 2),
    # O Reel: piso 1 / divisor 1000.
    (2, 1, 1000, 2, 1),
    # Crescendo: o piso deixa de mandar quando a audiência passa dele.
    (1000, 3, 100, 60, 10),
    # E o `max_per_day` volta a ser o teto quando a audiência o alcança.
    (100_000, 3, 100, 60, 60),
    # Sem seguidores conhecidos: FALHA ABERTA, vale o config.
    (None, 3, 100, 60, 60),
    # Divisor inválido não pode virar ZeroDivisionError nem calar o canal.
    (2, 3, 0, 60, 60),
])
def test_o_teto_efetivo(seguidores, piso, divisor, teto, esperado):
    assert pipeline.teto_por_audiencia(teto, seguidores, piso, divisor) == esperado


def test_a_divisao_e_para_baixo_e_o_piso_e_um_piso_nao_um_alvo():
    """199 seguidores ÷ 100 é 1, não 2: o teto nunca promete alcance que a
    conta não tem. E abaixo do piso quem manda é o piso."""
    assert pipeline.teto_por_audiencia(60, 199, 3, 100) == 3
    assert pipeline.teto_por_audiencia(60, 350, 3, 100) == 3
    assert pipeline.teto_por_audiencia(60, 400, 3, 100) == 4


# -- o número de seguidores, lido e cacheado ----------------------------------

def _client(resposta, capturado=None):
    def handler(request):
        if capturado is not None:
            capturado.append(str(request.url))
        return resposta
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_le_o_followers_count_do_user_info():
    urls: list[str] = []
    n = le_seguidores("IGUSER", "TOKEN",
                      client=_client(httpx.Response(200, json={"followers_count": 2,
                                                               "id": "IGUSER"}), urls))
    assert n == 2
    assert "followers_count" in urls[0] and "IGUSER" in urls[0]


@pytest.mark.parametrize("resposta", [
    httpx.Response(200, json={"error": {"message": "token expirado"}}),
    httpx.Response(200, json={"id": "IGUSER"}),          # sem o campo
    httpx.Response(400, json={}),
    httpx.Response(200, content=b"<html>nao e json</html>"),
])
def test_leitura_que_nao_deu_certo_devolve_None_e_nunca_levanta(resposta):
    assert le_seguidores("IGUSER", "TOKEN", client=_client(resposta)) is None


def test_a_leitura_e_UMA_POR_DIA_e_sobrevive_ao_processo(tmp_path, monkeypatch):
    """`user_info` uma vez por run seriam 96 chamadas/dia (cadência de 15 min)
    para um número que muda devagar. A marca é do DIA LOCAL, em `day_flags` —
    a mesma mecânica do desarme do `instagram_story_link`, e ela sobrevive ao
    fim do processo."""
    from afiliado.state import StateDB
    db = StateDB(tmp_path / "s.db")
    chamadas = []
    monkeypatch.setattr(cli, "le_seguidores",
                        lambda *a, **k: chamadas.append(1) or 2)

    assert cli.seguidores_do_dia("IGUSER", "TOKEN", "facebook_login", db) == 2
    assert cli.seguidores_do_dia("IGUSER", "TOKEN", "facebook_login", db) == 2
    assert len(chamadas) == 1

    # Outro processo, mesmo banco, mesmo dia: nenhuma chamada nova.
    db.close()
    db2 = StateDB(tmp_path / "s.db")
    assert cli.seguidores_do_dia("IGUSER", "TOKEN", "facebook_login", db2) == 2
    assert len(chamadas) == 1
    db2.close()


def test_leitura_falha_nao_e_cacheada(tmp_path, monkeypatch):
    """Cachear o "não sei" prenderia a conta ao `max_per_day` do config pelo
    resto do dia por causa de um timeout — e o inverso também: prenderia o teto
    baixo se a falha fosse do outro lado. O run seguinte tenta de novo."""
    from afiliado.state import StateDB
    db = StateDB(tmp_path / "s.db")
    chamadas = []
    monkeypatch.setattr(cli, "le_seguidores",
                        lambda *a, **k: chamadas.append(1) or None)
    assert cli.seguidores_do_dia("IGUSER", "TOKEN", "facebook_login", db) is None
    assert cli.seguidores_do_dia("IGUSER", "TOKEN", "facebook_login", db) is None
    assert len(chamadas) == 2
    db.close()


# -- e como isso chega aos canais --------------------------------------------

CANAIS_COM_REGUA = {
    # Sem `audiencia`: o Telegram tem outra audiência, e o teto dele é a meta
    # do canal. A régua do Instagram não pode encostar nele.
    "telegram": {"enabled": True, "max_per_day": 60},
    "instagram_feed": {"enabled": True, "max_per_day": 2,
                       "audiencia": {"piso": 2, "divisor": 500}},
    "instagram_story": {"enabled": True, "max_per_day": 60,
                        "audiencia": {"piso": 3, "divisor": 100}},
}


def _ambiente(monkeypatch):
    for nome, valor in (("TELEGRAM_BOT_TOKEN", "tok"), ("TELEGRAM_CHANNEL_ID", "@c"),
                        ("TELEGRAM_OPS_CHAT_ID", "999"), ("IG_USER_ID", "IGUSER"),
                        ("IG_ACCESS_TOKEN", "IGTOKEN")):
        monkeypatch.setenv(nome, valor)


def _monta(monkeypatch, tmp_path, seguidores, canais=None):
    from afiliado.state import StateDB
    _ambiente(monkeypatch)
    monkeypatch.setattr(cli, "le_seguidores", lambda *a, **k: seguidores)
    db = StateDB(tmp_path / "s.db")
    cfg = {"channels": canais if canais is not None else CANAIS_COM_REGUA,
           "brand": {"name": "Fiscal da Promo", "handle": "@ofiscaldapromo"},
           "instagram": {"api": "facebook_login"}}
    canais_montados, avisos = cli._build_channels(cfg, db=db)
    db.close()
    return {c.name: c for c in canais_montados}, avisos


def test_os_canais_nascem_com_o_teto_que_a_audiencia_autoriza(monkeypatch, tmp_path):
    canais, avisos = _monta(monkeypatch, tmp_path, seguidores=2)
    assert canais["instagram_story"].max_per_day == 3     # 60 configurado
    assert canais["instagram_feed"].max_per_day == 2      # já estava no piso
    # O canal sem régua no config não é tocado — o Telegram não tem audiência
    # do Instagram, e o teto dele é a meta do canal.
    assert canais["telegram"].max_per_day == 60


def test_o_resumo_de_ops_diz_o_teto_efetivo_E_o_motivo(monkeypatch, tmp_path):
    _, avisos = _monta(monkeypatch, tmp_path, seguidores=2)
    linha = next(a for a in avisos if "teto por audiência" in a)
    assert "instagram_story" in linha
    assert "teto por audiência: 3 (60 configurado)" in linha
    assert "2 seguidor(es) ÷ 100, piso 3" in linha
    # Só quem MUDOU vira linha: o feed já publicava 2 e continua publicando 2.
    assert not any("instagram_feed" in a and "teto por audiência" in a for a in avisos)


def test_sem_o_numero_de_seguidores_vale_o_config_E_o_run_diz(monkeypatch, tmp_path):
    """Falha aberta: a API fora não pode calar a conta. E não pode calar em
    silêncio — sem a linha, o dono não teria como saber que a régua não foi
    aplicada hoje."""
    canais, avisos = _monta(monkeypatch, tmp_path, seguidores=None)
    assert canais["instagram_story"].max_per_day == 60
    assert cli.AVISO_SEM_SEGUIDORES in avisos


def test_canal_sem_regua_no_config_nao_consulta_a_graph_api(monkeypatch, tmp_path):
    """A leitura custa uma chamada; sem canal que a use, ela não acontece."""
    chamadas = []
    monkeypatch.setattr(cli, "le_seguidores",
                        lambda *a, **k: chamadas.append(1) or 2)
    _ambiente(monkeypatch)
    from afiliado.state import StateDB
    db = StateDB(tmp_path / "s.db")
    cli._build_channels({"channels": {"telegram": {"enabled": True, "max_per_day": 60}},
                         "brand": {}, "instagram": {"api": "facebook_login"}}, db=db)
    db.close()
    assert chamadas == []


def test_o_config_de_producao_tem_a_regua_nos_canais_do_instagram():
    """Os números são chute e por isso moram no config — mas a AUSÊNCIA deles
    também é uma decisão, e ela desliga o teto em silêncio. Este teste cobra a
    presença e a forma, nunca os valores."""
    from afiliado.config import load_config
    canais = load_config("config.yaml")["channels"]
    for nome in ("instagram_story", "instagram_feed", "instagram_reel"):
        regua = cli.audiencia_do_canal(canais[nome])
        assert regua is not None, f"{nome} sem `audiencia:` no config.yaml"
        piso, divisor = regua
        assert piso >= 1 and divisor >= 1, nome

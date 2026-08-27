"""Fase 5F — canal `instagram_story_link`: story COM figurinha de link, pela
API PRIVADA do Instagram (instagrapi).

**Nenhum teste aqui importa instagrapi**, e a suíte inteira roda numa máquina
que não o tem instalado — o import do canal é preguiçoso e tudo abaixo usa um
DUPLO do `Client`. Nenhum teste toca a rede: a arte é renderizada contra um
`httpx.MockTransport`.

O que estes testes existem para prender é a VERIFICAÇÃO pós-publicação. O
sticker de link do instagrapi ficou quebrado **em silêncio** de 2025-11-03 a
2026-04-16 (issue #2320, ver `docs/superpowers/reviews/2026-08-27-sticker-de-link.md`):
o story ia ao ar sem link e ninguém percebia. Por isso o canal lê o story de
volta e distingue TRÊS estados — com link, sem link, e não consegui verificar —
sem nunca confundir o terceiro com um dos dois primeiros.
"""

import ast
import io
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from afiliado.channels import instagram_story_link as mod
from afiliado.channels.instagram_story_link import (AVISO_DESARMADO,
                                                    InstagramStoryLinkChannel)
from tests.test_state import make_post

SENHA = "S3nh4-D0-D0n0"


# -- duplos --------------------------------------------------------------------

class FakeLink:
    """O que `StoryLink(webUri=...)` é, para efeito do canal: um objeto com
    `webUri`. O de verdade é um modelo pydantic do instagrapi."""

    def __init__(self, webUri):          # noqa: N803 - o nome do campo é do instagrapi
        self.webUri = webUri


class FakeClient:
    """Duplo do `instagrapi.Client`.

    Registra o que recebeu (inclusive se o arquivo da arte AINDA EXISTIA na
    hora do upload) e devolve o que o teste mandar. `links_do_story` é o que
    `story_info` vai dizer que o story tem — é o botão que liga o cenário
    "instagrapi quebrou de novo".
    """

    def __init__(self, links_do_story=None, pk="STORY-777", erro_upload=None,
                 erro_info=None):
        self.links_do_story = links_do_story
        self.pk = pk
        self.erro_upload = erro_upload
        self.erro_info = erro_info
        self.chamadas: list[tuple] = []
        self.arte_existia = None
        self.arte_bytes = b""

    def photo_upload_to_story(self, path, **kwargs):
        self.chamadas.append(("upload", Path(path), kwargs.get("links")))
        self.arte_existia = Path(path).is_file()
        if self.arte_existia:
            self.arte_bytes = Path(path).read_bytes()
        if self.erro_upload is not None:
            raise self.erro_upload
        return SimpleNamespace(pk=self.pk, id=f"{self.pk}_1")

    def story_info(self, pk, **kwargs):
        self.chamadas.append(("story_info", pk, None))
        if self.erro_info is not None:
            raise self.erro_info
        return SimpleNamespace(pk=pk, links=list(self.links_do_story or []))


class Relogio:
    """`sleep` injetado: registra as pausas e não dorme de verdade."""

    def __init__(self):
        self.pausas: list[float] = []

    def __call__(self, segundos):
        self.pausas.append(segundos)


def _product_png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (800, 800), (10, 20, 30)).save(buf, "PNG")
    return buf.getvalue()


def _http() -> httpx.Client:
    """Cliente da ARTE (a imagem do produto). Nada além disto vai à rede — o
    instagrapi é sempre o duplo acima."""
    def handler(request):
        if request.url.host == "cf.shopee.com.br":
            return httpx.Response(200, content=_product_png(),
                                  headers={"content-type": "image/png"})
        raise AssertionError(f"o teste tocou a rede: {request.url}")

    return httpx.Client(transport=httpx.MockTransport(handler))


def _canal(client, sleep=None, **kw) -> InstagramStoryLinkChannel:
    return InstagramStoryLinkChannel("ofiscaldapromo", SENHA, session_path="nao/existe.json",
                                     client=client, sleep=sleep or Relogio(),
                                     link_factory=FakeLink, http_client=_http(), **kw)


def _com_link(post) -> FakeClient:
    return FakeClient(links_do_story=[FakeLink(post.affiliate_link)])


# -- 0. o import é preguiçoso --------------------------------------------------

def test_nenhum_import_de_instagrapi_no_topo_do_modulo():
    """Critério da fase: quem não instalou o extra `stories` não pode ver o
    pipeline quebrar. Todo import de instagrapi mora DENTRO de função."""
    fonte = Path(mod.__file__).read_text(encoding="utf-8")
    arvore = ast.parse(fonte)
    topo = [n for n in arvore.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    nomes = [getattr(n, "module", "") or "" for n in topo]
    nomes += [a.name for n in topo if isinstance(n, ast.Import) for a in n.names]
    assert not any("instagrapi" in n for n in nomes), nomes
    assert "instagrapi" in fonte          # ele É usado — só que lá dentro


def test_nome_e_tetos_do_canal():
    assert InstagramStoryLinkChannel.name == "instagram_story_link"
    assert InstagramStoryLinkChannel.max_per_run == 1
    assert not getattr(InstagramStoryLinkChannel, "manual", False)


# -- 1. a figurinha pedida -----------------------------------------------------

def test_publica_com_a_figurinha_do_link_de_afiliado():
    """Teste 1 do brief: o que chega ao cliente é `links=[StoryLink(webUri=<link
    de afiliado>)]` — o MESMO link curto que vai ao Telegram."""
    post = make_post()
    cliente = _com_link(post)
    res = _canal(cliente).publish(post)

    assert res.ok and res.message_id == "STORY-777"
    etapa, caminho, links = cliente.chamadas[0]
    assert etapa == "upload"
    assert [l.webUri for l in links] == [post.affiliate_link] == ["https://shope.ee/x"]
    assert cliente.arte_existia is True                    # o arquivo estava lá
    assert cliente.arte_bytes.startswith(bytes.fromhex("ffd8ff"))   # e era JPEG


def test_o_canal_pausa_entre_publicar_e_verificar():
    """Duas chamadas coladas à API privada são rajada de bot. A pausa é
    configurável e o teste não dorme de verdade."""
    post, relogio = make_post(), Relogio()
    assert _canal(_com_link(post), sleep=relogio).publish(post).ok
    assert relogio.pausas == [InstagramStoryLinkChannel.pausa] == [3.0]


# -- 2. estado COM LINK --------------------------------------------------------

def test_verificacao_ok_e_sucesso_com_o_pk_e_zera_o_contador():
    post = make_post()
    cliente = _com_link(post)
    canal = _canal(cliente)
    canal.sem_link_seguidos = 1                    # vinha de uma falha anterior
    res = canal.publish(post)

    assert res.ok and res.message_id == "STORY-777" and not res.error
    assert canal.sem_link_seguidos == 0
    assert [c[0] for c in cliente.chamadas] == ["upload", "story_info"]
    assert cliente.chamadas[1][1] == "STORY-777"   # leu de volta o story publicado


def test_a_figurinha_com_barra_no_fim_ainda_e_a_mesma():
    """O `webUri` volta por um modelo pydantic (`HttpUrl`), que normaliza a
    URL. Uma barra final não pode virar "story sem link"."""
    post = make_post()
    cliente = FakeClient(links_do_story=[FakeLink(post.affiliate_link + "/")])
    assert _canal(cliente).publish(post).ok


# -- 3. estado SEM LINK (o modo de falha que motivou a fase) -------------------

def test_story_sem_figurinha_e_falha_com_o_pk_na_mensagem():
    """Teste 3 do brief. O story FICA no ar (apagar é destrutivo), mas isto não
    é sucesso: sem link ele gasta cota e atenção e não converte nada."""
    post = make_post()
    canal = _canal(FakeClient(links_do_story=[]))
    res = canal.publish(post)

    assert not res.ok
    assert "SEM figurinha de link" in res.error
    assert "STORY-777" in res.error and "pk=" in res.error
    assert "instagrapi" in res.error
    assert canal.sem_link_seguidos == 1
    assert canal.disponivel is True                # uma só não desarma


def test_weburi_diferente_do_pedido_e_tratado_como_ausente():
    """Teste 4 do brief: a figurinha existe, mas aponta para outro lugar —
    para o seguidor é a mesma tragédia que não ter figurinha."""
    post = make_post()
    canal = _canal(FakeClient(links_do_story=[FakeLink("https://shope.ee/OUTRA-COISA")]))
    res = canal.publish(post)

    assert not res.ok and "SEM figurinha de link" in res.error
    assert "outro endereço" in res.error
    assert canal.sem_link_seguidos == 1


def test_story_sem_o_atributo_links_tambem_e_ausencia():
    """Uma versão do instagrapi que devolve um story sem `links` não pode
    virar exceção — vira o estado "sem link"."""
    class SemLinks(FakeClient):
        def story_info(self, pk, **kwargs):
            self.chamadas.append(("story_info", pk, None))
            return SimpleNamespace(pk=pk)

    res = _canal(SemLinks()).publish(make_post())
    assert not res.ok and "SEM figurinha de link" in res.error


# -- 4. estado NÃO VERIFICADO --------------------------------------------------

def test_story_info_que_levanta_diz_que_nao_foi_possivel_verificar():
    """Teste 5 do brief, e a lição da 5E: não relate observação que você não
    fez. Não é "sem link" (não sabemos) e não é "verificado" (não lemos)."""
    post = make_post()
    canal = _canal(FakeClient(erro_info=RuntimeError("504 do Instagram")))
    res = canal.publish(post)

    assert not res.ok
    assert "não foi possível verificar" in res.error.lower()
    assert "STORY-777" in res.error                # o story existe; eis o pk
    assert "504 do Instagram" in res.error         # a causa, não engolida
    assert "SEM figurinha" not in res.error        # não afirma ausência
    assert "verificado" not in res.error.lower()   # nem afirma presença
    # Não verificar NÃO é evidência de que o instagrapi quebrou: o contador de
    # desarme não se mexe, e o canal continua armado.
    assert canal.sem_link_seguidos == 0
    assert canal.disponivel is True


def test_upload_sem_pk_nao_tenta_verificar_e_nao_finge_sucesso():
    class SemPk(FakeClient):
        def photo_upload_to_story(self, path, **kwargs):
            super().photo_upload_to_story(path, **kwargs)
            return SimpleNamespace(id="sem pk aqui")

    cliente = SemPk()
    res = _canal(cliente).publish(make_post())
    assert not res.ok and "não foi possível verificar" in res.error.lower()
    assert [c[0] for c in cliente.chamadas] == ["upload"]     # sem pk, sem leitura


def test_verificacao_desligada_nao_afirma_que_verificou():
    """`verificar=False` existe para depuração. O resultado é sucesso, mas o
    canal avisa que ninguém conferiu — o silêncio é que custou 5 meses."""
    post = make_post()
    cliente = _com_link(post)
    canal = _canal(cliente, verificar=False)
    res = canal.publish(post)

    assert res.ok and res.message_id == "STORY-777"
    assert [c[0] for c in cliente.chamadas] == ["upload"]
    assert canal.warnings == [mod.AVISO_SEM_VERIFICACAO]
    assert "SEM verificação" in mod.AVISO_SEM_VERIFICACAO


# -- 4b. o desafio que chega no UPLOAD (rodada de correção, C1) ----------------
#
# A classificação de "esta sessão morreu" existia só no login. Com sessão
# carregada o `login()` normalmente passa direto e o `ChallengeRequired` chega
# no `photo_upload_to_story` — onde não era classificado, não desarmava, e o
# pipeline tentava 3× por run (até 18 chamadas que disparam desafio por dia,
# contra uma conta já sinalizada).


class ChallengeRequired(Exception):
    """Nome IGUAL ao do instagrapi, de propósito: a classificação é por NOME
    (`ERROS_DE_SESSAO`) — o único jeito de reconhecer a exceção sem importar a
    biblioteca, que nem instalada está."""


class ChallengeSelfieCaptcha(ChallengeRequired):
    """Um primo do desafio: no instagrapi a família toda desce de
    `ChallengeError`, então a classificação sobe a hierarquia."""


def test_desafio_no_upload_e_classificado_desarma_e_diz_o_que_fazer():
    """C1. Uma tentativa, canal desarmado, e a mensagem que manda rodar
    `afiliado ig-login` — em vez de "falha ao publicar o story" repetido 3×."""
    post = make_post()
    cliente = FakeClient(erro_upload=ChallengeRequired("challenge_required"))
    canal = _canal(cliente)
    res = canal.publish(post)

    assert not res.ok
    assert mod.SESSAO_INVALIDA in res.error
    assert "afiliado ig-login" in res.error
    assert "ChallengeRequired" in res.error       # a causa, com nome
    assert canal.disponivel is False and canal.max_per_run == 0
    assert canal.warnings == [mod.AVISO_SESSAO]
    assert [c[0] for c in cliente.chamadas] == ["upload"]

    # ...e não tenta de novo neste run.
    assert canal.publish(post).ok is False
    assert [c[0] for c in cliente.chamadas] == ["upload"]


def test_a_classificacao_do_desafio_sobe_a_hierarquia():
    cliente = FakeClient(erro_upload=ChallengeSelfieCaptcha("selfie"))
    canal = _canal(cliente)
    assert mod.SESSAO_INVALIDA in canal.publish(make_post()).error
    assert canal.disponivel is False


def test_erro_comum_no_upload_nao_desarma_o_canal():
    """A contraprova: um timeout não é evidência de sessão morta. O canal
    continua armado (e o freio de 3 falhas seguidas do pipeline vale)."""
    cliente = FakeClient(erro_upload=RuntimeError("timeout do upload"))
    canal = _canal(cliente)
    res = canal.publish(make_post())

    assert not res.ok and "timeout do upload" in res.error
    assert mod.SESSAO_INVALIDA not in res.error
    assert canal.disponivel is True and canal.warnings == []


def test_desafio_na_verificacao_desarma_sem_afirmar_que_falta_link():
    """A leitura de volta também fala com o Instagram. Um desafio ali desarma
    o canal — mas continua sendo NÃO VERIFICADO: não vimos o story."""
    post = make_post()
    cliente = FakeClient(erro_info=ChallengeRequired("challenge_required"))
    canal = _canal(cliente)
    res = canal.publish(post)

    assert not res.ok
    assert "não foi possível verificar" in res.error.lower()
    assert mod.SESSAO_INVALIDA in res.error and "ig-login" in res.error
    assert "SEM figurinha" not in res.error
    assert canal.sem_link_seguidos == 0          # desafio não é instagrapi quebrado
    assert canal.disponivel is False
    assert canal.warnings == [mod.AVISO_SESSAO]


def test_o_pipeline_faz_UMA_chamada_quando_o_desafio_chega_no_upload(tmp_path, monkeypatch):
    """A medida da revisão: com o desafio no upload eram 3 uploads por run.
    Agora o canal se fecha na primeira e o run segue sem ele."""
    from afiliado import llm, pipeline
    from afiliado.state import StateDB
    from tests.test_models import make_offer
    from tests.test_pipeline import CFG, FakeSource, no_network_validator

    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    cliente = FakeClient(erro_upload=ChallengeRequired("challenge_required"))
    canal = _canal(cliente)
    canal.max_per_run = 3
    cfg = {**CFG, "selection": {**CFG["selection"], "posts_per_run": 3}}
    ofertas = [make_offer(item_id=f"a{i}", title=f"Oferta {i}") for i in range(3)]

    summary = pipeline.run(cfg, [FakeSource(ofertas)], [canal], db,
                           validator=no_network_validator)

    assert [c[0] for c in cliente.chamadas] == ["upload"]      # UMA, não três
    assert canal.max_per_run == 0
    assert mod.AVISO_SESSAO in canal.warnings + summary.warnings
    db.close()


# -- 5. desarme automático -----------------------------------------------------

def test_duas_falhas_seguidas_desarmam_o_canal_e_avisam():
    """Teste 6 do brief. Ao desarmar, o canal fica indisponível pelo resto do
    run — `max_per_run = 0` é o atributo que o pipeline já lê para decidir se
    um canal ainda pode publicar."""
    post = make_post()
    canal = _canal(FakeClient(links_do_story=[]))

    assert not canal.publish(post).ok
    assert canal.disponivel is True and canal.warnings == []

    assert not canal.publish(post).ok
    assert canal.sem_link_seguidos == 2
    assert canal.disponivel is False
    assert canal.max_per_run == 0                  # o pipeline para de chamar
    assert canal.warnings == [AVISO_DESARMADO.format(n=2)]
    assert "instagram_story_link" in AVISO_DESARMADO
    assert "instagram_story" in AVISO_DESARMADO and "fallback" in AVISO_DESARMADO


def test_uma_verificacao_boa_no_meio_zera_o_contador():
    post = make_post()
    cliente = FakeClient(links_do_story=[])
    canal = _canal(cliente)

    assert not canal.publish(post).ok and canal.sem_link_seguidos == 1
    cliente.links_do_story = [FakeLink(post.affiliate_link)]
    assert canal.publish(post).ok and canal.sem_link_seguidos == 0
    cliente.links_do_story = []
    assert not canal.publish(post).ok and canal.sem_link_seguidos == 1
    assert canal.disponivel is True                # nunca houve DUAS seguidas
    assert canal.warnings == []


def test_canal_desarmado_nao_toca_mais_no_instagram():
    post = make_post()
    cliente = FakeClient(links_do_story=[])
    canal = _canal(cliente)
    canal.publish(post)
    canal.publish(post)
    chamadas = len(cliente.chamadas)

    res = canal.publish(post)
    assert not res.ok and "desarmado" in res.error
    assert len(cliente.chamadas) == chamadas       # nem upload, nem leitura


def test_o_pipeline_para_de_chamar_o_canal_desarmado(tmp_path, monkeypatch):
    """O desarme só vale alguma coisa se o PIPELINE respeitar. Ele já respeita:
    `max_per_run` é lido a cada oferta (`aberto()`), e zerá-lo fecha o canal
    pelo resto do run sem que o pipeline precise conhecer este canal."""
    from afiliado import llm, pipeline
    from afiliado.state import StateDB
    from tests.test_models import make_offer
    from tests.test_pipeline import CFG, FakeSource, no_network_validator

    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    db = StateDB(tmp_path / "s.db")
    cliente = FakeClient(links_do_story=[])          # todo story sai sem figurinha
    canal = _canal(cliente)
    canal.max_per_run = 3                            # 3 ofertas cabem no run
    cfg = {**CFG, "selection": {**CFG["selection"], "posts_per_run": 3}}
    ofertas = [make_offer(item_id=f"a{i}", title=f"Oferta {i}") for i in range(3)]

    summary = pipeline.run(cfg, [FakeSource(ofertas)], [canal], db,
                           validator=no_network_validator)

    assert [c[0] for c in cliente.chamadas].count("upload") == 2   # não houve a 3ª
    assert canal.max_per_run == 0 and canal.disponivel is False
    assert summary.published == []
    assert all("SEM figurinha de link" in motivo for _, motivo in summary.discarded)
    # O aviso do desarme chega ao resumo pelo mesmo caminho dos outros avisos de
    # canal (o pipeline drena `warnings` depois de cada publish); se essa drenagem
    # não existir, ele fica no canal. Nos dois mundos, ele EXISTE.
    assert AVISO_DESARMADO.format(n=2) in canal.warnings + summary.warnings
    db.close()


def test_max_sem_link_configuravel():
    post = make_post()
    canal = _canal(FakeClient(links_do_story=[]), max_sem_link=1)
    assert not canal.publish(post).ok
    assert canal.disponivel is False and canal.warnings == [AVISO_DESARMADO.format(n=1)]


# -- 5b. o desarme atravessa runs (rodada de correção, C2) ---------------------
#
# O desarme vivia só na instância e um `SEM_LINK` não consumia teto nenhum: a
# revisão mediu 2 uploads por run, `count_posts_today == 0`, e tudo zerado no
# processo seguinte — ~12 stories sem figurinha por dia, para sempre,
# invisíveis ao ritmo e ao teto da 5A.


def _um_run(banco, links_do_story, ofertas=3, max_sem_link=2):
    """UM run do pipeline, do jeito que o processo seguinte o encontraria: o
    banco reaberto do arquivo e um canal recém-construído — nada em memória
    atravessa daqui para o próximo."""
    from afiliado import pipeline
    from afiliado.state import StateDB
    from tests.test_models import make_offer
    from tests.test_pipeline import CFG, FakeSource, no_network_validator

    db = StateDB(banco)
    cliente = FakeClient(links_do_story=links_do_story)
    canal = _canal(cliente, estado=db, max_sem_link=max_sem_link)
    canal.max_per_day = 6
    lote = [make_offer(item_id=f"a{i}", title=f"Oferta {i}") for i in range(ofertas)]
    resumo = pipeline.run(CFG, [FakeSource(lote)], [canal], db,
                          validator=no_network_validator)
    publicados = db.count_posts_today(canal.name)
    db.close()
    return cliente, canal, resumo, publicados


def test_dois_runs_em_processos_separados_nao_passam_de_max_sem_link(tmp_path,
                                                                     monkeypatch):
    """C2, o teste que atravessa PROCESSOS: cada run reabre o banco e constrói
    um canal novo. Com o instagrapi quebrado, o DIA inteiro não passa de
    `max_sem_link` uploads — e cada story que foi ao ar está no banco."""
    from afiliado import llm
    from tests.test_pipeline import _congela

    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    _congela(monkeypatch, 20, 0)
    banco = tmp_path / "state_stories.db"

    c1, _, resumo1, publicados1 = _um_run(banco, [])
    c2, _, resumo2, publicados2 = _um_run(banco, [])
    c3, canal3, _, publicados3 = _um_run(banco, [])

    uploads = sum([e[0] for e in c.chamadas].count("upload") for c in (c1, c2, c3))
    assert uploads == 2                       # == max_sem_link, no dia inteiro
    assert [c[0] for c in c3.chamadas] == []  # o 3º run nem tocou no Instagram

    # Os stories que foram ao ar contam para o teto do dia e para o dedupe.
    assert (publicados1, publicados2, publicados3) == (1, 2, 2)

    # E o canal do 3º processo já nasce fechado, dizendo por quê.
    assert canal3.disponivel is False and canal3.max_per_run == 0
    assert canal3.warnings == [AVISO_DESARMADO.format(n=2)]
    assert AVISO_DESARMADO.format(n=2) in resumo2.warnings + canal3.warnings
    assert resumo1.published == resumo2.published == []


def test_o_desarme_por_sessao_invalida_tambem_atravessa_o_run(tmp_path):
    """Um desafio no upload fecha o canal pelo DIA — não pelo run. Seis
    processos por dia insistindo contra uma conta sinalizada é exatamente o
    que a fase existe para não fazer."""
    from afiliado.state import StateDB

    db = StateDB(tmp_path / "s.db")
    canal = _canal(FakeClient(erro_upload=ChallengeRequired("challenge_required")),
                   estado=db)
    assert not canal.publish(make_post()).ok
    assert db.day_flag(mod.CHAVE_DESARMADO) == mod.AVISO_SESSAO

    depois = _canal(FakeClient(links_do_story=[]), estado=db)
    assert depois.disponivel is False and depois.max_per_run == 0
    assert depois.warnings == [mod.AVISO_SESSAO]
    assert not depois.publish(make_post()).ok
    assert depois.client.chamadas == []          # nenhuma chamada nova
    db.close()


def test_o_contador_de_sem_link_atravessa_o_run_e_uma_verificacao_boa_o_zera(tmp_path):
    from afiliado.state import StateDB

    db = StateDB(tmp_path / "s.db")
    post = make_post()
    canal = _canal(FakeClient(links_do_story=[]), estado=db)
    assert not canal.publish(post).ok
    assert canal.disponivel is True               # uma só não desarma
    assert db.day_flag(mod.CHAVE_SEM_LINK) == "1"

    # Run seguinte, processo novo: o contador veio junto — e o instagrapi voltou.
    depois = _canal(_com_link(post), estado=db)
    assert depois.sem_link_seguidos == 1
    assert depois.publish(post).ok
    assert depois.sem_link_seguidos == 0
    assert db.day_flag(mod.CHAVE_SEM_LINK) == ""  # zerado no banco também
    db.close()


def test_canal_sem_estado_persistente_continua_funcionando(tmp_path):
    """`estado` é opcional: o canal construído sem banco (testes, uso avulso)
    se comporta como antes — desarme só dentro do processo."""
    canal = _canal(FakeClient(links_do_story=[]), max_sem_link=1)
    assert not canal.publish(make_post()).ok
    assert canal.disponivel is False


def test_o_story_sem_figurinha_e_um_post_publicado(tmp_path):
    """A raiz do C2: o story está na conta e o público vê. `ok=False` (não
    converte) mas `publicado=True` — é o que faz o pipeline gravá-lo."""
    post = make_post()
    res = _canal(FakeClient(links_do_story=[])).publish(post)
    assert not res.ok and res.publicado is True and res.message_id == "STORY-777"

    # Não verificável: o story também foi ao ar.
    res = _canal(FakeClient(erro_info=RuntimeError("504"))).publish(post)
    assert not res.ok and res.publicado is True and res.message_id == "STORY-777"

    # Upload que levantou: NÃO afirmamos que foi ao ar.
    res = _canal(FakeClient(erro_upload=RuntimeError("timeout"))).publish(post)
    assert not res.ok and res.publicado is False


# -- 6. o arquivo temporário da arte ------------------------------------------

def test_o_arquivo_temporario_e_apagado_depois_do_upload():
    post = make_post()
    cliente = _com_link(post)
    _canal(cliente).publish(post)
    caminho = cliente.chamadas[0][1]
    assert cliente.arte_existia is True and not caminho.exists()


def test_o_arquivo_temporario_e_apagado_ate_quando_o_upload_levanta():
    """Teste 8 do brief: `finally`, sempre. Um story por publicação, 6 por dia,
    para sempre — vazar o arquivo é encher o disco da máquina do dono."""
    cliente = FakeClient(erro_upload=RuntimeError("timeout do upload"))
    res = _canal(cliente).publish(make_post())
    caminho = cliente.chamadas[0][1]

    assert not res.ok and "timeout do upload" in res.error
    assert cliente.arte_existia is True and not caminho.exists()


def test_falha_ao_gerar_a_arte_nao_publica_nada():
    def sem_imagem(request):
        return httpx.Response(404)

    canal = InstagramStoryLinkChannel(
        "ofiscaldapromo", SENHA, session_path="nao/existe.json", client=FakeClient(),
        sleep=Relogio(), link_factory=FakeLink,
        http_client=httpx.Client(transport=httpx.MockTransport(sem_imagem)))
    res = canal.publish(make_post())
    assert not res.ok and "arte" in res.error
    assert canal.client.chamadas == []


# -- 7. sessão / login ---------------------------------------------------------

class FakeSessaoInvalida(Exception):
    """Faz o papel de `LoginRequired`/`ChallengeRequired`/`AccountSuspended`."""


def _instagrapi_falso(monkeypatch, cliente, erro_login=None, registro=None):
    """Substitui o import preguiçoso do instagrapi por duplos. É o único jeito
    de exercitar o login sem instalar a biblioteca (e sem tocar o Instagram)."""
    class Client:
        def __init__(self):
            self.settings = None
            self._cliente = cliente

        def load_settings(self, caminho):
            registro.append(("load_settings", str(caminho)))

        def dump_settings(self, caminho):
            registro.append(("dump_settings", str(caminho)))
            Path(caminho).write_text('{"uuids": {}}', encoding="utf-8")

        def login(self, usuario, senha, **kwargs):
            registro.append(("login", usuario, kwargs.get("verification_code")))
            if erro_login is not None:
                raise erro_login
            return True

        def __getattr__(self, nome):     # upload/story_info vêm do duplo
            return getattr(self._cliente, nome)

    monkeypatch.setattr(mod, "_instagrapi",
                        lambda: (Client, FakeLink, (FakeSessaoInvalida,)))
    return Client


def test_sem_sessao_no_disco_o_canal_loga_e_guarda_a_sessao(tmp_path, monkeypatch):
    post, registro = make_post(), []
    _instagrapi_falso(monkeypatch, _com_link(post), registro=registro)
    sessao = tmp_path / "ig_session.json"
    canal = InstagramStoryLinkChannel("ofiscaldapromo", SENHA, session_path=sessao,
                                      sleep=Relogio(), http_client=_http())
    assert canal.publish(post).ok

    assert [c[0] for c in registro] == ["login", "dump_settings"]
    assert sessao.is_file()                       # sessão guardada: um login só
    assert SENHA not in sessao.read_text(encoding="utf-8")


def test_com_sessao_no_disco_o_canal_carrega_antes_de_logar(tmp_path, monkeypatch):
    post, registro = make_post(), []
    _instagrapi_falso(monkeypatch, _com_link(post), registro=registro)
    sessao = tmp_path / "ig_session.json"
    sessao.write_text('{"uuids": {}}', encoding="utf-8")
    canal = InstagramStoryLinkChannel("ofiscaldapromo", SENHA, session_path=sessao,
                                      sleep=Relogio(), http_client=_http())
    assert canal.publish(post).ok
    assert [c[0] for c in registro][:2] == ["load_settings", "login"]


def test_o_login_acontece_uma_vez_so(tmp_path, monkeypatch):
    post, registro = make_post(), []
    _instagrapi_falso(monkeypatch, _com_link(post), registro=registro)
    canal = InstagramStoryLinkChannel("ofiscaldapromo", SENHA,
                                      session_path=tmp_path / "s.json",
                                      sleep=Relogio(), http_client=_http())
    assert canal.publish(post).ok
    assert canal.publish(post).ok
    assert [c[0] for c in registro].count("login") == 1


@pytest.mark.parametrize("erro,esperado", [
    # O que o instagrapi levanta quando a sessão morreu (desafio, senha
    # trocada, conta suspensa): aí sim o canal AFIRMA que a sessão está
    # inválida.
    (FakeSessaoInvalida("challenge_required"), "sessão do Instagram inválida"),
    # Qualquer outra coisa (rede, por exemplo) não é prova de sessão morta —
    # o canal diz o que aconteceu, não o que supõe.
    (RuntimeError("qualquer outra coisa"), "falha ao entrar no Instagram"),
])
def test_sessao_invalida_da_mensagem_acionavel_sem_laco_de_retry(tmp_path, monkeypatch,
                                                                 erro, esperado):
    """Teste 7 do brief. `challenge_required` respondido com retry em laço é o
    caminho mais curto para perder a conta."""
    post, registro = make_post(), []
    _instagrapi_falso(monkeypatch, _com_link(post), erro_login=erro, registro=registro)
    canal = InstagramStoryLinkChannel("ofiscaldapromo", SENHA,
                                      session_path=tmp_path / "s.json",
                                      sleep=Relogio(), http_client=_http())
    res = canal.publish(post)

    assert not res.ok
    assert esperado in res.error and "afiliado ig-login" in res.error
    assert str(erro) in res.error                 # a causa, sem a credencial
    assert SENHA not in res.error
    assert [c[0] for c in registro].count("login") == 1

    # ...e não tenta de novo neste run: o canal se fecha e avisa.
    assert canal.publish(post).ok is False
    assert [c[0] for c in registro].count("login") == 1
    assert canal.max_per_run == 0
    assert any("ig-login" in a for a in canal.warnings)


def test_instagrapi_ausente_vira_erro_acionavel(tmp_path, monkeypatch):
    def sem_biblioteca():
        raise ImportError("No module named 'instagrapi'")

    monkeypatch.setattr(mod, "_instagrapi", sem_biblioteca)
    canal = InstagramStoryLinkChannel("ofiscaldapromo", SENHA,
                                      session_path=tmp_path / "s.json",
                                      sleep=Relogio(), http_client=_http())
    res = canal.publish(make_post())
    assert not res.ok and "stories" in res.error and "pip install" in res.error


# -- 8. a senha nunca aparece --------------------------------------------------

def test_nenhuma_mensagem_do_canal_carrega_a_senha(capsys, tmp_path, monkeypatch):
    """Teste 10 do brief. Inclusive quando a senha vem DENTRO do texto de uma
    exceção de terceiro — o canal a raspa antes de devolver."""
    post = make_post()
    cenarios = [
        _canal(FakeClient(erro_upload=RuntimeError(f"login {SENHA} recusado"))),
        _canal(FakeClient(erro_info=RuntimeError(f"cookie de {SENHA}"))),
        _canal(FakeClient(links_do_story=[])),
    ]
    registro = []
    _instagrapi_falso(monkeypatch, _com_link(post),
                      erro_login=RuntimeError(f"senha {SENHA} inválida"), registro=registro)
    cenarios.append(InstagramStoryLinkChannel("ofiscaldapromo", SENHA,
                                              session_path=tmp_path / "s.json",
                                              sleep=Relogio(), http_client=_http()))
    for canal in cenarios:
        res = canal.publish(post)
        assert not res.ok
        assert SENHA not in res.error, res.error
        assert all(SENHA not in a for a in canal.warnings)
    saida = capsys.readouterr()
    assert SENHA not in saida.out and SENHA not in saida.err


def test_nenhum_print_do_projeto_toca_a_senha():
    """A outra metade do teste 10 do brief: uma varredura no fonte INTEIRO, não
    só neste canal. Nenhum `print` do projeto pode receber a senha — nem por
    variável, nem por atributo. Imprimir o NOME da variável ("IG_PASSWORD
    ausente", no doctor) é o oposto disso e continua permitido: é presença,
    nunca valor."""
    proibidos = {"senha", "password", "IG_PASSWORD"}
    src = Path(mod.__file__).resolve().parents[2]
    for arquivo in src.rglob("*.py"):
        arvore = ast.parse(arquivo.read_text(encoding="utf-8"))
        for no in ast.walk(arvore):
            if not (isinstance(no, ast.Call) and getattr(no.func, "id", "") == "print"):
                continue
            usados = {n.id for n in ast.walk(no) if isinstance(n, ast.Name)}
            usados |= {n.attr for n in ast.walk(no) if isinstance(n, ast.Attribute)}
            assert not usados & proibidos, f"{arquivo}: print com {usados & proibidos}"


def test_o_canal_nao_imprime_nada():
    """Este canal roda na máquina do dono, num terminal. `print` de qualquer
    coisa aqui é uma credencial a um passo de distância do histórico do shell —
    o que ele tem a dizer sai por `PublishResult` e por `warnings`."""
    fonte = Path(mod.__file__).read_text(encoding="utf-8")
    arvore = ast.parse(fonte)
    chamadas = [n for n in ast.walk(arvore)
                if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "print"]
    assert chamadas == []

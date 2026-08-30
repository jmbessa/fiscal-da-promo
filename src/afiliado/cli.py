import argparse
import io
import os
import signal
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import httpx

from afiliado import (categorias, config, creative, flagrante, llm, pipeline, preco_real,
                      pricing, selection, shopee_checkout, video)
from afiliado.channels import instagram_story_link
from afiliado.channels.instagram_common import (GRAPH_HOSTS, cota_de_publicacao,
                                                graph_error)
from afiliado.channels.instagram_feed import InstagramFeedChannel, sanitiza_titulo
from afiliado.channels.instagram_reel import InstagramReelChannel
from afiliado.channels.instagram_story import InstagramStoryChannel
from afiliado.channels.instagram_story_link import InstagramStoryLinkChannel
from afiliado.channels.story_dispatch import StoryDispatchChannel
from afiliado.channels.telegram import TelegramChannel, send_photo_bytes, send_text
from afiliado.errors import SourceError
from afiliado.models import CopyParts, Post, format_brl
from afiliado.sources.meli import DEFAULT_OFFERS_PATH, MeliSource
from afiliado.sources.shopee import ShopeeSource
from afiliado.state import StateDB
from afiliado.watchlist import load_watchlist


# --- Fase 5D: a peça de feed -------------------------------------------------

FEED_TIPOS = ("termometro", "flagrante")

# Onde `--dry-run` grava as artes para o dono olhar antes de publicar.
PREVIEWS_DIR = Path(".claude/previews")

# O carrossel é publicado pelo `InstagramFeedChannel` (mesmo endpoint, mesma
# cota da Meta), mas tem TETO PRÓPRIO — ele é um post por vez e não deve
# competir com o feed de oferta única pelo `max_per_day` daquele canal.
#
# Duas chaves em `posted`, e o motivo importa:
#   - CANAL_CARROSSEL guarda UMA linha por carrossel publicado (a primeira
#     oferta como representante). É essa contagem que `count_posts_today` lê,
#     e é o que faz "um carrossel = um post" valer também para o teto.
#   - CANAL_CARROSSEL_ITEM guarda uma linha por oferta que entrou no post. Não
#     conta para teto nenhum; existe para o DEDUPE (que é por fonte+item,
#     qualquer canal) não oferecer os mesmos seis produtos amanhã.
CANAL_CARROSSEL = "instagram_carrossel"
CANAL_CARROSSEL_ITEM = "instagram_carrossel_item"
DEFAULT_CARROSSEL_MAX_PER_DAY = 1

# Capa e fecho ocupam dois dos oito slides (ver creative.CARROSSEL_MAX_SLIDES).
MAX_OFERTAS_NO_CARROSSEL = creative.CARROSSEL_MAX_SLIDES - 2

SUBTITULO_TERMOMETRO = "O Fiscal olhou o histórico de preço de cada uma."

# Por quantos dias um flagrante JÁ DESPACHADO ao chat de operações não volta.
# Ele não registrava nada: agendado todo dia, o produto de maior gravidade
# voltaria toda manhã, porque nada no mundo muda de um dia para o outro num
# histórico de 90 dias. A marca vai em `day_flags` e NUNCA em `posted` —
# registrar como publicação bloquearia o produto no Telegram por 30 dias
# (`selection.dedupe_days`), que é efeito colateral de outra decisão. É por
# PRODUTO, não "um flagrante por semana": bloqueado o pior, o comando desce
# para o próximo, senão um item calaria uma semana inteira de denúncias.
FLAGRANTE_DEDUPE_DAYS = 7
FLAGRANTE_FLAG_PREFIXO = "flagrante"

# Fase 5G (G2): o teto DIÁRIO do flagrante, no mesmo lugar (`day_flags`) e com
# a mesma semântica do dedupe por produto. Ele existe porque o passo do Actions
# deixou de se prender ao cron das 08:00: ~15 dos 16 disparos são descartados
# pelo agendador (medido em 2026-08-28), e prender a peça a um slug de cron era
# feed que nunca sai. Rodando em todo disparo, o dedupe por PRODUTO não segura
# nada — ele desce para o próximo item, e o ops receberia 16 flagrantes
# diferentes por dia. A marca é gravada só DEPOIS do despacho bem-sucedido:
# um envio que falhou não gastou o dia, e o disparo seguinte repete.
FLAGRANTE_DIA_FLAG = "feed_flagrante_do_dia"

# Fase 5I: a produção saiu do GitHub Actions e passou para o Agendador de
# Tarefas do Windows, na máquina do dono. Estes são os nomes das duas tarefas
# que `deploy/agendar-windows.ps1` cria — e que o `doctor` procura. O nome é o
# CONTRATO entre os dois lados: tests/test_agendador_windows.py trava os dois.
TAREFA_RUN = "FiscalDaPromo-Run"
TAREFA_STORIES = "FiscalDaPromo-Stories"
# As duas peças de FEED entram na lista porque o único lugar que chamava
# `afiliado feed` era o passo "Conteúdo do feed" do publish.yml: desligar o
# `schedule:` de lá sem agendá-las mataria o carrossel e o flagrante em
# SILÊNCIO, que é o defeito que esta fase existe para acabar.
TAREFA_FEED = "FiscalDaPromo-Feed"
TAREFA_FLAGRANTE = "FiscalDaPromo-Flagrante"
TAREFAS_DA_PRODUCAO = (TAREFA_RUN, TAREFA_STORIES, TAREFA_FEED, TAREFA_FLAGRANTE)
SCRIPT_DO_AGENDADOR = "deploy/agendar-windows.ps1"
RUNBOOK_DA_PRODUCAO = "docs/runbooks/producao-windows.md"


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="afiliado")
    sub = p.add_subparsers(dest="cmd", required=True)
    prun = sub.add_parser("run", help="executa o pipeline")
    prun.add_argument("--dry-run", action="store_true",
                      help="APIs reais, mas imprime em vez de publicar")
    prun.add_argument("--config", default="config.yaml")
    prun.add_argument("--posts-per-run", type=int, default=None,
                      help="sobrepõe selection.posts_per_run (a máquina do dono roda a "
                           "cada 15 min e precisa de folga para disparos perdidos; a "
                           "VPS, que roda a cada 5, não precisa)")
    pdoc = sub.add_parser("doctor", help="verifica credenciais e dependências")
    pdoc.add_argument("--config", default="config.yaml")
    # Fase 5F: os canais de story ganham comando próprio porque o
    # `instagram_story_link` (instagrapi) NÃO pode rodar no GitHub Actions — IP
    # de datacenter, diferente a cada execução, é o que mais dispara
    # `challenge_required`. Este comando é para o dono agendar na máquina dele.
    pst = sub.add_parser("stories", help="roda SÓ os canais de story (na máquina do dono)")
    pst.add_argument("--posts", type=int, default=None,
                     help="sobrepõe selection.posts_per_run")
    pst.add_argument("--dry-run", action="store_true",
                     help="APIs reais, mas imprime em vez de publicar")
    pst.add_argument("--config", default="config.yaml")
    plog = sub.add_parser("ig-login", help="cria/renova a sessão do instagrapi")
    plog.add_argument("--config", default="config.yaml")
    # Fase 5D: a peça de FEED tem comando próprio porque não é o mesmo trabalho
    # do `run` — ela junta VÁRIAS ofertas num post só (ou nomeia um vendedor) e
    # sai 2 ou 3 vezes por semana, não a cada 5 minutos.
    pfeed = sub.add_parser("feed", help="monta a peça de feed do dia (carrossel ou flagrante)")
    pfeed.add_argument("--dry-run", action="store_true",
                       help=f"grava os PNGs em {PREVIEWS_DIR} e não publica nem "
                            "escreve no banco")
    pfeed.add_argument("--tipo", choices=FEED_TIPOS, default=FEED_TIPOS[0],
                       help="termometro (padrão): carrossel do dia, publicado. "
                            "flagrante: gráfico do 'de' que não se sustenta, "
                            "despachado ao chat de operações SEM publicar")
    pfeed.add_argument("--config", default="config.yaml")
    # Fase 5P: exercitar a leitura do preço de checkout À MÃO, com a leitura
    # ainda DESLIGADA no config. É o instrumento da decisão: o dono roda isto,
    # compara com a tela do celular dele e só então liga o interruptor.
    ppreco = sub.add_parser("preco-real",
                            help="lê no navegador o preço de checkout de UM anúncio "
                                 "e imprime o que o post publicaria (não publica nada)")
    ppreco.add_argument("alvo", help="URL do anúncio, ou o itemId (procurado no "
                                     "estoque de candidatas do state.db)")
    ppreco.add_argument("--preco", default="",
                        help="o preço da API em reais (ex.: 599,00) — obrigatório "
                             "quando o alvo é uma URL; é a ÂNCORA da leitura")
    ppreco.add_argument("--config", default="config.yaml")
    return p


def _shopee(db: StateDB | None = None) -> ShopeeSource:
    """`db` é o cursor da varredura rotativa (fase 5C, M1): sem ele a rotação
    existe dentro do run mas não sobrevive ao processo — é o caso do `doctor`."""
    return ShopeeSource(os.environ["SHOPEE_APP_ID"], os.environ["SHOPEE_APP_SECRET"], db=db)


MELI_ENV_AVISO = ("⚠️ fonte meli ignorada: variável MELI_CLIENT_ID/MELI_CLIENT_SECRET ausente "
                  "(ver docs/runbooks/meli-setup.md)")

# Um aviso por CANAL: o `warn_once` do pipeline deduplica pelo texto (sem os
# dígitos), então uma mensagem única faria o feed engolir o aviso do story.
ART_HOST_AVISO_TMPL = ("⚠️ {canal}: arte hospedada pelo bot do canal — "
                       "defina ART_HOST_BOT_TOKEN")
# As duas constantes abaixo existem para os TESTES: `_monta_instagram` formata
# o template direto, com o nome do canal que está montando. `ART_HOST_AVISO`
# mantém de propósito o valor exato de antes da 5E (o do feed) — é o que os
# testes da 5C afirmam, e mudar a string só para "arrumar" o nome moveria
# testes que não têm nada a ver com esta fase.
ART_HOST_AVISO = ART_HOST_AVISO_TMPL.format(canal="instagram_feed")
ART_HOST_AVISO_STORY = ART_HOST_AVISO_TMPL.format(canal="instagram_story")

# Fase 5F, rodada de correção (I1): o `afiliado stories` monta SÓ o canal de
# API privada. Ele é o único que não pode rodar no Actions; todos os outros —
# inclusive o `instagram_story` (Graph API) e o `story_dispatch` — saem pelo
# `afiliado run`. Montar os mesmos canais nos dois comandos daria dois tetos
# diários e dois dedupes (bancos diferentes, ver `state.stories_path`) sobre a
# MESMA conta.
CANAIS_DO_STORIES = ("instagram_story_link",)

# Banco do comando local. `data/state.db` é rastreado no git e o Actions o
# commita a cada run: se o `afiliado stories` escrevesse nele, o arquivo
# divergiria na máquina do dono e todo `git pull` viraria conflito binário.
# Consequência documentada no runbook: o dedupe deste canal é independente do
# resto — um produto que saiu no Telegram de manhã pode virar story à tarde.
DEFAULT_STORIES_STATE = "data/state_stories.db"

# O canal da API privada é montado SÓ pelo `afiliado stories`. `afiliado run` é
# o que roda no GitHub Actions, cujo IP é de datacenter e muda a cada execução:
# sessão de app móvel forjada + IP novo a cada hora é o padrão que mais dispara
# `challenge_required`. Ligado no config e chamado pelo comando errado, o canal
# é ignorado com este aviso — em vez de rodar onde não devia.
AVISO_STORY_LINK_FORA_DO_RUN = (
    "⚠️ canal instagram_story_link ligado, mas ignorado neste comando — ele só roda "
    "em `afiliado stories`, na máquina do dono (nunca no GitHub Actions)")
STORY_LINK_SEM_ENV = ("⚠️ canal instagram_story_link ignorado: variável "
                      "IG_USERNAME/IG_PASSWORD ausente "
                      "(ver docs/runbooks/instagrapi-stories.md)")
STORY_LINK_SEM_SESSAO = ("⚠️ instagram_story_link: sem sessão salva em {caminho} — "
                         "rode `afiliado ig-login` antes (login novo a cada run é o "
                         "que atrai desafio)")

# I1: o canal oficial de story é da Graph API e sai pelo `afiliado run` (é o que
# o Actions executa). Montá-lo também aqui daria dois tetos de 6/dia e dois
# dedupes, em bancos diferentes, sobre a mesma conta.
AVISO_STORY_OFICIAL_FORA_DO_STORIES = (
    "⚠️ canal instagram_story ligado, mas ignorado por `afiliado stories` — ele é da "
    "Graph API e sai pelo `afiliado run`; montá-lo aqui daria dois tetos diários e "
    "dois dedupes sobre a mesma conta")

# I3: a regra de ouro deixa de ser conselho. Com os dois canais ligados, o
# mesmo post saía pela API privada e pela oficial dentro da MESMA iteração do
# laço — mesma conta, mesmo minuto: o padrão que a investigação identificou
# como o que mais chama atenção. Falha FECHADA: o canal de risco é o que não
# sobe.
AVISO_REGRA_DE_OURO = (
    "⚠️ canal instagram_story_link NÃO montado: o instagram_story (Graph API) está "
    "ligado ao mesmo tempo. Publicar pela API privada e pela oficial na MESMA conta é "
    "o padrão que chama atenção — deixe só um em config.yaml e rode `afiliado doctor` "
    "(ver docs/runbooks/instagrapi-stories.md)")


def _meli(cfg: dict | None = None) -> MeliSource | None:
    client_id = _env("MELI_CLIENT_ID")
    client_secret = _env("MELI_CLIENT_SECRET")
    if not (client_id and client_secret):
        return None
    me = (cfg or {}).get("meli") or {}
    extra = {"links_path": me["links_path"]} if me.get("links_path") else {}
    return MeliSource(client_id, client_secret, refresh_token=_env("MELI_REFRESH_TOKEN"),
                      **extra)


def _aviso(avisos: list[str], texto: str) -> None:
    """Aviso de montagem: vai ao stdout (journal) E à lista que o pipeline
    injeta em `summary.warnings` — antes era só o print, e o chat de ops via
    "✅ Run concluído" com o Instagram/ML mudos (C4b)."""
    print(texto)
    avisos.append(texto)


def _build_sources(cfg: dict, db: StateDB | None = None) -> tuple[list, list[str]]:
    """Monta as fontes habilitadas em `sources:` (config.yaml) e devolve
    também os avisos de montagem.

    Seção ausente equivale a `{"shopee": True}` (comportamento de antes da
    fase 3) — o Mercado Livre nasce desligado. Fonte ligada sem env
    necessária: aviso (stdout + resumo de ops) e segue sem ela, nunca
    derruba o run (mesmo padrão de `_build_channels`)."""
    src_cfg = cfg.get("sources") or {"shopee": True}
    sources: list = []
    avisos: list[str] = []
    if src_cfg.get("shopee", True):
        sources.append(_shopee(db))
    if src_cfg.get("meli", False):
        meli = _meli(cfg)
        if meli is None:
            _aviso(avisos, MELI_ENV_AVISO)
        else:
            sources.append(meli)
    return sources, avisos


def _build_preco_real(cfg: dict, db: StateDB | None, avisos: list[str]):
    """O leitor de preço de checkout (fase 5P), ou None — que é o padrão.

    Mesmo contrato de `_build_channels`: o que impede a leitura de subir vira
    AVISO no stdout e no resumo de operações, nunca uma exceção. O `db` vai
    junto porque é nele que o desarme sobrevive ao processo, e os avisos que o
    leitor já traz (um dia que amanheceu desarmado) são drenados aqui — senão
    eles ficariam presos no objeto num run que não publicou nada."""
    leitor, recusas = preco_real.monta(cfg, estado=db)
    for texto in recusas:
        _aviso(avisos, texto)
    if leitor is not None:
        for texto in pipeline.drena_avisos(leitor):
            _aviso(avisos, texto)
    return leitor


def _env(name: str) -> str:
    """os.environ.get com .strip() — mata o footgun de credencial colada com
    espaço/quebra de linha nas pontas antes que ela chegue a algum canal."""
    return os.environ.get(name, "").strip()


def _senha() -> str:
    """`IG_PASSWORD` SEM `.strip()`.

    Senha de Instagram pode terminar (ou começar) em espaço, e o `.strip()` do
    `_env` a mutilava em silêncio: o login voltava `BadPassword`, que o canal
    relata como "sessão inválida" — mandando o dono para o galho errado do
    runbook (`afiliado ig-login`, que falharia de novo) em vez de "confira a
    senha no .env". Um token de API não tem esse problema; uma senha tem.
    """
    return os.environ.get("IG_PASSWORD", "")


def state_path(cfg: dict, stories: bool = False) -> str:
    """O banco de estado deste comando.

    `afiliado stories` usa um banco PRÓPRIO (`state.stories_path`, padrão
    `data/state_stories.db`, no .gitignore): `data/state.db` é rastreado no git
    e o Actions o commita a cada run — o comando local escrevendo nele faria
    todo `git pull` virar conflito binário. O preço, documentado no runbook: o
    dedupe e o histórico deste canal são independentes do resto."""
    st = cfg.get("state") or {}
    if stories:
        return st.get("stories_path") or DEFAULT_STORIES_STATE
    return st["path"]


def _abre_estado(cfg: dict, stories: bool = False) -> StateDB:
    return StateDB(state_path(cfg, stories),
                   timezone=pipeline.schedule_settings(cfg)["timezone"])


def _channel_settings(raw) -> tuple[bool, int | None]:
    """Normaliza a entrada de um canal em `channels:` — bool ou dict — para
    `(enabled, max_per_day)`. Dict: `enabled` (default True) e `max_per_day`
    (opcional, int > 0)."""
    if isinstance(raw, dict):
        return bool(raw.get("enabled", True)), raw.get("max_per_day")
    return bool(raw), None


def _instagram_api(cfg: dict) -> str:
    """Variante da API do Instagram (config `instagram.api`): instagram_login (padrão) ou facebook_login."""
    api = (cfg.get("instagram") or {}).get("api") or "instagram_login"
    return api if api in GRAPH_HOSTS else "instagram_login"


def story_link_cfg(cfg: dict) -> dict:
    """A entrada `channels.instagram_story_link` como dict (bool também vale).
    Além de `enabled`/`max_per_day`, ela carrega `max_sem_link` e
    `session_path` — que o canal, o `ig-login` e o `doctor` precisam ler."""
    raw = (cfg.get("channels") or {}).get(InstagramStoryLinkChannel.name)
    return dict(raw) if isinstance(raw, dict) else {"enabled": bool(raw)}


def ig_session_path(cfg: dict) -> Path:
    """Caminho da sessão do instagrapi. Um só lugar: o canal, o `ig-login` e o
    `doctor` têm de falar do MESMO arquivo."""
    return Path(story_link_cfg(cfg).get("session_path")
                or instagram_story_link.DEFAULT_SESSION_PATH)


def _monta_story_link(ch_cfg: dict, cfg: dict, channels: list, avisos: list[str],
                      brand_handle: str | None, brand_name: str,
                      api_privada: bool, db: StateDB | None = None) -> None:
    """Monta o `instagram_story_link` (instagrapi, story COM figurinha).

    Ele não se parece com os outros canais do Instagram: não usa Graph API, não
    hospeda arte no Telegram, e as credenciais dele são usuário e SENHA. E só é
    montado quando `api_privada` é verdadeiro — isto é, sob `afiliado stories`.

    Duas recusas, as duas fechadas: com o `instagram_story` ligado ele não sobe
    (a regra de ouro, I3), e sem `db` ele não teria como lembrar do desarme
    depois que o processo morre (C2) — por isso o banco vem de fora.
    """
    enabled, max_per_day = _channel_settings(ch_cfg.get(InstagramStoryLinkChannel.name))
    if not enabled:
        return
    if not api_privada:
        _aviso(avisos, AVISO_STORY_LINK_FORA_DO_RUN)
        return
    # A regra de ouro, em código: o config inteiro, não o recorte deste comando
    # — o `instagram_story` sai pelo `afiliado run`, e é a CONTA que não pode
    # receber os dois no mesmo dia.
    oficial, _ = _channel_settings(
        (cfg.get("channels") or {}).get(InstagramStoryChannel.name))
    if oficial:
        _aviso(avisos, AVISO_REGRA_DE_OURO)
        return
    usuario, senha, sessionid = _env("IG_USERNAME"), _senha(), _env("IG_SESSIONID")
    if not (usuario and (senha or sessionid)):
        # Credencial NUNCA entra em aviso, log ou resumo — só a ausência dela.
        _aviso(avisos, STORY_LINK_SEM_ENV)
        return
    bruto = story_link_cfg(cfg)
    sessao = ig_session_path(cfg)
    ch = InstagramStoryLinkChannel(
        usuario, senha, session_path=sessao, brand_handle=brand_handle,
        brand_name=brand_name, estado=db, sessionid=sessionid,
        max_sem_link=int(bruto.get("max_sem_link")
                         or instagram_story_link.MAX_SEM_LINK_PADRAO))
    if max_per_day is not None:
        ch.max_per_day = int(max_per_day)
    channels.append(ch)
    # O canal pode nascer FECHADO (desarmado hoje, C2). O aviso está nele; sai
    # daqui para o resumo de operações, como qualquer aviso de montagem —
    # senão o dia ficaria mudo sem que ninguém soubesse por quê.
    for aviso in ch.warnings:
        _aviso(avisos, aviso)
    ch.warnings.clear()
    if not sessao.is_file():
        # Sem sessão o canal faz login com senha no meio do run. Funciona — e é
        # exatamente o comportamento que atrai desafio se virar rotina.
        _aviso(avisos, STORY_LINK_SEM_SESSAO.format(caminho=sessao))


def _monta_instagram(cls, ch_cfg: dict, cfg: dict, channels: list, avisos: list[str],
                     brand_handle: str | None, brand_name: str) -> None:
    """Monta um canal do Instagram (`instagram_feed` ou `instagram_story`).

    Fase 5E: os dois pedem exatamente as mesmas envs (IG_USER_ID,
    IG_ACCESS_TOKEN e o par do Telegram que hospeda a arte), o mesmo construtor
    e o mesmo aviso diário de `ART_HOST_BOT_TOKEN` — o que muda é a classe e a
    chave em `channels:`. Canal ligado sem env: aviso e segue sem ele."""
    enabled, max_per_day = _channel_settings(ch_cfg.get(cls.name))
    if not enabled:
        return
    ig_user = _env("IG_USER_ID")
    ig_token = _env("IG_ACCESS_TOKEN")
    bot_token = _env("TELEGRAM_BOT_TOKEN")
    ops = _env("TELEGRAM_OPS_CHAT_ID")
    art_host = _env("ART_HOST_BOT_TOKEN")
    if not (ig_user and ig_token and bot_token and ops):
        _aviso(avisos, f"⚠️ canal {cls.name} ignorado: variável IG_USER_ID/IG_ACCESS_TOKEN "
                       "(ou TELEGRAM_BOT_TOKEN/TELEGRAM_OPS_CHAT_ID p/ hospedagem) ausente")
        return
    ch = cls(ig_user, ig_token, bot_token, ops, brand_handle=brand_handle,
             brand_name=brand_name, api=_instagram_api(cfg), art_host_bot_token=art_host)
    if max_per_day is not None:
        ch.max_per_day = int(max_per_day)
    channels.append(ch)
    if not art_host:
        # A5: a URL de hospedagem da arte carrega o token do bot que a
        # enviou, e é ela que vai à Meta. Sem um bot secundário, quem
        # viaja é o token do ADMINISTRADOR do canal público.
        _aviso(avisos, ART_HOST_AVISO_TMPL.format(canal=cls.name))


def _build_channels(cfg: dict, somente: tuple[str, ...] | None = None,
                    api_privada: bool = False,
                    db: StateDB | None = None) -> tuple[list, list[str]]:
    """Monta os canais habilitados em config.yaml a partir das envs
    disponíveis e devolve também os avisos de montagem.

    Seção `channels` ausente equivale a `{"telegram": True}` (comportamento da
    fase 1). Cada entrada aceita bool ou dict (`enabled`, `max_per_day` —
    fase 1.7); quando `max_per_day` está presente, vira atributo de instância
    no canal construído (`ch.max_per_day`), lido pelo pipeline via getattr.
    Canal ligado sem env necessária: aviso (stdout + resumo de ops) e segue
    sem ele — nunca derruba o run. Nenhum canal recebe a régua: o veredito
    (modo + selo) já vem decidido no `Post` (fase 5B).

    Fase 5F: `somente` recorta quais chaves de `channels:` existem para esta
    montagem (é o que faz `afiliado stories` levar só os canais de story), e
    `api_privada` autoriza o `instagram_story_link` — que fora do `afiliado
    stories` não é montado, nem no Actions nem na VPS."""
    ch_cfg = cfg.get("channels") or {"telegram": True}
    avisos_do_recorte: list[str] = []
    if somente is not None:
        # I1: o canal oficial de story é o único do recorte que o dono pode
        # esperar ver aqui — e ele não vem. Dizer isso é diferente de omitir.
        if _channel_settings(ch_cfg.get(InstagramStoryChannel.name))[0]:
            avisos_do_recorte.append(AVISO_STORY_OFICIAL_FORA_DO_STORIES)
        ch_cfg = {k: v for k, v in ch_cfg.items() if k in somente}
    brand_cfg = cfg.get("brand") or {}
    brand_handle = brand_cfg.get("handle") or None
    brand_name = brand_cfg.get("name") or "Fiscal da Promo"
    channels: list = []
    avisos: list[str] = []
    for aviso in avisos_do_recorte:
        _aviso(avisos, aviso)

    enabled, max_per_day = _channel_settings(ch_cfg.get("telegram"))
    if enabled:
        token = _env("TELEGRAM_BOT_TOKEN")
        chat_id = _env("TELEGRAM_CHANNEL_ID")
        if token and chat_id:
            ch = TelegramChannel(token, chat_id)
            if max_per_day is not None:
                ch.max_per_day = int(max_per_day)
            channels.append(ch)
        else:
            _aviso(avisos, "⚠️ canal telegram ignorado: variável "
                           "TELEGRAM_BOT_TOKEN/TELEGRAM_CHANNEL_ID ausente")

    enabled, max_per_day = _channel_settings(ch_cfg.get("story_dispatch"))
    if enabled:
        token = _env("TELEGRAM_BOT_TOKEN")
        ops = _env("TELEGRAM_OPS_CHAT_ID")
        if token and ops:
            ch = StoryDispatchChannel(token, ops, brand_handle=brand_handle, brand_name=brand_name)
            if max_per_day is not None:
                ch.max_per_day = int(max_per_day)
            channels.append(ch)
        else:
            _aviso(avisos, "⚠️ canal story_dispatch ignorado: variável "
                           "TELEGRAM_BOT_TOKEN/TELEGRAM_OPS_CHAT_ID ausente")

    # Fase 5E: o story virou publicação de verdade e entra aqui do lado do
    # feed. Ele NÃO declara `manual` — quem continua na trilha de despacho
    # (`summary.dispatched`, `posted.manual`) é só o `story_dispatch`, agora
    # desligado no config.yaml como fallback manual.
    for cls in (InstagramFeedChannel, InstagramStoryChannel):
        _monta_instagram(cls, ch_cfg, cfg, channels, avisos,
                         brand_handle=brand_handle, brand_name=brand_name)

    # Fase 5F: o story COM figurinha de link, pela API privada. Último de
    # propósito — é o canal de maior risco e o único que não roda em toda parte.
    _monta_story_link(ch_cfg, cfg, channels, avisos, brand_handle=brand_handle,
                      brand_name=brand_name, api_privada=api_privada, db=db)

    return channels, avisos


def _doctor_links_do_meli(meli: MeliSource, offers: list, cfg: dict) -> bool:
    """Checa `data/meli_links.json` no doctor (fase 5C, M5/A6): existe? quantos
    produtos do pool têm link? Devolve False (❌) só quando a fonte está
    LIGADA e a cobertura é ZERO — nesse estado o ML não publica nada, e o
    doctor precisa dizer isso e o que fazer.

    Com o pool de OFERTAS vazio o veredito é o mesmo (o ML não publica), mas a
    causa é outra: "0 de 0 produto(s) do pool com link" era verdade e não dizia
    nada — mandava rodar /meli-links-refresh quando o que falta são PRODUTOS
    (menor da revisão da 5C)."""
    caminho = meli.links_path
    ligado = bool((cfg.get("sources") or {}).get("meli", False))
    com_link, total = meli.link_coverage(offers)
    acao = "/meli-links-refresh"
    if not total:
        pool = (cfg.get("meli") or {}).get("offers_path") or DEFAULT_OFFERS_PATH
        situacao = (f"pool de OFERTAS vazio ou inválido ({pool}) — "
                    "sem produto não há link a gerar")
        acao = "/meli-pool-refresh (o pool de links vem depois)"
    elif not meli.links_file_exists:
        situacao = f"pool de links ausente ({caminho})"
    else:
        situacao = (f"{com_link} de {total} produto(s) do pool com anúncio linkado "
                    f"({caminho})")
    if ligado and com_link == 0:
        print(f"❌ Mercado Livre: {situacao} — o ML não vai publicar nada. "
              f"Rode {acao} (ver docs/runbooks/meli-setup.md)")
        return False
    if com_link < total or not total:
        print(f"⚠️ Mercado Livre: {situacao} — rode {acao}")
    else:
        print(f"✅ Mercado Livre: {situacao}")
    return True


# A leitura da resposta de `content_publishing_limit` mudou de casa na fase 5T:
# ela ganhou um SEGUNDO leitor (o canal `instagram_reel`, que recusa publicar
# com a cota estourada) e passou a morar junto de quem faz a chamada. O doctor
# continua chamando pelo nome de sempre; o comportamento é o mesmo, incluindo
# o "(None, None, 24)" para forma que a Meta nunca devolveu.
_cota_de_publicacao = cota_de_publicacao


def _no_windows() -> bool:
    """Função (e não `sys.platform` solto) para o teste do item do agendador
    valer em qualquer plataforma: a suíte roda no Linux do CI e na máquina do
    dono, e o veredito não pode depender de onde o pytest foi chamado."""
    return sys.platform.startswith("win")


def estado_da_tarefa(nome: str) -> str:
    """O `State` da tarefa agendada do Windows — `Ready`, `Disabled`,
    `Running`, `Queued` — ou string vazia quando ela não existe.

    `Get-ScheduledTask` e não `schtasks /query`: o `State` do módulo
    ScheduledTasks é um enum em INGLÊS em qualquer idioma do Windows, enquanto
    o "Status:" do schtasks é traduzido (nesta máquina ele imprime "Pronto" e
    "Desabilitado") — a checagem quebraria calada num Windows em outro idioma.
    """
    r = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command",
         f"(Get-ScheduledTask -TaskName '{nome}' -ErrorAction SilentlyContinue).State"],
        capture_output=True, text=True, timeout=60)
    return (r.stdout or "").strip()


def _doctor_agendador(consulta=None) -> bool:
    """Fase 5I (T4): as duas tarefas da produção existem e estão habilitadas?

    Este é o item que faltava. Na 5G o agendador do GitHub descartou ~15 de 16
    disparos por mais de um dia e NADA no projeto sabia dizer "ninguém está me
    chamando" — a produção mudou de host, e a pergunta continua valendo.

    Fora do Windows o item é PULADO sem falhar (a suíte roda no Linux do CI, e
    a VPS continua sendo uma opção). Consulta que estoura vira ⚠️, nunca ❌:
    um erro do PowerShell não é prova de tarefa ausente, e mandar o dono
    recriar tarefas que existem é pior do que calar.
    """
    if not _no_windows():
        print(f"ℹ️ Agendador: item pulado (não é Windows) — a produção roda no "
              f"Agendador de Tarefas da máquina do dono (ver {RUNBOOK_DA_PRODUCAO})")
        return True
    consulta = consulta or estado_da_tarefa
    ok = True
    for nome in TAREFAS_DA_PRODUCAO:
        try:
            estado = (consulta(nome) or "").strip()
        except Exception as exc:                  # noqa: BLE001 - vira aviso, não veredito
            print(f"⚠️ Agendador: não consegui consultar {nome} ({exc})")
            continue
        if not estado:
            ok = False
            print(f"❌ Agendador: a tarefa {nome} não existe — NINGUÉM está chamando o "
                  f"pipeline. Rode `powershell -ExecutionPolicy Bypass -File "
                  f"{SCRIPT_DO_AGENDADOR}` (ver {RUNBOOK_DA_PRODUCAO})")
        elif estado.lower() == "disabled":
            ok = False
            print(f"❌ Agendador: a tarefa {nome} existe mas está DESABILITADA — ela "
                  f"aparece na lista e não roda. Habilite no Agendador de Tarefas ou "
                  f"rode `powershell -ExecutionPolicy Bypass -File {SCRIPT_DO_AGENDADOR}` "
                  f"(ver {RUNBOOK_DA_PRODUCAO})")
        else:
            print(f"✅ Agendador: {nome} ({estado})")
    return ok


def doctor(cfg: dict) -> int:
    ok = True
    try:
        # Uma chamada de BUSCA (a p1 de uma raiz) mais a fatia do data feed
        # como o config a define: o doctor confere credencial e parsing, não
        # faz varredura — e sem StateDB não mexe no cursor do run.
        fonte = _shopee()
        offers = fonte.fetch_offers(
            {**cfg, "shopee": {**cfg["shopee"], "calls_per_run": 1, "pages": 1}})
        print(f"✅ Shopee: {len(offers)} ofertas; primeira: "
              f"{offers[0] if offers else '(vazio — confira sort_types/list_type)'}")
        # Fase 5L: o feed é a SEGUNDA superfície de descoberta e, sem esta
        # linha, só falaria pelo resumo do run — uma vez por dia. Feed quebrado
        # não pinta o doctor de vermelho: a busca continua publicando, e um ❌
        # num sistema que está entregando é a maneira mais rápida de ensinar o
        # dono a ignorar o ❌.
        stats = getattr(fonte, "discovery_stats", None)
        if getattr(stats, "feed", ""):
            print(f"📦 Data feed: {stats.feed}")
        if getattr(stats, "feed_warning", ""):
            print(stats.feed_warning)
    except Exception as exc:
        ok = False
        print(f"❌ Shopee: {exc}")

    meli = _meli(cfg)  # reaproveita o helper de _build_sources: mesma leitura
                    # de env (_env, já com .strip()) e a mesma construção; sem
                    # credenciais, avisa e não falha o doctor.
    if meli is None:
        print(MELI_ENV_AVISO)
    else:
        try:
            meli.ensure_token()  # só valida as credenciais OAuth
            # Leitura local do pool, sem rede — a MESMA validação do run
            # (C7d): quantas entradas valem e, por motivo, quantas caíram.
            offers = meli.fetch_offers(cfg)
            pool = f"{len(offers)} oferta(s) válida(s) no pool"
            if meli.pool_warning:
                print(f"⚠️ Mercado Livre: token ok; {pool}; {meli.pool_warning}")
            else:
                print(f"✅ Mercado Livre: token ok; {pool}")
            # Fase 5J (J4): a proporção que a fase existe para mudar. As
            # entradas sem régua publicam em modo B e ganham uma sozinhas
            # depois de `ref_min_observations` dias do nosso price_log; sem
            # este número, "o ML só publica modo B" vira descoberta de semanas
            # depois.
            com_regua, total_pool = meli.ruler_coverage(offers)
            if total_pool:
                print(f"🏷️ Mercado Livre: {com_regua} de {total_pool} entrada(s) "
                      f"com régua curada; {total_pool - com_regua} em modo B "
                      "esperando histórico")
        except Exception as exc:
            ok = False
            print(f"❌ Mercado Livre: {exc}")
        else:
            # A6: o pool de LINKS é o que decide se a fonte publica alguma
            # coisa — sem ele todo item do ML vira descarte, e o doctor dizia
            # ✅ assim mesmo.
            if not _doctor_links_do_meli(meli, offers, cfg):
                ok = False

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    ops = os.environ.get("TELEGRAM_OPS_CHAT_ID", "")
    if token and ops:
        # `send_text` devolve False (e imprime o `description` da API) quando
        # o bot foi removido/chat id errado — antes o doctor dizia ✅ mesmo assim.
        if send_text(token, ops, "🩺 doctor: bot funcionando"):
            print("✅ Telegram: mensagem de teste enviada ao chat de operações")
        else:
            ok = False
            print("❌ Telegram: envio ao chat de operações falhou (token/chat id?)")
    else:
        ok = False
        print("❌ Telegram: TELEGRAM_BOT_TOKEN/TELEGRAM_OPS_CHAT_ID ausentes")
    resp = llm.ask_json('Responda APENAS com JSON: {"ok": true}',
                        model=cfg["llm"]["model"])
    if resp == {"ok": True}:
        print("✅ Claude CLI: respondendo")
    else:
        ok = False
        print("❌ Claude CLI: sem resposta (claude instalado? autenticado?)")

    ig_user = os.environ.get("IG_USER_ID", "")
    ig_token = os.environ.get("IG_ACCESS_TOKEN", "")
    if ig_user and ig_token:
        # `content_publishing_limit` no lugar de `?fields=username` (revisão da
        # 5E): o username passa só com `instagram_basic` e imprimia ✅ com a
        # permissão de PUBLICAR perdida — e essa perda mata os DOIS canais do
        # Instagram de uma vez, agora que o story também sai pela API. Esta
        # rota exige a permissão de publicação E devolve, na mesma viagem, a
        # cota COMPARTILHADA por feed e story (100/24 h, medida ao vivo em
        # 2026-08-27). O handle vem do config (é o desenhado nas artes): esta
        # rota não devolve um, e uma segunda chamada só para isso gastaria
        # rede para repetir o que já está no config.yaml.
        try:
            r = httpx.get(
                f"{GRAPH_HOSTS[_instagram_api(cfg)]}/{ig_user}/content_publishing_limit",
                params={"fields": "config,quota_usage", "access_token": ig_token}, timeout=20)
            data = r.json()
        except Exception as exc:
            ok = False
            print(f"❌ Instagram: {exc}")
        else:
            if r.status_code == 200 and not (isinstance(data, dict) and data.get("error")):
                # Quem prova a permissão é o 200; a cota é o extra. Forma
                # estranha não vira ❌ — isso faria o dono desligar um canal
                # que está funcionando.
                usadas, total, horas = _cota_de_publicacao(data)
                cota = (f"{usadas} de {total} na cota de {horas} h"
                        if usadas is not None and total is not None
                        else f"cota não informada pela Meta ({data})")
                handle = (cfg.get("brand") or {}).get("handle") or f"id {ig_user}"
                print(f"✅ Instagram: {handle} · publicação liberada · {cota}")
            else:
                ok = False
                print(f"❌ Instagram: publicação bloqueada — {graph_error(data)}. "
                      "Os DOIS canais (instagram_feed e instagram_story) param. "
                      "Fallback: ligue channels.story_dispatch no config.yaml e poste "
                      "os stories à mão (ver docs/runbooks/meta-setup.md)")
    else:
        print("ℹ️ Instagram: não configurado (ver docs/runbooks/meta-setup.md)")

    if not _doctor_story_link(cfg):
        ok = False

    if not _doctor_preco_real(cfg):
        ok = False

    _doctor_preco_checkout(cfg, _watchlist(cfg))

    # Por último de propósito: é o item que responde "quem me chama?", e ele
    # fala do MUNDO (o agendador), não das credenciais.
    if not _doctor_agendador():
        ok = False

    return 0 if ok else 1


def _tem_playwright() -> bool:
    """O extra `preco` está instalado? Só a PRESENÇA — nada é aberto aqui."""
    import importlib.util
    return importlib.util.find_spec("playwright") is not None


def _doctor_preco_real(cfg: dict) -> bool:
    """Fase 5P: a leitura do preço de checkout, conferida SEM abrir navegador.

    DESLIGADA ele cala. Ela é o estado normal, e um item por diagnóstico sobre
    algo que não roda é ruído — que é como se ensina o dono a ignorar o doctor.

    Ligada, ele confere as duas coisas que só se descobre tarde demais: o perfil
    (apontar para o Chrome do dono é o erro que custa a conta) e a presença do
    extra (sem ele toda leitura se desarma no primeiro item, e o dia inteiro
    publica o preço da API achando que está lendo).
    """
    opcoes = preco_real.config_de(cfg)
    if not opcoes["enabled"]:
        return True
    motivo = preco_real.perfil_proibido(opcoes["profile_dir"])
    if motivo:
        print(f"❌ preco_real: {motivo}")
        return False
    if not _tem_playwright():
        print("❌ preco_real: ligado, mas o extra não está instalado — rode "
              "`pip install -e .[preco]`. Sem ele TODA leitura se desarma no "
              "primeiro item e o dia publica o preço da API "
              "(ver docs/runbooks/shopee-preco.md)")
        return False
    print(f"✅ preco_real: ligado · perfil próprio em {opcoes['profile_dir']} "
          f"(channel={opcoes['browser_channel'] or 'chromium empacotado'}, "
          f"teto={opcoes['timeout_s']:.0f}s, desarma em {opcoes['max_falhas']} "
          "falhas seguidas)")
    return True


def _doctor_preco_checkout(cfg: dict, watchlist) -> bool:
    """Fase 5R: o preço de checkout que vem do cubo, conferido SEM rede.

    Desligado ele cala, como o item da 5P. Ligado, ele responde a única
    pergunta que só se descobre tarde demais: **quantos preços ainda estão
    dentro do teto de idade hoje**. A seção envelhece sozinha — cada preço tem
    a data da RASPAGEM, não a do arquivo — e uma coleta que parou faria as
    peças voltarem ao preço de catálogo sem nada dizer.

    **Ele NUNCA pinta o doctor de vermelho**, e devolve sempre True. É o mesmo
    critério do data feed da 5L: seção vazia ou vencida não quebra nada — o
    post publica o preço da API, exatamente como publicava antes desta fase —,
    e um ❌ num sistema que está entregando é a maneira mais rápida de ensinar
    o dono a ignorar o ❌. Quem cobra a coleta é o aviso diário do run, que vai
    ao chat de operações.
    """
    opcoes = shopee_checkout.config_de(cfg)
    if not opcoes["enabled"]:
        return True
    regua = (f"teto {opcoes['gap_max']:.0%} · piso {opcoes['gap_min']:.0%} · "
             f"idade máx. {opcoes['idade_max_dias']} dia(s)")
    if watchlist is None:
        print("⚠️ preco_checkout: ligado, mas não há watchlist legível — o preço de "
              "checkout mora em `checkout_prices` dela; os posts publicam o preço "
              "da API (ver docs/runbooks/shopee-preco.md)")
        return True
    entradas = watchlist.checkout_prices
    if not entradas:
        print("⚠️ preco_checkout: ligado e a seção `checkout_prices` está vazia — "
              "rode /shopee-checkout-refresh (os posts publicam o preço da API)")
        return True
    dentro = len(shopee_checkout.frescos(entradas, date.today(),
                                         opcoes["idade_max_dias"]))
    marca = "✅" if dentro else "⚠️"
    print(f"{marca} preco_checkout: ligado · {dentro} de {len(entradas)} preços "
          f"dentro do teto de idade · {regua} · seção de "
          f"{watchlist.section_date(shopee_checkout.SECAO).isoformat()}"
          + ("" if dentro else " — rode /shopee-checkout-refresh"))
    return True


def _doctor_story_link(cfg: dict) -> bool:
    """Fase 5F: o canal do instagrapi, conferido SEM fazer login.

    O doctor roda a cada diagnóstico; autenticar aqui gastaria uma autenticação
    por execução, e é justamente a sequência de logins que atrai
    `challenge_required`. Então ele confere só o que dá para ver do lado de
    fora: os dois canais de story ligados juntos, a PRESENÇA das credenciais
    (nunca o valor) e a sessão no disco.
    """
    ch_cfg = cfg.get("channels") or {}
    ligado, _ = _channel_settings(ch_cfg.get(InstagramStoryLinkChannel.name))
    oficial, _ = _channel_settings(ch_cfg.get(InstagramStoryChannel.name))
    ok = True

    if ligado and oficial:
        ok = False
        print("❌ instagram_story_link e instagram_story ligados ao mesmo tempo: publicar "
              "pela API privada e pela oficial na MESMA CONTA, no mesmo dia, é o padrão "
              "que chama atenção. Deixe só um ligado no config.yaml "
              "(ver docs/runbooks/instagrapi-stories.md)")
    if not ligado:
        return ok

    # A senha é lida sem `.strip()` (ver `_senha`): uma senha de um espaço só é
    # uma senha, e o doctor não pode dizer que ela falta.
    presentes = {"IG_USERNAME": _env("IG_USERNAME"), "IG_PASSWORD": _senha()}
    faltando = [nome for nome, valor in presentes.items() if not valor]
    if faltando:
        ok = False
        print(f"❌ instagram_story_link: {'/'.join(faltando)} ausente(s) no ambiente — "
              "o canal não vai publicar nada (ver docs/runbooks/instagrapi-stories.md)")
    else:
        # Presença, nunca valor: esta é a senha da conta.
        print("✅ instagram_story_link: IG_USERNAME/IG_PASSWORD presentes "
              "(o doctor não faz login de propósito)")

    sessao = ig_session_path(cfg)
    if sessao.is_file():
        idade = int((time.time() - sessao.stat().st_mtime) // 86400)
        # O que o mtime mede é a ÚLTIMA GRAVAÇÃO — e `_guarda_sessao` reescreve
        # o arquivo a cada login bem-sucedido, então "sessão de N dias" era
        # sempre ~0 e não dizia nada sobre a idade do device. O texto agora diz
        # o que o número é. (Sessão velha é boa notícia, não alerta: device
        # estável e poucos logins é o que evita desafio.)
        print(f"✅ instagram_story_link: última sessão gravada há {idade} dia(s) "
              f"em {sessao}")
    else:
        print(f"⚠️ instagram_story_link: sem sessão em {sessao} — rode `afiliado ig-login` "
              "(senão o primeiro story do run faz login com senha, e login novo é o que "
              "atrai desafio)")

    # O desarme dura o DIA e vive no banco do `afiliado stories` (fase 5F, C2).
    # Sem isto, um dia inteiro sem story parecia "não havia oferta boa" — e o
    # único lugar que dizia a verdade era o resumo de operações daquele run,
    # que a essa altura já rolou para cima no chat.
    try:
        db = _abre_estado(cfg, stories=True)
    except Exception as exc:                      # banco ausente/ilegível não derruba o doctor
        print(f"⚠️ instagram_story_link: não consegui ler o estado do canal ({exc})")
        return ok
    try:
        aviso = db.day_flag(instagram_story_link.CHAVE_DESARMADO)
        if aviso:
            ok = False
            print(f"❌ instagram_story_link: DESARMADO hoje — {aviso}")
            print("   Rearma sozinho amanhã (dia local). Para rearmar agora: "
                  "`afiliado ig-login` (se foi sessão) ou conserte a figurinha e "
                  "espere uma verificação boa.")
        else:
            print("✅ instagram_story_link: armado hoje")
    finally:
        db.close()
    return ok


def ig_login(cfg: dict) -> int:
    """Cria ou renova `data/ig_session.json`, a sessão do instagrapi.

    As credenciais vêm do AMBIENTE (`.env`), nunca de argumento de linha de
    comando: argumento fica no histórico do shell e no `ps` de qualquer usuário
    da máquina. 2FA só por TOTP (`IG_TOTP_SEED`) — o instagrapi não faz SMS.

    Imprime sucesso/falha e o caminho da sessão. Nada mais: nem usuário, nem
    senha, nem o texto cru de uma exceção que possa carregá-la.
    """
    usuario, senha, semente = _env("IG_USERNAME"), _senha(), _env("IG_TOTP_SEED")
    # `IG_SESSIONID` (cookie de um navegador logado) tem PRECEDÊNCIA sobre a
    # senha. Conta business vinculada a Página pode não ter senha própria de
    # Instagram: medido em 2026-08-27, `login()` devolve "You can log in with
    # your linked Facebook account" e a senha correta é rejeitada.
    sessionid = _env("IG_SESSIONID")
    caminho = ig_session_path(cfg)
    if not (usuario and (senha or sessionid)):
        print("❌ ig-login: IG_USERNAME e (IG_PASSWORD ou IG_SESSIONID) ausentes no "
              "ambiente — preencha o .env (ver docs/runbooks/instagrapi-stories.md)")
        return 1
    try:
        cl = instagram_story_link.nova_sessao()
    except ImportError:
        print(f"❌ ig-login: {instagram_story_link.SEM_INSTAGRAPI}")
        return 1
    try:
        instagram_story_link.entra(cl, usuario, senha, caminho, totp_seed=semente,
                                   sessionid=sessionid)
        instagram_story_link.guarda_sessao(cl, caminho)
    except Exception as exc:      # noqa: BLE001 - vira mensagem, nunca traceback
        # O texto da exceção é de terceiro: raspado antes de ir ao terminal.
        detalhe = instagram_story_link.sem_segredos(str(exc), senha, semente, sessionid)
        print(f"❌ ig-login: falhou ({type(exc).__name__}: {detalhe})")
        if "linked Facebook account" in detalhe:
            print("   Esta conta é vinculada ao Facebook e pode não ter senha própria "
                  "de Instagram. Defina IG_SESSIONID com o cookie `sessionid` de um "
                  "navegador logado em instagram.com (ver o runbook).")
        if not semente:
            print("   Se a conta tem 2FA, defina IG_TOTP_SEED (app autenticador; "
                  "o instagrapi não faz SMS).")
        return 1
    _rearma_o_canal(cfg)
    print(f"✅ ig-login: sessão salva em {caminho}")
    return 0


def _rearma_o_canal(cfg: dict) -> None:
    """Um `ig-login` bem-sucedido apaga o desarme do dia (C2).

    Sem isto o dono re-logaria — o gesto que o próprio aviso pede — e o canal
    continuaria mudo até a virada do dia local, sem dizer por quê. Só aqui,
    porque só aqui houve prova de que a sessão voltou.
    """
    try:
        db = _abre_estado(cfg, stories=True)
    except Exception:      # noqa: BLE001 - o login funcionou; isto é acessório
        return
    try:
        db.set_day_flag(instagram_story_link.CHAVE_DESARMADO, "")
        db.set_day_flag(instagram_story_link.CHAVE_SEM_LINK, "")
    finally:
        db.close()


# =============================================================================
# Fase 5D — comando `afiliado feed`
#
# Duas peças, dois destinos. O TERMÔMETRO monta o carrossel do dia e publica —
# é o motor de retenção. O FLAGRANTE gera o gráfico do "de" que não se sustenta
# e NÃO publica: vai ao chat de operações esperar o "ok" do dono, porque
# nomear um vendedor específico com base em dado automatizado é risco jurídico
# e reputacional, e isso não se automatiza (`docs/feed.md`).
#
# Os dois respeitam o teto diário e o ritmo da 5A, como os outros canais.
# =============================================================================


def _cliente_http() -> httpx.Client:
    """O cliente que baixa as fotos dos produtos para as artes do feed.

    É uma função (e não um `httpx.Client()` embutido) para o teste injetar um
    `MockTransport`: nenhum teste da suíte toca a rede."""
    return httpx.Client(timeout=creative.DOWNLOAD_TIMEOUT)


def _marca(cfg: dict) -> tuple[str | None, str]:
    brand = cfg.get("brand") or {}
    return brand.get("handle") or None, brand.get("name") or "Fiscal da Promo"


def _grava_previews(prefixo: str, imagens: list[bytes]) -> list[Path]:
    PREVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    caminhos = []
    for i, png in enumerate(imagens, start=1):
        nome = f"{prefixo}.png" if len(imagens) == 1 else f"{prefixo}-{i:02d}.png"
        caminho = PREVIEWS_DIR / nome
        caminho.write_bytes(png)
        caminhos.append(caminho)
    return caminhos


def _watchlist(cfg: dict):
    try:
        return load_watchlist((cfg.get("watchlist") or {}).get("path", "data/watchlist.json"))
    except Exception:      # noqa: BLE001 - sem watchlist a peça sai sem boosts
        return None


def _ofertas_do_feed(cfg: dict, db: StateDB, avisos: list[str],
                     dry_run: bool = False) -> tuple[list, dict]:
    """As ofertas candidatas à peça de feed, com a régua já carimbada.

    Estoque de candidatas (o que os runs do pipeline acumularam) UNIÃO a fatia
    que as fontes devolvem agora — a mesma união do `pipeline`, sem o laço de
    publicação. A fatia fresca sobrescreve o estoque: preço mais novo vence.
    Fonte que falha vira aviso e não derruba o comando.

    A fatia fresca também ENTRA no estoque (rodada de fechamento, F2). Este
    comando paga a mesma descoberta que o `run` paga — 8 chamadas à Shopee por
    execução — e antes jogava fora o que ela achou: o que não coubesse no
    carrossel de hoje sumia com o processo. A descoberta é do projeto, não do
    comando. Em `--dry-run` nada é gravado (A10), e fonte sem
    `candidate_max_age_days` não usa o estoque (o pool do ML é relido inteiro a
    cada run) — gravá-la só engordaria o `state.db` que o Actions commita."""
    sources, avisos_fontes = _build_sources(cfg, db)
    avisos.extend(avisos_fontes)
    por_chave: dict[tuple[str, str], object] = {}
    for src in sources:
        idade = pipeline.candidate_max_age_days(cfg, src.name)
        if idade > 0:
            for o in db.load_candidates(src.name, idade):
                por_chave[(o.source, o.item_id)] = o
    for src in sources:
        try:
            lote = list(src.fetch_offers(cfg))
        except Exception as exc:     # noqa: BLE001 - fonte isolada, como no pipeline
            _aviso(avisos, f"⚠️ fonte {src.name} falhou: {exc}")
            continue
        for o in lote:
            por_chave[(o.source, o.item_id)] = o
        idade = pipeline.candidate_max_age_days(cfg, src.name)
        if lote and idade > 0 and not dry_run:
            db.upsert_candidates(lote)
            db.prune_candidates(idade, source=src.name)
    ofertas = pricing.enrich_offers(list(por_chave.values()), db, _watchlist(cfg), cfg)
    return ofertas, {s.name: s for s in sources}


def _minimo_de_desconto(cfg: dict) -> int:
    return int(pricing.setting(cfg.get("selection") or {}, "min_real_discount_pct",
                               pricing.DEFAULT_MIN_REAL_DISCOUNT_PCT))


def _teto_do_carrossel(cfg: dict) -> tuple[bool, int]:
    """`(ligado, teto diário)` de `channels.instagram_carrossel`. Seção ausente
    = ligado com `DEFAULT_CARROSSEL_MAX_PER_DAY` (um por dia)."""
    raw = (cfg.get("channels") or {}).get(CANAL_CARROSSEL)
    if raw is None:
        return True, DEFAULT_CARROSSEL_MAX_PER_DAY
    ligado, max_per_day = _channel_settings(raw)
    return ligado, int(max_per_day or DEFAULT_CARROSSEL_MAX_PER_DAY)


def _carrossel_pode_sair(cfg: dict, db: StateDB) -> tuple[bool, str]:
    """O ritmo da 5A aplicado ao carrossel: o teto diário distribuído pela
    janela de `schedule:`. Fora da janela o orçamento é 0 e a peça não sai."""
    ligado, teto = _teto_do_carrossel(cfg)
    if not ligado:
        return False, f"canal {CANAL_CARROSSEL} desligado em config.yaml"
    horario = pipeline.schedule_settings(cfg)
    orcamento = pipeline.pacing_budget(teto, db.local_now(),
                                       horario["window_start"], horario["window_end"])
    usados = db.count_posts_today(CANAL_CARROSSEL)
    if usados >= orcamento:
        return False, (f"teto/ritmo do carrossel: {usados} publicado(s) hoje, "
                       f"orçamento agora {orcamento} (teto do dia {teto})")
    return True, ""


def _canal_do_carrossel(cfg: dict, avisos: list[str]) -> InstagramFeedChannel | None:
    """O canal que publica o álbum — a MESMA classe do feed (mesmo endpoint,
    mesma cota da Meta). O teto é do `instagram_carrossel`, mas as credenciais
    e o aviso de `ART_HOST_BOT_TOKEN` são os do `instagram_feed`."""
    handle, nome = _marca(cfg)
    canais: list = []
    _monta_instagram(InstagramFeedChannel, {InstagramFeedChannel.name: True}, cfg,
                     canais, avisos, brand_handle=handle, brand_name=nome)
    return canais[0] if canais else None


def _posts_do_carrossel(escolhidas: list, by_name: dict, cfg: dict, db: StateDB,
                        dry_run: bool, avisos: list[str]) -> list[Post]:
    """Preço vivo + veredito, uma oferta por slide.

    O preço é atualizado antes de virar arte (o pool do ML chega com a MEDIANA
    como "atual"); oferta cujo refresh falha fica FORA do carrossel — publicar
    um preço velho num post que se chama Fiscal é o pior resultado possível.
    A copy não é usada pelo desenho do slide (`render_carrossel` lê só
    `post.offer` e `post.verdict`), então não se gasta LLM aqui."""
    minimo = _minimo_de_desconto(cfg)
    posts: list[Post] = []
    for offer in escolhidas:
        refresh = getattr(by_name.get(offer.source), "refresh_price", None)
        if refresh is not None:
            try:
                offer = refresh(offer)
            except Exception as exc:      # noqa: BLE001 - a oferta sai, o post continua
                _aviso(avisos, f"⚠️ {offer.item_id}: preço não atualizado ({exc}) "
                               "— fora do carrossel")
                continue
            if not dry_run:
                db.record_price(offer.source, offer.item_id, offer.price_current_cents)
        posts.append(Post(offer=offer, copy=CopyParts("", "", ""), affiliate_link="",
                          verdict=pricing.verdict(offer, minimo)))
    return posts


def capa_do_termometro(posts: list[Post]) -> tuple[str, str]:
    """Título e subtítulo da capa. O número vem da RÉGUA, não do marketing:
    "passou" é a oferta que o veredito autoriza a alegar desconto verificado ou
    que carrega o selo de menor preço."""
    n = len(posts)
    aprovadas = sum(1 for p in posts if p.verdict.mode == "A" or p.verdict.seal)
    if aprovadas == 0:
        titulo = f"NENHUMA DAS {n} PASSOU."
    elif aprovadas == 1 and n > 1:
        # A variante que a pesquisa cita nominalmente ("3 ofertas, 1 é real") —
        # e que resolve o "1 PASSARAM" de graça.
        titulo = f"{n} OFERTAS. 1 É REAL."
    elif aprovadas < n:
        titulo = f"{n} OFERTAS. {aprovadas} PASSARAM."
    else:
        titulo = f"AS {n} DO DIA COM SELO DO FISCAL"
    return titulo, SUBTITULO_TERMOMETRO


def legenda_do_carrossel(posts: list[Post], titulo: str, subtitulo: str) -> str:
    """A legenda do álbum — página de busca, não pedido de curtida.

    Um item por linha com o nome COMPLETO e o preço, as categorias por nome, e
    a janela MAIS CURTA entre as ofertas (é a única que vale para o post
    inteiro: prometer a maior seria alegar sobre um item o que só outro
    sustenta). Fecha na frase-assinatura.

    O preço sai por `pricing.preco_publicado`, que carrega o "sem cupom" dos
    itens da Shopee quando o rótulo está ligado (fase 5K; desligado desde a
    5N): cada slide é a arte de feed, e ela desenha o rótulo na pill — a
    legenda não pode discordar do álbum que acompanha, nos dois estados."""
    linhas = [titulo, subtitulo, ""]
    for i, post in enumerate(posts, start=1):
        offer = post.offer
        nome = sanitiza_titulo(offer.title)
        selo = " · selo do Fiscal" if post.verdict.seal else ""
        linhas.append(f"{i}. {nome} — {pricing.preco_publicado(offer)}{selo}")
    linhas += ["", "🔗 Link na bio e no canal do Telegram", ""]
    nomes = list(dict.fromkeys(
        n for n in (categorias.nome(p.offer.category) for p in posts) if n))
    if nomes:
        linhas.append("Categorias: " + " · ".join(nomes))
    janelas = [p.offer.price_window_days for p in posts if p.offer.price_window_days > 0]
    if janelas:
        linhas.append(f"Preço verificado nos últimos {min(janelas)} dias.")
    linhas.append(creative.ASSINATURA)
    return "\n".join(linhas)


def _notifica_ops(cfg: dict, texto: str) -> None:
    token, ops = _env("TELEGRAM_BOT_TOKEN"), _env("TELEGRAM_OPS_CHAT_ID")
    if token and ops:
        send_text(token, ops, texto)


def _feed_termometro(cfg: dict, args, db: StateDB) -> int:
    avisos: list[str] = []
    canal = None
    if not args.dry_run:
        pode, motivo = _carrossel_pode_sair(cfg, db)
        if not pode:
            print(f"ℹ️ carrossel não sai agora — {motivo}")
            return 0
        canal = _canal_do_carrossel(cfg, avisos)
        if canal is None:
            print("❌ carrossel: canal do Instagram não montado "
                  "(ver docs/runbooks/meta-setup.md)")
            return 1

    ofertas, by_name = _ofertas_do_feed(cfg, db, avisos, args.dry_run)
    candidatas = selection.filter_offers(ofertas, db, cfg)
    if not candidatas:
        print(f"ℹ️ carrossel: {len(ofertas)} oferta(s), nenhuma candidata — nada a montar")
        return 0
    # O ranking que já existe, com a vaga do carrossel no lugar da do post.
    cfg_ranking = {**cfg, "selection": {**cfg["selection"],
                                        "posts_per_run": MAX_OFERTAS_NO_CARROSSEL}}
    escolhidas = selection.rank_offers(candidatas, db.recent_titles(), cfg_ranking,
                                       _watchlist(cfg))[:MAX_OFERTAS_NO_CARROSSEL]
    posts = _posts_do_carrossel(escolhidas, by_name, cfg, db, args.dry_run, avisos)
    if not posts:
        print("ℹ️ carrossel: nenhuma oferta sobreviveu à atualização de preço")
        return 0

    handle, nome_marca = _marca(cfg)
    # As fotos primeiro, a capa e a legenda depois (F4): produto cuja imagem
    # não baixa é pulado, e uma capa que diz "6 OFERTAS" ou uma legenda que
    # lista um item que o álbum não tem seria a peça mentindo sobre si mesma.
    with _cliente_http() as client:
        try:
            fotos = creative.carrossel_fotos(posts, client, avisos)
            posts = [post for post, _ in fotos]
            titulo, subtitulo = capa_do_termometro(posts)
            legenda = legenda_do_carrossel(posts, titulo, subtitulo)
            imagens = creative.render_carrossel(fotos, titulo, subtitulo, handle=handle,
                                                brand_name=nome_marca)
        except SourceError as exc:
            # O passo do Actions é `continue-on-error` e o job segue VERDE: se
            # isto só fosse ao log, o feed podia parar por uma semana sem que
            # ninguém notasse. É o mesmo caminho da falha de publicação.
            print(f"❌ carrossel: falha ao gerar a arte — {exc}")
            if not args.dry_run:
                _notifica_ops(cfg, "\n".join(
                    [f"❌ Carrossel do feed não foi gerado: {exc}", *avisos]))
            return 1

    if args.dry_run:
        caminhos = _grava_previews("feed-carrossel", imagens)
        print(f"--- DRY-RUN: carrossel de {len(posts)} oferta(s), "
              f"{len(imagens)} slides ---")
        for caminho in caminhos:
            print(f"  {caminho}")
        print(f"\n{legenda}\n")
        for aviso in avisos:
            print(aviso)
        return 0

    resultado = canal.publish_carrossel(imagens, legenda)
    # F3: há aviso que só EXISTE depois de publicar — o "polling cego" da 5E,
    # quando a Meta não devolve `status_code` do container. Quem o drena é o
    # laço do `pipeline.run`, que este comando não usa: sem isto ele morria
    # dentro do objeto do canal. Aqui não há `_Warner` (não há run nem
    # deduplicação por dia), então as linhas vão ao resumo que o comando já
    # manda — e vão nos DOIS caminhos: o run que mais precisa de diagnóstico é
    # justamente o que falhou.
    avisos.extend(pipeline.drena_avisos(canal))
    for aviso in avisos:
        print(aviso)
    if not resultado.ok:
        print(f"❌ carrossel: publicação falhou — {resultado.error}")
        _notifica_ops(cfg, "\n".join(
            [f"❌ Carrossel do feed falhou: {resultado.error}", *avisos]))
        return 1

    # UMA linha no canal que conta para o teto (um carrossel é um post) e uma
    # por oferta no canal de item, para o dedupe não repetir os mesmos produtos.
    db.record_post(posts[0], CANAL_CARROSSEL, resultado.message_id)
    for post in posts:
        db.record_post(post, CANAL_CARROSSEL_ITEM, resultado.message_id)
    print(f"✅ carrossel publicado ({resultado.message_id}): {titulo}")
    _notifica_ops(cfg, "\n".join(
        [f"🎠 Carrossel publicado — {titulo}",
         *(f"• {p.offer.title[:40]}" for p in posts), *avisos]))
    return 0


# --- Fase 5P: exercitar a leitura de preço à mão ------------------------------

def _cents_do_texto(texto: str) -> int:
    """"599,00", "599.00", "R$ 599,00" -> 59900. 0 quando não dá para ler."""
    limpo = str(texto or "").replace("R$", "").strip().replace(".", "").replace(",", ".")
    try:
        return int(round(float(limpo) * 100))
    except ValueError:
        return 0


def _alvo_no_estoque(db: StateDB, item_id: str):
    """A candidata da Shopee com este itemId no estoque do `state.db` — é dela
    que saem a URL do anúncio e o preço da API sem chamar API nenhuma."""
    for offer in db.load_candidates("shopee", 30):
        if offer.item_id == item_id:
            return offer
    return None


def preco_real_a_mao(cfg: dict, args) -> int:
    """`afiliado preco-real <url|itemId>`: lê o preço de checkout de UM anúncio
    e imprime o que o post publicaria. Não publica, não escreve no banco.

    É o instrumento da decisão da fase (P1): a leitura nasce DESLIGADA, e este
    comando roda de qualquer jeito — o dono compara a leitura com a tela do
    celular dele e só então liga o interruptor.

    Duas coisas que ele NÃO faz, de propósito:
      - não grava o desarme (`estado=None`): três sondas à mão não podem calar a
        leitura da produção no dia seguinte;
      - não usa o perfil do Chrome do dono. Se `preco_real.profile_dir` apontar
        para lá, ele recusa aqui pelo mesmo caminho que a montagem do run.

    Sai 0 quando leu e 1 quando não leu — para um script saber a diferença.
    """
    opcoes = preco_real.config_de(cfg)
    motivo_do_perfil = preco_real.perfil_proibido(opcoes["profile_dir"])
    if motivo_do_perfil:
        print(f"❌ preco-real: {motivo_do_perfil}")
        return 1

    alvo = str(args.alvo).strip()
    url, preco_cents = alvo, _cents_do_texto(getattr(args, "preco", ""))
    # `"://"` e não `startswith("http")`: um `file://…` (uma página salva, o
    # jeito de exercitar o parser sem tocar a Shopee) é URL e não itemId, e
    # tratá-lo como itemId mandava o comando procurar no estoque.
    if "://" not in alvo:
        db = _abre_estado(cfg)
        try:
            offer = _alvo_no_estoque(db, alvo)
        finally:
            db.close()
        if offer is None:
            print(f"❌ preco-real: item {alvo} não está no estoque de candidatas "
                  "— passe a URL do anúncio e --preco (o preço da API, a âncora "
                  "da leitura)")
            return 1
        url = offer.product_url
        preco_cents = preco_cents or offer.price_current_cents
    if preco_cents <= 0:
        print("❌ preco-real: sem o preço da API não há âncora — "
              "passe --preco 599,00")
        return 1

    print(f"🔎 preco-real — {url}")
    print(f"   preço da API (âncora): {format_brl(preco_cents)}")
    print(f"   navegador: perfil {opcoes['profile_dir']} · "
          f"channel={opcoes['browser_channel'] or 'chromium empacotado'} · "
          f"headless={opcoes['headless']} · teto={opcoes['timeout_s']:.0f}s")
    print("   (perfil PRÓPRIO e deslogado — nunca o Chrome do dono)")

    try:
        navegador = preco_real.navegador_playwright(
            opcoes["profile_dir"], channel=opcoes["browser_channel"],
            headless=opcoes["headless"], teto_s=opcoes["timeout_s"],
            passo_s=opcoes["passo_s"])
    except ImportError:
        print(f"❌ preco-real: {preco_real.SEM_PLAYWRIGHT}")
        return 1

    def pronto(texto: str) -> bool:
        return preco_real.parse_preco(texto, preco_cents).ok

    try:
        texto, segundos = navegador(url, pronto)
    except Exception as exc:      # noqa: BLE001 - é um diagnóstico, não um run
        print(f"❌ preco-real: {preco_real.FALHA_DO_NAVEGADOR} "
              f"({type(exc).__name__}: {exc})")
        return 1

    leitura = preco_real.parse_preco(texto, preco_cents)
    tempo = f"{segundos:.1f}".replace(".", ",")
    if leitura.ok:
        print(f"✅ leitura em {tempo} s: "
              f"{format_brl(leitura.price_cents)} {leitura.condicao}")
        print(f"   o post publicaria: {format_brl(leitura.price_cents)} "
              f"{leitura.condicao}")
        return 0
    print(f"❌ sem leitura ({tempo} s): {leitura.motivo}")
    print(f"   o post publicaria: {format_brl(preco_cents)} "
          "(o preço da API, como hoje — é o que falhar fechado significa)")
    print(f"   primeiros 300 caracteres do que a página devolveu:\n"
          f"   {' | '.join(texto.splitlines())[:300]}")
    return 1


def legenda_do_flagrante(achado, verdict) -> str:
    """O que o dono lê no chat de operações antes de decidir publicar.

    Diz o que o vendedor alega, o que o nosso histórico mostra, e — em
    primeiro lugar — que a peça NÃO foi publicada."""
    offer = achado.offer
    linhas = [
        "🔎 FLAGRANTE PARA APROVAR — NÃO foi publicado",
        "",
        offer.title,
        f"O vendedor anuncia -{achado.desconto_alegado_pct}% sobre "
        f"{format_brl(offer.price_original_cents)}.",
    ]
    if achado.dias_no_pico:
        linhas.append(f"Esse preço existiu por {achado.dias_no_pico} dia(s) em "
                      f"{len(achado.historico)} dias de histórico "
                      f"(pico medido: {format_brl(achado.pico_cents)}).")
    else:
        linhas.append(f"Esse preço NUNCA existiu nos {len(achado.historico)} dias "
                      "de histórico que medimos.")
    linhas += [
        f"Preço de sempre (mediana): {format_brl(offer.price_ref_cents)} · "
        f"hoje: {format_brl(offer.price_current_cents)}",
        f"Gravidade: {achado.gravidade:.2f}",
    ]
    if verdict.seal:
        linhas.append(verdict.seal)
    linhas += [
        "",
        "Publique só se você concordar: nomear um vendedor é risco jurídico e "
        "reputacional, e por isso esta peça nunca sai sozinha.",
        creative.ASSINATURA,
    ]
    return "\n".join(linhas)


def serie_ate_hoje(historico: list, offer, hoje) -> list:
    """A série do gráfico termina no preço de HOJE — o mesmo que a legenda diz.

    O `price_log` pode ainda não ter a observação de hoje (em `--dry-run` nada
    é gravado, e o run que grava roda à parte), e aí o último ponto do gráfico
    era o preço de ONTEM enquanto a legenda dizia o de hoje. Medido no primeiro
    preview do comando: o gráfico marcava R$ 27,60 e o texto, R$ 26,00. Um
    gráfico que contradiz a própria legenda é pior que nenhum gráfico."""
    if offer.price_current_cents <= 0:
        return historico
    return [(d, c) for d, c in historico if d != hoje] + [(hoje, offer.price_current_cents)]


def chave_do_flagrante(offer) -> str:
    """A chave da marca de dedupe em `day_flags`, por PRODUTO."""
    return f"{FLAGRANTE_FLAG_PREFIXO}:{offer.source}:{offer.item_id}"


def _flagrante_pode_sair(db: StateDB) -> tuple[bool, str]:
    """O teto diário do flagrante, conferido ANTES da descoberta — como o
    `_carrossel_pode_sair` faz com o carrossel. Um disparo que já gastou a cota
    do dia sai sem pagar as 8 chamadas de descoberta e sem tocar em rede.

    Vale também em `--dry-run` (o preview mostra a peça que SAIRIA, e a que
    sairia é nenhuma), como já vale o dedupe por produto; o que o dry-run não
    faz é gravar (`somente_leitura`)."""
    marca = db.day_flag(FLAGRANTE_DIA_FLAG)
    return (False, marca) if marca else (True, "")


def _feed_flagrante(cfg: dict, args, db: StateDB) -> int:
    avisos: list[str] = []
    pode, marca = _flagrante_pode_sair(db)
    if not pode:
        print(f"ℹ️ flagrante não sai agora — já houve um hoje ({marca})")
        return 0
    ofertas, _ = _ofertas_do_feed(cfg, db, avisos, args.dry_run)
    achados = flagrante.encontra(ofertas, db, cfg)
    if not achados:
        print(f"ℹ️ nenhum flagrante entre {len(ofertas)} oferta(s) — "
              "nenhum 'de' inflado com histórico que o desminta")
        return 0
    # O dedupe vale também em `--dry-run`, e aqui ele NÃO esconde nada: o
    # preview existe para mostrar a peça que sairia, e a peça que sairia é a do
    # próximo produto ainda não despachado. Nada é gravado (`somente_leitura`),
    # então olhar o preview de manhã não cala a peça da tarde.
    ineditos = [a for a in achados
                if not db.day_flag_recente(chave_do_flagrante(a.offer),
                                           FLAGRANTE_DEDUPE_DAYS)]
    if not ineditos:
        print(f"ℹ️ {len(achados)} flagrante(s), todos já despachados nos últimos "
              f"{FLAGRANTE_DEDUPE_DAYS} dias — nada a mandar")
        return 0
    achado = ineditos[0]     # o de maior gravidade entre os que ainda não saíram
    verdict = pricing.verdict(achado.offer, _minimo_de_desconto(cfg))
    handle, nome_marca = _marca(cfg)
    serie = serie_ate_hoje(achado.historico, achado.offer, db.local_today())
    try:
        png = creative.render_grafico_preco(achado.offer, serie, verdict,
                                            handle=handle, brand_name=nome_marca)
    except SourceError as exc:
        print(f"❌ flagrante: falha ao gerar o gráfico — {exc}")
        return 1
    legenda = legenda_do_flagrante(achado, verdict)

    if args.dry_run:
        caminho = _grava_previews("feed-flagrante", [png])[0]
        print(f"--- DRY-RUN: flagrante de {len(ineditos)} candidato(s) ---\n  {caminho}")
        print(f"\n{legenda}\n")
        return 0

    # NÃO publica. Nunca. A peça vai ao chat de operações e espera o dono.
    token, ops = _env("TELEGRAM_BOT_TOKEN"), _env("TELEGRAM_OPS_CHAT_ID")
    if not (token and ops):
        print("❌ flagrante: TELEGRAM_BOT_TOKEN/TELEGRAM_OPS_CHAT_ID ausentes — "
              "sem chat de operações não há a quem pedir aprovação")
        return 1
    resposta = send_photo_bytes(token, ops, png, caption=legenda,
                                filename="flagrante.png", mime="image/png")
    if not resposta.get("ok"):
        print(f"❌ flagrante: envio ao chat de operações falhou — "
              f"{resposta.get('description') or resposta}")
        return 1
    # F5: a marca do dedupe. Em `day_flags`, nunca em `posted` — e só DEPOIS de
    # o despacho ter dado certo: um envio que falhou não foi despachado, e
    # calar o produto por uma semana por causa disso seria perder a denúncia.
    db.set_day_flag(chave_do_flagrante(achado.offer),
                    f"despachado · gravidade {achado.gravidade:.2f}")
    # G2: e a marca do TETO DO DIA, pelo mesmo motivo e no mesmo instante —
    # depois do "ok". O comando roda em todos os disparos do dia; esta linha é
    # o que faz o segundo sair calado em vez de mandar outro produto ao ops.
    db.set_day_flag(FLAGRANTE_DIA_FLAG,
                    f"despachado · {achado.offer.item_id} · "
                    f"gravidade {achado.gravidade:.2f}")
    print(f"✅ flagrante despachado ao chat de operações (gravidade "
          f"{achado.gravidade:.2f}) — aguardando o ok do dono, e este produto "
          f"não volta por {FLAGRANTE_DEDUPE_DAYS} dias")
    return 0


def feed(cfg: dict, args) -> int:
    db = _abre_estado(cfg)
    # A10, como no `run`: em dry-run nem o cursor da descoberta avança.
    db.somente_leitura = args.dry_run
    try:
        if args.tipo == "flagrante":
            return _feed_flagrante(cfg, args, db)
        return _feed_termometro(cfg, args, db)
    finally:
        db.close()


def load_dotenv(path: str | Path = ".env", override: bool = True) -> int:
    """Carrega KEY=VALUE de um .env local para o ambiente. Por padrão o .env
    do projeto TEM precedência sobre variáveis globais da máquina — evita que
    um TELEGRAM_BOT_TOKEN de outro projeto (ex.: Claudefolio) vaze para este.
    Em produção (Actions) não existe .env (gitignored), então nada muda lá.
    Retorna quantas variáveis foram definidas."""
    p = Path(path)
    if not p.is_file():
        return 0
    n = 0
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and (override or key not in os.environ):
            os.environ[key] = value
            n += 1
    return n


def _signal_handler(token: str, ops: str):
    """SIGTERM (TimeoutStartSec do systemd) e SIGINT matavam o Python sem
    exceção: sem resumo, sem "❌ Run abortado", ops em silêncio (fase 5A).
    Avisa o chat de operações e sai com 128+n, como um processo morto por
    sinal."""
    def handler(signum, frame):
        if token and ops:
            send_text(token, ops, f"❌ Run interrompido (sinal {signum})")
        raise SystemExit(128 + int(signum))
    return handler


def _install_signal_handlers(token: str, ops: str) -> None:
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _signal_handler(token, ops))
        except (ValueError, OSError):
            pass   # fora da thread principal (embutido/testes): segue sem handler


def configure_stdout(stream=None) -> None:
    """Garante saída UTF-8 (emojis do doctor/resumos) mesmo em console Windows
    cp1252; sem efeito quando o stream já é UTF-8 ou não suporta reconfigure."""
    stream = stream or sys.stdout
    enc = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
    if enc != "utf8" and hasattr(stream, "reconfigure"):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, io.UnsupportedOperation):
            pass


def main(argv: list[str] | None = None) -> int:
    configure_stdout()
    load_dotenv()
    args = _build_parser().parse_args(argv)
    cfg = config.load_config(args.config)
    if args.cmd == "doctor":
        return doctor(cfg)
    if args.cmd == "ig-login":
        return ig_login(cfg)
    if args.cmd == "feed":
        return feed(cfg, args)
    if args.cmd == "preco-real":
        return preco_real_a_mao(cfg, args)

    # `stories` (fase 5F) é o MESMO run — mesmo ritmo, dedupe, teto diário e
    # resumo de operações — com os canais recortados nos de story. O nome do
    # argumento muda só porque `run` já tinha o dele.
    somente_story = args.cmd == "stories"
    posts_por_run = getattr(args, "posts", None) if somente_story else args.posts_per_run
    if posts_por_run is not None:
        # O teto diário e o RITMO continuam mandando (fase 5A): isto só diz
        # quantas ofertas UM run pode chegar a publicar. Um agendador de 30 em
        # 30 min precisa de mais folga por run que um de 5 em 5.
        cfg = {**cfg, "selection": {**cfg["selection"],
                                    "posts_per_run": int(posts_por_run)}}

    db = _abre_estado(cfg, stories=somente_story)
    sources, avisos = _build_sources(cfg, db)
    leitor_de_preco = _build_preco_real(cfg, db, avisos)
    channels = []
    if not args.dry_run:
        # A API privada só é montada sob `afiliado stories` — ver
        # AVISO_STORY_LINK_FORA_DO_RUN. O `db` vai junto: é nele que o desarme
        # do canal sobrevive ao processo (C2).
        channels, avisos_canais = _build_channels(
            cfg, somente=CANAIS_DO_STORIES if somente_story else None,
            api_privada=somente_story, db=db)
        avisos += avisos_canais
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    ops = os.environ.get("TELEGRAM_OPS_CHAT_ID", "")
    _install_signal_handlers(token, "" if args.dry_run else ops)
    try:
        wl = load_watchlist((cfg.get("watchlist") or {}).get("path", "data/watchlist.json"))
    except Exception:
        wl = None
    try:
        # `checa_cadencia` (5G, G3): o aviso de buraco na cadência é do run de
        # PRODUÇÃO. Desde a 5I as duas tarefas rodam na mesma máquina e na
        # mesma cadência, então quem vigia o agendador é o `afiliado run`;
        # ligar o aviso aqui também só duplicaria a mesma mensagem — e o
        # `stories` tem banco próprio, com uma tabela `runs` que só ele
        # alimenta.
        summary = pipeline.run(cfg, sources, channels, db, dry_run=args.dry_run, watchlist=wl,
                               warnings_iniciais=avisos,
                               checa_cadencia=not somente_story,
                               preco_real=leitor_de_preco)
    except pipeline.RunAborted as exc:
        # Todas as fontes falharam: a causa está no próprio motivo (os avisos
        # por fonte podem já ter sido deduplicados hoje) e vai ao journal e
        # ao ops; o run sai com erro.
        print(f"❌ Run abortado: {exc}")
        if not args.dry_run and token and ops:
            send_text(token, ops, exc.summary.text(header=f"❌ Run abortado: {exc}"))
        raise
    except Exception as exc:
        if not args.dry_run and token and ops:
            send_text(token, ops, f"❌ Run abortado: {exc}")
        raise
    finally:
        db.close()

    if args.dry_run:
        print(summary.text())
    elif token and ops:
        notify_empty = bool((cfg.get("ops") or {}).get("notify_empty_runs", False))
        # `dispatched` entra aqui: um run que só despachou artes ao chat de ops
        # aconteceu, e o resumo precisa chegar (A12 tirou o despacho de
        # `published`, e sem esta linha ele sumiria do ops junto).
        houve_algo = (summary.published or summary.dispatched
                      or summary.discarded or summary.warnings)
        if houve_algo or notify_empty:
            send_text(token, ops, summary.text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

import argparse
import io
import os
import signal
import sys
import time
from pathlib import Path

import httpx

from afiliado import config, llm, pipeline
from afiliado.channels import instagram_story_link
from afiliado.channels.instagram_common import GRAPH_HOSTS, graph_error
from afiliado.channels.instagram_feed import InstagramFeedChannel
from afiliado.channels.instagram_story import InstagramStoryChannel
from afiliado.channels.instagram_story_link import InstagramStoryLinkChannel
from afiliado.channels.story_dispatch import StoryDispatchChannel
from afiliado.channels.telegram import TelegramChannel, send_text
from afiliado.sources.meli import DEFAULT_OFFERS_PATH, MeliSource
from afiliado.sources.shopee import ShopeeSource
from afiliado.state import StateDB
from afiliado.watchlist import load_watchlist


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="afiliado")
    sub = p.add_subparsers(dest="cmd", required=True)
    prun = sub.add_parser("run", help="executa o pipeline")
    prun.add_argument("--dry-run", action="store_true",
                      help="APIs reais, mas imprime em vez de publicar")
    prun.add_argument("--config", default="config.yaml")
    prun.add_argument("--posts-per-run", type=int, default=None,
                      help="sobrepõe selection.posts_per_run (o Actions roda a cada "
                           "30 min e precisa de mais que a VPS, que roda a cada 5)")
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
    usuario, senha = _env("IG_USERNAME"), _senha()
    if not (usuario and senha):
        # A senha NUNCA entra em aviso, log ou resumo — só a ausência dela.
        _aviso(avisos, STORY_LINK_SEM_ENV)
        return
    bruto = story_link_cfg(cfg)
    sessao = ig_session_path(cfg)
    ch = InstagramStoryLinkChannel(
        usuario, senha, session_path=sessao, brand_handle=brand_handle,
        brand_name=brand_name, estado=db,
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
        situacao = f"{com_link} de {total} produto(s) do pool com link ({caminho})"
    if ligado and com_link == 0:
        print(f"❌ Mercado Livre: {situacao} — o ML não vai publicar nada. "
              f"Rode {acao} (ver docs/runbooks/meli-setup.md)")
        return False
    if com_link < total or not total:
        print(f"⚠️ Mercado Livre: {situacao} — rode {acao}")
    else:
        print(f"✅ Mercado Livre: {situacao}")
    return True


def _inteiro(valor) -> int | None:
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def _cota_de_publicacao(data) -> tuple[int | None, int | None, int]:
    """`quota_usage` e `config.quota_total`/`quota_duration` da resposta de
    `content_publishing_limit`.

    Ao vivo (2026-08-27) a Meta devolveu
    `{"data": [{"config": {"quota_total": 100, "quota_duration": 86400},
    "quota_usage": 1}]}`; o mesmo objeto sem o envelope `data` também é lido.
    Qualquer outra forma — lista vazia, campo ausente, número que não é número
    — vira `(None, None, 24)`, e o doctor diz o que sabe em vez de estourar
    (nunca vi esta rota falhar, e é justamente por isso que ela não pode
    derrubar o diagnóstico inteiro). `quota_duration` vem em segundos."""
    linha: dict = {}
    if isinstance(data, dict):
        linhas = data.get("data")
        if isinstance(linhas, list) and linhas and isinstance(linhas[0], dict):
            linha = linhas[0]
        elif "quota_usage" in data or "config" in data:
            linha = data
    conf = linha.get("config")
    conf = conf if isinstance(conf, dict) else {}
    segundos = _inteiro(conf.get("quota_duration"))
    return (_inteiro(linha.get("quota_usage")), _inteiro(conf.get("quota_total")),
            segundos // 3600 if segundos else 24)


def doctor(cfg: dict) -> int:
    ok = True
    try:
        # Uma chamada só (a p1 de uma raiz): o doctor confere credencial e
        # parsing, não faz varredura — e sem StateDB não mexe no cursor do run.
        offers = _shopee().fetch_offers(
            {**cfg, "shopee": {**cfg["shopee"], "calls_per_run": 1, "pages": 1}})
        print(f"✅ Shopee: {len(offers)} ofertas; primeira: "
              f"{offers[0] if offers else '(vazio — confira sort_types/list_type)'}")
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

    return 0 if ok else 1


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
        # Sessão VELHA é uma boa notícia, não um alerta: device estável e poucos
        # logins é exatamente o que evita desafio. Só renove quando ela parar de
        # funcionar (o canal avisa, com `afiliado ig-login` na mensagem).
        print(f"✅ instagram_story_link: sessão de {idade} dia(s) em {sessao}")
    else:
        print(f"⚠️ instagram_story_link: sem sessão em {sessao} — rode `afiliado ig-login` "
              "(senão o primeiro story do run faz login com senha, e login novo é o que "
              "atrai desafio)")
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
    caminho = ig_session_path(cfg)
    if not (usuario and senha):
        print("❌ ig-login: IG_USERNAME/IG_PASSWORD ausentes no ambiente — preencha o "
              ".env (ver docs/runbooks/instagrapi-stories.md)")
        return 1
    try:
        cl = instagram_story_link.nova_sessao()
    except ImportError:
        print(f"❌ ig-login: {instagram_story_link.SEM_INSTAGRAPI}")
        return 1
    try:
        instagram_story_link.entra(cl, usuario, senha, caminho, totp_seed=semente)
        instagram_story_link.guarda_sessao(cl, caminho)
    except Exception as exc:      # noqa: BLE001 - vira mensagem, nunca traceback
        # O texto da exceção é de terceiro: raspado antes de ir ao terminal.
        detalhe = instagram_story_link.sem_segredos(str(exc), senha, semente)
        print(f"❌ ig-login: falhou ({type(exc).__name__}: {detalhe})")
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
        summary = pipeline.run(cfg, sources, channels, db, dry_run=args.dry_run, watchlist=wl,
                               warnings_iniciais=avisos)
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

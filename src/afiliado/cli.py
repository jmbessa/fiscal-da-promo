import argparse
import io
import os
import sys
from pathlib import Path

import httpx

from afiliado import config, llm, message, pipeline, pricing
from afiliado.channels.instagram_feed import GRAPH_HOSTS, InstagramFeedChannel
from afiliado.channels.story_dispatch import StoryDispatchChannel
from afiliado.channels.telegram import TelegramChannel, send_text
from afiliado.sources.meli import MeliSource
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
    pdoc = sub.add_parser("doctor", help="verifica credenciais e dependências")
    pdoc.add_argument("--config", default="config.yaml")
    return p


def _shopee() -> ShopeeSource:
    return ShopeeSource(os.environ["SHOPEE_APP_ID"], os.environ["SHOPEE_APP_SECRET"])


def _meli() -> MeliSource | None:
    client_id = _env("MELI_CLIENT_ID")
    client_secret = _env("MELI_CLIENT_SECRET")
    if not (client_id and client_secret):
        print("⚠️ fonte meli ignorada: variável MELI_CLIENT_ID/MELI_CLIENT_SECRET ausente "
              "(ver docs/runbooks/meli-setup.md)")
        return None
    return MeliSource(client_id, client_secret, refresh_token=_env("MELI_REFRESH_TOKEN"))


def _build_sources(cfg: dict) -> list:
    """Monta as fontes habilitadas em `sources:` (config.yaml).

    Seção ausente equivale a `{"shopee": True}` (comportamento de antes da
    fase 3) — o Mercado Livre nasce desligado. Fonte ligada sem env
    necessária: aviso no stdout e segue sem ela, nunca derruba o run (mesmo
    padrão de `_build_channels`)."""
    src_cfg = cfg.get("sources") or {"shopee": True}
    sources: list = []
    if src_cfg.get("shopee", True):
        sources.append(_shopee())
    if src_cfg.get("meli", False):
        meli = _meli()
        if meli is not None:
            sources.append(meli)
    return sources


def _env(name: str) -> str:
    """os.environ.get com .strip() — mata o footgun de credencial colada com
    espaço/quebra de linha nas pontas antes que ela chegue a algum canal."""
    return os.environ.get(name, "").strip()


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


def _regua(cfg: dict) -> dict:
    """`selection.min_real_discount_pct` e `selection.seal_tolerance` como
    kwargs para os canais que renderizam arte ou legenda — a mesma leitura
    (e os mesmos defaults de `pricing`/`message`) que o pipeline usa para o
    texto do Telegram, para os três concordarem quando o config muda."""
    sel = cfg.get("selection") or {}
    return {
        "min_real_discount_pct": int(pricing.setting(
            sel, "min_real_discount_pct", pricing.DEFAULT_MIN_REAL_DISCOUNT_PCT)),
        "seal_tolerance": float(pricing.setting(
            sel, "seal_tolerance", message.DEFAULT_SEAL_TOLERANCE)),
    }


def _build_channels(cfg: dict) -> list:
    """Monta os canais habilitados em config.yaml a partir das envs disponíveis.

    Seção `channels` ausente equivale a `{"telegram": True}` (comportamento da
    fase 1). Cada entrada aceita bool ou dict (`enabled`, `max_per_day` —
    fase 1.7); quando `max_per_day` está presente, vira atributo de instância
    no canal construído (`ch.max_per_day`), lido pelo pipeline via getattr.
    Canal ligado sem env necessária: aviso no stdout e segue sem ele — nunca
    derruba o run. Os canais que renderizam arte/legenda recebem a régua
    (`_regua`) do config."""
    ch_cfg = cfg.get("channels") or {"telegram": True}
    brand_cfg = cfg.get("brand") or {}
    brand_handle = brand_cfg.get("handle") or None
    brand_name = brand_cfg.get("name") or "Fiscal da Promo"
    regua = _regua(cfg)
    channels: list = []

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
            print("⚠️ canal telegram ignorado: variável TELEGRAM_BOT_TOKEN/TELEGRAM_CHANNEL_ID ausente")

    enabled, max_per_day = _channel_settings(ch_cfg.get("story_dispatch"))
    if enabled:
        token = _env("TELEGRAM_BOT_TOKEN")
        ops = _env("TELEGRAM_OPS_CHAT_ID")
        if token and ops:
            ch = StoryDispatchChannel(token, ops, brand_handle=brand_handle, brand_name=brand_name,
                                      **regua)
            if max_per_day is not None:
                ch.max_per_day = int(max_per_day)
            channels.append(ch)
        else:
            print("⚠️ canal story_dispatch ignorado: variável TELEGRAM_BOT_TOKEN/TELEGRAM_OPS_CHAT_ID ausente")

    enabled, max_per_day = _channel_settings(ch_cfg.get("instagram_feed"))
    if enabled:
        ig_user = _env("IG_USER_ID")
        ig_token = _env("IG_ACCESS_TOKEN")
        bot_token = _env("TELEGRAM_BOT_TOKEN")
        ops = _env("TELEGRAM_OPS_CHAT_ID")
        if ig_user and ig_token and bot_token and ops:
            ch = InstagramFeedChannel(ig_user, ig_token, bot_token, ops, brand_handle=brand_handle,
                                      brand_name=brand_name, api=_instagram_api(cfg), **regua)
            if max_per_day is not None:
                ch.max_per_day = int(max_per_day)
            channels.append(ch)
        else:
            print("⚠️ canal instagram_feed ignorado: variável IG_USER_ID/IG_ACCESS_TOKEN "
                  "(ou TELEGRAM_BOT_TOKEN/TELEGRAM_OPS_CHAT_ID p/ hospedagem) ausente")

    return channels


def doctor(cfg: dict) -> int:
    ok = True
    try:
        offers = _shopee().fetch_offers({**cfg, "shopee": {**cfg["shopee"], "pages": 1}})
        print(f"✅ Shopee: {len(offers)} ofertas; primeira: "
              f"{offers[0] if offers else '(vazio — confira sort_types/list_type)'}")
    except Exception as exc:
        ok = False
        print(f"❌ Shopee: {exc}")

    meli = _meli()  # reaproveita o helper de _build_sources: mesma leitura de
                    # env (_env, já com .strip()) e a mesma construção; sem
                    # credenciais, _meli() já imprime o aviso e não falha o doctor.
    if meli is not None:
        try:
            meli.ensure_token()  # só valida as credenciais OAuth
            offers = meli.fetch_offers(cfg)  # leitura local do pool, sem rede
            if meli.pool_warning:
                print(f"⚠️ Mercado Livre: token ok; {meli.pool_warning}")
            else:
                print(f"✅ Mercado Livre: token ok; {len(offers)} ofertas no pool")
        except Exception as exc:
            ok = False
            print(f"❌ Mercado Livre: {exc}")

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    ops = os.environ.get("TELEGRAM_OPS_CHAT_ID", "")
    if token and ops:
        send_text(token, ops, "🩺 doctor: bot funcionando")
        print("✅ Telegram: mensagem de teste enviada ao chat de operações")
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
        try:
            r = httpx.get(f"{GRAPH_HOSTS[_instagram_api(cfg)]}/{ig_user}",
                          params={"fields": "username", "access_token": ig_token}, timeout=20)
            data = r.json()
        except Exception as exc:
            ok = False
            print(f"❌ Instagram: {exc}")
        else:
            if r.status_code == 200 and isinstance(data, dict) and "username" in data:
                print(f"✅ Instagram: conectado como @{data['username']}")
            else:
                ok = False
                print(f"❌ Instagram: {data}")
    else:
        print("ℹ️ Instagram: não configurado (ver docs/runbooks/meta-setup.md)")

    return 0 if ok else 1


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

    db = StateDB(cfg["state"]["path"])
    sources = _build_sources(cfg)
    channels = []
    if not args.dry_run:
        channels = _build_channels(cfg)
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    ops = os.environ.get("TELEGRAM_OPS_CHAT_ID", "")
    try:
        wl = load_watchlist((cfg.get("watchlist") or {}).get("path", "data/watchlist.json"))
    except Exception:
        wl = None
    try:
        summary = pipeline.run(cfg, sources, channels, db, dry_run=args.dry_run, watchlist=wl)
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
        houve_algo = summary.published or summary.discarded or summary.warnings
        if houve_algo or notify_empty:
            send_text(token, ops, summary.text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

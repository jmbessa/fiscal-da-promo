import argparse
import os

import httpx

from afiliado import config, llm, pipeline
from afiliado.channels.instagram_feed import GRAPH, InstagramFeedChannel
from afiliado.channels.story_dispatch import StoryDispatchChannel
from afiliado.channels.telegram import TelegramChannel, send_text
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


def _env(name: str) -> str:
    """os.environ.get com .strip() — mata o footgun de credencial colada com
    espaço/quebra de linha nas pontas antes que ela chegue a algum canal."""
    return os.environ.get(name, "").strip()


def _build_channels(cfg: dict) -> list:
    """Monta os canais habilitados em config.yaml a partir das envs disponíveis.

    Seção `channels` ausente equivale a `{"telegram": True}` (comportamento da
    fase 1). Canal ligado sem env necessária: aviso no stdout e segue sem ele —
    nunca derruba o run."""
    ch_cfg = cfg.get("channels") or {"telegram": True}
    channels: list = []

    if ch_cfg.get("telegram"):
        token = _env("TELEGRAM_BOT_TOKEN")
        chat_id = _env("TELEGRAM_CHANNEL_ID")
        if token and chat_id:
            channels.append(TelegramChannel(token, chat_id))
        else:
            print("⚠️ canal telegram ignorado: variável TELEGRAM_BOT_TOKEN/TELEGRAM_CHANNEL_ID ausente")

    if ch_cfg.get("story_dispatch"):
        token = _env("TELEGRAM_BOT_TOKEN")
        ops = _env("TELEGRAM_OPS_CHAT_ID")
        if token and ops:
            channels.append(StoryDispatchChannel(token, ops))
        else:
            print("⚠️ canal story_dispatch ignorado: variável TELEGRAM_BOT_TOKEN/TELEGRAM_OPS_CHAT_ID ausente")

    if ch_cfg.get("instagram_feed"):
        ig_user = _env("IG_USER_ID")
        ig_token = _env("IG_ACCESS_TOKEN")
        bot_token = _env("TELEGRAM_BOT_TOKEN")
        ops = _env("TELEGRAM_OPS_CHAT_ID")
        if ig_user and ig_token and bot_token and ops:
            channels.append(InstagramFeedChannel(ig_user, ig_token, bot_token, ops))
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
            r = httpx.get(f"{GRAPH}/{ig_user}",
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


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    cfg = config.load_config(args.config)
    if args.cmd == "doctor":
        return doctor(cfg)

    db = StateDB(cfg["state"]["path"])
    sources = [_shopee()]
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
        send_text(token, ops, summary.text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

import argparse
import os

from afiliado import config, llm, pipeline
from afiliado.channels.telegram import TelegramChannel, send_text
from afiliado.sources.shopee import ShopeeSource
from afiliado.state import StateDB


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
        channels = [TelegramChannel(os.environ["TELEGRAM_BOT_TOKEN"],
                                    os.environ["TELEGRAM_CHANNEL_ID"])]
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    ops = os.environ.get("TELEGRAM_OPS_CHAT_ID", "")
    try:
        summary = pipeline.run(cfg, sources, channels, db, dry_run=args.dry_run)
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

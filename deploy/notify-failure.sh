#!/usr/bin/env bash
# Disparado por OnFailure= de afiliado.service (ver deploy/afiliado.service):
# avisa o chat de operações que a unidade falhou — SIGTERM do TimeoutStartSec,
# OOM, venv quebrado, Python que morreu sem exceção. Sem isto uma VPS quebrada
# era indistinguível de "sem oferta boa".
#
# Lê TELEGRAM_BOT_TOKEN/TELEGRAM_OPS_CHAT_ID do ambiente (EnvironmentFile da
# unidade) ou, na falta, do .env. Nunca imprime o token (sem xtrace).
set -u
ENV_FILE="${ENV_FILE:-/opt/afiliado/.env}"

valor_do_env() {  # valor_do_env CHAVE — última ocorrência no .env, sem aspas nem CR
  grep -E "^${1}=" "$ENV_FILE" 2>/dev/null | tail -n 1 | cut -d= -f2- \
    | tr -d "\"'\r" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//'
}

TOKEN="${TELEGRAM_BOT_TOKEN:-}"
CHAT="${TELEGRAM_OPS_CHAT_ID:-}"
[ -n "$TOKEN" ] || TOKEN="$(valor_do_env TELEGRAM_BOT_TOKEN)"
[ -n "$CHAT" ] || CHAT="$(valor_do_env TELEGRAM_OPS_CHAT_ID)"
if [ -z "$TOKEN" ] || [ -z "$CHAT" ]; then
  echo "notify-failure: TELEGRAM_BOT_TOKEN/TELEGRAM_OPS_CHAT_ID ausentes (ambiente e $ENV_FILE)" >&2
  exit 0
fi

TEXTO="❌ unidade afiliado falhou — ver journalctl -u afiliado"
if curl -fsS --max-time 20 "https://api.telegram.org/bot${TOKEN}/sendMessage" \
     --data-urlencode "chat_id=${CHAT}" --data-urlencode "text=${TEXTO}" >/dev/null; then
  echo "notify-failure: aviso enviado ao chat de operações"
else
  echo "notify-failure: envio ao Telegram falhou" >&2
fi

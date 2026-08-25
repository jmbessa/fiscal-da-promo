#!/usr/bin/env bash
# Cadastra no GitHub Actions todos os segredos presentes no .env local.
# Requer o GitHub CLI autenticado:  gh auth login
# Uso (na raiz do projeto):  bash deploy/github-secrets.sh [<owner/repo>]
set -euo pipefail

REPO_ARG=()
[ $# -ge 1 ] && REPO_ARG=(--repo "$1")

[ -f .env ] || { echo "erro: .env não encontrado na pasta atual"; exit 1; }
command -v gh >/dev/null || { echo "erro: GitHub CLI (gh) não instalado"; exit 1; }

ESPERADOS=(SHOPEE_APP_ID SHOPEE_APP_SECRET TELEGRAM_BOT_TOKEN TELEGRAM_CHANNEL_ID
           TELEGRAM_OPS_CHAT_ID CLAUDE_CODE_OAUTH_TOKEN IG_USER_ID IG_ACCESS_TOKEN)

for nome in "${ESPERADOS[@]}"; do
  linha=$(grep -m1 "^${nome}=" .env || true)
  valor=${linha#*=}
  if [ -z "$linha" ] || [ -z "$valor" ]; then
    echo "-- $nome: ausente no .env (pulado)"
    continue
  fi
  printf '%s' "$valor" | gh secret set "$nome" "${REPO_ARG[@]}" --body-file -
  echo "ok $nome"
done

echo
echo "Conferir:  gh secret list ${REPO_ARG[*]}"

#!/usr/bin/env bash
# Cadastra no GitHub Actions todos os segredos do .env local, de uma vez.
# Requer o GitHub CLI autenticado:  gh auth login
# Uso (na raiz do projeto):  bash deploy/github-secrets.sh [<owner/repo>]
#
# O próprio `gh` entende o formato dotenv (--env-file), inclusive valores que
# contêm "=" e arquivos salvos com CRLF no Windows — por isso não parseamos
# o arquivo aqui.
set -euo pipefail

REPO_ARG=()
[ $# -ge 1 ] && REPO_ARG=(--repo "$1")

[ -f .env ] || { echo "erro: .env não encontrado na pasta atual"; exit 1; }
command -v gh >/dev/null || { echo "erro: GitHub CLI (gh) não instalado"; exit 1; }

gh secret set "${REPO_ARG[@]}" --env-file .env

echo "Segredos cadastrados. Conferir:"
gh secret list "${REPO_ARG[@]}"

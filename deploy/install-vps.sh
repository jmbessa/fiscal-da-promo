#!/usr/bin/env bash
# Instalação do Fiscal da Promo numa VPS Ubuntu/Debian limpa.
# Uso (como root):  bash install-vps.sh <URL_DO_REPO_GIT>
# Depois: preencher /opt/afiliado/.env e rodar `systemctl enable --now afiliado.timer`.
set -euo pipefail

REPO_URL="${1:?informe a URL do repositório git}"
APP_DIR=/opt/afiliado
APP_USER=afiliado

echo "==> 1/7 pacotes do sistema"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git curl ca-certificates python3 python3-venv python3-pip

echo "==> 2/7 Node LTS (necessário para o Claude Code headless)"
if ! command -v node >/dev/null 2>&1; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
  apt-get install -y -qq nodejs
fi
npm install -g @anthropic-ai/claude-code

echo "==> 3/7 usuário de serviço e diretório"
id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin "$APP_USER"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" pull --ff-only
else
  git clone "$REPO_URL" "$APP_DIR"
fi

echo "==> 4/7 fuso horário (São Paulo) — o timer usa a hora local"
timedatectl set-timezone America/Sao_Paulo || echo "   (aviso: não foi possível ajustar o fuso automaticamente)"

echo "==> 5/7 ambiente Python"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q -e "$APP_DIR"

echo "==> 6/7 .env e permissões"
if [ ! -f "$APP_DIR/.env" ]; then
  cat > "$APP_DIR/.env" <<'ENVTEMPLATE'
SHOPEE_APP_ID=
SHOPEE_APP_SECRET=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHANNEL_ID=
TELEGRAM_OPS_CHAT_ID=
CLAUDE_CODE_OAUTH_TOKEN=
IG_USER_ID=
IG_ACCESS_TOKEN=
ENVTEMPLATE
  echo "   .env criado como modelo — PREENCHA antes de ligar o timer."
fi
chmod 600 "$APP_DIR/.env"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

echo "==> 7/7 unidades systemd"
install -m 644 "$APP_DIR/deploy/afiliado.service" /etc/systemd/system/afiliado.service
install -m 644 "$APP_DIR/deploy/afiliado.timer" /etc/systemd/system/afiliado.timer
systemctl daemon-reload

cat <<'FIM'

Instalação concluída. Próximos passos:

  1. Preencher os segredos:      sudo nano /opt/afiliado/.env
  2. Testar as credenciais:      sudo -u afiliado -H /opt/afiliado/.venv/bin/afiliado doctor
  3. Ensaiar sem publicar:       sudo -u afiliado -H /opt/afiliado/.venv/bin/afiliado run --dry-run
  4. Ligar a cadência de 5 min:  sudo systemctl enable --now afiliado.timer
  5. Acompanhar:                 systemctl list-timers afiliado.timer
                                 journalctl -u afiliado.service -f

  IMPORTANTE: desative o workflow `publish` no GitHub Actions antes de ligar o
  timer — os dois rodando ao mesmo tempo têm bancos de estado separados e
  publicariam a mesma oferta duas vezes.
FIM

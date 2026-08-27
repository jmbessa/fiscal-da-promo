#!/usr/bin/env bash
# Instalação do Fiscal da Promo numa VPS Ubuntu/Debian limpa.
# Uso (como root):  bash install-vps.sh <URL_DO_REPO_GIT>
# Depois: preencher /opt/afiliado/.env e rodar `systemctl enable --now afiliado.timer`.
# Re-executável: numa segunda rodada só atualiza o código e reinstala as unidades.
set -euo pipefail

REPO_URL="${1:?informe a URL do repositório git}"
APP_DIR=/opt/afiliado
APP_USER=afiliado
FUSO_OK=1

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
  # O diretório pertence ao usuário de serviço (passo 6): git >= 2.35.2
  # recusa `git pull` do root aqui ("dubious ownership"). Puxa como ele.
  runuser -u "$APP_USER" -- git -C "$APP_DIR" pull --ff-only
else
  git clone "$REPO_URL" "$APP_DIR"
fi

echo "==> 4/7 fuso horário (São Paulo) — o timer e o ritmo diário usam a hora local"
if ! timedatectl set-timezone America/Sao_Paulo; then
  FUSO_OK=0
  echo
  echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
  echo "!!! ATENÇÃO: não foi possível ajustar o fuso para America/Sao_Paulo.       !!!"
  echo "!!! O timer (08:00-23:55) e o ritmo diário usam a hora LOCAL da VPS: em    !!!"
  echo "!!! UTC a janela vira 05:00-20:55 BRT e o horário nobre fica de fora.      !!!"
  echo "!!! Ajuste manualmente ANTES de ligar o timer, por exemplo:                !!!"
  echo "!!!   sudo timedatectl set-timezone America/Sao_Paulo                      !!!"
  echo "!!!   (ou, sem systemd-timesyncd/privilégio: ln -sf                        !!!"
  echo "!!!    /usr/share/zoneinfo/America/Sao_Paulo /etc/localtime)               !!!"
  echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
  echo
fi

echo "==> 5/7 ambiente Python"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q -e "$APP_DIR"

echo "==> 6/7 .env e permissões"
if [ ! -f "$APP_DIR/.env" ]; then
  # TODAS as variáveis que o pipeline lê (as mesmas 12 dos GitHub Secrets).
  # As do Instagram e do Mercado Livre podem ficar vazias: o canal/fonte
  # correspondente é ignorado com aviso no chat de operações.
  # ART_HOST_BOT_TOKEN (fase 5C, A5): bot SECUNDÁRIO, só precisa estar no chat
  # de operações. É o token dele que vai à Meta na URL da arte do feed —
  # vazio, vai o do bot administrador do canal, e o run avisa todo dia.
  # IG_USERNAME/IG_PASSWORD (fase 5F): é a senha da conta do Instagram, não é
  # token revogável — quem a tem publica, apaga e troca a senha. Só existem
  # para o canal `instagram_story_link` (instagrapi, story COM figurinha de
  # link), que NUNCA roda na VPS nem no GitHub Actions: ele roda na máquina do
  # dono, com `afiliado stories`. Deixe as duas VAZIAS aqui.
  cat > "$APP_DIR/.env" <<'ENVTEMPLATE'
SHOPEE_APP_ID=
SHOPEE_APP_SECRET=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHANNEL_ID=
TELEGRAM_OPS_CHAT_ID=
ART_HOST_BOT_TOKEN=
CLAUDE_CODE_OAUTH_TOKEN=
IG_USER_ID=
IG_ACCESS_TOKEN=
# As duas abaixo são a SENHA da conta do Instagram (não é token revogável) e
# só servem ao `afiliado stories` na máquina do dono. Deixe vazias na VPS.
IG_USERNAME=
IG_PASSWORD=
MELI_CLIENT_ID=
MELI_CLIENT_SECRET=
MELI_REFRESH_TOKEN=
ENVTEMPLATE
  echo "   .env criado como modelo — PREENCHA antes de ligar o timer."
fi
chmod 600 "$APP_DIR/.env"
chmod +x "$APP_DIR/deploy/notify-failure.sh"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

echo "==> 7/7 unidades systemd"
install -m 644 "$APP_DIR/deploy/afiliado.service" /etc/systemd/system/afiliado.service
install -m 644 "$APP_DIR/deploy/afiliado-notify.service" /etc/systemd/system/afiliado-notify.service
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
  6. Testar o aviso de falha:    sudo systemctl start afiliado-notify.service
                                 (deve chegar "unidade afiliado falhou" no chat de operações)

  IMPORTANTE: desative o workflow `publish` no GitHub Actions antes de ligar o
  timer — os dois rodando ao mesmo tempo têm bancos de estado separados e
  publicariam a mesma oferta duas vezes.
FIM

if [ "$FUSO_OK" -eq 0 ]; then
  echo
  echo "!!! LEMBRETE: o fuso NÃO foi ajustado (passo 4). Corrija antes de ligar o timer. !!!"
fi

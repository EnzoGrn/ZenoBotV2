#!/usr/bin/env bash
# À lancer sur le VPS, en root, après avoir installé Docker (voir README). Peut être relancé sans risque.
#
# Prépare tout ce qu'il faut pour faire tourner le bot :
#   - l'utilisateur « deploy », qui possède le dossier du bot et pilote Docker
#     (c'est lui que GitHub Actions utilise pour déployer)
#   - le dossier de données, réservé à l'utilisateur du conteneur (uid 10001)
#   - la clé SSH de GitHub Actions (facultatif : tu peux la donner plus tard en relançant le script)
#
# Usage : sudo bash setup-server.sh ["ssh-ed25519 AAAA... github-actions-zenobot"]
set -euo pipefail

PUBKEY="${1:-}"
APP_DIR="${APP_DIR:-/opt/zenobot}"
DEPLOY_USER="${DEPLOY_USER:-deploy}"
CONTAINER_UID="${CONTAINER_UID:-10001}"   # doit correspondre à l'utilisateur « bot » du Dockerfile

if [ "$(id -u)" -ne 0 ]; then
  echo "À lancer en root : sudo bash $0 ..." >&2
  exit 1
fi
if [ -n "$PUBKEY" ]; then
  case "$PUBKEY" in
    ssh-ed25519\ *|ssh-rsa\ *|ecdsa-sha2-*) ;;
    *) echo "Cette valeur ne ressemble pas à une clé publique SSH (elle doit commencer par ssh-ed25519...)." >&2; exit 1 ;;
  esac
fi
if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  echo "Docker (avec le plugin « compose ») n'est pas installé : suis l'étape « Installer Docker » du README, puis relance ce script." >&2
  exit 1
fi

# rsync est nécessaire pour que GitHub Actions envoie le code
if ! command -v rsync >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update -qq && apt-get install -y -qq rsync
  else
    echo "Installe rsync avec le gestionnaire de paquets de ton système, puis relance ce script." >&2
    exit 1
  fi
fi

# 1. Utilisateur de déploiement (connexion par clé uniquement, pas de mot de passe)
if ! id "$DEPLOY_USER" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash "$DEPLOY_USER"
fi
usermod -aG docker "$DEPLOY_USER"

# 2. Dossier du bot : le code appartient à « deploy », la base de données à l'utilisateur du conteneur
mkdir -p "$APP_DIR/data"
chown -R "$DEPLOY_USER:$DEPLOY_USER" "$APP_DIR"
chown -R "$CONTAINER_UID:$CONTAINER_UID" "$APP_DIR/data"
chmod 700 "$APP_DIR/data"
if [ -f "$APP_DIR/.env" ]; then
  chmod 600 "$APP_DIR/.env"
fi

# 3. Clé SSH autorisée (si fournie)
if [ -n "$PUBKEY" ]; then
  SSH_DIR="/home/$DEPLOY_USER/.ssh"
  install -d -m 700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$SSH_DIR"
  touch "$SSH_DIR/authorized_keys"
  if ! grep -qxF "$PUBKEY" "$SSH_DIR/authorized_keys"; then
    printf '%s\n' "$PUBKEY" >> "$SSH_DIR/authorized_keys"
  fi
  chown "$DEPLOY_USER:$DEPLOY_USER" "$SSH_DIR/authorized_keys"
  chmod 600 "$SSH_DIR/authorized_keys"
fi

# 4. Reste d'une ancienne installation sans Docker (service systemd) ?
if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files zenobot.service 2>/dev/null | grep -q zenobot.service; then
  echo
  echo "⚠️  Un ancien service systemd « zenobot » existe : il utiliserait le même token que le conteneur."
  echo "   Supprime-le : sudo systemctl disable --now zenobot && sudo rm /etc/systemd/system/zenobot.service && sudo systemctl daemon-reload"
fi

echo
echo "✅ Serveur prêt (dossier : $APP_DIR, utilisateur de déploiement : $DEPLOY_USER)."
if [ -z "$PUBKEY" ]; then
  echo "   Pour le déploiement automatique, relance ce script avec la clé publique SSH de GitHub Actions."
fi

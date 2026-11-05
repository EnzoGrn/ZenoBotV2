#!/usr/bin/env bash
# À lancer UNE SEULE FOIS sur le VPS, en root, après avoir installé le bot (étapes 1 à 6 du README).
# Prépare un utilisateur « deploy » limité, utilisé uniquement par GitHub Actions :
#   - il peut écrire le code du bot dans /opt/zenobot (mais pas lire le .env)
#   - il peut redémarrer le service du bot, et rien d'autre en root
#
# Usage : sudo bash setup-server.sh "ssh-ed25519 AAAA... github-actions-zenobot"
set -euo pipefail

PUBKEY="${1:-}"
APP_DIR="${APP_DIR:-/opt/zenobot}"
APP_USER="${APP_USER:-zenobot}"
SERVICE="${SERVICE:-zenobot}"
DEPLOY_USER="${DEPLOY_USER:-deploy}"

if [ -z "$PUBKEY" ]; then
  echo "Usage : sudo bash $0 \"<clé publique SSH>\"" >&2
  echo "La clé publique est le contenu du fichier .pub généré avec ssh-keygen (voir README)." >&2
  exit 1
fi
case "$PUBKEY" in
  ssh-ed25519\ *|ssh-rsa\ *|ecdsa-sha2-*) ;;
  *) echo "Cette valeur ne ressemble pas à une clé publique SSH (elle doit commencer par ssh-ed25519...)." >&2; exit 1 ;;
esac
if [ "$(id -u)" -ne 0 ]; then
  echo "À lancer en root : sudo bash $0 ..." >&2
  exit 1
fi
if ! id "$APP_USER" >/dev/null 2>&1 || [ ! -d "$APP_DIR" ]; then
  echo "L'utilisateur $APP_USER ou le dossier $APP_DIR n'existe pas : installe d'abord le bot (README, étape 3)." >&2
  exit 1
fi

# rsync est nécessaire pour envoyer le code
if ! command -v rsync >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update -qq && apt-get install -y -qq rsync
  else
    echo "Installe rsync avec le gestionnaire de paquets de ton système, puis relance ce script." >&2
    exit 1
  fi
fi

# 1. Utilisateur de déploiement (sans mot de passe : connexion par clé uniquement)
if ! id "$DEPLOY_USER" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash "$DEPLOY_USER"
fi
usermod -aG "$APP_USER" "$DEPLOY_USER"
if getent group systemd-journal >/dev/null; then
  usermod -aG systemd-journal "$DEPLOY_USER"   # lecture des logs du service (vérification après déploiement)
fi

# 2. Droits sur le dossier du bot : écriture pour le groupe, mais .env réservé au bot
chgrp -R "$APP_USER" "$APP_DIR"
chmod -R g+rwX "$APP_DIR"
find "$APP_DIR" -type d -exec chmod g+s {} +
if [ -f "$APP_DIR/.env" ]; then
  chown "$APP_USER:$APP_USER" "$APP_DIR/.env"
  chmod 600 "$APP_DIR/.env"
fi

# 3. Clé SSH autorisée
SSH_DIR="/home/$DEPLOY_USER/.ssh"
install -d -m 700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$SSH_DIR"
touch "$SSH_DIR/authorized_keys"
if ! grep -qxF "$PUBKEY" "$SSH_DIR/authorized_keys"; then
  printf '%s\n' "$PUBKEY" >> "$SSH_DIR/authorized_keys"
fi
chown "$DEPLOY_USER:$DEPLOY_USER" "$SSH_DIR/authorized_keys"
chmod 600 "$SSH_DIR/authorized_keys"

# 4. sudo restreint : redémarrer le service du bot, rien d'autre
SYSTEMCTL="$(readlink -f "$(command -v systemctl)")"
TMP="$(mktemp)"
printf '%s ALL=(root) NOPASSWD: %s restart %s, %s is-active %s\n' \
  "$DEPLOY_USER" "$SYSTEMCTL" "$SERVICE" "$SYSTEMCTL" "$SERVICE" > "$TMP"
if ! visudo -cf "$TMP" >/dev/null; then
  rm -f "$TMP"
  echo "Erreur interne : règle sudo invalide, rien n'a été installé." >&2
  exit 1
fi
install -m 440 -o root -g root "$TMP" "/etc/sudoers.d/${SERVICE}-deploy"
rm -f "$TMP"

echo
echo "✅ Serveur prêt pour le déploiement automatique."
echo "   Utilisateur de déploiement : $DEPLOY_USER (dossier : $APP_DIR)"
echo "   Test depuis ton ordinateur : ssh -i <ta clé privée> $DEPLOY_USER@<adresse du VPS> 'sudo -n systemctl is-active $SERVICE'"

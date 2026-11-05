#!/usr/bin/env bash
# Exécuté SUR LE VPS (envoyé par le workflow via « ssh ... bash -s ») avant chaque déploiement.
# Sauvegarde le code actuellement en production pour pouvoir revenir en arrière.
# Le .env, la base (data/) et le venv ne sont jamais touchés.
set -euo pipefail
umask 002

APP_DIR="${APP_DIR:-/opt/zenobot}"
STATE_DIR="$APP_DIR/.deploy"

mkdir -p "$STATE_DIR"
rm -f "$STATE_DIR/previous.tar.gz"

if [ -f "$APP_DIR/bot.py" ]; then
  tar -czf "$STATE_DIR/previous.tar.gz" -C "$APP_DIR" \
    --exclude=./venv --exclude=./data --exclude=./.env --exclude=./.deploy --exclude=__pycache__ .
  echo "Sauvegarde de la version actuelle : $STATE_DIR/previous.tar.gz"
else
  echo "Premier déploiement : rien à sauvegarder."
fi

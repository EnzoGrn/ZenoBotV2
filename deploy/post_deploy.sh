#!/usr/bin/env bash
# Exécuté SUR LE VPS (envoyé par le workflow via « ssh ... bash -s ») une fois les fichiers copiés.
# 1. installe les dépendances  2. redémarre le bot  3. vérifie qu'il se connecte à Discord
# Si une étape échoue : retour automatique à la version précédente, puis le déploiement est marqué en échec.
set -euo pipefail
umask 002

APP_DIR="${APP_DIR:-/opt/zenobot}"
SERVICE="${SERVICE:-zenobot}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-60}"   # secondes d'attente maximum pour la connexion à Discord
HEALTH_INTERVAL="${HEALTH_INTERVAL:-2}"
SUDO="${SUDO-sudo -n}"                   # « sudo -n » : échoue au lieu de bloquer si un mot de passe est demandé
STATE_DIR="$APP_DIR/.deploy"
HEALTH_MARKER="Connecté en tant que"     # ligne écrite par bot.py quand la connexion Discord est établie

cd "$APP_DIR"

install_deps() {
  if [ ! -x "$APP_DIR/venv/bin/pip" ]; then
    echo "venv introuvable dans $APP_DIR/venv : suis l'étape 3 du README." >&2
    return 1
  fi
  "$APP_DIR/venv/bin/pip" install --quiet --disable-pip-version-check -r "$APP_DIR/requirements.txt"
}

restart_service() {
  # $SUDO volontairement sans guillemets : il peut contenir « sudo -n » (ou être vide en test)
  # shellcheck disable=SC2086
  $SUDO systemctl restart "$SERVICE"
}

wait_healthy() {
  local since="$1" waited=0 out
  while [ "$waited" -lt "$HEALTH_TIMEOUT" ]; do
    sleep "$HEALTH_INTERVAL"
    waited=$((waited + HEALTH_INTERVAL))
    out="$(journalctl -u "$SERVICE" --since "$since" --no-pager -o cat 2>&1 || true)"
    if grep -q "$HEALTH_MARKER" <<<"$out"; then
      return 0
    fi
  done
  return 1
}

show_logs() {
  echo "----- derniers logs du service -----"
  journalctl -u "$SERVICE" --since "$1" --no-pager -n 40 -o cat 2>&1 || true
  echo "------------------------------------"
}

restore_previous() {
  local archive="$STATE_DIR/previous.tar.gz"
  if [ ! -f "$archive" ]; then
    echo "Aucune sauvegarde disponible (premier déploiement ?) : retour arrière impossible." >&2
    return 1
  fi
  find "$APP_DIR" -mindepth 1 -maxdepth 1 \
    ! -name venv ! -name data ! -name .env ! -name .deploy -exec rm -rf {} +
  tar -xzf "$archive" -C "$APP_DIR"
}

since="$(date '+%Y-%m-%d %H:%M:%S')"

if install_deps && restart_service && wait_healthy "$since"; then
  echo "✅ Déploiement réussi : le bot est connecté à Discord."
  exit 0
fi

echo "❌ Le nouveau code n'a pas démarré correctement." >&2
show_logs "$since"

echo "↩️  Retour à la version précédente..."
if restore_previous; then
  since="$(date '+%Y-%m-%d %H:%M:%S')"
  install_deps || echo "Réinstallation des dépendances impossible." >&2
  restart_service || true
  if wait_healthy "$since"; then
    echo "↩️  Ancienne version restaurée et opérationnelle." >&2
  else
    echo "⚠️  Même l'ancienne version ne démarre pas : vérifie le .env et les logs (journalctl -u $SERVICE)." >&2
    show_logs "$since"
  fi
fi
exit 1

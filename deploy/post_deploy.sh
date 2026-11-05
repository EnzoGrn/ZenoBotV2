#!/usr/bin/env bash
# Exécuté SUR LE VPS (envoyé par le workflow via « ssh ... bash -s ») une fois les fichiers copiés.
# 1. reconstruit l'image Docker et recrée le conteneur  2. vérifie que le bot se connecte à Discord
# Si une étape échoue : retour automatique à la version précédente, puis le déploiement est marqué en échec.
set -euo pipefail
umask 002

APP_DIR="${APP_DIR:-/opt/zenobot}"
SERVICE="${SERVICE:-bot}"                # nom du service dans compose.yml
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-90}"   # secondes d'attente maximum pour la connexion à Discord
HEALTH_INTERVAL="${HEALTH_INTERVAL:-2}"
STATE_DIR="$APP_DIR/.deploy"
HEALTH_MARKER="Connecté en tant que"     # ligne écrite par bot.py quand la connexion Discord est établie

cd "$APP_DIR"

if [ ! -f .env ]; then
  echo "Fichier .env introuvable dans $APP_DIR : suis l'étape « configuration » du README." >&2
  exit 1
fi

start_stack() {
  # --force-recreate : le bot est toujours redémarré, même si l'image n'a pas changé
  docker compose up -d --build --force-recreate --remove-orphans
}

timestamp() { date -u '+%Y-%m-%dT%H:%M:%SZ'; }

wait_healthy() {
  local since="$1" waited=0 out
  while [ "$waited" -lt "$HEALTH_TIMEOUT" ]; do
    sleep "$HEALTH_INTERVAL"
    waited=$((waited + HEALTH_INTERVAL))
    out="$(docker compose logs --no-color --since "$since" "$SERVICE" 2>&1 || true)"
    if grep -q "$HEALTH_MARKER" <<<"$out"; then
      return 0
    fi
  done
  return 1
}

show_logs() {
  echo "----- derniers logs du bot -----"
  docker compose logs --no-color --since "$1" --tail 40 "$SERVICE" 2>&1 || true
  echo "--------------------------------"
}

restore_previous() {
  local archive="$STATE_DIR/previous.tar.gz"
  if [ ! -f "$archive" ]; then
    echo "Aucune sauvegarde disponible (premier déploiement ?) : retour arrière impossible." >&2
    return 1
  fi
  find "$APP_DIR" -mindepth 1 -maxdepth 1 \
    ! -name data ! -name .env ! -name .deploy -exec rm -rf {} +
  tar -xzf "$archive" -C "$APP_DIR"
}

since="$(timestamp)"

if start_stack && wait_healthy "$since"; then
  docker image prune -f >/dev/null 2>&1 || true   # supprime les anciennes images devenues inutiles
  echo "✅ Déploiement réussi : le bot est connecté à Discord."
  exit 0
fi

echo "❌ Le nouveau code n'a pas démarré correctement." >&2
show_logs "$since"

echo "↩️  Retour à la version précédente..."
if restore_previous; then
  since="$(timestamp)"
  start_stack || echo "Reconstruction de l'ancienne version impossible." >&2
  if wait_healthy "$since"; then
    echo "↩️  Ancienne version restaurée et opérationnelle." >&2
  else
    echo "⚠️  Même l'ancienne version ne démarre pas : vérifie le .env et les logs (docker compose logs)." >&2
    show_logs "$since"
  fi
fi
exit 1

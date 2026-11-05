#!/usr/bin/env bash
# Teste deploy/backup.sh et deploy/post_deploy.sh sans VPS : la commande « docker » est simulée.
# Scénarios : déploiement réussi, nouvelle version cassée (retour arrière), premier déploiement cassé.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

PASS=0
check() { # check "description" commande...
  local desc="$1"; shift
  if "$@"; then PASS=$((PASS + 1)); echo "  ok  $desc"; else echo "  ÉCHEC  $desc" >&2; exit 1; fi
}

# --- faux docker : « compose up » démarre le bot, qui se connecte sauf si bot.py contient BROKEN
BIN="$WORK/bin"; mkdir -p "$BIN"
cat > "$BIN/docker" <<'EOF'
#!/usr/bin/env bash
echo "$*" >> "$FAKE_STATE/docker.log"
[ "$1" = compose ] || exit 0
shift
case "$1" in
  up)
    if grep -q BROKEN "$APP_DIR/bot.py"; then
      echo "bot  | Traceback (most recent call last): BROKEN" > "$FAKE_STATE/logs"
    else
      echo "bot  | 2026-11-05 12:00:00 INFO zenobot: Connecté en tant que ZenoBot#0001 sur « Serveur » (12 membres)." > "$FAKE_STATE/logs"
    fi ;;
  logs) cat "$FAKE_STATE/logs" 2>/dev/null || true ;;
esac
EOF
chmod +x "$BIN/docker"

new_env() { # prépare un « VPS » avec une version en production
  APP_DIR="$WORK/$1/app"; FAKE_STATE="$WORK/$1/state"
  mkdir -p "$APP_DIR/data" "$APP_DIR/cogs" "$FAKE_STATE"
  echo "TOKEN=secret" > "$APP_DIR/.env"
  echo "base-de-donnees" > "$APP_DIR/data/bot.db"
  export APP_DIR FAKE_STATE
}
run() { # run script -> exécute avec l'environnement simulé
  PATH="$BIN:$PATH" HEALTH_TIMEOUT=4 HEALTH_INTERVAL=1 bash "$ROOT/deploy/$1"
}
publish() { # publish "contenu de bot.py" : simule l'envoi du nouveau code par rsync
  echo "$1" > "$APP_DIR/bot.py"
  echo "$1" > "$APP_DIR/cogs/module.py"
  echo "FROM scratch  # $1" > "$APP_DIR/Dockerfile"
}
ups() { grep -c '^compose up' "$FAKE_STATE/docker.log"; }

echo "1. Déploiement réussi"
new_env ok
publish "version 1"
run backup.sh >/dev/null
publish "version 2"
run post_deploy.sh >"$WORK/ok.out" 2>&1
check "message de réussite affiché" grep -q "Déploiement réussi" "$WORK/ok.out"
check "image reconstruite et conteneur recréé" grep -q "compose up -d --build --force-recreate" "$FAKE_STATE/docker.log"
check "un seul démarrage" test "$(ups)" -eq 1
check "la version 2 reste en place" grep -q "version 2" "$APP_DIR/bot.py"

echo "2. Nouvelle version cassée : retour arrière"
new_env bad
publish "version 1"
run backup.sh >/dev/null
publish "version 2 BROKEN"
echo "fichier ajouté par la version cassée" > "$APP_DIR/cogs/nouveau.py"
if run post_deploy.sh >"$WORK/bad.out" 2>&1; then rc=0; else rc=$?; fi
check "le déploiement est marqué en échec (code 1)" test "$rc" -eq 1
check "ancien bot.py restauré" grep -qx "version 1" "$APP_DIR/bot.py"
check "ancien Dockerfile restauré" grep -q "version 1" "$APP_DIR/Dockerfile"
check "fichier de la version cassée supprimé" test ! -e "$APP_DIR/cogs/nouveau.py"
check ".env intact" grep -qx "TOKEN=secret" "$APP_DIR/.env"
check "base de données intacte" grep -qx "base-de-donnees" "$APP_DIR/data/bot.db"
check "deux démarrages (nouvelle puis ancienne version)" test "$(ups)" -eq 2
check "ancienne version confirmée opérationnelle" grep -q "Ancienne version restaurée" "$WORK/bad.out"
check "les logs de l'échec sont affichés" grep -q "Traceback" "$WORK/bad.out"

echo "3. Premier déploiement cassé (aucune sauvegarde)"
new_env first
run backup.sh >"$WORK/first-backup.out"
check "pas de sauvegarde au premier déploiement" test ! -e "$APP_DIR/.deploy/previous.tar.gz"
publish "version 1 BROKEN"
if run post_deploy.sh >"$WORK/first.out" 2>&1; then rc=0; else rc=$?; fi
check "échec signalé" test "$rc" -eq 1
check "message explicite sur l'absence de sauvegarde" grep -q "retour arrière impossible" "$WORK/first.out"
check ".env intact" grep -qx "TOKEN=secret" "$APP_DIR/.env"

echo "4. Sans fichier .env"
new_env noenv
publish "version 1"
rm "$APP_DIR/.env"
if run post_deploy.sh >"$WORK/noenv.out" 2>&1; then rc=0; else rc=$?; fi
check "refus clair, rien n'est démarré" bash -c '[ "$1" -eq 1 ] && grep -q "\.env introuvable" "$2" && [ ! -e "$3/docker.log" ]' _ "$rc" "$WORK/noenv.out" "$FAKE_STATE"

echo "5. Sauvegarde : contenu"
new_env tar
publish "version 1"
run backup.sh >/dev/null
listing="$(tar -tzf "$APP_DIR/.deploy/previous.tar.gz")"
check "contient le code et le Dockerfile" bash -c 'grep -q "bot.py" <<<"$1" && grep -q "Dockerfile" <<<"$1"' _ "$listing"
check "ne contient ni .env, ni data" bash -c '! grep -qE "(\.env|data/)" <<<"$1"' _ "$listing"

echo
echo "TOUT EST OK ($PASS vérifications)"

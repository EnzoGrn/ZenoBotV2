#!/usr/bin/env bash
# Teste deploy/backup.sh et deploy/post_deploy.sh sans VPS : systemctl et journalctl sont simulés.
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

# --- faux systemctl / journalctl : le service « démarre » si bot.py ne contient pas BROKEN
BIN="$WORK/bin"; mkdir -p "$BIN"
cat > "$BIN/systemctl" <<'EOF'
#!/usr/bin/env bash
echo "$*" >> "$FAKE_STATE/systemctl.log"
if [ "$1" = restart ]; then
  if grep -q BROKEN "$APP_DIR/bot.py"; then
    echo "Traceback (most recent call last): BROKEN" > "$FAKE_STATE/journal"
  else
    echo "2026-11-05 12:00:00 INFO zenobot: Connecté en tant que ZenoBot#0001 sur « Serveur » (12 membres)." > "$FAKE_STATE/journal"
  fi
fi
EOF
cat > "$BIN/journalctl" <<'EOF'
#!/usr/bin/env bash
cat "$FAKE_STATE/journal" 2>/dev/null || true
EOF
chmod +x "$BIN/systemctl" "$BIN/journalctl"

new_env() { # prépare un « VPS » avec une version en production
  APP_DIR="$WORK/$1/app"; FAKE_STATE="$WORK/$1/state"
  mkdir -p "$APP_DIR/venv/bin" "$APP_DIR/data" "$APP_DIR/cogs" "$FAKE_STATE"
  printf '#!/usr/bin/env bash\necho "pip $*" >> "%s/pip.log"\n' "$FAKE_STATE" > "$APP_DIR/venv/bin/pip"
  chmod +x "$APP_DIR/venv/bin/pip"
  echo "TOKEN=secret" > "$APP_DIR/.env"
  echo "base-de-donnees" > "$APP_DIR/data/bot.db"
  echo "discord.py" > "$APP_DIR/requirements.txt"
  export APP_DIR FAKE_STATE
}
run() { # run script -> exécute avec l'environnement simulé
  PATH="$BIN:$PATH" SUDO="" HEALTH_TIMEOUT=4 HEALTH_INTERVAL=1 bash "$ROOT/deploy/$1"
}
publish() { # publish "contenu de bot.py" : simule l'envoi du nouveau code par rsync
  echo "$1" > "$APP_DIR/bot.py"
  echo "$1" > "$APP_DIR/cogs/module.py"
}

echo "1. Déploiement réussi"
new_env ok
publish "version 1"
run backup.sh >/dev/null
publish "version 2"
run post_deploy.sh >"$WORK/ok.out" 2>&1
check "message de réussite affiché" grep -q "Déploiement réussi" "$WORK/ok.out"
check "dépendances installées" grep -q "pip install" "$FAKE_STATE/pip.log"
check "service redémarré une fois" test "$(grep -c '^restart' "$FAKE_STATE/systemctl.log")" -eq 1
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
check "ancien module restauré" grep -qx "version 1" "$APP_DIR/cogs/module.py"
check "fichier de la version cassée supprimé" test ! -e "$APP_DIR/cogs/nouveau.py"
check ".env intact" grep -qx "TOKEN=secret" "$APP_DIR/.env"
check "base de données intacte" grep -qx "base-de-donnees" "$APP_DIR/data/bot.db"
check "service redémarré deux fois (nouvelle puis ancienne version)" test "$(grep -c '^restart' "$FAKE_STATE/systemctl.log")" -eq 2
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

echo "4. Sauvegarde : contenu"
new_env tar
publish "version 1"
mkdir -p "$APP_DIR/venv/lib" && echo x > "$APP_DIR/venv/lib/gros-fichier"
run backup.sh >/dev/null
listing="$(tar -tzf "$APP_DIR/.deploy/previous.tar.gz")"
check "contient le code" grep -q "bot.py" <<<"$listing"
check "ne contient ni .env, ni data, ni venv" bash -c '! grep -qE "(\.env|data/|venv)" <<<"$1"' _ "$listing"

echo
echo "TOUT EST OK ($PASS vérifications)"

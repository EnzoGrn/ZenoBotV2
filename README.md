# ZenoBot

Bot Discord de la communauté ZenoSurTwitch_. Python 3.12, discord.py, SQLite, déployé en conteneur Docker. Configuration à chaud via les commandes `/config`.

## Fonctionnalités

| Fonction | Détail |
|---|---|
| Annonces de live | Embed (titre, jeu, miniature) et ping du rôle « Notif live » au lancement du stream. Mis à jour si le titre ou le jeu change, transformé en récapitulatif (durée, jeux) à la fin. Une reprise en moins de 10 min réutilise l'annonce, sans nouveau ping. |
| Accueil | Message de bienvenue personnalisable, rôle d'arrivée. Attend la validation des règles si le serveur en a une. |
| Rôles à boutons | Message avec boutons pour prendre ou retirer des rôles. Boutons persistants. |
| Modération | Filtre de liens par liste blanche, blocage des invitations, anti-spam, avertissements, exclusions, kick, ban, purge, salon de logs. |
| Niveaux | XP au message (avec cooldown), classement, rôles débloqués par niveau. |
| Communauté | Sondages natifs, idées avec votes et fil de discussion, annonces de recherche de joueurs. |

## Commandes

Droits par défaut, modifiables dans *Paramètres du serveur → Intégrations*.

| Commande | Droit requis | Rôle |
|---|---|---|
| `/config salon` `role` `niveau-role` `message-bienvenue` `filtre-liens` `domaine` | Gérer le serveur | Réglages |
| `/config afficher` | Gérer le serveur | Configuration actuelle |
| `/config verifier` | Gérer le serveur | Diagnostic : permissions, rôles, salons |
| `/twitch statut` `test` | Gérer le serveur | État de la connexion Twitch, aperçu d'annonce |
| `/roles ajouter` `retirer` `liste` `panel` | Gérer les rôles | Rôles à boutons |
| `/warn` `warns` `unwarn` `clearwarns` | Exclure des membres | Avertissements |
| `/timeout` `untimeout` | Exclure des membres | Exclusion temporaire (`10m`, `2h`, `1j`, `1h30m`) |
| `/kick` `ban` `unban` | Expulser / Bannir | Sanctions |
| `/purge` | Gérer les messages | Suppression de messages récents |
| `/niveau` `top` | Tous | Niveau, classement |
| `/xp ajouter` `reset` | Gérer le serveur | Correction d'XP |
| `/sondage` | Gérer les messages | Sondage (2 à 5 choix) |
| `/idee` | Tous | Proposer une idée |
| `/idee-statut` | Gérer les messages | Statut d'une idée |
| `/cherche-joueurs` | Tous | Annonce de recherche de partenaires |

## Installation

Prérequis : VPS Ubuntu avec accès `sudo`, serveur Discord dont on est administrateur, compte Twitch avec double authentification.

### 1. Application Discord

1. <https://discord.com/developers/applications> → **New Application**. Noter l'**Application ID**.
2. Onglet **Bot** : activer **Server Members Intent** et **Message Content Intent** (sans eux, le bot ne démarre pas), puis **Reset Token** et copier le token (`DISCORD_TOKEN`).
3. Inviter le bot (remplacer `APPLICATION_ID`) :
   ```
   https://discord.com/oauth2/authorize?client_id=APPLICATION_ID&scope=bot+applications.commands&permissions=564358971223110
   ```
4. Dans *Paramètres du serveur → Rôles*, placer le rôle du bot au-dessus des rôles qu'il attribue (arrivée, Habitué, niveaux, rôles à boutons) et en dessous des rôles de modération.
5. Mode développeur (*Paramètres Discord → Avancés*), clic droit sur le serveur → **Copier l'identifiant** (`GUILD_ID`).

### 2. Application Twitch

1. <https://dev.twitch.tv/console/apps> → **Register Your Application**.
2. Nom unique, *OAuth Redirect URL* `https://localhost` (non utilisée), catégorie *Application Integration*, type *Confidential*.
3. **Manage** → copier le **Client ID** (`TWITCH_CLIENT_ID`), puis **New Secret** (`TWITCH_CLIENT_SECRET`, affiché une seule fois).

Le bot n'utilise que les données publiques de la chaîne (état du live) : aucune connexion au compte Twitch.

### 3. Docker

Méthode de la [documentation officielle](https://docs.docker.com/engine/install/ubuntu/) pour Ubuntu :

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}") stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo docker run --rm hello-world
```

### 4. Projet sur le VPS

Le projet doit être dans `/opt/zenobot`. Depuis une copie dans `~/zenobot` :

```bash
sudo mkdir -p /opt/zenobot
sudo cp -r ~/zenobot/. /opt/zenobot/
sudo rm -rf /opt/zenobot/.git /opt/zenobot/.github /opt/zenobot/tests
sudo bash /opt/zenobot/deploy/setup-server.sh
```

`setup-server.sh` (relançable sans risque) :
- crée l'utilisateur `deploy`, membre du groupe `docker`, propriétaire de `/opt/zenobot` ;
- réserve `/opt/zenobot/data` à l'uid 10001 (utilisateur du conteneur) ;
- installe rsync si absent ;
- accepte en argument la clé publique SSH de GitHub Actions (voir plus bas).

### 5. Configuration

```bash
cd /tmp
sudo -u deploy cp /opt/zenobot/.env.example /opt/zenobot/.env
sudo -u deploy nano /opt/zenobot/.env
sudo chmod 600 /opt/zenobot/.env
```

Variables obligatoires : `DISCORD_TOKEN`, `GUILD_ID`. Sans `TWITCH_CLIENT_ID` et `TWITCH_CLIENT_SECRET`, les annonces de live sont désactivées. Le `.env` n'est jamais copié dans l'image : le conteneur le lit au démarrage.

### 6. Lancement

```bash
cd /tmp
sudo -u deploy bash -c 'cd /opt/zenobot && docker compose up -d --build'
sudo docker logs -f zenobot
```

Les logs doivent contenir `commandes synchronisées` puis `Connecté en tant que`. Le conteneur redémarre automatiquement après un plantage ou un reboot du VPS.

### 7. Réglages dans Discord

1. `/config salon` : *Annonces de live*, puis facultativement *Bienvenue*, *Logs*, *Idées*, *Recherche de joueurs*, *Montées de niveau*.
2. Rendre le rôle « Notif live » mentionnable (*Rôles → ce rôle → Autoriser tout le monde à mentionner ce rôle*), puis `/config role` → *Notif live*.
3. `/config role` : *Rôle d'arrivée* et *Habitué* (autorisé à poster des liens).
4. Rôles à boutons : `/roles ajouter` pour chaque rôle, puis `/roles panel`. Après une modification de la liste, supprimer l'ancien message et republier le panneau.
5. Récompenses de niveau : `/config niveau-role niveau:5 role:@Habitué`.
6. `/twitch test` (aperçu sans ping, supprimé après 60 s) et `/twitch statut`.
7. `/config verifier` liste les problèmes restants. À relancer après chaque changement important.

L'intégration Twitch native de Discord (*Intégrations → Twitch*) attribue déjà un rôle aux abonnés : le bot ne s'en charge pas.

## Comportement

- **Live** : interrogation de Twitch toutes les minutes (annonce 1 à 2 min après le début). Fin de live confirmée après 3 vérifications hors-ligne consécutives.
- **Staff exempté** : les membres avec *Gérer les messages* (ou administrateur) échappent au filtre de liens et à l'anti-spam.
- **Filtre de liens** : actif par défaut. Autorisés : twitch.tv, youtube.com, youtu.be, tenor.com, giphy.com, imgur.com, medal.tv, streamable.com, discordapp.com/.net, x.com, twitter.com et leurs sous-domaines. Le rôle *Habitué* peut poster n'importe quel lien. Les invitations vers d'autres serveurs sont toujours bloquées. `/config domaine` modifie la liste, `/config filtre-liens actif:False` désactive le filtre.
- **Anti-spam** : plus de 7 messages en 8 s, 3 messages identiques en 30 s ou 6 mentions dans un message → suppression et exclusion de 5 min.
- **Avertissements** : exclusion automatique d'1 h tous les 3 avertissements. Le membre sanctionné reçoit un message privé si ses MP sont ouverts.
- **XP** : 15 à 25 XP par message, au plus un par minute, 5 caractères minimum. Un message supprimé par la modération ne rapporte rien.
- **Recherche de joueurs** : une annonce ouverte par personne, clôturée après 6 h. Les places excluent l'organisateur.
- **Serveur unique** : le bot ne fonctionne que sur `GUILD_ID` et quitte tout autre serveur.

Les seuils (anti-spam, XP, délais Twitch) sont dans `config.py`. Une modification demande de reconstruire l'image.

## Déploiement automatique (GitHub Actions)

Un push sur `main` déclenche :
1. les tests (syntaxe, bot, scripts de déploiement), la validation de `compose.yml`, la construction de l'image et un démarrage de contrôle avec les restrictions de production ;
2. la sauvegarde du code en production ;
3. l'envoi du code par rsync (`.env` et `data/` jamais touchés) ;
4. `docker compose up -d --build --force-recreate`, puis l'attente (90 s max) de `Connecté en tant que` dans les logs. En cas d'échec : restauration de la version précédente, reconstruction, et job en échec.

Les pull requests et les autres branches ne sont que testées.

### Mise en place

1. **Dépôt GitHub privé**, branche `main`. Vérifier que `.env` n'est jamais commité.
2. **Clé SSH dédiée**, sur le poste local :
   ```bash
   ssh-keygen -t ed25519 -C "github-actions-zenobot" -f zenobot_deploy
   ```
   Laisser la phrase secrète vide (Entrée deux fois). Produit `zenobot_deploy` (privée) et `zenobot_deploy.pub` (publique).
3. **Clé publique sur le VPS** :
   ```bash
   sudo bash /opt/zenobot/deploy/setup-server.sh "ssh-ed25519 AAAA... github-actions-zenobot"
   ```
   Test depuis le poste local : `ssh -i zenobot_deploy deploy@ADRESSE_DU_VPS "docker ps"` doit lister le conteneur `zenobot`.
4. **Empreinte du serveur**, à lire sur le VPS :
   ```bash
   echo "ADRESSE_DU_VPS $(cut -d' ' -f1,2 /etc/ssh/ssh_host_ed25519_key.pub)"
   ```
   Utiliser la même adresse que `VPS_HOST`. Pour un port autre que 22 : `[ADRESSE_DU_VPS]:PORT`.
5. **Secrets** (*Settings → Secrets and variables → Actions*) :

   | Secret | Valeur |
   |---|---|
   | `VPS_HOST` | adresse ou IP du VPS |
   | `VPS_USER` | `deploy` |
   | `VPS_PORT` | port SSH (vide pour 22) |
   | `VPS_SSH_KEY` | contenu complet de `zenobot_deploy`, lignes `BEGIN` et `END` comprises |
   | `VPS_HOST_KEY` | la ligne de l'étape 4 |

6. **Environnement** `production` (*Settings → Environments*). *Required reviewers* permet de valider chaque déploiement.
7. Supprimer `zenobot_deploy` du poste local, puis pousser sur `main` ou lancer *Actions → Run workflow*.

### Limites

- Le retour arrière restaure le code, pas les données : base et réglages `/config` ne sont pas annulés.
- Sans version précédente sur le VPS (dossier vide), un premier déploiement en échec n'a rien à restaurer.
- `deploy` pilote Docker, ce qui équivaut à root sur le VPS : toute personne pouvant pousser sur `main` exécute du code sur le serveur. Dépôt privé, double authentification GitHub, branche `main` protégée.
- Le dossier cible est `/opt/zenobot` (variable `APP_DIR` du workflow).

## Exploitation

```bash
sudo docker logs -f zenobot --tail 200          # logs (rotation à 30 Mo)
sudo docker restart zenobot                      # redémarrage
sudo docker stop zenobot                         # arrêt
```

`docker restart` ne relit pas le `.env`. Après l'avoir modifié :

```bash
sudo -u deploy bash -c 'cd /opt/zenobot && docker compose up -d --force-recreate'
```

**Mise à jour manuelle** : remplacer les fichiers de `/opt/zenobot` (sauf `.env` et `data/`), puis `sudo -u deploy bash -c 'cd /opt/zenobot && docker compose up -d --build'`.

**Sauvegarde de la base** (à chaud) :

```bash
sudo docker exec zenobot python -c "import sqlite3; s=sqlite3.connect('/app/data/bot.db'); d=sqlite3.connect('/app/data/backup.db'); s.backup(d); d.close()"
sudo cp /opt/zenobot/data/backup.db ~/zenobot-backup-$(date +%F).db
```

**Token compromis** : *Bot → Reset Token* sur le portail Discord, mise à jour du `.env`, recréation du conteneur.

## Migration vers un autre serveur

Le dépôt contient tout, sauf `.env` et `data/`.

1. Ancien serveur : arrêter le bot (deux instances avec le même token dupliquent les messages) et archiver :
   ```bash
   sudo docker stop zenobot
   sudo tar czf /tmp/zenobot-migration.tar.gz -C /opt/zenobot data .env
   ```
   L'archive contient les secrets : la transférer par `scp`, puis la supprimer.
2. Nouveau serveur : installer Docker (étape 3), copier `deploy/setup-server.sh`, puis :
   ```bash
   sudo mkdir -p /opt/zenobot
   sudo bash setup-server.sh "ssh-ed25519 AAAA... github-actions-zenobot"
   ```
3. Restaurer l'archive :
   ```bash
   sudo tar xzf zenobot-migration.tar.gz -C /opt/zenobot
   sudo chown -R 10001:10001 /opt/zenobot/data
   sudo chown deploy:deploy /opt/zenobot/.env && sudo chmod 600 /opt/zenobot/.env
   ```
4. GitHub : mettre à jour `VPS_HOST` et `VPS_HOST_KEY`, puis lancer *Actions → Run workflow*.

## Dépannage

| Symptôme | Cause et correction |
|---|---|
| `PrivilegedIntentsRequired` | Activer *Server Members Intent* et *Message Content Intent* (étape 1.2). |
| Commandes `/` absentes | Vérifier `GUILD_ID` et le scope `applications.commands` de l'invitation, recréer le conteneur, recharger Discord (`Ctrl+R`). |
| Pas d'annonce de live | `/twitch statut` puis `/config verifier` : salon non défini ou permissions manquantes dans ce salon. |
| Annonce sans notification | Rôle « Notif live » non mentionnable (étape 7.2). |
| Rôle non attribué | Le rôle du bot doit être au-dessus du rôle visé (étape 1.4). |
| `Authentification Twitch refusée` | `TWITCH_CLIENT_ID` ou `TWITCH_CLIENT_SECRET` incorrect. |
| `Chaîne Twitch introuvable` | `TWITCH_CHANNEL` incorrect. |
| `permission denied` sur `/app/data`, `unable to open database file` | `sudo chown -R 10001:10001 /opt/zenobot/data` |
| `permission denied ... docker.sock` | Utilisateur hors du groupe `docker` : préfixer par `sudo` ou passer par `sudo -u deploy`. |
| Conteneur en redémarrage permanent | `sudo docker logs --tail 50 zenobot` : token invalide, intents désactivés, `.env` incomplet. |
| Lien légitime supprimé | `/config domaine`, ou rôle *Habitué* pour le membre. |

## Structure

```
bot.py              point d'entrée
config.py           lecture du .env, seuils réglables
database.py         base SQLite
utils.py            fonctions communes
cogs/               une fonctionnalité par fichier
tests/              tests automatiques (python tests/test_bot.py, bash tests/test_deploy.sh)
deploy/             scripts de déploiement (préparation du VPS, sauvegarde, reconstruction, retour arrière)
.github/workflows/  CI/CD
Dockerfile          image du bot
compose.yml         conteneur (volume, redémarrage, durcissement)
.dockerignore       exclusions de l'image
.env.example        modèle de configuration
```

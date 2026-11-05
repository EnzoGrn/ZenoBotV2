# Bot Discord — ZenoSurTwitch_

Un bot Discord pour ta communauté de streamer : il annonce tes lives Twitch, partage tes clips, accueille les nouveaux, gère les rôles, les niveaux et la modération. Il est écrit en Python (discord.py), tourne dans un conteneur Docker sur ton VPS et se règle entièrement depuis Discord avec des commandes `/config`.

## Ce qu'il fait

| Fonction | Détail |
|---|---|
| **Annonces de live** | Dès que tu lances ton stream sur Twitch, un message avec le titre, le jeu et la miniature est publié dans ton salon d'annonces, avec un ping du rôle « Notif live ». Le message se met à jour si tu changes de titre ou de jeu. À la fin du live, il devient un récapitulatif (durée, jeux joués) et le ping disparaît. Si ton stream plante et reprend en moins de 10 minutes, la même annonce est réutilisée : pas de double ping. |
| **Clips** | Les nouveaux clips de ta chaîne sont reposés dans un salon dédié avec les réactions 🔥 et 😂. |
| **Accueil** | Message de bienvenue personnalisable + rôle donné automatiquement à l'arrivée (attend la validation des règles si ton serveur a un écran de règles). |
| **Rôles à boutons** | Un message avec des boutons : un clic pour prendre ou retirer un rôle (Notif live, jeux...). |
| **Modération** | Filtre de liens (liste de sites autorisés), blocage des invitations vers d'autres serveurs, anti-spam, avertissements, exclusions, expulsions, bannissements, purge de messages, journal dans un salon de logs. |
| **Niveaux** | XP gagnée en discutant (anti-abus), `/niveau`, `/top`, rôles offerts à certains niveaux. |
| **Communauté** | `/sondage` (sondages Discord natifs), `/idee` (idées avec votes et fil de discussion), `/cherche-joueurs` (annonce avec boutons Rejoindre / Quitter / Clôturer). |

Volontairement absents : commande de planning (tu postes ton planning toi-même le lundi dans ton salon), giveaways, fonctions « pédagogue ».

## Les commandes

Les droits indiqués sont ceux par défaut. Un admin peut les modifier dans *Paramètres du serveur → Intégrations → ton bot*.

| Commande | Qui peut l'utiliser | À quoi ça sert |
|---|---|---|
| `/config salon` / `role` / `niveau-role` / `message-bienvenue` / `filtre-liens` / `domaine` | Gérer le serveur | Régler le bot |
| `/config afficher` | Gérer le serveur | Voir la configuration actuelle |
| `/config verifier` | Gérer le serveur | Diagnostic : permissions, rôles, salons |
| `/twitch statut` / `test` | Gérer le serveur | Vérifier la connexion Twitch / voir un aperçu d'annonce |
| `/roles ajouter` / `retirer` / `liste` / `panel` | Gérer les rôles | Gérer les rôles à boutons |
| `/warn`, `/warns`, `/unwarn`, `/clearwarns` | Exclure des membres | Avertissements |
| `/timeout`, `/untimeout` | Exclure des membres | Exclusion temporaire (`10m`, `2h`, `1j`, `1h30m`) |
| `/kick`, `/ban`, `/unban` | Expulser / Bannir | Sanctions |
| `/purge` | Gérer les messages | Supprimer des messages récents |
| `/niveau`, `/top` | Tout le monde | Niveau et classement |
| `/xp ajouter` / `reset` | Gérer le serveur | Corriger l'XP d'un membre |
| `/sondage` | Gérer les messages | Créer un sondage (ouvrable à tous dans les Intégrations) |
| `/idee` | Tout le monde | Proposer une idée |
| `/idee-statut` | Gérer les messages | Marquer une idée (acceptée, refusée...) |
| `/cherche-joueurs` | Tout le monde | Trouver des partenaires de jeu |

## Installation

Compte environ 30 minutes. Il te faut un VPS Linux (Ubuntu dans les exemples) avec un accès `sudo`. Python n'est pas à installer : il est inclus dans l'image Docker.

### 1. Créer le bot sur Discord

1. Va sur <https://discord.com/developers/applications> → **New Application** → donne-lui un nom.
2. Note l'**Application ID** (page *General Information*) : c'est ton `client_id` pour l'invitation.
3. Menu **Bot** :
   - Dans *Privileged Gateway Intents*, active **Server Members Intent** et **Message Content Intent** (obligatoire, sinon le bot refuse de démarrer).
   - Clique sur **Reset Token**, copie le token. **Ne le partage avec personne** (pas même avec moi) : il donne le contrôle total du bot.
4. Invite le bot sur ton serveur avec ce lien (remplace `TON_APPLICATION_ID`) :

   ```
   https://discord.com/oauth2/authorize?client_id=TON_APPLICATION_ID&scope=bot+applications.commands&permissions=564358971223110
   ```

5. Dans *Paramètres du serveur → Rôles*, **glisse le rôle du bot au-dessus** des rôles qu'il doit attribuer (rôle d'arrivée, Habitué, rôles de niveau, rôles à boutons) et **en dessous** de tes rôles de modération.
6. Active le mode développeur (*Paramètres Discord → Avancés*), puis clic droit sur ton serveur → **Copier l'identifiant** : c'est ton `GUILD_ID`.

### 2. Créer l'application Twitch

1. Va sur <https://dev.twitch.tv/console/apps> (Twitch demande que la double authentification soit activée sur ton compte).
2. **Register Your Application** : un nom unique, *OAuth Redirect URL* : `https://localhost` (Twitch exige du HTTPS ; le bot ne s'en sert pas), catégorie *Application Integration*, type de client *Confidential*.
3. Ouvre l'application créée (**Manage**) : copie le **Client ID** et génère un **New Secret** (à copier tout de suite, il ne s'affiche qu'une fois).

Le bot n'accède qu'aux informations publiques de ta chaîne (live, clips). Il ne se connecte pas à ton compte Twitch.

### 3. Installer Docker sur le VPS

Le bot tourne dans un conteneur Docker. Sur Ubuntu, voici la méthode de la [documentation officielle](https://docs.docker.com/engine/install/ubuntu/) (pour un autre système, suis la doc) :

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}") stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo docker run --rm hello-world        # doit afficher « Hello from Docker! »
```

### 4. Poser le projet sur le VPS

Le projet doit se trouver dans `/opt/zenobot`. Si tu l'as envoyé dans ton dossier personnel (par exemple `~/zenobot`) :

```bash
sudo mkdir -p /opt/zenobot
sudo cp -r ~/zenobot/. /opt/zenobot/
sudo rm -rf /opt/zenobot/.git /opt/zenobot/.github /opt/zenobot/tests
sudo bash /opt/zenobot/deploy/setup-server.sh
```

Le script `setup-server.sh` crée l'utilisateur `deploy` (celui qui pilote Docker et qui sera utilisé par GitHub Actions), lui donne le dossier du bot, et prépare `data/`, le dossier de la base de données, pour l'utilisateur du conteneur. Tu peux le relancer sans risque.

### 5. Remplir la configuration

```bash
cd /tmp
sudo -u deploy cp /opt/zenobot/.env.example /opt/zenobot/.env
sudo -u deploy nano /opt/zenobot/.env        # colle DISCORD_TOKEN, GUILD_ID, TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET
sudo chmod 600 /opt/zenobot/.env
```

Le fichier `.env` reste sur ton VPS : ne l'envoie à personne et ne le mets jamais sur GitHub. Il n'est pas copié dans l'image Docker : il est lu au démarrage du conteneur.

### 6. Premier lancement

```bash
cd /tmp
sudo -u deploy bash -c 'cd /opt/zenobot && docker compose up -d --build'
sudo docker logs -f zenobot              # Ctrl+C pour quitter l'affichage (le bot continue de tourner)
```

La première construction de l'image prend une à deux minutes. Tu dois voir *« commandes synchronisées »* puis *« Connecté en tant que... »*, et le bot passe en ligne sur ton serveur. Le conteneur redémarre tout seul s'il plante ou si le VPS redémarre.

### 7. Régler le bot depuis Discord

Dans cet ordre :

1. `/config salon` → choisis la fonction *Annonces de live* et ton salon d'annonces. Idem pour *Clips*, *Bienvenue*, *Logs*, *Idées*, *Recherche de joueurs*, *Montées de niveau* (tous facultatifs).
2. Rends ton rôle « Notif live » **mentionnable** (*Paramètres → Rôles → ce rôle → Autoriser tout le monde à mentionner ce rôle*), puis `/config role` → *Notif live*.
3. `/config role` → *Rôle d'arrivée* (rôle donné aux nouveaux) et *Habitué* (a le droit de poster des liens).
4. Rôles à boutons : `/roles ajouter` pour chaque rôle (ex. Notif live 🔔, tes jeux), puis `/roles panel` pour publier le message avec les boutons. Quand tu modifies la liste, supprime l'ancien message et republie le panneau.
5. Récompenses de niveau (exemple) : `/config niveau-role niveau:5 role:@Habitué`.
6. `/twitch test` : publie un aperçu de l'annonce (sans ping, supprimé au bout de 60 s). `/twitch statut` vérifie la connexion à Twitch.
7. **`/config verifier`** : le bot te liste ce qui reste à corriger. Refais-le après chaque réglage important.

> Astuce : l'intégration Twitch native de Discord (*Paramètres du serveur → Intégrations → Twitch*) donne automatiquement un rôle à tes abonnés. Le bot n'a pas besoin de s'en occuper.

## Comment il se comporte

- **Live** : Twitch est interrogé toutes les minutes (l'annonce peut donc arriver 1 à 2 minutes après le début du stream). Le live n'est considéré comme terminé qu'après 3 minutes hors-ligne, pour absorber les micro-coupures.
- **Clips** : vérifiés toutes les 5 minutes. À l'activation, seuls les clips créés **à partir de ce moment** sont postés (pas de rafale d'anciens clips).
- **Staff exempté** : les membres qui ont la permission *Gérer les messages* (ou admin) ne sont pas touchés par le filtre de liens et l'anti-spam.
- **Filtre de liens** : activé par défaut. Autorisés : twitch.tv, youtube.com, youtu.be, tenor.com, giphy.com, imgur.com, medal.tv, streamable.com, discordapp.com/.net, x.com, twitter.com (et leurs sous-domaines). Les membres avec le rôle *Habitué* peuvent poster n'importe quel lien. Les invitations vers d'autres serveurs sont toujours bloquées (sauf pour le staff). Ajouter un site : `/config domaine action:Ajouter domaine:exemple.com`. Désactiver tout le filtre : `/config filtre-liens actif:False`.
- **Anti-spam** : plus de 7 messages en 8 secondes, 3 messages identiques d'affilée (30 s) ou 6 mentions dans un message → messages supprimés + exclusion de 5 minutes.
- **Avertissements** : tous les 3 avertissements, le membre est exclu automatiquement 1 heure. Les membres sanctionnés reçoivent un message privé (si leurs MP sont ouverts).
- **XP** : 15 à 25 XP par message, au maximum une fois par minute, messages de 5 caractères minimum. Un message supprimé par la modération ne rapporte rien.
- **Recherche de joueurs** : une annonce ouverte par personne à la fois, clôturée automatiquement au bout de 6 heures. Les places comptent les joueurs recherchés, sans l'organisateur.
- **Un seul serveur** : le bot ne fonctionne que sur le serveur `GUILD_ID`. S'il est invité ailleurs, il le quitte.

Les seuils (anti-spam, XP, délais Twitch...) se modifient dans `config.py` ; l'image doit ensuite être reconstruite (un `git push` avec le déploiement automatique, ou la commande de mise à jour à la main plus bas).

## Déploiement automatique (GitHub Actions)

À chaque `git push` sur la branche `main`, GitHub lance les tests puis met à jour ton VPS tout seul. Les pull requests et les autres branches sont seulement testées, jamais déployées.

Déroulement d'un déploiement :
1. **Tests** : syntaxe, tests du bot, tests des scripts de déploiement, vérification de `compose.yml`, construction de l'image Docker et démarrage de contrôle. Si une étape échoue, rien n'est déployé.
2. **Sauvegarde** du code actuellement en production.
3. **Envoi du code** par rsync. Le `.env` et la base `data/` ne sont jamais touchés.
4. **Reconstruction et redémarrage** (`docker compose up -d --build`), puis vérification : le script attend jusqu'à 90 s que le bot se connecte à Discord. S'il n'y arrive pas, il **restaure automatiquement l'ancienne version**, la reconstruit, et marque le déploiement en échec (tu reçois l'e-mail de GitHub). Les logs de l'échec sont affichés dans l'onglet *Actions*.

Mise en place, une seule fois (environ 15 minutes), une fois le bot installé et en ligne (étapes 1 à 6) :

**a. Mets le projet sur GitHub** : un dépôt **privé**, branche `main`. Le fichier `.gitignore` fourni exclut déjà `.env` : vérifie qu'il n'est jamais commité.

**b. Crée une clé SSH dédiée au déploiement**, sur ton ordinateur (elle servira à un robot : pas de phrase secrète) :

```bash
ssh-keygen -t ed25519 -C "github-actions-zenobot" -f zenobot_deploy
```

Quand `ssh-keygen` demande la phrase secrète, appuie deux fois sur Entrée. Tu obtiens `zenobot_deploy` (clé **privée**, secrète) et `zenobot_deploy.pub` (clé publique).

**c. Donne la clé publique au VPS** (le contenu de `zenobot_deploy.pub`, une ligne qui commence par `ssh-ed25519`) :

```bash
sudo bash /opt/zenobot/deploy/setup-server.sh "ssh-ed25519 AAAA... github-actions-zenobot"
```

Teste ensuite depuis ton ordinateur :

```bash
ssh -i zenobot_deploy deploy@ADRESSE_DU_VPS "docker ps"
```

Tu dois voir le conteneur `zenobot` dans la liste.

**d. Récupère l'empreinte du serveur** (elle évite qu'un faux serveur se fasse passer pour le tien) :

```bash
ssh-keyscan -t ed25519 ADRESSE_DU_VPS        # si ton SSH n'est pas sur le port 22 : ssh-keyscan -t ed25519 -p PORT ADRESSE_DU_VPS
```

Garde la ligne obtenue (celle qui ne commence pas par `#`). Pour être sûr qu'elle est bonne, compare avec `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` lancé sur le VPS.

**e. Ajoute les secrets sur GitHub** : *Settings → Secrets and variables → Actions → New repository secret*.

| Secret | Valeur |
|---|---|
| `VPS_HOST` | adresse ou IP du VPS |
| `VPS_USER` | `deploy` |
| `VPS_PORT` | port SSH (laisse vide si c'est 22) |
| `VPS_SSH_KEY` | **contenu entier** du fichier `zenobot_deploy` (clé privée, avec les lignes `BEGIN`/`END`) |
| `VPS_HOST_KEY` | la ligne obtenue à l'étape d |

Crée aussi l'environnement `production` (*Settings → Environments*). Tu peux y activer **Required reviewers** si tu veux valider chaque déploiement à la main.

**f. Supprime la clé privée de ton ordinateur** une fois le secret enregistré (ou garde-la dans un gestionnaire de mots de passe), et ne la partage jamais.

**g. Premier déploiement** : fais un `git push` sur `main`, ou lance *Actions → Tests et déploiement → Run workflow*.

À savoir :
- Le **premier déploiement** automatique n'a pas de version précédente à restaurer si le dossier du VPS était vide : s'il échoue, il faut regarder les logs. Avec le bot installé à la main comme ci-dessus, la sauvegarde existe dès le premier passage.
- Le retour arrière restaure le code, pas les données : il ne défait ni la base de données ni les réglages faits avec `/config`.
- L'utilisateur `deploy` pilote Docker, ce qui équivaut à avoir les droits root sur le VPS. Quiconque peut pousser sur `main` peut donc exécuter n'importe quel code sur ton serveur. Garde le dépôt privé, active la double authentification GitHub et protège la branche `main` (*Settings → Branches*).
- Le dossier du bot est `/opt/zenobot` (variable `APP_DIR` en haut du workflow). Adapte-le dans le workflow si le tien diffère.

## Maintenance

- **Logs** : `sudo docker logs -f zenobot` (ajoute `--tail 200` pour voir les 200 dernières lignes). Ils sont limités à 30 Mo, avec rotation automatique.
- **Redémarrer / arrêter** : `sudo docker restart zenobot` / `sudo docker stop zenobot`. Attention : `restart` ne relit pas le `.env`. Après avoir modifié le `.env`, recrée le conteneur avec `sudo -u deploy bash -c 'cd /opt/zenobot && docker compose up -d --force-recreate'`.
- **Mise à jour** : avec le déploiement automatique, un simple `git push`. À la main : remplace les fichiers du projet dans `/opt/zenobot` (garde `.env` et `data/`), puis `sudo -u deploy bash -c 'cd /opt/zenobot && docker compose up -d --build'`.
- **Sauvegarde de la base** : tout ce que le bot mémorise (réglages, XP, avertissements, rôles à boutons) est dans `/opt/zenobot/data`. Copie à chaud, sans arrêter le bot :
  ```bash
  sudo docker exec zenobot python -c "import sqlite3; s=sqlite3.connect('/app/data/bot.db'); d=sqlite3.connect('/app/data/backup.db'); s.backup(d); d.close()"
  sudo cp /opt/zenobot/data/backup.db ~/zenobot-backup-$(date +%F).db
  ```
- **Token compromis** : régénère-le tout de suite dans le portail Discord (*Bot → Reset Token*), mets-le à jour dans `.env`, puis recrée le conteneur (voir plus haut).

## Déménager sur un autre serveur (environ 10 minutes)

Le bot est entièrement décrit par le dépôt Git, plus deux éléments qui ne sont pas dedans : le fichier `.env` et le dossier `data/`.

1. **Ancien serveur** : arrête le bot (deux bots avec le même token publieraient tout en double), puis emballe la configuration et les données :
   ```bash
   sudo docker stop zenobot
   sudo tar czf /tmp/zenobot-migration.tar.gz -C /opt/zenobot data .env
   ```
   Récupère l'archive sur ton ordinateur (`scp`). Elle contient tes secrets : garde-la privée et supprime-la ensuite.
2. **Nouveau serveur** : installe Docker (étape 3), puis crée le dossier et l'utilisateur de déploiement en donnant la même clé publique qu'avant :
   ```bash
   sudo mkdir -p /opt/zenobot
   sudo bash setup-server.sh "ssh-ed25519 AAAA... github-actions-zenobot"
   ```
   (le script est dans le dépôt : `deploy/setup-server.sh`, à copier sur le serveur avant de le lancer).
3. **Restaurer** l'archive envoyée sur le nouveau serveur :
   ```bash
   sudo tar xzf zenobot-migration.tar.gz -C /opt/zenobot
   sudo chown -R 10001:10001 /opt/zenobot/data
   sudo chown deploy:deploy /opt/zenobot/.env && sudo chmod 600 /opt/zenobot/.env
   ```
4. **GitHub** : mets à jour les secrets `VPS_HOST` et `VPS_HOST_KEY` (nouvelle adresse, nouvelle empreinte), puis lance *Actions → Run workflow*. Le workflow envoie le code, construit l'image et démarre le bot.

## Dépannage

| Problème | Solution |
|---|---|
| Le bot refuse de démarrer : *PrivilegedIntentsRequired* | Active *Server Members Intent* et *Message Content Intent* dans le portail Discord (étape 1.3). |
| Les commandes `/` n'apparaissent pas | Vérifie `GUILD_ID`, que l'invitation contenait `applications.commands`, redémarre le bot puis recharge Discord (`Ctrl+R`). |
| Pas d'annonce de live | `/twitch statut` puis `/config verifier`. Cause fréquente : salon d'annonces non défini ou permissions manquantes dans ce salon. |
| L'annonce n'envoie pas de notification | Le rôle « Notif live » n'est pas mentionnable (étape 7.2). |
| Un rôle n'est pas attribué | Le rôle du bot doit être **au-dessus** de ce rôle dans la liste (étape 1.5). |
| *Authentification Twitch refusée* | Vérifie `TWITCH_CLIENT_ID` et `TWITCH_CLIENT_SECRET` dans `.env`. |
| *Chaîne Twitch introuvable* | Vérifie `TWITCH_CHANNEL` dans `.env`. |
| `permission denied` sur `/app/data` ou *unable to open database file* | Le dossier de données doit appartenir à l'utilisateur du conteneur : `sudo chown -R 10001:10001 /opt/zenobot/data`. |
| *permission denied ... docker.sock* | Ton utilisateur n'est pas dans le groupe `docker`. Préfixe la commande par `sudo`, ou passe par `sudo -u deploy`. |
| Le conteneur redémarre en boucle | `sudo docker logs --tail 50 zenobot` : la cause est dans les dernières lignes (token invalide, intents désactivés, `.env` incomplet...). |
| Un lien légitime est supprimé | Ajoute le site avec `/config domaine`, ou donne le rôle *Habitué* au membre. |

## Structure du projet

```
bot.py              point d'entrée
config.py           lecture du .env + seuils réglables
database.py         base SQLite
utils.py            fonctions communes
cogs/               un fichier par fonctionnalité (admin, twitch, moderation, levels, ...)
tests/              tests automatiques (lancés par la CI : python tests/test_bot.py)
deploy/             scripts de déploiement (préparation du VPS, sauvegarde, reconstruction, retour arrière)
.github/workflows/  CI/CD GitHub Actions
Dockerfile          image Docker du bot
compose.yml         lancement du conteneur (volume, redémarrage, durcissement)
.dockerignore       fichiers exclus de l'image (.env, tests, .git...)
.env.example        modèle de configuration
```

# Bot Discord — ZenoSurTwitch_

Un bot Discord pour ta communauté de streamer : il annonce tes lives Twitch, partage tes clips, accueille les nouveaux, gère les rôles, les niveaux et la modération. Il est écrit en Python (discord.py), tourne sur ton VPS et se règle entièrement depuis Discord avec des commandes `/config`.

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

Compte environ 30 minutes. Il te faut un VPS Linux avec **Python 3.10 ou plus récent**.

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
2. **Register Your Application** : un nom unique, *OAuth Redirect URL* : `http://localhost`, catégorie *Application Integration*, type de client *Confidential*.
3. Ouvre l'application créée (**Manage**) : copie le **Client ID** et génère un **New Secret** (à copier tout de suite, il ne s'affiche qu'une fois).

Le bot n'accède qu'aux informations publiques de ta chaîne (live, clips). Il ne se connecte pas à ton compte Twitch.

### 3. Installer sur le VPS

```bash
# Dépose le dossier du bot sur le VPS (scp, sftp...), par exemple dans /opt/zenobot
sudo useradd --system --create-home --home-dir /opt/zenobot --shell /usr/sbin/nologin zenobot   # si le dossier n'existe pas encore
sudo chown -R zenobot:zenobot /opt/zenobot
cd /opt/zenobot
sudo -u zenobot python3 -m venv venv
sudo -u zenobot venv/bin/pip install -r requirements.txt
sudo -u zenobot mkdir -p data
```

Sur Debian/Ubuntu, si `python3 -m venv` échoue : `sudo apt install python3-venv`.

### 4. Remplir la configuration

```bash
sudo -u zenobot cp .env.example .env
sudo -u zenobot nano .env        # colle DISCORD_TOKEN, GUILD_ID, TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET
sudo chmod 600 /opt/zenobot/.env
```

Le fichier `.env` reste sur ton VPS : ne l'envoie à personne et ne le mets jamais sur GitHub.

### 5. Premier lancement (test)

```bash
cd /opt/zenobot
sudo -u zenobot venv/bin/python bot.py
```

Tu dois voir *« commandes synchronisées »* puis *« Connecté en tant que... »*. Les commandes `/` apparaissent tout de suite sur ton serveur. Arrête avec `Ctrl+C`.

### 6. Le lancer en permanence (systemd)

```bash
sudo cp /opt/zenobot/zenobot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now zenobot
sudo systemctl status zenobot            # doit afficher « active (running) »
sudo journalctl -u zenobot -f            # suivre les logs en direct
```

Si tu n'utilises pas `/opt/zenobot`, adapte les chemins dans `zenobot.service`.

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

Les seuils (anti-spam, XP, délais Twitch...) se modifient dans `config.py` ; redémarre le bot ensuite (`sudo systemctl restart zenobot`).

## Déploiement automatique (GitHub Actions)

À chaque `git push` sur la branche `main`, GitHub lance les tests puis met à jour ton VPS tout seul. Les pull requests et les autres branches sont seulement testées, jamais déployées.

Déroulement d'un déploiement :
1. **Tests** : syntaxe, tests du bot, tests des scripts de déploiement. Si un test échoue, rien n'est déployé.
2. **Sauvegarde** de la version actuellement en production.
3. **Envoi du code** par rsync. Le `.env`, la base `data/` et le `venv` ne sont jamais touchés.
4. **Installation des dépendances, redémarrage, vérification** : le script attend jusqu'à 60 s que le bot se connecte à Discord. S'il n'y arrive pas, il **restaure automatiquement l'ancienne version**, la redémarre, et marque le déploiement en échec (tu reçois l'e-mail de GitHub). Les logs de l'échec sont affichés dans l'onglet *Actions*.

Mise en place, une seule fois (environ 15 minutes) :

**a. Mets le projet sur GitHub** : un dépôt **privé**, branche `main`. Le fichier `.gitignore` fourni exclut déjà `.env` : vérifie qu'il n'est jamais commité.

**b. Crée une clé SSH dédiée au déploiement**, sur ton ordinateur (pas de phrase secrète, elle servira à un robot) :

```bash
ssh-keygen -t ed25519 -N "" -C "github-actions-zenobot" -f zenobot_deploy
```

Tu obtiens `zenobot_deploy` (clé **privée**, secrète) et `zenobot_deploy.pub` (clé publique).

**c. Prépare le VPS** : copie `deploy/setup-server.sh` sur le VPS et lance-le avec la clé **publique** :

```bash
sudo bash setup-server.sh "$(cat zenobot_deploy.pub)"
```

Il crée l'utilisateur `deploy`, qui peut écrire le code dans `/opt/zenobot` et redémarrer le service du bot, sans pouvoir lire le `.env` ni agir en root pour autre chose. Teste ensuite depuis ton ordinateur :

```bash
ssh -i zenobot_deploy deploy@ADRESSE_DU_VPS 'sudo -n systemctl is-active zenobot && journalctl -u zenobot -n 1 --no-pager'
```

Tu dois voir `active`, puis une ligne de log (pas d'erreur de permission).

**d. Récupère l'empreinte du serveur** (elle évite qu'un faux serveur se fasse passer pour le tien) :

```bash
ssh-keyscan -t ed25519 ADRESSE_DU_VPS        # si ton SSH n'est pas sur le port 22 : ssh-keyscan -t ed25519 -p PORT ADRESSE_DU_VPS
```

Vérifie que l'empreinte correspond bien à ton serveur en lançant `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` sur le VPS, puis garde la ligne obtenue par `ssh-keyscan`.

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
- Le **premier déploiement** n'a pas de version précédente à restaurer : si le bot ne démarre pas, le déploiement échoue et il faut regarder les logs.
- Le retour arrière ne défait pas les changements de base de données (il n'y en a pas de migration automatique) ni de configuration faite via les commandes `/config`.
- Quiconque peut pousser sur `main` peut exécuter du code sur le VPS sous l'utilisateur du bot, et donc lire son `.env`. Garde le dépôt privé, active la double authentification GitHub et protège la branche `main` (*Settings → Branches*).
- Si ton VPS n'a pas la même version de Python que le workflow (`python-version` dans `.github/workflows/ci-deploy.yml`), aligne-les.
- Le dossier du bot est `/opt/zenobot` (variable `APP_DIR` en haut du workflow) et le service s'appelle `zenobot`. Adapte-les dans le workflow si les tiens diffèrent.

## Maintenance

- **Logs** : `sudo journalctl -u zenobot -f` (ajoute `-n 200` pour les 200 dernières lignes).
- **Mise à jour** : avec le déploiement automatique, un simple `git push`. À la main : remplace les fichiers `.py` (garde `.env` et `data/`), puis `sudo -u zenobot venv/bin/pip install -r requirements.txt` et `sudo systemctl restart zenobot`.
- **Sauvegarde** : tout ce qui est mémorisé (réglages, XP, avertissements, rôles à boutons) est dans `data/`. Arrête le service puis copie le dossier `data/` complet (ou utilise `sqlite3 data/bot.db ".backup 'sauvegarde.db'"` sans arrêter).
- **Token compromis** : régénère-le tout de suite dans le portail Discord (*Bot → Reset Token*), mets-le à jour dans `.env` et redémarre.

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
| Un lien légitime est supprimé | Ajoute le site avec `/config domaine`, ou donne le rôle *Habitué* au membre. |

## Structure du projet

```
bot.py              point d'entrée
config.py           lecture du .env + seuils réglables
database.py         base SQLite
utils.py            fonctions communes
cogs/               un fichier par fonctionnalité (admin, twitch, moderation, levels, ...)
tests/              tests automatiques (lancés par la CI : python tests/test_bot.py)
deploy/             scripts de déploiement (préparation du VPS, sauvegarde, redémarrage, retour arrière)
.github/workflows/  CI/CD GitHub Actions
zenobot.service     service systemd
.env.example        modèle de configuration
```

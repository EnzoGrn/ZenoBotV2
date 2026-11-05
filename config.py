"""Chargement de la configuration (fichier .env) et constantes réglables."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


class ConfigError(RuntimeError):
    """Configuration manquante ou invalide."""


@dataclass(frozen=True)
class Settings:
    discord_token: str
    guild_id: int
    twitch_client_id: str
    twitch_client_secret: str
    twitch_channel: str
    database_path: Path
    log_level: str

    @property
    def twitch_enabled(self) -> bool:
        return bool(self.twitch_client_id and self.twitch_client_secret and self.twitch_channel)


def load_settings() -> Settings:
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token:
        raise ConfigError("DISCORD_TOKEN est vide : colle le token du bot dans le fichier .env")

    guild_raw = os.getenv("GUILD_ID", "").strip()
    if not guild_raw.isdigit():
        raise ConfigError(
            "GUILD_ID est vide ou invalide : colle l'identifiant (des chiffres) de ton serveur dans le fichier .env"
        )

    db_path = Path(os.getenv("DATABASE_PATH", "data/bot.db").strip() or "data/bot.db")
    if not db_path.is_absolute():
        db_path = BASE_DIR / db_path

    return Settings(
        discord_token=token,
        guild_id=int(guild_raw),
        twitch_client_id=os.getenv("TWITCH_CLIENT_ID", "").strip(),
        twitch_client_secret=os.getenv("TWITCH_CLIENT_SECRET", "").strip(),
        twitch_channel=os.getenv("TWITCH_CHANNEL", "ZenoSurTwitch_").strip().lstrip("@").lower(),
        database_path=db_path,
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
    )


# ---------------------------------------------------------------------------
# Constantes réglables (modifie-les ici si besoin, puis redémarre le bot)
# ---------------------------------------------------------------------------

# --- Twitch ---
TWITCH_POLL_SECONDS = 60            # fréquence de vérification du live
TWITCH_OFFLINE_GRACE = 3            # vérifications consécutives « hors-ligne » avant de clôturer l'annonce
TWITCH_RESUME_WINDOW_MINUTES = 10   # si le live reprend dans ce délai, on réutilise la même annonce (pas de nouveau ping)

# --- Niveaux / XP ---
XP_MIN = 15
XP_MAX = 25
XP_COOLDOWN_SECONDS = 60            # un membre ne gagne de l'XP qu'une fois par minute
XP_MIN_MESSAGE_LENGTH = 5           # les messages plus courts ne rapportent rien

# --- Anti-spam ---
SPAM_MAX_MESSAGES = 7               # plus de N messages...
SPAM_WINDOW_SECONDS = 8             # ...en N secondes = spam
SPAM_DUPLICATE_MAX = 3              # N messages identiques d'affilée = spam
SPAM_DUPLICATE_WINDOW_SECONDS = 30
SPAM_MAX_MENTIONS = 6               # N mentions (membres + rôles) dans un message = spam
SPAM_TIMEOUT_MINUTES = 5            # durée de l'exclusion temporaire automatique

# --- Avertissements ---
WARN_AUTO_TIMEOUT_THRESHOLD = 3     # tous les N avertissements, exclusion temporaire automatique
WARN_AUTO_TIMEOUT_MINUTES = 60

# --- Recherche de joueurs ---
LFG_EXPIRY_HOURS = 6                # une annonce est clôturée automatiquement après N heures

# --- Filtre de liens : domaines autorisés par défaut (modifiables avec /config domaine) ---
DEFAULT_ALLOWED_DOMAINS = (
    "twitch.tv",
    "youtube.com",
    "youtu.be",
    "tenor.com",
    "giphy.com",
    "imgur.com",
    "medal.tv",
    "streamable.com",
    "discordapp.com",
    "discordapp.net",
    "x.com",
    "twitter.com",
)

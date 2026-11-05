"""Fonctions utilitaires partagées par les modules du bot."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Iterable, NamedTuple
from urllib.parse import urlparse

# Couleurs des embeds
COLOR_TWITCH = 0x9146FF
COLOR_OK = 0x57F287
COLOR_WARN = 0xFEE75C
COLOR_DANGER = 0xED4245
COLOR_INFO = 0x5865F2
COLOR_MUTED = 0x95A5A6

URL_RE = re.compile(r"(?i)\b(?:https?://|www\.)[^\s<>]+")
INVITE_RE = re.compile(r"(?i)\bdiscord(?:app)?\.(?:gg|com/invite)/[\w-]+")


# --------------------------------------------------------------------------- dates

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_iso(value: str) -> datetime:
    """Lit une date ISO 8601 (avec « Z » ou un décalage) et renvoie un datetime UTC."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def rfc3339(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def discord_ts(dt: datetime, style: str = "R") -> str:
    """Horodatage Discord (affiché dans le fuseau du lecteur)."""
    return f"<t:{int(dt.timestamp())}:{style}>"


# --------------------------------------------------------------------------- durées

_DURATION_RE = re.compile(
    r"^\s*"
    r"(?:(?P<d>\d+)\s*(?:jours?|j|d)\s*)?"
    r"(?:(?P<h>\d+)\s*(?:heures?|hrs?|h)\s*)?"
    r"(?:(?P<m>\d+)\s*(?:minutes?|mins?|m)\s*)?"
    r"(?:(?P<s>\d+)\s*(?:secondes?|secs?|s)\s*)?"
    r"$",
    re.IGNORECASE,
)


def parse_duration(text: str) -> timedelta | None:
    """Convertit « 10m », « 2h », « 1j », « 1h30m »... en durée. Un nombre seul = minutes."""
    text = text.strip().lower()
    if not text:
        return None
    try:
        if text.isdigit():
            delta = timedelta(minutes=int(text))
        else:
            match = _DURATION_RE.match(text)
            if not match or not any(match.groupdict().values()):
                return None
            parts = {key: int(value) for key, value in match.groupdict().items() if value}
            delta = timedelta(
                days=parts.get("d", 0),
                hours=parts.get("h", 0),
                minutes=parts.get("m", 0),
                seconds=parts.get("s", 0),
            )
    except OverflowError:
        return None
    return delta if delta.total_seconds() > 0 else None


def format_duration(delta: timedelta) -> str:
    total = max(0, int(delta.total_seconds()))
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, seconds = divmod(rest, 60)
    parts = []
    if days:
        parts.append(f"{days} j")
    if hours:
        parts.append(f"{hours} h")
    if minutes:
        parts.append(f"{minutes} min")
    if seconds and total < 60:
        parts.append(f"{seconds} s")
    return " ".join(parts) or "0 s"


# --------------------------------------------------------------------------- texte

def truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


def progress_bar(current: int, total: int, width: int = 12) -> str:
    ratio = 0.0 if total <= 0 else min(max(current / total, 0.0), 1.0)
    filled = round(ratio * width)
    return "▰" * filled + "▱" * (width - filled)


# --------------------------------------------------------------------------- niveaux

class LevelInfo(NamedTuple):
    level: int
    xp_into_level: int
    xp_needed: int


def xp_to_next(level: int) -> int:
    """XP nécessaire pour passer du niveau `level` au suivant."""
    return 5 * level * level + 50 * level + 100


def level_from_xp(xp: int) -> LevelInfo:
    level = 0
    remaining = max(0, xp)
    while remaining >= xp_to_next(level):
        remaining -= xp_to_next(level)
        level += 1
    return LevelInfo(level, remaining, xp_to_next(level))


# --------------------------------------------------------------------------- liens

def extract_hosts(text: str) -> list[str]:
    """Renvoie le nom de domaine de chaque lien trouvé (chaîne vide si illisible)."""
    hosts: list[str] = []
    for match in URL_RE.finditer(text):
        raw = match.group(0)
        if not raw.lower().startswith("http"):
            raw = "http://" + raw
        try:
            host = urlparse(raw).hostname
        except ValueError:
            host = None
        hosts.append((host or "").lower().rstrip("."))
    return hosts


def host_allowed(host: str, allowed: Iterable[str]) -> bool:
    """Vrai si `host` est un domaine autorisé ou l'un de ses sous-domaines."""
    return any(host == domain or host.endswith("." + domain) for domain in allowed)

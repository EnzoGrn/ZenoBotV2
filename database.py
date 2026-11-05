"""Base de données SQLite (asynchrone). Le bot est prévu pour UN seul serveur."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Sequence

import aiosqlite

from config import DEFAULT_ALLOWED_DOMAINS
from utils import utcnow

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS allowed_domains (
    domain TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS warnings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,
    moderator_id INTEGER NOT NULL,
    reason       TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_warnings_user ON warnings (user_id);
CREATE TABLE IF NOT EXISTS levels (
    user_id  INTEGER PRIMARY KEY,
    xp       INTEGER NOT NULL DEFAULT 0,
    messages INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_levels_xp ON levels (xp DESC);
CREATE TABLE IF NOT EXISTS level_rewards (
    level   INTEGER PRIMARY KEY,
    role_id INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS self_roles (
    role_id INTEGER PRIMARY KEY,
    label   TEXT NOT NULL,
    emoji   TEXT
);
CREATE TABLE IF NOT EXISTS lfg (
    message_id INTEGER PRIMARY KEY,
    channel_id INTEGER NOT NULL,
    author_id  INTEGER NOT NULL,
    game       TEXT NOT NULL,
    slots      INTEGER NOT NULL,
    note       TEXT,
    closed     INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lfg_players (
    message_id INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    PRIMARY KEY (message_id, user_id)
);
"""


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._conn: aiosqlite.Connection | None = None
        self._settings: dict[str, str] = {}
        self._domains: set[str] = set()
        self._xp_lock = asyncio.Lock()
        self._lfg_lock = asyncio.Lock()

    # ------------------------------------------------------------------ connexion

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Base de données non connectée")
        return self._conn

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

        # Domaines autorisés : on injecte les valeurs par défaut une seule fois
        if await self.kv_get("domains_seeded") is None:
            for domain in DEFAULT_ALLOWED_DOMAINS:
                await self.execute("INSERT OR IGNORE INTO allowed_domains (domain) VALUES (?)", (domain,))
            await self.kv_set("domains_seeded", "1")

        self._settings = {r["key"]: r["value"] for r in await self.fetchall("SELECT key, value FROM settings")}
        self._domains = {r["domain"] for r in await self.fetchall("SELECT domain FROM allowed_domains")}
        log.info("Base de données prête : %s", self.path)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------ requêtes de base

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        """Exécute une requête d'écriture et renvoie le nombre de lignes modifiées."""
        async with self.conn.execute(sql, params) as cursor:
            await self.conn.commit()
            return cursor.rowcount

    async def insert(self, sql: str, params: Sequence[Any] = ()) -> int:
        """Exécute un INSERT et renvoie l'identifiant de la ligne créée."""
        async with self.conn.execute(sql, params) as cursor:
            await self.conn.commit()
            return cursor.lastrowid or 0

    async def fetchone(self, sql: str, params: Sequence[Any] = ()) -> aiosqlite.Row | None:
        async with self.conn.execute(sql, params) as cursor:
            return await cursor.fetchone()

    async def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[aiosqlite.Row]:
        async with self.conn.execute(sql, params) as cursor:
            return list(await cursor.fetchall())

    # ------------------------------------------------------------------ réglages (en cache)

    def setting(self, key: str, default: str | None = None) -> str | None:
        return self._settings.get(key, default)

    def all_settings(self) -> dict[str, str]:
        return dict(self._settings)

    async def set_setting(self, key: str, value: str) -> None:
        await self.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self._settings[key] = value

    async def delete_setting(self, key: str) -> None:
        await self.execute("DELETE FROM settings WHERE key = ?", (key,))
        self._settings.pop(key, None)

    # ------------------------------------------------------------------ état interne (clé/valeur)

    async def kv_get(self, key: str) -> str | None:
        row = await self.fetchone("SELECT value FROM kv WHERE key = ?", (key,))
        return row["value"] if row else None

    async def kv_set(self, key: str, value: str) -> None:
        await self.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    # ------------------------------------------------------------------ domaines autorisés

    @property
    def domains(self) -> frozenset[str]:
        return frozenset(self._domains)

    async def add_domain(self, domain: str) -> bool:
        added = await self.execute("INSERT OR IGNORE INTO allowed_domains (domain) VALUES (?)", (domain,)) > 0
        self._domains.add(domain)
        return added

    async def remove_domain(self, domain: str) -> bool:
        removed = await self.execute("DELETE FROM allowed_domains WHERE domain = ?", (domain,)) > 0
        self._domains.discard(domain)
        return removed

    # ------------------------------------------------------------------ avertissements

    async def add_warning(self, user_id: int, moderator_id: int, reason: str) -> int:
        return await self.insert(
            "INSERT INTO warnings (user_id, moderator_id, reason, created_at) VALUES (?, ?, ?, ?)",
            (user_id, moderator_id, reason, utcnow().isoformat(timespec="seconds")),
        )

    async def list_warnings(self, user_id: int) -> list[aiosqlite.Row]:
        return await self.fetchall("SELECT * FROM warnings WHERE user_id = ? ORDER BY id DESC", (user_id,))

    async def count_warnings(self, user_id: int) -> int:
        row = await self.fetchone("SELECT COUNT(*) AS n FROM warnings WHERE user_id = ?", (user_id,))
        return row["n"] if row else 0

    async def delete_warning(self, warning_id: int) -> aiosqlite.Row | None:
        row = await self.fetchone("SELECT * FROM warnings WHERE id = ?", (warning_id,))
        if row:
            await self.execute("DELETE FROM warnings WHERE id = ?", (warning_id,))
        return row

    async def clear_warnings(self, user_id: int) -> int:
        return await self.execute("DELETE FROM warnings WHERE user_id = ?", (user_id,))

    # ------------------------------------------------------------------ niveaux

    async def get_xp(self, user_id: int) -> aiosqlite.Row | None:
        return await self.fetchone("SELECT xp, messages FROM levels WHERE user_id = ?", (user_id,))

    async def add_xp(self, user_id: int, amount: int, *, count_message: bool = False) -> tuple[int, int]:
        """Ajoute (ou retire) de l'XP. Renvoie (ancien total, nouveau total)."""
        async with self._xp_lock:
            row = await self.get_xp(user_id)
            old = row["xp"] if row else 0
            new = max(0, old + amount)
            inc = 1 if count_message else 0
            await self.execute(
                "INSERT INTO levels (user_id, xp, messages) VALUES (?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET xp = excluded.xp, messages = messages + ?",
                (user_id, new, inc, inc),
            )
        return old, new

    async def reset_xp(self, user_id: int) -> bool:
        return await self.execute("DELETE FROM levels WHERE user_id = ?", (user_id,)) > 0

    async def get_rank(self, user_id: int) -> int | None:
        row = await self.get_xp(user_id)
        if row is None:
            return None
        better = await self.fetchone("SELECT COUNT(*) AS n FROM levels WHERE xp > ?", (row["xp"],))
        return (better["n"] if better else 0) + 1

    async def top_xp(self, limit: int, offset: int = 0) -> list[aiosqlite.Row]:
        return await self.fetchall(
            "SELECT user_id, xp, messages FROM levels WHERE xp > 0 ORDER BY xp DESC, user_id ASC LIMIT ? OFFSET ?",
            (limit, offset),
        )

    async def count_ranked(self) -> int:
        row = await self.fetchone("SELECT COUNT(*) AS n FROM levels WHERE xp > 0")
        return row["n"] if row else 0

    # ------------------------------------------------------------------ récompenses de niveau

    async def set_reward(self, level: int, role_id: int) -> None:
        await self.execute(
            "INSERT INTO level_rewards (level, role_id) VALUES (?, ?) "
            "ON CONFLICT(level) DO UPDATE SET role_id = excluded.role_id",
            (level, role_id),
        )

    async def remove_reward(self, level: int) -> bool:
        return await self.execute("DELETE FROM level_rewards WHERE level = ?", (level,)) > 0

    async def list_rewards(self) -> list[aiosqlite.Row]:
        return await self.fetchall("SELECT level, role_id FROM level_rewards ORDER BY level")

    async def rewards_up_to(self, level: int) -> list[int]:
        rows = await self.fetchall("SELECT role_id FROM level_rewards WHERE level <= ? ORDER BY level", (level,))
        return [r["role_id"] for r in rows]

    # ------------------------------------------------------------------ rôles à boutons

    async def add_self_role(self, role_id: int, label: str, emoji: str | None) -> None:
        await self.execute(
            "INSERT INTO self_roles (role_id, label, emoji) VALUES (?, ?, ?) "
            "ON CONFLICT(role_id) DO UPDATE SET label = excluded.label, emoji = excluded.emoji",
            (role_id, label, emoji),
        )

    async def remove_self_role(self, role_id: int) -> bool:
        return await self.execute("DELETE FROM self_roles WHERE role_id = ?", (role_id,)) > 0

    async def get_self_role(self, role_id: int) -> aiosqlite.Row | None:
        return await self.fetchone("SELECT * FROM self_roles WHERE role_id = ?", (role_id,))

    async def list_self_roles(self) -> list[aiosqlite.Row]:
        return await self.fetchall("SELECT * FROM self_roles ORDER BY rowid")

    # ------------------------------------------------------------------ recherche de joueurs

    async def create_lfg(
        self, message_id: int, channel_id: int, author_id: int, game: str, slots: int, note: str | None
    ) -> None:
        await self.execute(
            "INSERT INTO lfg (message_id, channel_id, author_id, game, slots, note, closed, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
            (message_id, channel_id, author_id, game, slots, note, utcnow().isoformat(timespec="seconds")),
        )

    async def get_lfg(self, message_id: int) -> aiosqlite.Row | None:
        return await self.fetchone("SELECT * FROM lfg WHERE message_id = ?", (message_id,))

    async def lfg_players(self, message_id: int) -> list[int]:
        rows = await self.fetchall("SELECT user_id FROM lfg_players WHERE message_id = ? ORDER BY rowid", (message_id,))
        return [r["user_id"] for r in rows]

    async def lfg_join(self, message_id: int, user_id: int) -> str:
        """Inscrit un joueur. Renvoie « ok », « already », « full » ou « closed »."""
        async with self._lfg_lock:
            lfg = await self.get_lfg(message_id)
            if lfg is None or lfg["closed"]:
                return "closed"
            players = await self.lfg_players(message_id)
            if user_id in players:
                return "already"
            if len(players) >= lfg["slots"]:
                return "full"
            await self.execute("INSERT INTO lfg_players (message_id, user_id) VALUES (?, ?)", (message_id, user_id))
            return "ok"

    async def lfg_leave(self, message_id: int, user_id: int) -> bool:
        return await self.execute(
            "DELETE FROM lfg_players WHERE message_id = ? AND user_id = ?", (message_id, user_id)
        ) > 0

    async def lfg_close(self, message_id: int) -> None:
        await self.execute("UPDATE lfg SET closed = 1 WHERE message_id = ?", (message_id,))

    async def lfg_open_by_author(self, author_id: int) -> list[aiosqlite.Row]:
        return await self.fetchall("SELECT * FROM lfg WHERE author_id = ? AND closed = 0", (author_id,))

    async def lfg_expired(self, cutoff_iso: str) -> list[aiosqlite.Row]:
        return await self.fetchall("SELECT * FROM lfg WHERE closed = 0 AND created_at < ?", (cutoff_iso,))

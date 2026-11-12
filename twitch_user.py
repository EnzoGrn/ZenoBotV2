"""Autorisation Twitch « utilisateur » : le bot agit au nom de ta chaîne (chat, abonnements, points de chaîne...).

Le jeton est obtenu une seule fois (`/stream autoriser`), conservé dans la base de données et renouvelé automatiquement.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Optional
from urllib.parse import parse_qs, quote, urlparse

import aiohttp

import config as cfg

log = logging.getLogger(__name__)

TOKEN_KEY = "twitch.user_token"


class AuthError(Exception):
    """L'autorisation Twitch est absente, refusée ou expirée."""


def extract_code(text: str) -> str:
    """Accepte le code seul ou l'adresse complète (https://localhost/?code=...&scope=...) et renvoie le code."""
    text = text.strip()
    if "code=" in text:
        query = urlparse(text).query if "://" in text else text.lstrip("?")
        values = parse_qs(query).get("code")
        if values:
            return values[0]
    return text


class TwitchUserAuth:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        db: Any,
        channel_login: str,
        *,
        redirect_uri: str = cfg.TWITCH_REDIRECT_URI,
        scopes: tuple[str, ...] = cfg.TWITCH_USER_SCOPES,
        api_base: str = "https://api.twitch.tv/helix",
        oauth_base: str = "https://id.twitch.tv/oauth2",
    ):
        self._client_id = client_id
        self._client_secret = client_secret
        self._db = db
        self.channel_login = channel_login.lower()
        self.redirect_uri = redirect_uri
        self.scopes = scopes
        self._api_base = api_base.rstrip("/")
        self._oauth_base = oauth_base.rstrip("/")
        self._session: Optional[aiohttp.ClientSession] = None
        self._token: dict[str, Any] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ cycle de vie

    async def start(self) -> None:
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        raw = await self._db.kv_get(TOKEN_KEY)
        if raw:
            try:
                self._token = json.loads(raw)
            except ValueError:
                self._token = {}

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    # ------------------------------------------------------------------ état

    @property
    def authorized(self) -> bool:
        return bool(self._token.get("refresh_token"))

    @property
    def user_id(self) -> str:
        return str(self._token.get("user_id", ""))

    @property
    def login(self) -> str:
        return str(self._token.get("login", ""))

    @property
    def granted_scopes(self) -> set[str]:
        return set(self._token.get("scopes", []))

    @property
    def missing_scopes(self) -> list[str]:
        return [s for s in self.scopes if s not in self.granted_scopes]

    def authorize_url(self) -> str:
        scope = quote(" ".join(self.scopes), safe="")
        return (
            f"{self._oauth_base}/authorize?client_id={self._client_id}&redirect_uri={quote(self.redirect_uri, safe='')}"
            f"&response_type=code&scope={scope}&force_verify=true"
        )

    # ------------------------------------------------------------------ jetons

    async def _save(self) -> None:
        await self._db.kv_set(TOKEN_KEY, json.dumps(self._token))

    async def _forget(self) -> None:
        self._token = {}
        await self._db.kv_set(TOKEN_KEY, "")

    async def _token_request(self, data: dict[str, str]) -> dict[str, Any]:
        if self._session is None:
            raise AuthError("Client Twitch non démarré")
        payload = {"client_id": self._client_id, "client_secret": self._client_secret, **data}
        try:
            async with self._session.post(f"{self._oauth_base}/token", data=payload) as resp:
                body = await resp.json(content_type=None)
                if resp.status != 200:
                    raise AuthError(str((body or {}).get("message") or f"HTTP {resp.status}"))
                return body
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise AuthError(f"Twitch injoignable : {exc}") from exc

    async def exchange_code(self, code: str) -> str:
        """Échange le code d'autorisation contre un jeton. Renvoie le pseudo du compte autorisé."""
        body = await self._token_request(
            {"grant_type": "authorization_code", "code": extract_code(code), "redirect_uri": self.redirect_uri}
        )
        access = body["access_token"]
        status, data = await self._raw_request("GET", "users", access)
        users = (data or {}).get("data") or []
        if status != 200 or not users:
            raise AuthError("Impossible de lire le profil du compte autorisé.")
        user = users[0]
        if user["login"].lower() != self.channel_login:
            raise AuthError(
                f"Tu as autorisé le compte « {user['login']} » : connecte-toi avec le compte de la chaîne « {self.channel_login} »."
            )
        async with self._lock:
            self._token = {
                "access_token": access,
                "refresh_token": body["refresh_token"],
                "expires_at": time.time() + int(body.get("expires_in", 3600)) - 120,
                "scopes": body.get("scope", []),
                "user_id": user["id"],
                "login": user["login"].lower(),
            }
            await self._save()
        return user["login"]

    async def access_token(self, force: bool = False) -> str:
        async with self._lock:
            if not self.authorized:
                raise AuthError("Le bot n'est pas autorisé : utilise /stream autoriser.")
            if force or time.time() >= float(self._token.get("expires_at", 0)):
                try:
                    body = await self._token_request(
                        {"grant_type": "refresh_token", "refresh_token": self._token["refresh_token"]}
                    )
                except AuthError as exc:
                    if "Twitch injoignable" in str(exc):
                        raise
                    await self._forget()
                    raise AuthError(f"Autorisation expirée ou révoquée ({exc}) : refais /stream autoriser.") from exc
                self._token.update(
                    access_token=body["access_token"],
                    refresh_token=body.get("refresh_token", self._token["refresh_token"]),
                    expires_at=time.time() + int(body.get("expires_in", 3600)) - 120,
                )
                await self._save()
            return str(self._token["access_token"])

    # ------------------------------------------------------------------ requêtes API

    async def _raw_request(
        self, method: str, path: str, token: str, *, params: Any = None, json_body: Any = None
    ) -> tuple[int, Any]:
        if self._session is None:
            raise AuthError("Client Twitch non démarré")
        headers = {"Client-Id": self._client_id, "Authorization": f"Bearer {token}"}
        try:
            async with self._session.request(
                method, f"{self._api_base}/{path}", params=params, json=json_body, headers=headers
            ) as resp:
                try:
                    body = await resp.json(content_type=None)
                except (ValueError, aiohttp.ContentTypeError):
                    body = None
                return resp.status, body
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise AuthError(f"Twitch injoignable : {exc}") from exc

    async def request(self, method: str, path: str, *, params: Any = None, json_body: Any = None) -> tuple[int, Any]:
        """Appel de l'API Helix avec le jeton du compte autorisé (renouvelé automatiquement). Renvoie (statut, JSON)."""
        for attempt in (1, 2):
            token = await self.access_token(force=(attempt == 2))
            status, body = await self._raw_request(method, path, token, params=params, json_body=json_body)
            if status == 401 and attempt == 1:
                continue
            return status, body
        raise AuthError("Authentification Twitch impossible")

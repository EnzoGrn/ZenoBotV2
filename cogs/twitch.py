"""Twitch : annonces de live et partage automatique des clips (API Helix, sans webhook)."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import timedelta
from typing import Any, Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

import config as cfg
from utils import (
    COLOR_MUTED,
    COLOR_TWITCH,
    format_duration,
    parse_iso,
    rfc3339,
    truncate,
    utcnow,
)

log = logging.getLogger(__name__)

API_BASE = "https://api.twitch.tv/helix"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"
STATE_KEY = "twitch.state"


class TwitchAPIError(Exception):
    """Erreur de communication avec Twitch."""


class TwitchClient:
    """Petit client pour l'API Twitch (authentification « application », sans connexion de ton compte)."""

    def __init__(self, client_id: str, client_secret: str, *, api_base: str = API_BASE, token_url: str = TOKEN_URL):
        self._client_id = client_id
        self._client_secret = client_secret
        self._api_base = api_base.rstrip("/")
        self._token_url = token_url
        self._session: Optional[aiohttp.ClientSession] = None
        self._token: Optional[str] = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def _refresh_token(self) -> None:
        assert self._session is not None
        async with self._session.post(
            self._token_url,
            data={"client_id": self._client_id, "client_secret": self._client_secret, "grant_type": "client_credentials"},
        ) as resp:
            if resp.status != 200:
                raise TwitchAPIError(
                    f"Authentification Twitch refusée (HTTP {resp.status}) : vérifie TWITCH_CLIENT_ID et TWITCH_CLIENT_SECRET."
                )
            data = await resp.json()
        self._token = data["access_token"]
        self._expires_at = time.monotonic() + int(data.get("expires_in", 3600)) - 120

    async def _get_token(self, force: bool = False) -> str:
        async with self._lock:
            if force or not self._token or time.monotonic() >= self._expires_at:
                await self._refresh_token()
            assert self._token is not None
            return self._token

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        if self._session is None:
            raise TwitchAPIError("Client Twitch non démarré")
        try:
            for attempt in (1, 2):
                token = await self._get_token(force=(attempt == 2))
                headers = {"Client-Id": self._client_id, "Authorization": f"Bearer {token}"}
                async with self._session.get(f"{self._api_base}/{path}", params=params, headers=headers) as resp:
                    if resp.status == 401 and attempt == 1:
                        continue  # jeton expiré : on en redemande un et on réessaie une fois
                    if resp.status == 429:
                        raise TwitchAPIError("Limite de requêtes Twitch atteinte, nouvel essai plus tard.")
                    if resp.status != 200:
                        raise TwitchAPIError(f"Twitch a répondu HTTP {resp.status} : {(await resp.text())[:200]}")
                    return await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise TwitchAPIError(f"Twitch injoignable : {exc}") from exc
        raise TwitchAPIError("Authentification Twitch impossible")

    async def get_user(self, login: str) -> Optional[dict[str, Any]]:
        data = await self._get("users", {"login": login})
        return data["data"][0] if data.get("data") else None

    async def get_stream(self, login: str) -> Optional[dict[str, Any]]:
        data = await self._get("streams", {"user_login": login})
        return data["data"][0] if data.get("data") else None

    async def get_clips(self, broadcaster_id: str, started_at, first: int = 50) -> list[dict[str, Any]]:
        data = await self._get("clips", {"broadcaster_id": broadcaster_id, "started_at": rfc3339(started_at), "first": first})
        return data.get("data", [])


def _link_view(label: str, url: str) -> discord.ui.View:
    view = discord.ui.View()
    view.add_item(discord.ui.Button(style=discord.ButtonStyle.link, label=label, url=url))
    return view


class Twitch(commands.Cog):
    twitch_group = app_commands.Group(
        name="twitch",
        description="Outils Twitch (annonces de live, clips)",
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )

    def __init__(self, bot: commands.Bot, client: Optional[TwitchClient] = None):
        self.bot = bot
        self.login: str = bot.settings.twitch_channel  # type: ignore[attr-defined]
        self.client = client or TwitchClient(bot.settings.twitch_client_id, bot.settings.twitch_client_secret)  # type: ignore[attr-defined]
        self.state: dict[str, Any] = {}
        self._offline_streak = 0
        self._last_seen_live = None
        self._user: Optional[dict[str, Any]] = None
        self._user_fetched_at = 0.0
        self._warned_no_channel_for: Optional[str] = None

    @property
    def channel_url(self) -> str:
        return f"https://twitch.tv/{self.login}"

    # ------------------------------------------------------------------ cycle de vie

    async def cog_load(self) -> None:
        raw = await self.bot.db.kv_get(STATE_KEY)  # type: ignore[attr-defined]
        self.state = json.loads(raw) if raw else {}
        await self.client.start()
        self.live_loop.start()
        self.clips_loop.start()

    async def cog_unload(self) -> None:
        self.live_loop.cancel()
        self.clips_loop.cancel()
        await self.client.close()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not interaction.user.guild_permissions.manage_guild:  # type: ignore[union-attr]
            raise app_commands.MissingPermissions(["manage_guild"])
        return True

    async def _save_state(self) -> None:
        await self.bot.db.kv_set(STATE_KEY, json.dumps(self.state))  # type: ignore[attr-defined]

    async def _get_user(self) -> Optional[dict[str, Any]]:
        """Profil Twitch de la chaîne (mis en cache une heure)."""
        if self._user and time.monotonic() - self._user_fetched_at < 3600:
            return self._user
        try:
            user = await self.client.get_user(self.login)
        except TwitchAPIError as exc:
            log.warning("Profil Twitch indisponible : %s", exc)
            return self._user
        if user:
            self._user, self._user_fetched_at = user, time.monotonic()
        return self._user

    # ------------------------------------------------------------------ annonces de live

    @tasks.loop(seconds=cfg.TWITCH_POLL_SECONDS)
    async def live_loop(self) -> None:
        try:
            await self._check_live()
        except TwitchAPIError as exc:
            log.warning("Twitch (live) : %s", exc)
        except Exception:
            log.exception("Erreur inattendue pendant la vérification du live")

    @live_loop.before_loop
    async def _before_live(self) -> None:
        await self.bot.wait_until_ready()

    async def _check_live(self) -> None:
        stream = await self.client.get_stream(self.login)
        if stream is None:
            if self.state.get("stream_id"):
                self._offline_streak += 1
                if self._offline_streak >= cfg.TWITCH_OFFLINE_GRACE:
                    await self._close_announcement()
            else:
                self._offline_streak = 0
            return

        self._offline_streak = 0
        self._last_seen_live = utcnow()
        current = self.state.get("stream_id")
        if current and current != stream["id"]:
            await self._close_announcement()  # nouvelle session : on clôture l'ancienne annonce
            current = None
        if current:
            await self._refresh_announcement(stream)
        else:
            await self._announce(stream)

    def _build_live_embed(self, stream: dict[str, Any], user: Optional[dict[str, Any]]) -> discord.Embed:
        name = (user or {}).get("display_name") or stream.get("user_name") or self.login
        embed = discord.Embed(
            title=truncate(stream.get("title") or "Live en cours", 256),
            url=self.channel_url,
            color=COLOR_TWITCH,
        )
        embed.set_author(name=f"{name} est en live !", url=self.channel_url, icon_url=(user or {}).get("profile_image_url"))
        embed.add_field(name="🎮 Jeu", value=stream.get("game_name") or "Non précisé")
        thumbnail = (stream.get("thumbnail_url") or "").replace("{width}", "1280").replace("{height}", "720")
        if thumbnail:
            embed.set_image(url=f"{thumbnail}?t={int(time.time())}")  # le paramètre évite l'image en cache
        if stream.get("started_at"):
            embed.timestamp = parse_iso(stream["started_at"])
            embed.set_footer(text="En live depuis")
        return embed

    def _build_end_embed(self, user: Optional[dict[str, Any]]) -> discord.Embed:
        name = (user or {}).get("display_name") or self.login
        started = parse_iso(self.state["started_at"]) if self.state.get("started_at") else None
        ended = self._last_seen_live or utcnow()
        embed = discord.Embed(
            title=f"Le live de {name} est terminé",
            url=f"{self.channel_url}/videos",
            description="Merci à toutes et tous d'être passés ! 💜",
            color=COLOR_MUTED,
        )
        embed.set_author(name=name, url=self.channel_url, icon_url=(user or {}).get("profile_image_url"))
        if self.state.get("title"):
            embed.add_field(name="📝 Titre", value=truncate(discord.utils.escape_markdown(self.state["title"]), 256), inline=False)
        if started:
            embed.add_field(name="⏱️ Durée", value=format_duration(ended - started))
        games = self.state.get("games") or []
        if games:
            embed.add_field(name="🎮 Jeux", value=truncate(", ".join(games), 1024))
        return embed

    async def _announce(self, stream: dict[str, Any]) -> None:
        channel = self.bot.channel_setting("annonces")  # type: ignore[attr-defined]
        if channel is None:
            if self._warned_no_channel_for != stream["id"]:
                log.warning("Tu es en live mais aucun salon d'annonces n'est configuré (/config salon).")
                self._warned_no_channel_for = stream["id"]
            return

        user = await self._get_user()
        name = (user or {}).get("display_name") or self.login
        embed = self._build_live_embed(stream, user)
        view = _link_view("Regarder sur Twitch", self.channel_url)
        game = stream.get("game_name") or ""

        # Le live reprend juste après une coupure : on réutilise l'annonce existante (pas de nouveau ping)
        message_id: Optional[int] = None
        last_end = self.state.get("last_end")
        if (
            last_end
            and self.state.get("message_id")
            and self.state.get("channel_id") == channel.id
            and utcnow() - parse_iso(last_end) < timedelta(minutes=cfg.TWITCH_RESUME_WINDOW_MINUTES)
        ):
            try:
                await channel.get_partial_message(self.state["message_id"]).edit(
                    content=f"🔴 **{name}** est de retour en live !",
                    embed=embed,
                    view=view,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                message_id = self.state["message_id"]
            except discord.NotFound:
                message_id = None

        if message_id is None:
            role = self.bot.role_setting("notif_live")  # type: ignore[attr-defined]
            content = f"{role.mention} " if role else ""
            content += f"🔴 **{name}** est en live !"
            sent = await channel.send(
                content,
                embed=embed,
                view=view,
                allowed_mentions=discord.AllowedMentions(roles=[role]) if role else discord.AllowedMentions.none(),
            )
            message_id = sent.id
            log.info("Live annoncé dans #%s", channel.name)

        self.state.update(
            stream_id=stream["id"],
            message_id=message_id,
            channel_id=channel.id,
            started_at=stream.get("started_at"),
            title=stream.get("title") or "",
            game=game,
            games=[game] if game else [],
            last_end=None,
        )
        await self._save_state()

    async def _refresh_announcement(self, stream: dict[str, Any]) -> None:
        """Met à jour l'annonce quand le titre ou le jeu change en cours de live."""
        title, game = stream.get("title") or "", stream.get("game_name") or ""
        games: list[str] = self.state.setdefault("games", [])
        changed = False
        if game and game not in games:
            games.append(game)
            changed = True

        if title != self.state.get("title") or game != self.state.get("game"):
            channel = self.bot.get_channel(self.state.get("channel_id") or 0)
            if isinstance(channel, discord.TextChannel) and self.state.get("message_id"):
                embed = self._build_live_embed(stream, await self._get_user())
                try:
                    await channel.get_partial_message(self.state["message_id"]).edit(embed=embed)
                except discord.NotFound:
                    self.state["message_id"] = None
                except discord.HTTPException as exc:
                    log.warning("Mise à jour de l'annonce impossible : %s", exc)
            self.state["title"], self.state["game"] = title, game
            changed = True
        if changed:
            await self._save_state()

    async def _close_announcement(self) -> None:
        """Le live est fini : l'annonce devient un récapitulatif (et le ping disparaît du texte)."""
        if not self.state.get("stream_id"):
            return
        user = await self._get_user()
        channel = self.bot.get_channel(self.state.get("channel_id") or 0)
        if isinstance(channel, discord.TextChannel) and self.state.get("message_id"):
            try:
                await channel.get_partial_message(self.state["message_id"]).edit(
                    content=None,
                    embed=self._build_end_embed(user),
                    view=_link_view("Voir les rediffusions", f"{self.channel_url}/videos"),
                )
            except discord.NotFound:
                pass
            except discord.HTTPException as exc:
                log.warning("Clôture de l'annonce impossible : %s", exc)
        self.state.update(stream_id=None, last_end=utcnow().isoformat(timespec="seconds"))
        self._offline_streak = 0
        await self._save_state()
        log.info("Live terminé, annonce clôturée.")

    # ------------------------------------------------------------------ clips

    @tasks.loop(minutes=cfg.TWITCH_CLIPS_POLL_MINUTES)
    async def clips_loop(self) -> None:
        try:
            await self._check_clips()
        except TwitchAPIError as exc:
            log.warning("Twitch (clips) : %s", exc)
        except Exception:
            log.exception("Erreur inattendue pendant la recherche de clips")

    @clips_loop.before_loop
    async def _before_clips(self) -> None:
        await self.bot.wait_until_ready()

    async def _check_clips(self) -> None:
        channel = self.bot.channel_setting("clips")  # type: ignore[attr-defined]
        if channel is None:
            return
        user = await self._get_user()
        if not user:
            return

        now = utcnow()
        last = self.state.get("clips_checked_at")
        if not last:
            # Première activation : on commence à partir de maintenant (pas de rafale d'anciens clips)
            self.state["clips_checked_at"] = now.isoformat(timespec="seconds")
            await self._save_state()
            return

        since = max(parse_iso(last) - timedelta(minutes=5), now - timedelta(hours=24))
        clips = await self.client.get_clips(user["id"], since)
        clips.sort(key=lambda c: c["created_at"])
        for clip in clips:
            if await self.bot.db.clip_posted(clip["id"]):  # type: ignore[attr-defined]
                continue
            text = (
                f"🎬 **{discord.utils.escape_markdown(truncate(clip.get('title') or 'Sans titre', 150))}** — "
                f"clippé par **{discord.utils.escape_markdown(clip.get('creator_name') or 'quelqu’un')}**\n{clip['url']}"
            )
            try:
                sent = await channel.send(text, allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException as exc:
                log.warning("Impossible de poster un clip : %s", exc)
                return  # on réessaiera au prochain passage (le clip n'est pas marqué comme posté)
            await self.bot.db.mark_clip_posted(clip["id"])  # type: ignore[attr-defined]
            for emoji in ("🔥", "😂"):
                try:
                    await sent.add_reaction(emoji)
                except discord.HTTPException:
                    break
            await asyncio.sleep(1)

        self.state["clips_checked_at"] = now.isoformat(timespec="seconds")
        await self._save_state()

    # ------------------------------------------------------------------ commandes

    @twitch_group.command(name="statut", description="Vérifie la connexion à Twitch et l'état des annonces")
    async def twitch_statut(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        lines: list[str] = []
        try:
            user = await self.client.get_user(self.login)
            if user is None:
                lines.append(f"❌ Chaîne Twitch **{self.login}** introuvable : vérifie TWITCH_CHANNEL dans le fichier .env.")
            else:
                lines.append(f"✅ Connexion à l'API Twitch OK : chaîne **{user['display_name']}**")
                stream = await self.client.get_stream(self.login)
                if stream:
                    lines.append(f"🔴 Tu es **en live** : « {truncate(stream.get('title') or '', 80)} » ({stream.get('game_name') or 'jeu non précisé'})")
                else:
                    lines.append("⚫ Tu n'es pas en live pour le moment.")
        except TwitchAPIError as exc:
            lines.append(f"❌ {exc}")

        annonces = self.bot.channel_setting("annonces")  # type: ignore[attr-defined]
        clips = self.bot.channel_setting("clips")  # type: ignore[attr-defined]
        role = self.bot.role_setting("notif_live")  # type: ignore[attr-defined]
        lines.append(f"📢 Salon des annonces : {annonces.mention if annonces else '⚠️ non défini (/config salon)'}")
        lines.append(f"🔔 Rôle pingé : {role.mention if role else '⚠️ non défini (/config role)'}")
        lines.append(f"🎬 Salon des clips : {clips.mention if clips else 'non défini (clips désactivés)'}")
        if self.state.get("stream_id"):
            lines.append("📌 Une annonce de live est en cours de suivi.")
        await interaction.followup.send("\n".join(lines), ephemeral=True)

    @twitch_group.command(name="test", description="Publie une fausse annonce (sans ping) pour vérifier le rendu")
    async def twitch_test(self, interaction: discord.Interaction) -> None:
        channel = self.bot.channel_setting("annonces")  # type: ignore[attr-defined]
        if channel is None:
            await interaction.response.send_message("❌ Définis d'abord le salon avec `/config salon`.", ephemeral=True)
            return
        fake = {
            "title": "🧪 Ceci est un test d'annonce",
            "game_name": "Jeu de test",
            "started_at": rfc3339(utcnow()),
            "thumbnail_url": "",
        }
        embed = self._build_live_embed(fake, await self._get_user())
        try:
            await channel.send(
                "🧪 **Test** : voici à quoi ressemblera l'annonce (aucun ping, ce message disparaît dans 60 s).",
                embed=embed,
                view=_link_view("Regarder sur Twitch", self.channel_url),
                delete_after=60,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.Forbidden:
            await interaction.response.send_message(f"❌ Je ne peux pas écrire dans {channel.mention}.", ephemeral=True)
            return
        await interaction.response.send_message(f"✅ Test envoyé dans {channel.mention}.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Twitch(bot))

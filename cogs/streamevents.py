"""Événements Twitch en direct : collecte pour les crédits de fin de live, récompense « Vrai viewer ».

Le bot se connecte à Twitch (EventSub en WebSocket, aucune adresse publique nécessaire) avec ton compte autorisé
par `/stream autoriser`. Pendant un live, il note qui écrit, qui s'abonne, qui offre des abonnements, qui envoie
des bits, qui suit, qui raide, et relève chaque minute les personnes connectées au chat (les silencieux).
À la fin du live, il génère la page HTML des crédits et te l'envoie.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import secrets
from collections import deque
from datetime import timedelta
from typing import Any, Optional

import aiohttp
from aiohttp import web
import discord
from discord import app_commands
from discord.ext import commands, tasks

import config as cfg
import credits
import credits_editor
from twitch_user import AuthError, TwitchUserAuth
from utils import COLOR_INFO, COLOR_OK, COLOR_WARN, truncate, utcnow

log = logging.getLogger(__name__)

REWARD_TITLE = "Vrai viewer"
REWARD_PROMPT = "Écris ton pseudo Discord (ton nom d'utilisateur) pour recevoir le rôle « Vrai viewer » sur le serveur."
REWARD_SETTING = "reward.vrai_viewer"
LAST_STREAM_KEY = "credits.last"
TOKEN_SETTING = "credits.token"
STYLE_KEY = "credits.style"
PRESETS_KEY = "credits.presets"


class StreamEvents(commands.Cog):
    stream_group = app_commands.Group(
        name="stream",
        description="Outils de stream : autorisation Twitch, récompense, crédits (staff)",
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )

    def __init__(self, bot: commands.Bot, auth: Optional[TwitchUserAuth] = None):
        self.bot = bot
        s = bot.settings  # type: ignore[attr-defined]
        self.auth = auth or TwitchUserAuth(s.twitch_client_id, s.twitch_client_secret, bot.db, s.twitch_channel)  # type: ignore[attr-defined]
        self.ws_url = cfg.EVENTSUB_WS_URL
        self.connected = False
        self.subscribed: list[str] = []
        self.sub_errors: dict[str, str] = {}
        self._http: Optional[aiohttp.ClientSession] = None
        self._ws_task: Optional[asyncio.Task] = None
        self._stream_id: Optional[str] = None
        self._seen: deque[str] = deque(maxlen=500)
        self._recv_timeout = 15.0
        self._backoff = 5.0
        self._wake = asyncio.Event()
        self._warned_unauthorized = False
        self._warned_chatters = False
        self.web_port = cfg.CREDITS_WEB_PORT
        self._web_runner: Optional[web.AppRunner] = None
        self.web_bound_port: Optional[int] = None

    # ------------------------------------------------------------------ cycle de vie

    async def cog_load(self) -> None:
        await self.auth.start()
        self._http = aiohttp.ClientSession()
        self._ws_task = asyncio.create_task(self._ws_loop(), name="eventsub")
        self.chatters_loop.start()
        await self._start_web()

    async def cog_unload(self) -> None:
        self.chatters_loop.cancel()
        if self._ws_task is not None:
            self._ws_task.cancel()
        if self._web_runner is not None:
            await self._web_runner.cleanup()
        if self._http is not None:
            await self._http.close()
        await self.auth.close()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not interaction.user.guild_permissions.manage_guild:  # type: ignore[union-attr]
            raise app_commands.MissingPermissions(["manage_guild"])
        return True

    def restart_connection(self) -> None:
        """Relance la connexion Twitch (après une nouvelle autorisation, pour prendre les nouveaux droits)."""
        if self._ws_task is not None:
            self._ws_task.cancel()
        self.connected = False
        self._ws_task = asyncio.create_task(self._ws_loop(), name="eventsub")

    # ------------------------------------------------------------------ live en cours

    def current_stream_id(self) -> Optional[str]:
        if self._stream_id:
            return self._stream_id
        twitch = self.bot.get_cog("Twitch")
        return twitch.state.get("stream_id") if twitch is not None else None  # type: ignore[attr-defined]

    def bots(self) -> set[str]:
        extra = (self.bot.db.setting("credits.bots") or "").split(",")  # type: ignore[attr-defined]
        return {b.strip().lower() for b in (*cfg.DEFAULT_BOTS, *extra) if b.strip()}

    # ------------------------------------------------------------------ connexion EventSub

    async def _ws_loop(self) -> None:
        url, resume = self.ws_url, False
        while True:
            if not self.auth.authorized:
                if not self._warned_unauthorized:
                    log.info("Événements Twitch en attente : autorise le bot avec /stream autoriser.")
                    self._warned_unauthorized = True
                try:
                    await asyncio.wait_for(self._wake.wait(), 30)
                except asyncio.TimeoutError:
                    pass
                self._wake.clear()
                continue
            self._warned_unauthorized = False
            try:
                nxt = await self._ws_session(url, resume)
                if nxt:
                    url, resume = nxt, True
                    continue
                url, resume = self.ws_url, False
            except asyncio.CancelledError:
                raise
            except AuthError as exc:
                log.warning("Événements Twitch : %s", exc)
                url, resume = self.ws_url, False
            except Exception as exc:  # noqa: BLE001 - la boucle ne doit jamais s'arrêter
                log.warning("Événements Twitch : connexion perdue (%s)", exc)
                url, resume = self.ws_url, False
            self.connected = False
            await asyncio.sleep(self._backoff)
            self._backoff = min(self._backoff * 2, 120)

    async def _ws_session(self, url: str, resume: bool) -> Optional[str]:
        """Écoute une connexion. Renvoie l'adresse de reprise si Twitch demande de se reconnecter, sinon None."""
        assert self._http is not None
        self._recv_timeout = 15.0
        async with self._http.ws_connect(url) as ws:
            while True:
                try:
                    msg = await ws.receive(timeout=self._recv_timeout)
                except asyncio.TimeoutError:
                    raise ConnectionError("plus aucun message de Twitch (keepalive manqué)") from None
                if msg.type == aiohttp.WSMsgType.TEXT:
                    nxt = await self._handle_frame(json.loads(msg.data), resume)
                    if nxt:
                        return nxt
                elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING):
                    raise ConnectionError(f"connexion fermée par Twitch (code {ws.close_code})")
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    raise ConnectionError(f"erreur WebSocket : {ws.exception()}")

    async def _handle_frame(self, frame: dict[str, Any], resume: bool) -> Optional[str]:
        meta = frame.get("metadata", {})
        kind = meta.get("message_type")
        payload = frame.get("payload", {})
        if kind == "session_welcome":
            session = payload["session"]
            self._recv_timeout = float(session.get("keepalive_timeout_seconds") or 10) * 1.5 + 2
            self._backoff = 5.0
            self.connected = True
            if not resume:
                await self._subscribe_all(session["id"])
        elif kind == "notification":
            message_id = meta.get("message_id", "")
            if message_id in self._seen:
                return None
            self._seen.append(message_id)
            try:
                await self._on_notification(meta.get("subscription_type", ""), payload.get("event") or {})
            except Exception:  # noqa: BLE001
                log.exception("Erreur pendant le traitement d'un événement Twitch (%s)", meta.get("subscription_type"))
        elif kind == "session_reconnect":
            return payload["session"]["reconnect_url"]
        elif kind == "revocation":
            sub = payload.get("subscription", {})
            log.warning("Abonnement Twitch révoqué : %s (%s)", sub.get("type"), sub.get("status"))
        return None

    def _subscription_list(self) -> list[tuple[str, str, dict[str, str]]]:
        b = self.auth.user_id
        return [
            ("stream.online", "1", {"broadcaster_user_id": b}),
            ("channel.chat.message", "1", {"broadcaster_user_id": b, "user_id": b}),
            ("channel.subscribe", "1", {"broadcaster_user_id": b}),
            ("channel.subscription.message", "1", {"broadcaster_user_id": b}),
            ("channel.subscription.gift", "1", {"broadcaster_user_id": b}),
            ("channel.cheer", "1", {"broadcaster_user_id": b}),
            ("channel.follow", "2", {"broadcaster_user_id": b, "moderator_user_id": b}),
            ("channel.raid", "1", {"to_broadcaster_user_id": b}),
            ("channel.channel_points_custom_reward_redemption.add", "1", {"broadcaster_user_id": b}),
        ]

    async def _subscribe_all(self, session_id: str) -> None:
        self.subscribed, self.sub_errors = [], {}
        for event_type, version, condition in self._subscription_list():
            body = {
                "type": event_type,
                "version": version,
                "condition": condition,
                "transport": {"method": "websocket", "session_id": session_id},
            }
            status, data = await self.auth.request("POST", "eventsub/subscriptions", json_body=body)
            if status in (200, 202):
                self.subscribed.append(event_type)
            else:
                reason = (data or {}).get("message") if isinstance(data, dict) else None
                self.sub_errors[event_type] = f"HTTP {status} {reason or ''}".strip()
                log.warning("Abonnement Twitch refusé pour %s : %s", event_type, self.sub_errors[event_type])
        log.info("Événements Twitch : %d/%d abonnements actifs.", len(self.subscribed), len(self._subscription_list()))

    # ------------------------------------------------------------------ événements reçus

    async def _on_notification(self, kind: str, event: dict[str, Any]) -> None:
        db = self.bot.db  # type: ignore[attr-defined]
        if kind == "stream.online":
            if event.get("type", "live") == "live" and event.get("id"):
                self._stream_id = str(event["id"])
            return
        if kind == "channel.channel_points_custom_reward_redemption.add":
            await self._on_redemption(event)
            return

        stream_id = self.current_stream_id()
        if not stream_id:
            return  # hors live : rien à noter
        if kind == "channel.chat.message":
            text = ((event.get("message") or {}).get("text") or "").strip()
            if text and not text.startswith("!"):  # les commandes de bots ne comptent pas comme des messages
                await db.stream_chat_message(stream_id, event["chatter_user_login"].lower(), event["chatter_user_name"])
        elif kind == "channel.subscribe":
            await db.stream_add_event(
                stream_id, "sub", event["user_login"].lower(), event["user_name"], 0, "gift" if event.get("is_gift") else None
            )
        elif kind == "channel.subscription.message":
            await db.stream_add_event(
                stream_id, "resub", event["user_login"].lower(), event["user_name"], int(event.get("cumulative_months") or 0)
            )
        elif kind == "channel.subscription.gift":
            anonymous = bool(event.get("is_anonymous"))
            await db.stream_add_event(
                stream_id,
                "gift",
                "" if anonymous else event["user_login"].lower(),
                "" if anonymous else event["user_name"],
                int(event.get("total") or 1),
            )
        elif kind == "channel.cheer":
            anonymous = bool(event.get("is_anonymous"))
            await db.stream_add_event(
                stream_id,
                "cheer",
                "" if anonymous else event["user_login"].lower(),
                "" if anonymous else event["user_name"],
                int(event.get("bits") or 0),
            )
        elif kind == "channel.follow":
            await db.stream_add_event(stream_id, "follow", event["user_login"].lower(), event["user_name"])
        elif kind == "channel.raid":
            await db.stream_add_event(
                stream_id, "raid", event["from_broadcaster_user_login"].lower(), event["from_broadcaster_user_name"],
                int(event.get("viewers") or 0),
            )

    # ------------------------------------------------------------------ silencieux : personnes connectées au chat

    @tasks.loop(seconds=cfg.CHATTERS_POLL_SECONDS)
    async def chatters_loop(self) -> None:
        stream_id = self.current_stream_id()
        if not stream_id or not self.auth.authorized:
            return
        try:
            await self.poll_chatters(stream_id)
        except AuthError as exc:
            log.warning("Relevé des connectés au chat : %s", exc)
        except Exception:  # noqa: BLE001
            log.exception("Erreur inattendue pendant le relevé des connectés au chat")

    @chatters_loop.before_loop
    async def _before_chatters(self) -> None:
        await self.bot.wait_until_ready()

    async def poll_chatters(self, stream_id: str) -> int:
        people: list[tuple[str, str]] = []
        after: Optional[str] = None
        for _ in range(20):
            params: dict[str, Any] = {"broadcaster_id": self.auth.user_id, "moderator_id": self.auth.user_id, "first": 1000}
            if after:
                params["after"] = after
            status, body = await self.auth.request("GET", "chat/chatters", params=params)
            if status != 200:
                if not self._warned_chatters:
                    log.warning("Liste des connectés au chat refusée (HTTP %s) : autorisation à refaire ?", status)
                    self._warned_chatters = True
                return 0
            people += [(c["user_login"].lower(), c["user_name"]) for c in body.get("data", [])]
            after = (body.get("pagination") or {}).get("cursor")
            if not after:
                break
        if people:
            await self.bot.db.stream_mark_present(stream_id, people)  # type: ignore[attr-defined]
        return len(people)

    # ------------------------------------------------------------------ récompense « Vrai viewer »

    def find_member(self, text: str) -> tuple[Optional[discord.Member], str]:
        guild = self.bot.home_guild  # type: ignore[attr-defined]
        query = text.strip().lstrip("@").split("#")[0].strip().lower()
        if guild is None or not query:
            return None, "aucun pseudo Discord indiqué"
        people = [m for m in guild.members if not m.bot]
        exact = [m for m in people if m.name.lower() == query]
        if len(exact) == 1:
            return exact[0], ""
        loose = exact or [m for m in people if query in ((m.global_name or "").lower(), m.display_name.lower())]
        if len(loose) == 1:
            return loose[0], ""
        if loose:
            return None, "plusieurs membres portent ce nom : il faut écrire le nom d'utilisateur exact"
        return None, "aucun membre trouvé avec ce pseudo (est-il sur le serveur ?)"

    async def _settle(self, redemption_id: str, reward_id: str, status: str) -> None:
        """Valide (FULFILLED) ou rembourse (CANCELED) l'utilisation de points dans Twitch."""
        code, _ = await self.auth.request(
            "PATCH",
            "channel_points/custom_rewards/redemptions",
            params={"id": redemption_id, "broadcaster_id": self.auth.user_id, "reward_id": reward_id},
            json_body={"status": status},
        )
        if code != 200:
            log.warning("Impossible de passer la récompense en %s (HTTP %s).", status, code)

    async def _log_reward(self, title: str, text: str, color: int) -> None:
        await self.bot.send_log(discord.Embed(title=title, description=text, color=color))  # type: ignore[attr-defined]

    async def _on_redemption(self, event: dict[str, Any]) -> None:
        reward = event.get("reward") or {}
        reward_id = str(reward.get("id", ""))
        configured = self.bot.db.setting(REWARD_SETTING)  # type: ignore[attr-defined]
        if not configured or reward_id != configured:
            return
        redemption_id, who = event["id"], event.get("user_name", "?")
        title = f"🎟️ Récompense « {REWARD_TITLE} »"
        role = self.bot.role_setting("vrai_viewer")  # type: ignore[attr-defined]
        if role is None or not role.is_assignable():
            await self._log_reward(
                title,
                f"**{who}** a pris la récompense, mais le rôle « Vrai viewer » n'est pas configuré ou je ne peux pas l'attribuer "
                "(`/config role`, et mon rôle doit être au-dessus). La demande reste en attente dans Twitch.",
                COLOR_WARN,
            )
            return

        member, problem = self.find_member(event.get("user_input") or "")
        if member is None:
            await self._settle(redemption_id, reward_id, "CANCELED")
            await self._log_reward(title, f"**{who}** : {problem}. Points remboursés.", COLOR_WARN)
            return
        if role in member.roles:
            await self._settle(redemption_id, reward_id, "CANCELED")
            await self._log_reward(title, f"**{who}** : {member.mention} a déjà le rôle. Points remboursés.", COLOR_INFO)
            return
        try:
            await member.add_roles(role, reason=f"Récompense de points de chaîne ({who} sur Twitch)")
        except discord.HTTPException as exc:
            log.warning("Attribution du rôle Vrai viewer impossible : %s", exc)
            await self._log_reward(
                title, f"**{who}** : impossible de donner le rôle à {member.mention} ({exc}). La demande reste en attente.", COLOR_WARN
            )
            return
        await self._settle(redemption_id, reward_id, "FULFILLED")
        await self._log_reward(title, f"Twitch **{who}** → {member.mention} : rôle {role.mention} donné.", COLOR_OK)
        try:
            await member.send(f"🎉 Merci d'avoir échangé tes points ! Tu as maintenant le rôle **{role.name}** sur **{member.guild.name}**.")
        except discord.HTTPException:
            pass  # messages privés fermés


    # ------------------------------------------------------------------ page servie à OBS

    async def credits_token(self) -> str:
        token = self.bot.db.setting(TOKEN_SETTING)  # type: ignore[attr-defined]
        if not token:
            token = secrets.token_urlsafe(16)
            await self.bot.db.set_setting(TOKEN_SETTING, token)  # type: ignore[attr-defined]
        return token

    async def _start_web(self) -> None:
        await self.credits_token()
        app = web.Application(client_max_size=64 * 1024)
        app.router.add_get("/credits/{token}", self._web_credits)
        app.router.add_get("/credits/{token}/editor", self._web_editor)
        app.router.add_post("/credits/{token}/render", self._web_render)
        app.router.add_post("/credits/{token}/style", self._web_style)
        app.router.add_post("/credits/{token}/presets", self._web_preset_save)
        app.router.add_post("/credits/{token}/presets/delete", self._web_preset_delete)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        try:
            site = web.TCPSite(runner, "0.0.0.0", self.web_port)
            await site.start()
        except OSError as exc:
            log.warning("Page de crédits indisponible (port %s) : %s", self.web_port, exc)
            await runner.cleanup()
            return
        self._web_runner = runner
        self.web_bound_port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        log.info("Page de crédits servie sur le port %s.", self.web_bound_port)

    async def _web_credits(self, request: web.Request) -> web.Response:
        expected = self.bot.db.setting(TOKEN_SETTING) or ""  # type: ignore[attr-defined]
        if not secrets.compare_digest(request.match_info["token"], expected):
            raise web.HTTPNotFound()
        stream_id, info = await self._stream_for_credits()
        if stream_id and stream_id == self.current_stream_id() and self.auth.authorized:
            try:  # liste des connectés à jour au moment où la scène s'affiche
                await asyncio.wait_for(self.poll_chatters(stream_id), 5)
            except Exception:  # noqa: BLE001
                pass
        if stream_id:
            page, _ = await self.build_credits(stream_id, info)
        else:
            page = credits.render_credits(
                credits.Sections(), channel_name=self.bot.settings.twitch_channel, style=await self.load_style()  # type: ignore[attr-defined]
            )
        return web.Response(text=page, content_type="text/html", headers={"Cache-Control": "no-store"})

    # --- éditeur de style (même adresse secrète)

    def _check_token(self, request: web.Request) -> None:
        expected = self.bot.db.setting(TOKEN_SETTING) or ""  # type: ignore[attr-defined]
        if not secrets.compare_digest(request.match_info["token"], expected):
            raise web.HTTPNotFound()

    async def load_style(self) -> dict[str, Any]:
        raw = await self.bot.db.kv_get(STYLE_KEY)  # type: ignore[attr-defined]
        try:
            return credits.normalize_style(json.loads(raw) if raw else None)
        except ValueError:
            return credits.normalize_style(None)

    async def load_presets(self) -> dict[str, dict[str, Any]]:
        raw = await self.bot.db.kv_get(PRESETS_KEY)  # type: ignore[attr-defined]
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            return {}
        return {str(k)[: credits.PRESET_NAME_MAX]: credits.normalize_style(v) for k, v in list(data.items())[: credits.PRESET_LIMIT]}

    @staticmethod
    async def _json_body(request: web.Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except web.HTTPException:
            raise  # p. ex. 413 : corps trop gros
        except Exception:  # noqa: BLE001
            raise web.HTTPBadRequest(text=json.dumps({"error": "Requête invalide."}), content_type="application/json")
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text=json.dumps({"error": "Requête invalide."}), content_type="application/json")
        return body

    async def _web_editor(self, request: web.Request) -> web.Response:
        self._check_token(request)
        page = credits_editor.render_editor(await self.load_style(), await self.load_presets())
        return web.Response(text=page, content_type="text/html", headers={"Cache-Control": "no-store"})

    async def _web_render(self, request: web.Request) -> web.Response:
        self._check_token(request)
        body = await self._json_body(request)
        mode = "static" if body.get("mode") != "play" else "play"
        info = credits.DEMO_INFO
        page = credits.render_credits(
            credits.demo_sections(), channel_name=info["channel_name"], title=info.get("title", ""), games=info.get("games") or [],
            started_at=info.get("started_at"), style=credits.normalize_style(body.get("style")), mode=mode,
        )
        return web.Response(text=page, content_type="text/html", headers={"Cache-Control": "no-store"})

    async def _web_style(self, request: web.Request) -> web.Response:
        self._check_token(request)
        body = await self._json_body(request)
        style = credits.normalize_style(body.get("style"))
        await self.bot.db.kv_set(STYLE_KEY, json.dumps(style))  # type: ignore[attr-defined]
        return web.json_response({"style": style})

    async def _web_preset_save(self, request: web.Request) -> web.Response:
        self._check_token(request)
        body = await self._json_body(request)
        name = " ".join(str(body.get("name", "")).split())[: credits.PRESET_NAME_MAX]
        if not name:
            return web.json_response({"error": "Nom vide."}, status=400)
        if name in credits.PRESETS:
            return web.json_response({"error": "Ce nom est réservé à un préréglage fourni."}, status=400)
        presets = await self.load_presets()
        if name not in presets and len(presets) >= credits.PRESET_LIMIT:
            return web.json_response({"error": f"Maximum {credits.PRESET_LIMIT} préréglages."}, status=400)
        presets[name] = credits.normalize_style(body.get("style"))
        await self.bot.db.kv_set(PRESETS_KEY, json.dumps(presets))  # type: ignore[attr-defined]
        return web.json_response({"presets": presets})

    async def _web_preset_delete(self, request: web.Request) -> web.Response:
        self._check_token(request)
        body = await self._json_body(request)
        presets = await self.load_presets()
        presets.pop(str(body.get("name", "")), None)
        await self.bot.db.kv_set(PRESETS_KEY, json.dumps(presets))  # type: ignore[attr-defined]
        return web.json_response({"presets": presets})

    async def _stream_for_credits(self) -> tuple[Optional[str], dict[str, Any]]:
        """Le live en cours s'il y en a un, sinon le dernier live enregistré."""
        twitch = self.bot.get_cog("Twitch")
        stream_id = self.current_stream_id()
        if stream_id and twitch is not None and twitch.state.get("stream_id") == stream_id:  # type: ignore[attr-defined]
            st = twitch.state  # type: ignore[attr-defined]
            return stream_id, {"title": st.get("title", ""), "games": st.get("games", []), "started_at": st.get("started_at")}
        if stream_id:  # live détecté par EventSub avant l'annonce
            return stream_id, {}
        raw = await self.bot.db.kv_get(LAST_STREAM_KEY)  # type: ignore[attr-defined]
        last = json.loads(raw) if raw else {}
        return last.get("stream_id"), last

    # ------------------------------------------------------------------ crédits

    async def build_credits(self, stream_id: str, info: dict[str, Any]) -> tuple[str, credits.Sections]:
        db = self.bot.db  # type: ignore[attr-defined]
        login = self.bot.settings.twitch_channel  # type: ignore[attr-defined]
        sections = credits.build_sections(await db.stream_chatters(stream_id), await db.stream_events(stream_id), self.bots(), login)
        name = login
        twitch = self.bot.get_cog("Twitch")
        if twitch is not None:
            user = await twitch._get_user()  # type: ignore[attr-defined]
            name = (user or {}).get("display_name") or login
        page = credits.render_credits(
            sections, channel_name=name, title=info.get("title", ""), games=info.get("games") or [], started_at=info.get("started_at"),
            style=await self.load_style(),
        )
        return page, sections

    @staticmethod
    def _file_name(info: dict[str, Any]) -> str:
        day = (info.get("started_at") or utcnow().isoformat())[:10]
        return f"credits-{day}.html"

    async def stream_ended(self, stream_id: str, info: dict[str, Any]) -> None:
        """Appelé par le module Twitch quand le live est terminé : génère et envoie les crédits."""
        if self._stream_id == stream_id:
            self._stream_id = None
        db = self.bot.db  # type: ignore[attr-defined]
        await db.kv_set(LAST_STREAM_KEY, json.dumps({**info, "stream_id": stream_id}))
        try:
            page, sections = await self.build_credits(stream_id, info)
            if sections.is_empty:
                log.info("Live terminé : aucune donnée pour les crédits (autorisation Twitch faite ?).")
            else:
                channel = self.bot.channel_setting("credits")  # type: ignore[attr-defined]
                if channel is not None:  # archive du live ; la page d'OBS, elle, est toujours à jour
                    await channel.send(
                        self._summary(sections),
                        file=discord.File(io.BytesIO(page.encode("utf-8")), filename=self._file_name(info)),
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
        except discord.HTTPException as exc:
            log.warning("Envoi des crédits impossible : %s", exc)
        except Exception:  # noqa: BLE001
            log.exception("Erreur pendant la génération des crédits")
        cutoff = (utcnow() - timedelta(days=cfg.CREDITS_RETENTION_DAYS)).isoformat(timespec="seconds")
        await db.stream_purge(cutoff)

    @staticmethod
    def _summary(sections: credits.Sections) -> str:
        return (
            "📜 **Crédits du live** (archive, à ouvrir dans un navigateur).\n"
            f"{len(sections.subs)} abonné(s) · {len(sections.chatters)} personne(s) au chat "
            f"({sections.total_messages} messages) · {len(sections.silent)} de l'ombre · {len(sections.follows)} nouveau(x) follower(s)"
        )

    # ------------------------------------------------------------------ commandes

    @stream_group.command(name="autoriser", description="Autorise le bot à lire ta chaîne Twitch (une seule fois)")
    @app_commands.describe(code="Le code ou l'adresse complète affichée dans le navigateur après l'autorisation")
    async def autoriser(self, interaction: discord.Interaction, code: Optional[str] = None) -> None:
        if code is None:
            await interaction.response.send_message(
                "**Autorisation Twitch** (à faire une seule fois)\n"
                f"1. Ouvre ce lien **avec le compte de ta chaîne** et clique sur *Autoriser* :\n{self.auth.authorize_url()}\n"
                "2. Le navigateur affiche ensuite une page d'erreur : c'est normal. **Copie l'adresse complète** de cette page "
                "(elle commence par `https://localhost/?code=`).\n"
                "3. Colle-la ici : `/stream autoriser code:<adresse copiée>`",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            login = await self.auth.exchange_code(code)
        except AuthError as exc:
            await interaction.followup.send(f"❌ Autorisation refusée : {exc}", ephemeral=True)
            return
        self._wake.set()
        self.restart_connection()
        await interaction.followup.send(
            f"✅ Compte **{login}** autorisé. Connexion aux événements Twitch en cours (`/stream statut` pour vérifier).",
            ephemeral=True,
        )

    @stream_group.command(name="statut", description="État de la connexion Twitch, de la récompense et du live en cours")
    async def statut(self, interaction: discord.Interaction) -> None:
        lines: list[str] = []
        if not self.auth.authorized:
            lines.append("❌ Bot non autorisé : `/stream autoriser`.")
        else:
            lines.append(f"✅ Autorisé avec le compte **{self.auth.login}**.")
            if self.auth.missing_scopes:
                lines.append(f"⚠️ Droits manquants : {', '.join(self.auth.missing_scopes)} → refais `/stream autoriser`.")
            lines.append(
                f"{'✅' if self.connected else '❌'} Connexion aux événements : {'active' if self.connected else 'coupée'} "
                f"({len(self.subscribed)}/{len(self._subscription_list())} abonnements)"
            )
            for kind, reason in self.sub_errors.items():
                lines.append(f"   ⚠️ {kind} : {reason}")
        reward = self.bot.db.setting(REWARD_SETTING)  # type: ignore[attr-defined]
        role = self.bot.role_setting("vrai_viewer")  # type: ignore[attr-defined]
        lines.append(f"🎟️ Récompense : {'créée' if reward else '⚠️ non créée (`/stream recompense`)'}")
        lines.append(f"🏷️ Rôle « Vrai viewer » : {role.mention if role else '⚠️ non défini (`/config role`)'}")
        stream_id = self.current_stream_id()
        if stream_id:
            chatters = await self.bot.db.stream_chatters(stream_id)  # type: ignore[attr-defined]
            lines.append(
                f"🔴 Live en cours : {sum(1 for c in chatters if c['messages'])} personne(s) au chat, "
                f"{sum(1 for c in chatters if c['present'] and not c['messages'])} en silence"
            )
        channel = self.bot.channel_setting("credits")  # type: ignore[attr-defined]
        lines.append(f"📜 Archive des crédits après chaque live : {channel.mention if channel else 'désactivée (`/config salon` → Crédits)'}")
        lines.append(f"🌐 Page pour OBS : {'port ' + str(self.web_bound_port) if self.web_bound_port else '⚠️ indisponible'} (`/stream lien`)")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @stream_group.command(name="recompense", description="Crée (ou met à jour le prix de) la récompense de points « Vrai viewer »")
    @app_commands.describe(prix="Coût en points de chaîne")
    async def recompense(self, interaction: discord.Interaction, prix: app_commands.Range[int, 1, 10_000_000]) -> None:
        await interaction.response.defer(ephemeral=True)
        if not self.auth.authorized:
            await interaction.followup.send("❌ Autorise d'abord le bot : `/stream autoriser`.", ephemeral=True)
            return
        try:
            existing = self.bot.db.setting(REWARD_SETTING)  # type: ignore[attr-defined]
            if existing:
                status, _ = await self.auth.request(
                    "PATCH", "channel_points/custom_rewards",
                    params={"broadcaster_id": self.auth.user_id, "id": existing}, json_body={"cost": prix},
                )
                if status == 200:
                    await interaction.followup.send(f"✅ Prix de la récompense mis à jour : **{prix}** points.", ephemeral=True)
                    return
                if status != 404:
                    await interaction.followup.send(f"❌ Twitch a refusé la mise à jour (HTTP {status}).", ephemeral=True)
                    return
            status, body = await self.auth.request(
                "POST", "channel_points/custom_rewards", params={"broadcaster_id": self.auth.user_id},
                json_body={
                    "title": REWARD_TITLE, "cost": prix, "prompt": REWARD_PROMPT, "is_enabled": True,
                    "is_user_input_required": True, "background_color": "#9146FF",
                },
            )
        except AuthError as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
            return
        if status == 200 and (body or {}).get("data"):
            await self.bot.db.set_setting(REWARD_SETTING, body["data"][0]["id"])  # type: ignore[attr-defined]
            extra = "" if self.bot.role_setting("vrai_viewer") else "\n⚠️ Pense à créer le rôle puis `/config role` → Vrai viewer."  # type: ignore[attr-defined]
            await interaction.followup.send(f"✅ Récompense « {REWARD_TITLE} » créée à **{prix}** points.{extra}", ephemeral=True)
            return
        message = (body or {}).get("message", "") if isinstance(body, dict) else ""
        hint = " Une récompense du même nom existe déjà : supprime-la ou renomme-la dans Twitch." if "DUPLICATE" in message.upper() else ""
        await interaction.followup.send(f"❌ Création refusée (HTTP {status}) : {message or 'raison inconnue'}.{hint}", ephemeral=True)

    @stream_group.command(name="credits", description="Génère la page de crédits du live en cours (ou du dernier live)")
    async def credits_cmd(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        stream_id, info = await self._stream_for_credits()
        if not stream_id:
            await interaction.followup.send("Aucun live enregistré pour le moment.", ephemeral=True)
            return
        page, sections = await self.build_credits(stream_id, info)
        if sections.is_empty:
            await interaction.followup.send("Aucune donnée pour ce live (le bot est-il autorisé avec `/stream autoriser` ?).", ephemeral=True)
            return
        await interaction.followup.send(
            self._summary(sections), file=discord.File(io.BytesIO(page.encode("utf-8")), filename=self._file_name(info)), ephemeral=True
        )

    @stream_group.command(name="lien", description="Adresse de la page de crédits à mettre dans OBS")
    @app_commands.describe(regenerer="Crée une nouvelle adresse (l'ancienne cesse de fonctionner)")
    async def lien(self, interaction: discord.Interaction, regenerer: bool = False) -> None:
        if regenerer:
            await self.bot.db.delete_setting(TOKEN_SETTING)  # type: ignore[attr-defined]
        token = await self.credits_token()
        port = self.web_bound_port or self.web_port
        await interaction.response.send_message(
            "**Page de crédits pour OBS** : source *Navigateur*, adresse\n"
            f"`http://ADRESSE-DU-VPS:{port}/credits/{token}`\n"
            "Remplace `ADRESSE-DU-VPS` par l'adresse IP de ton serveur. Largeur 1920, hauteur 1080, et coche "
            "**« Actualiser le navigateur quand la scène devient active »** : les crédits se relancent, à jour, à chaque "
            "affichage de la scène de fin. Garde cette adresse pour toi.\n\n"
            f"**Personnaliser** (couleurs, police, défilement, rubriques, textes) : `http://ADRESSE-DU-VPS:{port}/credits/{token}/editor`",
            ephemeral=True,
        )

    @stream_group.command(name="bot-ajouter", description="Ajoute un compte (bot) à ne jamais remercier dans les crédits")
    @app_commands.describe(pseudo="Pseudo Twitch du bot")
    async def bot_add(self, interaction: discord.Interaction, pseudo: str) -> None:
        login = pseudo.strip().lstrip("@").lower()
        if not login:
            await interaction.response.send_message("Pseudo vide.", ephemeral=True)
            return
        extra = {b for b in (self.bot.db.setting("credits.bots") or "").split(",") if b}  # type: ignore[attr-defined]
        extra.add(login)
        await self.bot.db.set_setting("credits.bots", ",".join(sorted(extra)))  # type: ignore[attr-defined]
        await interaction.response.send_message(f"✅ **{login}** ne sera plus remercié dans les crédits.", ephemeral=True)

    @stream_group.command(name="bot-retirer", description="Retire un compte de la liste des bots ajoutés à la main")
    @app_commands.describe(pseudo="Pseudo Twitch")
    async def bot_remove(self, interaction: discord.Interaction, pseudo: str) -> None:
        login = pseudo.strip().lstrip("@").lower()
        extra = {b for b in (self.bot.db.setting("credits.bots") or "").split(",") if b}  # type: ignore[attr-defined]
        if login not in extra:
            note = " (c'est un bot connu par défaut, il ne peut pas être retiré)" if login in cfg.DEFAULT_BOTS else ""
            await interaction.response.send_message(f"**{login}** n'est pas dans ta liste{note}.", ephemeral=True)
            return
        extra.discard(login)
        await self.bot.db.set_setting("credits.bots", ",".join(sorted(extra)))  # type: ignore[attr-defined]
        await interaction.response.send_message(f"✅ **{login}** retiré de la liste.", ephemeral=True)

    @stream_group.command(name="bots", description="Liste des comptes exclus des crédits")
    async def bots_list(self, interaction: discord.Interaction) -> None:
        custom = sorted(b for b in (self.bot.db.setting("credits.bots") or "").split(",") if b)  # type: ignore[attr-defined]
        text = "**Par défaut :** " + ", ".join(cfg.DEFAULT_BOTS) + "\n**Ajoutés par toi :** " + (", ".join(custom) or "aucun")
        await interaction.response.send_message(truncate(text, 1900), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StreamEvents(bot))

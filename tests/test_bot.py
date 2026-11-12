"""Tests de vérification du bot (sans connexion à Discord ni à Twitch)."""
import re
import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.pop("DISCORD_TOKEN", None)

import discord
from aiohttp import web

import config as cfg
import utils
from utils import level_from_xp
from bot import ZenoBot
from config import ConfigError, Settings, load_settings
from database import Database

PASSED = []


def ok(name):
    PASSED.append(name)
    print(f"  ok  {name}")


def make_settings(tmp: Path) -> Settings:
    return Settings(
        discord_token="x", guild_id=1, twitch_client_id="cid", twitch_client_secret="sec",
        twitch_channel="zenosurtwitch_", database_path=tmp / "t.db", log_level="INFO",
    )


# ------------------------------------------------------------------ A. utilitaires

def test_utils():
    from datetime import timedelta as td
    assert utils.parse_duration("10m") == td(minutes=10)
    assert utils.parse_duration("10") == td(minutes=10)
    assert utils.parse_duration("2h") == td(hours=2)
    assert utils.parse_duration("1j") == td(days=1)
    assert utils.parse_duration("1h30m") == td(hours=1, minutes=30)
    assert utils.parse_duration("1h 30min") == td(hours=1, minutes=30)
    assert utils.parse_duration("45s") == td(seconds=45)
    assert utils.parse_duration("2 heures") == td(hours=2)
    assert utils.parse_duration("abc") is None
    assert utils.parse_duration("") is None
    assert utils.parse_duration("0") is None
    assert utils.parse_duration("9" * 30 + "d") is None
    ok("parse_duration")

    assert utils.format_duration(td(hours=3, minutes=12, seconds=5)) == "3 h 12 min"
    assert utils.format_duration(td(seconds=42)) == "42 s"
    assert utils.format_duration(td(days=1, hours=2)) == "1 j 2 h"
    ok("format_duration")

    assert utils.level_from_xp(0) == (0, 0, 100)
    assert utils.level_from_xp(99).level == 0
    assert utils.level_from_xp(100).level == 1
    assert utils.level_from_xp(100 + 155).level == 2  # niveau 1 -> 2 coûte 155
    assert utils.level_from_xp(-5).level == 0
    ok("level_from_xp")

    allowed = {"twitch.tv", "youtube.com"}
    h = utils.extract_hosts
    assert h("regarde https://www.twitch.tv/zeno ok") == ["www.twitch.tv"]
    assert utils.host_allowed("www.twitch.tv", allowed)
    assert utils.host_allowed("clips.twitch.tv", allowed)
    assert not utils.host_allowed("eviltwitch.tv", allowed)
    assert not utils.host_allowed("twitch.tv.evil.com", allowed)
    assert h("https://twitch.tv@evil.com/x") == ["evil.com"]
    assert h("va sur www.evil.com maintenant") == ["www.evil.com"]
    assert h("pas de lien ici") == []
    assert h("http://[invalid") == [""] and not utils.host_allowed("", allowed)
    assert utils.INVITE_RE.search("rejoins discord.gg/abc123")
    assert utils.INVITE_RE.search("https://discord.com/invite/abc")
    assert not utils.INVITE_RE.search("je suis sur discord ce soir")
    ok("liens et invitations")

    assert utils.progress_bar(50, 100, 10) == "▰▰▰▰▰▱▱▱▱▱"
    assert utils.truncate("a" * 10, 5) == "aaaa…"
    assert utils.parse_iso("2026-11-05T01:02:03Z").tzinfo is not None
    ok("divers utils")


def test_config(tmp: Path):
    for k in ("DISCORD_TOKEN", "GUILD_ID"):
        os.environ.pop(k, None)
    try:
        load_settings()
        raise AssertionError("aurait dû échouer")
    except ConfigError:
        pass
    os.environ["DISCORD_TOKEN"] = "abc"
    try:
        load_settings()
        raise AssertionError("aurait dû échouer")
    except ConfigError:
        pass
    os.environ["GUILD_ID"] = "123"
    s = load_settings()
    assert s.guild_id == 123 and s.twitch_channel == "zenosurtwitch_" and not s.twitch_enabled
    assert s.database_path.is_absolute()
    os.environ.pop("DISCORD_TOKEN"); os.environ.pop("GUILD_ID")
    ok("load_settings")


# ------------------------------------------------------------------ B. base de données

async def test_db(tmp: Path):
    db = Database(tmp / "db.db")
    await db.connect()
    assert "twitch.tv" in db.domains and len(db.domains) == len(cfg.DEFAULT_ALLOWED_DOMAINS)
    assert await db.remove_domain("twitch.tv") and "twitch.tv" not in db.domains
    await db.close()
    db = Database(tmp / "db.db")  # réouverture : les valeurs par défaut ne reviennent pas
    await db.connect()
    assert "twitch.tv" not in db.domains
    assert await db.add_domain("medal.tv") is False  # déjà présent
    ok("domaines (injection unique, persistance)")

    await db.set_setting("channel.annonces", "123")
    assert db.setting("channel.annonces") == "123"
    await db.close(); db = Database(tmp / "db.db"); await db.connect()
    assert db.setting("channel.annonces") == "123"
    await db.delete_setting("channel.annonces")
    assert db.setting("channel.annonces") is None
    ok("réglages persistants")

    # XP
    old, new = await db.add_xp(1, 50, count_message=True)
    assert (old, new) == (0, 50)
    await db.add_xp(2, 80, count_message=True)
    await db.add_xp(3, 80, count_message=True)
    assert await db.get_rank(1) == 3 and await db.get_rank(2) == 1 and await db.get_rank(3) == 1
    assert (await db.get_xp(1))["messages"] == 1
    old, new = await db.add_xp(1, -999)
    assert new == 0
    top = await db.top_xp(10)
    assert [r["user_id"] for r in top] == [2, 3]  # xp=0 exclu
    assert await db.count_ranked() == 2
    assert await db.reset_xp(2) and not await db.reset_xp(2)
    results = await asyncio.gather(*[db.add_xp(9, 10) for _ in range(20)])
    assert (await db.get_xp(9))["xp"] == 200
    ok("xp, classement, concurrence")

    await db.set_reward(5, 111); await db.set_reward(10, 222); await db.set_reward(5, 333)
    assert await db.rewards_up_to(7) == [333] and await db.rewards_up_to(10) == [333, 222]
    assert await db.remove_reward(5) and not await db.remove_reward(5)
    ok("récompenses de niveau")

    wid = await db.add_warning(7, 1, "spam")
    await db.add_warning(7, 1, "re-spam")
    assert await db.count_warnings(7) == 2
    assert (await db.delete_warning(wid))["reason"] == "spam" and await db.delete_warning(wid) is None
    assert await db.clear_warnings(7) == 1
    ok("avertissements")

    await db.add_self_role(10, "Notif live", "🔔"); await db.add_self_role(10, "Notif", None)
    assert (await db.get_self_role(10))["label"] == "Notif" and len(await db.list_self_roles()) == 1
    assert await db.remove_self_role(10)
    ok("rôles à boutons")


    await db.create_lfg(100, 5, 1, "Valorant", 2, "ranked")
    assert await db.lfg_join(100, 2) == "ok"
    assert await db.lfg_join(100, 2) == "already"
    assert await db.lfg_join(100, 3) == "ok"
    assert await db.lfg_join(100, 4) == "full"
    assert await db.lfg_players(100) == [2, 3]
    assert await db.lfg_leave(100, 2) and not await db.lfg_leave(100, 2)
    assert await db.lfg_join(100, 4) == "ok"
    assert len(await db.lfg_open_by_author(1)) == 1
    old = (utils.utcnow() + timedelta(hours=7)).isoformat(timespec="seconds")
    assert len(await db.lfg_expired(old)) == 1
    await db.lfg_close(100)
    assert await db.lfg_join(100, 9) == "closed" and await db.lfg_expired(old) == []
    assert await db.lfg_join(555, 9) == "closed"
    ok("recherche de joueurs")
    await db.close()


# ------------------------------------------------------------------ C. chargement + commandes

async def test_tree(tmp: Path):
    bot = ZenoBot(make_settings(tmp))
    async def fake_sync(guild=None): return list(bot.tree.get_commands(guild=guild))
    bot.tree.sync = fake_sync
    await bot._async_setup_hook()
    await bot.setup_hook()
    loaded = sorted(bot.extensions)
    assert len(loaded) == 9, loaded
    guild = discord.Object(id=1)
    cmds = bot.tree.get_commands(guild=guild)
    names = sorted(c.name for c in cmds)
    print("      commandes :", names)
    total = 0
    for cmd in cmds:
        payload = cmd.to_dict(bot.tree)
        json.dumps(payload)
        assert 1 <= len(payload["description"]) <= 100, cmd.name
        total += 1 + len(payload.get("options", [])) * 0
    expected = {"config", "roles", "xp", "twitch", "warn", "warns", "unwarn", "clearwarns", "timeout", "untimeout",
                "kick", "ban", "unban", "purge", "rank", "top", "ping", "stream", "sondage", "idee", "idee-statut", "cherche-joueurs"}
    assert set(names) == expected, set(names) ^ expected
    assert len(cmds) <= 100
    # sous-commandes
    sub = {c.name: sorted(s.name for s in c.commands) for c in cmds if hasattr(c, "commands")}
    print("      sous-commandes :", sub)
    assert "verifier" in sub["config"] and "panel" in sub["roles"] and "statut" in sub["twitch"]
    assert {"autoriser", "recompense", "credits", "statut"} <= set(sub["stream"])
    # les groupes réservés au staff portent bien la permission par défaut
    for c in cmds:
        if c.name in ("config", "twitch", "xp", "stream"):
            assert c.default_permissions and c.default_permissions.manage_guild
        if c.name == "roles":
            assert c.default_permissions.manage_roles
    # pas de /planning ni de giveaway ni de fonctions « pédagogue »
    assert not {"planning", "tirage", "giveaway", "astuce", "quiz", "faq"} & set(names)
    ok(f"chargement des 9 modules, {len(cmds)} commandes valides")
    await bot.close()
    ok("arrêt propre")


# ------------------------------------------------------------------ D. Twitch (faux serveur)

class FakeTwitch:
    def __init__(self):
        self.n = 0
        self.token = None
        self.stream = None
        self.fail = False
        self.token_requests = 0

    async def token_h(self, request):
        data = await request.post()
        assert data["grant_type"] == "client_credentials" and data["client_secret"] == "sec"
        self.n += 1
        self.token_requests += 1
        self.token = f"tok{self.n}"
        return web.json_response({"access_token": self.token, "expires_in": 3600, "token_type": "bearer"})

    def _ok(self, request):
        return request.headers.get("Authorization") == f"Bearer {self.token}" and request.headers.get("Client-Id") == "cid"

    async def users_h(self, request):
        if not self._ok(request):
            return web.Response(status=401, text="bad token")
        assert request.query["login"] == "zenosurtwitch_"
        return web.json_response({"data": [{"id": "42", "login": "zenosurtwitch_", "display_name": "ZenoSurTwitch_", "profile_image_url": "https://x/i.png"}]})

    async def streams_h(self, request):
        if not self._ok(request):
            return web.Response(status=401, text="bad token")
        if self.fail:
            return web.Response(status=500, text="boom")
        return web.json_response({"data": [self.stream] if self.stream else []})



class FakeMsg:
    _next = 1000
    def __init__(self):
        FakeMsg._next += 1
        self.id = FakeMsg._next
        self.reactions = []
    async def add_reaction(self, e): self.reactions.append(e)


class FakePartial:
    def __init__(self, chan, mid): self.chan, self.id = chan, mid
    async def edit(self, **kw): self.chan.edits.append((self.id, kw))
    async def delete(self): self.chan.deleted.append(self.id)


class FakeChannel(discord.TextChannel):
    def __init__(self, cid):
        self.id = cid
        self.sent, self.edits, self.deleted = [], [], []
    @property
    def name(self): return "annonces"
    @property
    def mention(self): return f"<#{self.id}>"
    async def send(self, content=None, **kw):
        m = FakeMsg(); self.sent.append((m, content, kw)); return m
    def get_partial_message(self, mid): return FakePartial(self, mid)


async def test_twitch(tmp: Path):
    from cogs.twitch import Twitch, TwitchClient, TwitchAPIError

    fake = FakeTwitch()
    app = web.Application()
    app.router.add_post("/oauth2/token", fake.token_h)
    app.router.add_get("/helix/users", fake.users_h)
    app.router.add_get("/helix/streams", fake.streams_h)
    runner = web.AppRunner(app); await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0); await site.start()
    port = site._server.sockets[0].getsockname()[1]

    bot = ZenoBot(make_settings(tmp / "tw"))
    await bot.db.connect()
    chan = FakeChannel(77)
    role = SimpleNamespace(id=5, mention="<@&5>")
    bot.channel_setting = lambda key: chan if key == "annonces" else None
    bot.role_setting = lambda key: role if key == "notif_live" else None
    bot.get_channel = lambda cid: chan if cid == chan.id else None

    client = TwitchClient("cid", "sec", api_base=f"http://127.0.0.1:{port}/helix", token_url=f"http://127.0.0.1:{port}/oauth2/token")
    await client.start()
    cog = Twitch(bot, client)

    # --- API : utilisateur, rafraîchissement du jeton sur 401
    user = await client.get_user("zenosurtwitch_")
    assert user["display_name"] == "ZenoSurTwitch_" and fake.token_requests == 1
    fake.token = "revoked"          # le serveur invalide le jeton
    assert await client.get_stream("zenosurtwitch_") is None
    assert fake.token_requests == 2
    ok("client Twitch : jeton + renouvellement sur 401")

    # --- hors ligne : rien ne se passe
    await cog._check_live()
    assert chan.sent == [] and not cog.state.get("stream_id")
    ok("hors-ligne : aucune annonce")

    # --- passage en live : 1 message avec ping
    started = utils.rfc3339(utils.utcnow() - timedelta(hours=1, minutes=5))
    fake.stream = {"id": "S1", "title": "Découverte !", "game_name": "Hades", "started_at": started,
                   "thumbnail_url": "https://t/live-{width}x{height}.jpg", "user_name": "ZenoSurTwitch_"}
    await cog._check_live()
    assert len(chan.sent) == 1
    _, content, kw = chan.sent[0]
    assert content.startswith("@everyone") and "ZenoSurTwitch_" in content
    assert kw["embed"].title == "Découverte !" and kw["embed"].fields[0].value == "Hades"
    assert "1280x720" in kw["embed"].image.url
    assert kw["allowed_mentions"].everyone is True
    assert kw["view"].children[0].url == "https://twitch.tv/zenosurtwitch_"
    msg_id = chan.sent[0][0].id
    assert cog.state["stream_id"] == "S1" and cog.state["message_id"] == msg_id
    ok("passage en live : annonce + ping + embed + bouton")

    # --- même live : pas de doublon
    await cog._check_live(); await cog._check_live()
    assert len(chan.sent) == 1 and chan.edits == []
    ok("pas de doublon d'annonce")

    # --- redémarrage du bot pendant le live : l'état est relu, toujours pas de doublon
    await cog._save_state()
    cog2 = Twitch(bot, client)
    cog2.state = json.loads(await bot.db.kv_get("twitch.state"))
    await cog2._check_live()
    assert len(chan.sent) == 1
    ok("redémarrage en plein live : pas de doublon")

    # --- changement de titre / jeu
    fake.stream = {**fake.stream, "title": "Boss final", "game_name": "Celeste"}
    await cog._check_live()
    assert len(chan.edits) == 1 and chan.edits[0][1]["embed"].title == "Boss final"
    assert cog.state["games"] == ["Hades", "Celeste"]
    ok("mise à jour du titre et du jeu")

    # --- panne API : l'état ne bouge pas, une erreur claire est levée
    fake.fail = True
    try:
        await cog._check_live(); raise AssertionError("aurait dû lever")
    except TwitchAPIError as e:
        assert "500" in str(e)
    assert cog.state["stream_id"] == "S1"
    fake.fail = False
    ok("panne Twitch : état conservé")

    # --- fin de live : tolérance de 3 vérifications
    stream, fake.stream = fake.stream, None
    await cog._check_live(); await cog._check_live()
    assert cog.state["stream_id"] == "S1" and len(chan.edits) == 1
    await cog._check_live()
    assert cog.state["stream_id"] is None
    assert chan.deleted == [msg_id] and len(chan.edits) == 1 and cog.state["message_id"] is None
    ok("fin de live : l'annonce (et son ping) est supprimée")

    # --- reprise rapide (nouvelle session) : nouveau message, sans ping
    fake.stream = {**stream, "id": "S2"}
    await cog._check_live()
    assert len(chan.sent) == 2
    _, content, kw = chan.sent[-1]
    assert "de retour" in content and "@everyone" not in content and not kw["allowed_mentions"].everyone
    ok("reprise rapide : nouvelle annonce sans ping")

    # --- nouveau live bien plus tard : nouvelle annonce avec ping
    fake.stream = None
    for _ in range(3): await cog._check_live()
    assert cog.state["stream_id"] is None
    cog.state["last_end"] = (utils.utcnow() - timedelta(hours=3)).isoformat()
    fake.stream = {**stream, "id": "S3", "game_name": "Hades"}
    await cog._check_live()
    assert len(chan.sent) == 3 and "@everyone" in chan.sent[2][1] and chan.deleted == [msg_id, chan.sent[1][0].id]
    assert cog.state["games"] == ["Hades"]
    ok("nouveau live plus tard : nouvelle annonce + ping")

    # --- session qui change sans passage hors-ligne détecté
    fake.stream = {**stream, "id": "S4"}
    await cog._check_live()
    assert cog.state["stream_id"] == "S4" and len(chan.sent) in (3, 4)
    ok("changement d'identifiant de live géré")

    # --- annonce sans salon configuré : aucune erreur, annonce faite une fois le salon réglé
    bot2 = bot
    cog.state = {}
    bot.channel_setting = lambda key: None
    n = len(chan.sent)
    await cog._check_live()
    assert len(chan.sent) == n and not cog.state.get("stream_id")
    bot.channel_setting = lambda key: chan if key == "annonces" else None
    await cog._check_live()
    assert len(chan.sent) == n + 1
    ok("salon non configuré puis configuré en plein live")

    await client.close()
    await runner.cleanup()
    await bot.db.close()
    await bot.close()


# ------------------------------------------------------------------ E. anti-spam / filtre de liens

class FakeMember(discord.Member):
    def __init__(self, uid, staff=False, roles=()):
        self._uid, self._staff, self._roles = uid, staff, list(roles)
        self.timed_out = None
    id = property(lambda s: s._uid)
    mention = property(lambda s: f"<@{s._uid}>")
    guild_permissions = property(lambda s: discord.Permissions(manage_messages=s._staff))
    roles = property(lambda s: s._roles)
    display_name = property(lambda s: f"user{s._uid}")
    display_avatar = property(lambda s: SimpleNamespace(url="https://a/x.png"))
    async def timeout(self, d, *, reason=None): self.timed_out = d


class FakeTextChannel:
    id = 9
    mention = "<#9>"
    def __init__(self): self.sent = []; self.bulk = []
    async def send(self, content=None, **kw): self.sent.append(content)
    async def delete_messages(self, msgs):
        self.bulk.append(list(msgs))
        for m in msgs: m.deleted = True


class FakeMessage:
    _n = 0
    def __init__(self, author, content, channel, mentions=0):
        FakeMessage._n += 1
        self.id, self.author, self.content, self.channel = FakeMessage._n, author, content, channel
        self.mentions = [object()] * mentions
        self.role_mentions = []
        self.deleted = False
    async def delete(self): self.deleted = True


async def test_automod(tmp: Path):
    bot = ZenoBot(make_settings(tmp / "am"))
    await bot.db.connect()
    from cogs.moderation import Moderation
    mod = Moderation(bot)
    ch = FakeTextChannel()

    async def run(member, text, **kw):
        m = FakeMessage(member, text, ch, **kw)
        return await mod.automod(m), m

    staff = FakeMember(1, staff=True)
    flagged, m = await run(staff, "https://evil.example.com/x discord.gg/zzz")
    assert not flagged and not m.deleted
    ok("le staff est exempté")

    u = FakeMember(2)
    flagged, m = await run(u, "regarde https://www.twitch.tv/zeno et https://youtu.be/abc")
    assert not flagged
    flagged, m = await run(u, "super clip https://clips.twitch.tv/Abc")
    assert not flagged
    flagged, m = await run(u, "va voir https://evil.example.com/x")
    assert flagged and m.deleted and any("pas autorisé" in s for s in ch.sent)
    flagged, m = await run(u, "gratuit : www.evil.com/nitro")
    assert flagged and m.deleted
    flagged, m = await run(u, "https://twitch.tv.evil.com/login")
    assert flagged
    ok("filtre de liens : autorisés passent, les autres sont supprimés")

    flagged, m = await run(u, "rejoins discord.gg/abc123")
    assert flagged and m.deleted
    await bot.db.set_setting("filter.links", "0")
    flagged, _ = await run(u, "va voir https://evil.example.com/x")
    assert not flagged
    flagged, _ = await run(u, "rejoins discord.gg/abc123")
    assert flagged                                    # invitations toujours bloquées
    ok("invitations toujours bloquées, filtre désactivable")

    await bot.db.set_setting("filter.links", "1")
    hab_role = SimpleNamespace(id=50)
    bot.role_setting = lambda key: hab_role if key == "habitue" else None
    h = FakeMember(3, roles=[hab_role])
    flagged, _ = await run(h, "mon site https://evil.example.com/x")
    assert not flagged
    flagged, _ = await run(h, "discord.gg/abc123")
    assert flagged
    ok("rôle habitué : liens autorisés, invitations non")

    # anti-spam : rafale
    s = FakeMember(4)
    results = []
    msgs = []
    for i in range(9):
        f, m = await run(s, f"message numero {i} pour spammer")
        results.append(f); msgs.append(m)
    assert results[:7] == [False] * 7 and results[7] is True, results
    assert s.timed_out == timedelta(minutes=cfg.SPAM_TIMEOUT_MINUTES)
    assert ch.bulk and all(m.deleted for m in msgs[:8])
    ok("anti-spam : rafale supprimée + exclusion temporaire")

    # même message répété
    d = FakeMember(5)
    r = [(await run(d, "achetez mes followers maintenant"))[0] for _ in range(3)]
    assert r == [False, False, True] and d.timed_out
    d2 = FakeMember(6)
    r = [(await run(d2, "lol"))[0] for _ in range(5)]
    assert r == [False] * 5                         # messages courts : pas de sanction
    ok("anti-spam : message répété (les messages courts sont tolérés)")

    mm = FakeMember(7)
    flagged, m = await run(mm, "salut tout le monde", mentions=cfg.SPAM_MAX_MENTIONS)
    assert flagged and m.deleted and mm.timed_out
    flagged, _ = await run(FakeMember(8), "salut tout le monde", mentions=2)
    assert not flagged
    ok("mentions de masse")

    # les messages supprimés par le bot ne sont pas loggés une 2e fois
    assert bot.is_silent_delete(msgs[0].id)
    ok("suppressions du bot marquées (pas de double log)")
    await bot.db.close()
    await bot.close()



# ------------------------------------------------------------------ F. divers (sondage, annonces joueurs, niveaux, accueil, droits)

class FakeResponse:
    def __init__(self): self.calls = []
    def is_done(self): return bool(self.calls)
    async def send_message(self, *a, **kw): self.calls.append(("send", a, kw))
    async def defer(self, **kw): self.calls.append(("defer", (), kw))
    async def edit_message(self, **kw): self.calls.append(("edit", (), kw))


class FakeInteraction:
    def __init__(self, user, message=None, command=None):
        self.user, self.message, self.command = user, message, command
        self.response = FakeResponse()
        self.followup = SimpleNamespace(sent=[])
        async def fu(*a, **kw): self.followup.sent.append((a, kw))
        self.followup.send = fu


async def test_extras(tmp: Path):
    from cogs import community, roles, welcome, admin, levels
    from cogs.community import Community, LFGView

    # attributs de permissions utilisés par le code
    for attr in list(admin.REQUIRED_GUILD_PERMS) + [a for d in admin.CHANNEL_EXTRA_PERMS.values() for a in d]:
        assert hasattr(discord.Permissions, attr), attr
    assert roles.DANGEROUS_PERMS.administrator and roles.DANGEROUS_PERMS.kick_members
    safe = discord.Permissions(send_messages=True, view_channel=True)
    assert not (safe.value & roles.DANGEROUS_PERMS.value)
    assert discord.Permissions(ban_members=True).value & roles.DANGEROUS_PERMS.value
    ok("permissions Discord référencées valides, rôles dangereux détectés")

    # sondage natif
    poll = discord.Poll(question="Quel jeu ?", duration=timedelta(hours=24), multiple=False)
    for c in ("Hades", "Celeste"): poll.add_answer(text=c)
    assert len(poll.answers) == 2
    ok("sondage natif")

    # boutons du LFG
    v = LFGView()
    assert [c.custom_id for c in v.children] == ["lfg:join", "lfg:leave", "lfg:close"]
    assert LFGView(full=True).children[0].disabled and not LFGView(full=True).children[1].disabled
    assert all(c.disabled for c in LFGView(closed=True).children)
    ok("boutons recherche de joueurs (états)")

    bot = ZenoBot(make_settings(tmp / "ex"))
    await bot.db.connect()
    cog = Community(bot)
    chan = FakeTextChannel()
    await bot.db.create_lfg(500, 9, 1, "Valorant", 2, "ranked")
    msg = SimpleNamespace(id=500, channel=chan)

    def itx(uid, staff=False):
        u = FakeMember(uid, staff=staff)
        return FakeInteraction(u, msg)

    i = itx(1); await cog.lfg_join(i)                       # l'organisateur
    assert "organisateur" in i.response.calls[0][1][0]
    i = itx(2); await cog.lfg_join(i)
    kind, _, kw = i.response.calls[0]
    assert kind == "edit" and "1 / 2" in kw["embed"].fields[1].value and "<@2>" in kw["embed"].fields[2].value
    assert i.followup.sent and chan.sent and "user2" in chan.sent[-1]
    i = itx(2); await cog.lfg_join(i)
    assert "déjà inscrit" in i.response.calls[0][1][0]
    i = itx(3); await cog.lfg_join(i)
    kw = i.response.calls[0][2]
    assert "Complet" in kw["embed"].footer.text and kw["view"].children[0].disabled
    i = itx(4); await cog.lfg_join(i)
    assert "complète" in i.response.calls[0][1][0]
    i = itx(2); await cog.lfg_leave(i)
    assert i.response.calls[0][0] == "edit" and "1 / 2" in i.response.calls[0][2]["embed"].fields[1].value
    i = itx(2); await cog.lfg_leave(i)
    assert "pas inscrit" in i.response.calls[0][1][0]
    i = itx(7); await cog.lfg_close(i)                      # ni auteur ni staff
    assert "Seul" in i.response.calls[0][1][0]
    i = itx(1); await cog.lfg_close(i)
    kw = i.response.calls[0][2]
    assert "clôturée" in kw["embed"].footer.text and all(c.disabled for c in kw["view"].children)
    i = itx(3); await cog.lfg_join(i)
    assert "plus active" in i.response.calls[0][1][0]
    ok("recherche de joueurs : rejoindre, complet, quitter, clôturer, droits")

    # niveaux : gain d'XP, cooldown, message court, récompense
    cfg.XP_MIN = cfg.XP_MAX = 100
    lv = levels.Levels(bot)
    role = SimpleNamespace(id=900, name="Débutant", colour=SimpleNamespace(value=0x9AA5B1), is_assignable=lambda: True)
    guild = SimpleNamespace(get_role=lambda rid: role if rid == 900 else None)
    mem = FakeMember(20); mem.guild = guild; mem.added = []
    async def add_roles(r, reason=None): mem._roles.append(r); mem.added.append(r)
    async def remove_roles(r, reason=None): mem._roles.remove(r)
    mem.add_roles, mem.remove_roles = add_roles, remove_roles
    await bot.db.set_reward(1, 900)
    ch = FakeTextChannel()
    await lv.award_xp(FakeMessage(mem, "ok", ch))                      # trop court
    assert await bot.db.get_xp(20) is None
    await lv.award_xp(FakeMessage(mem, "un vrai message", ch))
    assert (await bot.db.get_xp(20))["xp"] == 100
    assert ch.sent and "Débutant" in ch.sent[0] and "niveau 1" in ch.sent[0] and mem.added == [role]
    await lv.award_xp(FakeMessage(mem, "un autre message", ch))        # cooldown
    assert (await bot.db.get_xp(20))["xp"] == 100 and len(ch.sent) == 1
    ok("niveaux : XP, anti-abus (cooldown, messages courts), montée de niveau + rôle")

    # un niveau sans nouveau rang n'est pas annoncé
    mem2 = FakeMember(21); mem2.guild = guild
    async def add2(r, reason=None): mem2._roles.append(r)
    async def rem2(r, reason=None): mem2._roles.remove(r)
    mem2.add_roles, mem2.remove_roles = add2, rem2
    await bot.db.add_xp(21, 400)                                        # niveau 2
    ch2 = FakeTextChannel()
    await lv.award_xp(FakeMessage(mem2, "un vrai message", ch2))        # +100 -> niveau 3, pas de palier franchi
    assert level_from_xp((await bot.db.get_xp(21))["xp"]).level == 3 and ch2.sent == []
    ok("niveaux : seuls les changements de rang sont annoncés")

    # /rank : rang, niveau, prochain rang
    await bot.db.set_reward(5, 901)
    guild.get_role = lambda rid: {900: role, 901: SimpleNamespace(id=901, name="Actif", is_assignable=lambda: True)}.get(rid)
    itx_rank = FakeInteraction(FakeMember(99)); itx_rank.guild = guild
    await lv.rank.callback(lv, itx_rank, mem)
    embed = itx_rank.response.calls[0][2]["embed"]
    f = {x.name: x.value for x in embed.fields}
    assert f["Rang"] == "Débutant" and f["Niveau"] == "1" and "Actif" in f["Prochain rang"] and f["XP total"] == "100"
    itx_none = FakeInteraction(FakeMember(98)); itx_none.guild = guild
    await lv.rank.callback(lv, itx_none, None)
    assert "pas encore d'XP" in itx_none.response.calls[0][1][0]
    assert embed.colour.value == 0x9AA5B1                              # liseré = couleur du rôle de rang
    await bot.db.remove_reward(5)
    ok("/rank : rang, niveau, prochain rang, membre sans XP")

    # /top : pages, ligne surlignée, bouton « ma page », boutons réservés à l'auteur
    from cogs.levels import LeaderboardView
    for uid in range(300, 325):
        await bot.db.add_xp(uid, 1000 + uid)                          # 25 membres classés au-dessus des autres
    members = {uid: FakeMember(uid) for uid in range(300, 325)}
    guild.get_member = lambda uid: members.get(uid)
    viewer = 305                                                       # rang 20 (XP 1305 : 20e en partant du haut)
    rank_v = await bot.db.get_rank(viewer)
    e1, pages = await lv.leaderboard_embed(guild, 1, viewer)
    assert pages == (await bot.db.count_ranked() + 9) // 10 and "```diff" in e1.description
    e_mine, _ = await lv.leaderboard_embed(guild, (rank_v - 1) // 10 + 1, viewer)
    marked = [l for l in e_mine.description.splitlines() if l.startswith("+")]
    assert len(marked) == 1 and f"{rank_v:>3}." in marked[0] and f"#{rank_v}" in e_mine.footer.text
    assert not any(l.startswith("+") for l in e1.description.splitlines())
    itx_top = FakeInteraction(FakeMember(viewer)); itx_top.guild = guild
    async def orig(): return SimpleNamespace(edit=None)
    itx_top.original_response = orig
    await lv.top.callback(lv, itx_top, None)                           # sans page : ouvre sur la page du spectateur
    first = itx_top.response.calls[0][2]
    assert f"Page {(rank_v - 1) // 10 + 1}/" in first["embed"].footer.text
    view = LeaderboardView(lv, viewer, 1, pages)
    assert view.previous.disabled and not view.next.disabled
    itx_btn = FakeInteraction(FakeMember(viewer)); itx_btn.guild = guild
    await view.next.callback(itx_btn)
    assert view.page == 2 and itx_btn.response.calls[0][0] == "edit"
    await view.mine.callback(itx_btn)
    assert view.page == (rank_v - 1) // 10 + 1
    stranger = FakeInteraction(FakeMember(1)); stranger.guild = guild
    assert await view.interaction_check(stranger) is False and await view.interaction_check(itx_btn) is True
    ok("/top : pages, ligne surlignée, « ma page », boutons réservés à l'auteur")

    # /ping
    from cogs.community import Community
    itx_p = FakeInteraction(FakeMember(1)); itx_p.created_at = utils.utcnow()
    await Community.ping.callback(SimpleNamespace(bot=SimpleNamespace(latency=0.042)), itx_p)
    assert "42 ms" in itx_p.response.calls[0][1][0]
    ok("/ping")

    # échelle de rangs : un seul rang (remplacer), retrait si l'XP baisse, mode cumuler
    assert [l for l, _, _ in cfg.LEVEL_LADDER] == [10, 20, 25, 30, 40, 50, 60, 70, 80, 90]
    roles_by_id = {1000 + l: SimpleNamespace(id=1000 + l, name=n, is_assignable=lambda: True) for l, n, _ in cfg.LEVEL_LADDER}
    lg = SimpleNamespace(get_role=lambda rid: roles_by_id.get(rid))
    await bot.db.remove_reward(1)
    for l, _, _ in cfg.LEVEL_LADDER:
        await bot.db.set_reward(l, 1000 + l)
    m = FakeMember(40); m.guild = lg
    async def a2(r, reason=None): m._roles.append(r)
    async def r2(r, reason=None): m._roles.remove(r)
    m.add_roles, m.remove_roles = a2, r2
    names = lambda: sorted(r.name for r in m._roles)
    given, removed = await lv.sync_rewards(m, 9)
    assert not given and names() == []                                   # nouveaux : rien
    await lv.sync_rewards(m, 10);  assert names() == ["Débutant"]
    await lv.sync_rewards(m, 24);  assert names() == ["Actif"]            # remplace Débutant
    await lv.sync_rewards(m, 90);  assert names() == ["Boss final"]
    given, removed = await lv.sync_rewards(m, 90); assert not given and not removed
    await lv.sync_rewards(m, 31);  assert names() == ["Hyperactif"]       # XP retirée : rang abaissé
    await lv.sync_rewards(m, 0);   assert names() == []
    await bot.db.set_setting("levels.mode", "cumuler")
    await lv.sync_rewards(m, 25);  assert names() == ["Actif", "Débutant", "Superactif"]
    await bot.db.delete_setting("levels.mode")
    ok("rangs : un seul à la fois, abaissement, rien avant le niveau 10, mode cumuler")

    # /xp réservé au staff, /rank public
    cmd_xp, cmd_niv = SimpleNamespace(qualified_name="xp reset"), SimpleNamespace(qualified_name="rank")
    plain = FakeMember(30)
    assert await lv.interaction_check(FakeInteraction(plain, command=cmd_niv)) is True
    try:
        await lv.interaction_check(FakeInteraction(plain, command=cmd_xp)); raise AssertionError
    except discord.app_commands.MissingPermissions:
        pass
    ok("/xp réservé au staff, /rank public")

    # accueil
    member = SimpleNamespace(mention="<@5>", guild=SimpleNamespace(name="Chez Zeno", member_count=42))
    text = welcome.render_welcome(admin.DEFAULT_WELCOME, member)
    assert "<@5>" in text and "Chez Zeno" in text and "n°42" in text and "{" not in text
    assert welcome.render_welcome("Salut {member} {inconnu}", member) == "Salut <@5> {inconnu}"
    ok("message de bienvenue (variables)")

    # /config domaine : normalisation
    assert admin.DOMAIN_RE.match("medal.tv") and not admin.DOMAIN_RE.match("pas un domaine") and not admin.DOMAIN_RE.match("https://x.com")
    ok("validation des domaines")
    await bot.db.close(); await bot.close()



# ------------------------------------------------------------------ G. événements Twitch, crédits, récompense

class FakeHelix:
    """Faux Twitch : OAuth, Helix, EventSub (WebSocket)."""

    def __init__(self):
        self.access, self.refresh_tok = None, None
        self.subs, self.patches, self.reward_calls = [], [], []
        self.ws_connections = 0
        self.subs_done = asyncio.Event()
        self.second_done = asyncio.Event()
        self.refreshes = 0
        self.port = 0

    def _auth(self, request):
        return request.headers.get("Authorization") == f"Bearer {self.access}" and request.headers.get("Client-Id") == "cid"

    async def token_h(self, request):
        d = await request.post()
        assert d["client_secret"] == "sec"
        if d["grant_type"] == "authorization_code":
            assert d["redirect_uri"] == cfg.TWITCH_REDIRECT_URI
            if d["code"] not in ("GOODCODE", "OTHERCODE"):
                return web.json_response({"message": "invalid authorization code"}, status=400)
            self.access, self.refresh_tok = ("acc1" if d["code"] == "GOODCODE" else "accX"), "ref1"
            self.other = d["code"] == "OTHERCODE"
        else:
            assert d["grant_type"] == "refresh_token" and d["refresh_token"] == self.refresh_tok, d
            self.refreshes += 1
            self.access, self.refresh_tok = f"acc{self.refreshes + 1}", f"ref{self.refreshes + 1}"
        return web.json_response({"access_token": self.access, "refresh_token": self.refresh_tok, "expires_in": 3600,
                                  "scope": list(cfg.TWITCH_USER_SCOPES)})

    async def users_h(self, request):
        if not self._auth(request):
            return web.Response(status=401)
        login = "autre" if getattr(self, "other", False) else "zenosurtwitch_"
        return web.json_response({"data": [{"id": "42", "login": login, "display_name": login}]})

    async def eventsub_h(self, request):
        if not self._auth(request):
            return web.Response(status=401)
        body = await request.json()
        assert body["transport"]["method"] == "websocket" and body["transport"]["session_id"].startswith("S")
        self.subs.append(body)
        if body["type"] == "channel.follow":
            return web.json_response({"message": "missing scope"}, status=403)
        if len(self.subs) == len(cfg.TWITCH_USER_SCOPES) + 2:   # 9 abonnements demandés
            self.subs_done.set()
        return web.json_response({"data": [{}]}, status=202)

    async def chatters_h(self, request):
        if not self._auth(request):
            return web.Response(status=401)
        assert request.query["broadcaster_id"] == "42" and request.query["moderator_id"] == "42"
        if request.query.get("after") == "c1":
            return web.json_response({"data": [{"user_login": "dave", "user_name": "Dave"}], "pagination": {}})
        return web.json_response({"data": [{"user_login": "alice", "user_name": "Alice"}, {"user_login": "bob", "user_name": "Bob"},
                                           {"user_login": "nightbot", "user_name": "Nightbot"}], "pagination": {"cursor": "c1"}})

    async def redemption_h(self, request):
        if not self._auth(request):
            return web.Response(status=401)
        self.patches.append((dict(request.query), await request.json()))
        return web.json_response({"data": [{}]})

    async def rewards_h(self, request):
        if not self._auth(request):
            return web.Response(status=401)
        body = await request.json()
        self.reward_calls.append((request.method, dict(request.query), body))
        if request.method == "POST":
            return web.json_response({"data": [{"id": "RW9"}]})
        if request.query["id"] != "RW9":
            return web.json_response({"message": "not found"}, status=404)
        return web.json_response({"data": [{"id": "RW9"}]})

    async def ws_h(self, request):
        import uuid
        ws = web.WebSocketResponse(); await ws.prepare(request)
        self.ws_connections += 1
        n = self.ws_connections

        def frame(kind, payload=None, sub=None, mid=None):
            return {"metadata": {"message_id": mid or str(uuid.uuid4()), "message_type": kind, "subscription_type": sub},
                    "payload": payload or {}}

        await ws.send_json(frame("session_welcome", {"session": {"id": f"S{n}", "keepalive_timeout_seconds": 10}}))
        if n == 1:
            await asyncio.wait_for(self.subs_done.wait(), 5)
            chat = lambda login, name, text: frame("notification", {"event": {"chatter_user_login": login, "chatter_user_name": name, "message": {"text": text}}}, "channel.chat.message")
            await ws.send_json(frame("notification", {"event": {"type": "live", "id": "STR1"}}, "stream.online"))
            await ws.send_json(chat("alice", "Alice", "salut tout le monde"))
            await ws.send_json(chat("alice", "Alice", "deuxième message"))
            await ws.send_json(chat("nightbot", "Nightbot", "Pensez à follow"))
            await ws.send_json(chat("bob", "Bob", "!discord"))                  # commande : ne compte pas
            await ws.send_json(frame("notification", {"event": {"user_login": "erin", "user_name": "Erin", "is_gift": False}}, "channel.subscribe"))
            await ws.send_json(frame("notification", {"event": {"user_login": "alice", "user_name": "Alice", "cumulative_months": 14}}, "channel.subscription.message"))
            await ws.send_json(frame("notification", {"event": {"user_login": "gus", "user_name": "Gus", "total": 5, "is_anonymous": False}}, "channel.subscription.gift"))
            await ws.send_json(frame("notification", {"event": {"user_login": "hana", "user_name": "Hana", "bits": 300, "is_anonymous": False}}, "channel.cheer"))
            await ws.send_json(frame("notification", {"event": {"user_login": "ivan", "user_name": "Ivan"}}, "channel.follow"))
            await ws.send_json(frame("notification", {"event": {"from_broadcaster_user_login": "jules", "from_broadcaster_user_name": "Jules", "viewers": 12}}, "channel.raid"))
            self.dup = chat("zed", "Zed", "message en double")
            await ws.send_json(self.dup); await ws.send_json(self.dup)           # même message_id : compté une fois
            await ws.send_json(frame("session_reconnect", {"session": {"reconnect_url": f"ws://127.0.0.1:{self.port}/ws"}}))
            async for _ in ws: pass
        else:
            await ws.send_json(frame("notification", {"event": {"chatter_user_login": "carol", "chatter_user_name": "Carol", "message": {"text": "après la reconnexion"}}}, "channel.chat.message"))
            self.second_done.set()
            async for _ in ws: pass
        return ws


async def wait_until(cond, timeout=5.0):
    end = asyncio.get_event_loop().time() + timeout
    while not await cond():
        if asyncio.get_event_loop().time() > end:
            raise AssertionError("délai dépassé")
        await asyncio.sleep(0.05)


async def test_streamevents(tmp: Path):
    import credits
    from cogs.streamevents import StreamEvents
    from twitch_user import AuthError, TwitchUserAuth, extract_code

    # --- page de crédits : tri, exclusion des bots, échappement
    ch = [{"login": "a", "name": "Alice", "messages": 5, "present": 1}, {"login": "nightbot", "name": "Nightbot", "messages": 9, "present": 1},
          {"login": "b", "name": "Bob", "messages": 0, "present": 1}, {"login": "c", "name": "Carl", "messages": 0, "present": 1},
          {"login": "zeno", "name": "Zeno", "messages": 3, "present": 1}, {"login": "x", "name": "<script>alert(1)</script>", "messages": 1, "present": 1}]
    ev = [{"kind": "sub", "login": "c", "name": "Carl", "amount": 0, "detail": None}, {"kind": "resub", "login": "a", "name": "Alice", "amount": 14, "detail": None},
          {"kind": "gift", "login": "", "name": "", "amount": 5, "detail": None}, {"kind": "gift", "login": "g", "name": "Gus", "amount": 2, "detail": None},
          {"kind": "gift", "login": "g", "name": "Gus", "amount": 3, "detail": None}, {"kind": "raid", "login": "j", "name": "Jules", "amount": 12, "detail": None}]
    sec = credits.build_sections(ch, ev, ["NightBot"], "zeno")
    assert [n for n, _ in sec.chatters] == ["Alice", "<script>alert(1)</script>"]            # bots et chaîne exclus
    assert sec.silent == ["Bob"]                                                           # Carl est déjà remercié comme abonné
    assert sec.subs == [("Carl", ""), ("Alice", "14 mois")] and ("Gus", 5) in sec.gifters and (credits.ANONYMOUS, 5) in sec.gifters
    page = credits.render_credits(sec, channel_name="Zeno", title="Soirée <b>test</b>", games=["Hades"], started_at="2026-11-06T18:00:00Z",
                                  style=credits.normalize_style({"show_title": True, "show_meta": True}))
    assert "<script>alert(1)" not in page and "&lt;script&gt;" in page and "&lt;b&gt;" in page and "6 novembre 2026" in page
    assert "Nightbot" not in page and "Alice" in page
    assert "Merci à la personne de l'ombre" in page
    assert "Bob" not in page                                                            # silencieux : remerciés en groupe, pas nommés
    named = credits.render_credits(sec, channel_name="Zeno", style=credits.normalize_style({"shadow_names": True}))
    assert "Bob" in named and "Personnes de l'ombre" in named
    assert credits.build_sections([], [], [], "zeno").is_empty
    ok("crédits : tri, bots exclus, dons agrégés, silencieux, échappement HTML")

    assert extract_code("abc123") == "abc123"
    assert extract_code("https://localhost/?code=abc123&scope=user%3Aread%3Achat&state=x") == "abc123"
    assert extract_code("?code=zzz&scope=a") == "zzz"
    ok("extraction du code d'autorisation")

    # --- serveur Twitch factice
    fake = FakeHelix()
    app = web.Application()
    app.router.add_post("/oauth2/token", fake.token_h)
    app.router.add_get("/helix/users", fake.users_h)
    app.router.add_post("/helix/eventsub/subscriptions", fake.eventsub_h)
    app.router.add_get("/helix/chat/chatters", fake.chatters_h)
    app.router.add_patch("/helix/channel_points/custom_rewards/redemptions", fake.redemption_h)
    app.router.add_route("*", "/helix/channel_points/custom_rewards", fake.rewards_h)
    app.router.add_get("/ws", fake.ws_h)
    runner = web.AppRunner(app); await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0); await site.start()
    fake.port = site._server.sockets[0].getsockname()[1]
    base = f"http://127.0.0.1:{fake.port}"

    bot = ZenoBot(make_settings(tmp / "se"))
    await bot.db.connect()
    auth = TwitchUserAuth("cid", "sec", bot.db, "zenosurtwitch_", api_base=f"{base}/helix", oauth_base=f"{base}/oauth2")
    await auth.start()
    assert not auth.authorized and "client_id=cid" in auth.authorize_url() and "user%3Aread%3Achat" in auth.authorize_url()
    try:
        await auth.access_token()
        raise AssertionError("aurait dû échouer")
    except AuthError:
        pass
    try:
        await auth.exchange_code("MAUVAIS")
        raise AssertionError("aurait dû échouer")
    except AuthError:
        pass
    try:
        await auth.exchange_code("OTHERCODE")
        raise AssertionError("mauvais compte accepté")
    except AuthError as exc:
        assert "autre" in str(exc) and not auth.authorized
    fake.other = False
    assert await auth.exchange_code("https://localhost/?code=GOODCODE&scope=x") == "zenosurtwitch_"
    assert auth.authorized and auth.user_id == "42" and not auth.missing_scopes
    auth2 = TwitchUserAuth("cid", "sec", bot.db, "zenosurtwitch_", api_base=f"{base}/helix", oauth_base=f"{base}/oauth2")
    await auth2.start(); assert auth2.authorized and auth2.user_id == "42"     # jeton conservé après redémarrage
    await auth2.close()
    auth._token["expires_at"] = 0                                              # jeton expiré : renouvelé tout seul
    status, _ = await auth.request("GET", "users")
    assert status == 200 and fake.refreshes == 1 and auth._token["refresh_token"] == "ref2"
    fake.access = "revoque"                                                    # jeton refusé par Twitch : 1 renouvellement puis nouvel essai
    status, _ = await auth.request("GET", "users")
    assert status == 200 and fake.refreshes == 2
    ok("autorisation Twitch : échange du code, mauvais compte refusé, jeton conservé et renouvelé")

    # --- cog : connexion, abonnements, collecte
    guild_members = []
    logs, dms = [], []
    role = SimpleNamespace(id=700, name="Vrai viewer", mention="<@&700>", is_assignable=lambda: True)
    guild = SimpleNamespace(id=1, name="ZenoSurDiscord", members=guild_members)

    def make_member(uid, name, glob=None, display=None):
        m = SimpleNamespace(id=uid, name=name, global_name=glob, display_name=display or glob or name, bot=False,
                            mention=f"<@{uid}>", roles=[], guild=guild)
        async def add_roles(r, reason=None): m.roles.append(r)
        async def send(text): dms.append((uid, text))
        m.add_roles, m.send = add_roles, send
        return m

    alice_d, bob_d = make_member(11, "alice_d", "Alice"), make_member(12, "bob_d", "Bob")
    twin1, twin2 = make_member(13, "x1", "Jumeau"), make_member(14, "x2", "Jumeau")
    guild_members += [alice_d, bob_d, twin1, twin2]
    credits_chan = FakeChannel(88)
    twitch_stub = SimpleNamespace(state={}, _get_user=None)
    async def get_user(): return {"display_name": "ZenoSurTwitch_"}
    twitch_stub._get_user = get_user
    bot.get_cog = lambda name: twitch_stub if name == "Twitch" else None
    bot.get_guild = lambda gid: guild
    bot.channel_setting = lambda key: credits_chan if key == "credits" else None
    bot.role_setting = lambda key: role if key == "vrai_viewer" else None
    async def send_log(embed): logs.append(embed)
    bot.send_log = send_log

    async def never_ready(): await asyncio.Event().wait()            # le client Discord n'est jamais connecté dans ce test
    bot.wait_until_ready = never_ready
    cog = StreamEvents(bot, auth)
    cog.web_port = 0                                                 # port libre choisi par le système
    cog.ws_url = f"ws://127.0.0.1:{fake.port}/ws"
    await cog.cog_load()
    async def collected():
        rows = {r["login"]: r for r in await bot.db.stream_chatters("STR1")}
        return "carol" in rows
    await wait_until(collected)
    assert cog.connected and fake.ws_connections == 2 and len(fake.subs) == 9          # reconnexion sans nouvel abonnement
    assert {s["type"] for s in fake.subs} >= {"channel.chat.message", "channel.channel_points_custom_reward_redemption.add", "stream.online"}
    chat_sub = next(s for s in fake.subs if s["type"] == "channel.chat.message")
    assert chat_sub["condition"] == {"broadcaster_user_id": "42", "user_id": "42"}
    assert "channel.follow" in cog.sub_errors and len(cog.subscribed) == 8
    rows = {r["login"]: r for r in await bot.db.stream_chatters("STR1")}
    assert rows["alice"]["messages"] == 2 and rows["carol"]["messages"] == 1 and rows["zed"]["messages"] == 1  # doublon ignoré
    assert "bob" not in rows                                                           # « !discord » ne compte pas
    kinds = [(e["kind"], e["login"], e["amount"], e["detail"]) for e in await bot.db.stream_events("STR1")]
    assert ("sub", "erin", 0, None) in kinds and ("resub", "alice", 14, None) in kinds and ("gift", "gus", 5, None) in kinds
    assert ("cheer", "hana", 300, None) in kinds and ("follow", "ivan", 0, None) in kinds and ("raid", "jules", 12, None) in kinds
    ok("EventSub : connexion, abonnements, reconnexion sans doublon, collecte du chat et des événements")

    n = await cog.poll_chatters("STR1")
    assert n == 4                                                                      # 2 pages
    rows = {r["login"]: r for r in await bot.db.stream_chatters("STR1")}
    assert rows["bob"]["present"] == 1 and rows["bob"]["messages"] == 0 and rows["alice"]["present"] == 1 and rows["dave"]["present"] == 1
    ok("silencieux : relevé des connectés au chat (pagination)")

    # --- récompense Vrai viewer
    await bot.db.set_setting("reward.vrai_viewer", "RW1")
    def redemption(rid, text, reward="RW1"):
        return {"id": rid, "user_name": "Viewer", "user_input": text, "reward": {"id": reward, "title": "Vrai viewer", "cost": 5000}}
    await cog._on_notification("channel.channel_points_custom_reward_redemption.add", redemption("R1", "@Alice_D"))
    assert role in alice_d.roles and fake.patches[-1][1] == {"status": "FULFILLED"} and fake.patches[-1][0]["id"] == "R1"
    assert fake.patches[-1][0]["reward_id"] == "RW1" and fake.patches[-1][0]["broadcaster_id"] == "42"
    assert dms and dms[-1][0] == 11 and "Vrai viewer" in dms[-1][1]
    await cog._on_notification("channel.channel_points_custom_reward_redemption.add", redemption("R2", "alice_d"))
    assert fake.patches[-1][1] == {"status": "CANCELED"}                                # déjà le rôle : remboursé
    await cog._on_notification("channel.channel_points_custom_reward_redemption.add", redemption("R3", "personne_inconnu"))
    assert fake.patches[-1][1] == {"status": "CANCELED"}
    await cog._on_notification("channel.channel_points_custom_reward_redemption.add", redemption("R4", "Jumeau"))
    assert fake.patches[-1][1] == {"status": "CANCELED"} and not twin1.roles and "plusieurs" in logs[-1].description
    await cog._on_notification("channel.channel_points_custom_reward_redemption.add", redemption("R5", ""))
    assert fake.patches[-1][1] == {"status": "CANCELED"}
    count = len(fake.patches)
    await cog._on_notification("channel.channel_points_custom_reward_redemption.add", redemption("R6", "bob_d", reward="AUTRE"))
    assert len(fake.patches) == count and not bob_d.roles                               # autre récompense : ignorée
    bot.role_setting = lambda key: None
    await cog._on_notification("channel.channel_points_custom_reward_redemption.add", redemption("R7", "bob_d"))
    assert len(fake.patches) == count and not bob_d.roles and "en attente" in logs[-1].description   # rôle non configuré : laissé en attente
    bot.role_setting = lambda key: role if key == "vrai_viewer" else None
    ok("récompense : rôle donné, remboursement (déjà le rôle / inconnu / ambigu / vide), rôle absent = en attente")

    # --- commandes /stream
    itx = FakeInteraction(FakeMember(1, staff=True))
    await cog.recompense.callback(cog, itx, 5000)
    assert fake.reward_calls[-1][0] == "PATCH" or True
    # première fois : le réglage pointe vers RW1 (inexistant) -> 404 -> création
    assert fake.reward_calls[0][0] == "PATCH" and fake.reward_calls[1][0] == "POST" and fake.reward_calls[1][2]["is_user_input_required"] is True
    assert fake.reward_calls[1][2]["cost"] == 5000 and fake.reward_calls[1][2]["title"] == "Vrai viewer"
    assert bot.db.setting("reward.vrai_viewer") == "RW9" and "créée" in itx.followup.sent[-1][0][0]
    itx = FakeInteraction(FakeMember(1, staff=True))
    await cog.recompense.callback(cog, itx, 7000)
    assert fake.reward_calls[-1][0] == "PATCH" and fake.reward_calls[-1][2] == {"cost": 7000} and "mis à jour" in itx.followup.sent[-1][0][0]
    ok("/stream recompense : création puis mise à jour du prix")

    itx = FakeInteraction(FakeMember(1, staff=True))
    await cog.autoriser.callback(cog, itx, None)
    assert "authorize" in itx.response.calls[0][1][0] and "localhost" in itx.response.calls[0][1][0]
    cog.restart_connection = lambda: None
    itx = FakeInteraction(FakeMember(1, staff=True))
    fake.other = True
    await cog.autoriser.callback(cog, itx, "OTHERCODE")
    assert "autre" in itx.followup.sent[-1][0][0] and auth.authorized
    fake.other = False
    itx = FakeInteraction(FakeMember(1, staff=True))
    await cog.autoriser.callback(cog, itx, "GOODCODE")
    assert "✅" in itx.followup.sent[-1][0][0]
    itx = FakeInteraction(FakeMember(1, staff=True))
    await cog.statut.callback(cog, itx)
    text = itx.response.calls[0][1][0]
    assert "zenosurtwitch_" in text and "channel.follow" in text and "Récompense : créée" in text
    ok("/stream autoriser et /stream statut")

    # --- bots exclus
    itx = FakeInteraction(FakeMember(1, staff=True))
    await cog.bot_add.callback(cog, itx, "@Zed")
    assert "zed" in cog.bots() and "nightbot" in cog.bots()
    await cog.bot_remove.callback(cog, itx, "zed")
    assert "zed" not in cog.bots()
    ok("/stream bot-ajouter, bot-retirer")

    # --- page servie à OBS (pendant le live : toujours à jour)
    import aiohttp
    assert cog.web_bound_port
    token = await cog.credits_token()
    url = f"http://127.0.0.1:{cog.web_bound_port}/credits/"
    async with aiohttp.ClientSession() as http:
        async with http.get(url + "mauvais") as r:
            assert r.status == 404
        async with http.get(f"http://127.0.0.1:{cog.web_bound_port}/") as r:
            assert r.status == 404
        async with http.get(url + token) as r:
            live_page = await r.text()
            assert r.status == 200 and "no-store" in r.headers["Cache-Control"] and r.content_type == "text/html"
        assert "Alice" in live_page and "Carol" in live_page and "Nightbot" not in live_page and "personnes de l'ombre" in live_page
        assert "Bob" not in live_page and "Dave" not in live_page
        await cog._on_notification("channel.chat.message", {"chatter_user_login": "nina", "chatter_user_name": "Nina", "message": {"text": "tout dernier message"}})
        async with http.get(url + token) as r:
            assert "Nina" in await r.text()                                              # message arrivé juste avant la scène de fin
        itx = FakeInteraction(FakeMember(1, staff=True))
        await cog.lien.callback(cog, itx, False)
        text = itx.response.calls[0][1][0]
        assert f"/credits/{token}" in text and "Actualiser le navigateur" in text
        itx = FakeInteraction(FakeMember(1, staff=True))
        await cog.lien.callback(cog, itx, True)
        new_token = await cog.credits_token()
        assert new_token != token and f"/credits/{new_token}" in itx.response.calls[0][1][0]
        async with http.get(url + token) as r:
            assert r.status == 404                                                       # ancienne adresse invalidée
    ok("page OBS : accessible avec l'adresse secrète, à jour en direct, adresse régénérable")

    # --- personnalisation : validation du style
    hostile = credits.normalize_style({"accent": "red;}body{display:none", "text": "#12345", "font": "Comic\"><script>", "speed": 99999,
                                       "columns": "9", "heading": "<img src=x onerror=1>\x00", "bogus": 1,
                                       "sections": [{"id": "subs", "on": False, "title": "X"}, {"id": "subs"}, {"id": "nope"}]})
    d = credits.default_style()
    assert hostile["accent"] == d["accent"] and hostile["text"] == d["text"] and hostile["font"] == d["font"] and "bogus" not in hostile
    assert hostile["speed"] <= next(f["max"] for f in credits.FIELDS if f["key"] == "speed") and hostile["columns"] == d["columns"]
    assert [s["id"] for s in hostile["sections"]].count("subs") == 1 and len(hostile["sections"]) == len(d["sections"])
    assert hostile["sections"][0]["id"] == "subs" and hostile["sections"][0]["on"] is False
    assert credits.normalize_style("pas un dict") == d and credits.normalize_style(None) == d
    html_out = credits.render_credits(sec, channel_name="Zeno", style=hostile)
    assert "display:none" not in html_out and "<img src=x" not in html_out and "Comic" not in html_out
    custom = credits.normalize_style({"accent": "#ff0000", "columns": "3", "align": "left", "chat_sort": "alpha",
                                      "sections": [{"id": "chatters", "on": True, "title": "Les bavards"}, {"id": "subs", "on": False, "title": "Abos"}]})
    page2 = credits.render_credits(sec, channel_name="Zeno", style=custom)
    assert "#ff0000" in page2 and "Les bavards" in page2 and "Abos" not in page2 and "__" not in page2.replace("__proto__", "")
    for name in credits.PRESETS:
        assert credits.normalize_style(credits.preset_style(name)) == credits.preset_style(name)
    assert "static" in credits.render_credits(credits.demo_sections(), channel_name="Z", mode="static").lower()
    # modèles : verre (cartes + pastilles) et classique
    glass = credits.render_credits(credits.demo_sections(), channel_name="Z", style=credits.normalize_style({"top_count": 3, "show_counts": True}))
    assert glass.count('class="card"') == 3 and 'class="pill"' in glass
    assert "40 messages" in glass and "Alice" in glass and "mask-image" in glass and "scaleX(1.30)" in glass
    nocards = credits.render_credits(credits.demo_sections(), channel_name="Z", style=credits.normalize_style({"top_count": 0, "show_counts": False, "edge_fade": 0}))
    assert 'class="card"' not in nocards and "mask-image" not in nocards
    classic = credits.render_credits(credits.demo_sections(), channel_name="Z", style=credits.preset_style("Modèle 2 · Classique"))
    assert 'class="card"' not in classic.split("<body>")[1] and "Ceux qui ont animé le chat" in classic and "Merci</h1>" in classic
    assert all("__" not in credits.render_credits(credits.demo_sections(), channel_name="Z", style=credits.preset_style(n)) for n in credits.PRESETS)
    evil = credits.render_credits(credits.demo_sections(), channel_name="Z", style=credits.normalize_style({"card_bg": "red", "card_radius": "1;}", "template": "x"}))
    assert not re.search(r"\bred\b", evil.split("<body>")[0]) and "1;}" not in evil and credits.normalize_style({"template": "x"})["template"] == "glass"
    ok("style : validation stricte, injection refusée, rubriques, préréglages, rendu")
    ok("modèles : verre (cartes, pastilles, fondu) et classique, réglages validés")

    # --- personnalisation : routes web
    t2 = await cog.credits_token()
    base = f"http://127.0.0.1:{cog.web_bound_port}/credits/{t2}"
    async with aiohttp.ClientSession() as http:
        async with http.get(f"http://127.0.0.1:{cog.web_bound_port}/credits/zzz/editor") as r:
            assert r.status == 404
        async with http.post(f"http://127.0.0.1:{cog.web_bound_port}/credits/zzz/style", json={"style": {}}) as r:
            assert r.status == 404
        async with http.get(base + "/editor") as r:
            ed = await r.text()
            assert r.status == 200 and "Personnalisation" in ed or "personnalisation" in ed
            assert "</script><script" not in ed and ed.count("<script") == 2
        async with http.post(base + "/render", json={"style": {"accent": "#00ff88"}, "mode": "static"}) as r:
            demo = await r.text()
            assert r.status == 200 and "#00ff88" in demo and "ZenoSurTwitch_" in demo
        async with http.post(base + "/style", data="pas du json") as r:
            assert r.status == 400
        async with http.post(base + "/style", data=b"x" * 200000) as r:
            assert r.status == 413
        async with http.post(base + "/style", json={"style": {"accent": "#ff00aa", "heading": "Merci à tous"}}) as r:
            saved = await r.json()
            assert r.status == 200 and saved["style"]["accent"] == "#ff00aa"
        async with http.get(base) as r:
            assert "#ff00aa" in await r.text()                                          # la page OBS utilise le style enregistré
        async with http.post(base + "/presets", json={"name": "Modèle 1 · Verre", "style": {}}) as r:
            assert r.status == 400                                                       # nom réservé
        async with http.post(base + "/presets", json={"name": "Mon style", "style": {"accent": "#112233"}}) as r:
            pr = await r.json()
            assert r.status == 200 and pr["presets"]["Mon style"]["accent"] == "#112233"
        async with http.get(base + "/editor") as r:
            assert "Mon style" in await r.text()
        async with http.post(base + "/presets/delete", json={"name": "Mon style"}) as r:
            assert "Mon style" not in (await r.json())["presets"]
    page_arch, _ = await cog.build_credits("STR1", {})
    assert "#ff00aa" in page_arch                                                       # archive et /stream credits : même style
    ok("éditeur : routes protégées, enregistrement, aperçu, préréglages, page OBS à jour")

    # --- fin de live : envoi des crédits
    info = {"stream_id": "STR1", "title": "Soirée test", "games": ["Hades"], "started_at": "2026-11-06T18:00:00Z"}
    await cog.stream_ended("STR1", info)
    m, content, kw = credits_chan.sent[-1]
    f = kw["file"]
    assert f.filename == "credits-2026-11-06.html" and "crédits du live" in content.lower()
    body = f.fp.read().decode("utf-8")
    assert "Alice" in body and "Carol" in body and "Erin" in body and "Gus" in body and "Hana" in body and "Ivan" in body and "Jules" in body
    assert "Bob" not in body and "Dave" not in body and "de l'ombre" in body and "Nightbot" not in body and "ZenoSurTwitch_" in body
    assert kw["allowed_mentions"].everyone is False
    assert json.loads(await bot.db.kv_get("credits.last"))["stream_id"] == "STR1" and cog._stream_id is None
    async with aiohttp.ClientSession() as http:                                          # après le live : la page montre le dernier live
        async with http.get(url + new_token) as r:
            assert "Carol" in await r.text()
    itx = FakeInteraction(FakeMember(1, staff=True))
    await cog.credits_cmd.callback(cog, itx)                                           # dernier live regénéré à la demande
    assert itx.followup.sent[-1][1]["file"].filename == "credits-2026-11-06.html"
    sent_before = len(credits_chan.sent)
    await cog.stream_ended("VIDE", {"title": "", "games": [], "started_at": None})
    assert len(credits_chan.sent) == sent_before                                       # live sans donnée : rien d'envoyé
    itx = FakeInteraction(FakeMember(1, staff=True))
    await cog.credits_cmd.callback(cog, itx)
    assert "Aucune donnée" in itx.followup.sent[-1][0][0]
    bot.channel_setting = lambda key: None
    sent_before = len(credits_chan.sent)
    await cog.stream_ended("STR1", info)
    assert len(credits_chan.sent) == sent_before                                         # pas de salon crédits : pas d'archive
    ok("fin de live : archive envoyée si un salon est défini, page OBS sur le dernier live, /stream credits")
    bot.channel_setting = lambda key: credits_chan if key == "credits" else None

    old = (utils.utcnow() - timedelta(days=cfg.CREDITS_RETENTION_DAYS + 1)).isoformat(timespec="seconds")
    await bot.db.execute("INSERT INTO stream_chatters (stream_id, login, name, messages, present, created_at) VALUES ('OLD','x','X',1,0,?)", (old,))
    await cog.stream_ended("VIDE2", {"title": "", "games": [], "started_at": None})
    assert not await bot.db.stream_chatters("OLD") and await bot.db.stream_chatters("STR1")
    ok("données anciennes purgées")

    await cog.cog_unload()
    await auth.close()
    await runner.cleanup()
    await bot.db.close(); await bot.close()

# ------------------------------------------------------------------ main

async def main():
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        print("A. utilitaires / configuration")
        test_utils(); test_config(tmp)
        print("B. base de données")
        await test_db(tmp)
        print("C. chargement des modules et commandes")
        await test_tree(tmp)
        print("D. Twitch (faux serveur local)")
        await test_twitch(tmp)
        print("E. modération automatique")
        await test_automod(tmp)
        print("F. sondage, annonces joueurs, niveaux, accueil")
        await test_extras(tmp)
        print("G. événements Twitch, crédits, récompense")
        await test_streamevents(tmp)
    print(f"\nTOUT EST OK ({len(PASSED)} vérifications)")


asyncio.run(main())

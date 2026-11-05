"""Tests de vérification du bot (sans connexion à Discord ni à Twitch)."""
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
    assert len(loaded) == 8, loaded
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
                "kick", "ban", "unban", "purge", "niveau", "top", "sondage", "idee", "idee-statut", "cherche-joueurs"}
    assert set(names) == expected, set(names) ^ expected
    assert len(cmds) <= 100
    # sous-commandes
    sub = {c.name: sorted(s.name for s in c.commands) for c in cmds if hasattr(c, "commands")}
    print("      sous-commandes :", sub)
    assert "verifier" in sub["config"] and "panel" in sub["roles"] and "statut" in sub["twitch"]
    # les groupes réservés au staff portent bien la permission par défaut
    for c in cmds:
        if c.name in ("config", "twitch", "xp"):
            assert c.default_permissions and c.default_permissions.manage_guild
        if c.name == "roles":
            assert c.default_permissions.manage_roles
    # pas de /planning ni de giveaway ni de fonctions « pédagogue »
    assert not {"planning", "tirage", "giveaway", "astuce", "quiz", "faq"} & set(names)
    ok(f"chargement des 8 modules, {len(cmds)} commandes valides")
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


class FakeChannel(discord.TextChannel):
    def __init__(self, cid):
        self.id = cid
        self.sent, self.edits = [], []
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
    assert "<@&5>" in content and "ZenoSurTwitch_" in content
    assert kw["embed"].title == "Découverte !" and kw["embed"].fields[0].value == "Hades"
    assert "1280x720" in kw["embed"].image.url
    assert kw["allowed_mentions"].roles == [role]
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
    mid, kw = chan.edits[-1]
    assert mid == msg_id and kw["content"] is None
    end = kw["embed"]
    assert "terminé" in end.title
    fields = {f.name: f.value for f in end.fields}
    assert "Hades, Celeste" in fields["🎮 Jeux"] and "1 h" in fields["⏱️ Durée"]
    assert kw["view"].children[0].label == "Voir les rediffusions"
    ok("fin de live : récapitulatif (durée, jeux), ping retiré")

    # --- reprise rapide (nouvelle session) : même message, pas de nouveau ping
    fake.stream = {**stream, "id": "S2"}
    await cog._check_live()
    assert len(chan.sent) == 1
    mid, kw = chan.edits[-1]
    assert mid == msg_id and "de retour" in kw["content"] and "<@&5>" not in kw["content"]
    assert kw["allowed_mentions"].roles is False or not kw["allowed_mentions"].roles
    ok("reprise rapide : annonce réutilisée, pas de re-ping")

    # --- nouveau live bien plus tard : nouvelle annonce avec ping
    fake.stream = None
    for _ in range(3): await cog._check_live()
    assert cog.state["stream_id"] is None
    cog.state["last_end"] = (utils.utcnow() - timedelta(hours=3)).isoformat()
    fake.stream = {**stream, "id": "S3", "game_name": "Hades"}
    await cog._check_live()
    assert len(chan.sent) == 2 and "<@&5>" in chan.sent[1][1]
    assert cog.state["games"] == ["Hades"]
    ok("nouveau live plus tard : nouvelle annonce + ping")

    # --- session qui change sans passage hors-ligne détecté
    fake.stream = {**stream, "id": "S4"}
    await cog._check_live()
    assert cog.state["stream_id"] == "S4" and len(chan.sent) in (2, 3)
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
    role = SimpleNamespace(id=900, is_assignable=lambda: True)
    guild = SimpleNamespace(get_role=lambda rid: role if rid == 900 else None)
    mem = FakeMember(20); mem.guild = guild; mem.added = []
    async def add_roles(r, reason=None): mem._roles.append(r); mem.added.append(r)
    mem.add_roles = add_roles
    await bot.db.set_reward(1, 900)
    ch = FakeTextChannel()
    await lv.award_xp(FakeMessage(mem, "ok", ch))                      # trop court
    assert await bot.db.get_xp(20) is None
    await lv.award_xp(FakeMessage(mem, "un vrai message", ch))
    assert (await bot.db.get_xp(20))["xp"] == 100
    assert ch.sent and "niveau 1" in ch.sent[0] and mem.added == [role]
    await lv.award_xp(FakeMessage(mem, "un autre message", ch))        # cooldown
    assert (await bot.db.get_xp(20))["xp"] == 100 and len(ch.sent) == 1
    ok("niveaux : XP, anti-abus (cooldown, messages courts), montée de niveau + rôle")

    # /xp réservé au staff, /niveau public
    cmd_xp, cmd_niv = SimpleNamespace(qualified_name="xp reset"), SimpleNamespace(qualified_name="niveau")
    plain = FakeMember(30)
    assert await lv.interaction_check(FakeInteraction(plain, command=cmd_niv)) is True
    try:
        await lv.interaction_check(FakeInteraction(plain, command=cmd_xp)); raise AssertionError
    except discord.app_commands.MissingPermissions:
        pass
    ok("/xp réservé au staff, /niveau public")

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
    print(f"\nTOUT EST OK ({len(PASSED)} vérifications)")


asyncio.run(main())

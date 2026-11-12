"""Bot Discord de ZenoSurTwitch_ : point d'entrée.

Lancement : python bot.py

Chaque message passe par un circuit unique (voir `on_message`) :
modération automatique -> gain d'XP. Un message supprimé par l'anti-spam ne rapporte donc pas d'XP.
"""
from __future__ import annotations

import logging
import signal
import sys
from collections import deque

import discord
from discord import app_commands
from discord.ext import commands

from config import ConfigError, Settings, load_settings
from database import Database

log = logging.getLogger("zenobot")

EXTENSIONS = (
    "cogs.admin",
    "cogs.logs",
    "cogs.welcome",
    "cogs.roles",
    "cogs.moderation",
    "cogs.levels",
    "cogs.community",
    "cogs.twitch",
    "cogs.streamevents",
)
TWITCH_EXTENSIONS = ("cogs.twitch", "cogs.streamevents")


async def reply_error(interaction: discord.Interaction, message: str) -> None:
    """Répond à l'utilisateur (message visible par lui seul), que l'interaction ait déjà reçu une réponse ou non."""
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.HTTPException:
        pass


class HomeTree(app_commands.CommandTree):
    """Arbre de commandes : réservé au serveur d'origine + messages d'erreur lisibles."""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild_id != interaction.client.settings.guild_id:
            await reply_error(interaction, "Ce bot fonctionne uniquement sur son serveur d'origine.")
            return False
        return True

    async def on_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        original = error.original if isinstance(error, app_commands.CommandInvokeError) else error

        if isinstance(original, app_commands.MissingPermissions):
            await reply_error(interaction, "⛔ Tu n'as pas la permission d'utiliser cette commande.")
        elif isinstance(original, app_commands.CommandOnCooldown):
            await reply_error(interaction, f"⏳ Doucement ! Réessaie dans {original.retry_after:.0f} s.")
        elif isinstance(original, app_commands.CheckFailure):
            await reply_error(interaction, "⛔ Tu ne peux pas utiliser cette commande ici.")
        elif isinstance(original, discord.Forbidden):
            await reply_error(
                interaction,
                "⚠️ Je n'ai pas les permissions nécessaires. Vérifie la position de mon rôle et mes permissions "
                "(la commande `/config verifier` fait le diagnostic).",
            )
        else:
            name = interaction.command.qualified_name if interaction.command else "?"
            log.error("Erreur dans la commande /%s", name, exc_info=error)
            await reply_error(interaction, "❌ Une erreur est survenue. Réessaie dans un instant.")


class ZenoBot(commands.Bot):
    def __init__(self, settings: Settings):
        intents = discord.Intents.default()
        intents.members = True          # arrivées/départs, rôles (intent privilégié : à activer sur le portail Discord)
        intents.message_content = True  # filtre de liens, anti-spam, XP (intent privilégié : idem)
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            tree_cls=HomeTree,
            help_command=None,
            # Par défaut le bot ne ping jamais @everyone ni de rôles : chaque envoi autorise explicitement ce qu'il faut.
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True, replied_user=False),
            activity=discord.Activity(type=discord.ActivityType.watching, name=f"twitch.tv/{settings.twitch_channel}"),
            max_messages=2000,
        )
        self.settings = settings
        self.db = Database(settings.database_path)
        # Messages supprimés par le bot lui-même (pour ne pas les logger deux fois)
        self._silent_deletes: deque[int] = deque(maxlen=300)

    # ------------------------------------------------------------------ démarrage / arrêt

    async def setup_hook(self) -> None:
        await self.db.connect()
        for extension in EXTENSIONS:
            if extension in TWITCH_EXTENSIONS and not self.settings.twitch_enabled:
                log.warning("Clés Twitch absentes : annonces de live, crédits et récompense désactivés (%s).", extension)
                continue
            await self.load_extension(extension)

        guild = discord.Object(id=self.settings.guild_id)
        self.tree.copy_global_to(guild=guild)
        synced = await self.tree.sync(guild=guild)
        log.info("%d commandes synchronisées sur le serveur.", len(synced))

    async def on_ready(self) -> None:
        guild = self.home_guild
        if guild is None:
            log.error("Je ne suis pas sur le serveur GUILD_ID=%s : invite-moi avec le lien d'invitation.", self.settings.guild_id)
        else:
            log.info("Connecté en tant que %s sur « %s » (%d membres).", self.user, guild.name, guild.member_count or 0)

    async def close(self) -> None:
        await super().close()
        await self.db.close()

    async def on_guild_join(self, guild: discord.Guild) -> None:
        if guild.id != self.settings.guild_id:
            log.warning("Invité sur un autre serveur (%s) : je le quitte.", guild.name)
            await guild.leave()

    # ------------------------------------------------------------------ aides pour les modules

    def is_home(self, guild: discord.Guild | None) -> bool:
        return guild is not None and guild.id == self.settings.guild_id

    @property
    def home_guild(self) -> discord.Guild | None:
        return self.get_guild(self.settings.guild_id)

    def channel_setting(self, key: str) -> discord.TextChannel | None:
        """Salon configuré avec /config salon (None s'il n'existe pas ou n'est pas défini)."""
        value = self.db.setting(f"channel.{key}")
        if not value:
            return None
        channel = self.get_channel(int(value))
        return channel if isinstance(channel, discord.TextChannel) else None

    def role_setting(self, key: str) -> discord.Role | None:
        """Rôle configuré avec /config role (None s'il n'existe pas ou n'est pas défini)."""
        value = self.db.setting(f"role.{key}")
        guild = self.home_guild
        if not value or guild is None:
            return None
        return guild.get_role(int(value))

    async def send_log(self, embed: discord.Embed) -> None:
        channel = self.channel_setting("logs")
        if channel is None:
            return
        try:
            await channel.send(embed=embed)
        except discord.HTTPException as exc:
            log.warning("Impossible d'écrire dans le salon de logs : %s", exc)

    def mark_silent_delete(self, message_id: int) -> None:
        self._silent_deletes.append(message_id)

    def is_silent_delete(self, message_id: int) -> bool:
        return message_id in self._silent_deletes

    # ------------------------------------------------------------------ circuit des messages

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not self.is_home(message.guild):
            return
        if message.type not in (discord.MessageType.default, discord.MessageType.reply):
            return

        moderation = self.get_cog("Moderation")
        if moderation is not None and await moderation.automod(message):  # type: ignore[attr-defined]
            return  # message supprimé : pas d'XP

        levels = self.get_cog("Levels")
        if levels is not None:
            await levels.award_xp(message)  # type: ignore[attr-defined]


def main() -> None:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"Configuration invalide : {exc}", file=sys.stderr)
        sys.exit(1)

    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    def _stop(signum, frame):  # arrêt propre quand systemd envoie SIGTERM
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _stop)

    bot = ZenoBot(settings)
    bot.run(settings.discord_token, log_handler=None)


if __name__ == "__main__":
    main()

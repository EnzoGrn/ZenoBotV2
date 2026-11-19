"""Rôles à boutons : un clic pour prendre ou retirer un rôle (jeux, plateformes...)."""
from __future__ import annotations

import logging
import re
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils import COLOR_INFO

log = logging.getLogger(__name__)

MAX_SELF_ROLES = 25  # limite Discord : 5 lignes de 5 boutons

# Un rôle qui donne l'un de ces droits ne peut jamais devenir un rôle « en libre-service »
DANGEROUS_PERMS = discord.Permissions(
    administrator=True,
    manage_guild=True,
    manage_roles=True,
    manage_channels=True,
    manage_messages=True,
    manage_webhooks=True,
    manage_nicknames=True,
    kick_members=True,
    ban_members=True,
    moderate_members=True,
    mention_everyone=True,
)

CUSTOM_EMOJI_RE = re.compile(r"^<a?:\w+:\d+>$")
MESSAGE_LINK_RE = re.compile(r"discord(?:app)?\.com/channels/(\d+)/(\d+)/(\d+)")

# Règlement : réagir au message des règles donne un rôle (réglages enregistrés en base)
RULES_MESSAGE = "rules.message"   # « salon:message »
RULES_EMOJI = "rules.emoji"
RULES_ROLE = "rules.role"


def norm_emoji(value: object) -> str:
    """Forme comparable d'un emoji (sans le sélecteur de variante des emojis Unicode)."""
    return str(value).replace("\ufe0f", "").strip()


class SelfRoleButton(discord.ui.DynamicItem[discord.ui.Button], template=r"selfrole:(?P<role_id>[0-9]+)"):
    """Bouton persistant : fonctionne même après un redémarrage du bot."""

    def __init__(self, role_id: int, label: str = "Rôle", emoji: str | discord.PartialEmoji | None = None):
        super().__init__(
            discord.ui.Button(
                label=label,
                emoji=emoji,
                style=discord.ButtonStyle.secondary,
                custom_id=f"selfrole:{role_id}",
            )
        )
        self.role_id = role_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str], /
    ) -> "SelfRoleButton":
        return cls(int(match["role_id"]), label=item.label or "Rôle", emoji=item.emoji)

    async def callback(self, interaction: discord.Interaction) -> None:
        bot = interaction.client
        member = interaction.user
        role = interaction.guild.get_role(self.role_id) if interaction.guild else None
        registered = await bot.db.get_self_role(self.role_id)  # type: ignore[attr-defined]

        if role is None or registered is None or not isinstance(member, discord.Member):
            await interaction.response.send_message("Ce rôle n'est plus disponible.", ephemeral=True)
            return
        if not role.is_assignable():
            await interaction.response.send_message(
                "Je ne peux pas gérer ce rôle pour le moment. Préviens un admin !", ephemeral=True
            )
            return

        try:
            if role in member.roles:
                await member.remove_roles(role, reason="Rôle à boutons")
                text = f"Rôle {role.mention} retiré."
            else:
                await member.add_roles(role, reason="Rôle à boutons")
                text = f"Rôle {role.mention} ajouté !"
        except discord.HTTPException:
            text = "Impossible de modifier ton rôle pour le moment, réessaie plus tard."
        await interaction.response.send_message(text, ephemeral=True)


class Roles(commands.Cog):
    roles_group = app_commands.Group(
        name="roles",
        description="Rôles à boutons",
        guild_only=True,
        default_permissions=discord.Permissions(manage_roles=True),
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.add_dynamic_items(SelfRoleButton)

    async def cog_unload(self) -> None:
        self.bot.remove_dynamic_items(SelfRoleButton)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not interaction.user.guild_permissions.manage_roles:  # type: ignore[union-attr]
            raise app_commands.MissingPermissions(["manage_roles"])
        return True

    @roles_group.command(name="ajouter", description="Ajoute un rôle à la liste des rôles à boutons")
    @app_commands.describe(role="Le rôle proposé", label="Texte du bouton", emoji="Emoji du bouton (facultatif)")
    async def roles_add(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
        label: app_commands.Range[str, 1, 40],
        emoji: Optional[app_commands.Range[str, 1, 64]] = None,
    ) -> None:
        guild = interaction.guild
        assert guild is not None
        if role.is_default() or role.managed:
            await interaction.response.send_message("❌ Ce rôle ne peut pas être proposé (rôle @everyone ou rôle géré par une intégration).", ephemeral=True)
            return
        if role.permissions.value & DANGEROUS_PERMS.value:
            await interaction.response.send_message(
                "❌ Ce rôle donne des permissions de modération ou d'administration : "
                "je refuse de le proposer en libre-service.",
                ephemeral=True,
            )
            return
        if not role.is_assignable():
            await interaction.response.send_message(
                "❌ Je ne peux pas attribuer ce rôle : place mon rôle AU-DESSUS de lui dans la liste des rôles du serveur.",
                ephemeral=True,
            )
            return
        existing = await self.bot.db.list_self_roles()
        if len(existing) >= MAX_SELF_ROLES and not any(r["role_id"] == role.id for r in existing):
            await interaction.response.send_message(f"❌ Maximum {MAX_SELF_ROLES} rôles à boutons.", ephemeral=True)
            return
        if emoji is not None and not (CUSTOM_EMOJI_RE.match(emoji) or not emoji.isascii()):
            await interaction.response.send_message("❌ Emoji invalide : utilise un emoji (ex. 🔔) ou un emoji du serveur.", ephemeral=True)
            return

        await self.bot.db.add_self_role(role.id, label, emoji)
        await interaction.response.send_message(
            f"✅ {role.mention} ajouté. Utilise `/roles panel` pour publier (ou republier) le message avec les boutons.",
            ephemeral=True,
        )

    @roles_group.command(name="retirer", description="Retire un rôle de la liste des rôles à boutons")
    async def roles_remove(self, interaction: discord.Interaction, role: discord.Role) -> None:
        removed = await self.bot.db.remove_self_role(role.id)
        text = (
            f"✅ {role.mention} retiré. Republie le panneau avec `/roles panel` (et supprime l'ancien message)."
            if removed
            else "Ce rôle n'était pas dans la liste."
        )
        await interaction.response.send_message(text, ephemeral=True)

    @roles_group.command(name="liste", description="Affiche les rôles à boutons")
    async def roles_list(self, interaction: discord.Interaction) -> None:
        rows = await self.bot.db.list_self_roles()
        if not rows:
            await interaction.response.send_message("Aucun rôle à boutons pour l'instant. Ajoute-en avec `/roles ajouter`.", ephemeral=True)
            return
        lines = [f"{r['emoji'] or '▫️'} **{r['label']}** → <@&{r['role_id']}>" for r in rows]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @roles_group.command(name="panel", description="Publie le message avec les boutons de rôles")
    @app_commands.describe(salon="Salon où publier", titre="Titre du message", description="Texte du message")
    async def roles_panel(
        self,
        interaction: discord.Interaction,
        salon: discord.TextChannel,
        titre: app_commands.Range[str, 1, 100] = "Choisis tes rôles",
        description: app_commands.Range[str, 1, 1000] = "Clique sur un bouton pour obtenir ou retirer un rôle.",
    ) -> None:
        rows = await self.bot.db.list_self_roles()
        if not rows:
            await interaction.response.send_message("❌ Ajoute d'abord des rôles avec `/roles ajouter`.", ephemeral=True)
            return

        view = discord.ui.View(timeout=None)
        for row in rows:
            view.add_item(SelfRoleButton(row["role_id"], label=row["label"], emoji=row["emoji"]))
        embed = discord.Embed(title=titre, description=description, color=COLOR_INFO)

        try:
            message = await salon.send(embed=embed, view=view)
        except discord.Forbidden:
            await interaction.response.send_message(f"❌ Je ne peux pas écrire dans {salon.mention}.", ephemeral=True)
            return
        await interaction.response.send_message(f"✅ Panneau publié : {message.jump_url}", ephemeral=True)

    # ------------------------------------------------------------------ règlement : réaction = rôle

    def rules_config(self) -> tuple[int, int, str, int] | None:
        """(salon, message, emoji, rôle) du règlement, ou None s'il n'est pas configuré."""
        raw = self.bot.db.setting(RULES_MESSAGE)
        role_id = self.bot.db.setting(RULES_ROLE)
        if not raw or not role_id or ":" not in raw:
            return None
        channel_id, message_id = raw.split(":", 1)
        return int(channel_id), int(message_id), self.bot.db.setting(RULES_EMOJI) or "✅", int(role_id)

    @roles_group.command(name="regles", description="Réagir au message du règlement donne un rôle")
    @app_commands.describe(
        lien="Lien du message du règlement (clic droit sur le message → Copier le lien)",
        role="Rôle donné (par exemple Membre)",
        emoji="Emoji à cliquer (✅ par défaut)",
    )
    async def roles_rules(
        self,
        interaction: discord.Interaction,
        lien: str,
        role: discord.Role,
        emoji: app_commands.Range[str, 1, 64] = "✅",
    ) -> None:
        guild = interaction.guild
        assert guild is not None
        match = MESSAGE_LINK_RE.search(lien)
        if not match or int(match[1]) != guild.id:
            await interaction.response.send_message("❌ Lien invalide : copie le lien du message (clic droit → Copier le lien du message) sur ce serveur.", ephemeral=True)
            return
        if not (CUSTOM_EMOJI_RE.match(emoji) or not emoji.isascii()):
            await interaction.response.send_message("❌ Emoji invalide : utilise un emoji (ex. ✅) ou un emoji du serveur.", ephemeral=True)
            return
        if role.is_default() or role.managed:
            await interaction.response.send_message("❌ Ce rôle ne peut pas être utilisé.", ephemeral=True)
            return
        if role.permissions.value & DANGEROUS_PERMS.value:
            await interaction.response.send_message("❌ Ce rôle donne des permissions de modération ou d'administration : je refuse de le donner automatiquement.", ephemeral=True)
            return
        if not role.is_assignable():
            await interaction.response.send_message("❌ Je ne peux pas attribuer ce rôle : place mon rôle AU-DESSUS de lui dans la liste des rôles du serveur.", ephemeral=True)
            return

        channel = guild.get_channel(int(match[2]))
        try:
            message = await channel.fetch_message(int(match[3])) if channel else None  # type: ignore[union-attr]
            if message is None:
                raise discord.NotFound(None, "salon introuvable")  # type: ignore[arg-type]
            await message.add_reaction(discord.PartialEmoji.from_str(emoji))
        except discord.Forbidden:
            await interaction.response.send_message("❌ Je n'ai pas le droit de lire ce salon ou d'y ajouter des réactions.", ephemeral=True)
            return
        except discord.HTTPException:
            await interaction.response.send_message("❌ Message introuvable (ou emoji refusé par Discord).", ephemeral=True)
            return

        await self.bot.db.set_setting(RULES_MESSAGE, f"{int(match[2])}:{int(match[3])}")
        await self.bot.db.set_setting(RULES_EMOJI, emoji)
        await self.bot.db.set_setting(RULES_ROLE, str(role.id))
        await interaction.response.send_message(
            f"✅ Réagir avec {emoji} à [ce message]({message.jump_url}) donne {role.mention} (retirer la réaction retire le rôle). "
            "Les autres réactions n'ont aucun effet.",
            ephemeral=True,
        )

    @roles_group.command(name="regles-retirer", description="Désactive le rôle donné par réaction au règlement")
    async def roles_rules_off(self, interaction: discord.Interaction) -> None:
        had = self.rules_config() is not None
        for key in (RULES_MESSAGE, RULES_EMOJI, RULES_ROLE):
            await self.bot.db.delete_setting(key)
        await interaction.response.send_message("✅ Désactivé." if had else "Rien n'était configuré.", ephemeral=True)

    def _rules_match(self, payload: discord.RawReactionActionEvent) -> tuple[discord.Guild, discord.Role] | None:
        conf = self.rules_config()
        if conf is None or payload.guild_id is None or payload.message_id != conf[1] or payload.channel_id != conf[0]:
            return None
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None or not self.bot.is_home(guild):  # type: ignore[attr-defined]
            return None
        if norm_emoji(payload.emoji) != norm_emoji(conf[2]):
            return None
        role = guild.get_role(conf[3])
        return (guild, role) if role is not None else None

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        found = self._rules_match(payload)
        member = payload.member
        if found is None or member is None or member.bot:
            return
        _, role = found
        if not role.is_assignable():
            log.warning("Règlement : je ne peux pas attribuer %s (mon rôle est trop bas ?)", role)
            return
        try:
            if role not in member.roles:
                await member.add_roles(role, reason="Règlement accepté")
        except discord.HTTPException as exc:
            log.warning("Règlement : impossible de donner le rôle à %s : %s", member, exc)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        found = self._rules_match(payload)
        if found is None:
            return
        guild, role = found
        member = guild.get_member(payload.user_id)
        if member is None or member.bot or role not in member.roles or not role.is_assignable():
            return
        try:
            await member.remove_roles(role, reason="Réaction au règlement retirée")
        except discord.HTTPException as exc:
            log.warning("Règlement : impossible de retirer le rôle à %s : %s", member, exc)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Roles(bot))

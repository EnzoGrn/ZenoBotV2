"""Rôles à boutons : un clic pour prendre ou retirer un rôle (Notif live, jeux...)."""
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


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Roles(bot))

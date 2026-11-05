"""Niveaux et XP : on gagne de l'XP en discutant, des rôles se débloquent avec les niveaux."""
from __future__ import annotations

import logging
import random
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import config as cfg
from utils import COLOR_INFO, level_from_xp, progress_bar

log = logging.getLogger(__name__)

MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}


class Levels(commands.Cog):
    xp_group = app_commands.Group(
        name="xp",
        description="Gestion de l'XP (réservé au staff)",
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._cooldowns: dict[int, float] = {}

    # ------------------------------------------------------------------ gain d'XP

    async def award_xp(self, message: discord.Message) -> None:
        """Appelé par le bot pour chaque message non filtré par la modération."""
        member = message.author
        if not isinstance(member, discord.Member):
            return
        if len(message.content.strip()) < cfg.XP_MIN_MESSAGE_LENGTH:
            return

        now = time.monotonic()
        if now - self._cooldowns.get(member.id, -1e9) < cfg.XP_COOLDOWN_SECONDS:
            return
        self._cooldowns[member.id] = now
        if len(self._cooldowns) > 5000:
            self._cooldowns = {uid: t for uid, t in self._cooldowns.items() if now - t < cfg.XP_COOLDOWN_SECONDS}

        gain = random.randint(cfg.XP_MIN, cfg.XP_MAX)
        old_xp, new_xp = await self.bot.db.add_xp(member.id, gain, count_message=True)
        old_level, new_level = level_from_xp(old_xp).level, level_from_xp(new_xp).level
        if new_level > old_level:
            await self._level_up(member, new_level, message.channel)

    async def _level_up(self, member: discord.Member, level: int, fallback_channel: discord.abc.Messageable) -> None:
        channel = self.bot.channel_setting("niveaux") or fallback_channel
        try:
            await channel.send(
                f"🎉 {member.mention} passe **niveau {level}** !",
                allowed_mentions=discord.AllowedMentions(users=[member]),
            )
        except discord.HTTPException:
            pass
        await self._apply_rewards(member, level)

    async def _apply_rewards(self, member: discord.Member, level: int) -> list[discord.Role]:
        """Donne les rôles de récompense correspondant au niveau (cumulatifs)."""
        given = []
        for role_id in await self.bot.db.rewards_up_to(level):
            role = member.guild.get_role(role_id)
            if role is None or role in member.roles or not role.is_assignable():
                continue
            try:
                await member.add_roles(role, reason=f"Récompense de niveau {level}")
                given.append(role)
            except discord.HTTPException as exc:
                log.warning("Récompense impossible (%s) : %s", role, exc)
        return given

    # ------------------------------------------------------------------ commandes

    @app_commands.command(name="niveau", description="Affiche ton niveau (ou celui d'un autre membre)")
    @app_commands.describe(membre="Le membre (toi par défaut)")
    @app_commands.guild_only()
    async def niveau(self, interaction: discord.Interaction, membre: Optional[discord.Member] = None) -> None:
        target = membre or interaction.user
        row = await self.bot.db.get_xp(target.id)
        if row is None or row["xp"] == 0:
            who = "Tu n'as" if target.id == interaction.user.id else f"{target.display_name} n'a"
            await interaction.response.send_message(f"{who} pas encore d'XP. Discute un peu sur le serveur ! 💬", ephemeral=True)
            return
        info = level_from_xp(row["xp"])
        rank = await self.bot.db.get_rank(target.id)
        embed = discord.Embed(title=target.display_name, color=COLOR_INFO)
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="Niveau", value=str(info.level))
        embed.add_field(name="Classement", value=f"#{rank}")
        embed.add_field(name="Messages", value=str(row["messages"]))
        embed.add_field(
            name=f"Progression vers le niveau {info.level + 1}",
            value=f"{progress_bar(info.xp_into_level, info.xp_needed)}  {info.xp_into_level} / {info.xp_needed} XP",
            inline=False,
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="top", description="Classement des membres les plus actifs")
    @app_commands.describe(page="Numéro de page")
    @app_commands.guild_only()
    async def top(self, interaction: discord.Interaction, page: app_commands.Range[int, 1, 1000] = 1) -> None:
        per_page = 10
        total = await self.bot.db.count_ranked()
        if total == 0:
            await interaction.response.send_message("Personne n'a encore d'XP.", ephemeral=True)
            return
        pages = (total + per_page - 1) // per_page
        if page > pages:
            await interaction.response.send_message(f"Il n'y a que {pages} page(s).", ephemeral=True)
            return

        rows = await self.bot.db.top_xp(per_page, (page - 1) * per_page)
        lines = []
        for i, row in enumerate(rows, start=(page - 1) * per_page + 1):
            member = interaction.guild.get_member(row["user_id"])  # type: ignore[union-attr]
            name = discord.utils.escape_markdown(member.display_name) if member else "*ancien membre*"
            lines.append(f"{MEDALS.get(i, f'`{i}.`')} **{name}** — niveau {level_from_xp(row['xp']).level} ({row['xp']} XP)")
        embed = discord.Embed(title="🏆 Classement", description="\n".join(lines), color=COLOR_INFO)
        embed.set_footer(text=f"Page {page}/{pages}")
        await interaction.response.send_message(embed=embed)

    # ------------------------------------------------------------------ gestion staff

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Ne concerne que le groupe /xp (les commandes /niveau et /top sont publiques)
        command = interaction.command
        if command is not None and command.qualified_name.startswith("xp "):
            if not interaction.user.guild_permissions.manage_guild:  # type: ignore[union-attr]
                raise app_commands.MissingPermissions(["manage_guild"])
        return True

    @xp_group.command(name="ajouter", description="Ajoute (ou retire, avec un nombre négatif) de l'XP à un membre")
    async def xp_add(
        self,
        interaction: discord.Interaction,
        membre: discord.Member,
        quantite: app_commands.Range[int, -1_000_000, 1_000_000],
    ) -> None:
        old_xp, new_xp = await self.bot.db.add_xp(membre.id, quantite)
        new_level = level_from_xp(new_xp).level
        if new_level > level_from_xp(old_xp).level:
            await self._apply_rewards(membre, new_level)
        await interaction.response.send_message(
            f"✅ {membre.mention} : {old_xp} → **{new_xp} XP** (niveau {new_level}).", ephemeral=True
        )

    @xp_group.command(name="reset", description="Remet l'XP d'un membre à zéro")
    async def xp_reset(self, interaction: discord.Interaction, membre: discord.Member) -> None:
        done = await self.bot.db.reset_xp(membre.id)
        await interaction.response.send_message(
            f"✅ XP de {membre.mention} remis à zéro." if done else f"{membre.mention} n'avait pas d'XP.", ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Levels(bot))

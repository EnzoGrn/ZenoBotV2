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
from utils import COLOR_INFO, level_from_xp, progress_bar, truncate

log = logging.getLogger(__name__)

LEADERBOARD_PAGE_SIZE = 10


class LeaderboardView(discord.ui.View):
    """Boutons du classement : précédent, suivant, « ma page ». Réservés à l'auteur de la commande."""

    def __init__(self, cog: "Levels", owner_id: int, page: int, pages: int):
        super().__init__(timeout=300)
        self.cog, self.owner_id, self.page, self.pages = cog, owner_id, page, pages
        self.message: Optional[discord.Message] = None
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        self.previous.disabled = self.page <= 1
        self.next.disabled = self.page >= self.pages

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Ce classement est celui de quelqu'un d'autre : lance `/top` pour avoir le tien.", ephemeral=True
            )
            return False
        return True

    async def _show(self, interaction: discord.Interaction, page: int) -> None:
        embed, self.pages = await self.cog.leaderboard_embed(interaction.guild, page, self.owner_id)  # type: ignore[arg-type]
        self.page = min(max(page, 1), self.pages)
        self._sync_buttons()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._show(interaction, self.page - 1)

    @discord.ui.button(label="📍 Ma page", style=discord.ButtonStyle.primary)
    async def mine(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        rank = await self.cog.bot.db.get_rank(self.owner_id)
        if not rank:
            await interaction.response.send_message("Tu n'as pas encore d'XP : discute un peu ! 💬", ephemeral=True)
            return
        await self._show(interaction, (rank - 1) // LEADERBOARD_PAGE_SIZE + 1)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._show(interaction, self.page + 1)

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True  # type: ignore[union-attr]
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


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
            await self._level_up(member, old_level, new_level, message.channel)

    async def _level_up(
        self, member: discord.Member, old_level: int, level: int, fallback_channel: discord.abc.Messageable
    ) -> None:
        """Synchronise les rangs ; n'annonce que les changements de rang (pas chaque niveau)."""
        await self.sync_rewards(member, level)
        rewards = await self.bot.db.list_rewards()
        crossed = [r for r in rewards if old_level < r["level"] <= level]
        if not crossed:
            return
        role = member.guild.get_role(crossed[-1]["role_id"])
        rank_name = role.name if role else f"niveau {crossed[-1]['level']}"
        channel = self.bot.channel_setting("niveaux") or fallback_channel
        text = f"🎉 {member.mention} passe **{rank_name}** (niveau {level}) !"
        try:
            await channel.send(text, allowed_mentions=discord.AllowedMentions(users=[member]))
        except discord.HTTPException:
            pass

    async def sync_rewards(self, member: discord.Member, level: int) -> tuple[list[discord.Role], list[discord.Role]]:
        """Aligne les rôles de récompense du membre sur son niveau. Renvoie (rôles donnés, rôles retirés).

        Mode « remplacer » (défaut) : un seul rang à la fois, le plus haut atteint.
        Mode « cumuler » : tous les rangs atteints sont gardés.
        Dans les deux cas, un rang au-dessus du niveau actuel est retiré (XP retirée par le staff).
        """
        rewards = await self.bot.db.list_rewards()
        reached = [r["role_id"] for r in rewards if r["level"] <= level]
        if self.bot.db.setting("levels.mode", "remplacer") == "cumuler":
            wanted = set(reached)
        else:
            wanted = set(reached[-1:])
        managed = {r["role_id"] for r in rewards}

        given: list[discord.Role] = []
        removed: list[discord.Role] = []
        have = {role.id for role in member.roles}
        for role_id in managed:
            role = member.guild.get_role(role_id)
            if role is None or not role.is_assignable():
                continue
            try:
                if role_id in wanted and role_id not in have:
                    await member.add_roles(role, reason=f"Rang du niveau {level}")
                    given.append(role)
                elif role_id not in wanted and role_id in have:
                    await member.remove_roles(role, reason=f"Rang du niveau {level}")
                    removed.append(role)
            except discord.HTTPException as exc:
                log.warning("Synchronisation du rang impossible (%s) : %s", role, exc)
        return given, removed

    # ------------------------------------------------------------------ commandes

    @app_commands.command(name="rank", description="Affiche ton rang et tes stats (ou celles d'un autre membre)")
    @app_commands.describe(membre="Le membre (toi par défaut)")
    @app_commands.guild_only()
    async def rank(self, interaction: discord.Interaction, membre: Optional[discord.Member] = None) -> None:
        target = membre or interaction.user
        row = await self.bot.db.get_xp(target.id)
        if row is None or row["xp"] == 0:
            who = "Tu n'as" if target.id == interaction.user.id else f"{target.display_name} n'a"
            await interaction.response.send_message(f"{who} pas encore d'XP. Discute un peu sur le serveur ! 💬", ephemeral=True)
            return
        info = level_from_xp(row["xp"])
        rank = await self.bot.db.get_rank(target.id)
        rewards = await self.bot.db.list_rewards()
        current = [r for r in rewards if r["level"] <= info.level]
        upcoming = [r for r in rewards if r["level"] > info.level]

        def role_name(reward) -> str:
            role = interaction.guild.get_role(reward["role_id"])  # type: ignore[union-attr]
            return role.name if role else f"niveau {reward['level']}"

        color = COLOR_INFO
        if current:
            rank_role = interaction.guild.get_role(current[-1]["role_id"])  # type: ignore[union-attr]
            if rank_role is not None and rank_role.colour.value:
                color = rank_role.colour.value
        embed = discord.Embed(title=target.display_name, color=color)
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="Rang", value=role_name(current[-1]) if current else "Aucun rang")
        embed.add_field(name="Niveau", value=str(info.level))
        embed.add_field(name="Classement", value=f"#{rank}")
        embed.add_field(name="Messages", value=str(row["messages"]))
        embed.add_field(name="XP total", value=str(row["xp"]))
        if upcoming:
            embed.add_field(name="Prochain rang", value=f"{role_name(upcoming[0])} (niveau {upcoming[0]['level']})")
        embed.add_field(
            name=f"Progression vers le niveau {info.level + 1}",
            value=f"{progress_bar(info.xp_into_level, info.xp_needed)}  {info.xp_into_level} / {info.xp_needed} XP",
            inline=False,
        )
        await interaction.response.send_message(embed=embed)

    async def leaderboard_embed(self, guild: discord.Guild, page: int, viewer_id: int) -> tuple[discord.Embed, int]:
        """Construit la page `page` du classement ; la ligne du spectateur est surlignée (bloc « diff » : vert)."""
        per_page = LEADERBOARD_PAGE_SIZE
        total = await self.bot.db.count_ranked()
        pages = max(1, (total + per_page - 1) // per_page)
        page = min(max(page, 1), pages)
        first = (page - 1) * per_page + 1
        rows = await self.bot.db.top_xp(per_page, first - 1)

        lines = []
        for i, row in enumerate(rows, start=first):
            member = guild.get_member(row["user_id"])
            name = (member.display_name if member else "ancien membre").replace("`", "'")
            name = truncate(name, 16).ljust(16)
            info = level_from_xp(row["xp"])
            pct = 100 * info.xp_into_level / info.xp_needed
            mark = "+" if row["user_id"] == viewer_id else " "
            lines.append(f"{mark}{i:>3}. {name} niv {info.level:<3} {pct:3.0f}% {row['xp']:>8}")
        embed = discord.Embed(
            title="🏆 Classement",
            description="```diff\n" + "\n".join(lines) + "\n```",
            color=COLOR_INFO,
        )
        mine = await self.bot.db.get_rank(viewer_id)
        footer = f"Page {page}/{pages}"
        if mine:
            footer += f" · Ta position : #{mine}"
        embed.set_footer(text=footer)
        return embed, pages

    @app_commands.command(name="top", description="Classement des membres les plus actifs")
    @app_commands.describe(page="Numéro de page (par défaut : celle où tu te trouves)")
    @app_commands.guild_only()
    async def top(self, interaction: discord.Interaction, page: Optional[app_commands.Range[int, 1, 1000]] = None) -> None:
        if await self.bot.db.count_ranked() == 0:
            await interaction.response.send_message("Personne n'a encore d'XP.", ephemeral=True)
            return
        if page is None:
            mine = await self.bot.db.get_rank(interaction.user.id)
            page = (mine - 1) // LEADERBOARD_PAGE_SIZE + 1 if mine else 1
        embed, pages = await self.leaderboard_embed(interaction.guild, page, interaction.user.id)  # type: ignore[arg-type]
        view = LeaderboardView(self, interaction.user.id, min(page, pages), pages)
        await interaction.response.send_message(embed=embed, view=view)
        view.message = await interaction.original_response()

    # ------------------------------------------------------------------ gestion staff

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Ne concerne que le groupe /xp (les commandes /rank et /top sont publiques)
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
        await self.sync_rewards(membre, new_level)
        await interaction.response.send_message(
            f"✅ {membre.mention} : {old_xp} → **{new_xp} XP** (niveau {new_level}).", ephemeral=True
        )

    @xp_group.command(name="reset", description="Remet l'XP d'un membre à zéro")
    async def xp_reset(self, interaction: discord.Interaction, membre: discord.Member) -> None:
        done = await self.bot.db.reset_xp(membre.id)
        await self.sync_rewards(membre, 0)
        await interaction.response.send_message(
            f"✅ XP de {membre.mention} remis à zéro." if done else f"{membre.mention} n'avait pas d'XP.", ephemeral=True
        )

    @xp_group.command(name="synchroniser", description="Attribue à chaque membre le rang correspondant à son niveau")
    async def xp_sync(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        assert guild is not None
        changed = 0
        for row in await self.bot.db.top_xp(100_000):
            member = guild.get_member(row["user_id"])
            if member is None:
                continue
            given, removed = await self.sync_rewards(member, level_from_xp(row["xp"]).level)
            if given or removed:
                changed += 1
        await interaction.followup.send(f"✅ Rangs synchronisés : {changed} membre(s) mis à jour.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Levels(bot))

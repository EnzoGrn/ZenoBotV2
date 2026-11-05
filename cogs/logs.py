"""Journal du serveur : arrivées, départs, messages supprimés ou modifiés."""
from __future__ import annotations

import logging
from datetime import timedelta

import discord
from discord.ext import commands

from utils import COLOR_DANGER, COLOR_INFO, COLOR_OK, COLOR_WARN, discord_ts, truncate, utcnow

log = logging.getLogger(__name__)


def _safe(text: str, limit: int = 900) -> str:
    return discord.utils.escape_markdown(truncate(text, limit)) or "*(vide)*"


class Logs(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if not self.bot.is_home(member.guild):
            return
        embed = discord.Embed(
            title="Arrivée d'un membre",
            description=f"{member.mention} (`{member}`)",
            color=COLOR_OK,
            timestamp=utcnow(),
        )
        embed.add_field(name="Compte créé", value=discord_ts(member.created_at, "R"))
        if utcnow() - member.created_at < timedelta(days=7):
            embed.add_field(name="⚠️ Attention", value="Compte très récent (moins de 7 jours)", inline=False)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"ID : {member.id}")
        await self.bot.send_log(embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if not self.bot.is_home(member.guild):
            return
        roles = [r.mention for r in member.roles if not r.is_default()]
        embed = discord.Embed(
            title="Départ d'un membre",
            description=f"{member.mention} (`{member}`)",
            color=COLOR_WARN,
            timestamp=utcnow(),
        )
        if member.joined_at:
            embed.add_field(name="Arrivé", value=discord_ts(member.joined_at, "R"))
        if roles:
            embed.add_field(name="Rôles", value=truncate(" ".join(roles), 1024), inline=False)
        embed.set_footer(text=f"ID : {member.id}")
        await self.bot.send_log(embed)

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        if message.author.bot or not self.bot.is_home(message.guild):
            return
        if self.bot.is_silent_delete(message.id):
            return
        embed = discord.Embed(
            title="Message supprimé",
            description=f"Auteur : {message.author.mention} — Salon : {message.channel.mention}",  # type: ignore[union-attr]
            color=COLOR_DANGER,
            timestamp=utcnow(),
        )
        embed.add_field(name="Contenu", value=_safe(message.content), inline=False)
        if message.attachments:
            embed.add_field(name="Pièces jointes", value=str(len(message.attachments)))
        embed.set_footer(text=f"ID auteur : {message.author.id}")
        await self.bot.send_log(embed)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if after.author.bot or not self.bot.is_home(after.guild) or before.content == after.content:
            return
        embed = discord.Embed(
            title="Message modifié",
            description=f"Auteur : {after.author.mention} — [Aller au message]({after.jump_url})",
            color=COLOR_INFO,
            timestamp=utcnow(),
        )
        embed.add_field(name="Avant", value=_safe(before.content), inline=False)
        embed.add_field(name="Après", value=_safe(after.content), inline=False)
        embed.set_footer(text=f"ID auteur : {after.author.id}")
        await self.bot.send_log(embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Logs(bot))

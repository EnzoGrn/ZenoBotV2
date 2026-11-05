"""Accueil des nouveaux membres : rôle d'arrivée + message de bienvenue."""
from __future__ import annotations

import logging

import discord
from discord.ext import commands

from cogs.admin import DEFAULT_WELCOME

log = logging.getLogger(__name__)


def render_welcome(template: str, member: discord.Member) -> str:
    return (
        template.replace("{member}", member.mention)
        .replace("{server}", member.guild.name)
        .replace("{count}", str(member.guild.member_count or ""))
    )


class Welcome(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _greet(self, member: discord.Member) -> None:
        role = self.bot.role_setting("auto")
        if role is not None:
            try:
                await member.add_roles(role, reason="Rôle d'arrivée")
            except discord.HTTPException as exc:
                log.warning("Impossible de donner le rôle d'arrivée à %s : %s", member, exc)

        channel = self.bot.channel_setting("bienvenue")
        if channel is None:
            return
        text = render_welcome(self.bot.db.setting("welcome.message") or DEFAULT_WELCOME, member)
        try:
            await channel.send(text, allowed_mentions=discord.AllowedMentions(users=[member]))
        except discord.HTTPException as exc:
            log.warning("Impossible d'envoyer le message de bienvenue : %s", exc)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot or not self.bot.is_home(member.guild):
            return
        # Si le serveur a un écran de règles (« membre en attente »), on accueille une fois qu'il l'a validé.
        if member.pending:
            return
        await self._greet(member)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if after.bot or not self.bot.is_home(after.guild):
            return
        if before.pending and not after.pending:
            await self._greet(after)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Welcome(bot))

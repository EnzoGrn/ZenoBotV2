"""Communauté : sondages, idées, recherche de joueurs."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config as cfg
from utils import COLOR_INFO, COLOR_MUTED, COLOR_OK, COLOR_WARN, COLOR_DANGER, parse_iso, truncate, utcnow

log = logging.getLogger(__name__)

IDEA_STATUSES = {
    "reflexion": ("🤔 En réflexion", COLOR_WARN),
    "acceptee": ("✅ Acceptée", COLOR_OK),
    "refusee": ("❌ Refusée", COLOR_DANGER),
    "realisee": ("🎉 Réalisée", COLOR_INFO),
}
Choice100 = app_commands.Range[str, 1, 55]


# ============================================================================ recherche de joueurs

class LFGView(discord.ui.View):
    """Boutons persistants des annonces « recherche de joueurs ». L'état est stocké en base."""

    def __init__(self, *, full: bool = False, closed: bool = False):
        super().__init__(timeout=None)
        if closed:
            for child in self.children:
                child.disabled = True  # type: ignore[union-attr]
        elif full:
            self.join.disabled = True

    @staticmethod
    def _cog(interaction: discord.Interaction) -> "Community":
        return interaction.client.get_cog("Community")  # type: ignore[return-value]

    @discord.ui.button(label="Rejoindre", emoji="✅", style=discord.ButtonStyle.success, custom_id="lfg:join")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._cog(interaction).lfg_join(interaction)

    @discord.ui.button(label="Quitter", emoji="🚪", style=discord.ButtonStyle.secondary, custom_id="lfg:leave")
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._cog(interaction).lfg_leave(interaction)

    @discord.ui.button(label="Clôturer", emoji="🔒", style=discord.ButtonStyle.danger, custom_id="lfg:close")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._cog(interaction).lfg_close(interaction)


class Community(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.add_view(LFGView())
        self.lfg_expiry_loop.start()

    async def cog_unload(self) -> None:
        self.lfg_expiry_loop.cancel()

    # ============================================================================ sondages

    @app_commands.command(name="sondage", description="Crée un sondage (2 à 5 choix)")
    @app_commands.describe(
        question="La question posée",
        choix1="Premier choix",
        choix2="Deuxième choix",
        choix3="Troisième choix (facultatif)",
        choix4="Quatrième choix (facultatif)",
        choix5="Cinquième choix (facultatif)",
        duree="Durée en heures (1 à 168, 24 par défaut)",
        multiple="Autoriser plusieurs réponses",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.checks.has_permissions(manage_messages=True)
    async def sondage(
        self,
        interaction: discord.Interaction,
        question: app_commands.Range[str, 1, 300],
        choix1: Choice100,
        choix2: Choice100,
        choix3: Optional[Choice100] = None,
        choix4: Optional[Choice100] = None,
        choix5: Optional[Choice100] = None,
        duree: app_commands.Range[int, 1, 168] = 24,
        multiple: bool = False,
    ) -> None:
        poll = discord.Poll(question=question, duration=timedelta(hours=duree), multiple=multiple)
        for choice in (choix1, choix2, choix3, choix4, choix5):
            if choice:
                poll.add_answer(text=choice)
        try:
            await interaction.response.send_message(poll=poll)
        except discord.Forbidden:
            await interaction.response.send_message("❌ Il me manque la permission « Créer des sondages » dans ce salon.", ephemeral=True)

    # ============================================================================ idées

    @app_commands.command(name="idee", description="Propose une idée (jeu, format de stream, événement...)")
    @app_commands.describe(idee="Ton idée, en quelques phrases")
    @app_commands.guild_only()
    @app_commands.checks.cooldown(1, 120.0, key=lambda i: i.user.id)
    async def idee(self, interaction: discord.Interaction, idee: app_commands.Range[str, 5, 1000]) -> None:
        channel = self.bot.channel_setting("idees")
        if channel is None:
            await interaction.response.send_message("❌ Le salon des idées n'est pas configuré (`/config salon`).", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        embed = discord.Embed(description=idee, color=COLOR_INFO, timestamp=utcnow())
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        try:
            message = await channel.send(embed=embed)
        except discord.Forbidden:
            await interaction.followup.send(f"❌ Je ne peux pas écrire dans {channel.mention}.", ephemeral=True)
            return

        embed.set_footer(text=f"Idée • ID : {message.id}")
        await message.edit(embed=embed)
        for emoji in ("👍", "👎"):
            try:
                await message.add_reaction(emoji)
            except discord.HTTPException:
                break
        try:
            await message.create_thread(name=truncate(f"Idée de {interaction.user.display_name} : {idee}", 90), auto_archive_duration=1440)
        except discord.HTTPException:
            pass  # pas grave : l'idée est publiée, seul le fil de discussion manque
        await interaction.followup.send(f"✅ Ton idée est publiée : {message.jump_url}", ephemeral=True)

    @app_commands.command(name="idee-statut", description="Change le statut d'une idée (réservé au staff)")
    @app_commands.describe(identifiant="L'ID inscrit en bas de l'idée", statut="Nouveau statut")
    @app_commands.choices(statut=[app_commands.Choice(name=label, value=key) for key, (label, _) in IDEA_STATUSES.items()])
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.checks.has_permissions(manage_messages=True)
    async def idee_statut(self, interaction: discord.Interaction, identifiant: str, statut: app_commands.Choice[str]) -> None:
        channel = self.bot.channel_setting("idees")
        if channel is None:
            await interaction.response.send_message("❌ Le salon des idées n'est pas configuré.", ephemeral=True)
            return
        if not identifiant.strip().isdigit():
            await interaction.response.send_message("❌ L'identifiant doit être un nombre (il est inscrit en bas de l'idée).", ephemeral=True)
            return
        try:
            message = await channel.fetch_message(int(identifiant))
        except discord.NotFound:
            await interaction.response.send_message("❌ Idée introuvable dans le salon des idées.", ephemeral=True)
            return
        if message.author.id != self.bot.user.id or not message.embeds:  # type: ignore[union-attr]
            await interaction.response.send_message("❌ Ce message n'est pas une idée.", ephemeral=True)
            return

        label, color = IDEA_STATUSES[statut.value]
        embed = message.embeds[0].copy()
        embed.color = color
        embed.clear_fields()
        embed.add_field(name="Statut", value=label)
        await message.edit(embed=embed)
        await interaction.response.send_message(f"✅ Statut mis à jour : {label}", ephemeral=True)

    # ============================================================================ recherche de joueurs

    async def _render_lfg(self, message_id: int) -> Optional[tuple[discord.Embed, LFGView]]:
        lfg = await self.bot.db.get_lfg(message_id)
        if lfg is None:
            return None
        players = await self.bot.db.lfg_players(message_id)
        closed = bool(lfg["closed"])
        full = len(players) >= lfg["slots"]

        color = COLOR_MUTED if closed else COLOR_OK if full else COLOR_INFO
        embed = discord.Embed(title=f"🎮 {lfg['game']}", description=lfg["note"] or "Qui est partant ?", color=color)
        embed.add_field(name="Organisateur", value=f"<@{lfg['author_id']}>")
        embed.add_field(name="Places", value=f"{len(players)} / {lfg['slots']}")
        embed.add_field(name="Joueurs inscrits", value="\n".join(f"• <@{uid}>" for uid in players) or "*Personne pour l'instant*", inline=False)
        embed.timestamp = parse_iso(lfg["created_at"])
        embed.set_footer(text="Annonce clôturée" if closed else "Complet !" if full else "Clique sur « Rejoindre » pour t'inscrire")
        return embed, LFGView(full=full, closed=closed)

    async def _refresh_lfg(self, interaction: discord.Interaction, message_id: int) -> None:
        rendered = await self._render_lfg(message_id)
        if rendered is None:
            await interaction.response.send_message("Cette annonce n'existe plus.", ephemeral=True)
            return
        embed, view = rendered
        await interaction.response.edit_message(embed=embed, view=view)

    @app_commands.command(name="cherche-joueurs", description="Publie une annonce pour trouver des partenaires de jeu")
    @app_commands.describe(
        jeu="À quel jeu ?",
        places="Combien de joueurs recherches-tu (sans te compter) ?",
        message="Détails : mode de jeu, niveau, heure... (facultatif)",
        ping="Prévenir le rôle « joueurs » (s'il est configuré)",
    )
    @app_commands.guild_only()
    async def cherche_joueurs(
        self,
        interaction: discord.Interaction,
        jeu: app_commands.Range[str, 1, 60],
        places: app_commands.Range[int, 1, 15] = 3,
        message: Optional[app_commands.Range[str, 1, 300]] = None,
        ping: bool = False,
    ) -> None:
        if await self.bot.db.lfg_open_by_author(interaction.user.id):
            await interaction.response.send_message("Tu as déjà une annonce ouverte : clôture-la avant d'en publier une autre.", ephemeral=True)
            return

        channel = self.bot.channel_setting("joueurs") or interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("❌ Utilise cette commande dans un salon textuel.", ephemeral=True)
            return

        role = self.bot.role_setting("joueurs") if ping else None
        content = f"{role.mention} " if role else ""
        content += f"**{discord.utils.escape_markdown(interaction.user.display_name)}** cherche des joueurs pour **{discord.utils.escape_markdown(jeu)}** !"
        mentions = discord.AllowedMentions(roles=[role] if role else False, users=False)

        embed = discord.Embed(title=f"🎮 {jeu}", description=message or "Qui est partant ?", color=COLOR_INFO)
        try:
            sent = await channel.send(content, embed=embed, view=LFGView(), allowed_mentions=mentions)
        except discord.Forbidden:
            await interaction.response.send_message(f"❌ Je ne peux pas écrire dans {channel.mention}.", ephemeral=True)
            return

        await self.bot.db.create_lfg(sent.id, channel.id, interaction.user.id, jeu, places, message)
        rendered = await self._render_lfg(sent.id)
        if rendered:
            await sent.edit(embed=rendered[0], view=rendered[1])
        await interaction.response.send_message(f"✅ Annonce publiée : {sent.jump_url}", ephemeral=True)

    async def lfg_join(self, interaction: discord.Interaction) -> None:
        message = interaction.message
        assert message is not None
        lfg = await self.bot.db.get_lfg(message.id)
        if lfg is None or lfg["closed"]:
            await interaction.response.send_message("Cette annonce n'est plus active.", ephemeral=True)
            return
        if interaction.user.id == lfg["author_id"]:
            await interaction.response.send_message("Tu es l'organisateur : ta place est déjà réservée !", ephemeral=True)
            return

        status = await self.bot.db.lfg_join(message.id, interaction.user.id)
        if status == "already":
            await interaction.response.send_message("Tu es déjà inscrit.", ephemeral=True)
        elif status == "full":
            await interaction.response.send_message("Désolé, l'annonce est complète.", ephemeral=True)
        elif status == "closed":
            await interaction.response.send_message("Cette annonce n'est plus active.", ephemeral=True)
        else:
            await self._refresh_lfg(interaction, message.id)
            players = await self.bot.db.lfg_players(message.id)
            try:
                await interaction.followup.send(f"✅ Tu es inscrit ! <@{lfg['author_id']}> est prévenu.", ephemeral=True)
                await message.channel.send(
                    f"<@{lfg['author_id']}> : **{discord.utils.escape_markdown(interaction.user.display_name)}** rejoint ta partie de "
                    f"**{discord.utils.escape_markdown(lfg['game'])}** ({len(players)}/{lfg['slots']}).",
                    delete_after=60,
                    allowed_mentions=discord.AllowedMentions(users=[discord.Object(id=lfg["author_id"])]),
                )
            except discord.HTTPException:
                pass

    async def lfg_leave(self, interaction: discord.Interaction) -> None:
        message = interaction.message
        assert message is not None
        lfg = await self.bot.db.get_lfg(message.id)
        if lfg is None or lfg["closed"]:
            await interaction.response.send_message("Cette annonce n'est plus active.", ephemeral=True)
            return
        if not await self.bot.db.lfg_leave(message.id, interaction.user.id):
            await interaction.response.send_message("Tu n'es pas inscrit à cette annonce.", ephemeral=True)
            return
        await self._refresh_lfg(interaction, message.id)

    async def lfg_close(self, interaction: discord.Interaction) -> None:
        message = interaction.message
        assert message is not None
        lfg = await self.bot.db.get_lfg(message.id)
        if lfg is None or lfg["closed"]:
            await interaction.response.send_message("Cette annonce est déjà clôturée.", ephemeral=True)
            return
        is_author = interaction.user.id == lfg["author_id"]
        is_staff = isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.manage_messages
        if not (is_author or is_staff):
            await interaction.response.send_message("Seul l'organisateur (ou un modo) peut clôturer l'annonce.", ephemeral=True)
            return
        await self.bot.db.lfg_close(message.id)
        await self._refresh_lfg(interaction, message.id)

    @tasks.loop(minutes=10)
    async def lfg_expiry_loop(self) -> None:
        try:
            cutoff = (utcnow() - timedelta(hours=cfg.LFG_EXPIRY_HOURS)).isoformat(timespec="seconds")
            for lfg in await self.bot.db.lfg_expired(cutoff):
                await self.bot.db.lfg_close(lfg["message_id"])
                channel = self.bot.get_channel(lfg["channel_id"])
                rendered = await self._render_lfg(lfg["message_id"])
                if isinstance(channel, discord.TextChannel) and rendered:
                    try:
                        await channel.get_partial_message(lfg["message_id"]).edit(embed=rendered[0], view=rendered[1])
                    except discord.HTTPException:
                        pass
        except Exception:
            log.exception("Erreur pendant la clôture automatique des annonces")

    @lfg_expiry_loop.before_loop
    async def _before_expiry(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Community(bot))

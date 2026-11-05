"""Modération : anti-spam, filtre de liens, avertissements, sanctions."""
from __future__ import annotations

import logging
import time
from collections import deque
from datetime import timedelta
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import config as cfg
from utils import (
    COLOR_DANGER,
    COLOR_OK,
    COLOR_WARN,
    INVITE_RE,
    discord_ts,
    extract_hosts,
    format_duration,
    host_allowed,
    parse_duration,
    parse_iso,
    truncate,
    utcnow,
)

log = logging.getLogger(__name__)

MAX_TIMEOUT = timedelta(days=28)  # limite imposée par Discord
NO_REASON = "Aucune raison précisée"


class Moderation(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._recent: dict[int, deque[tuple[float, discord.Message]]] = {}
        self._last_content: dict[int, tuple[str, int, float]] = {}

    # ================================================================== modération automatique

    @staticmethod
    def _is_staff(member: discord.Member) -> bool:
        perms = member.guild_permissions
        return perms.administrator or perms.manage_messages

    async def automod(self, message: discord.Message) -> bool:
        """Analyse un message. Renvoie True si le message a été supprimé."""
        member = message.author
        if not isinstance(member, discord.Member) or self._is_staff(member):
            return False
        content = message.content or ""

        # 1. Invitations vers d'autres serveurs Discord : toujours bloquées
        if INVITE_RE.search(content):
            await self._remove(message, "invitation Discord", "Les invitations vers d'autres serveurs ne sont pas autorisées ici.")
            return True

        # 2. Liens non autorisés
        if self.bot.db.setting("filter.links", "1") == "1" and content:
            habitue = self.bot.role_setting("habitue")
            if habitue is None or habitue not in member.roles:
                allowed = self.bot.db.domains
                bad = [h for h in extract_hosts(content) if not host_allowed(h, allowed)]
                if bad:
                    await self._remove(message, f"lien non autorisé ({bad[0] or 'illisible'})", "Ce lien n'est pas autorisé. Demande à un modo si c'est important !")
                    return True

        # 3. Mentions de masse
        mentions = len(message.mentions) + len(message.role_mentions)
        if mentions >= cfg.SPAM_MAX_MENTIONS:
            await self._spam_sanction(message, f"mentions de masse ({mentions})")
            return True

        # 4. Messages en rafale
        now = time.monotonic()
        queue = self._recent.setdefault(member.id, deque())
        queue.append((now, message))
        while queue and now - queue[0][0] > cfg.SPAM_WINDOW_SECONDS:
            queue.popleft()
        if len(queue) > cfg.SPAM_MAX_MESSAGES:
            await self._spam_sanction(message, f"{len(queue)} messages en {cfg.SPAM_WINDOW_SECONDS} s", extra=[m for _, m in queue])
            queue.clear()
            return True

        # 5. Même message répété
        normalized = content.strip().lower()
        if len(normalized) >= 6:
            last = self._last_content.get(member.id)
            if last and last[0] == normalized and now - last[2] <= cfg.SPAM_DUPLICATE_WINDOW_SECONDS:
                count = last[1] + 1
            else:
                count = 1
            self._last_content[member.id] = (normalized, count, now if count == 1 else (last[2] if last else now))
            if count >= cfg.SPAM_DUPLICATE_MAX:
                self._last_content.pop(member.id, None)
                await self._spam_sanction(message, f"message répété {count} fois")
                return True

        # Nettoyage occasionnel de la mémoire
        if len(self._recent) > 2000:
            self._recent = {uid: q for uid, q in self._recent.items() if q and now - q[-1][0] <= cfg.SPAM_WINDOW_SECONDS}
            self._last_content = {uid: v for uid, v in self._last_content.items() if now - v[2] <= cfg.SPAM_DUPLICATE_WINDOW_SECONDS}
        return False

    async def _delete(self, message: discord.Message) -> None:
        self.bot.mark_silent_delete(message.id)
        try:
            await message.delete()
        except (discord.NotFound, discord.Forbidden):
            pass
        except discord.HTTPException as exc:
            log.warning("Suppression impossible : %s", exc)

    async def _remove(self, message: discord.Message, reason: str, notice: str) -> None:
        """Supprime un message interdit, prévient le membre et journalise."""
        member = message.author
        await self._delete(message)
        try:
            await message.channel.send(
                f"{member.mention} {notice}",
                delete_after=10,
                allowed_mentions=discord.AllowedMentions(users=[member]),
            )
        except discord.HTTPException:
            pass
        embed = discord.Embed(
            title="AutoMod : message supprimé",
            description=f"{member.mention} dans {message.channel.mention}",  # type: ignore[union-attr]
            color=COLOR_WARN,
            timestamp=utcnow(),
        )
        embed.add_field(name="Raison", value=reason, inline=False)
        embed.add_field(name="Contenu", value=discord.utils.escape_markdown(truncate(message.content, 900)) or "*(vide)*", inline=False)
        embed.set_footer(text=f"ID : {member.id}")
        await self.bot.send_log(embed)

    async def _spam_sanction(self, message: discord.Message, reason: str, extra: Optional[list[discord.Message]] = None) -> None:
        member = message.author
        to_delete = {m.id: m for m in (extra or []) if m.channel.id == message.channel.id}
        to_delete[message.id] = message
        for m in to_delete.values():
            self.bot.mark_silent_delete(m.id)
        try:
            if len(to_delete) > 1:
                await message.channel.delete_messages(list(to_delete.values()))  # type: ignore[union-attr]
            else:
                await message.delete()
        except discord.HTTPException:
            pass

        duration = timedelta(minutes=cfg.SPAM_TIMEOUT_MINUTES)
        timed_out = True
        try:
            await member.timeout(duration, reason=f"AutoMod : {reason}")  # type: ignore[union-attr]
        except discord.HTTPException:
            timed_out = False

        notice = f"🚫 Anti-spam : {reason}."
        if timed_out:
            notice += f" Tu es exclu temporairement {format_duration(duration)}."
        try:
            await message.channel.send(
                f"{member.mention} {notice}", delete_after=10, allowed_mentions=discord.AllowedMentions(users=[member])
            )
        except discord.HTTPException:
            pass

        embed = discord.Embed(
            title="AutoMod : spam détecté",
            description=f"{member.mention} dans {message.channel.mention}",  # type: ignore[union-attr]
            color=COLOR_DANGER,
            timestamp=utcnow(),
        )
        embed.add_field(name="Raison", value=reason, inline=False)
        embed.add_field(name="Sanction", value=f"Exclusion {format_duration(duration)}" if timed_out else "Messages supprimés (exclusion impossible : permissions/rôle)", inline=False)
        embed.set_footer(text=f"ID : {member.id}")
        await self.bot.send_log(embed)

    # ================================================================== aides pour les commandes

    def _blocked(self, interaction: discord.Interaction, target: discord.Member) -> Optional[str]:
        """Vérifie qu'on peut sanctionner ce membre. Renvoie un message d'erreur, ou None si c'est possible."""
        guild = interaction.guild
        assert guild is not None
        if target.id == interaction.user.id:
            return "Tu ne peux pas faire ça sur toi-même."
        if target.id == self.bot.user.id:  # type: ignore[union-attr]
            return "Je ne vais pas me sanctionner moi-même 😄"
        if target.id == guild.owner_id:
            return "Impossible : c'est le propriétaire du serveur."
        if interaction.user.id != guild.owner_id and target.top_role >= interaction.user.top_role:  # type: ignore[union-attr]
            return "Ce membre a un rôle égal ou supérieur au tien."
        if target.top_role >= guild.me.top_role:
            return "Son rôle est au-dessus du mien : place mon rôle plus haut dans la liste des rôles."
        return None

    async def _notify(self, user: discord.abc.User, title: str, reason: str, guild: discord.Guild, extra: str = "") -> bool:
        """Prévient la personne en message privé (silencieusement ignoré si ses MP sont fermés)."""
        embed = discord.Embed(title=title, description=f"Serveur : **{guild.name}**", color=COLOR_WARN)
        embed.add_field(name="Raison", value=truncate(reason, 1000), inline=False)
        if extra:
            embed.add_field(name="Détail", value=extra, inline=False)
        try:
            await user.send(embed=embed)
            return True
        except discord.HTTPException:
            return False

    async def _log_action(
        self,
        action: str,
        target: discord.abc.User,
        moderator: discord.abc.User,
        reason: str,
        color: int = COLOR_WARN,
        extra: Optional[dict[str, str]] = None,
    ) -> None:
        embed = discord.Embed(title=action, color=color, timestamp=utcnow())
        embed.add_field(name="Membre", value=f"{target.mention} (`{target}`)")
        embed.add_field(name="Modérateur", value=moderator.mention)
        embed.add_field(name="Raison", value=truncate(reason, 1000), inline=False)
        for name, value in (extra or {}).items():
            embed.add_field(name=name, value=value)
        embed.set_footer(text=f"ID : {target.id}")
        await self.bot.send_log(embed)

    @staticmethod
    def _result(title: str, member: discord.abc.User, reason: str, color: int, note: str = "") -> discord.Embed:
        embed = discord.Embed(title=title, description=f"{member.mention}\n**Raison :** {truncate(reason, 500)}", color=color)
        if note:
            embed.set_footer(text=note)
        return embed

    # ================================================================== commandes

    @app_commands.command(name="warn", description="Donne un avertissement à un membre")
    @app_commands.describe(membre="Le membre à avertir", raison="Pourquoi ?")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    async def warn(self, interaction: discord.Interaction, membre: discord.Member, raison: app_commands.Range[str, 1, 400] = NO_REASON) -> None:
        if (problem := self._blocked(interaction, membre)):
            await interaction.response.send_message(f"❌ {problem}", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await self.bot.db.add_warning(membre.id, interaction.user.id, raison)
        total = await self.bot.db.count_warnings(membre.id)
        dm_ok = await self._notify(membre, "⚠️ Avertissement", raison, interaction.guild, f"C'est ton avertissement n°{total}.")  # type: ignore[arg-type]

        note = f"Avertissement n°{total}" + ("" if dm_ok else " • MP fermés : le membre n'a pas été prévenu")
        extra: dict[str, str] = {"Total": str(total)}
        threshold = cfg.WARN_AUTO_TIMEOUT_THRESHOLD
        if threshold and total % threshold == 0:
            duration = timedelta(minutes=cfg.WARN_AUTO_TIMEOUT_MINUTES)
            try:
                await membre.timeout(duration, reason=f"{total} avertissements")
                note += f" • exclusion automatique {format_duration(duration)}"
                extra["Sanction auto"] = f"Exclusion {format_duration(duration)}"
            except discord.HTTPException:
                note += " • exclusion automatique impossible (permissions)"

        await self._log_action("Avertissement", membre, interaction.user, raison, COLOR_WARN, extra)
        await interaction.followup.send(embed=self._result("⚠️ Avertissement enregistré", membre, raison, COLOR_WARN, note), ephemeral=True)

    @app_commands.command(name="warns", description="Affiche les avertissements d'un membre")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    async def warns(self, interaction: discord.Interaction, membre: discord.Member) -> None:
        rows = await self.bot.db.list_warnings(membre.id)
        if not rows:
            await interaction.response.send_message(f"{membre.mention} n'a aucun avertissement. ✅", ephemeral=True)
            return
        lines = []
        for row in rows[:10]:
            when = discord_ts(parse_iso(row["created_at"]), "d")
            lines.append(f"`#{row['id']}` {when} par <@{row['moderator_id']}> : {truncate(row['reason'], 150)}")
        embed = discord.Embed(title=f"Avertissements de {membre.display_name}", description="\n".join(lines), color=COLOR_WARN)
        embed.set_footer(text=f"{len(rows)} au total" + (" (10 derniers affichés)" if len(rows) > 10 else "") + " • /unwarn pour en retirer un")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="unwarn", description="Retire un avertissement (par son numéro)")
    @app_commands.describe(numero="Numéro affiché par /warns (ex : 12)")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    async def unwarn(self, interaction: discord.Interaction, numero: app_commands.Range[int, 1, 10_000_000]) -> None:
        row = await self.bot.db.delete_warning(numero)
        if row is None:
            await interaction.response.send_message("❌ Aucun avertissement avec ce numéro.", ephemeral=True)
            return
        await interaction.response.send_message(f"✅ Avertissement `#{numero}` de <@{row['user_id']}> retiré.", ephemeral=True)

    @app_commands.command(name="clearwarns", description="Efface tous les avertissements d'un membre")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    async def clearwarns(self, interaction: discord.Interaction, membre: discord.Member) -> None:
        count = await self.bot.db.clear_warnings(membre.id)
        await interaction.response.send_message(f"✅ {count} avertissement(s) effacé(s) pour {membre.mention}.", ephemeral=True)

    @app_commands.command(name="timeout", description="Exclut temporairement un membre (il ne peut plus écrire ni parler)")
    @app_commands.describe(membre="Le membre", duree="Ex : 10m, 2h, 1j, 1h30m (un nombre seul = minutes). Maximum 28 jours", raison="Pourquoi ?")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    async def timeout(self, interaction: discord.Interaction, membre: discord.Member, duree: str, raison: app_commands.Range[str, 1, 400] = NO_REASON) -> None:
        if (problem := self._blocked(interaction, membre)):
            await interaction.response.send_message(f"❌ {problem}", ephemeral=True)
            return
        delta = parse_duration(duree)
        if delta is None:
            await interaction.response.send_message("❌ Durée invalide. Exemples : `10m`, `2h`, `1j`, `1h30m`.", ephemeral=True)
            return
        if delta > MAX_TIMEOUT:
            await interaction.response.send_message("❌ Discord limite l'exclusion temporaire à 28 jours.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await membre.timeout(delta, reason=f"{interaction.user} : {raison}"[:500])
        dm_ok = await self._notify(membre, "🔇 Exclusion temporaire", raison, interaction.guild, f"Durée : {format_duration(delta)}")  # type: ignore[arg-type]
        await self._log_action("Exclusion temporaire", membre, interaction.user, raison, COLOR_WARN, {"Durée": format_duration(delta)})
        note = "" if dm_ok else "MP fermés : le membre n'a pas été prévenu"
        await interaction.followup.send(embed=self._result(f"🔇 Exclu {format_duration(delta)}", membre, raison, COLOR_WARN, note), ephemeral=True)

    @app_commands.command(name="untimeout", description="Met fin à l'exclusion temporaire d'un membre")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    async def untimeout(self, interaction: discord.Interaction, membre: discord.Member) -> None:
        if not membre.is_timed_out():
            await interaction.response.send_message(f"{membre.mention} n'est pas exclu.", ephemeral=True)
            return
        await membre.timeout(None, reason=f"Levée par {interaction.user}")
        await self._log_action("Fin d'exclusion", membre, interaction.user, "Levée manuellement", COLOR_OK)
        await interaction.response.send_message(f"✅ Exclusion de {membre.mention} levée.", ephemeral=True)

    @app_commands.command(name="kick", description="Expulse un membre (il pourra revenir avec une invitation)")
    @app_commands.guild_only()
    @app_commands.default_permissions(kick_members=True)
    @app_commands.checks.has_permissions(kick_members=True)
    async def kick(self, interaction: discord.Interaction, membre: discord.Member, raison: app_commands.Range[str, 1, 400] = NO_REASON) -> None:
        if (problem := self._blocked(interaction, membre)):
            await interaction.response.send_message(f"❌ {problem}", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        dm_ok = await self._notify(membre, "👢 Tu as été expulsé", raison, interaction.guild)  # type: ignore[arg-type]
        await membre.kick(reason=f"{interaction.user} : {raison}"[:500])
        await self._log_action("Expulsion", membre, interaction.user, raison, COLOR_DANGER)
        note = "" if dm_ok else "MP fermés : le membre n'a pas été prévenu"
        await interaction.followup.send(embed=self._result("👢 Membre expulsé", membre, raison, COLOR_DANGER, note), ephemeral=True)

    @app_commands.command(name="ban", description="Bannit un membre (ou quelqu'un qui a déjà quitté le serveur)")
    @app_commands.describe(utilisateur="Le membre à bannir", raison="Pourquoi ?", supprimer_messages="Supprimer ses messages des X derniers jours (0 à 7)")
    @app_commands.guild_only()
    @app_commands.default_permissions(ban_members=True)
    @app_commands.checks.has_permissions(ban_members=True)
    async def ban(
        self,
        interaction: discord.Interaction,
        utilisateur: discord.User,
        raison: app_commands.Range[str, 1, 400] = NO_REASON,
        supprimer_messages: app_commands.Range[int, 0, 7] = 0,
    ) -> None:
        guild = interaction.guild
        assert guild is not None
        member = guild.get_member(utilisateur.id)
        if member is not None and (problem := self._blocked(interaction, member)):
            await interaction.response.send_message(f"❌ {problem}", ephemeral=True)
            return
        if member is None and utilisateur.id in (interaction.user.id, self.bot.user.id):  # type: ignore[union-attr]
            await interaction.response.send_message("❌ Impossible.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        dm_ok = await self._notify(utilisateur, "🔨 Tu as été banni", raison, guild) if member else False
        await guild.ban(utilisateur, reason=f"{interaction.user} : {raison}"[:500], delete_message_seconds=supprimer_messages * 86400)
        await self._log_action("Bannissement", utilisateur, interaction.user, raison, COLOR_DANGER)
        note = "" if dm_ok or member is None else "MP fermés : le membre n'a pas été prévenu"
        await interaction.followup.send(embed=self._result("🔨 Membre banni", utilisateur, raison, COLOR_DANGER, note), ephemeral=True)

    @app_commands.command(name="unban", description="Débannit quelqu'un (à partir de son identifiant)")
    @app_commands.describe(identifiant="L'identifiant (chiffres) de la personne bannie", raison="Pourquoi ?")
    @app_commands.guild_only()
    @app_commands.default_permissions(ban_members=True)
    @app_commands.checks.has_permissions(ban_members=True)
    async def unban(self, interaction: discord.Interaction, identifiant: str, raison: app_commands.Range[str, 1, 400] = NO_REASON) -> None:
        identifiant = identifiant.strip()
        if not identifiant.isdigit():
            await interaction.response.send_message("❌ L'identifiant doit être composé de chiffres uniquement.", ephemeral=True)
            return
        guild = interaction.guild
        assert guild is not None
        try:
            user = await self.bot.fetch_user(int(identifiant))
            await guild.unban(user, reason=f"{interaction.user} : {raison}"[:500])
        except discord.NotFound:
            await interaction.response.send_message("❌ Cette personne n'est pas dans la liste des bannis.", ephemeral=True)
            return
        await self._log_action("Débannissement", user, interaction.user, raison, COLOR_OK)
        await interaction.response.send_message(f"✅ {user.mention} (`{user}`) est débanni.", ephemeral=True)

    @app_commands.command(name="purge", description="Supprime des messages récents du salon")
    @app_commands.describe(nombre="Nombre de messages récents à examiner (1 à 100)", membre="Ne supprimer que les messages de ce membre")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.checks.has_permissions(manage_messages=True)
    async def purge(self, interaction: discord.Interaction, nombre: app_commands.Range[int, 1, 100], membre: Optional[discord.Member] = None) -> None:
        channel = interaction.channel
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            await interaction.response.send_message("❌ Impossible dans ce type de salon.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        def check(m: discord.Message) -> bool:
            if membre is not None and m.author.id != membre.id:
                return False
            self.bot.mark_silent_delete(m.id)
            return True

        deleted = await channel.purge(limit=nombre, check=check, reason=f"Purge par {interaction.user}")
        await self._log_action(
            "Purge de messages", interaction.user, interaction.user, f"{len(deleted)} message(s) supprimé(s) dans {channel.mention}", COLOR_WARN,
            {"Filtre": membre.mention if membre else "aucun"},
        )
        await interaction.followup.send(f"🧹 {len(deleted)} message(s) supprimé(s).", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Moderation(bot))

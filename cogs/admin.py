"""Commandes /config : réglage des salons, rôles et filtres sans toucher au code."""
from __future__ import annotations

import logging
import re
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils import COLOR_INFO, truncate

log = logging.getLogger(__name__)

CHANNEL_LABELS = {
    "annonces": "Annonces de live",
    "clips": "Clips Twitch",
    "bienvenue": "Messages de bienvenue",
    "logs": "Logs du serveur (modération)",
    "idees": "Idées (/idee)",
    "joueurs": "Recherche de joueurs",
    "niveaux": "Montées de niveau",
}

ROLE_LABELS = {
    "notif_live": "Notif live (pingé à chaque live)",
    "auto": "Rôle d'arrivée (donné aux nouveaux)",
    "habitue": "Habitué (a le droit de poster des liens)",
    "joueurs": "Ping « recherche de joueurs »",
}

# Permissions supplémentaires que le bot doit avoir dans certains salons
CHANNEL_EXTRA_PERMS = {
    "clips": {"add_reactions": "ajouter des réactions"},
    "idees": {"add_reactions": "ajouter des réactions", "create_public_threads": "créer des fils"},
}

# Permissions dont le bot a besoin au niveau du serveur
REQUIRED_GUILD_PERMS = {
    "view_channel": "Voir les salons",
    "send_messages": "Envoyer des messages",
    "embed_links": "Intégrer des liens",
    "add_reactions": "Ajouter des réactions",
    "read_message_history": "Voir les anciens messages",
    "manage_messages": "Gérer les messages",
    "manage_roles": "Gérer les rôles",
    "kick_members": "Expulser des membres",
    "ban_members": "Bannir des membres",
    "moderate_members": "Exclure temporairement des membres",
    "create_public_threads": "Créer des fils publics",
    "send_messages_in_threads": "Envoyer des messages dans les fils",
    "send_polls": "Créer des sondages",
}

DOMAIN_RE = re.compile(r"^(?:[a-z0-9-]+\.)+[a-z]{2,}$")

DEFAULT_WELCOME = (
    "Bienvenue {member} sur **{server}** ! 👋\n"
    "Installe-toi, présente-toi et prends tes rôles. Tu es le membre n°{count}."
)


def _choices(labels: dict[str, str]) -> list[app_commands.Choice[str]]:
    return [app_commands.Choice(name=label, value=key) for key, label in labels.items()]


def channel_problems(guild: discord.Guild, channel: discord.TextChannel, key: str = "") -> list[str]:
    """Liste ce qui manque au bot pour travailler correctement dans ce salon."""
    perms = channel.permissions_for(guild.me)
    needed = {"view_channel": "voir le salon", "send_messages": "envoyer des messages", "embed_links": "intégrer des liens"}
    needed.update(CHANNEL_EXTRA_PERMS.get(key, {}))
    return [label for attr, label in needed.items() if not getattr(perms, attr)]


def role_problems(guild: discord.Guild, role: discord.Role, key: str = "") -> list[str]:
    problems = []
    if key != "notif_live" and not role.is_assignable():
        problems.append("je ne peux pas attribuer ce rôle (mon rôle doit être placé AU-DESSUS dans la liste des rôles)")
    if key in ("notif_live", "joueurs") and not role.mentionable and not guild.me.guild_permissions.mention_everyone:
        problems.append("ce rôle n'est pas mentionnable : coche « Autoriser tout le monde à mentionner ce rôle » ou donne-moi la permission « Mentionner @everyone, @here et tous les rôles »")
    return problems


class Admin(commands.Cog):
    """Réglages du bot."""

    cfg_group = app_commands.Group(
        name="config",
        description="Réglages du bot",
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not interaction.user.guild_permissions.manage_guild:  # type: ignore[union-attr]
            raise app_commands.MissingPermissions(["manage_guild"])
        return True

    # ------------------------------------------------------------------ salons / rôles

    @cfg_group.command(name="salon", description="Choisit le salon utilisé pour une fonction (vide = retirer)")
    @app_commands.describe(fonction="La fonction concernée", salon="Le salon à utiliser (laisse vide pour retirer)")
    @app_commands.choices(fonction=_choices(CHANNEL_LABELS))
    async def cfg_salon(
        self,
        interaction: discord.Interaction,
        fonction: app_commands.Choice[str],
        salon: Optional[discord.TextChannel] = None,
    ) -> None:
        key = fonction.value
        if salon is None:
            await self.bot.db.delete_setting(f"channel.{key}")
            await interaction.response.send_message(f"✅ **{fonction.name}** : salon retiré.", ephemeral=True)
            return
        await self.bot.db.set_setting(f"channel.{key}", str(salon.id))
        text = f"✅ **{fonction.name}** → {salon.mention}"
        problems = channel_problems(interaction.guild, salon, key)  # type: ignore[arg-type]
        if problems:
            text += "\n⚠️ Dans ce salon, il me manque la permission de : " + ", ".join(problems) + "."
        await interaction.response.send_message(text, ephemeral=True)

    @cfg_group.command(name="role", description="Choisit le rôle utilisé pour une fonction (vide = retirer)")
    @app_commands.describe(fonction="La fonction concernée", role="Le rôle à utiliser (laisse vide pour retirer)")
    @app_commands.choices(fonction=_choices(ROLE_LABELS))
    async def cfg_role(
        self,
        interaction: discord.Interaction,
        fonction: app_commands.Choice[str],
        role: Optional[discord.Role] = None,
    ) -> None:
        key = fonction.value
        if role is None:
            await self.bot.db.delete_setting(f"role.{key}")
            await interaction.response.send_message(f"✅ **{fonction.name}** : rôle retiré.", ephemeral=True)
            return
        await self.bot.db.set_setting(f"role.{key}", str(role.id))
        text = f"✅ **{fonction.name}** → {role.mention}"
        problems = role_problems(interaction.guild, role, key)  # type: ignore[arg-type]
        if problems:
            text += "\n⚠️ " + "\n⚠️ ".join(problems)
        await interaction.response.send_message(text, ephemeral=True)

    # ------------------------------------------------------------------ niveaux

    @cfg_group.command(name="niveau-role", description="Rôle offert quand un membre atteint un niveau (vide = retirer)")
    @app_commands.describe(niveau="Niveau à atteindre", role="Rôle offert (laisse vide pour retirer la récompense)")
    async def cfg_niveau_role(
        self,
        interaction: discord.Interaction,
        niveau: app_commands.Range[int, 1, 200],
        role: Optional[discord.Role] = None,
    ) -> None:
        if role is None:
            removed = await self.bot.db.remove_reward(niveau)
            msg = f"✅ Récompense du niveau {niveau} retirée." if removed else f"Aucune récompense n'était définie au niveau {niveau}."
            await interaction.response.send_message(msg, ephemeral=True)
            return
        await self.bot.db.set_reward(niveau, role.id)
        text = f"✅ Niveau **{niveau}** → {role.mention}"
        problems = role_problems(interaction.guild, role)  # type: ignore[arg-type]
        if problems:
            text += "\n⚠️ " + "\n⚠️ ".join(problems)
        await interaction.response.send_message(text, ephemeral=True)

    # ------------------------------------------------------------------ accueil / filtre de liens

    @cfg_group.command(name="message-bienvenue", description="Change le message d'accueil (vide = message par défaut)")
    @app_commands.describe(texte="Variables : {member} (mention), {server} (nom du serveur), {count} (nombre de membres)")
    async def cfg_welcome(
        self,
        interaction: discord.Interaction,
        texte: Optional[app_commands.Range[str, 1, 1500]] = None,
    ) -> None:
        if texte is None:
            await self.bot.db.delete_setting("welcome.message")
            await interaction.response.send_message("✅ Message de bienvenue remis par défaut.", ephemeral=True)
            return
        await self.bot.db.set_setting("welcome.message", texte)
        await interaction.response.send_message(f"✅ Nouveau message de bienvenue enregistré :\n>>> {texte}", ephemeral=True)

    @cfg_group.command(name="filtre-liens", description="Active ou désactive le filtre de liens")
    @app_commands.describe(actif="Activer (oui) ou désactiver (non) le filtre")
    async def cfg_links(self, interaction: discord.Interaction, actif: bool) -> None:
        await self.bot.db.set_setting("filter.links", "1" if actif else "0")
        state = "activé" if actif else "désactivé"
        await interaction.response.send_message(f"✅ Filtre de liens **{state}**.", ephemeral=True)

    @cfg_group.command(name="domaine", description="Ajoute ou retire un site de la liste des liens autorisés")
    @app_commands.describe(action="Ajouter ou retirer", domaine="Ex : medal.tv (les sous-domaines sont inclus)")
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Ajouter", value="ajouter"),
            app_commands.Choice(name="Retirer", value="retirer"),
        ]
    )
    async def cfg_domain(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        domaine: app_commands.Range[str, 3, 100],
    ) -> None:
        domain = re.sub(r"^https?://", "", domaine.strip().lower()).split("/")[0]
        if domain.startswith("www."):
            domain = domain[4:]
        if not DOMAIN_RE.match(domain):
            await interaction.response.send_message("❌ Domaine invalide. Exemple : `medal.tv`", ephemeral=True)
            return
        if action.value == "ajouter":
            added = await self.bot.db.add_domain(domain)
            msg = f"✅ `{domain}` ajouté aux liens autorisés." if added else f"`{domain}` était déjà autorisé."
        else:
            removed = await self.bot.db.remove_domain(domain)
            msg = f"✅ `{domain}` retiré des liens autorisés." if removed else f"`{domain}` n'était pas dans la liste."
        await interaction.response.send_message(msg, ephemeral=True)

    # ------------------------------------------------------------------ affichage / diagnostic

    @cfg_group.command(name="afficher", description="Affiche la configuration actuelle")
    async def cfg_show(self, interaction: discord.Interaction) -> None:
        db = self.bot.db
        guild = interaction.guild
        assert guild is not None

        lines = []
        for key, label in CHANNEL_LABELS.items():
            value = db.setting(f"channel.{key}")
            lines.append(f"**{label}** : " + (f"<#{value}>" if value else "*non défini*"))
        channels = "\n".join(lines)

        lines = []
        for key, label in ROLE_LABELS.items():
            value = db.setting(f"role.{key}")
            lines.append(f"**{label}** : " + (f"<@&{value}>" if value else "*non défini*"))
        roles = "\n".join(lines)

        rewards = await db.list_rewards()
        reward_text = "\n".join(f"Niveau **{r['level']}** → <@&{r['role_id']}>" for r in rewards) or "*aucune*"

        self_roles = await db.list_self_roles()
        self_roles_text = " ".join(f"<@&{r['role_id']}>" for r in self_roles) or "*aucun (voir /roles ajouter)*"

        links_on = db.setting("filter.links", "1") == "1"
        domains = ", ".join(sorted(db.domains)) or "*aucun*"
        welcome = db.setting("welcome.message") or DEFAULT_WELCOME

        embed = discord.Embed(title="⚙️ Configuration du bot", color=COLOR_INFO)
        embed.add_field(name="Salons", value=channels, inline=False)
        embed.add_field(name="Rôles", value=roles, inline=False)
        embed.add_field(name="Récompenses de niveau", value=truncate(reward_text, 1024), inline=False)
        embed.add_field(name="Rôles à boutons", value=truncate(self_roles_text, 1024), inline=False)
        embed.add_field(
            name=f"Filtre de liens : {'activé' if links_on else 'désactivé'}",
            value=truncate("Autorisés : " + domains, 1024),
            inline=False,
        )
        embed.add_field(name="Message de bienvenue", value=truncate(welcome, 1024), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @cfg_group.command(name="verifier", description="Vérifie que tout est bien réglé (permissions, rôles, salons)")
    async def cfg_check(self, interaction: discord.Interaction) -> None:
        db = self.bot.db
        guild = interaction.guild
        assert guild is not None
        problems: list[str] = []

        missing = [label for attr, label in REQUIRED_GUILD_PERMS.items() if not getattr(guild.me.guild_permissions, attr)]
        if missing:
            problems.append("Permissions manquantes pour mon rôle : " + ", ".join(missing))

        for key, label in CHANNEL_LABELS.items():
            value = db.setting(f"channel.{key}")
            if not value:
                continue
            channel = guild.get_channel(int(value))
            if not isinstance(channel, discord.TextChannel):
                problems.append(f"{label} : le salon configuré n'existe plus (refais /config salon).")
                continue
            for issue in channel_problems(guild, channel, key):
                problems.append(f"{label} ({channel.mention}) : il me manque la permission de {issue}.")

        for key, label in ROLE_LABELS.items():
            value = db.setting(f"role.{key}")
            if not value:
                continue
            role = guild.get_role(int(value))
            if role is None:
                problems.append(f"{label} : le rôle configuré n'existe plus (refais /config role).")
                continue
            for issue in role_problems(guild, role, key):
                problems.append(f"{label} ({role.mention}) : {issue}.")

        for reward in await db.list_rewards():
            role = guild.get_role(reward["role_id"])
            if role is None:
                problems.append(f"Récompense du niveau {reward['level']} : le rôle n'existe plus.")
            elif not role.is_assignable():
                problems.append(f"Récompense du niveau {reward['level']} ({role.mention}) : je ne peux pas attribuer ce rôle (place le mien au-dessus).")

        for row in await db.list_self_roles():
            role = guild.get_role(row["role_id"])
            if role is None:
                problems.append(f"Rôle à boutons « {row['label']} » : le rôle n'existe plus (/roles retirer).")
            elif not role.is_assignable():
                problems.append(f"Rôle à boutons {role.mention} : je ne peux pas l'attribuer (place mon rôle au-dessus).")

        if not self.bot.settings.twitch_enabled:
            problems.append("Twitch est désactivé : renseigne TWITCH_CLIENT_ID et TWITCH_CLIENT_SECRET dans le fichier .env.")
        if db.setting("channel.annonces") is None:
            problems.append("Aucun salon d'annonces de live : /config salon fonction:Annonces de live.")
        if db.setting("role.notif_live") is None:
            problems.append("Aucun rôle « Notif live » : /config role fonction:Notif live.")

        if problems:
            text = "⚠️ **Points à corriger :**\n" + "\n".join(f"• {p}" for p in problems)
        else:
            text = "✅ Tout est en ordre."
        await interaction.response.send_message(truncate(text, 1900), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Admin(bot))

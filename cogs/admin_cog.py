import discord
from discord import app_commands
from discord.ext import commands

import database as db
import voice_utils
import leaderboard_utils


class AdminCog(commands.Cog):
    """Club name pool + captain whitelist + result overrides.
    Everything here is scoped to the server you run it in, and requires
    Manage Server permission."""

    def __init__(self, bot):
        self.bot = bot

    club_group = app_commands.Group(name="club", description="Manage this server's pool of club names used for team VCs")
    captain_group = app_commands.Group(name="captain", description="Manage this server's captain whitelist")

    # ------------------------------------------------------------- one-time setup
    @app_commands.command(name="ntf_setup", description="[Admin] Create (or locate) this server's permanent #in-progress and #leaderboard channels")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def ntf_setup(self, interaction: discord.Interaction):
        guild = interaction.guild
        cfg = db.get_guild_config(guild.id) or {}

        progress_channel = guild.get_channel(cfg.get("progress_channel_id")) if cfg.get("progress_channel_id") else None
        if progress_channel is None:
            progress_channel = await voice_utils.create_progress_text_channel(guild, category=None)

        leaderboard_channel = guild.get_channel(cfg.get("leaderboard_channel_id")) if cfg.get("leaderboard_channel_id") else None
        if leaderboard_channel is None:
            leaderboard_channel = await voice_utils.create_leaderboard_channel(guild)

        db.upsert_guild_config(
            guild.id, progress_channel_id=progress_channel.id, leaderboard_channel_id=leaderboard_channel.id
        )

        await progress_channel.send("🚫 No games are currently in progress.")
        await leaderboard_utils.refresh_leaderboard_channel(self.bot, guild)

        await interaction.response.send_message(
            f"✅ NTF is set up for this server — {progress_channel.mention} and {leaderboard_channel.mention} are ready. "
            f"These (and your clubs/captains/leaderboard) are separate per server, so other servers NTF is in won't see this data.",
            ephemeral=True,
        )

    # ------------------------------------------------------------------ clubs
    @club_group.command(name="add", description="Add a club name to this server's random pool")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def club_add(self, interaction: discord.Interaction, name: str):
        db.add_club(interaction.guild_id, name.strip())
        await interaction.response.send_message(f"✅ Added club **{name}** to this server's pool.", ephemeral=True)

    @club_group.command(name="remove", description="Remove a club name from this server's pool")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def club_remove(self, interaction: discord.Interaction, name: str):
        db.remove_club(interaction.guild_id, name.strip())
        await interaction.response.send_message(f"🗑️ Removed **{name}**.", ephemeral=True)

    @club_group.command(name="list", description="List all club names stored for this server")
    async def club_list(self, interaction: discord.Interaction):
        clubs = db.list_clubs(interaction.guild_id)
        if not clubs:
            await interaction.response.send_message("No clubs stored yet for this server. Use `/club add`.", ephemeral=True)
            return
        await interaction.response.send_message("📋 **Club pool:**\n" + "\n".join(f"• {c}" for c in clubs), ephemeral=True)

    # ---------------------------------------------------------------- captains
    @captain_group.command(name="add", description="Whitelist a player so they auto-become a captain in this server")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def captain_add(self, interaction: discord.Interaction, member: discord.Member):
        db.ensure_player(interaction.guild_id, member.id, member.display_name)
        db.set_captain(interaction.guild_id, member.id, True)
        await interaction.response.send_message(f"🎖️ {member.mention} is now a whitelisted captain in this server.", ephemeral=True)

    @captain_group.command(name="remove", description="Remove a player from this server's captain whitelist")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def captain_remove(self, interaction: discord.Interaction, member: discord.Member):
        db.set_captain(interaction.guild_id, member.id, False)
        await interaction.response.send_message(f"Removed {member.mention} from the captain whitelist.", ephemeral=True)

    @captain_group.command(name="list", description="List all whitelisted captains in this server")
    async def captain_list(self, interaction: discord.Interaction):
        caps = db.get_captains(interaction.guild_id)
        if not caps:
            await interaction.response.send_message("No captains whitelisted yet in this server.", ephemeral=True)
            return
        lines = [f"• <@{c['discord_id']}> (MMR {c['mmr']})" for c in caps]
        await interaction.response.send_message("🎖️ **Captain whitelist:**\n" + "\n".join(lines), ephemeral=True)

    # ---------------------------------------------------------------- overrides
    @app_commands.command(name="admin_fix_mmr", description="Manually set a player's MMR in this server (corrections only)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def admin_fix_mmr(self, interaction: discord.Interaction, member: discord.Member, mmr: int):
        db.ensure_player(interaction.guild_id, member.id, member.display_name)
        db.set_mmr(interaction.guild_id, member.id, mmr)
        await interaction.response.send_message(f"Set {member.mention}'s MMR to {mmr}.", ephemeral=True)
        await leaderboard_utils.refresh_leaderboard_channel(self.bot, interaction.guild)


async def setup(bot):
    await bot.add_cog(AdminCog(bot))

import discord
from discord import app_commands
from discord.ext import commands

import database as db
import mmr
import leaderboard_utils


class MMRCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="rank", description="Show your (or someone else's) rank and MMR in this server")
    async def rank(self, interaction: discord.Interaction, member: discord.Member = None):
        member = member or interaction.user
        db.ensure_player(interaction.guild_id, member.id, member.display_name)
        p = db.get_player(interaction.guild_id, member.id)
        letter = mmr.rank_for_mmr(p["mmr"])
        embed = discord.Embed(title=f"{member.display_name}'s Pro Clubs Rank", color=discord.Color.blurple())
        embed.add_field(name="Rank", value=f"**{letter}**", inline=True)
        embed.add_field(name="MMR", value=str(p["mmr"]), inline=True)
        embed.add_field(name="Record", value=f"{p['wins']}W - {p['losses']}L", inline=True)
        if p["is_captain"]:
            embed.set_footer(text="🎖️ Whitelisted captain")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="leaderboard", description="Show this server's top players by MMR, with your own standing highlighted")
    async def leaderboard(self, interaction: discord.Interaction):
        rows = db.leaderboard(interaction.guild_id, 20)
        embed = leaderboard_utils.build_leaderboard_embed(rows, highlight_id=interaction.user.id)

        db.ensure_player(interaction.guild_id, interaction.user.id, interaction.user.display_name)
        p = db.get_player(interaction.guild_id, interaction.user.id)
        in_top = any(r["discord_id"] == interaction.user.id for r in rows)
        if p and not in_top:
            pos = db.get_player_rank_position(interaction.guild_id, interaction.user.id)
            letter = mmr.rank_for_mmr(p["mmr"])
            embed.add_field(
                name="Your Standing",
                value=f"`#{pos}` **{letter}** — {p['mmr']} MMR ({p['wins']}W-{p['losses']}L)",
                inline=False,
            )
        await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(MMRCog(bot))

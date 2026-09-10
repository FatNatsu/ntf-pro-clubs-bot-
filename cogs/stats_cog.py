import discord
from discord import app_commands
from discord.ext import commands

import database as db
import mmr


def _form_string(form_list):
    if not form_list:
        return "*no matches played yet*"
    return " ".join("🟢" if r == "W" else "🔴" for r in form_list)


class StatsCog(commands.Cog):
    """/player_stats and /club_stats — scoped to the server you run them in.
    Point these at a dedicated #stats channel if you want, they work anywhere."""

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="player_stats", description="View a player's Pro Clubs profile in this server")
    async def player_stats(self, interaction: discord.Interaction, member: discord.Member = None):
        member = member or interaction.user
        guild_id = interaction.guild_id
        db.ensure_player(guild_id, member.id, member.display_name)
        p = db.get_player(guild_id, member.id)
        record = db.get_player_record(guild_id, member.id)
        form = db.get_player_recent_form(guild_id, member.id, limit=10)
        best_club = db.get_player_best_club(guild_id, member.id)
        best_mate = db.get_player_most_played_with(guild_id, member.id)

        embed = discord.Embed(
            title=f"👤 {member.display_name}'s Profile",
            color=discord.Color.blurple(),
        )
        if member.display_avatar:
            embed.set_thumbnail(url=member.display_avatar.url)

        embed.add_field(name="Rank", value=f"**{mmr.rank_for_mmr(p['mmr'])}**  ({p['mmr']} MMR)", inline=True)
        embed.add_field(name="Overall Record", value=f"{record['wins']}W - {record['losses']}L", inline=True)
        embed.add_field(name="\u200b", value="\u200b", inline=True)

        embed.add_field(name="Recent Form (last 10)", value=_form_string(form), inline=False)

        if best_club:
            embed.add_field(name="Best Club", value=f"**{best_club['club_name']}** ({best_club['wins']} wins)", inline=True)
        else:
            embed.add_field(name="Best Club", value="*no wins recorded yet*", inline=True)

        if best_mate:
            embed.add_field(name="Most Played With", value=f"<@{best_mate['teammate_id']}> ({best_mate['games']} games)", inline=True)
        else:
            embed.add_field(name="Most Played With", value="*not enough data yet*", inline=True)

        if p["is_captain"]:
            embed.set_footer(text="🎖️ Whitelisted captain")

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="club_stats", description="View a club's record and best run in this server")
    async def club_stats(self, interaction: discord.Interaction, club_name: str):
        guild_id = interaction.guild_id
        record = db.get_club_record(guild_id, club_name)
        if record["wins"] == 0 and record["losses"] == 0:
            await interaction.response.send_message(
                f"No match history found for **{club_name}** in this server yet.", ephemeral=True
            )
            return

        form = db.get_club_recent_form(guild_id, club_name, limit=10)
        best_run = db.get_club_best_run(guild_id, club_name)
        top_player = db.get_club_top_player(guild_id, club_name)

        embed = discord.Embed(title=f"🛡️ {club_name}", color=discord.Color.dark_gold())
        embed.add_field(name="Record", value=f"{record['wins']}W - {record['losses']}L", inline=True)
        embed.add_field(name="Best Run", value=f"{best_run} wins in a row", inline=True)
        embed.add_field(name="\u200b", value="\u200b", inline=True)
        embed.add_field(name="Recent Form (last 10)", value=_form_string(form), inline=False)
        if top_player:
            embed.add_field(
                name="Top Player",
                value=f"<@{top_player['player_id']}> ({top_player['wins']} wins)",
                inline=False,
            )

        await interaction.response.send_message(embed=embed)

    @club_stats.autocomplete("club_name")
    async def club_name_autocomplete(self, interaction: discord.Interaction, current: str):
        guild_id = interaction.guild_id
        names = set(db.list_clubs(guild_id)) | set(db.get_all_known_club_names(guild_id))
        matches = [n for n in sorted(names) if current.lower() in n.lower()]
        return [app_commands.Choice(name=n, value=n) for n in matches[:25]]


async def setup(bot):
    await bot.add_cog(StatsCog(bot))

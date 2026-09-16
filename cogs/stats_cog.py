from typing import Literal, Optional

import discord
from discord import app_commands
from discord.ext import commands

import database as db
import mmr


def _form_string(form_list):
    if not form_list:
        return "*no matches played yet*"
    return " ".join("🟢" if r == "W" else "🔴" for r in form_list)


def _streak_string(streak_type, count):
    if streak_type is None or count == 0:
        return "*no matches played yet*"
    if streak_type == "W":
        return f"🔥 {count}-game win streak"
    return f"❄️ {count}-game loss streak"


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
        rivals_stats = db.get_player_mode_stats(guild_id, member.id, "rivals")
        league_stats = db.get_player_mode_stats(guild_id, member.id, "league")
        form = db.get_player_recent_form(guild_id, member.id, limit=10)
        streak_type, streak_count = db.get_player_streak(guild_id, member.id)
        best_club = db.get_player_best_club(guild_id, member.id)
        best_mate = db.get_player_most_played_with(guild_id, member.id)

        embed = discord.Embed(
            title=f"👤 {member.display_name}'s Profile",
            color=discord.Color.blurple(),
        )
        if member.display_avatar:
            embed.set_thumbnail(url=member.display_avatar.url)

        embed.add_field(
            name="⚔️ Rivals",
            value=f"**{mmr.rank_for_mmr(rivals_stats['mmr'])}** — {rivals_stats['mmr']} MMR\n{rivals_stats['wins']}W - {rivals_stats['losses']}L",
            inline=True,
        )
        embed.add_field(
            name="🏆 League",
            value=f"**{mmr.rank_for_mmr(league_stats['mmr'])}** — {league_stats['mmr']} MMR\n{league_stats['wins']}W - {league_stats['losses']}L",
            inline=True,
        )
        embed.add_field(name="Current Streak", value=_streak_string(streak_type, streak_count), inline=True)

        embed.add_field(name="Recent Form (last 10, both modes)", value=_form_string(form), inline=False)

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

    @app_commands.command(name="head_to_head", description="Compare two players' record specifically against each other")
    async def head_to_head(self, interaction: discord.Interaction, player_a: discord.Member, player_b: discord.Member = None):
        player_b = player_b or interaction.user
        if player_a.id == player_b.id:
            await interaction.response.send_message("Pick two different players.", ephemeral=True)
            return

        guild_id = interaction.guild_id
        db.ensure_player(guild_id, player_a.id, player_a.display_name)
        db.ensure_player(guild_id, player_b.id, player_b.display_name)
        a_wins, b_wins, total = db.get_head_to_head(guild_id, player_a.id, player_b.id)

        if total == 0:
            await interaction.response.send_message(
                f"{player_a.mention} and {player_b.mention} haven't been on opposing teams in a recorded match yet.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(title=f"⚔️ {player_a.display_name} vs {player_b.display_name}", color=discord.Color.orange())
        embed.add_field(name=player_a.display_name, value=f"**{a_wins}** win(s)", inline=True)
        embed.add_field(name=player_b.display_name, value=f"**{b_wins}** win(s)", inline=True)
        embed.set_footer(text=f"{total} meeting(s) as opponents (games where they were teammates don't count here)")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="session_history", description="Show recent completed sessions and their winners, optionally filtered to one mode")
    async def session_history(self, interaction: discord.Interaction, mode: Optional[Literal["rivals", "league"]] = None):
        history = db.get_session_history(interaction.guild_id, limit=10, mode=mode)
        if not history:
            scope = f"{mode} " if mode else ""
            await interaction.response.send_message(f"No completed {scope}sessions recorded yet in this server.", ephemeral=True)
            return

        lines = []
        for row in history:
            mode_label = "🏆 League" if row["mode"] == "league" else "⚔️ Rivals"
            date_str = (row["created_at"] or "")[:10]  # just the date portion
            lines.append(f"{mode_label} — **{row['winning_club']}** (captain <@{row['winning_captain']}>) — {date_str}")

        title = f"📜 {mode.title()} Session History" if mode else "📜 Session History"
        embed = discord.Embed(title=title, description="\n".join(lines), color=discord.Color.blurple())
        embed.set_footer(text="Most recent 10 completed sessions. Test sessions never appear here.")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="club_stats", description="View a club's record, streak, and best run in this server, for both modes")
    async def club_stats(self, interaction: discord.Interaction, club_name: str):
        guild_id = interaction.guild_id
        rivals_record = db.get_club_record(guild_id, club_name, "rivals")
        league_record = db.get_club_record(guild_id, club_name, "league")
        if sum(rivals_record.values()) == 0 and sum(league_record.values()) == 0:
            await interaction.response.send_message(
                f"No match history found for **{club_name}** in this server yet.", ephemeral=True
            )
            return

        rivals_form = db.get_club_recent_form(guild_id, club_name, "rivals", limit=10)
        league_form = db.get_club_recent_form(guild_id, club_name, "league", limit=10)
        rivals_streak = db.get_club_streak(guild_id, club_name, "rivals")
        league_streak = db.get_club_streak(guild_id, club_name, "league")
        rivals_best_run = db.get_club_best_run(guild_id, club_name, "rivals")
        league_best_run = db.get_club_best_run(guild_id, club_name, "league")
        top_player = db.get_club_top_player(guild_id, club_name)

        embed = discord.Embed(title=f"🛡️ {club_name}", color=discord.Color.dark_gold())
        embed.add_field(
            name="⚔️ Rivals",
            value=f"{rivals_record['wins']}W - {rivals_record['losses']}L\n{_streak_string(*rivals_streak)}\nBest run: {rivals_best_run} wins in a row",
            inline=True,
        )
        embed.add_field(
            name="🏆 League",
            value=f"{league_record['wins']}W - {league_record['losses']}L\n{_streak_string(*league_streak)}\nBest run: {league_best_run} wins in a row",
            inline=True,
        )
        embed.add_field(name="\u200b", value="\u200b", inline=True)
        embed.add_field(name="Rivals Recent Form", value=_form_string(rivals_form), inline=True)
        embed.add_field(name="League Recent Form", value=_form_string(league_form), inline=True)
        embed.add_field(name="\u200b", value="\u200b", inline=True)
        if top_player:
            embed.add_field(
                name="Top Player (both modes combined)",
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

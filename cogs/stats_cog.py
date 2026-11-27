from typing import Literal, Optional

import discord
from discord import app_commands
from discord.ext import commands

import database as db
import mmr
import season
import ui


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


def build_profile_embed(guild_id: int, member, season_no: int = None):
    """A player's profile for one season - the current one unless season_no
    names an ended season (Founder decision, 27 Nov 2026: every season keeps
    its own history, picked from the Season menu)."""
    current = db.current_season(guild_id)
    past = season_no is not None and season_no != current
    p = db.get_player(guild_id, member.id)
    which = season_no if past else None
    description = (
        f"**Season {season_no}** · ended — final record" if past
        else season.countdown_line(guild_id)  # which season these numbers belong to
    )
    embed = discord.Embed(title=f"👤 {member.display_name}'s Profile", description=description, color=ui.accent("neutral"))
    if member.display_avatar:
        embed.set_thumbnail(url=member.display_avatar.url)

    # Full-width blocks, one under another: reads the same on a phone
    # and a computer, with no empty spacer gaps.
    for label, key in (("⚔️ Rivals", "rivals"), ("🏆 League", "league")):
        form = db.get_player_recent_form(guild_id, member.id, key, limit=10, season=which)
        streak = db.get_player_streak(guild_id, member.id, key, season=which)
        if past:
            stats = db.season_record(guild_id, season_no, member.id, key)
            crowned = db.season_crown(guild_id, season_no, key) == member.id
            crown = f"\n👑 Golden Crown — most wins in Season {season_no}" if crowned else ""
            if not stats:
                embed.add_field(name=label, value="*Didn't play this mode that season*" + crown, inline=False)
                continue
            top = f"**{mmr.rank_for_mmr(stats['mmr'])}** · {stats['mmr']} MMR · {stats['wins']}W - {stats['losses']}L (final)"
        else:
            stats = db.get_player_mode_stats(guild_id, member.id, key)
            crown = "\n👑 Golden Crown — most wins last season" if season.crown_holder(guild_id, key) == member.id else ""
            top = (
                f"**{mmr.rank_for_mmr(stats['mmr'])}** · {stats['mmr']} MMR · {stats['wins']}W - {stats['losses']}L\n"
                f"{mmr.rank_progress_bar(stats['mmr'])}"
            )
        embed.add_field(
            name=label,
            value=top + "\n" + (f"Streak: {_streak_string(*streak)}\nForm: {_form_string(form)}" if form else "*No matches played yet*") + crown,
            inline=False,
        )

    best_club = db.get_player_best_club(guild_id, member.id, season=which)
    best_mate = db.get_player_most_played_with(guild_id, member.id, season=which)
    best_teammate = db.get_player_best_teammate(guild_id, member.id, season=which)
    rival = db.get_player_rival(guild_id, member.id, season=which)
    worst_mate = db.get_player_most_losses_with(guild_id, member.id, season=which)
    highlights = [
        f"🛡️ **Best club:** {best_club['club_name']} ({best_club['wins']} wins)" if best_club
        else "🛡️ **Best club:** *no wins yet*",
        f"🤝 **Best teammate:** <@{best_teammate['teammate_id']}> ({best_teammate['wins']} wins in {best_teammate['games']} games together)"
        if best_teammate else "🤝 **Best teammate:** *no wins together yet*",
        f"👥 **Most played with:** <@{best_mate['teammate_id']}> ({best_mate['games']} games)" if best_mate
        else "👥 **Most played with:** *not enough games yet*",
        f"💀 **Most losses with:** <@{worst_mate['teammate_id']}> ({worst_mate['losses']} losses in {worst_mate['games']} games together)"
        if worst_mate else "💀 **Most losses with:** *no losses together yet*",
        f"😈 **Rival:** <@{rival['opponent_id']}> (lost to them {rival['losses']} times in {rival['games']} games)" if rival
        else "😈 **Rival:** *no losses yet*",
    ]
    embed.add_field(name=f"Season {season_no}" if past else "This season", value="\n".join(highlights), inline=False)
    if p and p["is_captain"]:
        embed.set_footer(text="🎖️ Whitelisted captain")
    return embed


class ProfileSeasonView(discord.ui.View):
    """The Season menu under a profile: the current season plus every ended
    season this player played in (newest first, up to Discord's 25)."""

    def __init__(self, guild_id: int, member, past: list, selected: int = None):
        super().__init__(timeout=600)
        self.guild_id = guild_id
        self.member = member
        self.past = past
        current = db.current_season(guild_id)
        selected = selected or current
        options = [discord.SelectOption(label=f"Season {current} (now)", value=str(current), default=selected == current)]
        options += [
            discord.SelectOption(label=f"Season {n}", value=str(n), default=selected == n)
            for n in past if n != current
        ][:24]
        select = discord.ui.Select(placeholder="Pick a season", options=options)
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        chosen = int(interaction.data["values"][0])
        await interaction.response.edit_message(
            embed=build_profile_embed(self.guild_id, self.member, chosen),
            view=ProfileSeasonView(self.guild_id, self.member, self.past, chosen),
        )


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
        embed = build_profile_embed(guild_id, member)
        past = db.past_seasons(guild_id, member.id)
        if past:
            await interaction.response.send_message(embed=embed, view=ProfileSeasonView(guild_id, member, past))
        else:
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

        embed = discord.Embed(title=f"⚔️ {player_a.display_name} vs {player_b.display_name}", color=ui.accent("neutral"))
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
            lines.append(f"**#{row.get('season_session_no') or row['id']}** — {mode_label} — **{row['winning_club']}** (captain <@{row['winning_captain']}>) — {date_str}")

        title = f"📜 {mode.title()} Session History" if mode else "📜 Session History"
        embed = discord.Embed(title=title, description="\n".join(lines), color=ui.accent("neutral"))
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

        embed = discord.Embed(title=f"🛡️ {club_name}", color=ui.accent("neutral"))
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

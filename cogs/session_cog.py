import asyncio
import random

import discord
from discord import app_commands
from discord.ext import commands

import config
import database as db
import mmr
import team_balance
import voice_utils
import leaderboard_utils


class ScoreModal(discord.ui.Modal):
    """Popup asking for the scoreline once a captain/admin picks a winner."""

    def __init__(self, cog: "SessionCog", match_id, winner_team_id, loser_team_id,
                 winner_club, loser_club, origin_view, origin_message):
        super().__init__(title=f"{winner_club} vs {loser_club}")
        self.cog = cog
        self.match_id = match_id
        self.winner_team_id = winner_team_id
        self.loser_team_id = loser_team_id
        self.winner_club = winner_club
        self.loser_club = loser_club
        self.origin_view = origin_view
        self.origin_message = origin_message

        self.winner_score = discord.ui.TextInput(
            label=f"{winner_club} score", placeholder="e.g. 4", max_length=3, required=True
        )
        self.loser_score = discord.ui.TextInput(
            label=f"{loser_club} score", placeholder="e.g. 2", max_length=3, required=True
        )
        self.add_item(self.winner_score)
        self.add_item(self.loser_score)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            w_score = int(self.winner_score.value)
            l_score = int(self.loser_score.value)
        except ValueError:
            w_score, l_score = None, None
        await self.cog.finalize_match(
            interaction, self.match_id, self.winner_team_id, self.loser_team_id,
            w_score, l_score, self.origin_view, self.origin_message,
        )


class MatchControlView(discord.ui.View):
    """Red win buttons for one match, in session-control. Flips the winner
    green + disables both once a captain/admin reports it. Each match is its
    own message so the 2 (rivals) or up-to-2-concurrent (league) fixtures
    stay visually separate and easy to tap on a console controller."""

    def __init__(self, cog: "SessionCog", session_id, match_id,
                 team_a_id, team_a_club, team_b_id, team_b_club):
        super().__init__(timeout=None)
        self.cog = cog
        self.session_id = session_id
        self.match_id = match_id
        self.team_a_id, self.team_a_club = team_a_id, team_a_club
        self.team_b_id, self.team_b_club = team_b_id, team_b_club

        self.btn_a = discord.ui.Button(label=f"{team_a_club} Win", style=discord.ButtonStyle.danger, emoji="🔴", row=0)
        self.btn_b = discord.ui.Button(label=f"{team_b_club} Win", style=discord.ButtonStyle.danger, emoji="🔴", row=0)
        self.btn_a.callback = self._make_callback(team_a_id, team_a_club, team_b_id, team_b_club)
        self.btn_b.callback = self._make_callback(team_b_id, team_b_club, team_a_id, team_a_club)
        self.add_item(self.btn_a)
        self.add_item(self.btn_b)

    def _make_callback(self, winner_id, winner_club, loser_id, loser_club):
        async def callback(interaction: discord.Interaction):
            if not self.cog.is_captain_or_admin(interaction, self.session_id, {self.team_a_id, self.team_b_id}):
                await interaction.response.send_message(
                    "Only a captain of one of these two teams (or a server admin) can report this result.",
                    ephemeral=True,
                )
                return
            modal = ScoreModal(
                self.cog, self.match_id, winner_id, loser_id, winner_club, loser_club,
                self, interaction.message,
            )
            await interaction.response.send_modal(modal)
        return callback


class SessionControlPanelView(discord.ui.View):
    """Persistent-for-the-session sub + end controls, in session-control."""

    def __init__(self, cog: "SessionCog", session_id, teams):
        super().__init__(timeout=None)
        self.cog = cog
        self.session_id = session_id

        for team_id, info in teams.items():
            btn = discord.ui.Button(
                label=f"Add sub — {info['club_name']}", style=discord.ButtonStyle.secondary, emoji="🔁"
            )
            btn.callback = self._make_sub_callback(team_id, info["club_name"])
            self.add_item(btn)

        end_btn = discord.ui.Button(label="End Session", style=discord.ButtonStyle.danger, emoji="🛑")
        end_btn.callback = self._end_callback
        self.add_item(end_btn)

    def _make_sub_callback(self, team_id, club_name):
        async def callback(interaction: discord.Interaction):
            await self.cog.request_sub(interaction, self.session_id, team_id, club_name)
        return callback

    async def _end_callback(self, interaction: discord.Interaction):
        state = self.cog.active_sessions.get(self.session_id)
        if not state:
            await interaction.response.send_message("This session has already ended.", ephemeral=True)
            return
        is_captain = interaction.user.id in {t["captain_id"] for t in state["teams"].values()}
        is_admin = interaction.user.guild_permissions.manage_guild
        if not (is_captain or is_admin):
            await interaction.response.send_message("Only a captain or a server admin can end the session.", ephemeral=True)
            return
        await interaction.response.send_message("End this session for everyone?", view=ConfirmEndView(self.cog, self.session_id), ephemeral=True)


class ConfirmEndView(discord.ui.View):
    def __init__(self, cog, session_id):
        super().__init__(timeout=30)
        self.cog = cog
        self.session_id = session_id

    @discord.ui.button(label="Yes, end it", style=discord.ButtonStyle.danger, emoji="✅")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Ending session…", ephemeral=True)
        await self.cog.end_session(self.session_id, ended_by=interaction.user)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Cancelled.", ephemeral=True)


class SpectateView(discord.ui.View):
    """Attached to the round's progress message - one button per team."""

    def __init__(self, cog: "SessionCog", session_id, team_ids_and_clubs):
        super().__init__(timeout=None)
        self.cog = cog
        self.session_id = session_id
        for team_id, club_name in team_ids_and_clubs:
            btn = discord.ui.Button(label=f"👀 Watch {club_name}", style=discord.ButtonStyle.secondary)
            btn.callback = self._make_callback(team_id, club_name)
            self.add_item(btn)

    def _make_callback(self, team_id, club_name):
        async def callback(interaction: discord.Interaction):
            state = self.cog.active_sessions.get(self.session_id)
            if not state:
                await interaction.response.send_message("This session has ended.", ephemeral=True)
                return
            member = interaction.user
            if member.voice is None or member.voice.channel is None:
                await interaction.response.send_message(
                    "Hop into any voice channel first — Discord won't let the bot pull you in from nowhere.",
                    ephemeral=True,
                )
                return
            channel = interaction.guild.get_channel(state["teams"][team_id]["voice_channel_id"])
            await voice_utils.allow_member_in_channel(channel, member, connect=True)
            moved = await voice_utils.move_member_to_channel(interaction.guild, member.id, channel)
            if moved:
                await voice_utils.set_spectator_mute(interaction.guild, member.id, True)
                await interaction.response.send_message(f"You're now spectating {club_name} (muted).", ephemeral=True)
            else:
                await interaction.response.send_message("Couldn't move you — try again.", ephemeral=True)
        return callback


class SessionCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # session_id -> runtime state (teams, matches progress, channel ids)
        self.active_sessions: dict[int, dict] = {}

    # ------------------------------------------------------------------ util
    def is_captain_or_admin(self, interaction: discord.Interaction, session_id, team_ids=None):
        state = self.active_sessions.get(session_id)
        if not state:
            return False
        if interaction.user.guild_permissions.manage_guild:
            return True
        captain_ids = {
            t["captain_id"] for tid, t in state["teams"].items() if team_ids is None or tid in team_ids
        }
        return interaction.user.id in captain_ids

    def _team_players_for_match(self, session_id, team_id):
        state = self.active_sessions[session_id]
        guild_id = state["guild_id"]
        ids = state["teams"][team_id]["on_field"]
        players = []
        for pid in ids:
            p = db.get_player(guild_id, pid)
            if p:
                players.append(p)
        return players

    async def _get_or_create_progress_channel(self, guild: discord.Guild):
        cfg = db.get_guild_config(guild.id) or {}
        channel = guild.get_channel(cfg.get("progress_channel_id")) if cfg.get("progress_channel_id") else None
        if channel is None:
            channel = await voice_utils.create_progress_text_channel(guild, category=None)
            db.upsert_guild_config(guild.id, progress_channel_id=channel.id)
        return channel

    # -------------------------------------------------------------- start
    async def start_session(self, guild: discord.Guild, mode: str, players: list, announce_channel_id: int):
        num_teams = config.MODE_TEAMS[mode]
        actual_count = len(players)
        target_cap = config.QUEUE_CAP[mode]
        initial_team_size = max(1, actual_count // num_teams)
        missing = max(0, target_cap - actual_count)
        bench_limit = config.BENCH_SIZE + missing

        built = team_balance.build_teams(players, num_teams, initial_team_size=initial_team_size)
        club_names = team_balance.pick_random_club_names(db.list_clubs(guild.id), num_teams)
        captain_ids = [t["captain"]["discord_id"] for t in built]

        session_id = db.create_session(guild.id, mode)
        category = await voice_utils.create_session_category(guild, session_id, mode)
        progress_channel = await self._get_or_create_progress_channel(guild)

        teams_state = {}
        db_team_ids = []
        for i, built_team in enumerate(built):
            club_name = club_names[i]
            captain = built_team["captain"]
            member_ids = [m["discord_id"] for m in built_team["members"]]

            team_id = db.create_team(session_id, club_name, captain_id=captain["discord_id"])
            db_team_ids.append(team_id)

            # channel is always created at the full config.TEAM_SIZE cap, even
            # if we're only seating `initial_team_size` right now - that way
            # subs pulled in later from the bench have somewhere to go.
            channel = await voice_utils.create_team_voice_channel(guild, category, club_name, member_ids, captain_ids)
            db.set_team_voice_channel(team_id, channel.id)

            for pid in member_ids:
                db.add_team_member(team_id, pid, role="player")
                await voice_utils.move_member_to_channel(guild, pid, channel)

            teams_state[team_id] = {
                "club_name": club_name,
                "captain_id": captain["discord_id"],
                "voice_channel_id": channel.id,
                "on_field": set(member_ids),
            }
            built_team["_team_id"] = team_id  # stash for the bench pass below

        bench_channel = await voice_utils.create_bench_channel(guild, category, captain_ids, bench_limit=bench_limit)
        control_channel = await voice_utils.create_control_channel(guild, category, captain_ids)
        db.set_session_channels(
            session_id, category_id=category.id, bench_id=bench_channel.id,
            control_id=control_channel.id, progress_id=progress_channel.id,
        )

        # anyone drafted beyond initial_team_size (only happens if actual_count
        # isn't evenly divisible by num_teams) starts the session on the bench
        for built_team in built:
            for bench_player in built_team["bench"]:
                pid = bench_player["discord_id"]
                db.add_team_member(built_team["_team_id"], pid, role="sub")
                await voice_utils.move_member_to_channel(guild, pid, bench_channel)

        rounds = team_balance.generate_round_robin(db_team_ids)
        db.create_matches(session_id, rounds)

        self.active_sessions[session_id] = {
            "guild_id": guild.id,
            "mode": mode,
            "teams": teams_state,
            "bench_channel_id": bench_channel.id,
            "control_channel_id": control_channel.id,
            "progress_channel_id": progress_channel.id,
            "category_id": category.id,
            "announce_channel_id": announce_channel_id,
            "current_round": 1,
            "total_rounds": len(rounds),
            "progress_round_message": None,
            "auto_close_task": None,
            "sub_lock": asyncio.Lock(),
        }

        await self._post_team_overview(guild, session_id)
        control_channel_obj = guild.get_channel(control_channel.id)
        await control_channel_obj.send(view=SessionControlPanelView(self, session_id, teams_state))
        await self._post_round(guild, session_id, 1)

        announce_channel = guild.get_channel(announce_channel_id)
        if announce_channel:
            clubs_line = ", ".join(club_names)
            extra_note = f" ({missing} bench seats reserved for late arrivals.)" if missing else ""
            await announce_channel.send(
                f"🟢 **NTF {mode.title()} session started!** Teams: {clubs_line}.{extra_note} "
                f"Captains — check {control_channel_obj.mention}."
            )

        return session_id

    async def _post_team_overview(self, guild, session_id):
        state = self.active_sessions[session_id]
        control_channel = guild.get_channel(state["control_channel_id"])
        embed = discord.Embed(title="🎛️ NTF Session Control", color=discord.Color.blurple())
        for team_id, info in state["teams"].items():
            players = [db.get_player(state["guild_id"], pid) for pid in info["on_field"]]
            players = [p for p in players if p]
            avg_mmr = round(sum(p["mmr"] for p in players) / len(players)) if players else 0
            embed.add_field(
                name=info["club_name"],
                value=f"Captain: <@{info['captain_id']}>\nPlayers: {len(players)}\nAvg MMR: {avg_mmr}",
                inline=True,
            )
        embed.set_footer(text="Report results below. Only captains and admins can click.")
        await control_channel.send(embed=embed)

    # -------------------------------------------------------------- rounds
    async def _post_round(self, guild, session_id, round_no):
        state = self.active_sessions[session_id]
        matches = [m for m in db.get_matches_for_session(session_id) if m["round_no"] == round_no]
        control_channel = guild.get_channel(state["control_channel_id"])
        progress_channel = guild.get_channel(state["progress_channel_id"])

        # clear round divider so captains can tell fixtures apart at a glance
        await control_channel.send(f"**━━━━━━━━━━ ROUND {round_no} ━━━━━━━━━━**")

        watch_targets = []
        embed_index = {}
        progress_embeds = []
        for n, match in enumerate(matches, start=1):
            club_a = state["teams"][match["team_a_id"]]["club_name"]
            club_b = state["teams"][match["team_b_id"]]["club_name"]

            control_embed = discord.Embed(
                title=f"Round {round_no} • Match {n}/{len(matches)}: {club_a} vs {club_b}",
                description="Captains, click your side once the game finishes.",
                color=discord.Color.orange(),
            )
            view = MatchControlView(self, session_id, match["id"], match["team_a_id"], club_a, match["team_b_id"], club_b)
            await control_channel.send(embed=control_embed, view=view)

            # one SEPARATE embed per fixture in #in-progress - a distinct
            # coloured card per matchup is a much clearer split between the
            # teams than cramming everything into one embed's fields.
            fixture_embed = discord.Embed(
                title=f"{club_a}  🆚  {club_b}",
                description="⏳ In progress",
                color=discord.Color.orange(),
            )
            progress_embeds.append(fixture_embed)
            embed_index[match["id"]] = n - 1

            watch_targets.append((match["team_a_id"], club_a))
            watch_targets.append((match["team_b_id"], club_b))

        header_embed = discord.Embed(title=f"📺 Round {round_no} — Now Playing", color=discord.Color.green())
        all_embeds = [header_embed] + progress_embeds

        spectate_view = SpectateView(self, session_id, watch_targets)
        progress_message = await progress_channel.send(embeds=all_embeds, view=spectate_view)

        state["progress_round_message"] = {
            "message": progress_message,
            "embed_index": embed_index,   # match_id -> index INTO fixture embeds (add 1 for real embeds list index)
            "round_no": round_no,
        }

    def _round_result_line(self, club_a, score_a, club_b, score_b, a_won):
        score_text = f"{score_a} - {score_b}" if score_a is not None else "final"
        if a_won:
            return f"🟩 **{club_a}** 👑  {score_text}  {club_b} 🟥"
        return f"🟥 {club_a}  {score_text}  👑 **{club_b}** 🟩"

    async def _update_progress_field(self, session_id, match_id, club_a, club_b, score_a, score_b, a_won):
        state = self.active_sessions.get(session_id)
        pr = state["progress_round_message"] if state else None
        if not pr or match_id not in pr["embed_index"]:
            return
        message = pr["message"]
        idx = pr["embed_index"][match_id] + 1  # +1 to skip the header embed
        try:
            embeds = list(message.embeds)
            fixture_embed = embeds[idx]
            fixture_embed.description = self._round_result_line(club_a, score_a, club_b, score_b, a_won)
            fixture_embed.colour = discord.Color.green() if a_won else discord.Color.red()
            embeds[idx] = fixture_embed
            await message.edit(embeds=embeds)
        except (discord.HTTPException, IndexError):
            pass

    # -------------------------------------------------------------- results
    async def finalize_match(self, interaction, match_id, winner_team_id, loser_team_id,
                              winner_score, loser_score, origin_view: MatchControlView, origin_message):
        match = db.get_match(match_id)
        session_id = match["session_id"]
        state = self.active_sessions.get(session_id)
        if not state:
            await interaction.response.send_message("This session has ended.", ephemeral=True)
            return

        team_a_id, team_b_id = match["team_a_id"], match["team_b_id"]
        a_won = winner_team_id == team_a_id
        team_a_players = self._team_players_for_match(session_id, team_a_id)
        team_b_players = self._team_players_for_match(session_id, team_b_id)

        # individual player MMR only - clubs/teams never carry MMR themselves
        mmr_updates = mmr.apply_match_result(team_a_players, team_b_players, a_won)
        winner_ids = {p["discord_id"] for p in (team_a_players if a_won else team_b_players)}
        for discord_id, new_mmr in mmr_updates.items():
            db.update_mmr(state["guild_id"], discord_id, new_mmr, won=discord_id in winner_ids)

        score_a = winner_score if a_won else loser_score
        score_b = loser_score if a_won else winner_score
        db.report_match_result(match_id, winner_team_id, score_a, score_b)

        club_a = state["teams"][team_a_id]["club_name"]
        club_b = state["teams"][team_b_id]["club_name"]
        db.record_match_participants(
            state["guild_id"], match_id, session_id, team_a_id, team_b_id,
            [p["discord_id"] for p in team_a_players], [p["discord_id"] for p in team_b_players],
            club_a, club_b, a_won,
        )

        winner_club = state["teams"][winner_team_id]["club_name"]

        # flip the control-room buttons: winner green + disabled, loser grey + disabled
        origin_view.btn_a.disabled = True
        origin_view.btn_b.disabled = True
        if winner_team_id == origin_view.team_a_id:
            origin_view.btn_a.style = discord.ButtonStyle.success
            origin_view.btn_a.emoji = "👑"
            origin_view.btn_b.style = discord.ButtonStyle.secondary
        else:
            origin_view.btn_b.style = discord.ButtonStyle.success
            origin_view.btn_b.emoji = "👑"
            origin_view.btn_a.style = discord.ButtonStyle.secondary
        await origin_message.edit(view=origin_view)

        guild = interaction.guild
        await self._update_progress_field(session_id, match_id, club_a, club_b, score_a, score_b, a_won)
        await leaderboard_utils.refresh_leaderboard_channel(self.bot, guild)

        await interaction.response.send_message(f"Result recorded — {winner_club} win ✅", ephemeral=True)

        await self._maybe_advance_round(guild, session_id)

    async def _maybe_advance_round(self, guild, session_id):
        state = self.active_sessions[session_id]
        all_matches = db.get_matches_for_session(session_id)
        round_no = state["current_round"]
        this_round = [m for m in all_matches if m["round_no"] == round_no]
        if any(m["status"] != "reported" for m in this_round):
            return  # round still in progress

        if round_no < state["total_rounds"]:
            # the old round's progress message disappears, a fresh one takes its place
            pr = state.get("progress_round_message")
            if pr and pr["message"]:
                try:
                    await pr["message"].delete()
                except discord.HTTPException:
                    pass
            state["current_round"] += 1
            await self._post_round(guild, session_id, state["current_round"])
        else:
            await self._post_final_standings(guild, session_id, all_matches)

    async def _post_final_standings(self, guild, session_id, all_matches):
        state = self.active_sessions[session_id]
        wins_by_team = {tid: 0 for tid in state["teams"]}
        losses_by_team = {tid: 0 for tid in state["teams"]}
        goals_for = {tid: 0 for tid in state["teams"]}
        goals_against = {tid: 0 for tid in state["teams"]}

        for m in all_matches:
            winner = m["winner_team_id"]
            if winner not in wins_by_team:
                continue
            wins_by_team[winner] += 1
            loser = m["team_b_id"] if winner == m["team_a_id"] else m["team_a_id"]
            if loser in losses_by_team:
                losses_by_team[loser] += 1

            score_a, score_b = m["score_a"], m["score_b"]
            if score_a is not None and score_b is not None:
                if m["team_a_id"] in goals_for:
                    goals_for[m["team_a_id"]] += score_a
                    goals_against[m["team_a_id"]] += score_b
                if m["team_b_id"] in goals_for:
                    goals_for[m["team_b_id"]] += score_b
                    goals_against[m["team_b_id"]] += score_a

        # Ranked by wins, then fewer losses, then goal differential - GD is
        # what actually separates teams tied on wins/losses since everyone
        # in a round robin plays the same number of games.
        team_ids_ranked = sorted(
            state["teams"].keys(),
            key=lambda tid: (
                -wins_by_team[tid],
                losses_by_team[tid],
                -(goals_for[tid] - goals_against[tid]),
            ),
        )

        medals = ["🥇", "🥈", "🥉", "4️⃣"]
        lines = []
        for i, team_id in enumerate(team_ids_ranked):
            club = state["teams"][team_id]["club_name"]
            captain = state["teams"][team_id]["captain_id"]
            wins = wins_by_team[team_id]
            losses = losses_by_team[team_id]
            gd = goals_for[team_id] - goals_against[team_id]
            gd_text = f"+{gd}" if gd > 0 else str(gd)
            medal = medals[i] if i < len(medals) else f"{i + 1}."
            lines.append(
                f"{medal} **{club}** — {wins}W-{losses}L — GD {gd_text} "
                f"({goals_for[team_id]}-{goals_against[team_id]}) — Captain <@{captain}>"
            )

        old_pr = state.get("progress_round_message")
        if old_pr and old_pr["message"]:
            try:
                await old_pr["message"].delete()
            except discord.HTTPException:
                pass

        embed = discord.Embed(title="🏁 Session Complete — Final Standings", description="\n".join(lines), color=discord.Color.gold())
        embed.set_footer(text=f"All player MMR is already up to date. Voice channels close automatically in {config.SESSION_CLOSE_DELAY_SECONDS} seconds.")

        control_channel = guild.get_channel(state["control_channel_id"])
        progress_channel = guild.get_channel(state["progress_channel_id"])
        await control_channel.send(embed=embed)
        await progress_channel.send(embed=embed)

        state["auto_close_task"] = asyncio.create_task(self._auto_close_after_delay(session_id))

    async def _auto_close_after_delay(self, session_id):
        await asyncio.sleep(config.SESSION_CLOSE_DELAY_SECONDS)
        state = self.active_sessions.get(session_id)
        if not state:
            return  # already ended manually in the meantime
        await self.end_session(session_id, ended_by=None, natural_completion=True)

    # -------------------------------------------------------------- subs
    async def request_sub(self, interaction: discord.Interaction, session_id, team_id, club_name):
        """No swapping - this just transfers a random player from the Bench
        VC onto the requesting team, filling an empty seat (typically left
        over from a force start)."""
        state = self.active_sessions.get(session_id)
        if not state:
            await interaction.response.send_message("This session has ended.", ephemeral=True)
            return

        captain_id = state["teams"][team_id]["captain_id"]
        is_admin = interaction.user.guild_permissions.manage_guild
        if interaction.user.id != captain_id and not is_admin:
            await interaction.response.send_message(f"Only {club_name}'s captain can request a sub.", ephemeral=True)
            return

        async with state["sub_lock"]:
            # re-fetch state in case the session ended while we were waiting on the lock
            state = self.active_sessions.get(session_id)
            if not state:
                await interaction.response.send_message("This session has ended.", ephemeral=True)
                return

            guild = interaction.guild
            bench_channel = guild.get_channel(state["bench_channel_id"])
            # Exclude anyone already active on ANY team, not just this one - a
            # player who's still registered elsewhere shouldn't be double-booked
            # onto a second team's roster.
            all_on_field = set()
            for t in state["teams"].values():
                all_on_field |= t["on_field"]
            candidates = [m for m in bench_channel.members if m.id not in all_on_field]
            if not candidates:
                await interaction.response.send_message("Nobody is in the Bench right now.", ephemeral=True)
                return

            incoming = random.choice(candidates)
            team_channel = guild.get_channel(state["teams"][team_id]["voice_channel_id"])

            # Subs ADD to the roster rather than swapping anyone out - so a sub
            # can legitimately push a team past the normal 6 (e.g. 6 -> 7).
            # Raise the VC's user_limit by one first, or Discord will refuse to
            # move them into an already-full channel.
            try:
                await team_channel.edit(user_limit=team_channel.user_limit + 1)
            except discord.HTTPException:
                pass

            await voice_utils.allow_member_in_channel(team_channel, incoming, connect=True)
            moved = await voice_utils.move_member_to_channel(guild, incoming.id, team_channel)

            state["teams"][team_id]["on_field"].add(incoming.id)
            db.add_team_member(team_id, incoming.id, "player")

        progress_channel = guild.get_channel(state["progress_channel_id"])
        await progress_channel.send(f"🔁 <@{incoming.id}> joins **{club_name}** from the bench.")

        if moved:
            await interaction.response.send_message(f"<@{incoming.id}> has been moved onto {club_name}.", ephemeral=True)
        else:
            await interaction.response.send_message(
                f"<@{incoming.id}> has been added to {club_name}, but they weren't in voice so I couldn't drag them — ask them to rejoin the Bench VC.",
                ephemeral=True,
            )

    # -------------------------------------------------------------- end
    async def end_session(self, session_id, ended_by: discord.Member = None, natural_completion: bool = False):
        state = self.active_sessions.get(session_id)
        if not state:
            return
        guild = self.bot.get_guild(state["guild_id"])
        if guild is None:
            return

        # if a captain/admin manually ends early, cancel the pending 60s auto-close
        task = state.get("auto_close_task")
        if task and not task.done() and not natural_completion:
            task.cancel()

        progress_channel = guild.get_channel(state["progress_channel_id"])

        if not natural_completion:
            announce_channel = guild.get_channel(state["announce_channel_id"])
            if announce_channel:
                who = ended_by.mention if ended_by else "the system"
                lines = [f"**{info['club_name']}** — Captain <@{info['captain_id']}>" for info in state["teams"].values()]
                await announce_channel.send(f"🔴 **NTF session ended** by {who}.\n" + "\n".join(lines))

        category = guild.get_channel(state["category_id"])
        if category:
            await voice_utils.teardown_session_category(guild, category)

        if progress_channel:
            await progress_channel.send("🚫 No games are currently in progress.")

        db.end_session(session_id)
        del self.active_sessions[session_id]

    # -------------------------------------------------------------- admin overrides
    @app_commands.command(name="force_end_session", description="[Admin] Force-end an active NTF session, no captain needed")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def force_end_session(self, interaction: discord.Interaction):
        guild_sessions = [sid for sid, s in self.active_sessions.items() if s["guild_id"] == interaction.guild_id]
        if not guild_sessions:
            await interaction.response.send_message("No active sessions on this server.", ephemeral=True)
            return
        if len(guild_sessions) == 1:
            await interaction.response.send_message("Ending the active session…", ephemeral=True)
            await self.end_session(guild_sessions[0], ended_by=interaction.user)
            return
        view = discord.ui.View(timeout=30)
        for sid in guild_sessions:
            club_list = ", ".join(t["club_name"] for t in self.active_sessions[sid]["teams"].values())
            btn = discord.ui.Button(label=f"Session #{sid}: {club_list}"[:80], style=discord.ButtonStyle.danger)

            async def make_cb(session_id=sid):
                async def cb(inter: discord.Interaction):
                    await inter.response.send_message(f"Ending session #{session_id}…", ephemeral=True)
                    await self.end_session(session_id, ended_by=inter.user)
                return cb

            btn.callback = await make_cb()
            view.add_item(btn)
        await interaction.response.send_message("Multiple sessions active — pick one to end:", view=view, ephemeral=True)

    @app_commands.command(name="admin_end_all_sessions", description="[Admin] Immediately end every active NTF session on this server")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def admin_end_all_sessions(self, interaction: discord.Interaction):
        guild_sessions = [sid for sid, s in self.active_sessions.items() if s["guild_id"] == interaction.guild_id]
        if not guild_sessions:
            await interaction.response.send_message("No active sessions on this server.", ephemeral=True)
            return
        await interaction.response.send_message(f"Ending {len(guild_sessions)} session(s)…", ephemeral=True)
        for sid in list(guild_sessions):
            await self.end_session(sid, ended_by=interaction.user)


async def setup(bot):
    await bot.add_cog(SessionCog(bot))

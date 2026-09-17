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
    """Popup asking for the scoreline once a captain/admin picks a winner.
    went_to_pens changes the labels to make clear this is the regulation
    score (the shootout winner was already decided by the Penalties button
    before this modal ever opens) and tags the result accordingly."""

    def __init__(self, cog: "SessionCog", match_id, winner_team_id, loser_team_id,
                 winner_club, loser_club, origin_view, origin_message, went_to_pens: bool = False):
        title = f"{winner_club} vs {loser_club}" + (" (Pens)" if went_to_pens else "")
        super().__init__(title=title)
        self.cog = cog
        self.match_id = match_id
        self.winner_team_id = winner_team_id
        self.loser_team_id = loser_team_id
        self.winner_club = winner_club
        self.loser_club = loser_club
        self.origin_view = origin_view
        self.origin_message = origin_message
        self.went_to_pens = went_to_pens

        score_label_suffix = " (regulation)" if went_to_pens else ""
        self.winner_score = discord.ui.TextInput(
            label=f"{winner_club} score{score_label_suffix}", placeholder="e.g. 4", max_length=3, required=True
        )
        self.loser_score = discord.ui.TextInput(
            label=f"{loser_club} score{score_label_suffix}", placeholder="e.g. 2", max_length=3, required=True
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
            w_score, l_score, self.origin_view, self.origin_message, self.went_to_pens,
        )


class PenaltyPickView(discord.ui.View):
    """Shown after tapping Penalties - pick which team actually won the
    shootout, separate from the regulation scoreline entered afterward."""

    def __init__(self, cog: "SessionCog", session_id, match_id,
                 team_a_id, team_a_club, team_b_id, team_b_club,
                 origin_view, origin_message):
        super().__init__(timeout=60)
        self.cog = cog
        self.session_id = session_id
        self.match_id = match_id
        self.team_a_id, self.team_a_club = team_a_id, team_a_club
        self.team_b_id, self.team_b_club = team_b_id, team_b_club
        self.origin_view = origin_view
        self.origin_message = origin_message

        btn_a = discord.ui.Button(label=f"{team_a_club} won on pens", style=discord.ButtonStyle.success)
        btn_b = discord.ui.Button(label=f"{team_b_club} won on pens", style=discord.ButtonStyle.success)
        btn_a.callback = self._make_callback(team_a_id, team_a_club, team_b_id, team_b_club)
        btn_b.callback = self._make_callback(team_b_id, team_b_club, team_a_id, team_a_club)
        self.add_item(btn_a)
        self.add_item(btn_b)

    def _make_callback(self, winner_id, winner_club, loser_id, loser_club):
        async def callback(interaction: discord.Interaction):
            # No scoreline needed for a penalty win - the shootout winner is
            # already fully decided by this click, and penalties don't
            # contribute goals toward the goal-differential tiebreaker (a
            # shootout isn't "real" goals scored during play), so this
            # finalizes directly with no score at all.
            await self.cog.finalize_match(
                interaction, self.match_id, winner_id, loser_id, None, None,
                self.origin_view, self.origin_message, went_to_pens=True,
            )
        return callback


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
        self.btn_pens = discord.ui.Button(label="Went to Penalties", style=discord.ButtonStyle.secondary, emoji="⚽", row=1)
        self.btn_live = discord.ui.Button(label="Match Live", style=discord.ButtonStyle.success, emoji="▶️", row=1)
        self.btn_a.callback = self._make_callback(team_a_id, team_a_club, team_b_id, team_b_club)
        self.btn_b.callback = self._make_callback(team_b_id, team_b_club, team_a_id, team_a_club)
        self.btn_pens.callback = self._make_pens_callback()
        self.btn_live.callback = self._make_live_callback()
        self.add_item(self.btn_a)
        self.add_item(self.btn_b)
        self.add_item(self.btn_pens)
        self.add_item(self.btn_live)

    def _make_live_callback(self):
        async def callback(interaction: discord.Interaction):
            if not self.cog.is_captain_or_admin(interaction, self.session_id, {self.team_a_id, self.team_b_id}):
                await interaction.response.send_message(
                    "Only a captain of one of these two teams (or a server admin) can mark this match live.",
                    ephemeral=True,
                )
                return
            state = self.cog.active_sessions.get(self.session_id)
            if not state:
                await interaction.response.send_message("This session has ended.", ephemeral=True)
                return
            # Freezes both teams' CURRENT rosters for this specific match -
            # if a sub happens after this point but before the result is
            # reported, the sub won't count for THIS match, only future
            # ones, since they weren't part of the frozen snapshot.
            state["match_rosters"][self.match_id] = {
                "team_a": set(state["teams"][self.team_a_id]["on_field"]),
                "team_b": set(state["teams"][self.team_b_id]["on_field"]),
            }
            self.btn_live.disabled = True
            self.btn_live.label = "Rosters Locked"
            await interaction.response.edit_message(view=self)
            await self.cog._mark_progress_field_live(self.session_id, self.match_id)
        return callback

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

    def _make_pens_callback(self):
        async def callback(interaction: discord.Interaction):
            if not self.cog.is_captain_or_admin(interaction, self.session_id, {self.team_a_id, self.team_b_id}):
                await interaction.response.send_message(
                    "Only a captain of one of these two teams (or a server admin) can report this result.",
                    ephemeral=True,
                )
                return
            pick_view = PenaltyPickView(
                self.cog, self.session_id, self.match_id,
                self.team_a_id, self.team_a_club, self.team_b_id, self.team_b_club,
                origin_view=self, origin_message=interaction.message,
            )
            await interaction.response.send_message(
                "Who won the penalty shootout?", view=pick_view, ephemeral=True,
            )
        return callback


class TransferDestinationView(discord.ui.View):
    """Second step of a manual transfer - pick which team to move the
    already-selected player onto."""

    def __init__(self, cog: "SessionCog", session_id, player_id: int, player_label: str):
        super().__init__(timeout=60)
        self.cog = cog
        self.session_id = session_id
        self.player_id = player_id
        state = cog.active_sessions[session_id]
        options = [
            discord.SelectOption(label=info["club_name"], value=str(team_id))
            for team_id, info in state["teams"].items()
        ]
        select = discord.ui.Select(placeholder=f"Move {player_label} to which team?", options=options[:25])
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        dest_team_id = int(interaction.data["values"][0])
        state = self.cog.active_sessions.get(self.session_id)
        if not state:
            await interaction.followup.send("This session has ended.", ephemeral=True)
            return
        added, moved, reason = await self.cog.transfer_player(interaction.guild, self.session_id, state, self.player_id, dest_team_id)
        club_name = state["teams"][dest_team_id]["club_name"]
        if not added:
            await interaction.followup.send(f"❌ Transfer blocked: {reason}.", ephemeral=True)
        elif moved:
            await interaction.followup.send(f"Moved <@{self.player_id}> onto {club_name}.", ephemeral=True)
        else:
            await interaction.followup.send(
                f"<@{self.player_id}> is now on {club_name}'s roster, but couldn't be physically dragged there: {reason}.",
                ephemeral=True,
            )


class ReassignCaptainPlayerSelectView(discord.ui.View):
    """Second step of reassigning a captain - pick the new captain from
    that team's current roster."""

    def __init__(self, cog: "SessionCog", session_id, team_id: int):
        super().__init__(timeout=60)
        self.cog = cog
        self.session_id = session_id
        self.team_id = team_id
        state = cog.active_sessions[session_id]
        info = state["teams"][team_id]

        options = []
        for pid in info["on_field"]:
            member = cog.bot.get_user(pid)
            label = member.display_name if member else str(pid)
            if pid == info["captain_id"]:
                label += " (current captain)"
            options.append(discord.SelectOption(label=label, value=str(pid)))

        if options:
            select = discord.ui.Select(placeholder=f"New captain for {info['club_name']}?", options=options[:25])
            select.callback = self._on_select
            self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        new_captain_id = int(interaction.data["values"][0])
        await interaction.response.defer(ephemeral=True)
        state = self.cog.active_sessions.get(self.session_id)
        if not state:
            await interaction.followup.send("This session has ended.", ephemeral=True)
            return
        if new_captain_id == state["teams"][self.team_id]["captain_id"]:
            await interaction.followup.send("They're already the captain of that team.", ephemeral=True)
            return
        await self.cog.reassign_captain(interaction.guild, self.session_id, self.team_id, new_captain_id)
        club_name = state["teams"][self.team_id]["club_name"]
        await interaction.followup.send(f"🎖️ <@{new_captain_id}> is now the captain of **{club_name}**.", ephemeral=True)


class ReassignCaptainTeamSelectView(discord.ui.View):
    """First step of reassigning a captain - pick which team needs a new one."""

    def __init__(self, cog: "SessionCog", session_id):
        super().__init__(timeout=60)
        self.cog = cog
        self.session_id = session_id
        state = cog.active_sessions[session_id]
        options = [
            discord.SelectOption(
                label=f"{info['club_name']} (captain: {cog.bot.get_user(info['captain_id']).display_name if info['captain_id'] and cog.bot.get_user(info['captain_id']) else 'none assigned'})",
                value=str(team_id),
            )
            for team_id, info in state["teams"].items()
        ]
        select = discord.ui.Select(placeholder="Which team needs a new captain?", options=options[:25])
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        team_id = int(interaction.data["values"][0])
        state = self.cog.active_sessions.get(self.session_id)
        if not state:
            await interaction.response.send_message("This session has ended.", ephemeral=True)
            return
        view = ReassignCaptainPlayerSelectView(self.cog, self.session_id, team_id)
        if not view.children:
            await interaction.response.send_message("Nobody is currently rostered on that team.", ephemeral=True)
            return
        club_name = state["teams"][team_id]["club_name"]
        await interaction.response.send_message(f"Who should captain **{club_name}**?", view=view, ephemeral=True)


class RemovePlayerSelectView(discord.ui.View):
    """Pick a currently-rostered player to remove from their team entirely
    (not benched, not transferred elsewhere - fully off the roster). Frees
    up their team's sub slot for someone else, for when a player leaves the
    session mid-way and there's no one to swap them for."""

    def __init__(self, cog: "SessionCog", session_id):
        super().__init__(timeout=60)
        self.cog = cog
        self.session_id = session_id
        state = cog.active_sessions[session_id]

        options = []
        for team_id, info in state["teams"].items():
            for pid in info["on_field"]:
                member = cog.bot.get_user(pid)
                label = member.display_name if member else str(pid)
                options.append(discord.SelectOption(label=f"{label} ({info['club_name']})", value=f"{team_id}:{pid}"))

        if options:
            select = discord.ui.Select(placeholder="Remove which player from their team entirely?", options=options[:25])
            select.callback = self._on_select
            self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        team_id_str, pid_str = interaction.data["values"][0].split(":")
        team_id, player_id = int(team_id_str), int(pid_str)
        state = self.cog.active_sessions.get(self.session_id)
        if not state:
            await interaction.followup.send("This session has ended.", ephemeral=True)
            return
        club_name = state["teams"][team_id]["club_name"]
        await self.cog.remove_player_from_roster(interaction.guild, self.session_id, team_id, player_id)
        await interaction.followup.send(f"❌ Removed <@{player_id}> from **{club_name}**'s roster. That slot is now free.", ephemeral=True)


class TransferPlayerSelectView(discord.ui.View):
    """First step of a manual transfer - pick which currently-rostered
    player to move. Only lists players actively on a team, not fake/test
    accounts or people the bot can't resolve to a real member."""

    def __init__(self, cog: "SessionCog", session_id):
        super().__init__(timeout=60)
        self.cog = cog
        self.session_id = session_id
        state = cog.active_sessions[session_id]

        options = []
        for team_id, info in state["teams"].items():
            for pid in info["on_field"]:
                member = cog.bot.get_user(pid)
                label = member.display_name if member else str(pid)
                options.append(discord.SelectOption(label=f"{label} ({info['club_name']})", value=str(pid)))

        if options:
            select = discord.ui.Select(placeholder="Which player do you want to transfer?", options=options[:25])
            select.callback = self._on_select
            self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        player_id = int(interaction.data["values"][0])
        member = interaction.guild.get_member(player_id)
        label = member.display_name if member else str(player_id)
        await interaction.response.send_message(
            f"Transfer **{label}** to which team?",
            view=TransferDestinationView(self.cog, self.session_id, player_id, label),
            ephemeral=True,
        )


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

        transfer_btn = discord.ui.Button(label="Transfer Player", style=discord.ButtonStyle.primary, emoji="🔄")
        transfer_btn.callback = self._transfer_callback
        self.add_item(transfer_btn)

        reassign_btn = discord.ui.Button(label="Reassign Captain", style=discord.ButtonStyle.primary, emoji="🎖️")
        reassign_btn.callback = self._reassign_captain_callback
        self.add_item(reassign_btn)

        remove_btn = discord.ui.Button(label="Remove Player", style=discord.ButtonStyle.danger, emoji="❌")
        remove_btn.callback = self._remove_player_callback
        self.add_item(remove_btn)

        end_btn = discord.ui.Button(label="End Session", style=discord.ButtonStyle.danger, emoji="🛑")
        end_btn.callback = self._end_callback
        self.add_item(end_btn)

    def _make_sub_callback(self, team_id, club_name):
        async def callback(interaction: discord.Interaction):
            await self.cog.request_sub(interaction, self.session_id, team_id, club_name)
        return callback

    async def _transfer_callback(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                "Only an admin can manually transfer a player between teams — a captain can still use "
                "Add Sub for their own team.",
                ephemeral=True,
            )
            return
        state = self.cog.active_sessions.get(self.session_id)
        if not state:
            await interaction.response.send_message("This session has ended.", ephemeral=True)
            return
        view = TransferPlayerSelectView(self.cog, self.session_id)
        if not view.children:
            await interaction.response.send_message("Nobody is currently rostered to transfer.", ephemeral=True)
            return
        await interaction.response.send_message("Who do you want to transfer?", view=view, ephemeral=True)

    async def _reassign_captain_callback(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                "Only an admin can reassign a team's captain.",
                ephemeral=True,
            )
            return
        state = self.cog.active_sessions.get(self.session_id)
        if not state:
            await interaction.response.send_message("This session has ended.", ephemeral=True)
            return
        view = ReassignCaptainTeamSelectView(self.cog, self.session_id)
        await interaction.response.send_message("Which team needs a new captain?", view=view, ephemeral=True)

    async def _remove_player_callback(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                "Only an admin can remove a player from their team's roster entirely.",
                ephemeral=True,
            )
            return
        state = self.cog.active_sessions.get(self.session_id)
        if not state:
            await interaction.response.send_message("This session has ended.", ephemeral=True)
            return
        view = RemovePlayerSelectView(self.cog, self.session_id)
        if not view.children:
            await interaction.response.send_message("Nobody is currently rostered to remove.", ephemeral=True)
            return
        await interaction.response.send_message("Remove who from their team entirely?", view=view, ephemeral=True)

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
            # Defer immediately - several Discord API calls happen below
            # (permission grant, move, mute) before we'd otherwise respond,
            # which can blow past Discord's 3-second window under rapid
            # clicking and show "didn't respond in time".
            await interaction.response.defer(ephemeral=True)

            state = self.cog.active_sessions.get(self.session_id)
            if not state:
                await interaction.followup.send("This session has ended.", ephemeral=True)
                return
            member = interaction.user
            if member.voice is None or member.voice.channel is None:
                await interaction.followup.send(
                    "Hop into any voice channel first — Discord won't let the bot pull you in from nowhere.",
                    ephemeral=True,
                )
                return

            # Fully serialize spectate switches (and the auto-unmute listener
            # below) through one lock per session - rapid clicking across
            # several different Watch buttons was interleaving moves, mutes,
            # and the auto-unmute listener's checks in ways that occasionally
            # left someone unmuted even though they were still spectating.
            async with state["spectate_lock"]:
                state = self.cog.active_sessions.get(self.session_id)
                if not state:
                    await interaction.followup.send("This session has ended.", ephemeral=True)
                    return

                channel = interaction.guild.get_channel(state["teams"][team_id]["voice_channel_id"])
                await voice_utils.allow_member_in_channel(channel, member, connect=True)
                # Bump the channel's displayed capacity by one for a
                # spectator too - it never actually blocked the move (the
                # bot's own Move Members bypasses that cap regardless), but
                # showing "7/6" style numbers only for subs and never for
                # spectators looked inconsistent, so this keeps the number
                # honest either way.
                try:
                    await channel.edit(user_limit=channel.user_limit + 1)
                except discord.HTTPException:
                    pass
                moved, reason = await voice_utils.move_member_to_channel(interaction.guild, member.id, channel)
                if moved:
                    state["spectators"][member.id] = channel.id
                    await voice_utils.set_spectator_mute(interaction.guild, member.id, True)

            if moved:
                await interaction.followup.send(
                    f"You're now spectating {club_name} (muted). The mute lifts automatically as soon as you "
                    f"leave this VC.",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(f"Couldn't move you — {reason}.", ephemeral=True)
        return callback


def _captain_display(captain_id):
    """Renders a team's captain as a mention, or a clear placeholder if
    that team's captaincy was stripped (e.g. after a captain transferred
    away) and hasn't been reassigned yet."""
    if captain_id is None:
        return "⚠️ *No captain assigned — use Reassign Captain*"
    return f"<@{captain_id}>"


class SessionCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # session_id -> runtime state (teams, matches progress, channel ids)
        self.active_sessions: dict[int, dict] = {}

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        """Auto-lifts a spectator's mute the moment they leave the VC they
        were watching - to Bench, back to general chat, or disconnecting
        entirely. The mute is only ever meant to last while they're actually
        sitting in that specific team's channel. Goes through the same
        spectate_lock as the Watch buttons themselves, so this can't fire
        mid-way through a button click still updating that same record."""
        for state in self.active_sessions.values():
            if state["guild_id"] != member.guild.id:
                continue
            async with state["spectate_lock"]:
                watching_channel_id = state["spectators"].get(member.id)
                if watching_channel_id is None:
                    continue
                after_channel_id = after.channel.id if after.channel else None
                if after_channel_id != watching_channel_id:
                    await voice_utils.set_spectator_mute(member.guild, member.id, False)
                    del state["spectators"][member.id]
                    # Give back the capacity the Watch button borrowed when
                    # they joined, same as a sub leaving a team - never below
                    # the normal TEAM_SIZE floor.
                    watched_channel = member.guild.get_channel(watching_channel_id)
                    if watched_channel and watched_channel.user_limit > config.TEAM_SIZE:
                        try:
                            await watched_channel.edit(user_limit=watched_channel.user_limit - 1)
                        except discord.HTTPException:
                            pass

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

    def _team_players_for_match(self, session_id, team_id, match_id=None, side=None):
        """If match_id/side are given AND that match's roster was frozen via
        the Match Live button, uses that frozen snapshot instead of the
        CURRENT on_field roster - this is what stops a sub who joins mid-
        match (after it went live, before the result is reported) from
        being credited for a game they didn't actually play from the start.
        Falls back to the live current roster if no freeze was ever taken
        for this match (e.g. captains didn't use the button).
        MMR/wins/losses come from this session's MODE specifically - captain
        and NA status stay shared, so those still come from the players
        table, but MMR is fully mode-scoped."""
        state = self.active_sessions[session_id]
        guild_id = state["guild_id"]
        mode = state["mode"]
        frozen = state.get("match_rosters", {}).get(match_id) if match_id is not None else None
        ids = frozen[side] if frozen else state["teams"][team_id]["on_field"]
        players = []
        for pid in ids:
            p = db.get_player(guild_id, pid)
            if p:
                mode_stats = db.get_player_mode_stats(guild_id, pid, mode)
                players.append({**p, "mmr": mode_stats["mmr"], "wins": mode_stats["wins"], "losses": mode_stats["losses"]})
        return players

    async def _get_or_create_progress_channel(self, guild: discord.Guild):
        cfg = db.get_guild_config(guild.id) or {}
        channel = guild.get_channel(cfg.get("progress_channel_id")) if cfg.get("progress_channel_id") else None
        if channel is None:
            channel = await voice_utils.create_progress_text_channel(guild, category=None)
            db.upsert_guild_config(guild.id, progress_channel_id=channel.id)
        return channel

    # -------------------------------------------------------------- start
    async def start_session(self, guild: discord.Guild, mode: str, players: list, announce_channel_id: int, is_test: bool = False):
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

        # All the channels this session needs are independent of each other
        # (different channels, no shared state) - create them all at once
        # instead of one-by-one, so the wall-clock time is roughly "however
        # long the single slowest channel creation takes" rather than the
        # sum of every one of them in sequence. This is the single biggest
        # lever on how long a session takes to actually spin up.
        team_channel_tasks = [
            voice_utils.create_team_voice_channel(guild, category, club_names[i], [m["discord_id"] for m in built_team["members"]], captain_ids)
            for i, built_team in enumerate(built)
        ]
        bench_channel_task = voice_utils.create_bench_channel(guild, category, captain_ids, bench_limit=bench_limit)
        control_channel_task = voice_utils.create_control_channel(guild, category, captain_ids)
        channel_results = await asyncio.gather(*team_channel_tasks, bench_channel_task, control_channel_task)
        team_channels = channel_results[:num_teams]
        bench_channel = channel_results[num_teams]
        control_channel = channel_results[num_teams + 1]

        # Ghosts get the same free-roaming Move Members access every captain
        # has across every team VC and Bench - NEVER session-control, since
        # they have zero roster or match-reporting authority, purely voice
        # mobility. Applied fresh for every session regardless of whether a
        # given ghost is even playing in it - they're not part of the draft
        # pool and this never touches on_field/team rosters at all.
        for ghost in db.get_ghosts(guild.id):
            ghost_member = guild.get_member(ghost["discord_id"])
            if not ghost_member:
                continue
            for channel in list(team_channels) + [bench_channel]:
                try:
                    await channel.set_permissions(ghost_member, view_channel=True, connect=True, move_members=True)
                except discord.HTTPException:
                    pass

        teams_state = {}
        db_team_ids = []
        failed_moves = []  # (discord_id, intended_club_or_bench, reason) - reported to players after seating
        move_tasks = []
        move_task_meta = []  # (pid, club_name) in the same order as move_tasks, so results line back up
        for i, built_team in enumerate(built):
            club_name = club_names[i]
            captain = built_team["captain"]
            member_ids = [m["discord_id"] for m in built_team["members"]]
            channel = team_channels[i]

            team_id = db.create_team(session_id, club_name, captain_id=captain["discord_id"])
            db_team_ids.append(team_id)

            # channel is always created at the full config.TEAM_SIZE cap, even
            # if we're only seating `initial_team_size` right now - that way
            # subs pulled in later from the bench have somewhere to go.
            db.set_team_voice_channel(team_id, channel.id)

            for pid in member_ids:
                db.add_team_member(team_id, pid, role="player")
                move_tasks.append(voice_utils.move_member_to_channel(guild, pid, channel))
                move_task_meta.append((pid, club_name))

            teams_state[team_id] = {
                "club_name": club_name,
                "captain_id": captain["discord_id"],
                "voice_channel_id": channel.id,
                "on_field": set(member_ids),
            }
            built_team["_team_id"] = team_id  # stash for the bench pass below

        # Moving every player into their team's VC is likewise independent
        # per-player - one slow or rate-limited move no longer holds up
        # every move behind it in line.
        if move_tasks:
            move_results = await asyncio.gather(*move_tasks)
            for (pid, club_name), (moved, reason) in zip(move_task_meta, move_results):
                if not moved:
                    failed_moves.append((pid, club_name, reason))

        # Once someone is seated on a team's roster, they can no longer
        # voluntarily sit in Bench and get poached by another team's sub
        # request - lock them out of Bench specifically. This does NOT
        # apply to genuine bench-only overflow players (handled separately
        # below), who are meant to be there.
        bench_lock_tasks = []
        for info in teams_state.values():
            for pid in info["on_field"]:
                member = guild.get_member(pid)
                if member:
                    bench_lock_tasks.append(voice_utils.allow_member_in_channel(bench_channel, member, connect=False))
        if bench_lock_tasks:
            await asyncio.gather(*bench_lock_tasks)

        db.set_session_channels(
            session_id, category_id=category.id, bench_id=bench_channel.id,
            control_id=control_channel.id, progress_id=progress_channel.id,
        )

        # anyone drafted beyond initial_team_size (only happens if actual_count
        # isn't evenly divisible by num_teams) starts the session on the bench
        bench_move_tasks = []
        bench_move_meta = []
        for built_team in built:
            for bench_player in built_team["bench"]:
                pid = bench_player["discord_id"]
                db.add_team_member(built_team["_team_id"], pid, role="sub")
                bench_move_tasks.append(voice_utils.move_member_to_channel(guild, pid, bench_channel))
                bench_move_meta.append(pid)
        if bench_move_tasks:
            bench_move_results = await asyncio.gather(*bench_move_tasks)
            for pid, (moved, reason) in zip(bench_move_meta, bench_move_results):
                if not moved:
                    failed_moves.append((pid, "Bench", reason))

        # Rivals gets a genuine best-of-two - a single match wouldn't be much
        # of a "session" between just two teams. League stays a single
        # round robin (each team already plays 3 games there).
        rounds = team_balance.generate_round_robin(db_team_ids, double_round=(mode == "rivals"))
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
            "spectators": {},  # user_id -> channel_id they're spectating, for auto-unmute on leave
            "spectate_lock": asyncio.Lock(),
            "roster_message": None,
            "match_rosters": {},  # match_id -> {"team_a": {ids}, "team_b": {ids}} once frozen via Match Live
            "is_test": is_test,
        }

        await self._post_team_overview(guild, session_id)
        await self._post_team_roster(guild, session_id)
        control_channel_obj = guild.get_channel(control_channel.id)
        await control_channel_obj.send(view=SessionControlPanelView(self, session_id, teams_state))
        await self._post_round(guild, session_id, 1)

        announce_channel = guild.get_channel(announce_channel_id)
        if announce_channel:
            clubs_line = ", ".join(club_names)
            extra_note = f" ({missing} bench seats reserved for late arrivals.)" if missing else ""
            test_note = " 🧪 **TEST SESSION — no MMR, wins/losses, or match history will be recorded.**" if is_test else ""
            await announce_channel.send(
                f"🟢 **NTF {mode.title()} session started!** Teams: {clubs_line}.{extra_note}{test_note} "
                f"Captains — check {control_channel_obj.mention}."
            )
            if failed_moves:
                # Discord can only move someone who's already connected to a
                # voice channel somewhere - this can't be forced, so anyone
                # not already in voice when the queue popped needs to join
                # their own VC manually rather than silently being left
                # nowhere with no one aware of it.
                lines = [f"• <@{pid}> → **{dest}** ({reason})" for pid, dest, reason in failed_moves]
                await announce_channel.send(
                    "⚠️ **Couldn't automatically move these players into voice — they'll need to join manually:**\n"
                    + "\n".join(lines)
                )

        return session_id

    async def _post_team_overview(self, guild, session_id):
        state = self.active_sessions[session_id]
        control_channel = guild.get_channel(state["control_channel_id"])
        embed = discord.Embed(title="🎛️ NTF Session Control", color=discord.Color.blurple())
        for team_id, info in state["teams"].items():
            mmrs = [db.get_player_mode_stats(state["guild_id"], pid, state["mode"])["mmr"] for pid in info["on_field"]]
            avg_mmr = round(sum(mmrs) / len(mmrs)) if mmrs else 0
            embed.add_field(
                name=info["club_name"],
                value=f"Captain: {_captain_display(info['captain_id'])}\nPlayers: {len(mmrs)}\nAvg MMR: {avg_mmr}",
                inline=True,
            )
        embed.set_footer(text="Report results below. Only captains and admins can click.")
        await control_channel.send(embed=embed)

    def _build_roster_embed(self, session_id):
        state = self.active_sessions[session_id]
        embed = discord.Embed(title="📋 Team Rosters", color=discord.Color.blurple())
        for info in state["teams"].values():
            member_lines = "\n".join(f"• <@{pid}>" for pid in info["on_field"]) or "*empty*"
            embed.add_field(
                name=info["club_name"],
                value=f"**Captain:** {_captain_display(info['captain_id'])}\n{member_lines}",
                inline=True,
            )
        return embed

    async def _post_team_roster(self, guild, session_id):
        """Posts the roster list to the permanent #in-progress channel so
        everyone can see who's on which team and who's captaining, without
        needing session-control access. Kept up to date via
        _refresh_team_roster whenever a sub changes a team's composition."""
        state = self.active_sessions[session_id]
        progress_channel = guild.get_channel(state["progress_channel_id"])
        message = await progress_channel.send(embed=self._build_roster_embed(session_id))
        state["roster_message"] = message

    async def _refresh_team_roster(self, session_id):
        state = self.active_sessions.get(session_id)
        if not state:
            return
        message = state.get("roster_message")
        if not message:
            return
        try:
            await message.edit(embed=self._build_roster_embed(session_id))
        except discord.HTTPException:
            pass

    # -------------------------------------------------------------- rounds
    async def _send_with_retry(self, coro_func, *args, retries=2, delay=1.5, **kwargs):
        """Retries a Discord API call a couple extra times on a transient
        DiscordServerError (5xx) before giving up - Discord's own
        infrastructure occasionally has brief outages that resolve within a
        second or two, so a short retry avoids aborting an entire session
        creation over what's usually just a passing blip on their end."""
        last_exc = None
        for attempt in range(retries + 1):
            try:
                return await coro_func(*args, **kwargs)
            except discord.DiscordServerError as e:
                last_exc = e
                if attempt < retries:
                    await asyncio.sleep(delay)
        raise last_exc

    async def _post_round(self, guild, session_id, round_no):
        state = self.active_sessions[session_id]
        matches = [m for m in db.get_matches_for_session(session_id) if m["round_no"] == round_no]
        control_channel = guild.get_channel(state["control_channel_id"])
        progress_channel = guild.get_channel(state["progress_channel_id"])

        # clear round divider so captains can tell fixtures apart at a glance
        await self._send_with_retry(control_channel.send, f"**━━━━━━━━━━ ROUND {round_no} ━━━━━━━━━━**")

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
            await self._send_with_retry(control_channel.send, embed=control_embed, view=view)

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
        progress_message = await self._send_with_retry(progress_channel.send, embeds=all_embeds, view=spectate_view)

        state["progress_round_message"] = {
            "message": progress_message,
            "embed_index": embed_index,   # match_id -> index INTO fixture embeds (add 1 for real embeds list index)
            "round_no": round_no,
        }

    def _round_result_line(self, club_a, score_a, club_b, score_b, a_won, went_to_pens=False):
        score_text = f"{score_a} - {score_b}" if score_a is not None else "final"
        pens_tag = " (Pens)" if went_to_pens else ""
        if a_won:
            return f"🟩 **{club_a}** 👑  {score_text}{pens_tag}  {club_b} 🟥"
        return f"🟥 {club_a}  {score_text}{pens_tag}  👑 **{club_b}** 🟩"

    async def _update_progress_field(self, session_id, match_id, club_a, club_b, score_a, score_b, a_won, went_to_pens=False):
        state = self.active_sessions.get(session_id)
        pr = state["progress_round_message"] if state else None
        if not pr or match_id not in pr["embed_index"]:
            return
        message = pr["message"]
        idx = pr["embed_index"][match_id] + 1  # +1 to skip the header embed
        try:
            embeds = list(message.embeds)
            fixture_embed = embeds[idx]
            fixture_embed.description = self._round_result_line(club_a, score_a, club_b, score_b, a_won, went_to_pens)
            fixture_embed.colour = discord.Color.green() if a_won else discord.Color.red()
            embeds[idx] = fixture_embed
            await message.edit(embeds=embeds)
        except (discord.HTTPException, IndexError):
            pass

    async def _mark_progress_field_live(self, session_id, match_id):
        """Updates a fixture's card in #in-progress from the generic
        "In progress" placeholder to an explicit LIVE tag, the moment a
        captain hits Match Live in session-control - lets spectators tell
        which specific games are actually being played right now versus
        ones that just haven't started yet."""
        state = self.active_sessions.get(session_id)
        pr = state["progress_round_message"] if state else None
        if not pr or match_id not in pr["embed_index"]:
            return
        message = pr["message"]
        idx = pr["embed_index"][match_id] + 1  # +1 to skip the header embed
        try:
            embeds = list(message.embeds)
            fixture_embed = embeds[idx]
            fixture_embed.description = "🔴 **LIVE**"
            embeds[idx] = fixture_embed
            await message.edit(embeds=embeds)
        except (discord.HTTPException, IndexError):
            pass

    # -------------------------------------------------------------- results
    async def finalize_match(self, interaction, match_id, winner_team_id, loser_team_id,
                              winner_score, loser_score, origin_view: MatchControlView, origin_message,
                              went_to_pens: bool = False):
        # Same reasoning as request_sub: several Discord API calls happen
        # below (button edit, progress card edit, leaderboard refresh)
        # before we'd otherwise respond - defer immediately so Discord's
        # 3-second window doesn't expire and show "didn't respond in time"
        # while the result is still being recorded.
        await interaction.response.defer(ephemeral=True)

        match = db.get_match(match_id)
        session_id = match["session_id"]
        state = self.active_sessions.get(session_id)
        if not state:
            await interaction.followup.send("This session has ended.", ephemeral=True)
            return

        if match["status"] == "reported":
            # Someone else already reported this exact match - most likely
            # a double-click, or two people (a captain and an admin) both
            # trying to report around the same time. Without this check,
            # finalize_match would happily run a second time and double the
            # win, the loss, and the MMR change for every player in it.
            await interaction.followup.send(
                "This match was already reported — refusing to record it a second time. "
                "If the result was wrong, use the admin correction commands to fix it instead.",
                ephemeral=True,
            )
            origin_view.btn_a.disabled = True
            origin_view.btn_b.disabled = True
            origin_view.btn_live.disabled = True
            try:
                await origin_message.edit(view=origin_view)
            except discord.HTTPException:
                pass
            return

        team_a_id, team_b_id = match["team_a_id"], match["team_b_id"]
        a_won = winner_team_id == team_a_id
        team_a_players = self._team_players_for_match(session_id, team_a_id, match_id, "team_a")
        team_b_players = self._team_players_for_match(session_id, team_b_id, match_id, "team_b")

        # Test sessions (/debug_test_session) skip every real-stat write -
        # no MMR, no win/loss counts, no match/club history - while still
        # running the full match-reporting flow (buttons, rounds, standings)
        # so the experience is otherwise identical to a real session.
        if not state["is_test"]:
            # individual player MMR only - clubs/teams never carry MMR themselves
            mmr_updates = mmr.apply_match_result(team_a_players, team_b_players, a_won)
            winner_ids = {p["discord_id"] for p in (team_a_players if a_won else team_b_players)}
            for discord_id, new_mmr in mmr_updates.items():
                db.update_mode_mmr(state["guild_id"], discord_id, state["mode"], new_mmr, won=discord_id in winner_ids)

        score_a = winner_score if a_won else loser_score
        score_b = loser_score if a_won else winner_score
        db.report_match_result(match_id, winner_team_id, score_a, score_b)

        club_a = state["teams"][team_a_id]["club_name"]
        club_b = state["teams"][team_b_id]["club_name"]
        if not state["is_test"]:
            db.record_match_participants(
                state["guild_id"], match_id, session_id, team_a_id, team_b_id,
                [p["discord_id"] for p in team_a_players], [p["discord_id"] for p in team_b_players],
                club_a, club_b, a_won, state["mode"],
            )

        winner_club = state["teams"][winner_team_id]["club_name"]

        # flip the control-room buttons: winner green + disabled, loser grey + disabled
        origin_view.btn_a.disabled = True
        origin_view.btn_b.disabled = True
        origin_view.btn_live.disabled = True
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
        await self._update_progress_field(session_id, match_id, club_a, club_b, score_a, score_b, a_won, went_to_pens)
        if not state["is_test"]:
            await leaderboard_utils.refresh_leaderboard_channel(self.bot, guild)

        pens_note = " (on penalties)" if went_to_pens else ""
        test_note = " *(test — not recorded)*" if state["is_test"] else ""
        await interaction.followup.send(f"Result recorded — {winner_club} win{pens_note} ✅{test_note}", ephemeral=True)

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
        winning_team_id = team_ids_ranked[0]

        # Small flat bonus for everyone who finished 1st, on top of whatever
        # they already earned from individual match results - skipped
        # entirely for test sessions, same as every other real-stat write.
        # Recording the winner for /session_history follows the same rule,
        # so test sessions never pollute the real history.
        if not state["is_test"]:
            winning_players = state["teams"][winning_team_id]["on_field"]
            for pid in winning_players:
                db.bump_mode_mmr(state["guild_id"], pid, state["mode"], config.SESSION_WIN_BONUS_MMR)
            db.set_session_winner(session_id, winning_team_id)

            cfg = db.get_guild_config(state["guild_id"])
            history_channel_id = cfg.get("history_channel_id") if cfg else None
            history_channel = guild.get_channel(history_channel_id) if history_channel_id else None
            if history_channel:
                winning_club = state["teams"][winning_team_id]["club_name"]
                winning_captain = state["teams"][winning_team_id]["captain_id"]
                mode_label = "🏆 League" if state["mode"] == "league" else "⚔️ Rivals"
                await history_channel.send(
                    f"**Session #{session_id}** — {mode_label} — **{winning_club}** won, captained by <@{winning_captain}>."
                )

        lines = []
        for i, team_id in enumerate(team_ids_ranked):
            club = state["teams"][team_id]["club_name"]
            captain = state["teams"][team_id]["captain_id"]
            wins = wins_by_team[team_id]
            losses = losses_by_team[team_id]
            gd = goals_for[team_id] - goals_against[team_id]
            gd_text = f"+{gd}" if gd > 0 else str(gd)
            medal = medals[i] if i < len(medals) else f"{i + 1}."
            bonus_note = ""
            if team_id == winning_team_id:
                bonus_note = " (test — no bonus applied)" if state["is_test"] else f" (+{config.SESSION_WIN_BONUS_MMR} MMR session bonus)"
            lines.append(
                f"{medal} **{club}** — {wins}W-{losses}L — GD {gd_text} "
                f"({goals_for[team_id]}-{goals_against[team_id]}) — Captain <@{captain}>{bonus_note}"
            )

        old_pr = state.get("progress_round_message")
        if old_pr and old_pr["message"]:
            try:
                await old_pr["message"].delete()
            except discord.HTTPException:
                pass

        title = "🏁 Test Session Complete — Final Standings" if state["is_test"] else "🏁 Session Complete — Final Standings"
        embed = discord.Embed(title=title, description="\n".join(lines), color=discord.Color.gold())
        footer = f"Test session — no MMR/stats were recorded. Voice channels close automatically in {config.SESSION_CLOSE_DELAY_SECONDS} seconds." if state["is_test"] else \
            f"All player MMR is already up to date. Voice channels close automatically in {config.SESSION_CLOSE_DELAY_SECONDS} seconds."
        embed.set_footer(text=footer)

        control_channel = guild.get_channel(state["control_channel_id"])
        progress_channel = guild.get_channel(state["progress_channel_id"])
        await control_channel.send(embed=embed)
        await progress_channel.send(embed=embed)
        if not state["is_test"]:
            await leaderboard_utils.refresh_leaderboard_channel(self.bot, guild)

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
        # Acknowledge the button press IMMEDIATELY, before any of the slower
        # Discord API calls below (channel edit, permission grant, member
        # move) - otherwise Discord's 3-second interaction window can expire
        # while we're still working, showing "didn't respond in time" even
        # though the sub itself may still go through in the background.
        await interaction.response.defer(ephemeral=True)

        state = self.active_sessions.get(session_id)
        if not state:
            await interaction.followup.send("This session has ended.", ephemeral=True)
            return

        captain_id = state["teams"][team_id]["captain_id"]
        is_admin = interaction.user.guild_permissions.manage_guild
        if interaction.user.id != captain_id and not is_admin:
            await interaction.followup.send(f"Only {club_name}'s captain can request a sub.", ephemeral=True)
            return

        # Hard cap: a team can take exactly ONE sub beyond the normal 6
        # (7 max) - never more, no matter how many times Add Sub is clicked.
        if len(state["teams"][team_id]["on_field"]) >= config.TEAM_SIZE + 1:
            await interaction.followup.send(
                f"{club_name} is already at its maximum of {config.TEAM_SIZE + 1} players — it's already used its one sub slot.",
                ephemeral=True,
            )
            return

        async with state["sub_lock"]:
            # re-fetch state in case the session ended while we were waiting on the lock
            state = self.active_sessions.get(session_id)
            if not state:
                await interaction.followup.send("This session has ended.", ephemeral=True)
                return

            guild = interaction.guild
            bench_channel = guild.get_channel(state["bench_channel_id"])
            # Only exclude people already on THIS team - someone active on a
            # DIFFERENT team is a valid candidate too, they just get properly
            # transferred off that team as part of the sub (see below), so a
            # player can voluntarily bench themselves and get pulled onto a
            # new team without ever being double-booked on two teams at once.
            candidates = [m for m in bench_channel.members if m.id not in state["teams"][team_id]["on_field"]]
            if not candidates:
                your_voice = interaction.user.voice.channel.mention if interaction.user.voice and interaction.user.voice.channel else "not connected to any voice channel"
                bench_occupants = ", ".join(m.display_name for m in bench_channel.members) if bench_channel.members else "empty"
                await interaction.followup.send(
                    f"Nobody eligible is in {bench_channel.mention} right now.\n"
                    f"Debug info — the bot currently sees you in: **{your_voice}**. "
                    f"{bench_channel.mention} occupants: **{bench_occupants}**.",
                    ephemeral=True,
                )
                return

            guild_id = state["guild_id"]

            # If this team already has NA players, prefer pulling another NA
            # player from the Bench too, to keep them grouped together even
            # through subs - only falls back to the normal candidate pool if
            # there's no NA player currently available to sub in.
            team_has_na = any(
                (db.get_player(guild_id, pid) or {}).get("is_na") for pid in state["teams"][team_id]["on_field"]
            )
            if team_has_na:
                na_candidates = [m for m in candidates if (db.get_player(guild_id, m.id) or {}).get("is_na")]
                if na_candidates:
                    candidates = na_candidates

            # Pick whichever Bench candidate best BALANCES the requesting
            # team, rather than always the strongest player: work out the
            # whole session's overall average MMR as a fairness benchmark,
            # then choose whoever would bring this team's own average
            # closest to that benchmark. A team sitting below the overall
            # average naturally gets pulled toward a higher-MMR candidate;
            # a team sitting above it gets pulled toward a lower-MMR one.
            # All MMR here is specific to this session's mode.
            all_active_ids = set()
            for t in state["teams"].values():
                all_active_ids |= t["on_field"]
            all_active_mmrs = [db.get_player_mode_stats(guild_id, pid, state["mode"])["mmr"] for pid in all_active_ids]
            overall_avg = (
                sum(all_active_mmrs) / len(all_active_mmrs)
                if all_active_mmrs else config.STARTING_MMR
            )

            target_team_mmrs = [db.get_player_mode_stats(guild_id, pid, state["mode"])["mmr"] for pid in state["teams"][team_id]["on_field"]]
            current_total = sum(target_team_mmrs)
            current_count = len(target_team_mmrs)

            candidate_mmr = {m.id: db.get_player_mode_stats(guild_id, m.id, state["mode"])["mmr"] for m in candidates}

            def resulting_avg(mmr):
                return (current_total + mmr) / (current_count + 1)

            incoming = min(candidates, key=lambda m: abs(resulting_avg(candidate_mmr[m.id]) - overall_avg))
            team_channel = guild.get_channel(state["teams"][team_id]["voice_channel_id"])

            # If they're currently active on a DIFFERENT team, transfer them
            # off it first - a player can only ever be registered to one
            # team's roster at a time. Shrink that team's VC back down by one
            # at the same time, so a transfer is capacity-neutral overall
            # (one channel +1, the other -1) instead of permanently inflating
            # every team you've ever passed through.
            for other_team_id, other_info in state["teams"].items():
                if other_team_id != team_id and incoming.id in other_info["on_field"]:
                    other_info["on_field"].discard(incoming.id)
                    db.set_member_role(other_team_id, incoming.id, "sub")
                    other_channel = guild.get_channel(other_info["voice_channel_id"])
                    if other_channel and other_channel.user_limit > config.TEAM_SIZE:
                        try:
                            await other_channel.edit(user_limit=other_channel.user_limit - 1)
                        except discord.HTTPException:
                            pass
                    break

            # Subs otherwise ADD to the roster rather than swapping anyone
            # else out - so a sub can legitimately push a team past the
            # normal 6 (e.g. 6 -> 7). Raise the VC's user_limit by one first,
            # or Discord will refuse to move them into an already-full channel.
            try:
                await team_channel.edit(user_limit=team_channel.user_limit + 1)
            except discord.HTTPException:
                pass

            await voice_utils.allow_member_in_channel(team_channel, incoming, connect=True)
            moved, move_fail_reason = await voice_utils.move_member_to_channel(guild, incoming.id, team_channel)

            # Bench's extra capacity (if any) was only ever reserved to cover
            # a force-start deficiency - now that this seat's been used to
            # pull someone onto a team, give it back, same as any other
            # capacity-neutral transfer. Never shrinks below the normal
            # BENCH_SIZE floor.
            if bench_channel and bench_channel.user_limit > config.BENCH_SIZE:
                try:
                    await bench_channel.edit(user_limit=bench_channel.user_limit - 1)
                except discord.HTTPException:
                    pass

            state["teams"][team_id]["on_field"].add(incoming.id)
            db.add_team_member(team_id, incoming.id, "player")

            # Now that they're rostered onto a team, lock them out of Bench
            # too - once assigned, a player can't voluntarily bench
            # themselves to get poached by yet another team.
            bench_channel = guild.get_channel(state["bench_channel_id"])
            if bench_channel:
                await voice_utils.allow_member_in_channel(bench_channel, incoming, connect=False)

        progress_channel = guild.get_channel(state["progress_channel_id"])
        await progress_channel.send(f"🔁 <@{incoming.id}> joins **{club_name}** from the bench.")
        await self._refresh_team_roster(session_id)

        if moved:
            await interaction.followup.send(f"<@{incoming.id}> has been moved onto {club_name}.", ephemeral=True)
        else:
            await interaction.followup.send(
                f"<@{incoming.id}> has been added to {club_name}'s roster, but I couldn't physically drag "
                f"them there: **{move_fail_reason}**. Ask them to rejoin the Bench VC and try again, or move "
                f"them manually.",
                ephemeral=True,
            )

    # -------------------------------------------------------------- manual transfer
    async def transfer_player(self, guild: discord.Guild, session_id: int, state: dict, player_id: int, dest_team_id: int):
        """Moves player_id directly onto dest_team_id's roster, removing
        them from wherever they're currently rostered (if anywhere) first.
        Unlike request_sub, this doesn't require them to be on the Bench -
        it's for the case where a player is already active on one team but
        needs to move straight onto a different one. Shares the same
        capacity-neutral VC math and Bench-lock enforcement as a sub.
        Returns (added: bool, moved: bool, reason: str | None) - added is
        False only if the hard cap blocked the transfer entirely (nothing
        changed); moved reflects whether the physical voice drag succeeded
        given the roster change did go through."""
        # Same hard cap as Add Sub - a team can hold at most one sub beyond
        # the normal 6 (7 max). Check this BEFORE touching anything, so a
        # blocked transfer doesn't still rip the player off their old team.
        if len(state["teams"][dest_team_id]["on_field"]) >= config.TEAM_SIZE + 1:
            dest_club = state["teams"][dest_team_id]["club_name"]
            return False, False, f"{dest_club} is already at its maximum of {config.TEAM_SIZE + 1} players"

        # remove from any other team they're currently on, shrinking that
        # team's VC back down (never below the normal TEAM_SIZE floor)
        stripped_captaincy_of = None
        for other_team_id, other_info in state["teams"].items():
            if other_team_id != dest_team_id and player_id in other_info["on_field"]:
                other_info["on_field"].discard(player_id)
                db.set_member_role(other_team_id, player_id, "sub")
                other_channel = guild.get_channel(other_info["voice_channel_id"])
                if other_channel and other_channel.user_limit > config.TEAM_SIZE:
                    try:
                        await other_channel.edit(user_limit=other_channel.user_limit - 1)
                    except discord.HTTPException:
                        pass

                # A captain who transfers away loses that team's captaincy -
                # they're no longer even rostered there, so it shouldn't
                # stay pointed at them. Admins are exempt: they already see
                # and can act on session-control regardless of captain
                # status, so there's nothing to strip for them functionally.
                if other_info["captain_id"] == player_id:
                    transferred_member = guild.get_member(player_id)
                    is_admin_player = bool(transferred_member and transferred_member.guild_permissions.manage_guild)
                    if not is_admin_player:
                        other_info["captain_id"] = None
                        stripped_captaincy_of = other_info["club_name"]
                break

        dest_channel = guild.get_channel(state["teams"][dest_team_id]["voice_channel_id"])
        try:
            await dest_channel.edit(user_limit=dest_channel.user_limit + 1)
        except discord.HTTPException:
            pass

        member = guild.get_member(player_id)
        moved, reason = False, "that user isn't in the bot's member cache for this server"
        if member:
            await voice_utils.allow_member_in_channel(dest_channel, member, connect=True)
            moved, reason = await voice_utils.move_member_to_channel(guild, player_id, dest_channel)

        state["teams"][dest_team_id]["on_field"].add(player_id)
        db.add_team_member(dest_team_id, player_id, "player")

        # lock them out of Bench now that they're rostered on their new team
        bench_channel = guild.get_channel(state["bench_channel_id"])
        if bench_channel and member:
            await voice_utils.allow_member_in_channel(bench_channel, member, connect=False)

        club_name = state["teams"][dest_team_id]["club_name"]
        progress_channel = guild.get_channel(state["progress_channel_id"])
        if progress_channel:
            note = f" ⚠️ They were {stripped_captaincy_of}'s captain — that team needs a new one via Reassign Captain." if stripped_captaincy_of else ""
            await progress_channel.send(f"🔄 <@{player_id}> has been transferred to **{club_name}**.{note}")
        await self._refresh_team_roster(session_id)

        return True, moved, reason

    # -------------------------------------------------------------- captain reassignment
    async def reassign_captain(self, guild: discord.Guild, session_id: int, team_id: int, new_captain_id: int):
        """Hands captain status to someone else already on that team's
        roster - grants them the same Move Members access every captain has
        across ALL team VCs and the Bench, plus view/send on session-control.
        The outgoing captain's access is left as-is rather than revoked, so
        nothing risks breaking mid-session over a permission removal - the
        only thing that actually changes for match-reporting purposes is
        which captain_id is authorized to report results for this team."""
        state = self.active_sessions[session_id]
        old_captain_id = state["teams"][team_id]["captain_id"]
        state["teams"][team_id]["captain_id"] = new_captain_id
        db.set_captain(state["guild_id"], new_captain_id, True)

        new_captain = guild.get_member(new_captain_id)
        if new_captain:
            for other_info in state["teams"].values():
                team_channel = guild.get_channel(other_info["voice_channel_id"])
                if team_channel:
                    await voice_utils.allow_member_in_channel(team_channel, new_captain, connect=True)
                    try:
                        await team_channel.set_permissions(new_captain, view_channel=True, connect=True, move_members=True)
                    except discord.HTTPException:
                        pass
            bench_channel = guild.get_channel(state["bench_channel_id"])
            if bench_channel:
                try:
                    await bench_channel.set_permissions(new_captain, view_channel=True, connect=True, move_members=True)
                except discord.HTTPException:
                    pass
            control_channel = guild.get_channel(state["control_channel_id"])
            if control_channel:
                try:
                    await control_channel.set_permissions(new_captain, view_channel=True, send_messages=True)
                except discord.HTTPException:
                    pass

        club_name = state["teams"][team_id]["club_name"]
        progress_channel = guild.get_channel(state["progress_channel_id"])
        if progress_channel:
            await progress_channel.send(f"🎖️ <@{new_captain_id}> is now captaining **{club_name}** (previously <@{old_captain_id}>).")
        await self._refresh_team_roster(session_id)

    # -------------------------------------------------------------- full removal
    async def remove_player_from_roster(self, guild: discord.Guild, session_id: int, team_id: int, player_id: int):
        """Pulls a player off their team's roster entirely - not benched,
        not transferred, just gone. Frees their seat (shrinking the team's
        VC back down, same as any other departure) so someone else can
        actually be subbed/transferred in without hitting the 7-player cap.
        Unlike a transfer, captaincy is ALWAYS cleared here (no admin
        exemption) since they're not on any team anymore at all. Their
        Bench lock is lifted too, so if they come back later they can
        rejoin normally through Bench instead of needing another manual fix."""
        state = self.active_sessions[session_id]
        info = state["teams"][team_id]
        info["on_field"].discard(player_id)
        db.set_member_role(team_id, player_id, "removed")

        channel = guild.get_channel(info["voice_channel_id"])
        if channel and channel.user_limit > config.TEAM_SIZE:
            try:
                await channel.edit(user_limit=channel.user_limit - 1)
            except discord.HTTPException:
                pass

        was_captain = info["captain_id"] == player_id
        if was_captain:
            info["captain_id"] = None

        member = guild.get_member(player_id)
        bench_channel = guild.get_channel(state["bench_channel_id"])
        if bench_channel and member:
            await voice_utils.allow_member_in_channel(bench_channel, member, connect=True)

        club_name = info["club_name"]
        progress_channel = guild.get_channel(state["progress_channel_id"])
        if progress_channel:
            note = f" ⚠️ They were {club_name}'s captain — that team needs a new one via Reassign Captain." if was_captain else ""
            await progress_channel.send(f"❌ <@{player_id}> has been removed from **{club_name}**'s roster entirely.{note}")
        await self._refresh_team_roster(session_id)

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

        # lift any lingering spectator mutes before the channels disappear -
        # a server mute is a per-member flag independent of the channel, so
        # deleting the VC doesn't clear it on its own
        for spectator_id in list(state["spectators"].keys()):
            await voice_utils.set_spectator_mute(guild, spectator_id, False)
        state["spectators"].clear()

        if not natural_completion:
            announce_channel = guild.get_channel(state["announce_channel_id"])
            if announce_channel:
                who = ended_by.mention if ended_by else "the system"
                lines = [f"**{info['club_name']}** — Captain {_captain_display(info['captain_id'])}" for info in state["teams"].values()]
                await announce_channel.send(f"🔴 **NTF session ended** by {who}.\n" + "\n".join(lines))

        # Voice infrastructure disappears right away either way - that's the
        # actual "ending" of the session. The permanent text channels get
        # cleaned up separately below, with a grace period so people can
        # still read what happened before it disappears.
        category = guild.get_channel(state["category_id"])
        if category:
            await voice_utils.teardown_session_category(guild, category)

        progress_channel_id = state["progress_channel_id"]
        guild_id = state["guild_id"]

        db.end_session(session_id)
        del self.active_sessions[session_id]

        # Natural completion already waited 60s (via _auto_close_after_delay)
        # BEFORE calling this method, so the grace period has already
        # happened - clean up immediately. A manual/early end hasn't had any
        # grace period yet, so give it the same 60 seconds here instead,
        # as a background task so this method (and whatever button/command
        # called it) can return right away rather than blocking on the wait.
        delay = 0 if natural_completion else config.SESSION_CLOSE_DELAY_SECONDS
        asyncio.create_task(self._finalize_channel_cleanup(guild_id, progress_channel_id, delay))

    async def _finalize_channel_cleanup(self, guild_id: int, progress_channel_id: int, delay: int):
        if delay:
            await asyncio.sleep(delay)
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return

        progress_channel = guild.get_channel(progress_channel_id)
        if progress_channel:
            try:
                while True:
                    deleted = await progress_channel.purge(limit=100)
                    if len(deleted) < 100:
                        break
            except discord.HTTPException:
                pass
            await progress_channel.send("🚫 No games are currently in progress.")

        queue_cog = self.bot.get_cog("QueueCog")
        if queue_cog:
            await queue_cog.reset_channel(guild)

        # Refresh the leaderboard one more time right alongside the other
        # two channels resetting - it's already kept live after every match
        # and the session-win bonus, but this guarantees all three permanent
        # channels are certainly in sync at this exact moment, regardless of
        # anything else (manual MMR corrections, etc.) that happened in the
        # meantime.
        await leaderboard_utils.refresh_leaderboard_channel(self.bot, guild)

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

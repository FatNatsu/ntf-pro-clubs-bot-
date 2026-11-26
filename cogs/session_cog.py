import asyncio
import random
import time

import discord
from discord import app_commands
from discord.ext import commands

import config
import database as db
import mmr
import team_balance
import voice_utils
import leaderboard_utils


def _ordinal(n: int) -> str:
    """7 -> '7th'. Used so messages follow the mode's team cap if it ever changes."""
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def _cap(state) -> int:
    """Hard ceiling per team for this session's mode (League 7, Rivals 11)."""
    return config.team_cap(state["mode"])


def _normal(state) -> int:
    """The size Add sub fills a team to in this session's mode."""
    return config.normal_team_size(state["mode"])


def _team_channel_name(state, team_id) -> str:
    """A team's voice channel is named after its club, with a marker added
    while the team is at its cap."""
    info = state["teams"][team_id]
    if len(info["on_field"]) >= _cap(state):
        return f"{info['club_name']} - {config.TEAM_FULL_SUFFIX}"[:100]
    return info["club_name"]


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
    """Red win buttons for one match, in that game's captains channel. Flips the winner
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
        self.btn_undo = discord.ui.Button(label="Undo Result (Admin)", style=discord.ButtonStyle.secondary, emoji="↩️", row=2)
        self.btn_a.callback = self._make_callback(team_a_id, team_a_club, team_b_id, team_b_club)
        self.btn_b.callback = self._make_callback(team_b_id, team_b_club, team_a_id, team_a_club)
        self.btn_pens.callback = self._make_pens_callback()
        self.btn_live.callback = self._make_live_callback()
        self.btn_undo.callback = self._make_undo_callback()
        self.add_item(self.btn_a)
        self.add_item(self.btn_b)
        self.add_item(self.btn_pens)
        self.add_item(self.btn_live)
        self.add_item(self.btn_undo)

    def _make_undo_callback(self):
        async def callback(interaction: discord.Interaction):
            if not interaction.user.guild_permissions.manage_guild:
                await interaction.response.send_message("Only an admin can undo a match result.", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True)
            affected = db.undo_match_result(self.match_id)
            if affected is None:
                await interaction.followup.send("This match hasn't been reported yet — nothing to undo.", ephemeral=True)
                return

            # re-enable the win buttons so it can be correctly re-reported
            self.btn_a.disabled = False
            self.btn_b.disabled = False
            self.btn_a.style = discord.ButtonStyle.danger
            self.btn_b.style = discord.ButtonStyle.danger
            self.btn_a.emoji = "🔴"
            self.btn_b.emoji = "🔴"
            self.btn_live.disabled = False
            try:
                await interaction.message.edit(view=self)
            except discord.HTTPException:
                pass

            state = self.cog.active_sessions.get(self.session_id)
            if state:
                await self.cog._update_progress_field_reset(self.session_id, self.match_id, self.team_a_club, self.team_b_club)
                await self.cog._refresh_status(self.session_id)
                await self.cog._log_action(
                    self.session_id,
                    f"↩️ <@{interaction.user.id}> undid the result for **{self.team_a_club}** vs **{self.team_b_club}** "
                    f"— {len(affected)} player(s) reverted. Ready to be re-reported correctly.",
                )
            await interaction.followup.send(
                f"✅ Undone — {len(affected)} player(s) had their win/loss and MMR reverted exactly. "
                f"You can now click the correct winner.",
                ephemeral=True,
            )
        return callback

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
            await self.cog._refresh_status(self.session_id)
            await self.cog._log_action(
                self.session_id,
                f"▶️ <@{interaction.user.id}> marked **{self.team_a_club}** vs **{self.team_b_club}** as LIVE.",
            )
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
            if player_id not in info["on_field"]
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
        if not self.cog.can_manage_player(interaction, state, self.player_id):
            await interaction.followup.send(
                "You can only transfer players from the team you captain.", ephemeral=True
            )
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
        if added:
            await self.cog._log_action(
                self.session_id,
                f"🔄 <@{interaction.user.id}> transferred <@{self.player_id}> to **{club_name}**.",
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
        await self.cog._log_action(
            self.session_id,
            f"🎖️ <@{interaction.user.id}> reassigned **{club_name}**'s captaincy to <@{new_captain_id}>.",
        )


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

    def __init__(self, cog: "SessionCog", session_id, only_team_id=None):
        super().__init__(timeout=60)
        self.cog = cog
        self.session_id = session_id
        state = cog.active_sessions[session_id]

        options = []
        for team_id, info in state["teams"].items():
            if only_team_id is not None and team_id != only_team_id:
                continue
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
        if not self.cog.can_manage_player(interaction, state, player_id):
            await interaction.followup.send(
                "You can only remove players from the team you captain.", ephemeral=True
            )
            return
        club_name = state["teams"][team_id]["club_name"]
        await self.cog.remove_player_from_roster(interaction.guild, self.session_id, team_id, player_id)
        await interaction.followup.send(f"❌ Removed <@{player_id}> from **{club_name}**'s roster. That slot is now free.", ephemeral=True)
        await self.cog._log_action(
            self.session_id,
            f"❌ <@{interaction.user.id}> removed <@{player_id}> from **{club_name}**'s roster entirely.",
        )


class TransferPlayerSelectView(discord.ui.View):
    """First step of a manual transfer - pick which currently-rostered
    player to move. Only lists players actively on a team, not fake/test
    accounts or people the bot can't resolve to a real member."""

    def __init__(self, cog: "SessionCog", session_id, only_team_id=None):
        super().__init__(timeout=60)
        self.cog = cog
        self.session_id = session_id
        state = cog.active_sessions[session_id]

        options = []
        for team_id, info in state["teams"].items():
            if only_team_id is not None and team_id != only_team_id:
                continue
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


class CaptainPanelView(discord.ui.View):
    """The roster controls for ONE game, posted in that game's captains-only
    channel - so the two captains playing each other have their own panel,
    separate from the other game going on at the same time.

    Every button acts on the team the person pressing it captains:
    - Add sub: pulls the next player from the Bench (the session-wide turn
      order still decides whose turn it is).
    - Request Nth player: League only - the last-resort seat above the
      normal team size.
    - Transfer Player: move one of YOUR OWN players onto any other team,
      including a team in the other game.
    - Remove Player: take one of YOUR OWN players off the roster.
    Admins who aren't captaining use the admin panel in the admin log."""

    def __init__(self, cog: "SessionCog", session_id, mode):
        super().__init__(timeout=None)
        self.cog = cog
        self.session_id = session_id
        self.mode = mode

        self.sub_button = discord.ui.Button(label="Add sub", style=discord.ButtonStyle.success, emoji="🔁")
        self.sub_button.callback = self._sub_callback
        self.add_item(self.sub_button)

        if config.team_cap(mode) > config.normal_team_size(mode):
            extra_btn = discord.ui.Button(
                label=f"Request {_ordinal(config.team_cap(mode))} player", style=discord.ButtonStyle.secondary, emoji="➕"
            )
            extra_btn.callback = self._extra_callback
            self.add_item(extra_btn)

        transfer_btn = discord.ui.Button(label="Transfer Player", style=discord.ButtonStyle.primary, emoji="🔄")
        transfer_btn.callback = self._transfer_callback
        self.add_item(transfer_btn)

        remove_btn = discord.ui.Button(label="Remove Player", style=discord.ButtonStyle.danger, emoji="❌")
        remove_btn.callback = self._remove_callback
        self.add_item(remove_btn)

    def sync_sub_buttons(self, teams, turn_team_id=None):
        """Shows whose turn it is on the Add sub button (the turn order runs
        across the whole session, both games). Greyed out when no team can
        take a sub."""
        if turn_team_id is None:
            self.sub_button.disabled = True
            self.sub_button.label = "No sub available"
            self.sub_button.style = discord.ButtonStyle.secondary
        else:
            self.sub_button.disabled = False
            self.sub_button.label = f"Add sub (next: {teams[turn_team_id]['club_name']})"[:80]
            self.sub_button.style = discord.ButtonStyle.success

    async def _own_team(self, interaction: discord.Interaction, action: str):
        """Returns (state, team_id) for the team the presser captains, or
        (None, None) after telling them why they can't."""
        state = self.cog.active_sessions.get(self.session_id)
        if not state:
            await interaction.response.send_message("This session has ended.", ephemeral=True)
            return None, None
        team_id = self.cog.captained_team(state, interaction.user.id)
        if team_id is None:
            if interaction.user.guild_permissions.manage_guild:
                msg = f"You aren't captaining a team — use the admin controls in the admin log to {action}."
            else:
                msg = f"Only a team's captain can {action}."
            await interaction.response.send_message(msg, ephemeral=True)
            return None, None
        return state, team_id

    async def _sub_callback(self, interaction: discord.Interaction):
        state, team_id = await self._own_team(interaction, "request a sub")
        if team_id is None:
            return
        await self.cog.request_sub(interaction, self.session_id, team_id, state["teams"][team_id]["club_name"])

    async def _extra_callback(self, interaction: discord.Interaction):
        state, team_id = await self._own_team(interaction, "request an extra player")
        if team_id is None:
            return
        await self.cog.request_sub(
            interaction, self.session_id, team_id, state["teams"][team_id]["club_name"], extra=True
        )

    async def _transfer_callback(self, interaction: discord.Interaction):
        state, team_id = await self._own_team(interaction, "transfer a player")
        if team_id is None:
            return
        view = TransferPlayerSelectView(self.cog, self.session_id, only_team_id=team_id)
        if not view.children:
            await interaction.response.send_message("Nobody is on your team to transfer.", ephemeral=True)
            return
        await interaction.response.send_message("Which of your players do you want to transfer?", view=view, ephemeral=True)

    async def _remove_callback(self, interaction: discord.Interaction):
        state, team_id = await self._own_team(interaction, "remove a player")
        if team_id is None:
            return
        view = RemovePlayerSelectView(self.cog, self.session_id, only_team_id=team_id)
        if not view.children:
            await interaction.response.send_message("Nobody is on your team to remove.", ephemeral=True)
            return
        await interaction.response.send_message("Remove which of your players from the team?", view=view, ephemeral=True)


class AdminPanelView(discord.ui.View):
    """Admin-only controls for one session, posted in the admin log when the
    session starts and removed when it ends. Everything here works on ANY
    team, and ending the session lives only here (captains can't end it)."""

    def __init__(self, cog: "SessionCog", session_id):
        super().__init__(timeout=None)
        self.cog = cog
        self.session_id = session_id
        for label, style, emoji, callback in [
            ("Pull Sub For Team", discord.ButtonStyle.secondary, "🛠️", self._pull_callback),
            ("Transfer Player", discord.ButtonStyle.primary, "🔄", self._transfer_callback),
            ("Remove Player", discord.ButtonStyle.danger, "❌", self._remove_callback),
            ("Reassign Captain", discord.ButtonStyle.primary, "🎖️", self._reassign_callback),
            ("End Session", discord.ButtonStyle.danger, "🛑", self._end_callback),
        ]:
            btn = discord.ui.Button(label=label, style=style, emoji=emoji)
            btn.callback = callback
            self.add_item(btn)

    async def _state(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("Only an admin can use these controls.", ephemeral=True)
            return None
        state = self.cog.active_sessions.get(self.session_id)
        if not state:
            await interaction.response.send_message("This session has ended.", ephemeral=True)
        return state

    async def _pull_callback(self, interaction: discord.Interaction):
        state = await self._state(interaction)
        if not state:
            return
        options = [
            discord.SelectOption(
                label=f"{info['club_name']} ({len(info['on_field'])})", value=str(team_id)
            )
            for team_id, info in state["teams"].items() if len(info["on_field"]) < _cap(state)
        ]
        if not options:
            await interaction.response.send_message("Every team is already at its cap.", ephemeral=True)
            return
        view = discord.ui.View(timeout=60)
        select = discord.ui.Select(placeholder="Pull a sub for which team?", options=options)

        async def on_select(select_interaction: discord.Interaction):
            team_id = int(select.values[0])
            current = self.cog.active_sessions.get(self.session_id)
            if not current:
                await select_interaction.response.send_message("This session has ended.", ephemeral=True)
                return
            # a team already at its normal size is asking for the last-resort seat
            extra = len(current["teams"][team_id]["on_field"]) >= _normal(current)
            await self.cog.request_sub(
                select_interaction, self.session_id, team_id, current["teams"][team_id]["club_name"], extra=extra
            )

        select.callback = on_select
        view.add_item(select)
        await interaction.response.send_message("Pick the team to pull a sub for:", view=view, ephemeral=True)

    async def _transfer_callback(self, interaction: discord.Interaction):
        state = await self._state(interaction)
        if not state:
            return
        view = TransferPlayerSelectView(self.cog, self.session_id)
        if not view.children:
            await interaction.response.send_message("Nobody is currently rostered to transfer.", ephemeral=True)
            return
        await interaction.response.send_message("Who do you want to transfer?", view=view, ephemeral=True)

    async def _remove_callback(self, interaction: discord.Interaction):
        state = await self._state(interaction)
        if not state:
            return
        view = RemovePlayerSelectView(self.cog, self.session_id)
        if not view.children:
            await interaction.response.send_message("Nobody is currently rostered to remove.", ephemeral=True)
            return
        await interaction.response.send_message("Remove who from their team entirely?", view=view, ephemeral=True)

    async def _reassign_callback(self, interaction: discord.Interaction):
        state = await self._state(interaction)
        if not state:
            return
        view = ReassignCaptainTeamSelectView(self.cog, self.session_id)
        await interaction.response.send_message("Which team needs a new captain?", view=view, ephemeral=True)

    async def _end_callback(self, interaction: discord.Interaction):
        state = await self._state(interaction)
        if not state:
            return
        await interaction.response.send_message(
            "End this session for everyone?", view=ConfirmEndView(self.cog, self.session_id), ephemeral=True
        )


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
    """Attached to a game's public match channel - one Watch button per team in that game."""

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

                # A spectator borrows one seat on the channel's displayed
                # capacity (it never actually blocks the move - the bot's own
                # Move Members bypasses the cap - but this keeps the number
                # honest). The limit is always DERIVED from who's actually
                # watching (the team cap + current spectators on that channel)
                # rather than nudged up and down by one, so a double-click,
                # a failed move, or hopping straight between Watch buttons
                # can never make it drift past the cap.
                previous_channel_id = state["spectators"].get(member.id)
                already_here = previous_channel_id == channel.id
                await self.cog._sync_spectator_capacity(
                    interaction.guild, state, channel.id, extra=0 if already_here else 1
                )
                moved, reason = await voice_utils.move_member_to_channel(interaction.guild, member.id, channel)
                if moved:
                    state["spectators"][member.id] = channel.id
                    await voice_utils.set_spectator_mute(interaction.guild, member.id, True)
                    if previous_channel_id is not None and previous_channel_id != channel.id:
                        # switched straight from another team's Watch button
                        await self.cog._sync_spectator_capacity(interaction.guild, state, previous_channel_id)
                # settle at the exact count either way - if the move failed
                # this also takes back the seat borrowed above
                await self.cog._sync_spectator_capacity(interaction.guild, state, channel.id)

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
        for session_id, state in list(self.active_sessions.items()):
            if state["guild_id"] != member.guild.id:
                continue

            # Bench wait tracking: note when someone enters the Bench VC (that
            # starts their place in the sub queue) and forget them when they
            # leave - so someone who leaves and comes back goes to the back.
            # Plain dict updates, no awaits, so no lock needed.
            before_id = before.channel.id if before.channel else None
            after_id = after.channel.id if after.channel else None
            if before_id != after_id and not getattr(member, "bot", False):
                if after_id == state["bench_channel_id"]:
                    self._bench_register(state, member.id)
                    self._schedule_sub_queue_refresh(session_id)
                elif before_id == state["bench_channel_id"]:
                    state["bench_since"].pop(member.id, None)
                    self._schedule_sub_queue_refresh(session_id)

            team_channel_ids = {info["voice_channel_id"] for info in state["teams"].values()}
            async with state["spectate_lock"]:
                watching_channel_id = state["spectators"].get(member.id)
                after_channel_id = after.channel.id if after.channel else None
                if watching_channel_id is not None and after_channel_id != watching_channel_id:
                    await voice_utils.set_spectator_mute(member.guild, member.id, False)
                    del state["spectators"][member.id]
                    # Give back the seat the Watch button borrowed - recomputed
                    # from who's still watching, so it settles at exactly
                    # the team cap (+ anyone still watching), never below.
                    await self._sync_spectator_capacity(member.guild, state, watching_channel_id)
                # Anyone moving in or out of a team channel can change how
                # many extra seats it needs (an admin dropping in or leaving),
                # so settle both channels involved at their exact number.
                if before_id != after_id:
                    for channel_id in (before_id, after_id):
                        if channel_id in team_channel_ids and channel_id != watching_channel_id:
                            await self._sync_spectator_capacity(member.guild, state, channel_id)

    # ------------------------------------------------------ sub turn + queue
    def _sub_turn_order(self, state):
        """Teams that can take a sub right now, in the order they'll get to
        pull one: fewest players first, ties broken by a FIXED team order
        (the order the teams were created in) - so as teams fill up it
        cycles A -> B -> C -> D -> A rather than jumping around. Teams already
        at the mode's normal size are left out (any seat above that is only
        ever filled on an explicit request, never by turn), as are teams
        with no captain (nobody there could press the button - an admin can
        still pull for them)."""
        order = []
        for idx, (team_id, info) in enumerate(state["teams"].items()):
            if len(info["on_field"]) >= _normal(state):
                continue
            if info["captain_id"] is None:
                continue
            order.append((len(info["on_field"]), idx, team_id))
        order.sort()
        return [team_id for _n, _idx, team_id in order]

    def _sub_turn_team(self, state):
        order = self._sub_turn_order(state)
        return order[0] if order else None

    def _bench_register(self, state, user_id):
        """Records when someone entered the Bench VC (once - re-registering
        someone already waiting keeps their original place in line)."""
        if user_id not in state["bench_since"]:
            state["bench_seq"] += 1
            state["bench_since"][user_id] = {"seq": state["bench_seq"], "at": time.time(), "skips": 0}

    def _bench_waiting(self, state, guild):
        """Members currently sitting in the Bench VC, longest-waiting first.
        Works from who's actually in the channel right now: anyone we
        somehow missed leaving is dropped, and anyone already in there
        before we started tracking is slotted in (in the order Discord lists
        them) rather than ignored."""
        bench_channel = guild.get_channel(state["bench_channel_id"])
        members = [m for m in (bench_channel.members if bench_channel else []) if not getattr(m, "bot", False)]
        present = {m.id for m in members}
        for user_id in [u for u in state["bench_since"] if u not in present]:
            del state["bench_since"][user_id]
        for m in members:
            self._bench_register(state, m.id)
        members.sort(key=lambda m: state["bench_since"][m.id]["seq"])
        return members

    def _sub_candidates(self, state, guild, team_id):
        """Everyone in the Bench who could be pulled onto team_id, longest
        waiting first (anyone already on that team is excluded; someone on a
        DIFFERENT team is still fair game and gets properly transferred). A
        team that already has NA players sees only the NA players waiting, if
        there are any, so NA players stay grouped through subs."""
        on_team = state["teams"][team_id]["on_field"]
        candidates = [m for m in self._bench_waiting(state, guild) if m.id not in on_team]
        guild_id = state["guild_id"]
        if candidates and any((db.get_player(guild_id, pid) or {}).get("is_na") for pid in on_team):
            na_only = [m for m in candidates if (db.get_player(guild_id, m.id) or {}).get("is_na")]
            if na_only:
                candidates = na_only
        return candidates

    def _pick_sub(self, state, guild, team_id, candidates=None):
        """Chooses who team_id would get from the Bench right now.
        Returns (pick, pool, forced): pick is the chosen member (None if
        nobody's waiting), pool is the list of longest-waiting candidates it
        was chosen from, and forced is True when the pick was made purely
        because they'd been passed over too many times.

        The pool is the config.SUB_PICK_WINDOW longest-waiting candidates.
        Anyone in it who's been passed over config.SUB_MAX_SKIPS times goes
        next regardless of MMR. Otherwise MMR balancing decides: pick
        whoever brings this team's average closest to the whole session's
        average MMR (a team below average is pulled toward a stronger
        player, a team above it toward a weaker one). Ties go to whoever
        has waited longest. This only READS state - see _apply_skips."""
        if candidates is None:
            candidates = self._sub_candidates(state, guild, team_id)
        if not candidates:
            return None, [], False
        pool = candidates[:config.SUB_PICK_WINDOW]

        for m in pool:
            if state["bench_since"][m.id].get("skips", 0) >= config.SUB_MAX_SKIPS:
                return m, pool, True

        guild_id, mode = state["guild_id"], state["mode"]
        mmr_of = lambda uid: db.get_player_mode_stats(guild_id, uid, mode)["mmr"]
        active_ids = set()
        for t in state["teams"].values():
            active_ids |= t["on_field"]
        active_mmrs = [mmr_of(pid) for pid in active_ids]
        overall_avg = sum(active_mmrs) / len(active_mmrs) if active_mmrs else config.STARTING_MMR
        team_mmrs = [mmr_of(pid) for pid in state["teams"][team_id]["on_field"]]
        total, count = sum(team_mmrs), len(team_mmrs)
        best = min(pool, key=lambda m: abs((total + mmr_of(m.id)) / (count + 1) - overall_avg))
        return best, pool, False

    def _apply_skips(self, state, pool, incoming):
        """Once a pick is actually made, everyone in the pool who was ahead of
        them in line has been passed over one more time."""
        for m in pool:
            if m.id == incoming.id:
                break
            state["bench_since"][m.id]["skips"] = state["bench_since"][m.id].get("skips", 0) + 1

    def _sub_queue_text(self, state, guild):
        teams = state["teams"]
        guild_id, mode = state["guild_id"], state["mode"]
        order = self._sub_turn_order(state)
        lines = []
        pick, pool, forced = None, [], False
        if order:
            first = teams[order[0]]
            lines.append(f"▶ **Next sub goes to {first['club_name']}** — {len(first['on_field'])} players, the fewest")
            # exactly who they'd get if they pressed Add sub right now (same function the button uses)
            pick, pool, forced = self._pick_sub(state, guild, order[0])
            if pick is None:
                lines.append("Nobody's waiting in the Bench yet.")
            elif forced:
                lines.append(f"They'd get <@{pick.id}> — passed over {config.SUB_MAX_SKIPS} times already, so up next regardless of MMR.")
            elif len(pool) == 1:
                lines.append(f"They'd get <@{pick.id}> — the only one waiting.")
            else:
                lines.append(f"They'd get <@{pick.id}> — the best MMR fit for them out of the first {len(pool)} in line.")
            lines.append("**Turn order:** " + " → ".join(
                f"{teams[t]['club_name']} ({len(teams[t]['on_field'])})" for t in order
            ))
        else:
            lines.append(f"No team can take a sub right now (all at {_normal(state)}, or no captain).")
        at_normal = [
            info["club_name"] for info in teams.values()
            if _normal(state) <= len(info["on_field"]) < _cap(state)
        ]
        if at_normal:
            lines.append(
                f"✅ At {_normal(state)}: {', '.join(at_normal)} — their captain can "
                f"request a {_ordinal(_cap(state))} player if needed"
            )
        full = [info["club_name"] for info in teams.values() if len(info["on_field"]) >= _cap(state)]
        if full:
            lines.append(f"🔒 Full ({_cap(state)}): {', '.join(full)}")
        lines.append("")

        waiting = self._bench_waiting(state, guild)
        if not waiting:
            lines.append("🪑 **Bench:** nobody waiting yet.")
        else:
            lines.append(
                f"🪑 **Bench — longest waiting first** "
                f"(each pick is balanced by MMR among the first {config.SUB_PICK_WINDOW} in line):"
            )
            any_na = False
            for n, m in enumerate(waiting[:30], start=1):
                marker = "▶ " if pick is not None and m.id == pick.id else ""
                tags = ""
                if (db.get_player(guild_id, m.id) or {}).get("is_na"):
                    tags += " 🌎"
                    any_na = True
                rostered_on = [info["club_name"] for info in teams.values() if m.id in info["on_field"]]
                if rostered_on:
                    tags += f" (currently on {rostered_on[0]})"
                rating = db.get_player_mode_stats(guild_id, m.id, mode)["mmr"]
                joined = int(state["bench_since"][m.id]["at"])
                lines.append(f"{marker}`{n}.` <@{m.id}>{tags} — {rating} MMR — joined <t:{joined}:R>")
            if len(waiting) > 30:
                lines.append(f"*…and {len(waiting) - 30} more*")
            if any_na:
                lines.append("")
                lines.append("🌎 = NA. A team that already has NA players only considers the NA players waiting, if there are any.")
        return "\n".join(lines)

    def _build_sub_queue_embed(self, state, guild):
        embed = discord.Embed(
            title="🔁 Sub Queue",
            description=self._sub_queue_text(state, guild)[:4000],
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="Updates live. Only the team whose turn it is can pull a sub — admins can override.")
        return embed

    async def _refresh_sub_queue(self, session_id):
        """The live Sub Queue is shown in the public sub-queue channel and in
        each game's captains channel - keep every copy the same."""
        state = self.active_sessions.get(session_id)
        if not state:
            return
        guild = self.bot.get_guild(state["guild_id"])
        if not guild:
            return
        messages = list(state.get("sub_queue_messages", []))
        messages += [slot["sub_queue_message"] for slot in state["slots"] if slot.get("sub_queue_message")]
        if not messages:
            return
        embed = self._build_sub_queue_embed(state, guild)
        for message in messages:
            try:
                await message.edit(embed=embed)
            except discord.HTTPException:
                pass

    def _schedule_sub_queue_refresh(self, session_id):
        """Refreshes the Sub Queue message shortly from now, batching a burst
        of Bench joins/leaves (e.g. 20 people hopping in at once) into ONE
        edit instead of one Discord call each."""
        state = self.active_sessions.get(session_id)
        if not state:
            return
        task = state.get("sub_queue_task")
        if task and not task.done():
            return  # one's already pending - it will pick this change up too

        async def _later():
            await asyncio.sleep(config.SUB_QUEUE_REFRESH_DELAY)
            await self._refresh_sub_queue(session_id)

        state["sub_queue_task"] = asyncio.create_task(_later())

    # ------------------------------------------------------------------ util
    async def _sync_spectator_capacity(self, guild, state, channel_id, extra=0):
        """Sets a team channel's user limit to the mode's team cap plus however
        many spectators are currently registered as watching it and however
        many non-team admins are sitting in it (plus `extra` for a spectator
        about to join). It drops back to the plain cap when they leave. Derived from the spectator record every time, so
        it's idempotent and self-correcting - it can never drift upward the
        way repeated +1/-1 nudges could. Roster size plays no part here: the
        roster hard cap and Add sub buttons only ever look at on_field."""
        channel = guild.get_channel(channel_id)
        if not channel:
            return
        watching = sum(1 for cid in state["spectators"].values() if cid == channel_id)
        # Admins who drop into the channel without being on the team (and
        # without using a Watch button) get a seat added for them too, so
        # they never eat into the team's own places.
        on_team = next(
            (info["on_field"] for info in state["teams"].values() if info["voice_channel_id"] == channel_id), set()
        )
        admins = sum(
            1 for m in channel.members
            if not getattr(m, "bot", False)
            and m.id not in on_team
            and m.id not in state["spectators"]
            and m.guild_permissions.manage_guild
        )
        target = _cap(state) + watching + admins + extra
        if channel.user_limit != target:
            try:
                await channel.edit(user_limit=target)
            except discord.HTTPException:
                pass

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

        # Anti-stacking: pull how many times each pair in THIS pool has
        # already been teammates in a real match, so build_teams can avoid
        # repeatedly handing a captain the same player session after
        # session (3+ times triggers a swap to another team, when one's
        # available).
        teammate_counts = db.get_teammate_pair_counts(guild.id, [p["discord_id"] for p in players])
        built = team_balance.build_teams(players, num_teams, initial_team_size=initial_team_size, teammate_counts=teammate_counts)
        club_names = team_balance.pick_random_club_names(db.list_clubs(guild.id), num_teams)
        captain_ids = [t["captain"]["discord_id"] for t in built]

        session_id = db.create_session(guild.id, mode)
        # One category for the Bench, then one per game played at the same
        # time (League 2, Rivals 1) - see the layout note in config.py.
        # Created one after another so they sit in this order in the server.
        num_slots = max(1, num_teams // 2)
        prefix = f"{config.SESSION_CATEGORY_PREFIX} #{session_id}"
        category = await voice_utils.create_named_category(
            guild, f"{prefix} · {config.BENCH_CATEGORY_LABEL} ({mode.upper()})"
        )
        slot_categories = []
        for n in range(1, num_slots + 1):
            slot_categories.append(
                await voice_utils.create_named_category(guild, f"{prefix} · {config.MATCH_CATEGORY_LABEL} {n}")
            )
        progress_channel = await self._get_or_create_progress_channel(guild)

        # All the channels this session needs are independent of each other
        # (different channels, no shared state) - create them all at once
        # instead of one-by-one, so the wall-clock time is roughly "however
        # long the single slowest channel creation takes" rather than the
        # sum of every one of them in sequence. This is the single biggest
        # lever on how long a session takes to actually spin up.
        team_channel_tasks = [
            voice_utils.create_team_voice_channel(
                guild, category, club_names[i], [m["discord_id"] for m in built_team["members"]], captain_ids,
                user_limit=config.team_cap(mode),
                channel_name=(
                    f"{club_names[i]} - {config.TEAM_FULL_SUFFIX}"[:100]
                    if len(built_team["members"]) >= config.team_cap(mode) else None
                ),
            )
            for i, built_team in enumerate(built)
        ]
        bench_channel_task = voice_utils.create_bench_channel(guild, category, captain_ids)
        sub_queue_channel_task = voice_utils.create_read_only_text_channel(
            guild, category, config.SUB_QUEUE_CHANNEL_NAME
        )
        channel_results = await asyncio.gather(*team_channel_tasks, bench_channel_task, sub_queue_channel_task)
        team_channels = channel_results[:num_teams]
        bench_channel = channel_results[num_teams]
        sub_queue_channel = channel_results[num_teams + 1]

        # Each game's two text channels, public one first so it sits on top.
        # The team voice channels start in the Bench category and are moved
        # under their game by _post_round (the same step that re-sorts them
        # when League opponents change each round).
        slots = []
        for n, slot_category in enumerate(slot_categories, start=1):
            public_channel = await voice_utils.create_read_only_text_channel(
                guild, slot_category, config.MATCH_PUBLIC_CHANNEL_NAME.format(n=n)
            )
            captains_channel = await voice_utils.create_private_text_channel(
                guild, slot_category, config.MATCH_CAPTAINS_CHANNEL_NAME.format(n=n)
            )
            slots.append({
                "n": n,
                "category_id": slot_category.id,
                "public_channel_id": public_channel.id,
                "captains_channel_id": captains_channel.id,
                "team_ids": (),            # the two teams playing here this round
                "match_id": None,
                "round_no": None,
                "public_message": None,    # rosters + Watch buttons
                "panel": None,             # {"message", "view"} - this game's captain panel
                "sub_queue_message": None, # this game's copy of the live Sub Queue
                "captain_access": set(),   # member ids currently let into the captains channel
            })

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

            # channel is always created at the mode's full team cap, even
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
                # the voice channel name the bot last set, so it only renames on a real change
                "vc_name": (
                    f"{club_name} - {config.TEAM_FULL_SUFFIX}"[:100]
                    if len(member_ids) >= config.team_cap(mode) else club_name
                ),
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
            control_id=slots[0]["captains_channel_id"], progress_id=progress_channel.id,
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
            "sub_queue_channel_id": sub_queue_channel.id,
            "slots": slots,
            "progress_channel_id": progress_channel.id,
            "category_id": category.id,  # the Bench category
            "category_ids": [category.id] + [c.id for c in slot_categories],  # everything to tear down at the end
            "announce_channel_id": announce_channel_id,
            "current_round": 1,
            "total_rounds": len(rounds),
            "progress_round_message": None,
            "auto_close_task": None,
            "sub_lock": asyncio.Lock(),
            "spectators": {},  # user_id -> channel_id they're spectating, for auto-unmute on leave
            "spectate_lock": asyncio.Lock(),
            "admin_panel": None,           # {"message", "view"} - the admin controls posted in the admin log
            "admin_status_message": None,  # the live match status message in the admin log
            "bench_since": {},  # user_id -> {"seq", "at"}: when each person entered the Bench VC, so subs are pulled longest-waiting first
            "bench_seq": 0,
            "sub_queue_messages": [],   # the public copy of the live "Sub Queue" (each game's captains channel has its own)
            "sub_queue_task": None,     # pending debounced refresh of that message, if any
            "match_rosters": {},  # match_id -> {"team_a": {ids}, "team_b": {ids}} once frozen via Match Live
            "admin_log_channel_id": (db.get_guild_config(guild.id) or {}).get("admin_log_channel_id"),
            "is_test": is_test,
        }

        session_state = self.active_sessions[session_id]
        # a full pop can start with every team already at its cap (Rivals at 22)
        await self._sync_bench_visibility(guild, session_state)

        # Live Sub Queue, public copy: whose turn it is + the Bench in
        # longest-waiting-first order, so benched players can see their place.
        try:
            session_state["sub_queue_messages"].append(
                await sub_queue_channel.send(embed=self._build_sub_queue_embed(session_state, guild))
            )
        except discord.HTTPException:
            pass
        await self._post_round(guild, session_id, 1)

        announce_channel = guild.get_channel(announce_channel_id)
        clubs_line = ", ".join(club_names)
        if announce_channel:
            extra_note = f" (Started {missing} short of a full queue — late arrivals can join the Bench and be subbed in.)" if missing else ""
            test_note = " 🧪 **TEST SESSION — no MMR, wins/losses, or match history will be recorded.**" if is_test else ""
            await announce_channel.send(
                f"🟢 **NTF {mode.title()} session started!** Teams: {clubs_line}.{extra_note}{test_note} "
                f"Captains — your controls are in your match's captains channel."
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

        await self._log_action(
            session_id,
            f"\n━━━━━━━━━━━━━━━━━━━━\n📋 **Session #{session_id} started** — {mode.title()} — "
            f"Teams: {clubs_line}\n━━━━━━━━━━━━━━━━━━━━",
        )
        # the admin controls + live match status go right under that header line
        await self._post_admin_panel(guild, session_id)

        return session_id

    # ------------------------------------------------------ per-game channels
    def captained_team(self, state, user_id):
        """The team this user captains in this session, or None."""
        return next((team_id for team_id, info in state["teams"].items() if info["captain_id"] == user_id), None)

    def can_manage_player(self, interaction: discord.Interaction, state, player_id) -> bool:
        """Admins can transfer/remove anyone. A captain only their OWN
        players - never someone on another team."""
        if interaction.user.guild_permissions.manage_guild:
            return True
        team_id = self.captained_team(state, interaction.user.id)
        return team_id is not None and player_id in state["teams"][team_id]["on_field"]

    def _match_status_text(self, state, match) -> str:
        if match["status"] == "reported":
            score = f"{match['score_a']}-{match['score_b']}" if match["score_a"] is not None else "final"
            winner = state["teams"].get(match["winner_team_id"], {}).get("club_name", "?")
            return f"✅ Finished {score} — **{winner}** won"
        if match["id"] in state["match_rosters"]:
            return "🔴 **LIVE**"
        return "⏳ Waiting to start"

    def _build_match_embed(self, session_id, slot, match=None):
        """What everyone sees in a game's public match channel: the two
        teams, their captains and players, and where the game is at."""
        state = self.active_sessions[session_id]
        if match is None and slot["match_id"] is not None:
            match = db.get_match(slot["match_id"])
        clubs = [state["teams"][t]["club_name"] for t in slot["team_ids"]]
        embed = discord.Embed(
            title=f"Match {slot['n']} • Round {slot['round_no']}: {' vs '.join(clubs)}",
            description=self._match_status_text(state, match) if match else None,
            color=discord.Color.blurple(),
        )
        for team_id in slot["team_ids"]:
            info = state["teams"][team_id]
            member_lines = "\n".join(f"• <@{pid}>" for pid in info["on_field"]) or "*empty*"
            embed.add_field(
                name=f"{info['club_name']} ({len(info['on_field'])})",
                value=f"**Captain:** {_captain_display(info['captain_id'])}\n{member_lines}",
                inline=True,
            )
        embed.set_footer(text="Press a Watch button to spectate that team (you join muted).")
        return embed

    async def _sync_captains_access(self, guild, state, slot):
        """Lets exactly the two captains playing in this game into its
        captains channel, and takes out anyone who no longer belongs there
        (League opponents change each round; captains get reassigned).
        Derived from who captains the two teams right now, so it can be
        called after any change and only does work when something differs."""
        channel = guild.get_channel(slot["captains_channel_id"])
        if not channel:
            return
        wanted = {state["teams"][t]["captain_id"] for t in slot["team_ids"]} - {None}
        have = slot["captain_access"]
        for uid in wanted - have:
            member = guild.get_member(uid)
            if member:
                try:
                    await channel.set_permissions(member, view_channel=True, send_messages=True)
                except discord.HTTPException:
                    pass
        for uid in have - wanted:
            member = guild.get_member(uid)
            if member:
                try:
                    await channel.set_permissions(member, overwrite=None)
                except discord.HTTPException:
                    pass
        slot["captain_access"] = set(wanted)

    def _schedule_rename(self, guild, state, channel_id, name):
        """Renames a channel or category in the background. Discord only
        allows a couple of renames per channel every ten minutes and makes
        the bot wait when that's used up, so this never blocks a round from
        starting - it applies the most recently wanted name when it can."""
        wanted = state.setdefault("rename_wanted", {})
        applied = state.setdefault("rename_applied", {})
        running = state.setdefault("rename_running", set())
        wanted[channel_id] = name
        if channel_id in running or applied.get(channel_id) == name:
            return
        running.add(channel_id)

        async def _run():
            try:
                for _attempt in range(3):
                    target = wanted.get(channel_id)
                    if applied.get(channel_id) == target:
                        return
                    channel = guild.get_channel(channel_id)
                    if not channel:
                        return
                    try:
                        await channel.edit(name=target)
                    except discord.HTTPException:
                        return
                    applied[channel_id] = target
            finally:
                running.discard(channel_id)

        asyncio.create_task(_run())

    async def _delete_quietly(self, message):
        if message:
            try:
                await message.delete()
            except discord.HTTPException:
                pass

    async def _assign_slot(self, guild, session_id, slot, match, round_no, n, total):
        """Sets one game's category up for this round's fixture: moves the two
        teams' voice channels under it, lets their two captains into the
        captains channel, and posts a fresh public roster (with Watch
        buttons), match-report buttons and captain panel."""
        state = self.active_sessions[session_id]
        team_a_id, team_b_id = match["team_a_id"], match["team_b_id"]
        club_a = state["teams"][team_a_id]["club_name"]
        club_b = state["teams"][team_b_id]["club_name"]
        slot["team_ids"] = (team_a_id, team_b_id)
        slot["match_id"] = match["id"]
        slot["round_no"] = round_no

        category = guild.get_channel(slot["category_id"])
        if category:
            for team_id in (team_a_id, team_b_id):
                voice_channel = guild.get_channel(state["teams"][team_id]["voice_channel_id"])
                if voice_channel and getattr(voice_channel, "category_id", None) != category.id:
                    try:
                        await voice_channel.edit(category=category)
                    except discord.HTTPException:
                        pass
        self._schedule_rename(
            guild, state, slot["category_id"],
            f"{config.SESSION_CATEGORY_PREFIX} #{session_id} · {config.MATCH_CATEGORY_LABEL} {slot['n']} · {club_a} vs {club_b}"[:100],
        )
        await self._sync_captains_access(guild, state, slot)

        # public match channel: one message, replaced each round
        await self._delete_quietly(slot.get("public_message"))
        slot["public_message"] = None
        public_channel = guild.get_channel(slot["public_channel_id"])
        if public_channel:
            watch_view = SpectateView(self, session_id, [(team_a_id, club_a), (team_b_id, club_b)])
            slot["public_message"] = await self._send_with_retry(
                public_channel.send, embed=self._build_match_embed(session_id, slot, match), view=watch_view
            )

        # captains channel: round divider, the result buttons, then the roster
        # panel and Sub Queue reposted underneath so they're always at the bottom
        captains_channel = guild.get_channel(slot["captains_channel_id"])
        if not captains_channel:
            return
        if slot.get("panel"):
            await self._delete_quietly(slot["panel"]["message"])
        await self._delete_quietly(slot.get("sub_queue_message"))
        slot["panel"], slot["sub_queue_message"] = None, None

        await self._send_with_retry(captains_channel.send, f"**━━━━━━━━━━ ROUND {round_no} ━━━━━━━━━━**")
        control_embed = discord.Embed(
            title=f"Round {round_no} • Match {n}/{total}: {club_a} vs {club_b}",
            description="Captains: press **Match Live** at kick-off, then click the winner once the game finishes.",
            color=discord.Color.orange(),
        )
        for team_id in (team_a_id, team_b_id):
            info = state["teams"][team_id]
            mmrs = [db.get_player_mode_stats(state["guild_id"], pid, state["mode"])["mmr"] for pid in info["on_field"]]
            avg_mmr = round(sum(mmrs) / len(mmrs)) if mmrs else 0
            control_embed.add_field(
                name=info["club_name"],
                value=f"Captain: {_captain_display(info['captain_id'])}\nPlayers: {len(mmrs)}\nAvg MMR: {avg_mmr}",
                inline=True,
            )
        match_view = MatchControlView(self, session_id, match["id"], team_a_id, club_a, team_b_id, club_b)
        await self._send_with_retry(captains_channel.send, embed=control_embed, view=match_view)

        panel_view = CaptainPanelView(self, session_id, state["mode"])
        panel_view.sync_sub_buttons(state["teams"], self._sub_turn_team(state))
        panel_message = await self._send_with_retry(captains_channel.send, view=panel_view)
        slot["panel"] = {"message": panel_message, "view": panel_view}
        slot["sub_queue_message"] = await self._send_with_retry(
            captains_channel.send, embed=self._build_sub_queue_embed(state, guild)
        )

    # ------------------------------------------------------ admin log panel
    def _build_admin_status_embed(self, session_id, ended: bool = False):
        state = self.active_sessions[session_id]
        round_no = state["current_round"]
        matches = [m for m in db.get_matches_for_session(session_id) if m["round_no"] == round_no]
        title = f"Session #{session_id} — {state['mode'].title()} — Round {round_no}/{state['total_rounds']}"
        embed = discord.Embed(
            title=title + (" — ended" if ended else ""),
            color=discord.Color.dark_grey() if ended else discord.Color.green(),
        )
        for n, match in enumerate(matches, start=1):
            info_a = state["teams"][match["team_a_id"]]
            info_b = state["teams"][match["team_b_id"]]
            embed.add_field(
                name=f"Match {n}: {info_a['club_name']} vs {info_b['club_name']}",
                value=(
                    f"{self._match_status_text(state, match)}\n"
                    f"Captains: {_captain_display(info_a['captain_id'])} / {_captain_display(info_b['captain_id'])}"
                ),
                inline=False,
            )
        if not ended:
            embed.set_footer(text="Updates by itself. Everything that happens is logged below.")
        return embed

    async def _post_admin_panel(self, guild, session_id):
        """Posts this session's live match status and admin controls in the
        admin log (the same channel every action is already logged to). If
        the server has no admin log set up, there is simply no panel -
        admins still have /force_end_session."""
        state = self.active_sessions[session_id]
        channel = guild.get_channel(state["admin_log_channel_id"]) if state.get("admin_log_channel_id") else None
        if not channel:
            return
        try:
            status_message = await channel.send(embed=self._build_admin_status_embed(session_id))
            view = AdminPanelView(self, session_id)
            panel_message = await channel.send(f"🛠️ **Session #{session_id} — admin controls**", view=view)
        except discord.HTTPException:
            return
        state["admin_status_message"] = status_message
        state["admin_panel"] = {"message": panel_message, "view": view}
        try:
            await status_message.pin()  # keeps it one tap away as the log grows underneath
        except discord.HTTPException:
            pass

    async def _refresh_status(self, session_id):
        """Brings every "where is each game at" display up to date: the
        status message in the admin log and each game's public match channel.
        Called when a round starts, a match goes live, a result is reported
        or undone."""
        state = self.active_sessions.get(session_id)
        if not state:
            return
        status_message = state.get("admin_status_message")
        if status_message:
            try:
                await status_message.edit(embed=self._build_admin_status_embed(session_id))
            except discord.HTTPException:
                pass
        for slot in state["slots"]:
            message = slot.get("public_message")
            if message and slot["team_ids"]:
                try:
                    await message.edit(embed=self._build_match_embed(session_id, slot))
                except discord.HTTPException:
                    pass

    async def _refresh_sub_buttons(self, session_id):
        """Re-syncs the Add sub button on every game's captain panel with the
        current rosters - it shows whose turn it is, and greys out when no
        team can take a sub. Best-effort: a failed edit never blocks the
        roster change that triggered it."""
        state = self.active_sessions.get(session_id)
        if not state:
            return
        turn_team_id = self._sub_turn_team(state)
        for slot in state["slots"]:
            panel = slot.get("panel")
            if not panel:
                continue
            panel["view"].sync_sub_buttons(state["teams"], turn_team_id)
            try:
                await panel["message"].edit(view=panel["view"])
            except discord.HTTPException:
                pass

    async def _sync_bench_visibility(self, guild, state):
        """The Bench only exists to feed subs onto teams, so while EVERY team
        is at its hard cap (League 7, Rivals 11) there is nothing it can do
        and it is hidden from the server. The moment any team drops below
        its cap (a player removed or transferred out) it comes back. Derived
        from the rosters every time, so it can't get stuck either way.
        Hiding is done on the @everyone permission only: the channel itself,
        its id and everything that points at it stay exactly as they are."""
        all_full = bool(state["teams"]) and all(
            len(info["on_field"]) >= _cap(state) for info in state["teams"].values()
        )
        if state.get("bench_hidden", False) == all_full:
            return
        bench_channel = guild.get_channel(state["bench_channel_id"])
        if not bench_channel:
            return
        try:
            await bench_channel.set_permissions(
                guild.default_role, view_channel=not all_full, connect=not all_full
            )
        except discord.HTTPException:
            return  # leave the flag alone so the next roster change tries again
        state["bench_hidden"] = all_full

    def _schedule_team_name_sync(self, guild, state, team_id):
        """Keeps a team's voice channel name in step with whether the team is
        at its cap ("<club> - MAX Players") or not ("<club>"). Runs in the
        background and never blocks a sub or transfer: Discord only allows a
        channel to be renamed a couple of times every ten minutes and makes
        the bot wait when that's used up, so a rename can lag behind while
        everything else carries on."""
        info = state["teams"][team_id]
        if info.get("vc_name") == _team_channel_name(state, team_id):
            return
        pending = state.setdefault("name_sync_pending", set())
        if team_id in pending:
            return  # one already running - it re-checks the roster when it finishes
        pending.add(team_id)

        async def _run():
            try:
                for _attempt in range(3):
                    desired = _team_channel_name(state, team_id)
                    if info.get("vc_name") == desired:
                        return
                    channel = guild.get_channel(info["voice_channel_id"])
                    if not channel:
                        return
                    try:
                        await channel.edit(name=desired)
                    except discord.HTTPException:
                        return
                    info["vc_name"] = desired
            finally:
                pending.discard(team_id)

        asyncio.create_task(_run())

    async def _refresh_team_roster(self, session_id):
        state = self.active_sessions.get(session_id)
        if not state:
            return
        # Every roster change already calls this, so it's the one place that
        # also keeps the Add sub buttons in step with team sizes.
        await self._refresh_sub_buttons(session_id)
        self._schedule_sub_queue_refresh(session_id)  # the "next sub goes to..." line depends on roster sizes too
        guild = self.bot.get_guild(state["guild_id"])
        if guild:
            for team_id in state["teams"]:
                self._schedule_team_name_sync(guild, state, team_id)
            await self._sync_bench_visibility(guild, state)
            # a transfer, removal or reassignment can change who captains a team
            for slot in state["slots"]:
                if slot["team_ids"]:
                    await self._sync_captains_access(guild, state, slot)
        # rosters (and captains) shown in each game's public channel + the admin status
        await self._refresh_status(session_id)

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

    async def _log_action(self, session_id, message):
        """Posts one line to the admin-only audit log channel (set up via
        /ntf_setup), if configured for this server - a permanent record of
        who clicked what during a session (results reported, subs,
        transfers, removals, reassignments, undos), so admins can figure
        out exactly what happened and credit the right people afterward.
        Best-effort: silently does nothing if the channel isn't set up or
        the bot can't post there, since this is a supplementary record, not
        something that should ever block the actual gameplay action."""
        state = self.active_sessions.get(session_id)
        if not state or not state.get("admin_log_channel_id"):
            return
        guild = self.bot.get_guild(state["guild_id"])
        if not guild:
            return
        channel = guild.get_channel(state["admin_log_channel_id"])
        if not channel:
            return
        try:
            await channel.send(message)
        except discord.HTTPException:
            pass

    async def _post_round(self, guild, session_id, round_no):
        state = self.active_sessions[session_id]
        matches = [m for m in db.get_matches_for_session(session_id) if m["round_no"] == round_no]
        progress_channel = guild.get_channel(state["progress_channel_id"])
        slots = state["slots"]

        embed_index = {}
        progress_embeds = []
        for n, match in enumerate(matches, start=1):
            club_a = state["teams"][match["team_a_id"]]["club_name"]
            club_b = state["teams"][match["team_b_id"]]["club_name"]

            # this round's n-th fixture is played in the n-th game category
            slot = slots[min(n, len(slots)) - 1]
            await self._assign_slot(guild, session_id, slot, match, round_no, n, len(matches))

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

        # #in-progress keeps the round-by-round scores; rosters and the Watch
        # buttons now live in each game's public match channel.
        header_embed = discord.Embed(title=f"📺 Round {round_no} — Now Playing", color=discord.Color.green())
        all_embeds = [header_embed] + progress_embeds
        progress_message = await self._send_with_retry(progress_channel.send, embeds=all_embeds)

        state["progress_round_message"] = {
            "message": progress_message,
            "embed_index": embed_index,   # match_id -> index INTO fixture embeds (add 1 for real embeds list index)
            "round_no": round_no,
        }
        await self._refresh_status(session_id)

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

    async def _update_progress_field_reset(self, session_id, match_id, club_a, club_b):
        """Resets a fixture's card in #in-progress back to the plain
        "In progress" placeholder - used after an admin undoes a result, so
        the card doesn't keep showing the old (wrong) winner/score while
        the match waits to be re-reported correctly."""
        state = self.active_sessions.get(session_id)
        pr = state["progress_round_message"] if state else None
        if not pr or match_id not in pr["embed_index"]:
            return
        message = pr["message"]
        idx = pr["embed_index"][match_id] + 1  # +1 to skip the header embed
        try:
            embeds = list(message.embeds)
            fixture_embed = embeds[idx]
            fixture_embed.description = "⏳ In progress"
            fixture_embed.colour = discord.Color.orange()
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
            old_mmr_by_id = {p["discord_id"]: p["mmr"] for p in team_a_players + team_b_players}
            mmr_deltas = {discord_id: new_mmr - old_mmr_by_id[discord_id] for discord_id, new_mmr in mmr_updates.items()}
            for discord_id, new_mmr in mmr_updates.items():
                db.update_mode_mmr(state["guild_id"], discord_id, state["mode"], new_mmr, won=discord_id in winner_ids)
        else:
            mmr_deltas = {}

        score_a = winner_score if a_won else loser_score
        score_b = loser_score if a_won else winner_score
        db.report_match_result(match_id, winner_team_id, score_a, score_b)

        club_a = state["teams"][team_a_id]["club_name"]
        club_b = state["teams"][team_b_id]["club_name"]
        if not state["is_test"]:
            db.record_match_participants(
                state["guild_id"], match_id, session_id, team_a_id, team_b_id,
                [p["discord_id"] for p in team_a_players], [p["discord_id"] for p in team_b_players],
                club_a, club_b, a_won, state["mode"], mmr_deltas=mmr_deltas,
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
        await self._refresh_status(session_id)
        if not state["is_test"]:
            await leaderboard_utils.refresh_leaderboard_channel(self.bot, guild)

        pens_note = " (on penalties)" if went_to_pens else ""
        test_note = " *(test — not recorded)*" if state["is_test"] else ""
        await interaction.followup.send(f"Result recorded — {winner_club} win{pens_note} ✅{test_note}", ephemeral=True)

        score_text = "" if went_to_pens else f" {score_a}-{score_b}"
        await self._log_action(
            session_id,
            f"🔴 <@{interaction.user.id}> reported: **{club_a}** vs **{club_b}**{score_text} — "
            f"winner **{winner_club}**{pens_note}.",
        )

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

        progress_channel = guild.get_channel(state["progress_channel_id"])
        for slot in state["slots"]:
            captains_channel = guild.get_channel(slot["captains_channel_id"])
            if captains_channel:
                try:
                    await captains_channel.send(embed=embed)
                except discord.HTTPException:
                    pass
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
    def _sub_blocked_reason(self, state, team_id, club_name, extra, full_msg):
        """Why this team can't take a player through this path right now, or
        None if it can. Normal subs stop at the normal size; the extra
        (7th player) request only applies to a team sitting between
        the normal size and the team cap."""
        size = len(state["teams"][team_id]["on_field"])
        if size >= _cap(state):
            return full_msg
        if extra and size < _normal(state):
            return (
                f"{club_name} only has {size} — use the green Add sub button until the team is at "
                f"{_normal(state)}. The {_ordinal(_cap(state))} player is a last resort after that."
            )
        if not extra and size >= _normal(state):
            if _cap(state) <= _normal(state):
                return full_msg
            return (
                f"{club_name} already has {_normal(state)} players. If you really need one more, "
                f"the captain can press **Request {_ordinal(_cap(state))} player** "
                f"(maximum {_cap(state)} per team)."
            )
        return None

    async def request_sub(self, interaction: discord.Interaction, session_id, team_id, club_name, extra: bool = False):
        """Pulls a player from the Bench VC onto the requesting team: the
        longest-waiting players form the pool and MMR balancing picks the best
        fit for that team from it (see _pick_sub; an NA team only considers
        waiting NA players first). It has to be that team's turn - the team
        with the fewest players goes first - unless an admin is the one
        clicking. No swapping: this adds to the roster, up to
        _normal(state).

        extra=True is the "Request 7th player" path: it fills the one seat
        between the mode's normal size and its cap (League only). It only
        works for a team already at the normal size, and it waits until no
        other team is still short of that size (those teams get the Bench
        first), unless an admin is the one clicking."""
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

        # Hard ceiling: a team can never exceed the mode's cap,
        # subs included. Subs are only available while there's a free slot -
        # once someone is removed or transferred out, the slot opens again.
        # (The Add sub button is also greyed out at this point, but this
        # check stays as the real enforcement.)
        full_msg = (
            f"{club_name} is already at its maximum of {_cap(state)} players — "
            f"a slot only opens up if someone is removed from the roster or transferred out."
        )
        blocked = self._sub_blocked_reason(state, team_id, club_name, extra, full_msg)
        if blocked:
            await interaction.followup.send(blocked, ephemeral=True)
            return

        async with state["sub_lock"]:
            # re-fetch state in case the session ended while we were waiting on the lock
            state = self.active_sessions.get(session_id)
            if not state:
                await interaction.followup.send("This session has ended.", ephemeral=True)
                return

            # Re-check now that we hold the lock. Two quick clicks can both pass the
            # early check above before either one finishes - which would otherwise
            # push a team past the ceiling, or let one team pull twice in a row.
            blocked = self._sub_blocked_reason(state, team_id, club_name, extra, full_msg)
            if blocked:
                await interaction.followup.send(blocked, ephemeral=True)
                return

            # Turn order: the team with the fewest players pulls next (fixed team
            # order on ties), so captains never argue over who's up. Admins can pull
            # for any team - the way past a captain who's away.
            turn_order = self._sub_turn_order(state)
            if extra and turn_order and not is_admin:
                # The extra seat is a last resort: teams still short of the
                # normal size get the Bench first.
                short = ", ".join(
                    f"{state['teams'][t]['club_name']} ({len(state['teams'][t]['on_field'])})" for t in turn_order
                )
                await interaction.followup.send(
                    f"⏳ A {_ordinal(_cap(state))} player has to wait — these teams are still under "
                    f"{_normal(state)} and go first: {short}. An admin can override.",
                    ephemeral=True,
                )
                return
            if not extra and turn_order and team_id != turn_order[0] and not is_admin:
                turn_team = state["teams"][turn_order[0]]
                place = f" **{club_name}** is #{turn_order.index(team_id) + 1} in line." if team_id in turn_order else ""
                await interaction.followup.send(
                    f"⏳ Not your turn yet — **{turn_team['club_name']}** pulls next "
                    f"(they have the fewest players: {len(turn_team['on_field'])}).{place} "
                    f"If they're away, an admin can pull for you.",
                    ephemeral=True,
                )
                return

            guild = interaction.guild
            bench_channel = guild.get_channel(state["bench_channel_id"])
            # Everyone waiting in the Bench, longest-waiting first, except people already
            # on THIS team. Someone active on a DIFFERENT team is still a valid candidate -
            # they just get properly transferred off it as part of the sub (see below) - so
            # a player can voluntarily bench themselves and get pulled onto a new team
            # without ever being double-booked on two teams at once.
            candidates = self._sub_candidates(state, guild, team_id)
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

            # Same choice the Sub Queue message shows in session-control: the longest-waiting
            # players form the pool, MMR balancing picks from it (see _pick_sub).
            incoming, pool, _forced = self._pick_sub(state, guild, team_id, candidates)
            self._apply_skips(state, pool, incoming)
            team_channel = guild.get_channel(state["teams"][team_id]["voice_channel_id"])

            # If they're currently active on a DIFFERENT team, transfer them
            # off it first - a player can only ever be registered to one
            # team's roster at a time. (Team voice channels are created at the
            # full team cap and a roster can never exceed it, so
            # there's no per-channel capacity to adjust as people move.)
            for other_team_id, other_info in state["teams"].items():
                if other_team_id != team_id and incoming.id in other_info["on_field"]:
                    other_info["on_field"].discard(incoming.id)
                    db.set_member_role(other_team_id, incoming.id, "sub")
                    break

            await voice_utils.allow_member_in_channel(team_channel, incoming, connect=True)
            moved, move_fail_reason = await voice_utils.move_member_to_channel(guild, incoming.id, team_channel)

            state["teams"][team_id]["on_field"].add(incoming.id)
            db.add_team_member(team_id, incoming.id, "player")

            # Now that they're rostered onto a team, lock them out of Bench
            # too - once assigned, a player can't voluntarily bench
            # themselves to get poached by yet another team.
            bench_channel = guild.get_channel(state["bench_channel_id"])
            if bench_channel:
                await voice_utils.allow_member_in_channel(bench_channel, incoming, connect=False)

        progress_channel = guild.get_channel(state["progress_channel_id"])
        joined_as = f" as their {_ordinal(_cap(state))} player" if extra else ""
        await progress_channel.send(f"🔁 <@{incoming.id}> joins **{club_name}** from the bench{joined_as}.")
        await self._refresh_team_roster(session_id)
        await self._log_action(
            session_id,
            f"🔁 <@{interaction.user.id}> requested a {'extra (' + _ordinal(_cap(state)) + ') player' if extra else 'sub'} "
            f"for **{club_name}** — <@{incoming.id}> came on.",
        )

        next_team_id = self._sub_turn_team(state)
        next_note = f" Next sub goes to **{state['teams'][next_team_id]['club_name']}**." if next_team_id is not None else ""
        if moved:
            await interaction.followup.send(f"<@{incoming.id}> has been moved onto {club_name}.{next_note}", ephemeral=True)
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
        team-cap hard ceiling and Bench-lock enforcement as a sub.
        Returns (added: bool, moved: bool, reason: str | None) - added is
        False only if the hard cap blocked the transfer entirely (nothing
        changed); moved reflects whether the physical voice drag succeeded
        given the roster change did go through."""
        # Held for the whole transfer so it can't race an Add sub (or another transfer) into
        # the same team: the ceiling check and the roster add below must happen atomically.
        async with state["sub_lock"]:
            # Same hard ceiling as Add Sub - a team can never exceed
            # the mode's cap. Check this BEFORE touching anything, so a
            # blocked transfer doesn't still rip the player off their old team.
            # A transfer only fills a team to its NORMAL size. Where the cap is
            # higher (League: 6, then a 7th), that last seat is only ever
            # filled through Request Nth player, never by a transfer.
            if len(state["teams"][dest_team_id]["on_field"]) >= _normal(state):
                dest_club = state["teams"][dest_team_id]["club_name"]
                if _cap(state) > _normal(state):
                    return False, False, (
                        f"{dest_club} already has {_normal(state)} players — a {_ordinal(_cap(state))} can only be "
                        f"added with Request {_ordinal(_cap(state))} player"
                    )
                return False, False, f"{dest_club} is already at its maximum of {_cap(state)} players"

            # remove from any other team they're currently on
            stripped_captaincy_of = None
            for other_team_id, other_info in state["teams"].items():
                if other_team_id != dest_team_id and player_id in other_info["on_field"]:
                    other_info["on_field"].discard(player_id)
                    db.set_member_role(other_team_id, player_id, "sub")

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
            # their game's captains channel is opened to them by
            # _refresh_team_roster below (via _sync_captains_access)

        club_name = state["teams"][team_id]["club_name"]
        progress_channel = guild.get_channel(state["progress_channel_id"])
        if progress_channel:
            await progress_channel.send(f"🎖️ <@{new_captain_id}> is now captaining **{club_name}** (previously <@{old_captain_id}>).")
        await self._refresh_team_roster(session_id)

    # -------------------------------------------------------------- full removal
    async def remove_player_from_roster(self, guild: discord.Guild, session_id: int, team_id: int, player_id: int):
        """Pulls a player off their team's roster entirely - not benched,
        not transferred, just gone. Frees their seat on the roster so someone
        else can actually be subbed/transferred in without hitting the
        team cap (which also re-enables that team's Add sub
        button, since every roster change goes through _refresh_team_roster).
        Unlike a transfer, captaincy is ALWAYS cleared here (no admin
        exemption) since they're not on any team anymore at all. Their
        Bench lock is lifted too, so if they come back later they can
        rejoin normally through Bench instead of needing another manual fix."""
        state = self.active_sessions[session_id]
        info = state["teams"][team_id]
        info["on_field"].discard(player_id)
        db.set_member_role(team_id, player_id, "removed")

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

        # the Sub Queue message is about to disappear with the session - stop any pending refresh of it
        queue_task = state.get("sub_queue_task")
        if queue_task and not queue_task.done():
            queue_task.cancel()

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
        for category_id in state.get("category_ids") or [state["category_id"]]:
            category = guild.get_channel(category_id)
            if category:
                await voice_utils.teardown_session_category(guild, category)

        # admin log: take the controls away, leave the final status behind as part of the record
        admin_panel = state.get("admin_panel")
        if admin_panel:
            await self._delete_quietly(admin_panel["message"])
        status_message = state.get("admin_status_message")
        if status_message:
            try:
                await status_message.edit(embed=self._build_admin_status_embed(session_id, ended=True))
                await status_message.unpin()
            except discord.HTTPException:
                pass

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

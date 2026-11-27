import asyncio
import logging
import time
import traceback
from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands

import config
import database as db
import ui

log = logging.getLogger("ntf")

PLATFORM_ICONS = {"Console": "🎮", "PC": "🖥️"}


class QueuePanelView(discord.ui.View):
    """The permanent entry point. Joining opens a quick platform pick, then
    hands off to that mode's dedicated ready-check message."""

    def __init__(self, cog: "QueueCog"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Join Rivals (2 teams)", style=discord.ButtonStyle.primary, emoji="⚔️", custom_id="pc_join_rivals")
    async def join_rivals(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Which platform are you on?", view=PlatformPickView(self.cog, "rivals"), ephemeral=True)

    @discord.ui.button(label="Join League (4 teams)", style=discord.ButtonStyle.primary, emoji="🏆", custom_id="pc_join_league")
    async def join_league(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Which platform are you on?", view=PlatformPickView(self.cog, "league"), ephemeral=True)

    @discord.ui.button(label="Leave Queue", style=discord.ButtonStyle.danger, emoji="🚪", custom_id="pc_leave_queue")
    async def leave_queue(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_leave(interaction)

    @discord.ui.button(label="Show Queue", style=discord.ButtonStyle.secondary, emoji="📋", custom_id="pc_show_queue")
    async def show_queue(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_show(interaction)


class PlatformPickView(discord.ui.View):
    """Ephemeral - this IS the 'ready up' action: picking a platform both
    joins the queue and signals ready intent in one tap."""

    def __init__(self, cog: "QueueCog", mode: str):
        super().__init__(timeout=60)
        self.cog = cog
        self.mode = mode

    @discord.ui.button(label="Console", style=discord.ButtonStyle.success, emoji="🎮")
    async def console(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_ready(interaction, self.mode, "Console")

    @discord.ui.button(label="PC", style=discord.ButtonStyle.success, emoji="🖥️")
    async def pc(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_ready(interaction, self.mode, "PC")


class ReadyMessageView(discord.ui.View):
    """The dedicated per-mode ready-check message. Stays up to date as
    players ready up or leave. Admins also get Force Start and Pause Queue."""

    def __init__(self, cog: "QueueCog", mode: str, paused: bool = False):
        super().__init__(timeout=None)
        self.cog = cog
        self.mode = mode

        self.console_btn = discord.ui.Button(label="Ready Up (Console)", style=discord.ButtonStyle.success, emoji="🎮", row=0)
        self.pc_btn = discord.ui.Button(label="Ready Up (PC)", style=discord.ButtonStyle.success, emoji="🖥️", row=0)
        leave_btn = discord.ui.Button(label="Not Ready / Leave", style=discord.ButtonStyle.danger, emoji="🚪", row=0)

        self.console_btn.callback = self._make_ready_cb("Console")
        self.pc_btn.callback = self._make_ready_cb("PC")
        leave_btn.callback = self._leave_cb

        self.add_item(self.console_btn)
        self.add_item(self.pc_btn)
        self.add_item(leave_btn)

        self.force_btn = None
        if mode in config.FORCE_START_MIN:
            self.force_btn = discord.ui.Button(
                label=f"Force Start (Admin, {config.FORCE_START_MIN[mode]}+)",
                style=discord.ButtonStyle.primary, emoji="⚡", row=1,
            )
            self.force_btn.callback = self._force_start_cb
            self.add_item(self.force_btn)

        self.pause_btn = discord.ui.Button(label="Pause Queue", style=discord.ButtonStyle.secondary, emoji="⏸️", row=1)
        self.pause_btn.callback = self._pause_cb
        self.add_item(self.pause_btn)
        self.set_paused(paused)

    def set_paused(self, paused: bool):
        """While a queue is paused nobody new can ready up, so those buttons
        (and Force Start) grey out and the pause button becomes Resume.
        Leaving is always allowed."""
        self.console_btn.disabled = paused
        self.pc_btn.disabled = paused
        if self.force_btn:
            self.force_btn.disabled = paused
        self.pause_btn.label = "Resume Queue" if paused else "Pause Queue"
        self.pause_btn.emoji = "▶️" if paused else "⏸️"

    def _make_ready_cb(self, platform):
        async def cb(interaction: discord.Interaction):
            await self.cog.handle_ready(interaction, self.mode, platform)
        return cb

    async def _leave_cb(self, interaction: discord.Interaction):
        await self.cog.handle_leave(interaction)

    async def _force_start_cb(self, interaction: discord.Interaction):
        await self.cog.handle_force_start(interaction, self.mode)

    async def _pause_cb(self, interaction: discord.Interaction):
        await self.cog.handle_pause(interaction, self.mode)


class QueueCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # guild_id -> {"rivals": {...}, "league": {...}}
        self.state: dict[int, dict[str, dict]] = {}
        # guild_id -> channel where the panel/ready-checks/announcements live
        self.panel_channel: dict[int, int] = {}
        self.panel_message: dict[int, discord.Message] = {}

    def _mode_state(self, guild_id: int, mode: str):
        guild_state = self.state.setdefault(guild_id, {})
        return guild_state.setdefault(mode, {
            "ready": [], "platforms": {}, "message": None, "view": None,
            "countdown_task": None, "timeout_task": None,
            "paused_by": None,  # admin's user id while this mode's queue is paused
            # Queue states: WAITING (no countdown), COUNTDOWN (countdown_task set),
            # PAUSED (paused_by set) and STARTING (starting True while one pop
            # builds the session). Only one pop per mode can be STARTING.
            "starting": False,
            "countdown_deadline": None,   # time.monotonic() when the countdown ends
            "paused_remaining": None,     # seconds left on a countdown that was paused
        })

    def _rostered_session(self, guild_id: int, user_id: int):
        """The id of a running session this player is on a team in, or None.
        Bench players and spectators aren't rostered, so they're never blocked."""
        session_cog = self.bot.get_cog("SessionCog")
        if not session_cog:
            return None
        for session_id, s in getattr(session_cog, "active_sessions", {}).items():
            if s.get("guild_id") != guild_id:
                continue
            if any(user_id in info["on_field"] for info in s["teams"].values()):
                return session_id
        return None

    def _cancel_countdown(self, state: dict):
        """Stops a pending countdown. Never cancels the task that is running
        this code: a countdown that has finished and is starting the session
        must not cancel itself on the way (NTF-001)."""
        task = state.get("countdown_task")
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()
        state["countdown_task"] = None
        state["countdown_deadline"] = None

    async def _recheck_countdown(self, guild: discord.Guild, mode: str):
        """Called after someone leaves a mode. A countdown only keeps running
        while the queue is still full; otherwise it stops and the queue goes
        back to waiting. It starts again from the top once the queue refills."""
        state = self._mode_state(guild.id, mode)
        if state.get("countdown_task") is not None and len(state["ready"]) < config.QUEUE_CAP[mode]:
            self._cancel_countdown(state)
            await self._refresh_ready_message(
                guild, mode, note="⏳ Someone left, so the countdown stopped. It starts again when the queue is full."
            )
            await self._refresh_panel_message(guild.id)
            return True
        return False

    def _resolve_panel_channel_id(self, guild_id: int):
        """Falls back to the persisted queue_channel_id (set by /ntf_setup)
        if this cog's in-memory panel_channel cache is empty - which happens
        after every bot restart, since that cache is never saved to disk.
        Without this, actions like reset_channel would silently no-op after
        any redeploy until someone manually re-ran /queue_panel."""
        channel_id = self.panel_channel.get(guild_id)
        if channel_id:
            return channel_id
        cfg = db.get_guild_config(guild_id)
        if cfg and cfg.get("queue_channel_id"):
            self.panel_channel[guild_id] = cfg["queue_channel_id"]
            return cfg["queue_channel_id"]
        return None

    async def cog_load(self):
        self.bot.add_view(QueuePanelView(self))

    # -------------------------------------------------------------- panel
    @app_commands.command(name="queue_panel", description="[Admin] Post the NTF queue panel here — players ready up themselves")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def queue_panel(self, interaction: discord.Interaction):
        await interaction.response.send_message("Posting the queue panel…", ephemeral=True)
        await self.ensure_panel_in_channel(interaction.guild, interaction.channel)

    async def ensure_panel_in_channel(self, guild: discord.Guild, channel: discord.TextChannel):
        """Posts (or moves) the queue panel into the given channel. Used by
        both the manual /queue_panel command and the auto-provisioned
        #ntf-queue channel from /ntf_setup. Replaces the previous panel this
        bot is tracking, so running /queue_panel twice leaves one panel."""
        old = self.panel_message.get(guild.id)
        self.panel_channel[guild.id] = channel.id
        embed = self._build_panel_embed(guild.id)
        message = await channel.send(**ui.send_kwargs("queue", embed), view=QueuePanelView(self))
        self.panel_message[guild.id] = message
        if old is not None and old.id != message.id:
            try:
                await old.delete()
            except discord.HTTPException:
                pass
        return message

    def _protected_message_ids(self, guild_id: int) -> set:
        """Messages in the queue channel that clean-up must never delete: the
        permanent panel, both modes' live ready checks (including a paused
        one), and the announcements of sessions that are still running."""
        protected = set()
        panel = self.panel_message.get(guild_id)
        if panel is not None:
            protected.add(panel.id)
        for mode in ("rivals", "league"):
            message = self._mode_state(guild_id, mode).get("message")
            if message is not None:
                protected.add(message.id)
        session_cog = self.bot.get_cog("SessionCog")
        for s in getattr(session_cog, "active_sessions", {}).values() if session_cog else []:
            if s.get("guild_id") == guild_id:
                protected.update(s.get("announce_message_ids", ()))
        return protected

    async def reset_channel(self, guild: discord.Guild):
        """Tidies #ntf-queue after a session ends (NTF-007). Deletes only this
        bot's own messages that nothing still needs - never the live panel,
        a ready check (paused or counting down), another running session's
        announcements, pinned messages, or anything a person wrote. Reposts
        the panel only if it's missing (for example after a restart, when the
        bot no longer knows which message the panel is)."""
        channel_id = self._resolve_panel_channel_id(guild.id)
        if not channel_id:
            return
        channel = guild.get_channel(channel_id)
        if not channel:
            return
        bot_id = self.bot.user.id if getattr(self.bot, "user", None) else None
        if bot_id is None:
            return
        protected = self._protected_message_ids(guild.id)

        def removable(message):
            return message.author.id == bot_id and message.id not in protected and not message.pinned

        try:
            await channel.purge(limit=None, check=removable)
        except discord.HTTPException:
            log.warning("queue channel clean-up failed in guild %s", guild.id, exc_info=True)
        panel = self.panel_message.get(guild.id)
        if panel is None or getattr(panel, "deleted", False) or panel.channel.id != channel.id:
            self.panel_message.pop(guild.id, None)
            await self.ensure_panel_in_channel(guild, channel)

    def _mode_status_text(self, state: dict) -> str:
        if state.get("paused_by"):
            return "⏸️ Paused"
        if state.get("countdown_task") is not None or state.get("starting"):
            return "🚀 Starting"
        return "⏳ Waiting"

    def _build_panel_embed(self, guild_id: int):
        embed = discord.Embed(
            title="🎮 NTF Pro Clubs Queue",
            description=(
                "Pick a mode below to ready up. Once a queue fills, teams are drafted, "
                "club voice channels are created automatically, and you'll get "
                "dragged straight into your team's VC."
            ),
            color=ui.accent("neutral"),
        )
        for mode, heading, players_line in (
            ("rivals", "⚔️ Rivals", f"{config.starting_team_size('rivals')} per team"),
            ("league", "🏆 League", (
                f"{config.normal_team_size('league')} + "
                f"{config.team_cap('league') - config.normal_team_size('league')} on request"
            )),
        ):
            state = self._mode_state(guild_id, mode)
            ready, cap = len(state["ready"]), config.QUEUE_CAP[mode]
            embed.add_field(
                name=heading,
                value=(
                    f"**{ready}/{cap} ready**\n"
                    f"{ui.bar(ready, cap)}\n"
                    f"**Status:** {self._mode_status_text(state)}\n"
                    f"**Teams:** {config.MODE_TEAMS[mode]} teams\n"
                    f"**Players:** {players_line}\n"
                    f"**Force start:** {config.FORCE_START_MIN[mode]}+ ready"
                ),
                inline=True,
            )
        embed.set_footer(text="Connect to any voice channel before the queue pops — the bot can only move players already in voice.")
        return embed

    async def _refresh_panel_message(self, guild_id: int):
        msg = self.panel_message.get(guild_id)
        if not msg:
            return
        try:
            await msg.edit(**ui.edit_kwargs("queue", self._build_panel_embed(guild_id)))
        except discord.HTTPException:
            pass

    # -------------------------------------------------------- ready message
    def _build_ready_embed(self, mode: str, state: dict, note: str = None):
        cap = config.QUEUE_CAP[mode]
        ready = len(state["ready"])
        title = f"{'⚔️' if mode == 'rivals' else '🏆'} NTF {mode.title()} Ready Check"
        lines = [f"{PLATFORM_ICONS[state['platforms'][pid]]} <@{pid}>" for pid in state["ready"]]
        if state.get("paused_by"):
            note = (
                f"⏸️ **Queue paused by <@{state['paused_by']}>**\n"
                f"Nobody new can ready up while it's paused. The start countdown is stopped, "
                f"and players who have already readied stay in the queue."
            )
        elif note is None and state.get("countdown_deadline") is not None:
            left = max(1, round(state["countdown_deadline"] - time.monotonic()))
            note = f"🚀 **Queue full! Starting in about {left} seconds...**"
        desc = f"{ui.bar(ready, cap)}  **{ready}/{cap} ready**"
        if note:
            desc = f"{note}\n\n{desc}"
        embed = discord.Embed(title=title, description=desc, color=ui.accent(mode))
        embed.add_field(name=f"Readied up ({ready})", value="\n".join(lines) if lines else "*nobody yet*", inline=False)
        if mode in config.FORCE_START_MIN:
            embed.set_footer(text=f"Admins can Force Start once {config.FORCE_START_MIN[mode]}+ are ready.")
        return embed

    async def _refresh_ready_message(self, guild: discord.Guild, mode: str, note: str = None):
        state = self._mode_state(guild.id, mode)
        embed = self._build_ready_embed(mode, state, note)
        banner = f"ready_check_{mode}"
        channel_id = self._resolve_panel_channel_id(guild.id)
        channel = guild.get_channel(channel_id) if channel_id else None
        if channel is None:
            return

        async def post_new():
            state["view"] = ReadyMessageView(self, mode, paused=bool(state.get("paused_by")))
            state["message"] = await channel.send(**ui.send_kwargs(banner, embed), view=state["view"])

        if state["message"] is None:
            await post_new()
        else:
            view = state.get("view")
            if view:
                view.set_paused(bool(state.get("paused_by")))
            try:
                await state["message"].edit(**ui.edit_kwargs(banner, embed), view=view)
            except discord.HTTPException:
                await post_new()

    def _reset_mode_state(self, guild_id: int, mode: str):
        """Empties one mode's queue. Leaves the "starting" flag alone - the
        pop that is building a session owns it and clears it when done."""
        state = self._mode_state(guild_id, mode)
        self._cancel_countdown(state)
        task = state.get("timeout_task")
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()
        old_message = state["message"]
        state["ready"] = []
        state["platforms"] = {}
        state["message"] = None
        state["view"] = None
        state["timeout_task"] = None
        state["paused_by"] = None
        state["paused_remaining"] = None
        return old_message

    # ---------------------------------------------------------- join / ready
    async def handle_ready(self, interaction: discord.Interaction, mode: str, platform: str):
        guild = interaction.guild
        user = interaction.user
        db.ensure_player(guild.id, user.id, user.display_name)

        state = self._mode_state(guild.id, mode)
        other_mode = "league" if mode == "rivals" else "rivals"
        other_state = self._mode_state(guild.id, other_mode)

        if state.get("paused_by") and user.id not in state["ready"]:
            await interaction.response.send_message(
                f"⏸️ The {mode.title()} queue is paused by an admin — you can ready up once it's resumed.",
                ephemeral=True,
            )
            return

        if len(state["ready"]) >= config.QUEUE_CAP[mode] and user.id not in state["ready"]:
            await interaction.response.send_message("That queue just filled up — wait for the next one!", ephemeral=True)
            return

        # NTF-005: someone already playing on a team in a running session
        # can't queue for another one at the same time.
        if user.id not in state["ready"] and self._rostered_session(guild.id, user.id) is not None:
            await interaction.response.send_message(
                "You're on a team in a session that's still running, so you can't queue for another one yet. "
                "Ask your captain or an admin to remove you from that team first if you've left it.",
                ephemeral=True,
            )
            return

        if user.id in other_state["ready"]:
            other_state["ready"].remove(user.id)
            other_state["platforms"].pop(user.id, None)
            # switching modes can take the other queue below full mid-countdown
            if not await self._recheck_countdown(guild, other_mode):
                await self._refresh_ready_message(guild, other_mode)

        if user.id not in state["ready"]:
            state["ready"].append(user.id)
        state["platforms"][user.id] = platform

        await interaction.response.send_message(
            f"✅ Readied up for **{mode.title()}** as {PLATFORM_ICONS[platform]} {platform} "
            f"({len(state['ready'])}/{config.QUEUE_CAP[mode]}).",
            ephemeral=True,
        )
        await self._refresh_ready_message(guild, mode)
        await self._refresh_panel_message(guild.id)

        if (len(state["ready"]) >= config.QUEUE_CAP[mode] and state["countdown_task"] is None
                and not state.get("paused_by") and not state.get("starting")):
            await self._begin_countdown(guild, mode)

    async def handle_pause(self, interaction: discord.Interaction, mode: str):
        """Admin-only. Pausing freezes ONE mode's queue exactly as it is:
        nobody new can ready up and a running start countdown is stopped,
        but everyone already readied stays in. League and Rivals pause
        separately.

        Resuming (NTF-004): if the queue is still full, the countdown carries
        on from where it was paused (at least 5 seconds, so nobody is caught
        out). If anyone left while it was paused, the queue isn't full any
        more, so there's no countdown; it starts again from the full 15
        seconds once the queue refills."""
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("Only an admin can pause or resume the queue.", ephemeral=True)
            return
        guild = interaction.guild
        state = self._mode_state(guild.id, mode)
        if state.get("starting"):
            await interaction.response.send_message(
                f"The {mode.title()} session is already being set up, so the queue can't be paused right now.", ephemeral=True
            )
            return
        if state.get("paused_by"):
            state["paused_by"] = None
            remaining = state.get("paused_remaining")
            state["paused_remaining"] = None
            await interaction.response.send_message(f"▶️ {mode.title()} queue resumed.", ephemeral=True)
            await self._refresh_ready_message(guild, mode)
            await self._refresh_panel_message(guild.id)
            if len(state["ready"]) >= config.QUEUE_CAP[mode] and state["countdown_task"] is None:
                seconds = max(5, round(remaining)) if remaining else config.READY_COUNTDOWN_SECONDS
                await self._begin_countdown(guild, mode, seconds=min(seconds, config.READY_COUNTDOWN_SECONDS))
            return

        state["paused_by"] = interaction.user.id
        deadline = state.get("countdown_deadline")
        state["paused_remaining"] = max(0.0, deadline - time.monotonic()) if deadline is not None else None
        self._cancel_countdown(state)
        await interaction.response.send_message(
            f"⏸️ {mode.title()} queue paused. Players already readied stay in; press Resume Queue to carry on.",
            ephemeral=True,
        )
        await self._refresh_ready_message(guild, mode)
        await self._refresh_panel_message(guild.id)

    async def handle_leave(self, interaction: discord.Interaction):
        guild = interaction.guild
        removed_from = None
        for mode in ("rivals", "league"):
            state = self._mode_state(guild.id, mode)
            if interaction.user.id in state["ready"]:
                state["ready"].remove(interaction.user.id)
                state["platforms"].pop(interaction.user.id, None)
                removed_from = mode
        if removed_from:
            await interaction.response.send_message("👋 You're no longer readied up.", ephemeral=True)
            # NTF-002: leaving mid-countdown stops it if the queue is no longer full
            if not await self._recheck_countdown(guild, removed_from):
                await self._refresh_ready_message(guild, removed_from)
                await self._refresh_panel_message(guild.id)
        else:
            await interaction.response.send_message("You're not in a queue.", ephemeral=True)

    async def handle_show(self, interaction: discord.Interaction):
        guild = interaction.guild
        embed = discord.Embed(title="📋 Current Queues", color=discord.Color.blurple())
        for mode in ("rivals", "league"):
            state = self._mode_state(guild.id, mode)
            lines = [f"{PLATFORM_ICONS[state['platforms'][pid]]} <@{pid}>" for pid in state["ready"]]
            embed.add_field(
                name=f"{mode.title()} ({len(state['ready'])}/{config.QUEUE_CAP[mode]})",
                value="\n".join(lines) if lines else "*empty*",
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ------------------------------------------------------------- popping
    async def _begin_countdown(self, guild: discord.Guild, mode: str, seconds: float = None):
        state = self._mode_state(guild.id, mode)
        seconds = config.READY_COUNTDOWN_SECONDS if seconds is None else seconds
        task = state.get("timeout_task")
        if task and not task.done():
            task.cancel()
            state["timeout_task"] = None
        state["countdown_deadline"] = time.monotonic() + seconds
        state["countdown_task"] = asyncio.create_task(self._countdown_then_start(guild, mode, seconds))
        await self._refresh_ready_message(guild, mode)
        await self._refresh_panel_message(guild.id)

    def _drop_rostered(self, guild_id: int, mode: str) -> list:
        """Takes anyone who has since been put on a team in a running session
        out of this queue. Returns who was dropped."""
        state = self._mode_state(guild_id, mode)
        dropped = [pid for pid in state["ready"] if self._rostered_session(guild_id, pid) is not None]
        for pid in dropped:
            state["ready"].remove(pid)
            state["platforms"].pop(pid, None)
        return dropped

    async def _countdown_then_start(self, guild: discord.Guild, mode: str, seconds: float):
        try:
            await asyncio.sleep(seconds)
            state = self._mode_state(guild.id, mode)
            if state.get("countdown_task") is not asyncio.current_task():
                return  # stopped or replaced while it was sleeping
            state["countdown_task"] = None
            state["countdown_deadline"] = None
            if state.get("paused_by") or state.get("starting"):
                return
            # NTF-002: check the queue is still full right before starting.
            # An undersized session only ever starts through Force Start.
            self._drop_rostered(guild.id, mode)
            if len(state["ready"]) < config.QUEUE_CAP[mode]:
                await self._refresh_ready_message(
                    guild, mode, note="⏳ The queue isn't full any more, so it didn't start. Waiting for players."
                )
                await self._refresh_panel_message(guild.id)
                return
            snapshot = list(state["ready"])[: config.QUEUE_CAP[mode]]
            await self._pop_and_start(guild, mode, snapshot)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("queue countdown failed for mode=%s in guild=%s", mode, guild.id)

    async def handle_force_start(self, interaction: discord.Interaction, mode: str):
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("Only an admin can force start.", ephemeral=True)
            return
        state = self._mode_state(interaction.guild_id, mode)
        if state.get("starting"):
            await interaction.response.send_message(f"The {mode.title()} session is already starting.", ephemeral=True)
            return
        if state.get("paused_by"):
            await interaction.response.send_message("The queue is paused — resume it before force starting.", ephemeral=True)
            return
        minimum = config.FORCE_START_MIN.get(mode)
        eligible = [pid for pid in state["ready"] if self._rostered_session(interaction.guild_id, pid) is None]
        if minimum is None or len(eligible) < minimum:
            await interaction.response.send_message(f"Need at least {minimum} ready players to force start.", ephemeral=True)
            return
        await interaction.response.send_message(f"⚡ Force starting {mode.title()} with {len(eligible)} players…", ephemeral=True)
        # everyone who's ready at this moment and not already playing elsewhere
        snapshot = [pid for pid in state["ready"] if self._rostered_session(interaction.guild_id, pid) is None]
        if len(snapshot) < minimum:
            await interaction.followup.send("Someone left while that was going through, so it didn't start.", ephemeral=True)
            return
        started = await self._pop_and_start(interaction.guild, mode, snapshot)
        if started is None:
            await interaction.followup.send(f"The {mode.title()} session was already starting.", ephemeral=True)

    async def _pop_and_start(self, guild: discord.Guild, mode: str, player_ids: list):
        """The one place a queue turns into a session (NTF-003). Returns True
        if the session started, False if it failed, or None if another pop for
        this mode was already starting (the automatic countdown and a Force
        Start, or two admins pressing Force Start together). If starting the
        session fails, everyone is put back in the queue and the queue is
        paused, so nobody loses their place and it can't fail in a loop."""
        state = self._mode_state(guild.id, mode)
        if state.get("starting"):
            return None
        state["starting"] = True  # set before the first await, so no other pop can slip in
        try:
            platforms = {pid: state["platforms"].get(pid, "Console") for pid in player_ids}
            players = []
            for pid in player_ids:
                p = db.get_player(guild.id, pid)
                if p:
                    mode_stats = db.get_player_mode_stats(guild.id, pid, mode)
                    players.append({**p, "mmr": mode_stats["mmr"], "wins": mode_stats["wins"], "losses": mode_stats["losses"]})

            session_cog = self.bot.get_cog("SessionCog")
            old_message = self._reset_mode_state(guild.id, mode)
            if old_message:
                try:
                    await old_message.delete()
                except discord.HTTPException:
                    pass
            await self._refresh_panel_message(guild.id)

            channel_id = self._resolve_panel_channel_id(guild.id)
            channel = guild.get_channel(channel_id) if channel_id else None
            if session_cog is None:
                log.error("SessionCog not loaded - cannot start a %s session in guild %s", mode, guild.id)
                await self._restore_after_failed_start(guild, mode, player_ids, platforms)
                if channel:
                    await channel.send("⚠️ Session cog not loaded — cannot start the match. Everyone is back in the queue.")
                return False

            try:
                await session_cog.start_session(guild, mode, players, channel_id)
                return True
            except Exception:
                # Logged in full and posted where people can see it, so a
                # failed start is never silent.
                log.error("Failed to start session for mode=%s in guild=%s:\n%s", mode, guild.id, traceback.format_exc())
                restored = await self._restore_after_failed_start(guild, mode, player_ids, platforms)
                if channel:
                    try:
                        await channel.send(
                            f"🚨 **{mode.title()} queue popped but the session failed to start.** "
                            f"{restored} player(s) have been put back in the queue, and the queue is paused so it "
                            f"doesn't fail again straight away. An admin can press **Resume Queue** to try again "
                            f"(the bot's logs have the exact error)."
                        )
                    except discord.HTTPException:
                        pass
                return False
        finally:
            state["starting"] = False

    async def _restore_after_failed_start(self, guild: discord.Guild, mode: str, player_ids: list, platforms: dict) -> int:
        """Puts the players from a failed start back at the front of the
        queue, in their original order, and pauses it. Skips anyone who has
        joined the other queue since or ended up on a team anyway."""
        state = self._mode_state(guild.id, mode)
        other = self._mode_state(guild.id, "league" if mode == "rivals" else "rivals")
        back = [
            pid for pid in player_ids
            if pid not in state["ready"] and pid not in other["ready"] and self._rostered_session(guild.id, pid) is None
        ]
        state["ready"] = back + [pid for pid in state["ready"] if pid not in back]
        for pid in back:
            state["platforms"][pid] = platforms.get(pid, "Console")
        self._cancel_countdown(state)
        bot_user = getattr(self.bot, "user", None)
        state["paused_by"] = bot_user.id if bot_user else 0
        state["paused_remaining"] = None
        try:
            await self._refresh_ready_message(guild, mode)
            await self._refresh_panel_message(guild.id)
        except discord.HTTPException:
            pass
        return len(back)

    # -------------------------------------------------------------- close
    async def _close_queue(self, guild: discord.Guild, mode: str, reason: str, closed_by: discord.Member = None):
        state = self._mode_state(guild.id, mode)
        count = len(state["ready"])
        old_message = self._reset_mode_state(guild.id, mode)
        if old_message:
            try:
                await old_message.delete()
            except discord.HTTPException:
                pass
        await self._refresh_panel_message(guild.id)

        channel_id = self._resolve_panel_channel_id(guild.id)
        channel = guild.get_channel(channel_id) if channel_id else None
        if channel:
            who = f" by {closed_by.mention}" if closed_by else ""
            extra = " Try Rivals instead!" if mode == "league" else ""
            await channel.send(f"🔒 **{mode.title()} queue closed**{who} ({count} players cleared) — {reason}.{extra}")

    @app_commands.command(name="close_queue", description="[Admin] Immediately close a queue and clear it out")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def close_queue(self, interaction: discord.Interaction, mode: Literal["rivals", "league"]):
        state = self._mode_state(interaction.guild_id, mode)
        if not state["ready"] and state["message"] is None:
            await interaction.response.send_message(f"The {mode} queue is already empty.", ephemeral=True)
            return
        await interaction.response.send_message(f"Closing the {mode} queue…", ephemeral=True)
        await self._close_queue(interaction.guild, mode, reason="closed by an admin", closed_by=interaction.user)


async def setup(bot):
    await bot.add_cog(QueueCog(bot))

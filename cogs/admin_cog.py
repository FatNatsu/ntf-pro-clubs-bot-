import discord
from discord import app_commands
from discord.ext import commands
from typing import Literal

import config
import database as db
import mmr
import mmr as mmr_module  # alias used specifically where a local variable/param would shadow the plain `mmr` name
import voice_utils
import leaderboard_utils


class ConfirmSeasonResetView(discord.ui.View):
    def __init__(self, bot, guild_id: int, mode: str):
        super().__init__(timeout=30)
        self.bot = bot
        self.guild_id = guild_id
        self.mode = mode

    @discord.ui.button(label="Yes, reset the season", style=discord.ButtonStyle.danger, emoji="⚠️")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        db.reset_mode_leaderboard(self.guild_id, self.mode)
        clubs_reset = db.reset_club_records(self.guild_id, self.mode)
        await leaderboard_utils.refresh_leaderboard_channel(self.bot, interaction.guild)
        await interaction.response.edit_message(
            content=f"✅ **{self.mode.title()}** season reset — every player's {self.mode} MMR is back to "
                    f"{config.STARTING_MMR} with a clean record, and {clubs_reset} **{self.mode}** club result(s) "
                    f"were cleared too. The other mode's player and club records are completely untouched — run "
                    f"this again for that mode if you want that reset too.",
            view=None,
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Cancelled — no changes made.", view=None)


class AdminCog(commands.Cog):
    """Club name pool + captain whitelist + result overrides.
    Everything here is scoped to the server you run it in, and requires
    Manage Server permission."""

    def __init__(self, bot):
        self.bot = bot

    club_group = app_commands.Group(name="club", description="Manage this server's pool of club names used for team VCs")
    captain_group = app_commands.Group(name="captain", description="Manage this server's captain whitelist")
    na_group = app_commands.Group(name="na", description="Manage this server's NA whitelist - keeps NA players together on a team")
    ghost_group = app_commands.Group(name="ghost", description="Manage this server's ghost whitelist - lets them move between VCs freely during a session")
    girl_group = app_commands.Group(name="girl", description="Manage this server's girl whitelist - keeps them together on the same team")

    # ------------------------------------------------------------- one-time setup
    @app_commands.command(name="ntf_setup", description="[Admin] Create this server's permanent #ntf-queue, #in-progress and #leaderboard channels")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def ntf_setup(self, interaction: discord.Interaction):
        guild = interaction.guild
        cfg = db.get_guild_config(guild.id) or {}

        queue_channel = guild.get_channel(cfg.get("queue_channel_id")) if cfg.get("queue_channel_id") else None
        queue_channel_is_new = queue_channel is None
        if queue_channel is None:
            queue_channel = await voice_utils.create_queue_channel(guild)

        progress_channel = guild.get_channel(cfg.get("progress_channel_id")) if cfg.get("progress_channel_id") else None
        if progress_channel is None:
            progress_channel = await voice_utils.create_progress_text_channel(guild, category=None)

        leaderboard_channel = guild.get_channel(cfg.get("leaderboard_channel_id")) if cfg.get("leaderboard_channel_id") else None
        if leaderboard_channel is None:
            leaderboard_channel = await voice_utils.create_leaderboard_channel(guild)

        history_channel = guild.get_channel(cfg.get("history_channel_id")) if cfg.get("history_channel_id") else None
        if history_channel is None:
            history_channel = await voice_utils.create_history_channel(guild)

        admin_log_channel = guild.get_channel(cfg.get("admin_log_channel_id")) if cfg.get("admin_log_channel_id") else None
        if admin_log_channel is None:
            admin_log_channel = await voice_utils.create_admin_log_channel(guild)

        db.upsert_guild_config(
            guild.id,
            queue_channel_id=queue_channel.id,
            progress_channel_id=progress_channel.id,
            leaderboard_channel_id=leaderboard_channel.id,
            history_channel_id=history_channel.id,
            admin_log_channel_id=admin_log_channel.id,
        )

        if queue_channel_is_new:
            queue_cog = self.bot.get_cog("QueueCog")
            if queue_cog:
                await queue_cog.ensure_panel_in_channel(guild, queue_channel)

        await progress_channel.send("🚫 No games are currently in progress.")
        await leaderboard_utils.refresh_leaderboard_channel(self.bot, guild)

        await interaction.response.send_message(
            f"✅ NTF is set up for this server — {queue_channel.mention}, {progress_channel.mention}, "
            f"{leaderboard_channel.mention}, {history_channel.mention}, and {admin_log_channel.mention} are ready. "
            f"⚠️ **One manual step needed**: {admin_log_channel.mention} is hidden from everyone by default since "
            f"Discord has no automatic way to detect who has admin permissions — go into that channel's settings "
            f"and give your staff/mod role permission to view it. These channels (and your "
            f"clubs/captains/leaderboard) are separate per server, so other servers NTF is in won't see this data.",
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

    @club_group.command(name="purge_history", description="[Admin] Permanently wipe a club's match history (e.g. old test/placeholder names)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def club_purge_history(self, interaction: discord.Interaction, name: str):
        deleted = db.purge_club_history(interaction.guild_id, name)
        if deleted == 0:
            await interaction.response.send_message(f"No history found for **{name}** — nothing to purge.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"🧹 Purged **{name}**'s match history ({deleted} record(s) removed). "
            f"It'll no longer show up in `/club_stats` or its autocomplete. "
            f"If it's still in your club pool, use `/club remove` separately to take it out of rotation too.",
            ephemeral=True,
        )

    @club_purge_history.autocomplete("name")
    async def club_purge_history_autocomplete(self, interaction: discord.Interaction, current: str):
        names = db.get_all_known_club_names(interaction.guild_id)
        matches = [n for n in names if current.lower() in n.lower()]
        return [app_commands.Choice(name=n, value=n) for n in matches[:25]]

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
        lines = []
        for c in caps:
            rivals_mmr = db.get_player_mode_stats(interaction.guild_id, c["discord_id"], "rivals")["mmr"]
            league_mmr = db.get_player_mode_stats(interaction.guild_id, c["discord_id"], "league")["mmr"]
            lines.append(f"• <@{c['discord_id']}> (Rivals {rivals_mmr} / League {league_mmr})")
        await interaction.response.send_message("🎖️ **Captain whitelist:**\n" + "\n".join(lines), ephemeral=True)

    # ---------------------------------------------------------------- NA whitelist
    @na_group.command(name="add", description="Mark a player as NA - the draft will try to keep NA players on the same team")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def na_add(self, interaction: discord.Interaction, member: discord.Member):
        db.ensure_player(interaction.guild_id, member.id, member.display_name)
        db.set_na(interaction.guild_id, member.id, True)
        await interaction.response.send_message(f"🌎 {member.mention} is now marked NA in this server.", ephemeral=True)

    @na_group.command(name="remove", description="Remove a player from the NA whitelist")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def na_remove(self, interaction: discord.Interaction, member: discord.Member):
        db.set_na(interaction.guild_id, member.id, False)
        await interaction.response.send_message(f"Removed {member.mention} from the NA whitelist.", ephemeral=True)

    @na_group.command(name="list", description="List all NA-whitelisted players in this server")
    async def na_list(self, interaction: discord.Interaction):
        na_players = db.get_na_players(interaction.guild_id)
        if not na_players:
            await interaction.response.send_message("No NA players whitelisted yet in this server.", ephemeral=True)
            return
        lines = [f"• <@{p['discord_id']}>" for p in na_players]
        await interaction.response.send_message("🌎 **NA whitelist:**\n" + "\n".join(lines), ephemeral=True)

    # ---------------------------------------------------------------- ghost whitelist
    @ghost_group.command(name="add", description="Mark a player as a ghost - free VC movement during sessions, no roster/session-control access")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def ghost_add(self, interaction: discord.Interaction, member: discord.Member):
        db.ensure_player(interaction.guild_id, member.id, member.display_name)
        db.set_ghost(interaction.guild_id, member.id, True)
        await interaction.response.send_message(f"👻 {member.mention} is now a ghost in this server.", ephemeral=True)

    @ghost_group.command(name="remove", description="Remove a player from the ghost whitelist")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def ghost_remove(self, interaction: discord.Interaction, member: discord.Member):
        db.set_ghost(interaction.guild_id, member.id, False)
        await interaction.response.send_message(f"Removed {member.mention} from the ghost whitelist.", ephemeral=True)

    @ghost_group.command(name="list", description="List all ghost-whitelisted players in this server")
    async def ghost_list(self, interaction: discord.Interaction):
        ghosts = db.get_ghosts(interaction.guild_id)
        if not ghosts:
            await interaction.response.send_message("No ghosts whitelisted yet in this server.", ephemeral=True)
            return
        lines = [f"• <@{p['discord_id']}>" for p in ghosts]
        await interaction.response.send_message("👻 **Ghost whitelist:**\n" + "\n".join(lines), ephemeral=True)

    # ---------------------------------------------------------------- girl whitelist
    @girl_group.command(name="add", description="Mark a player as girl-whitelisted - the draft will try to keep them on the same team")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def girl_add(self, interaction: discord.Interaction, member: discord.Member):
        db.ensure_player(interaction.guild_id, member.id, member.display_name)
        db.set_girl(interaction.guild_id, member.id, True)
        await interaction.response.send_message(f"🎀 {member.mention} is now girl-whitelisted in this server.", ephemeral=True)

    @girl_group.command(name="remove", description="Remove a player from the girl whitelist")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def girl_remove(self, interaction: discord.Interaction, member: discord.Member):
        db.set_girl(interaction.guild_id, member.id, False)
        await interaction.response.send_message(f"Removed {member.mention} from the girl whitelist.", ephemeral=True)

    @girl_group.command(name="list", description="List all girl-whitelisted players in this server")
    async def girl_list(self, interaction: discord.Interaction):
        girls = db.get_girls(interaction.guild_id)
        if not girls:
            await interaction.response.send_message("No girl-whitelisted players yet in this server.", ephemeral=True)
            return
        lines = [f"• <@{p['discord_id']}>" for p in girls]
        await interaction.response.send_message("🎀 **Girl whitelist:**\n" + "\n".join(lines), ephemeral=True)

    # ---------------------------------------------------------------- overrides
    @app_commands.command(name="admin_fix_mmr", description="Manually set a player's MMR in one mode in this server (corrections only)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def admin_fix_mmr(self, interaction: discord.Interaction, member: discord.Member, mode: Literal["rivals", "league"], mmr: int):
        db.ensure_player(interaction.guild_id, member.id, member.display_name)
        db.set_mode_mmr(interaction.guild_id, member.id, mode, mmr)
        await interaction.response.send_message(f"Set {member.mention}'s **{mode}** MMR to {mmr}.", ephemeral=True)
        await leaderboard_utils.refresh_leaderboard_channel(self.bot, interaction.guild)

    @app_commands.command(name="add_wins", description="[Admin] Add wins to a player's record in one mode, adjusting their MMR to match")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def add_wins(self, interaction: discord.Interaction, member: discord.Member, mode: Literal["rivals", "league"], amount: int):
        db.ensure_player(interaction.guild_id, member.id, member.display_name)
        stats = db.get_player_mode_stats(interaction.guild_id, member.id, mode)
        old_mmr = stats["mmr"]
        new_mmr = mmr_module.simulate_correction(old_mmr, abs(amount), "win", +1)
        db.set_mode_mmr(interaction.guild_id, member.id, mode, new_mmr)
        new_wins = db.add_mode_wins(interaction.guild_id, member.id, mode, amount)
        await leaderboard_utils.refresh_leaderboard_channel(self.bot, interaction.guild)
        await interaction.response.send_message(
            f"➕ Added {abs(amount)} **{mode}** win(s) to {member.mention}. New win count: **{new_wins}**. "
            f"MMR adjusted {old_mmr} → **{new_mmr}** ({mmr_module.rank_for_mmr(new_mmr)}) to match.",
            ephemeral=True,
        )

    @app_commands.command(name="add_losses", description="[Admin] Add losses to a player's record in one mode, adjusting their MMR to match")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def add_losses(self, interaction: discord.Interaction, member: discord.Member, mode: Literal["rivals", "league"], amount: int):
        db.ensure_player(interaction.guild_id, member.id, member.display_name)
        stats = db.get_player_mode_stats(interaction.guild_id, member.id, mode)
        old_mmr = stats["mmr"]
        new_mmr = mmr_module.simulate_correction(old_mmr, abs(amount), "loss", -1)
        db.set_mode_mmr(interaction.guild_id, member.id, mode, new_mmr)
        new_losses = db.add_mode_losses(interaction.guild_id, member.id, mode, amount)
        await leaderboard_utils.refresh_leaderboard_channel(self.bot, interaction.guild)
        await interaction.response.send_message(
            f"➕ Added {abs(amount)} **{mode}** loss(es) to {member.mention}. New loss count: **{new_losses}**. "
            f"MMR adjusted {old_mmr} → **{new_mmr}** ({mmr_module.rank_for_mmr(new_mmr)}) to match.",
            ephemeral=True,
        )

    @app_commands.command(name="deduct_wins", description="[Admin] Deduct wins from a player's record in one mode, adjusting their MMR to match")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def deduct_wins(self, interaction: discord.Interaction, member: discord.Member, mode: Literal["rivals", "league"], amount: int):
        db.ensure_player(interaction.guild_id, member.id, member.display_name)
        stats = db.get_player_mode_stats(interaction.guild_id, member.id, mode)
        old_mmr = stats["mmr"]
        new_mmr = mmr_module.simulate_correction(old_mmr, abs(amount), "win", -1)
        db.set_mode_mmr(interaction.guild_id, member.id, mode, new_mmr)
        new_wins = db.deduct_mode_wins(interaction.guild_id, member.id, mode, amount)
        await leaderboard_utils.refresh_leaderboard_channel(self.bot, interaction.guild)
        await interaction.response.send_message(
            f"➖ Deducted {abs(amount)} **{mode}** win(s) from {member.mention}. New win count: **{new_wins}**. "
            f"MMR adjusted {old_mmr} → **{new_mmr}** ({mmr_module.rank_for_mmr(new_mmr)}) to match.",
            ephemeral=True,
        )

    @app_commands.command(name="deduct_losses", description="[Admin] Deduct losses from a player's record in one mode, adjusting their MMR to match")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def deduct_losses(self, interaction: discord.Interaction, member: discord.Member, mode: Literal["rivals", "league"], amount: int):
        db.ensure_player(interaction.guild_id, member.id, member.display_name)
        stats = db.get_player_mode_stats(interaction.guild_id, member.id, mode)
        old_mmr = stats["mmr"]
        new_mmr = mmr_module.simulate_correction(old_mmr, abs(amount), "loss", +1)
        db.set_mode_mmr(interaction.guild_id, member.id, mode, new_mmr)
        new_losses = db.deduct_mode_losses(interaction.guild_id, member.id, mode, amount)
        await leaderboard_utils.refresh_leaderboard_channel(self.bot, interaction.guild)
        await interaction.response.send_message(
            f"➖ Deducted {abs(amount)} **{mode}** loss(es) from {member.mention}. New loss count: **{new_losses}**. "
            f"MMR adjusted {old_mmr} → **{new_mmr}** ({mmr_module.rank_for_mmr(new_mmr)}) to match.",
            ephemeral=True,
        )

    @app_commands.command(name="deduct_mmr", description="[Admin] Deduct a set amount of MMR from a player in one mode (e.g. for a ban)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def deduct_mmr(self, interaction: discord.Interaction, member: discord.Member, mode: Literal["rivals", "league"], amount: int):
        db.ensure_player(interaction.guild_id, member.id, member.display_name)
        stats = db.get_player_mode_stats(interaction.guild_id, member.id, mode)
        current = stats["mmr"]
        new_mmr = max(0, current - abs(amount))
        db.set_mode_mmr(interaction.guild_id, member.id, mode, new_mmr)
        await leaderboard_utils.refresh_leaderboard_channel(self.bot, interaction.guild)
        await interaction.response.send_message(
            f"➖ Deducted {abs(amount)} **{mode}** MMR from {member.mention}. New MMR: **{new_mmr}** "
            f"({mmr_module.rank_for_mmr(new_mmr)}).",
            ephemeral=True,
        )

    @app_commands.command(name="register_everyone", description="[Admin] Add every current server member to the leaderboard at the starting MMR")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def register_everyone(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        added = 0
        async for member in guild.fetch_members(limit=None):
            if member.bot:
                continue
            if db.get_player(guild.id, member.id) is None:
                db.ensure_player(guild.id, member.id, member.display_name)
                added += 1
            db.ensure_player_mode_stats(guild.id, member.id, "rivals")
            db.ensure_player_mode_stats(guild.id, member.id, "league")
        await leaderboard_utils.refresh_leaderboard_channel(self.bot, guild)
        await interaction.followup.send(
            f"✅ Added {added} new member(s) to both leaderboards at {config.STARTING_MMR} MMR. "
            f"Everyone else was already tracked.",
            ephemeral=True,
        )

    @app_commands.command(name="prune_left_members", description="[Admin] Remove tracked players who are no longer in this server")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def prune_left_members(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        active_ids = set()
        async for member in guild.fetch_members(limit=None):
            active_ids.add(member.id)
        removed = db.prune_left_members(guild.id, active_ids)
        db.prune_left_members_mode_stats(guild.id, active_ids)
        await leaderboard_utils.refresh_leaderboard_channel(self.bot, guild)
        await interaction.followup.send(
            f"🧹 Removed {removed} player(s) who are no longer in this server. Their match/club history "
            f"stays intact for reference — only their leaderboard entries (both modes) were cleared.",
            ephemeral=True,
        )

    @app_commands.command(name="season_reset", description="[Admin] Reset every player's MMR and W-L back to the start for a new season")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def season_reset(self, interaction: discord.Interaction, mode: Literal["rivals", "league"]):
        await interaction.response.send_message(
            f"⚠️ This resets **every player's {mode} MMR** in this server back to {config.STARTING_MMR} with a "
            f"clean win/loss record for {mode} specifically, AND wipes every **club's {mode} win/loss record** "
            f"too. The other mode's player and club records are completely untouched — run this again for that "
            f"mode separately if you want it reset too. Player match history (recent form, best club, "
            f"most-played-with) stays intact — only current standings reset, not the historical log. This "
            f"can't be undone. Continue?",
            view=ConfirmSeasonResetView(self.bot, interaction.guild_id, mode),
            ephemeral=True,
        )

    @app_commands.command(name="debug_clear_test_data", description="[Admin] TEST ONLY: remove fake test-bot accounts from the leaderboard")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def debug_clear_test_data(self, interaction: discord.Interaction):
        removed = db.clear_test_players(interaction.guild_id)
        await leaderboard_utils.refresh_leaderboard_channel(self.bot, interaction.guild)
        await interaction.response.send_message(
            f"🧹 Removed {removed} fake test-bot account(s) from this server's leaderboard. "
            f"Real players were untouched.\n\n"
            f"Note: club win/loss records (`/club_stats`) may still include results from test "
            f"sessions, since those are tracked by club name rather than by player — worth "
            f"keeping in mind if you ran tests against your real club pool.",
            ephemeral=True,
        )

    @app_commands.command(name="debug_test_session", description="[Admin] TEST ONLY: start a session filled with fake players so you can test solo")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def debug_test_session(self, interaction: discord.Interaction, mode: Literal["rivals", "league"],
                                  second_tester: discord.Member = None, make_second_tester_captain: bool = False,
                                  start_on_bench: bool = False):
        guild = interaction.guild
        guild_id = guild.id
        cap = config.QUEUE_CAP[mode]

        if interaction.user.voice is None or interaction.user.voice.channel is None:
            await interaction.response.send_message(
                "Join any voice channel first — Discord can only drag players who are already connected, "
                "same as a real queue pop.",
                ephemeral=True,
            )
            return

        if second_tester and (second_tester.voice is None or second_tester.voice.channel is None):
            await interaction.response.send_message(
                f"{second_tester.mention} needs to join a voice channel first too, or they won't get dragged "
                f"into their team's VC.",
                ephemeral=True,
            )
            return

        # You fill one real seat; a second real account can optionally fill
        # another (useful for testing things that need two real Discord
        # users, like "captains can join any team's VC but regular players
        # can't move themselves"). Everything else is a synthetic test
        # account (negative IDs, guaranteed never to collide with a real
        # Discord snowflake) so the team-draft/round/MMR/close flow can run
        # without needing a full queue of real people.
        real_id = interaction.user.id
        db.ensure_player(guild_id, real_id, interaction.user.display_name)

        real_ids = [real_id]
        if second_tester:
            db.ensure_player(guild_id, second_tester.id, second_tester.display_name)
            real_ids.append(second_tester.id)

        fake_count = cap - len(real_ids)
        fake_ids = list(range(-1, -(fake_count + 1), -1))  # -1, -2, ... -fake_count
        for i, fid in enumerate(fake_ids, start=1):
            db.ensure_player(guild_id, fid, f"🤖 Test Bot {i}")

        players = []
        for pid in real_ids + fake_ids:
            p = db.get_player(guild_id, pid)
            if p:
                mode_stats = db.get_player_mode_stats(guild_id, pid, mode)
                players.append({**p, "mmr": mode_stats["mmr"], "wins": mode_stats["wins"], "losses": mode_stats["losses"]})

        # Force the second tester to be treated as captain-eligible for the
        # draft - this is an in-memory-only override on the player dict
        # passed into team_balance, it does NOT touch their persistent
        # captain-whitelist flag in the database.
        if second_tester and make_second_tester_captain:
            for p in players:
                if p["discord_id"] == second_tester.id:
                    p["is_captain"] = 1
                    break

        session_cog = self.bot.get_cog("SessionCog")
        if not session_cog:
            await interaction.response.send_message("Session cog isn't loaded — can't start a test session.", ephemeral=True)
            return

        second_note = ""
        if second_tester:
            role_note = " (forced as a captain candidate)" if make_second_tester_captain else ""
            second_note = f" and {second_tester.mention}{role_note}"

        await interaction.response.send_message(
            f"🧪 Starting a **test {mode}** session — you{second_note} + {len(fake_ids)} fake test bots "
            f"({len(players)} total). As an admin you can report results for **either side** of every match "
            f"yourself. Fake players will show as broken mentions — that's expected.",
            ephemeral=True,
        )
        session_id = await session_cog.start_session(guild, mode, players, interaction.channel_id, is_test=True)

        state = session_cog.active_sessions.get(session_id)
        if state and start_on_bench:
            # Only move you to the Bench if you explicitly asked to test
            # BEING pulled in as a sub. Leave this off (the default) to test
            # the OTHER side instead - clicking Add Sub as a captain/admin to
            # pull someone else in - since clicking that button doesn't
            # require you to be anywhere in particular, only having
            # permission to click it.
            assigned_team_id = None
            for team_id, info in state["teams"].items():
                if real_id in info["on_field"]:
                    assigned_team_id = team_id
                    break

            if assigned_team_id is not None:
                state["teams"][assigned_team_id]["on_field"].discard(real_id)
                db.set_member_role(assigned_team_id, real_id, "sub")
                bench_channel = guild.get_channel(state["bench_channel_id"])
                # We're intentionally un-rostering them for this test, so
                # explicitly re-open Bench access - normally a rostered
                # player is locked out of Bench once seated on a team.
                real_member = guild.get_member(real_id)
                if real_member:
                    await voice_utils.allow_member_in_channel(bench_channel, real_member, connect=True)
                await voice_utils.move_member_to_channel(guild, real_id, bench_channel)
                await interaction.followup.send(
                    f"🪑 You've been moved to the Bench so you can test BEING subbed in — "
                    f"click **any team's** Add Sub button (as an admin, from any device/account) to pull "
                    f"yourself onto that team.",
                    ephemeral=True,
                )
        elif state:
            for team_id, info in state["teams"].items():
                if real_id in info["on_field"]:
                    await interaction.followup.send(
                        f"📍 You were drafted onto **{info['club_name']}**. As an admin you can click "
                        f"**any** team's Add Sub button right now to test pulling someone in — you don't "
                        f"need to be physically anywhere for that to work. If instead you want to test "
                        f"BEING pulled in as a sub, re-run this with `start_on_bench:True`.",
                        ephemeral=True,
                    )
                    break


async def setup(bot):
    await bot.add_cog(AdminCog(bot))

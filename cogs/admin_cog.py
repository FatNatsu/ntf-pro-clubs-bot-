import discord
from discord import app_commands
from discord.ext import commands
from typing import Literal

import config
import database as db
import mmr
import voice_utils
import leaderboard_utils


class ConfirmSeasonResetView(discord.ui.View):
    def __init__(self, bot, guild_id: int):
        super().__init__(timeout=30)
        self.bot = bot
        self.guild_id = guild_id

    @discord.ui.button(label="Yes, reset the season", style=discord.ButtonStyle.danger, emoji="⚠️")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        db.reset_leaderboard(self.guild_id)
        await leaderboard_utils.refresh_leaderboard_channel(self.bot, interaction.guild)
        await interaction.response.edit_message(
            content=f"✅ Season reset — every player is back to {config.STARTING_MMR} MMR with a clean record.",
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

        db.upsert_guild_config(
            guild.id,
            queue_channel_id=queue_channel.id,
            progress_channel_id=progress_channel.id,
            leaderboard_channel_id=leaderboard_channel.id,
        )

        if queue_channel_is_new:
            queue_cog = self.bot.get_cog("QueueCog")
            if queue_cog:
                await queue_cog.ensure_panel_in_channel(guild, queue_channel)

        await progress_channel.send("🚫 No games are currently in progress.")
        await leaderboard_utils.refresh_leaderboard_channel(self.bot, guild)

        await interaction.response.send_message(
            f"✅ NTF is set up for this server — {queue_channel.mention}, {progress_channel.mention}, "
            f"and {leaderboard_channel.mention} are ready. These (and your clubs/captains/leaderboard) "
            f"are separate per server, so other servers NTF is in won't see this data.",
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

    @app_commands.command(name="deduct_mmr", description="[Admin] Deduct a set amount of MMR from a player (e.g. for a ban)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def deduct_mmr(self, interaction: discord.Interaction, member: discord.Member, amount: int):
        db.ensure_player(interaction.guild_id, member.id, member.display_name)
        p = db.get_player(interaction.guild_id, member.id)
        current = p["mmr"] if p else config.STARTING_MMR
        new_mmr = max(0, current - abs(amount))
        db.set_mmr(interaction.guild_id, member.id, new_mmr)
        await leaderboard_utils.refresh_leaderboard_channel(self.bot, interaction.guild)
        await interaction.response.send_message(
            f"➖ Deducted {abs(amount)} MMR from {member.mention}. New MMR: **{new_mmr}** "
            f"({mmr.rank_for_mmr(new_mmr)}).",
            ephemeral=True,
        )

    @app_commands.command(name="season_reset", description="[Admin] Reset every player's MMR and W-L back to the start for a new season")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def season_reset(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            f"⚠️ This resets **every player** in this server back to {config.STARTING_MMR} MMR with a clean "
            f"win/loss record. Match and club history stay intact — only current standing resets. "
            f"This can't be undone. Continue?",
            view=ConfirmSeasonResetView(self.bot, interaction.guild_id),
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

        players = [db.get_player(guild_id, pid) for pid in real_ids + fake_ids]
        players = [p for p in players if p]

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
        session_id = await session_cog.start_session(guild, mode, players, interaction.channel_id)

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

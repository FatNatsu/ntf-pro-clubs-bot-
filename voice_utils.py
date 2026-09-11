"""
All the guild-channel plumbing: creating the session category, the locked
team voice channels, the bench, the captains-only control room, and the
public in-progress text channel - plus moving members around.

Split out from the cogs so the Discord-API-shaped code is in one place.
"""

import discord
import config


async def create_session_category(guild: discord.Guild, session_id: int, mode: str):
    name = f"{config.SESSION_CATEGORY_PREFIX} #{session_id} ({mode.upper()})"
    category = await guild.create_category(name=name)
    return category


async def create_team_voice_channel(guild, category, club_name, member_ids, captain_ids):
    """Locked to TEAM_SIZE. Regular members can connect but are still subject
    to that cap like anyone else. Captains get Move Members too, not just
    Connect - Discord's own voice channel user-limit specifically exempts
    anyone with Move Members on that channel, so this is what actually lets
    a captain join a "full" VC (their own team's or any other team's)."""
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=True, connect=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, connect=True, move_members=True, mute_members=True),
    }
    for uid in member_ids:
        member = guild.get_member(uid)
        if member:
            overwrites[member] = discord.PermissionOverwrite(view_channel=True, connect=True)
    for uid in captain_ids:
        member = guild.get_member(uid)
        if member:
            overwrites[member] = discord.PermissionOverwrite(view_channel=True, connect=True, move_members=True)

    channel = await guild.create_voice_channel(
        name=club_name,
        category=category,
        user_limit=config.TEAM_SIZE,
        overwrites=overwrites,
    )
    return channel


async def create_bench_channel(guild, category, captain_ids, bench_limit=None):
    """Bench: visible + joinable by anyone in the session, subject to the
    normal cap. Captains additionally get Move Members so they can always
    get in even if it's already full, same reasoning as team channels.
    bench_limit lets a force-started session open up extra bench seats to
    cover the players who weren't in the initial pop (see session_cog.py)."""
    if bench_limit is None:
        bench_limit = config.BENCH_SIZE
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=True, connect=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, connect=True, move_members=True, mute_members=True),
    }
    for uid in captain_ids:
        member = guild.get_member(uid)
        if member:
            overwrites[member] = discord.PermissionOverwrite(view_channel=True, connect=True, move_members=True)
    channel = await guild.create_voice_channel(
        name=config.BENCH_CHANNEL_NAME,
        category=category,
        user_limit=bench_limit,
        overwrites=overwrites,
    )
    return channel


async def create_control_channel(guild, category, captain_ids):
    """Text channel only captains (and the bot) can see or post in - this is
    where match panels, sub requests, and the end-session button live."""
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False, send_messages=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
    }
    for uid in captain_ids:
        member = guild.get_member(uid)
        if member:
            overwrites[member] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

    channel = await guild.create_text_channel(
        name=config.SESSION_CONTROL_CHANNEL_NAME,
        category=category,
        overwrites=overwrites,
    )
    return channel


async def create_progress_text_channel(guild, category=None):
    """Public read-only feed: everyone can view, only the bot can post.
    Pass category=None to create it as a permanent top-level channel
    (used for the guild-wide #in-progress channel set up once via /ntf_setup)."""
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
    }
    channel = await guild.create_text_channel(
        name=config.IN_PROGRESS_CHANNEL_NAME,
        category=category,
        overwrites=overwrites,
    )
    return channel


async def create_leaderboard_channel(guild):
    """Permanent, read-only, top-level - holds the one live-updated leaderboard message."""
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
    }
    channel = await guild.create_text_channel(
        name=config.LEADERBOARD_CHANNEL_NAME,
        overwrites=overwrites,
    )
    return channel


async def create_queue_channel(guild):
    """Permanent, top-level - holds the NTF queue panel. Read-only aside from
    the bot so it stays a clean, clutter-free entry point (all interaction
    happens through buttons, nobody needs to type here)."""
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
    }
    channel = await guild.create_text_channel(
        name=config.QUEUE_CHANNEL_NAME,
        overwrites=overwrites,
    )
    return channel


async def move_member_to_channel(guild: discord.Guild, user_id: int, channel: discord.VoiceChannel):
    """
    Discord only lets a bot move members who are ALREADY connected to a
    voice channel somewhere in the guild - it cannot force someone who is
    not in voice at all to join one. Make sure players hop into any voice
    channel before the queue pops.

    Returns (success: bool, reason: str | None) - the reason is only set on
    failure, so callers can surface exactly why a drag didn't happen instead
    of a generic silent failure.
    """
    member = guild.get_member(user_id)
    if member is None:
        return False, "that user isn't in the bot's member cache for this server"
    if member.voice is None or member.voice.channel is None:
        return False, "the bot doesn't see them connected to any voice channel right now"
    try:
        await member.move_to(channel)
        return True, None
    except discord.Forbidden:
        return False, "the bot is missing Move Members permission for that channel"
    except discord.HTTPException as e:
        return False, f"Discord API error ({e.status}): {e.text}"


async def set_spectator_mute(guild: discord.Guild, user_id: int, muted: bool = True):
    member = guild.get_member(user_id)
    if member is None:
        return False
    try:
        await member.edit(mute=muted)
        return True
    except discord.HTTPException:
        return False


async def allow_member_in_channel(channel: discord.VoiceChannel, member: discord.Member, connect=True):
    """Best-effort - never raises. If the bot lacks Manage Roles (or any
    other permission issue), this returns False instead of crashing the
    caller partway through a sub/spectate flow. The bot's own Move Members
    permission on the channel is often enough to drag someone in regardless,
    so a failure here shouldn't block the rest of the operation."""
    try:
        await channel.set_permissions(member, view_channel=True, connect=connect)
        return True
    except discord.HTTPException:
        return False


async def teardown_session_category(guild, category: discord.CategoryChannel):
    for ch in list(category.channels):
        try:
            await ch.delete(reason="Pro Clubs session ended")
        except discord.HTTPException:
            pass
    try:
        await category.delete(reason="Pro Clubs session ended")
    except discord.HTTPException:
        pass

"""
All the guild-channel plumbing: creating the session category, the locked
team voice channels, the bench, each game's public and captains-only channels - plus moving members around.

Split out from the cogs so the Discord-API-shaped code is in one place.
"""

import discord
import config


async def create_named_category(guild: discord.Guild, name: str):
    """A session uses several categories (one for the Bench, one per game) -
    see the layout note in config.py."""
    return await guild.create_category(name=name[:100])


async def create_team_voice_channel(guild, category, club_name, member_ids, captain_ids, user_limit, channel_name=None):
    """Locked to user_limit (the mode's team cap). Regular members can connect but are still subject
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
        name=channel_name or club_name,
        category=category,
        user_limit=user_limit,
        overwrites=overwrites,
    )
    return channel


async def create_bench_channel(guild, category, captain_ids):
    """Bench: visible + joinable by anyone in the session, with NO player
    cap at all (user_limit=0 is Discord's "unlimited"). Captains additionally
    get Move Members so they can always get in and drag people around."""
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
        user_limit=0,
        overwrites=overwrites,
    )
    return channel


async def create_read_only_text_channel(guild, category, name):
    """Everyone can see it, only the bot can post - used for a game's public
    match channel (rosters + Watch buttons) and the public sub queue."""
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
    }
    return await guild.create_text_channel(name=name, category=category, overwrites=overwrites)


async def create_private_text_channel(guild, category, name):
    """Hidden from everyone but the bot (and admins, who see every channel).
    Used for a game's captains channel - the two captains playing that game
    are let in round by round (see SessionCog._sync_captains_access)."""
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False, send_messages=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
    }
    return await guild.create_text_channel(name=name, category=category, overwrites=overwrites)


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


async def create_history_channel(guild):
    """Permanent, read-only, top-level - a running feed where each completed
    session's winner gets posted as its own message, building up a
    permanent log over time (unlike the leaderboard channel, which edits
    one message in place)."""
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
    }
    channel = await guild.create_text_channel(
        name=config.HISTORY_CHANNEL_NAME,
        overwrites=overwrites,
    )
    return channel


async def create_admin_log_channel(guild):
    """Permanent, top-level, hidden from @everyone by default - a running
    audit trail of every button click during a session (who reported what
    score, who used a sub/transfer/removal/reassignment, who marked a match
    live). Discord has no built-in "anyone with Manage Server" channel
    overwrite, so this only denies @everyone and grants the bot - the
    server owner needs to separately give their own staff/admin role
    permission to view it, the same one-time manual step as setting up any
    other private channel."""
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
    }
    channel = await guild.create_text_channel(
        name=config.ADMIN_LOG_CHANNEL_NAME,
        overwrites=overwrites,
    )
    return channel


async def create_season_archive_channel(guild):
    """Permanent, read-only, top-level - a running feed of season-end
    snapshots. Every time /season_reset is confirmed (for either mode),
    the top 5 of BOTH ladders gets posted here as a permanent record
    before the reset wipes the live leaderboard."""
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
    }
    channel = await guild.create_text_channel(
        name=config.SEASON_ARCHIVE_CHANNEL_NAME,
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

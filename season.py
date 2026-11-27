"""Seasons: how long one lasts, when the current one ends, the Golden Crown
(most wins in a season, one per mode) and the season reset itself - shared by
the /season_reset command and the automatic reset at the end of the countdown."""
import asyncio
import datetime
import logging

import discord

import backup
import config
import database as db
import leaderboard_utils
import mmr
import ui

MODES = (("rivals", "⚔️"), ("league", "🏆"))

log = logging.getLogger("ntf")

# One season reset per server at a time (NTF-028): the automatic reset and an
# admin's Reset Season button can't both run, and the second one finds the
# season number already moved on and does nothing.
_reset_locks: dict = {}


def resetting(guild_id: int) -> bool:
    """True while a season reset is running for this server."""
    lock = _reset_locks.get(guild_id)
    return bool(lock and lock.locked())


def _now():
    return datetime.datetime.utcnow()


def _parse(text):
    try:
        return datetime.datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def ensure_started(guild_id: int):
    """A server that has never had a season clock gets one starting now, as
    Season 1. Only the clock is set - nobody's stats are touched."""
    cfg = db.get_guild_config(guild_id) or {}
    if not _parse(cfg.get("season_started_at")):
        db.set_season_boundary(guild_id)
    if not cfg.get("season_number"):
        db.upsert_guild_config(guild_id, season_number=1)


def info(guild_id: int):
    """{"number", "start", "end", "seconds_left"} for this server's current
    season, or None if its clock hasn't been started yet."""
    cfg = db.get_guild_config(guild_id) or {}
    start = _parse(cfg.get("season_started_at"))
    if not start:
        return None
    end = start + datetime.timedelta(days=config.SEASON_LENGTH_DAYS)
    return {
        "number": cfg.get("season_number") or 1,
        "start": start,
        "end": end,
        "seconds_left": (end - _now()).total_seconds(),
    }


def countdown_line(guild_id: int):
    """"Season 1 · ends 26 December 2026 (in 30 days)" using Discord's own
    date stamps, which every member sees in their own time zone and which
    count down by themselves without the message being edited."""
    season = info(guild_id)
    if not season:
        return None
    stamp = int(season["end"].replace(tzinfo=datetime.timezone.utc).timestamp())
    return f"**Season {season['number']}** · ends <t:{stamp}:D> (<t:{stamp}:R>)"


def crown_holder(guild_id: int, mode: str):
    """discord_id of last season's Golden Crown winner in this mode, or None."""
    cfg = db.get_guild_config(guild_id) or {}
    return cfg.get(f"crown_{mode}_id")


async def run_reset(bot, guild: discord.Guild, expected_number: int = None):
    """Ends the season: archives both ladders' top 5 and the Golden Crown
    winners, resets everyone's MMR and records, removes past sessions, clears
    session-history and starts the next season's clock. Returns a short
    summary dict, or None if nothing changed (a session is still running, a
    reset is already happening, or the season it was asked to end has
    already ended). Takes a database backup first."""
    lock = _reset_locks.setdefault(guild.id, asyncio.Lock())
    if lock.locked():
        return None
    async with lock:
        return await _run_reset_locked(bot, guild, expected_number)


async def _run_reset_locked(bot, guild: discord.Guild, expected_number):
    guild_id = guild.id
    session_cog = bot.get_cog("SessionCog")
    if session_cog and any(s["guild_id"] == guild_id for s in session_cog.active_sessions.values()):
        return None
    queue_cog = bot.get_cog("QueueCog")
    if queue_cog and any(m.get("starting") for m in getattr(queue_cog, "state", {}).get(guild_id, {}).values()):
        return None  # a queue is turning into a session right now; try again next time

    ensure_started(guild_id)
    cfg = db.get_guild_config(guild_id) or {}
    number = cfg.get("season_number") or 1
    if expected_number is not None and number != expected_number:
        return None  # that season was already ended by another reset

    # a copy of everything as it was, before anything is reset
    try:
        await asyncio.to_thread(backup.make_backup, "pre-season-reset")
    except Exception:
        log.exception("backup before the season %s reset failed in guild %s - resetting anyway", number, guild_id)

    # the Golden Crown: most wins this season, one per mode
    crowns = {}
    for mode, _emoji in MODES:
        winner = db.most_wins(guild_id, mode)
        crowns[mode] = winner

    archive_id = cfg.get("season_archive_channel_id")
    archive_channel = guild.get_channel(archive_id) if archive_id else None
    if archive_channel:
        embed = discord.Embed(title=f"🏅 Season {number} Ended — Rivals & League Reset", color=ui.accent("neutral"))
        for mode, emoji in MODES:
            winner = crowns[mode]
            crown_text = (
                f"<@{winner['discord_id']}> — {winner['wins']} wins ({winner['wins']}W-{winner['losses']}L)"
                if winner else "*nobody won a game*"
            )
            embed.add_field(name=f"👑 {mode.title()} Golden Crown — most wins", value=crown_text, inline=False)
            top5 = db.mode_leaderboard(guild_id, mode, limit=5)
            lines = [
                f"`{i}.` **{mmr.rank_for_mmr(p['mmr'])}** — <@{p['discord_id']}> — {p['mmr']} MMR ({p['wins']}W-{p['losses']}L)"
                for i, p in enumerate(top5, start=1)
            ] or ["*no players tracked yet*"]
            embed.add_field(name=f"{emoji} {mode.title()} Top 5", value="\n".join(lines), inline=False)
        try:
            await archive_channel.send(**ui.send_kwargs("season_archive", embed))
        except discord.HTTPException:
            # the backup taken above still holds the final standings
            log.exception("season %s archive post failed in guild %s", number, guild_id)

    clubs_reset = 0
    for mode, _emoji in MODES:
        db.reset_mode_leaderboard(guild_id, mode)
        clubs_reset += db.reset_club_records(guild_id, mode)
    db.set_season_boundary(guild_id)
    sessions_removed = db.reset_sessions(guild_id)
    db.upsert_guild_config(
        guild_id,
        season_number=number + 1,
        season_warned=0,
        crown_rivals_id=crowns["rivals"]["discord_id"] if crowns["rivals"] else None,
        crown_league_id=crowns["league"]["discord_id"] if crowns["league"] else None,
    )

    # session-history starts empty for the new season too; the season
    # archive (the season winners) is never touched. In the background -
    # clearing a long channel can take a while.
    history_id = cfg.get("history_channel_id")
    history_channel = guild.get_channel(history_id) if history_id else None
    if history_channel:
        bot_id = bot.user.id

        async def _clear_history():
            try:
                await history_channel.purge(limit=None, check=lambda m: m.author.id == bot_id)
            except discord.HTTPException:
                pass

        asyncio.create_task(_clear_history())

    await leaderboard_utils.refresh_leaderboard_channel(bot, guild)
    return {"ended": number, "clubs_reset": clubs_reset, "sessions_removed": sessions_removed, "crowns": crowns}

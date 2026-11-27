"""Admin alerts and a record of recent errors (NTF-023, NTF-031).

notify_admins() posts a short message in a server's admin log. It never
raises: an alert that can't be posted is logged instead, so it can never
break whatever was reporting the problem.

ErrorTracker keeps the last errors the bot logged (first line only, no
tracebacks or tokens), for the admin /ntf_status command."""

import collections
import logging
import time

import discord

import database as db

log = logging.getLogger("ntf")
STARTED_AT = time.time()


class ErrorTracker(logging.Handler):
    def __init__(self, keep: int = 20):
        super().__init__(level=logging.ERROR)
        self.count = 0
        self.recent = collections.deque(maxlen=keep)

    def emit(self, record):
        try:
            first_line = record.getMessage().splitlines()[0][:150] if record.getMessage() else record.levelname
        except Exception:
            first_line = record.levelname
        self.count += 1
        self.recent.append((record.created, first_line))


errors = ErrorTracker()


def install():
    """Attach the tracker to the bot's logger once."""
    if errors not in log.handlers:
        log.addHandler(errors)


async def notify_admins(guild: discord.Guild, text: str) -> bool:
    """Posts text in this server's admin log. True if it was posted."""
    try:
        cfg = db.get_guild_config(guild.id) or {}
        channel = guild.get_channel(cfg.get("admin_log_channel_id") or 0)
        if channel is None:
            log.warning("admin alert for guild %s had nowhere to go: %s", guild.id, text[:200])
            return False
        await channel.send(text[:2000])
        return True
    except Exception:
        log.warning("admin alert failed in guild %s", getattr(guild, "id", "?"), exc_info=True)
        return False

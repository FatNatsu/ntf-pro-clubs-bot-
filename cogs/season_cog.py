import logging

import discord
from discord.ext import commands, tasks

import config
import database as db
import leaderboard_utils
import season

log = logging.getLogger("ntf")


class SeasonCog(commands.Cog):
    """Keeps every server's season clock: starts it, warns a day before the
    season ends, and resets the season by itself when the countdown runs out
    (waiting for any live session to finish first)."""

    def __init__(self, bot):
        self.bot = bot
        self.tick.start()

    def cog_unload(self):
        self.tick.cancel()

    @tasks.loop(minutes=10)
    async def tick(self):
        for guild in list(self.bot.guilds):
            try:
                await self._check(guild)
            except Exception:  # one server's problem must never stop the clock for the others
                log.exception("season check failed for guild %s", guild.id)

    @tick.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_ready()

    async def _check(self, guild: discord.Guild):
        cfg = db.get_guild_config(guild.id)
        if not cfg or not cfg.get("leaderboard_channel_id"):
            return  # /ntf_setup hasn't been run here
        if season.info(guild.id) is None or not cfg.get("season_number"):
            season.ensure_started(guild.id)
            await leaderboard_utils.refresh_leaderboard_channel(self.bot, guild)  # shows the countdown
            return

        current = season.info(guild.id)
        if current["seconds_left"] <= 0:
            result = await season.run_reset(self.bot, guild, expected_number=current["number"])
            if result:
                log.info("Season %s ended automatically in guild %s", result["ended"], guild.id)
            return  # None = a session is live; try again on the next tick

        if current["seconds_left"] <= config.SEASON_WARNING_HOURS * 3600 and not cfg.get("season_warned"):
            channel = guild.get_channel(cfg.get("season_archive_channel_id") or 0)
            if not channel:
                db.upsert_guild_config(guild.id, season_warned=1)  # nowhere to post it
                return
            try:
                await channel.send(
                    f"⏳ **Season {current['number']} ends in less than {config.SEASON_WARNING_HOURS} hours.** "
                    f"MMR, records and sessions reset when it does, and the Golden Crown goes to the player "
                    f"with the most wins in each mode. Get your last games in."
                )
            except discord.HTTPException:
                # NTF-028: not marked as sent, so the next check tries again
                log.warning("season warning failed to post in guild %s; will retry", guild.id)
                return
            db.upsert_guild_config(guild.id, season_warned=1)


async def setup(bot):
    await bot.add_cog(SeasonCog(bot))

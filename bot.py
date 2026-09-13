"""
NTF - Pro Clubs Discord Bot
Entry point: loads cogs, sets up intents, syncs slash commands.

Run with:  python bot.py
Requires:  DISCORD_BOT_TOKEN env var (see README.md)
"""

import asyncio
import logging

import discord
from discord.ext import commands

import config
import database as db

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ntf")

INTENTS = discord.Intents.default()
INTENTS.members = True         # needed to resolve display names / look up members
INTENTS.voice_states = True    # needed to know who's already in voice before we move them

EXTENSIONS = [
    "cogs.admin_cog",
    "cogs.mmr_cog",
    "cogs.stats_cog",
    "cogs.queue_cog",
    "cogs.session_cog",
]


class NTFBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!ntf ", intents=INTENTS, help_command=None)

    async def setup_hook(self):
        db.init_db()
        for ext in EXTENSIONS:
            await self.load_extension(ext)
            log.info("Loaded %s", ext)

        for guild_id in config.GUILD_IDS:
            guild_obj = discord.Object(id=guild_id)
            self.tree.copy_global_to(guild=guild_obj)
            synced = await self.tree.sync(guild=guild_obj)
            log.info("Synced %d commands to guild %s (instant)", len(synced), guild_id)

        # ALWAYS also do a global sync, regardless of whether GUILD_ID is
        # set - otherwise commands only ever exist on that one test server
        # and never appear on any other server (like a friend's) the bot
        # gets invited to. This can take up to an hour to first appear on a
        # brand-new server, but only needs to happen once per code change.
        global_synced = await self.tree.sync()
        log.info("Synced %d global commands (can take up to an hour to appear on new servers)", len(global_synced))

    async def on_ready(self):
        await self.change_presence(activity=discord.Game(name="NTF Pro Clubs"))
        log.info("NTF is online as %s (id: %s)", self.user, self.user.id)


bot = NTFBot()


def main():
    if not config.DISCORD_TOKEN or config.DISCORD_TOKEN == "PUT_YOUR_TOKEN_HERE":
        raise SystemExit(
            "No bot token configured. Set the DISCORD_BOT_TOKEN environment variable "
            "(see README.md) before running bot.py."
        )
    bot.run(config.DISCORD_TOKEN)


if __name__ == "__main__":
    main()

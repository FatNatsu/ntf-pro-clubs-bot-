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

        if config.GUILD_ID:
            guild_obj = discord.Object(id=config.GUILD_ID)
            self.tree.copy_global_to(guild=guild_obj)
            synced = await self.tree.sync(guild=guild_obj)
            log.info("Synced %d commands to guild %s (instant)", len(synced), config.GUILD_ID)
        else:
            synced = await self.tree.sync()
            log.info("Synced %d global commands (can take up to an hour to appear)", len(synced))

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

from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands

import database as db
import mmr
import leaderboard_utils

LEADERBOARD_PAGE_SIZE = 25


class LeaderboardPaginatorView(discord.ui.View):
    """Previous/Next paged view of one mode's entire leaderboard - used by
    /leaderboard full:True so it scales to any server size instead of being
    capped at a flat number that risks exceeding Discord's embed limit."""

    def __init__(self, guild_id: int, mode: str, highlight_id: int = None, page: int = 0):
        super().__init__(timeout=120)
        self.guild_id = guild_id
        self.mode = mode
        self.highlight_id = highlight_id
        self.page = page
        self._sync_button_state()

    def _sync_button_state(self):
        total = db.count_mode_players(self.guild_id, self.mode)
        self.previous_button.disabled = self.page <= 0
        self.next_button.disabled = (self.page + 1) * LEADERBOARD_PAGE_SIZE >= total

    def build_embed(self):
        total = db.count_mode_players(self.guild_id, self.mode)
        rows = db.mode_leaderboard(self.guild_id, self.mode, limit=LEADERBOARD_PAGE_SIZE, offset=self.page * LEADERBOARD_PAGE_SIZE)
        title = f"🏆 NTF Full {self.mode.title()} Leaderboard"
        embed = leaderboard_utils.build_leaderboard_embed(
            rows, highlight_id=self.highlight_id, title=title,
            start_rank=self.page * LEADERBOARD_PAGE_SIZE + 1,
        )
        total_pages = max(1, (total + LEADERBOARD_PAGE_SIZE - 1) // LEADERBOARD_PAGE_SIZE)
        embed.set_footer(text=f"Page {self.page + 1}/{total_pages} — {total} tracked {self.mode} player(s) in this server")
        return embed

    @discord.ui.button(label="◀ Previous", style=discord.ButtonStyle.secondary)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self._sync_button_state()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        self._sync_button_state()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)


class MMRCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="rank", description="Show your (or someone else's) rank and MMR in one mode in this server")
    async def rank(self, interaction: discord.Interaction, mode: Literal["rivals", "league"], member: discord.Member = None):
        member = member or interaction.user
        db.ensure_player(interaction.guild_id, member.id, member.display_name)
        p = db.get_player(interaction.guild_id, member.id)
        stats = db.get_player_mode_stats(interaction.guild_id, member.id, mode)
        letter = mmr.rank_for_mmr(stats["mmr"])
        embed = discord.Embed(title=f"{member.display_name}'s {mode.title()} Rank", color=discord.Color.blurple())
        embed.add_field(name="Rank", value=f"**{letter}**", inline=True)
        embed.add_field(name="MMR", value=str(stats["mmr"]), inline=True)
        embed.add_field(name="Record", value=f"{stats['wins']}W - {stats['losses']}L", inline=True)
        if p["is_captain"]:
            embed.set_footer(text="🎖️ Whitelisted captain")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="leaderboard", description="Show this server's top players by MMR in one mode, with your own standing highlighted")
    async def leaderboard(self, interaction: discord.Interaction, mode: Literal["rivals", "league"], full: bool = False):
        if full:
            # Paginated view of literally everyone tracked in this mode, 25
            # per page, with Previous/Next buttons to browse the whole server.
            view = LeaderboardPaginatorView(interaction.guild_id, mode, highlight_id=interaction.user.id)
            await interaction.response.send_message(embed=view.build_embed(), view=view)
            return

        rows = db.mode_leaderboard(interaction.guild_id, mode, 10)
        embed = leaderboard_utils.build_leaderboard_embed(rows, highlight_id=interaction.user.id, title=f"🏆 NTF {mode.title()} Leaderboard")

        db.ensure_player(interaction.guild_id, interaction.user.id, interaction.user.display_name)
        stats = db.get_player_mode_stats(interaction.guild_id, interaction.user.id, mode)
        in_top = any(r["discord_id"] == interaction.user.id for r in rows)
        if not in_top:
            pos = db.get_mode_rank_position(interaction.guild_id, interaction.user.id, mode)
            letter = mmr.rank_for_mmr(stats["mmr"])
            embed.add_field(
                name="Your Standing",
                value=f"`#{pos}` **{letter}** — {stats['mmr']} MMR ({stats['wins']}W-{stats['losses']}L)",
                inline=False,
            )
        await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(MMRCog(bot))

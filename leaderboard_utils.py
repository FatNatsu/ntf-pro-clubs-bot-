import discord

import database as db
import mmr


def build_leaderboard_embed(rows, highlight_id=None, title="🏆 NTF Leaderboard"):
    lines = []
    for i, p in enumerate(rows, start=1):
        letter = mmr.rank_for_mmr(p["mmr"])
        marker = "➡️ " if highlight_id and p["discord_id"] == highlight_id else ""
        lines.append(
            f"{marker}`{i:>2}.` **{letter}** — <@{p['discord_id']}> — {p['mmr']} MMR ({p['wins']}W-{p['losses']}L)"
        )
    embed = discord.Embed(
        title=title,
        description="\n".join(lines) if lines else "No players tracked yet — get a session going!",
        color=discord.Color.gold(),
    )
    return embed


async def refresh_leaderboard_channel(bot, guild: discord.Guild):
    """Edits (or creates) the one persistent leaderboard message in the
    guild's permanent #leaderboard channel, set up via /ntf_setup."""
    cfg = db.get_guild_config(guild.id)
    if not cfg or not cfg.get("leaderboard_channel_id"):
        return
    channel = guild.get_channel(cfg["leaderboard_channel_id"])
    if channel is None:
        return

    rows = db.leaderboard(guild.id, 20)
    embed = build_leaderboard_embed(rows)
    embed.set_footer(text="Run /leaderboard anywhere to see your own rank highlighted, even outside the top 20.")

    message = None
    message_id = cfg.get("leaderboard_message_id")
    if message_id:
        try:
            message = await channel.fetch_message(message_id)
        except (discord.NotFound, discord.HTTPException):
            message = None

    if message:
        try:
            await message.edit(embed=embed)
            return
        except discord.HTTPException:
            pass

    message = await channel.send(embed=embed)
    db.upsert_guild_config(guild.id, leaderboard_message_id=message.id)

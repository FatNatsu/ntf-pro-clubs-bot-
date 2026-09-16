import discord

import database as db
import mmr


def build_leaderboard_embed(rows, highlight_id=None, title="🏆 NTF Leaderboard", start_rank=1):
    lines = []
    for i, p in enumerate(rows, start=start_rank):
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


async def _refresh_one_mode_leaderboard(guild: discord.Guild, channel: discord.TextChannel, cfg: dict, mode: str):
    """Edits (or creates) the persistent leaderboard message for one
    specific mode. Rivals and League each get their own message in the
    same #leaderboard channel, tracked independently."""
    rows = db.mode_leaderboard(guild.id, mode, 10)
    embed = build_leaderboard_embed(rows, title=f"🏆 NTF {mode.title()} Leaderboard")
    embed.set_footer(text=f"Run /leaderboard mode:{mode} anywhere to see your own rank highlighted, even outside the top 10.")

    message_id_key = f"leaderboard_message_id_{mode}"
    message = None
    message_id = cfg.get(message_id_key)
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
    db.upsert_guild_config(guild.id, **{message_id_key: message.id})


async def refresh_leaderboard_channel(bot, guild: discord.Guild):
    """Edits (or creates) TWO persistent leaderboard messages - one for
    Rivals, one for League - in the guild's permanent #leaderboard channel,
    set up via /ntf_setup. Each mode's message is tracked and updated
    independently of the other."""
    cfg = db.get_guild_config(guild.id)
    if not cfg or not cfg.get("leaderboard_channel_id"):
        return
    channel = guild.get_channel(cfg["leaderboard_channel_id"])
    if channel is None:
        return

    for mode in ("rivals", "league"):
        await _refresh_one_mode_leaderboard(guild, channel, cfg, mode)
        cfg = db.get_guild_config(guild.id)  # re-fetch so the second mode sees any message_id just written

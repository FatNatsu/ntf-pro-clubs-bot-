"""
The shared look of NTF's panels: accent colours, text progress bars and the
banner strip that sits on top of a panel.

How a banner works in Discord: an embed's own image is always drawn at the
BOTTOM of the embed, so to get a header strip on top, a panel is sent as two
embeds in one message - a first embed that holds only the banner image, and
the real panel underneath, both with the same accent colour. The picture
itself is a file in assets/banners/ that gets attached to the message.

Banners are fixed pictures: no live data (team names, scores) in them.
If a banner file doesn't exist yet, the panel is simply sent without one, so
missing artwork never breaks anything.
"""

import os

import discord

# Left-edge accent colour of a panel (from the design pack).
ACCENT = {
    "league": 0x20C9FF,   # cyan
    "rivals": 0xA970FF,   # purple
    "neutral": 0x7F8EA3,  # admin / anything not tied to a mode
}

BANNER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "banners")
BANNER_EXT = "jpg"


def accent(key: str) -> int:
    """Accent colour for a mode ("league" / "rivals"); anything else is neutral."""
    return ACCENT.get(key, ACCENT["neutral"])


def bar(current: int, total: int, length: int = 12) -> str:
    """A text progress bar, e.g. ▰▰▰▰▰▰▰▱▱▱▱▱ - the only kind Discord can draw."""
    if total <= 0:
        return "▱" * length
    filled = max(0, min(length, round(length * current / total)))
    return "▰" * filled + "▱" * (length - filled)


def _banner_path(name: str):
    path = os.path.join(BANNER_DIR, f"{name}.{BANNER_EXT}")
    return path if os.path.isfile(path) else None


def _banner_embed(name: str, colour):
    embed = discord.Embed(color=colour)
    embed.set_image(url=f"attachment://{name}.{BANNER_EXT}")
    return embed


def send_kwargs(name: str, embed: discord.Embed) -> dict:
    """Keyword arguments for channel.send(...) that put banner `name` on top
    of `embed`. Use as: await channel.send(**ui.send_kwargs("queue", embed), view=view)"""
    path = _banner_path(name)
    if not path:
        return {"embed": embed}
    return {
        "embeds": [_banner_embed(name, embed.colour), embed],
        "file": discord.File(path, filename=f"{name}.{BANNER_EXT}"),
    }


def edit_kwargs(name: str, embed: discord.Embed) -> dict:
    """Keyword arguments for message.edit(...) that keep banner `name` on top
    while replacing the panel underneath. The attached picture stays on the
    message; only the embeds are resent."""
    if not _banner_path(name):
        return {"embed": embed}
    return {"embeds": [_banner_embed(name, embed.colour), embed]}

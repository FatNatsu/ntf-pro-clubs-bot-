"""
Central configuration for the Pro Clubs bot.
Edit these values to tune the bot without digging through logic code.
"""

import os

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
DISCORD_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "PUT_YOUR_TOKEN_HERE")

# Comma-separated list of server IDs that get INSTANT slash-command syncing
# (no waiting on Discord's global propagation delay) - e.g. "111,222,333".
# Falls back to the older single-ID DISCORD_GUILD_ID variable if that's all
# that's set, for backward compatibility.
_raw_guild_ids = os.environ.get("DISCORD_GUILD_IDS", os.environ.get("DISCORD_GUILD_ID", ""))
GUILD_IDS = [int(g.strip()) for g in _raw_guild_ids.split(",") if g.strip()]

# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------
# "rivals" = 2 teams, "league" = 4 teams
MODE_TEAMS = {
    "rivals": 2,
    "league": 4,
}

TEAM_SIZE = 6          # locked size per team voice channel
BENCH_SIZE = 4          # locked size of the bench voice channel

QUEUE_CAP = {
    # These are the pop thresholds you asked for: 12 for rivals, 24 for league.
    # NOTE: this is exactly TEAM_SIZE * num_teams, i.e. enough for starting
    # line-ups only. The bench starts EMPTY and fills as captains pull
    # players in from the Bench VC (see session_cog.py) - it is not
    # pre-populated from extra queue overflow.
    "rivals": TEAM_SIZE * MODE_TEAMS["rivals"],   # 12
    "league": TEAM_SIZE * MODE_TEAMS["league"],   # 24
}

# Once the queue hits its cap, players get a grace window before the session
# actually locks in and channels get built - last chance to back out.
READY_COUNTDOWN_SECONDS = 15

# Admins can force-start a mode early once this many players are readied
# up, so the bot can still field full teams even if the queue never fully
# fills. League needs 16 (4 full teams of 4). Rivals needs 8 (2 teams of 4)
# - not the full 12, since force-start exists specifically for "enough to
# make a fair game even if it never fills all the way."
FORCE_START_MIN = {
    "league": 16,
    "rivals": 8,
}

# ---------------------------------------------------------------------------
# MMR / Ranks (Elo-style)
# ---------------------------------------------------------------------------
STARTING_MMR = 500

# Win/loss K-factor (how much a single result moves MMR) per rank. Wins
# taper down from G (most generous) to S+ (smallest), same as before.
# Losses now start at ZERO at G - no penalty at all for a loss while you're
# still learning - and increase steadily through B, so the safety net
# fades gradually rather than disappearing all at once. From A upward,
# losses jump back up toward full strength (see the numbers below).
K_FACTORS = {
    "G": {"win": 32, "loss": 0},
    "F": {"win": 30, "loss": 6},
    "E": {"win": 29, "loss": 12},
    "D": {"win": 28, "loss": 18},
    "C": {"win": 27, "loss": 20},
    "B": {"win": 26, "loss": 22},
    "A": {"win": 26, "loss": 30},
    "S": {"win": 16, "loss": 20},
    "S+": {"win": 10, "loss": 24},
}

# Rank floor thresholds, low to high. Starting MMR (500) lands new players
# in D. Top is S+ at 2500 MMR.
RANK_THRESHOLDS = [
    ("G", 200),
    ("F", 300),
    ("E", 400),
    ("D", 500),
    ("C", 600),
    ("B", 700),
    ("A", 900),
    ("S", 1100),
    ("S+", 1500),
]

# ---------------------------------------------------------------------------
# Category / channel naming
# ---------------------------------------------------------------------------
SESSION_CATEGORY_PREFIX = "PRO CLUBS SESSION"
BENCH_CHANNEL_NAME = "🪑 Bench"
SESSION_CONTROL_CHANNEL_NAME = "🎛️ session-control"
IN_PROGRESS_CHANNEL_NAME = "📺 in-progress"      # permanent, created once via /ntf_setup
LEADERBOARD_CHANNEL_NAME = "🏆 leaderboard"       # permanent, created once via /ntf_setup
HISTORY_CHANNEL_NAME = "📜 session-history"        # permanent, created once via /ntf_setup
ADMIN_LOG_CHANNEL_NAME = "🔒 admin-log"            # permanent, created once via /ntf_setup - admin-only by default
SEASON_ARCHIVE_CHANNEL_NAME = "🏅 season-archive"  # permanent, created once via /ntf_setup - top 5 snapshot whenever a season resets
QUEUE_CHANNEL_NAME = "🎮 ntf-queue"                # permanent, created once via /ntf_setup

# How long the final standings stay visible in #in-progress before the
# session's voice channels (including session-control) are torn down.
SESSION_CLOSE_DELAY_SECONDS = 60

# Small flat MMR bonus for every player on the 1st-place team once a session
# concludes, on top of whatever they already earned from individual match
# results - a slight reward for winning the whole session, not just games.
SESSION_WIN_BONUS_MMR = 15

DB_PATH = os.environ.get("PRO_CLUBS_DB_PATH", "pro_clubs.db")

# ---------------------------------------------------------------------------
# Branding
# ---------------------------------------------------------------------------
BOT_NAME = "NTF"
EMBED_TITLE_PREFIX = "NTF"  # used to prefix embed titles, e.g. "NTF Pro Clubs Queue"

"""
Central configuration for the Pro Clubs bot.
Edit these values to tune the bot without digging through logic code.
"""

import os

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
DISCORD_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "PUT_YOUR_TOKEN_HERE")
GUILD_ID = int(os.environ.get("DISCORD_GUILD_ID", "0"))  # your server ID, for instant slash-command sync

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
READY_COUNTDOWN_SECONDS = 30

# Admins can force-start League early once this many players are readied up,
# so the bot can still make 4 full teams of 4 even if the queue never fills
# to 24. Not offered for Rivals (2 teams of 6 is already the minimum shape).
FORCE_START_MIN = {
    "league": 16,
}

# If League hasn't reached FORCE_START_MIN within this long, it auto-closes
# so the channel doesn't sit clogged with a queue that's never going to pop -
# players can immediately start a fresh Rivals queue instead.
LEAGUE_QUEUE_TIMEOUT_SECONDS = 300  # 5 minutes

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
    ("G", 0),
    ("F", 200),
    ("E", 350),
    ("D", 500),
    ("C", 800),
    ("B", 1200),
    ("A", 1700),
    ("S", 2000),
    ("S+", 2500),
]

# ---------------------------------------------------------------------------
# Category / channel naming
# ---------------------------------------------------------------------------
SESSION_CATEGORY_PREFIX = "PRO CLUBS SESSION"
BENCH_CHANNEL_NAME = "🪑 Bench"
SESSION_CONTROL_CHANNEL_NAME = "🎛️ session-control"
IN_PROGRESS_CHANNEL_NAME = "📺 in-progress"      # permanent, created once via /ntf_setup
LEADERBOARD_CHANNEL_NAME = "🏆 leaderboard"       # permanent, created once via /ntf_setup
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

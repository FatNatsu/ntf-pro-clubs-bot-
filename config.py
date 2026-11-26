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
# League and Rivals are SEPARATE modes with their own sizes - never assume a
# number from one applies to the other. Everything below is per mode:
#
#   teams       how many teams the mode has
#   starting    players seated per team when a FULL queue pops
#               (queue pop size = starting x teams)
#   normal      the size a team normally fills to. The Add sub button and the
#               sub turn order only ever top a team up to this number.
#   cap         hard ceiling per team, subs included. When cap is above
#               normal, the seats in between are a last resort: they are only
#               filled when the team's captain (or an admin) explicitly asks,
#               with the "Request Nth player" button.
#   force_start how many readied-up players an admin needs to force start
#               early (3 per team). Whatever number is actually ready is split
#               evenly, with any remainder starting on the Bench.
#
# League: 4 teams, pops at 24 (6 a side), teams sit at 6, a 7th on request.
# Rivals: 2 teams, pops at 22 (11 a side), subs can be added up to 11.
MODE_RULES = {
    "league": {"teams": 4, "starting": 6, "normal": 6, "cap": 7, "force_start": 12},
    "rivals": {"teams": 2, "starting": 11, "normal": 11, "cap": 11, "force_start": 6},
}


def starting_team_size(mode: str) -> int:
    return MODE_RULES[mode]["starting"]


def normal_team_size(mode: str) -> int:
    return MODE_RULES[mode]["normal"]


def team_cap(mode: str) -> int:
    return MODE_RULES[mode]["cap"]


MODE_TEAMS = {mode: rules["teams"] for mode, rules in MODE_RULES.items()}

# Pop thresholds: League 24, Rivals 22.
QUEUE_CAP = {mode: rules["starting"] * rules["teams"] for mode, rules in MODE_RULES.items()}

# Only used as team_balance.build_teams' fallback when a caller doesn't say
# how many to seat (every real caller does).
STARTING_TEAM_SIZE = MODE_RULES["league"]["starting"]

# Shown after the club name on a team's voice channel once the team is at
# its cap, e.g. "Fram Esports - MAX Players". Removed again when a seat opens.
TEAM_FULL_SUFFIX = "MAX Players"

# The Bench voice channel has no player cap at all - it's created with no user
# limit. This constant is no longer enforced anywhere; it's only left defined
# so any older code that still references it doesn't crash.
BENCH_SIZE = 4

# The live "Sub Queue" message in session-control is refreshed this many seconds
# after the Bench changes, batching a burst of people joining/leaving into a
# single edit instead of one Discord call per person.
SUB_QUEUE_REFRESH_DELAY = 1.5

# How a sub is chosen from the Bench: the SUB_PICK_WINDOW longest-waiting
# players form the pool, and MMR balancing picks whoever best evens out the
# requesting team's average from THAT pool - so wait order decides who's
# eligible, balance decides who out of them. 1 = strict first-come-first-served
# (no balancing); a very large number = pure MMR balancing (wait order ignored).
SUB_PICK_WINDOW = 4

# Fairness guarantee for the above: a player who's been passed over this many
# times (still in the pool, but someone else fit a team better) goes next
# regardless of MMR, so nobody can wait indefinitely just because they never
# happen to be the best fit.
SUB_MAX_SKIPS = 2

# Once the queue hits its cap, players get a grace window before the session
# actually locks in and channels get built - last chance to back out.
READY_COUNTDOWN_SECONDS = 15

# Admins can force-start a mode early once this many players are readied
# up (see MODE_RULES above): League 12, Rivals 6.
FORCE_START_MIN = {mode: rules["force_start"] for mode, rules in MODE_RULES.items()}

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

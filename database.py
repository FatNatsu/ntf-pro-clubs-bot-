"""
All persistence lives here. Plain sqlite3, no ORM.

Every server (guild) the bot joins gets its OWN players, clubs, captains,
MMR and leaderboard - nothing here is shared across servers. Sessions,
teams, and matches were already implicitly guild-scoped (every session
belongs to exactly one guild), but players/clubs/match-history needed an
explicit guild_id since the same Discord account or club name can appear
in more than one server.
"""

import sqlite3
import contextlib
import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    guild_id        INTEGER NOT NULL,
    discord_id      INTEGER NOT NULL,
    display_name    TEXT NOT NULL,
    mmr             INTEGER NOT NULL DEFAULT 1200,
    wins            INTEGER NOT NULL DEFAULT 0,
    losses          INTEGER NOT NULL DEFAULT 0,
    is_captain      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, discord_id)
);

CREATE TABLE IF NOT EXISTS clubs (
    guild_id        INTEGER NOT NULL,
    name            TEXT NOT NULL,
    PRIMARY KEY (guild_id, name)
);

CREATE TABLE IF NOT EXISTS sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    mode            TEXT NOT NULL,               -- 'rivals' | 'league'
    status          TEXT NOT NULL DEFAULT 'active', -- active | ended
    category_id     INTEGER,
    bench_channel_id        INTEGER,
    control_channel_id      INTEGER,
    progress_channel_id     INTEGER,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS teams (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER NOT NULL,
    club_name       TEXT NOT NULL,
    voice_channel_id INTEGER,
    captain_id      INTEGER,
    FOREIGN KEY(session_id) REFERENCES sessions(id)
);

CREATE TABLE IF NOT EXISTS team_members (
    team_id         INTEGER NOT NULL,
    player_id       INTEGER NOT NULL,
    role            TEXT NOT NULL DEFAULT 'player', -- player | sub | spectator
    PRIMARY KEY (team_id, player_id)
);

CREATE TABLE IF NOT EXISTS matches (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER NOT NULL,
    team_a_id       INTEGER NOT NULL,
    team_b_id       INTEGER NOT NULL,
    round_no        INTEGER NOT NULL,
    winner_team_id  INTEGER,
    score_a         INTEGER,
    score_b         INTEGER,
    status          TEXT NOT NULL DEFAULT 'pending', -- pending | reported
    reported_at     TEXT,
    FOREIGN KEY(session_id) REFERENCES sessions(id)
);

-- One row per player per reported match: powers /player_stats (recent form,
-- best club, most-played-with teammate). guild_id is denormalized here
-- (also derivable via match->session->guild) purely so these lookups don't
-- need a 3-way join.
CREATE TABLE IF NOT EXISTS match_participants (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    match_id        INTEGER NOT NULL,
    session_id      INTEGER NOT NULL,
    player_id       INTEGER NOT NULL,
    team_id         INTEGER NOT NULL,
    club_name       TEXT NOT NULL,
    result          TEXT NOT NULL,   -- 'win' | 'loss'
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

-- One row per side per reported match: powers /club_stats.
CREATE TABLE IF NOT EXISTS club_match_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    match_id        INTEGER NOT NULL,
    club_name       TEXT NOT NULL,
    result          TEXT NOT NULL,   -- 'win' | 'loss'
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

-- One row per guild: where the PERMANENT in-progress / leaderboard channels
-- live, set up once via /ntf_setup and reused by every session after that.
CREATE TABLE IF NOT EXISTS guild_config (
    guild_id                INTEGER PRIMARY KEY,
    progress_channel_id     INTEGER,
    leaderboard_channel_id  INTEGER,
    leaderboard_message_id  INTEGER,
    queue_channel_id        INTEGER
);
"""


@contextlib.contextmanager
def get_conn():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)


# ---------------------------------------------------------------------------
# Player helpers (per-guild)
# ---------------------------------------------------------------------------

def ensure_player(guild_id: int, discord_id: int, display_name: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO players (guild_id, discord_id, display_name, mmr) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(guild_id, discord_id) DO UPDATE SET display_name=excluded.display_name",
            (guild_id, discord_id, display_name, config.STARTING_MMR),
        )


def get_player(guild_id: int, discord_id: int):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM players WHERE guild_id=? AND discord_id=?", (guild_id, discord_id)
        ).fetchone()
        return dict(row) if row else None


def set_captain(guild_id: int, discord_id: int, is_captain: bool):
    with get_conn() as conn:
        conn.execute(
            "UPDATE players SET is_captain=? WHERE guild_id=? AND discord_id=?",
            (1 if is_captain else 0, guild_id, discord_id),
        )


def get_captains(guild_id: int):
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM players WHERE guild_id=? AND is_captain=1", (guild_id,)).fetchall()
        return [dict(r) for r in rows]


def set_mmr(guild_id: int, discord_id: int, new_mmr: int):
    """Pure MMR correction - does not touch win/loss counters."""
    with get_conn() as conn:
        conn.execute("UPDATE players SET mmr=? WHERE guild_id=? AND discord_id=?", (new_mmr, guild_id, discord_id))


def update_mmr(guild_id: int, discord_id: int, new_mmr: int, won: bool):
    with get_conn() as conn:
        if won:
            conn.execute(
                "UPDATE players SET mmr=?, wins=wins+1 WHERE guild_id=? AND discord_id=?",
                (new_mmr, guild_id, discord_id),
            )
        else:
            conn.execute(
                "UPDATE players SET mmr=?, losses=losses+1 WHERE guild_id=? AND discord_id=?",
                (new_mmr, guild_id, discord_id),
            )


def leaderboard(guild_id: int, limit=20):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM players WHERE guild_id=? ORDER BY mmr DESC LIMIT ?", (guild_id, limit)
        ).fetchall()
        return [dict(r) for r in rows]


def get_player_rank_position(guild_id: int, discord_id: int):
    """1-based position on this guild's MMR leaderboard, or None if untracked."""
    with get_conn() as conn:
        p = conn.execute(
            "SELECT mmr FROM players WHERE guild_id=? AND discord_id=?", (guild_id, discord_id)
        ).fetchone()
        if not p:
            return None
        row = conn.execute(
            "SELECT COUNT(*) + 1 AS pos FROM players WHERE guild_id=? AND mmr > ?", (guild_id, p["mmr"])
        ).fetchone()
        return row["pos"]


# ---------------------------------------------------------------------------
# Club helpers (per-guild)
# ---------------------------------------------------------------------------

def add_club(guild_id: int, name: str):
    with get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO clubs (guild_id, name) VALUES (?, ?)", (guild_id, name))


def remove_club(guild_id: int, name: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM clubs WHERE guild_id=? AND name=?", (guild_id, name))


def list_clubs(guild_id: int):
    with get_conn() as conn:
        rows = conn.execute("SELECT name FROM clubs WHERE guild_id=? ORDER BY name", (guild_id,)).fetchall()
        return [r["name"] for r in rows]


# ---------------------------------------------------------------------------
# Session / team / match helpers (already guild-scoped via sessions.guild_id)
# ---------------------------------------------------------------------------

def create_session(guild_id: int, mode: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO sessions (guild_id, mode) VALUES (?, ?)", (guild_id, mode)
        )
        return cur.lastrowid


def set_session_channels(session_id, category_id=None, bench_id=None, control_id=None, progress_id=None):
    with get_conn() as conn:
        if category_id is not None:
            conn.execute("UPDATE sessions SET category_id=? WHERE id=?", (category_id, session_id))
        if bench_id is not None:
            conn.execute("UPDATE sessions SET bench_channel_id=? WHERE id=?", (bench_id, session_id))
        if control_id is not None:
            conn.execute("UPDATE sessions SET control_channel_id=? WHERE id=?", (control_id, session_id))
        if progress_id is not None:
            conn.execute("UPDATE sessions SET progress_channel_id=? WHERE id=?", (progress_id, session_id))


def get_session(session_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        return dict(row) if row else None


def end_session(session_id):
    with get_conn() as conn:
        conn.execute("UPDATE sessions SET status='ended' WHERE id=?", (session_id,))


def create_team(session_id, club_name, captain_id=None):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO teams (session_id, club_name, captain_id) VALUES (?, ?, ?)",
            (session_id, club_name, captain_id),
        )
        return cur.lastrowid


def set_team_voice_channel(team_id, voice_channel_id):
    with get_conn() as conn:
        conn.execute("UPDATE teams SET voice_channel_id=? WHERE id=?", (voice_channel_id, team_id))


def get_teams_for_session(session_id):
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM teams WHERE session_id=?", (session_id,)).fetchall()
        return [dict(r) for r in rows]


def add_team_member(team_id, player_id, role="player"):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO team_members (team_id, player_id, role) VALUES (?, ?, ?)",
            (team_id, player_id, role),
        )


def set_member_role(team_id, player_id, role):
    with get_conn() as conn:
        conn.execute(
            "UPDATE team_members SET role=? WHERE team_id=? AND player_id=?",
            (role, team_id, player_id),
        )


def get_team_members(team_id, role=None):
    with get_conn() as conn:
        if role:
            rows = conn.execute(
                "SELECT * FROM team_members WHERE team_id=? AND role=?", (team_id, role)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM team_members WHERE team_id=?", (team_id,)).fetchall()
        return [dict(r) for r in rows]


def create_matches(session_id, rounds):
    """rounds: list of rounds, each a list of (team_a_id, team_b_id) tuples."""
    with get_conn() as conn:
        for round_no, pairs in enumerate(rounds, start=1):
            for team_a_id, team_b_id in pairs:
                conn.execute(
                    "INSERT INTO matches (session_id, team_a_id, team_b_id, round_no) VALUES (?, ?, ?, ?)",
                    (session_id, team_a_id, team_b_id, round_no),
                )


def get_matches_for_session(session_id):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM matches WHERE session_id=? ORDER BY round_no", (session_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def report_match_result(match_id, winner_team_id, score_a=None, score_b=None):
    with get_conn() as conn:
        conn.execute(
            "UPDATE matches SET winner_team_id=?, score_a=?, score_b=?, status='reported', "
            "reported_at=CURRENT_TIMESTAMP WHERE id=?",
            (winner_team_id, score_a, score_b, match_id),
        )


def get_match(match_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM matches WHERE id=?", (match_id,)).fetchone()
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# Match/club history (per-guild) - powers /player_stats and /club_stats
# ---------------------------------------------------------------------------

def record_match_participants(guild_id, match_id, session_id, team_a_id, team_b_id,
                                team_a_player_ids, team_b_player_ids,
                                club_a, club_b, a_won):
    """Log one row per player for this match, and one row per side for the club."""
    with get_conn() as conn:
        for pid in team_a_player_ids:
            conn.execute(
                "INSERT INTO match_participants (guild_id, match_id, session_id, player_id, team_id, club_name, result) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (guild_id, match_id, session_id, pid, team_a_id, club_a, "win" if a_won else "loss"),
            )
        for pid in team_b_player_ids:
            conn.execute(
                "INSERT INTO match_participants (guild_id, match_id, session_id, player_id, team_id, club_name, result) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (guild_id, match_id, session_id, pid, team_b_id, club_b, "loss" if a_won else "win"),
            )
        conn.execute(
            "INSERT INTO club_match_results (guild_id, match_id, club_name, result) VALUES (?, ?, ?, ?)",
            (guild_id, match_id, club_a, "win" if a_won else "loss"),
        )
        conn.execute(
            "INSERT INTO club_match_results (guild_id, match_id, club_name, result) VALUES (?, ?, ?, ?)",
            (guild_id, match_id, club_b, "loss" if a_won else "win"),
        )


def get_player_recent_form(guild_id, player_id, limit=10):
    """Most recent results first, e.g. ['W','W','L','W']."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT result FROM match_participants WHERE guild_id=? AND player_id=? ORDER BY id DESC LIMIT ?",
            (guild_id, player_id, limit),
        ).fetchall()
        return ["W" if r["result"] == "win" else "L" for r in rows]


def get_player_record(guild_id, player_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT "
            "SUM(CASE WHEN result='win' THEN 1 ELSE 0 END) AS wins, "
            "SUM(CASE WHEN result='loss' THEN 1 ELSE 0 END) AS losses "
            "FROM match_participants WHERE guild_id=? AND player_id=?",
            (guild_id, player_id),
        ).fetchone()
        return {"wins": row["wins"] or 0, "losses": row["losses"] or 0}


def get_player_best_club(guild_id, player_id):
    """Club this player has the most WINS with (min 1 win)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT club_name, COUNT(*) AS wins FROM match_participants "
            "WHERE guild_id=? AND player_id=? AND result='win' GROUP BY club_name ORDER BY wins DESC LIMIT 1",
            (guild_id, player_id),
        ).fetchone()
        return dict(row) if row else None


def get_player_most_played_with(guild_id, player_id):
    """Teammate this player has shared a team_id/match with most often."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT mp2.player_id AS teammate_id, COUNT(*) AS games "
            "FROM match_participants mp1 "
            "JOIN match_participants mp2 "
            "  ON mp1.match_id = mp2.match_id AND mp1.team_id = mp2.team_id AND mp1.player_id != mp2.player_id "
            "WHERE mp1.guild_id=? AND mp1.player_id=? "
            "GROUP BY mp2.player_id ORDER BY games DESC LIMIT 1",
            (guild_id, player_id),
        ).fetchone()
        return dict(row) if row else None


def get_club_record(guild_id, club_name):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT "
            "SUM(CASE WHEN result='win' THEN 1 ELSE 0 END) AS wins, "
            "SUM(CASE WHEN result='loss' THEN 1 ELSE 0 END) AS losses "
            "FROM club_match_results WHERE guild_id=? AND club_name=?",
            (guild_id, club_name),
        ).fetchone()
        return {"wins": row["wins"] or 0, "losses": row["losses"] or 0}


def get_club_recent_form(guild_id, club_name, limit=10):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT result FROM club_match_results WHERE guild_id=? AND club_name=? ORDER BY id DESC LIMIT ?",
            (guild_id, club_name, limit),
        ).fetchall()
        return ["W" if r["result"] == "win" else "L" for r in rows]


def get_club_best_run(guild_id, club_name):
    """Longest consecutive win streak in chronological order."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT result FROM club_match_results WHERE guild_id=? AND club_name=? ORDER BY id ASC",
            (guild_id, club_name),
        ).fetchall()
    best = current = 0
    for r in rows:
        if r["result"] == "win":
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def get_club_top_player(guild_id, club_name):
    """The player with the most WINS for this club (min 1 win)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT player_id, COUNT(*) AS wins FROM match_participants "
            "WHERE guild_id=? AND club_name=? AND result='win' GROUP BY player_id ORDER BY wins DESC LIMIT 1",
            (guild_id, club_name),
        ).fetchone()
        return dict(row) if row else None


def get_all_known_club_names(guild_id):
    """Includes clubs that have match history even if later removed from the pool."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT club_name FROM club_match_results WHERE guild_id=? ORDER BY club_name", (guild_id,)
        ).fetchall()
        return [r["club_name"] for r in rows]


# ---------------------------------------------------------------------------
# Per-guild permanent channel config (in-progress / leaderboard)
# ---------------------------------------------------------------------------

def get_guild_config(guild_id: int):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM guild_config WHERE guild_id=?", (guild_id,)).fetchone()
        return dict(row) if row else None


def upsert_guild_config(guild_id: int, **fields):
    """fields may include progress_channel_id, leaderboard_channel_id, leaderboard_message_id."""
    if not fields:
        return
    with get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO guild_config (guild_id) VALUES (?)", (guild_id,))
        cols = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE guild_config SET {cols} WHERE guild_id=?", (*fields.values(), guild_id))

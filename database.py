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
    is_na           INTEGER NOT NULL DEFAULT 0,
    is_ghost        INTEGER NOT NULL DEFAULT 0,
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
    winning_team_id INTEGER,
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
    mmr_delta       INTEGER NOT NULL DEFAULT 0,  -- exact MMR change this match caused, so it can be precisely reversed later
    mode            TEXT,            -- 'rivals' | 'league' - lets player recent form/streak split by mode too
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

-- One row per side per reported match: powers /club_stats.
CREATE TABLE IF NOT EXISTS club_match_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    match_id        INTEGER NOT NULL,
    club_name       TEXT NOT NULL,
    result          TEXT NOT NULL,   -- 'win' | 'loss'
    mode            TEXT,            -- 'rivals' | 'league' - lets club records split by mode too
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

-- One row per guild: where the PERMANENT in-progress / leaderboard channels
-- live, set up once via /ntf_setup and reused by every session after that.
CREATE TABLE IF NOT EXISTS guild_config (
    guild_id                INTEGER PRIMARY KEY,
    progress_channel_id     INTEGER,
    leaderboard_channel_id  INTEGER,
    leaderboard_message_id  INTEGER,
    queue_channel_id        INTEGER,
    history_channel_id      INTEGER,
    leaderboard_message_id_rivals  INTEGER,
    leaderboard_message_id_league  INTEGER,
    admin_log_channel_id    INTEGER
);

-- Separate MMR/wins/losses per mode (rivals vs league). Captain and NA
-- status stay on the players table above - those are shared across both
-- modes by design, only MMR/win-loss are split.
CREATE TABLE IF NOT EXISTS player_mode_stats (
    guild_id        INTEGER NOT NULL,
    discord_id      INTEGER NOT NULL,
    mode            TEXT NOT NULL,               -- 'rivals' | 'league'
    mmr             INTEGER NOT NULL DEFAULT 1200,
    wins            INTEGER NOT NULL DEFAULT 0,
    losses          INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, discord_id, mode)
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
        # Lightweight migration: CREATE TABLE IF NOT EXISTS won't add a new
        # column to a table that already exists (which it does, on the
        # persistent volume, for anyone who set this bot up before this
        # column was added) - so add it here if it's missing.
        try:
            conn.execute("ALTER TABLE players ADD COLUMN is_na INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass  # column already exists
        try:
            conn.execute("ALTER TABLE players ADD COLUMN is_ghost INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass  # column already exists
        try:
            conn.execute("ALTER TABLE match_participants ADD COLUMN mmr_delta INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass  # column already exists
        try:
            conn.execute("ALTER TABLE guild_config ADD COLUMN admin_log_channel_id INTEGER")
        except sqlite3.OperationalError:
            pass  # column already exists
        try:
            conn.execute("ALTER TABLE sessions ADD COLUMN winning_team_id INTEGER")
        except sqlite3.OperationalError:
            pass  # column already exists
        try:
            conn.execute("ALTER TABLE guild_config ADD COLUMN history_channel_id INTEGER")
        except sqlite3.OperationalError:
            pass  # column already exists
        try:
            conn.execute("ALTER TABLE guild_config ADD COLUMN leaderboard_message_id_rivals INTEGER")
        except sqlite3.OperationalError:
            pass  # column already exists
        try:
            conn.execute("ALTER TABLE guild_config ADD COLUMN leaderboard_message_id_league INTEGER")
        except sqlite3.OperationalError:
            pass  # column already exists
        try:
            conn.execute("ALTER TABLE club_match_results ADD COLUMN mode TEXT")
        except sqlite3.OperationalError:
            pass  # column already exists
        try:
            conn.execute("ALTER TABLE match_participants ADD COLUMN mode TEXT")
        except sqlite3.OperationalError:
            pass  # column already exists

        # One-time backfill: existing club_match_results rows predate the
        # mode column and have NULL there - fill them in from the session
        # each match actually belonged to, via the same join path used at
        # query time, so historical club records split correctly by mode
        # too instead of just the rows recorded going forward.
        conn.execute(
            "UPDATE club_match_results SET mode = ("
            "  SELECT s.mode FROM matches m JOIN sessions s ON m.session_id = s.id "
            "  WHERE m.id = club_match_results.match_id"
            ") WHERE mode IS NULL"
        )

        # Same backfill for match_participants, so historical player recent
        # form/streak split correctly by mode too, not just new results.
        conn.execute(
            "UPDATE match_participants SET mode = ("
            "  SELECT s.mode FROM matches m JOIN sessions s ON m.session_id = s.id "
            "  WHERE m.id = match_participants.match_id"
            ") WHERE mode IS NULL"
        )

        # One-time backfill: seed player_mode_stats for every existing
        # player from their current combined mmr/wins/losses, so nobody's
        # progress vanishes the moment MMR splits into per-mode tracking.
        # INSERT OR IGNORE makes this safe to run on every startup - once a
        # player has real rivals/league rows, this never touches them again.
        existing_players = conn.execute("SELECT guild_id, discord_id, mmr, wins, losses FROM players").fetchall()
        for p in existing_players:
            for mode in ("rivals", "league"):
                conn.execute(
                    "INSERT OR IGNORE INTO player_mode_stats (guild_id, discord_id, mode, mmr, wins, losses) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (p["guild_id"], p["discord_id"], mode, p["mmr"], p["wins"], p["losses"]),
                )


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


def set_na(guild_id: int, discord_id: int, is_na: bool):
    with get_conn() as conn:
        conn.execute(
            "UPDATE players SET is_na=? WHERE guild_id=? AND discord_id=?",
            (1 if is_na else 0, guild_id, discord_id),
        )


def get_na_players(guild_id: int):
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM players WHERE guild_id=? AND is_na=1", (guild_id,)).fetchall()
        return [dict(r) for r in rows]


def set_ghost(guild_id: int, discord_id: int, is_ghost: bool):
    with get_conn() as conn:
        conn.execute(
            "UPDATE players SET is_ghost=? WHERE guild_id=? AND discord_id=?",
            (1 if is_ghost else 0, guild_id, discord_id),
        )


def get_ghosts(guild_id: int):
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM players WHERE guild_id=? AND is_ghost=1", (guild_id,)).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Per-mode MMR/wins/losses - captain and NA status above stay shared across
# both modes; only these are actually split.
# ---------------------------------------------------------------------------

def ensure_player_mode_stats(guild_id: int, discord_id: int, mode: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO player_mode_stats (guild_id, discord_id, mode, mmr) VALUES (?, ?, ?, ?)",
            (guild_id, discord_id, mode, config.STARTING_MMR),
        )


def get_player_mode_stats(guild_id: int, discord_id: int, mode: str):
    """Auto-creates a default row (starting MMR, 0W-0L) if this player has
    never had a result recorded in this specific mode yet."""
    ensure_player_mode_stats(guild_id, discord_id, mode)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM player_mode_stats WHERE guild_id=? AND discord_id=? AND mode=?",
            (guild_id, discord_id, mode),
        ).fetchone()
        return dict(row)


def update_mode_mmr(guild_id: int, discord_id: int, mode: str, new_mmr: int, won: bool):
    """The per-mode equivalent of update_mmr - records a full match result
    (new MMR + win/loss increment) for this specific mode only."""
    ensure_player_mode_stats(guild_id, discord_id, mode)
    with get_conn() as conn:
        if won:
            conn.execute(
                "UPDATE player_mode_stats SET mmr=?, wins=wins+1 WHERE guild_id=? AND discord_id=? AND mode=?",
                (new_mmr, guild_id, discord_id, mode),
            )
        else:
            conn.execute(
                "UPDATE player_mode_stats SET mmr=?, losses=losses+1 WHERE guild_id=? AND discord_id=? AND mode=?",
                (new_mmr, guild_id, discord_id, mode),
            )


def set_mode_mmr(guild_id: int, discord_id: int, mode: str, new_mmr: int):
    """Admin correction - sets MMR directly for one mode, without touching
    win/loss counts."""
    ensure_player_mode_stats(guild_id, discord_id, mode)
    with get_conn() as conn:
        conn.execute(
            "UPDATE player_mode_stats SET mmr=? WHERE guild_id=? AND discord_id=? AND mode=?",
            (max(0, new_mmr), guild_id, discord_id, mode),
        )


def bump_mode_mmr(guild_id: int, discord_id: int, mode: str, amount: int):
    """Adds amount to a player's MMR in one mode without touching win/loss -
    used for the session-win bonus."""
    ensure_player_mode_stats(guild_id, discord_id, mode)
    with get_conn() as conn:
        conn.execute(
            "UPDATE player_mode_stats SET mmr=MAX(0, mmr+?) WHERE guild_id=? AND discord_id=? AND mode=?",
            (amount, guild_id, discord_id, mode),
        )


def deduct_mode_mmr(guild_id: int, discord_id: int, mode: str, amount: int):
    ensure_player_mode_stats(guild_id, discord_id, mode)
    with get_conn() as conn:
        conn.execute(
            "UPDATE player_mode_stats SET mmr=MAX(0, mmr-?) WHERE guild_id=? AND discord_id=? AND mode=?",
            (abs(amount), guild_id, discord_id, mode),
        )


def add_mode_wins(guild_id: int, discord_id: int, mode: str, amount: int):
    ensure_player_mode_stats(guild_id, discord_id, mode)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT wins FROM player_mode_stats WHERE guild_id=? AND discord_id=? AND mode=?",
            (guild_id, discord_id, mode),
        ).fetchone()
        new_wins = row["wins"] + abs(amount)
        conn.execute(
            "UPDATE player_mode_stats SET wins=? WHERE guild_id=? AND discord_id=? AND mode=?",
            (new_wins, guild_id, discord_id, mode),
        )
        return new_wins


def deduct_mode_wins(guild_id: int, discord_id: int, mode: str, amount: int):
    ensure_player_mode_stats(guild_id, discord_id, mode)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT wins FROM player_mode_stats WHERE guild_id=? AND discord_id=? AND mode=?",
            (guild_id, discord_id, mode),
        ).fetchone()
        new_wins = max(0, row["wins"] - abs(amount))
        conn.execute(
            "UPDATE player_mode_stats SET wins=? WHERE guild_id=? AND discord_id=? AND mode=?",
            (new_wins, guild_id, discord_id, mode),
        )
        return new_wins


def add_mode_losses(guild_id: int, discord_id: int, mode: str, amount: int):
    ensure_player_mode_stats(guild_id, discord_id, mode)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT losses FROM player_mode_stats WHERE guild_id=? AND discord_id=? AND mode=?",
            (guild_id, discord_id, mode),
        ).fetchone()
        new_losses = row["losses"] + abs(amount)
        conn.execute(
            "UPDATE player_mode_stats SET losses=? WHERE guild_id=? AND discord_id=? AND mode=?",
            (new_losses, guild_id, discord_id, mode),
        )
        return new_losses


def deduct_mode_losses(guild_id: int, discord_id: int, mode: str, amount: int):
    ensure_player_mode_stats(guild_id, discord_id, mode)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT losses FROM player_mode_stats WHERE guild_id=? AND discord_id=? AND mode=?",
            (guild_id, discord_id, mode),
        ).fetchone()
        new_losses = max(0, row["losses"] - abs(amount))
        conn.execute(
            "UPDATE player_mode_stats SET losses=? WHERE guild_id=? AND discord_id=? AND mode=?",
            (new_losses, guild_id, discord_id, mode),
        )
        return new_losses


def mode_leaderboard(guild_id: int, mode: str, limit=20, offset=0):
    """Same shape as leaderboard() but scoped to one mode - joins in
    display_name/is_captain from players for convenience."""
    with get_conn() as conn:
        if limit is None:
            rows = conn.execute(
                "SELECT p.discord_id, p.display_name, p.is_captain, pms.mmr, pms.wins, pms.losses "
                "FROM player_mode_stats pms JOIN players p ON pms.guild_id=p.guild_id AND pms.discord_id=p.discord_id "
                "WHERE pms.guild_id=? AND pms.mode=? ORDER BY pms.mmr DESC",
                (guild_id, mode),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT p.discord_id, p.display_name, p.is_captain, pms.mmr, pms.wins, pms.losses "
                "FROM player_mode_stats pms JOIN players p ON pms.guild_id=p.guild_id AND pms.discord_id=p.discord_id "
                "WHERE pms.guild_id=? AND pms.mode=? ORDER BY pms.mmr DESC LIMIT ? OFFSET ?",
                (guild_id, mode, limit, offset),
            ).fetchall()
        return [dict(r) for r in rows]


def count_mode_players(guild_id: int, mode: str) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM player_mode_stats WHERE guild_id=? AND mode=?", (guild_id, mode)
        ).fetchone()
        return row["c"]


def get_mode_rank_position(guild_id: int, discord_id: int, mode: str):
    """1-based position on this guild's per-mode MMR leaderboard, or None
    if untracked in that mode."""
    ensure_player_mode_stats(guild_id, discord_id, mode)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT discord_id FROM player_mode_stats WHERE guild_id=? AND mode=? ORDER BY mmr DESC",
            (guild_id, mode),
        ).fetchall()
        for i, r in enumerate(rows, start=1):
            if r["discord_id"] == discord_id:
                return i
        return None


def reset_mode_leaderboard(guild_id: int, mode: str):
    """Season reset for one mode only - the other mode's stats are
    untouched. Run once per mode if you want both reset."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE player_mode_stats SET mmr=?, wins=0, losses=0 WHERE guild_id=? AND mode=?",
            (config.STARTING_MMR, guild_id, mode),
        )


def prune_left_members_mode_stats(guild_id: int, active_discord_ids: set):
    """Companion to prune_left_members - also removes per-mode stats rows
    for anyone no longer in the server, across both modes."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT discord_id FROM player_mode_stats WHERE guild_id=?", (guild_id,)
        ).fetchall()
        tracked_ids = {r["discord_id"] for r in rows}
        to_remove = tracked_ids - active_discord_ids
        for discord_id in to_remove:
            conn.execute("DELETE FROM player_mode_stats WHERE guild_id=? AND discord_id=?", (guild_id, discord_id))
        return len(to_remove)


def set_mmr(guild_id: int, discord_id: int, new_mmr: int):
    """Pure MMR correction - does not touch win/loss counters."""
    with get_conn() as conn:
        conn.execute("UPDATE players SET mmr=? WHERE guild_id=? AND discord_id=?", (new_mmr, guild_id, discord_id))


def bump_mmr(guild_id: int, discord_id: int, delta: int):
    """Adds delta to a player's current MMR without touching win/loss counts
    or requiring the caller to know their current rating first - used for
    flat bonuses like the session-win bonus, as opposed to a full match
    result (which goes through update_mmr instead)."""
    with get_conn() as conn:
        conn.execute("UPDATE players SET mmr = mmr + ? WHERE guild_id=? AND discord_id=?", (delta, guild_id, discord_id))


def reset_leaderboard(guild_id: int):
    """Season reset: every player in this guild goes back to the starting
    MMR with a clean win/loss record. Match/club history is left untouched -
    only current standing resets, not the historical record."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE players SET mmr=?, wins=0, losses=0 WHERE guild_id=?",
            (config.STARTING_MMR, guild_id),
        )


def reset_club_records(guild_id: int, mode: str):
    """Season reset for clubs in ONE mode only - the other mode's club
    records are untouched (and so is best run / recent form, since those are
    derived from the same rows), matching how player MMR resets per mode.
    The club NAME pool itself (/club add /club remove) is untouched - this
    only clears the match history behind /club_stats for this mode. Returns
    how many rows were deleted."""
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM club_match_results WHERE guild_id=? AND mode=?", (guild_id, mode))
        return cur.rowcount


def add_wins(guild_id: int, discord_id: int, amount: int):
    """Adds to a player's win count directly, without touching MMR - for
    manually crediting a win that wasn't reported through a session
    (e.g. an undercount correction). Returns the new count."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT wins FROM players WHERE guild_id=? AND discord_id=?", (guild_id, discord_id)
        ).fetchone()
        current = row["wins"] if row else 0
        new_wins = current + abs(amount)
        conn.execute(
            "UPDATE players SET wins=? WHERE guild_id=? AND discord_id=?",
            (new_wins, guild_id, discord_id),
        )
        return new_wins


def add_losses(guild_id: int, discord_id: int, amount: int):
    """Adds to a player's loss count directly, without touching MMR - same
    reasoning as add_wins. Returns the new count."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT losses FROM players WHERE guild_id=? AND discord_id=?", (guild_id, discord_id)
        ).fetchone()
        current = row["losses"] if row else 0
        new_losses = current + abs(amount)
        conn.execute(
            "UPDATE players SET losses=? WHERE guild_id=? AND discord_id=?",
            (new_losses, guild_id, discord_id),
        )
        return new_losses


def deduct_wins(guild_id: int, discord_id: int, amount: int):
    """Reduces a player's win count by amount (never below 0), without
    touching their MMR - for correcting a mistakenly-recorded win or
    penalizing a banned player's record specifically. Returns the new count."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT wins FROM players WHERE guild_id=? AND discord_id=?", (guild_id, discord_id)
        ).fetchone()
        current = row["wins"] if row else 0
        new_wins = max(0, current - abs(amount))
        conn.execute(
            "UPDATE players SET wins=? WHERE guild_id=? AND discord_id=?",
            (new_wins, guild_id, discord_id),
        )
        return new_wins


def deduct_losses(guild_id: int, discord_id: int, amount: int):
    """Reduces a player's loss count by amount (never below 0), without
    touching their MMR - same reasoning as deduct_wins. Returns the new count."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT losses FROM players WHERE guild_id=? AND discord_id=?", (guild_id, discord_id)
        ).fetchone()
        current = row["losses"] if row else 0
        new_losses = max(0, current - abs(amount))
        conn.execute(
            "UPDATE players SET losses=? WHERE guild_id=? AND discord_id=?",
            (new_losses, guild_id, discord_id),
        )
        return new_losses


def update_mmr(guild_id: int, discord_id: int, new_mmr: int, won: bool):
    """Records a full match result: sets the new MMR AND increments the
    win or loss counter accordingly. This is the function every reported
    match goes through - set_mmr/bump_mmr are for corrections/bonuses that
    should NOT touch the win/loss record."""
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


def leaderboard(guild_id: int, limit=20, offset=0):
    with get_conn() as conn:
        if limit is None:
            rows = conn.execute(
                "SELECT * FROM players WHERE guild_id=? ORDER BY mmr DESC", (guild_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM players WHERE guild_id=? ORDER BY mmr DESC LIMIT ? OFFSET ?", (guild_id, limit, offset)
            ).fetchall()
        return [dict(r) for r in rows]


def count_players(guild_id: int) -> int:
    with get_conn() as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM players WHERE guild_id=?", (guild_id,)).fetchone()
        return row["c"]


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


def set_session_winner(session_id, winning_team_id):
    with get_conn() as conn:
        conn.execute("UPDATE sessions SET winning_team_id=? WHERE id=?", (winning_team_id, session_id))


def get_session_history(guild_id, limit=10, mode=None):
    """Most recent completed sessions first, with the winning club's name
    and captain resolved via a join - only includes sessions that actually
    finished with a recorded winner (test sessions never set one, so they
    won't show up here). Pass mode='rivals' or 'league' to filter to just
    that mode; omit it to see both mixed together, most recent first."""
    with get_conn() as conn:
        if mode:
            rows = conn.execute(
                "SELECT s.id, s.mode, s.created_at, t.club_name AS winning_club, t.captain_id AS winning_captain "
                "FROM sessions s "
                "JOIN teams t ON s.winning_team_id = t.id "
                "WHERE s.guild_id=? AND s.status='ended' AND s.mode=? "
                "ORDER BY s.created_at DESC LIMIT ?",
                (guild_id, mode, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT s.id, s.mode, s.created_at, t.club_name AS winning_club, t.captain_id AS winning_captain "
                "FROM sessions s "
                "JOIN teams t ON s.winning_team_id = t.id "
                "WHERE s.guild_id=? AND s.status='ended' "
                "ORDER BY s.created_at DESC LIMIT ?",
                (guild_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]


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


def undo_match_result(match_id):
    """Reverses a previously-reported match: undoes each participant's exact
    recorded MMR delta and win/loss increment, deletes their
    match_participants and club_match_results rows for this match, and
    resets the match back to pending so it can be re-reported correctly.
    Returns the list of affected discord_ids, or None if the match was
    never actually reported (nothing to undo).
    Note: matches reported before mmr_delta existed have it stored as 0, so
    their win/loss count will still be correctly reversed but their MMR
    won't move - a known limitation for pre-existing data only."""
    with get_conn() as conn:
        match = conn.execute("SELECT * FROM matches WHERE id=?", (match_id,)).fetchone()
        if not match or match["status"] != "reported":
            return None

        session_row = conn.execute("SELECT mode FROM sessions WHERE id=?", (match["session_id"],)).fetchone()
        mode = session_row["mode"] if session_row else None

        participants = conn.execute(
            "SELECT * FROM match_participants WHERE match_id=?", (match_id,)
        ).fetchall()

        affected = []
        for p in participants:
            if mode:
                if p["result"] == "win":
                    conn.execute(
                        "UPDATE player_mode_stats SET mmr=MAX(0, mmr-?), wins=MAX(0, wins-1) "
                        "WHERE guild_id=? AND discord_id=? AND mode=?",
                        (p["mmr_delta"], p["guild_id"], p["player_id"], mode),
                    )
                else:
                    conn.execute(
                        "UPDATE player_mode_stats SET mmr=MAX(0, mmr-?), losses=MAX(0, losses-1) "
                        "WHERE guild_id=? AND discord_id=? AND mode=?",
                        (p["mmr_delta"], p["guild_id"], p["player_id"], mode),
                    )
            affected.append(p["player_id"])

        conn.execute("DELETE FROM match_participants WHERE match_id=?", (match_id,))
        conn.execute("DELETE FROM club_match_results WHERE match_id=?", (match_id,))
        conn.execute(
            "UPDATE matches SET status='pending', winner_team_id=NULL, score_a=NULL, score_b=NULL, reported_at=NULL WHERE id=?",
            (match_id,),
        )
        return affected


def get_match(match_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM matches WHERE id=?", (match_id,)).fetchone()
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# Match/club history (per-guild) - powers /player_stats and /club_stats
# ---------------------------------------------------------------------------

def record_match_participants(guild_id, match_id, session_id, team_a_id, team_b_id,
                                team_a_player_ids, team_b_player_ids,
                                club_a, club_b, a_won, mode, mmr_deltas=None):
    """Log one row per player for this match, and one row per side for the
    club. mode tags both the player rows (recent form/streak split by mode)
    and the club rows (club records split by mode too).
    mmr_deltas: optional {discord_id: delta} - the exact MMR change this
    match caused for each player, so a later /undo_match_result can reverse
    it precisely rather than guessing. Defaults to 0 if not given."""
    mmr_deltas = mmr_deltas or {}
    with get_conn() as conn:
        for pid in team_a_player_ids:
            conn.execute(
                "INSERT INTO match_participants (guild_id, match_id, session_id, player_id, team_id, club_name, result, mmr_delta, mode) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (guild_id, match_id, session_id, pid, team_a_id, club_a, "win" if a_won else "loss", mmr_deltas.get(pid, 0), mode),
            )
        for pid in team_b_player_ids:
            conn.execute(
                "INSERT INTO match_participants (guild_id, match_id, session_id, player_id, team_id, club_name, result, mmr_delta, mode) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (guild_id, match_id, session_id, pid, team_b_id, club_b, "loss" if a_won else "win", mmr_deltas.get(pid, 0), mode),
            )
        conn.execute(
            "INSERT INTO club_match_results (guild_id, match_id, club_name, result, mode) VALUES (?, ?, ?, ?, ?)",
            (guild_id, match_id, club_a, "win" if a_won else "loss", mode),
        )
        conn.execute(
            "INSERT INTO club_match_results (guild_id, match_id, club_name, result, mode) VALUES (?, ?, ?, ?, ?)",
            (guild_id, match_id, club_b, "loss" if a_won else "win", mode),
        )


def get_player_recent_form(guild_id, player_id, mode, limit=10):
    """Most recent results first in this specific mode, e.g. ['W','W','L','W']."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT result FROM match_participants WHERE guild_id=? AND player_id=? AND mode=? ORDER BY id DESC LIMIT ?",
            (guild_id, player_id, mode, limit),
        ).fetchall()
        return ["W" if r["result"] == "win" else "L" for r in rows]


def get_player_streak(guild_id, player_id, mode):
    """Returns (streak_type, count) for this player in this mode specifically
    - streak_type is 'W' or 'L', count is how many consecutive results of
    that type they currently have, walking back from their most recent
    match in this mode. Returns (None, 0) if they have no match history in
    this mode yet."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT result FROM match_participants WHERE guild_id=? AND player_id=? AND mode=? ORDER BY id DESC",
            (guild_id, player_id, mode),
        ).fetchall()
    if not rows:
        return None, 0
    current_result = rows[0]["result"]
    count = 0
    for r in rows:
        if r["result"] == current_result:
            count += 1
        else:
            break
    return ("W" if current_result == "win" else "L"), count


def get_head_to_head(guild_id, player_a_id, player_b_id):
    """Returns (a_wins, b_wins, total_meetings) counting only matches where
    these two players were on OPPOSING teams (teammate matches don't count
    as a head-to-head result for either of them)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT mp_a.result AS a_result "
            "FROM match_participants mp_a "
            "JOIN match_participants mp_b "
            "  ON mp_a.match_id = mp_b.match_id AND mp_a.team_id != mp_b.team_id "
            "WHERE mp_a.guild_id=? AND mp_a.player_id=? AND mp_b.player_id=?",
            (guild_id, player_a_id, player_b_id),
        ).fetchall()
    a_wins = sum(1 for r in rows if r["a_result"] == "win")
    b_wins = sum(1 for r in rows if r["a_result"] == "loss")
    return a_wins, b_wins, len(rows)


def prune_left_members(guild_id: int, active_discord_ids: set):
    """Removes any tracked player from this guild who is no longer an
    actual member of the server (left, kicked, banned) - active_discord_ids
    is the current real member list, fetched fresh from Discord by the
    caller. Only deletes the players row itself; match/club history stays
    intact for reference (the same "keep history, only clear standings"
    principle as /season_reset). Returns how many were removed."""
    with get_conn() as conn:
        rows = conn.execute("SELECT discord_id FROM players WHERE guild_id=?", (guild_id,)).fetchall()
        tracked_ids = {r["discord_id"] for r in rows}
        to_remove = tracked_ids - active_discord_ids
        for discord_id in to_remove:
            conn.execute("DELETE FROM players WHERE guild_id=? AND discord_id=?", (guild_id, discord_id))
        return len(to_remove)


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


def get_club_record(guild_id, club_name, mode):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT "
            "SUM(CASE WHEN result='win' THEN 1 ELSE 0 END) AS wins, "
            "SUM(CASE WHEN result='loss' THEN 1 ELSE 0 END) AS losses "
            "FROM club_match_results WHERE guild_id=? AND club_name=? AND mode=?",
            (guild_id, club_name, mode),
        ).fetchone()
        return {"wins": row["wins"] or 0, "losses": row["losses"] or 0}


def get_club_recent_form(guild_id, club_name, mode, limit=10):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT result FROM club_match_results WHERE guild_id=? AND club_name=? AND mode=? ORDER BY id DESC LIMIT ?",
            (guild_id, club_name, mode, limit),
        ).fetchall()
        return ["W" if r["result"] == "win" else "L" for r in rows]


def get_club_streak(guild_id, club_name, mode):
    """Returns (streak_type, count) for this club in this mode specifically -
    same shape as get_player_streak. Returns (None, 0) if the club has no
    recorded results in this mode yet."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT result FROM club_match_results WHERE guild_id=? AND club_name=? AND mode=? ORDER BY id DESC",
            (guild_id, club_name, mode),
        ).fetchall()
    if not rows:
        return None, 0
    current_result = rows[0]["result"]
    count = 0
    for r in rows:
        if r["result"] == current_result:
            count += 1
        else:
            break
    return ("W" if current_result == "win" else "L"), count


def get_club_best_run(guild_id, club_name, mode):
    """Longest consecutive win streak in chronological order, for this mode specifically."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT result FROM club_match_results WHERE guild_id=? AND club_name=? AND mode=? ORDER BY id ASC",
            (guild_id, club_name, mode),
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


def purge_club_history(guild_id: int, club_name: str):
    """Wipes ALL match/club history for a specific club name - removes it
    from /club_stats and its autocomplete entirely. Does NOT touch the
    current club pool - use /club remove separately for that if it's still
    in there. Returns how many total rows were deleted."""
    with get_conn() as conn:
        cur1 = conn.execute(
            "DELETE FROM match_participants WHERE guild_id=? AND club_name=?", (guild_id, club_name)
        )
        cur2 = conn.execute(
            "DELETE FROM club_match_results WHERE guild_id=? AND club_name=?", (guild_id, club_name)
        )
        return cur1.rowcount + cur2.rowcount


def clear_test_players(guild_id: int):
    """Removes every synthetic test-bot account (negative discord_id, from
    /debug_test_session) for this guild, along with their match history rows.
    Returns how many player rows were deleted. Never touches real players
    (positive Discord snowflakes) or real match/club history."""
    with get_conn() as conn:
        cur = conn.execute("SELECT COUNT(*) AS c FROM players WHERE guild_id=? AND discord_id < 0", (guild_id,))
        count = cur.fetchone()["c"]
        conn.execute("DELETE FROM match_participants WHERE guild_id=? AND player_id < 0", (guild_id,))
        conn.execute("DELETE FROM players WHERE guild_id=? AND discord_id < 0", (guild_id,))
        return count


# ---------------------------------------------------------------------------
# Per-guild permanent channel config (in-progress / leaderboard)
# ---------------------------------------------------------------------------

def get_guild_config(guild_id: int):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM guild_config WHERE guild_id=?", (guild_id,)).fetchone()
        return dict(row) if row else None


def upsert_guild_config(guild_id: int, **fields):
    """fields may include progress_channel_id, leaderboard_channel_id, leaderboard_message_id, queue_channel_id, history_channel_id."""
    if not fields:
        return
    with get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO guild_config (guild_id) VALUES (?)", (guild_id,))
        cols = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE guild_config SET {cols} WHERE guild_id=?", (*fields.values(), guild_id))

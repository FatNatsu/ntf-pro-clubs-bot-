"""Season reset keeps every season's history, tagged by season (Founder decision, 27 Nov 2026)."""
import sys, os, tempfile
repo = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ["PRO_CLUBS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, repo)
import database as db
db.init_db()


def mk(g, status="ended"):
    sid = db.create_session(g, "league")
    with db.get_conn() as c:
        c.execute("UPDATE sessions SET status=? WHERE id=?", (status, sid))
        t = c.execute("INSERT INTO teams (session_id,club_name) VALUES (?,?)", (sid, "A")).lastrowid
        m = c.execute("INSERT INTO matches (session_id,team_a_id,team_b_id,round_no) VALUES (?,?,?,1)", (sid, t, t)).lastrowid
    db.record_match_participants(g, m, sid, t, t, [1], [], "A", "A", True, "league")
    return sid


s1, s2, s3 = mk(1), mk(1), mk(1, "active")
mk(2)
assert [db.session_number(s) for s in (s1, s2, s3)] == [1, 2, 3]      # numbered within the season
assert db.reset_sessions(1) == 3
with db.get_conn() as c:
    assert c.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 4           # nothing deleted
    assert c.execute("SELECT COUNT(*) FROM match_participants").fetchone()[0] == 4
    assert c.execute("SELECT status FROM sessions WHERE id=?", (s3,)).fetchone()[0] == "ended"
# the next season counts from #1 again, and its stats start empty
db.upsert_guild_config(1, season_number=2)
s4 = mk(1)
assert db.session_number(s4) == 1 and db.session_number(s1) == 1
assert db.get_player_recent_form(1, 1, "league") == ["W"]               # only this season's game
assert db.get_player_recent_form(1, 1, "league", season=1) == ["W", "W", "W"]
assert db.get_club_record(1, "A", "league")["wins"] == 1                 # club stats: this season only
# another server is never affected
assert db.session_number(mk(2)) == 2
print("RESET OK")

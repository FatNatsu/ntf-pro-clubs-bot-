import sys, os, tempfile
repo=sys.argv[1]; sys.path.insert(0, repo)
os.environ["PRO_CLUBS_DB_PATH"]=os.path.join(tempfile.mkdtemp(),"t.db")
import database as db
db.init_db()
def mk(g, status):
    with db.get_conn() as c:
        sid=c.execute("INSERT INTO sessions (guild_id,mode,status) VALUES (?,?,?)",(g,"league",status)).lastrowid
        t=c.execute("INSERT INTO teams (session_id,club_name) VALUES (?,?)",(sid,"A")).lastrowid
        c.execute("INSERT INTO team_members (team_id,player_id) VALUES (?,1)",(t,))
        m=c.execute("INSERT INTO matches (session_id,team_a_id,team_b_id,round_no) VALUES (?,?,?,1)",(sid,t,t)).lastrowid
        c.execute("INSERT INTO match_participants (guild_id,match_id,session_id,player_id,team_id,club_name,result) VALUES (?,?,?,1,?,'A','win')",(g,m,sid,t))
        c.execute("INSERT INTO club_match_results (guild_id,match_id,club_name,result) VALUES (?,?,'A','win')",(g,m))
    return sid
mk(1,"ended"); mk(1,"ended"); other=mk(2,"ended")
assert db.reset_sessions(1)==2
with db.get_conn() as c:
    for t in ("sessions","teams","team_members","matches","match_participants","club_match_results"):
        assert c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]==1, t
assert mk(1,"ended")==other+1
assert db.reset_sessions(1)==1 and db.reset_sessions(2)==1
assert mk(1,"ended")==1
print("RESET OK")

import sys, os, tempfile
repo=sys.argv[1]; sys.path.insert(0, repo)
os.environ["PRO_CLUBS_DB_PATH"]=os.path.join(tempfile.mkdtemp(),"t.db")
import config, database as db
db.init_db(); G=1
for pid in (1,2): db.ensure_player(G,pid,f"p{pid}")
S=config.STARTING_MMR
assert db.balance_rating(G,1,"league")==S
db.update_mode_mmr(G,1,"league",S+30,True)
st=db.get_player_mode_stats(G,1,"league"); assert st["mmr"]==S+30 and st["hidden_mmr"]==S+30 and st["wins"]==1, st
db.set_hidden_mmr(G,2,"league",1144)
db.update_mode_mmr(G,2,"league",S-20,False)
st=db.get_player_mode_stats(G,2,"league"); assert st["mmr"]==S-20 and st["hidden_mmr"]==1124 and st["losses"]==1, st
db.bump_mode_mmr(G,1,"league",25)                      # session bonus: visible only
assert db.balance_rating(G,1,"league")==S+30
db.reset_mode_leaderboard(G,"league")
a=db.get_player_mode_stats(G,1,"league"); b=db.get_player_mode_stats(G,2,"league")
assert a["mmr"]==S and a["wins"]==0 and a["hidden_mmr"]==S+30 and a["last_season_mmr"]==S+55, a
assert b["mmr"]==S and b["hidden_mmr"]==1124 and b["last_season_mmr"]==S-20, b
assert db.get_player_mode_stats(G,1,"rivals")["hidden_mmr"] is None       # other mode untouched
assert [r["discord_id"] for r in db.hidden_leaderboard(G,"league")]==[2,1]
print("HIDDEN OK")
# undo reverses the hidden rating too
with db.get_conn() as c:
    sid=c.execute("INSERT INTO sessions (guild_id,mode,status) VALUES (?,?,?)",(G,"league","active")).lastrowid
    ta=c.execute("INSERT INTO teams (session_id,club_name) VALUES (?,?)",(sid,"A")).lastrowid
    tb=c.execute("INSERT INTO teams (session_id,club_name) VALUES (?,?)",(sid,"B")).lastrowid
    mid=c.execute("INSERT INTO matches (session_id,team_a_id,team_b_id,round_no) VALUES (?,?,?,1)",(sid,ta,tb)).lastrowid
before=db.get_player_mode_stats(G,1,"league"); b2=db.get_player_mode_stats(G,2,"league")
db.update_mode_mmr(G,1,"league",before["mmr"]+26,True); db.update_mode_mmr(G,2,"league",b2["mmr"]-18,False)
db.report_match_result(mid,ta,2,1)
db.record_match_participants(G,mid,sid,ta,tb,[1],[2],"A","B",True,"league",mmr_deltas={1:26,2:-18})
assert db.balance_rating(G,1,"league")==before["hidden_mmr"]+26 and db.balance_rating(G,2,"league")==b2["hidden_mmr"]-18
assert db.undo_match_result(mid) is not None
a=db.get_player_mode_stats(G,1,"league"); b=db.get_player_mode_stats(G,2,"league")
assert a["mmr"]==before["mmr"] and a["hidden_mmr"]==before["hidden_mmr"] and a["wins"]==0, a
assert b["mmr"]==b2["mmr"] and b["hidden_mmr"]==b2["hidden_mmr"] and b["losses"]==0, b
# a session left "active" by a restart is marked ended by the season reset (and kept)
db.reset_sessions(G)
with db.get_conn() as c:
    assert c.execute("SELECT status FROM sessions WHERE id=?",(sid,)).fetchone()[0]=="ended"
print("UNDO+STALE OK")

import sys, os, tempfile
repo=sys.argv[1]; sys.path.insert(0, repo)
os.environ["PRO_CLUBS_DB_PATH"]=os.path.join(tempfile.mkdtemp(),"t.db")
import database as db
db.init_db(); G=1
with db.get_conn() as c:
    sid=c.execute("INSERT INTO sessions (guild_id,mode) VALUES (?,?)",(G,"league")).lastrowid
def game(mid, a, b, a_won):
    db.record_match_participants(G, mid, sid, 10, 20, a, b, "A", "B", a_won, "league")
assert db.get_player_rival(G,1) is None and db.get_player_best_teammate(G,1) is None
game(1,[1,2,3],[7,8],True)      # 1 wins with 2,3
game(2,[1,2],[7,9],False)       # 1 loses with 2, against 7,9
game(3,[1,3],[7,8],False)       # 1 loses with 3, against 7,8
game(4,[1,4],[9],True)          # 1 wins with 4, against 9
r=db.get_player_rival(G,1); assert r=={"opponent_id":7,"losses":2,"games":3}, r
t=db.get_player_best_teammate(G,1); assert t=={"teammate_id":4,"wins":1,"games":1}, t   # 1 win in 1 game beats 1 in 2
assert db.get_player_rival(G,4) is None
w=db.get_player_most_losses_with(G,1); assert w in ({"teammate_id":2,"losses":1,"games":2},{"teammate_id":3,"losses":1,"games":2}), w
assert db.get_player_most_losses_with(G,4) is None
game(5,[1,2],[9],False)
assert db.get_player_most_losses_with(G,1)=={"teammate_id":2,"losses":2,"games":3}
print("STATS OK")

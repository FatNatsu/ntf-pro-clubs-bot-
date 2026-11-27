"""Decider for 1st, shared 1st and who gets the session bonus (Founder decisions, 27 Nov 2026)."""
import sys, os, asyncio
sys.argv = sys.argv[:2]
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "run_sessions.py")).read().rsplit("asyncio.run(league())", 1)[0]
exec(src)

awards = []
_real_award = db.award_session_win
def _spy(session_id, team_id, **k):
    ok = _real_award(session_id, team_id, **k)
    if ok:
        awards.append((session_id, team_id, set(k.get("player_ids", ()))))
    return ok
db.award_session_win = _spy


def control(g, st, slot):
    capch = g.get_channel(slot["captains_channel_id"])
    return next(m for m in capch.messages if isinstance(m.view, S.MatchControlView) and m.view.match_id == slot["match_id"])


async def play(g, cog, st, x, decide):
    """Reports every game of the current round; decide(team_a, team_b) -> (winner, w_goals, l_goals)."""
    for slot in list(st["slots"]):
        if db.get_match(slot["match_id"])["status"] == "reported":
            continue
        mmsg = control(g, st, slot)
        a, b = slot["team_ids"]
        winner, wg, lg = decide(a, b)
        loser = b if winner == a else a
        await go_live(g, st, mmsg)
        await cog.finalize_match(fakes.Interaction(g, x["admin"], message=mmsg), slot["match_id"], winner, loser, wg, lg, mmsg.view, mmsg)
    await settle()


def finals(x):
    return [m for m in x["history"].messages if "Final Standings" in (m.content or "") or any("Final Standings" in (e.title or "") for e in m.embeds)]


async def main():
    # ---- Rivals 1-1, level goal difference -> a Decider, which counts and settles 1st
    g, cog, sid, st, x = await make("rivals", 12, 41)
    a, b = list(st["teams"])
    assert st["total_rounds"] == 2
    await play(g, cog, st, x, lambda p, q: (a, 2, 1))
    early = next(p for p in st["teams"][a]["on_field"] if p != st["teams"][a]["captain_id"])
    await cog.remove_player_from_roster(g, sid, a, early)        # played game 1 for A, then left
    late = 777; g.add_member(late); db.ensure_player(g.id, late, "late")
    st["teams"][a]["on_field"].add(late)                        # on A at the end, never played
    await play(g, cog, st, x, lambda p, q: (b, 3, 2))
    assert st.get("decider") and st["current_round"] == 3 and st["total_rounds"] == 3 and not st.get("finished")
    assert finals(x) == [], "no final standings before the Decider"
    pub = g.get_channel(st["slots"][0]["public_channel_id"])
    assert any("Decider" in (m.content or "") for m in pub.messages)
    assert "Decider" in st["slots"][0]["control_title"]
    await play(g, cog, st, x, lambda p, q: (a, 1, 0))
    assert st.get("finished") and len(finals(x)) == 1
    with db.get_conn() as conn:
        assert db.get_match(st["decider"]["match_id"])["status"] == "reported"
        assert conn.execute("SELECT COUNT(*) FROM match_participants WHERE match_id=?", (st["decider"]["match_id"],)).fetchone()[0] > 0  # counted for MMR
    (_, team, players), = [w for w in awards if w[0] == sid]
    assert team == a and early in players and late in players and not (players & st["teams"][b]["on_field"])
    await settle()

    # ---- Rivals 1-1 but goal difference differs -> no Decider
    g, cog, sid, st, x = await make("rivals", 12, 42)
    a, b = list(st["teams"])
    await play(g, cog, st, x, lambda p, q: (a, 4, 0))
    await play(g, cog, st, x, lambda p, q: (b, 1, 0))
    assert not st.get("decider") and st.get("finished") and len(finals(x)) == 1
    assert [w[1] for w in awards if w[0] == sid] == [a]
    await settle()

    # ---- Decider never played (admin ends the session) -> both share 1st, once
    g, cog, sid, st, x = await make("rivals", 12, 43)
    a, b = list(st["teams"])
    await play(g, cog, st, x, lambda p, q: (a, 2, 1))
    await play(g, cog, st, x, lambda p, q: (b, 2, 1))
    assert st.get("decider")
    await cog.end_session(sid, x["admin"])
    (_, team, players), = [w for w in awards if w[0] == sid]
    assert st["teams"][a]["on_field"] <= players and st["teams"][b]["on_field"] <= players
    assert any("shared 1st" in (m.content or "") for m in x["history"].messages)

    # ---- League: three teams level on everything -> no Decider, they share 1st
    g, cog, sid, st, x = await make("league", 16, 44)
    A, B, C, D = list(st["teams"])
    beats = {(A, B), (B, C), (C, A)}
    def decide(p, q):
        if D in (p, q):
            return (q if p == D else p), 1, 0
        return (p if (p, q) in beats else q), 1, 0
    while not st.get("finished"):
        await play(g, cog, st, x, decide)
    assert not st.get("decider") and len(finals(x)) == 1
    (_, team, players), = [w for w in awards if w[0] == sid]
    for t in (A, B, C):
        assert st["teams"][t]["on_field"] <= players
    assert not (players & st["teams"][D]["on_field"])
    assert any("shared 1st" in (m.content or "") for m in x["history"].messages)
    print("DECIDER OK")


asyncio.run(main())

"""Match reporting and roster integrity: NTF-009, 010, 011, 013, 014, 016, 020."""
import sys, os, asyncio
sys.argv = sys.argv[:2]
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "run_sessions.py")).read().rsplit("asyncio.run(league())", 1)[0]
exec(src)
import sqlite3


def control(g, st, slot):
    capch = g.get_channel(slot["captains_channel_id"])
    mmsg = next(m for m in capch.messages if isinstance(m.view, S.MatchControlView) and m.view.match_id == slot["match_id"])
    return mmsg, mmsg.view


def stats(g, uid, mode):
    return db.get_player_mode_stats(g.id, uid, mode)


async def main():
    # ---- NTF-011: score checks
    v = S.validate_scoreline
    assert v("3", "1", "A", "B") == (3, 1, None)
    assert v(" 0 ", "0", "A", "B", went_to_pens=True) == (0, 0, None)
    for w, l, why in [("x", "1", "whole numbers"), ("-1", "0", "between 0"), ("2", "3", "can't win 2-3"),
                      ("2", "2", "draw"), ("", "1", "whole numbers"), ("100", "1", "between 0")]:
        r = v(w, l, "A", "B"); assert r[0] is None and why in r[2], (w, l, r)
    assert "level" in v("2", "1", "A", "B", went_to_pens=True)[2]

    g, cog, sid, st, x = await make("league", 16, 21)
    slot1, slot2 = st["slots"]
    a, b = slot1["team_ids"]; c, d = slot2["team_ids"]
    m1, m2 = slot1["match_id"], slot2["match_id"]
    msg1, v1 = control(g, st, slot1); msg2, v2 = control(g, st, slot2)
    capA = cap_of(g, st, a)

    # a bad score from the popup records nothing
    modal = S.ScoreModal(cog, m1, a, b, "A", "B", v1, msg1)
    modal.winner_score.value, modal.loser_score.value = "1", "4"
    it = fakes.Interaction(g, capA, message=msg1); await modal.on_submit(it)
    assert "can't win 1-4" in it.last and db.get_match(m1)["status"] == "pending"

    # ---- NTF-014: a captain replaced after opening the popup can't report
    new_cap = next(p for p in st["teams"][a]["on_field"] if p != capA.id)
    await cog.reassign_captain(g, sid, a, new_cap)
    it = fakes.Interaction(g, capA, message=msg1)
    await cog.finalize_match(it, m1, a, b, 2, 0, v1, msg1)
    assert "not a captain" in it.last and db.get_match(m1)["status"] == "pending"
    # ---- NTF-020: and the stand-in captain did NOT join the permanent whitelist
    assert not db.get_player(g.id, new_cap)["is_captain"]

    # ---- Match Live is required (Founder decision 27 Nov): not live -> refused, paused -> refused
    it = fakes.Interaction(g, x["admin"], message=msg1); await v1.btn_a.callback(it)
    assert "Match Live" in it.last and it.modal is None
    it = fakes.Interaction(g, x["admin"], message=msg1); await v1.btn_pens.callback(it)
    assert "Match Live" in it.last
    it = fakes.Interaction(g, x["admin"], message=msg1); await cog.finalize_match(it, m1, a, b, 2, 0, v1, msg1)
    assert "Match Live" in it.last and db.get_match(m1)["status"] == "pending"
    await go_live(g, st, msg1)
    await v1.btn_pause.callback(fakes.Interaction(g, x["admin"], message=msg1))
    it = fakes.Interaction(g, x["admin"], message=msg1); await cog.finalize_match(it, m1, a, b, 2, 0, v1, msg1)
    assert "paused" in it.last and db.get_match(m1)["status"] == "pending"
    it = fakes.Interaction(g, x["admin"], message=msg1); await v1.btn_a.callback(it)
    assert "Resume Match" in it.last
    await go_live(g, st, msg1)   # Resume Match
    it = fakes.Interaction(g, x["admin"], message=msg1); await v1.btn_a.callback(it)
    assert isinstance(it.modal, S.ScoreModal)  # live again -> the score popup opens
    await go_live(g, st, msg2)

    # ---- NTF-009: the result is all-or-nothing. Break the participants insert halfway.
    before = {p: stats(g, p, "league")["mmr"] for p in st["teams"][a]["on_field"] | st["teams"][b]["on_field"]}
    real_connect = sqlite3.connect
    class Boom(sqlite3.Connection):
        def execute(self, sql, *args):
            if sql.startswith("INSERT INTO club_match_results"):
                raise sqlite3.OperationalError("disk I/O error (simulated)")
            return super().execute(sql, *args)
    sqlite3.connect = lambda *a_, **k: real_connect(*a_, factory=Boom, **k)
    try:
        await cog.finalize_match(fakes.Interaction(g, x["admin"], message=msg1), m1, a, b, 2, 0, v1, msg1)
        raise AssertionError("expected the simulated failure")
    except sqlite3.OperationalError:
        pass
    finally:
        sqlite3.connect = real_connect
    assert db.get_match(m1)["status"] == "pending"
    assert {p: stats(g, p, "league")["mmr"] for p in before} == before, "MMR changed although the result failed"
    with db.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM match_participants WHERE match_id=?", (m1,)).fetchone()[0] == 0

    # ---- NTF-009: a Discord failure AFTER saving doesn't record twice or stop the round
    async def broken_edit(**k): raise fakes.HTTPException()
    msg1.edit = broken_edit
    await cog.finalize_match(fakes.Interaction(g, x["admin"], message=msg1), m1, a, b, 2, 0, v1, msg1)
    assert db.get_match(m1)["status"] == "reported" and v1.btn_pens.disabled  # pens button off too
    once = {p: stats(g, p, "league")["mmr"] for p in before}
    it = fakes.Interaction(g, x["admin"], message=msg1)
    await cog.finalize_match(it, m1, a, b, 2, 0, v1, msg1)  # duplicate
    assert "already reported" in it.last and {p: stats(g, p, "league")["mmr"] for p in before} == once

    # ---- penalties are stored as penalties
    await cog.finalize_match(fakes.Interaction(g, x["admin"], message=msg2), m2, c, d, None, None, v2, msg2, went_to_pens=True)
    assert db.get_match(m2)["went_to_pens"] == 1 and "pens" in cog._results_text(sid)
    await settle()

    # ---- NTF-010: play through to the last round, then report both final games at the same time
    assert st["current_round"] == 2
    while st["current_round"] < st["total_rounds"]:
        r = st["current_round"]
        for slot in st["slots"]:
            mmsg, mv = control(g, st, slot)
            ta, tb = slot["team_ids"]
            await go_live(g, st, mmsg)
            await cog.finalize_match(fakes.Interaction(g, x["admin"], message=mmsg), slot["match_id"], ta, tb, 1, 0, mv, mmsg)
        await settle()
        assert st["current_round"] == r + 1
    finals = [(slot, *control(g, st, slot)) for slot in st["slots"]]
    for slot, mmsg, mv in finals:
        await go_live(g, st, mmsg)
    pre = {p: stats(g, p, "league")["mmr"] for t in st["teams"].values() for p in t["on_field"]}
    await asyncio.gather(*[
        cog.finalize_match(fakes.Interaction(g, x["admin"], message=mmsg), slot["match_id"], *slot["team_ids"], 3, 1, mv, mmsg)
        for slot, mmsg, mv in finals
    ])
    await settle()
    finals_posted = [m for m in x["history"].messages if "Final Standings" in (m.content or "") or any("Final Standings" in (e.title or "") for e in m.embeds)]
    assert len(finals_posted) == 1, len(finals_posted)
    with db.get_conn() as conn:
        winner = conn.execute("SELECT winning_team_id FROM sessions WHERE id=?", (sid,)).fetchone()[0]
    assert winner is not None
    assert db.award_session_win(sid, winner, guild_id=g.id, mode="league", player_ids=[1], bonus=15) is False  # never twice

    # ---- NTF-013: Undo is refused once the final standings are in
    slot, mmsg, mv = finals[0]
    if sid in cog.active_sessions:
        it = fakes.Interaction(g, x["admin"], message=mmsg); await mv.btn_undo.callback(it)
        assert "can't be undone" in it.last and db.get_match(slot["match_id"])["status"] == "reported"

    # ---- end_session twice at once (auto-close + an admin) runs once
    await settle()
    if sid in cog.active_sessions:
        await asyncio.gather(cog.end_session(sid, x["admin"]), cog.end_session(sid, x["admin"]))
    assert sid not in cog.active_sessions

    # ---- NTF-016: 28 League players -> admins pick the club first and can reach everyone
    g, cog, sid, st, x = await make("league", 24, 22)
    extra = 0
    for tid, info in st["teams"].items():
        uid = 700 + extra; extra += 1
        m = g.add_member(uid); db.ensure_player(g.id, uid, m.display_name)
        info["on_field"].add(uid)
    total = sum(len(t["on_field"]) for t in st["teams"].values()); assert total == 28
    picker = S._player_picker(cog, sid, S.RemovePlayerSelectView, "Remove who?")
    assert isinstance(picker, S.TeamFirstSelectView) and len(picker.children[0].options) == 4
    reachable = set()
    for opt in picker.children[0].options:
        view = S.RemovePlayerSelectView(cog, sid, only_team_id=int(opt.value))
        assert len(view.children[0].options) <= 25
        reachable |= {int(o.value.split(":")[1]) for o in view.children[0].options}
    assert reachable == set().union(*[t["on_field"] for t in st["teams"].values()])
    t_pick = S._player_picker(cog, sid, S.TransferPlayerSelectView, "Transfer who?")
    assert isinstance(t_pick, S.TeamFirstSelectView)
    # ---- a stale remove menu does nothing once the player has moved on
    tid = next(iter(st["teams"])); pid = next(p for p in st["teams"][tid]["on_field"] if p != st["teams"][tid]["captain_id"])
    view = S.RemovePlayerSelectView(cog, sid, only_team_id=tid)
    st["teams"][tid]["on_field"].discard(pid)
    it = fakes.Interaction(g, x["admin"], values=[f"{tid}:{pid}"]); await view._on_select(it)
    assert "not on that team any more" in it.last
    print("MATCH INTEGRITY OK")


asyncio.run(main())

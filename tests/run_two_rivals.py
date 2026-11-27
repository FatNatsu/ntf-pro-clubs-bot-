"""Two Rivals sessions at the same time in one server: separate channels, separate club names, independent."""
import sys, os, asyncio
sys.argv = sys.argv[:2]
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "run_sessions.py")).read().rsplit("asyncio.run(league())", 1)[0]
exec(src)


async def main():
    g = fakes.Guild(51)
    announce = g._mk("queue", "text"); adminlog = g._mk("admin-log", "text"); history = g._mk("session-history", "text")
    lobby = g._mk("lobby", "voice")
    db.upsert_guild_config(g.id, admin_log_channel_id=adminlog.id, history_channel_id=history.id)
    for name in ["Dhafar", "NTF", "Fram Esports", "The Fifth Pass"]:
        db.add_club(g.id, name)
    admin = g.add_member(900, admin=True)
    cog = S.SessionCog(fakes.Bot(g))

    def players(first):
        out = []
        for uid in range(first, first + 12):
            m = g.add_member(uid); m.join(lobby); db.ensure_player(g.id, uid, m.display_name)
            if uid < first + 2: db.set_captain(g.id, uid, True)
            p = db.get_player(g.id, uid); st = db.get_player_mode_stats(g.id, uid, "rivals")
            out.append({**p, "mmr": st["mmr"], "wins": st["wins"], "losses": st["losses"]})
        return out

    s1 = await cog.start_session(g, "rivals", players(100), announce.id)
    s2 = await cog.start_session(g, "rivals", players(200), announce.id)
    st1, st2 = cog.active_sessions[s1], cog.active_sessions[s2]
    clubs1 = {t["club_name"] for t in st1["teams"].values()}
    clubs2 = {t["club_name"] for t in st2["teams"].values()}
    assert not (clubs1 & clubs2), (clubs1, clubs2)                       # no two calls with the same name
    assert not (set(st1["category_ids"]) & set(st2["category_ids"]))     # separate channels
    vc1 = {t["voice_channel_id"] for t in st1["teams"].values()}
    vc2 = {t["voice_channel_id"] for t in st2["teams"].values()}
    assert len(vc1) == 2 and len(vc2) == 2 and not (vc1 & vc2)
    assert st1["bench_channel_id"] != st2["bench_channel_id"]
    # each player is in their own session's team call
    for st in (st1, st2):
        for t in st["teams"].values():
            for pid in t["on_field"]:
                assert g.get_member(pid).voice.channel.id == t["voice_channel_id"]

    # a result in game 1 doesn't touch game 2
    slot = st1["slots"][0]
    capch = g.get_channel(slot["captains_channel_id"])
    mmsg = next(m for m in capch.messages if isinstance(m.view, S.MatchControlView))
    a, b = slot["team_ids"]
    await go_live(g, st1, mmsg)
    await cog.finalize_match(fakes.Interaction(g, admin, message=mmsg), slot["match_id"], a, b, 2, 0, mmsg.view, mmsg)
    assert db.get_match(st2["slots"][0]["match_id"])["status"] == "pending"
    # ending game 1 leaves game 2's channels alone
    await cog.end_session(s1, admin)
    assert s2 in cog.active_sessions and all(g.get_channel(c) for c in st2["category_ids"])
    print("TWO RIVALS OK")


asyncio.run(main())

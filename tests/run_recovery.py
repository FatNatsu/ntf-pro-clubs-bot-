"""Restart recovery (NTF-008), voice access sync (NTF-019), admin alerts and /ntf_status (NTF-023, NTF-031)."""
import sys, os, asyncio
sys.argv = sys.argv[:2]
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "run_sessions.py")).read().rsplit("asyncio.run(league())", 1)[0]
exec(src)
import logging
import alerts, queue_cog as Q, admin_cog as A
alerts.install()


def ow(channel, member):
    o = channel.overwrites.get(member)
    return None if o is None else {k: v for k, v in o.k.items() if v is not None}


async def main():
    # ================= restart recovery
    g, cog, sid, st, x = await make("rivals", 12, 71)
    slot = st["slots"][0]
    a, b = slot["team_ids"]
    capch = g.get_channel(slot["captains_channel_id"])
    mmsg = next(m for m in capch.messages if isinstance(m.view, S.MatchControlView))
    first_match = slot["match_id"]
    await go_live(g, st, mmsg)
    await cog.finalize_match(fakes.Interaction(g, x["admin"], message=mmsg), first_match, a, b, 2, 0, mmsg.view, mmsg)
    await settle()
    fan = g.get_member(201)
    await slot["public_message"].view.children[0].callback(fakes.Interaction(g, fan))
    assert fan.muted and db.bot_mutes_for_session(sid) == [201]
    session_cats = list(st["category_ids"])
    mmr_before = db.get_player_mode_stats(g.id, a and next(iter(st["teams"][a]["on_field"])), "rivals")["mmr"]
    # someone's own category whose id happens to be saved but that isn't a session category is left alone
    lookalike = g._mk("General Chat", "category")
    with db.get_conn() as c:
        c.execute("UPDATE sessions SET category_ids = category_ids || ',' || ? WHERE id=?", (str(lookalike.id), sid))

    # --- the bot restarts: a fresh cog with nothing in memory
    bot2 = fakes.Bot(g); bot2.guilds = [g]
    cog2 = S.SessionCog(bot2)
    await cog2.on_ready()
    await cog2.on_ready()   # a reconnect later must not run it again
    for cid in session_cats:
        assert g.get_channel(cid) is None, "the cut-off session's channels should be gone"
    assert g.get_channel(lookalike.id) is not None and g.get_channel(x["lobby"].id) and g.get_channel(x["adminlog"].id)
    assert not fan.muted and db.bot_mutes_for_session(sid) == []
    assert db.get_session(sid)["status"] == "ended"
    note = [m.content for m in x["adminlog"].messages if m.content and "restarted" in m.content]
    assert len(note) == 1 and "1 result(s)" in note[0], note
    assert db.get_match(first_match)["status"] == "reported"                         # results kept
    assert db.get_player_mode_stats(g.id, next(iter(st["teams"][a]["on_field"])), "rivals")["mmr"] == mmr_before

    # a session that IS running in memory is never touched
    g, cog, sid, st, x = await make("rivals", 12, 72)
    cog.bot.guilds = [g]
    assert await cog.recover_unfinished_sessions(g) == [] and all(g.get_channel(c) for c in st["category_ids"])

    # ================= queue channel tidied after a restart
    qbot = fakes.Bot(g); qbot.guilds = [g]
    qch = g._mk("ntf-queue", "text"); db.upsert_guild_config(g.id, queue_channel_id=qch.id)
    stale = await qch.send("old ready check with dead buttons")
    human = await qch.send("anyone on?"); human.author = fakes._User(555)
    qcog = Q.QueueCog(qbot)
    await qcog.on_ready()
    ids = {m.id for m in qch.messages}
    assert stale.id not in ids and human.id in ids and qcog.panel_message[g.id].id in ids

    # ================= voice access follows the roster (NTF-019)
    g, cog, sid, st, x = await make("league", 16, 73)
    t1, t2, t3, t4 = list(st["teams"])
    vc = lambda t: g.get_channel(st["teams"][t]["voice_channel_id"])
    bench = g.get_channel(st["bench_channel_id"])
    p = g.get_member(next(q for q in st["teams"][t1]["on_field"] if q != st["teams"][t1]["captain_id"]))
    assert ow(vc(t1), p) == {"view_channel": True, "connect": True}
    added, moved, _ = await cog.transfer_player(g, sid, st, p.id, t2)
    assert added
    assert ow(vc(t1), p) is None, "old team's call should be closed to them"
    assert ow(vc(t2), p) == {"view_channel": True, "connect": True}
    assert ow(bench, p) == {"view_channel": True, "connect": False}
    await cog.remove_player_from_roster(g, sid, t2, p.id)
    assert all(ow(vc(t), p) is None for t in st["teams"]) and ow(bench, p) is None
    # captain change: the new one gets captain access everywhere, the old one is a normal player again
    old_cap = g.get_member(st["teams"][t3]["captain_id"])
    new_cap = g.get_member(next(q for q in st["teams"][t3]["on_field"] if q != old_cap.id))
    await cog.reassign_captain(g, sid, t3, new_cap.id)
    for t in st["teams"]:
        assert ow(vc(t), new_cap) == {"view_channel": True, "connect": True, "move_members": True}
        assert ow(vc(t), old_cap) == ({"view_channel": True, "connect": True} if t == t3 else None)
    assert ow(bench, old_cap) == {"view_channel": True, "connect": False}
    # nothing is sent when access is already right (saves Discord's rate limit)
    calls = []
    for t in st["teams"]:
        ch = vc(t); real = ch.set_permissions
        async def counting(*args, _real=real, **kw):
            calls.append(1); return await _real(*args, **kw)
        ch.set_permissions = counting
    await cog._sync_member_access(g, st, new_cap.id)
    assert calls == []
    # admins and ghosts are never touched
    db.set_ghost(g.id, 202, True)
    ghost = g.get_member(202)
    await vc(t4).set_permissions(ghost, view_channel=True, connect=True, move_members=True)
    await cog._sync_member_access(g, st, ghost.id)
    await cog._sync_member_access(g, st, x["admin"].id)
    assert ow(vc(t4), ghost) == {"view_channel": True, "connect": True, "move_members": True}

    # ================= /ntf_status and admin alerts
    logging.getLogger("ntf").error("simulated failure for the status page")
    acog = A.AdminCog(cog.bot); cog.bot.cogs = {"SessionCog": cog}
    it = fakes.Interaction(g, x["admin"])
    await acog.ntf_status(it)
    emb = it.embeds[-1]
    fields = dict(emb.fields)
    assert "Season" in emb.description
    assert any(k.startswith("Sessions running (1)") for k in fields)
    assert "simulated failure" in next(v for k, v in fields.items() if k.startswith("Errors since start"))
    assert await alerts.notify_admins(g, "test alert") and x["adminlog"].messages[-1].content == "test alert"
    print("RECOVERY OK")


asyncio.run(main())

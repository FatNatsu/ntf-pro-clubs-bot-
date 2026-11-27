import sys, os, asyncio
sys.argv = sys.argv[:2]
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "run_sessions.py")).read().rsplit("asyncio.run(league())", 1)[0]
exec(src)
async def pause():
    g, cog, sid, st, x = await make("league", 16, 7)
    slot = st["slots"][0]; a, b = slot["team_ids"]; mid = slot["match_id"]
    capch = g.get_channel(slot["captains_channel_id"])
    mmsg = next(m for m in capch.messages if isinstance(m.view, S.MatchControlView)); mv = mmsg.view
    capA = cap_of(g, st, a); regular = g.get_member(next(p for p in st["teams"][a]["on_field"] if p != capA.id))
    assert mv.btn_pause.disabled                                           # can't pause before kick-off
    it = fakes.Interaction(g, capA, message=mmsg); await mv.btn_pause.callback(it); assert "isn't live" in it.last
    await mv.btn_live.callback(fakes.Interaction(g, capA, message=mmsg))
    assert not mv.btn_pause.disabled and mv.btn_live.disabled and mid in st["match_rosters"]
    it = fakes.Interaction(g, regular, message=mmsg); await mv.btn_pause.callback(it); assert "Only a captain" in it.last
    # the other game's captain can't pause this one
    other_cap = cap_of(g, st, st["slots"][1]["team_ids"][0])
    it = fakes.Interaction(g, other_cap, message=mmsg); await mv.btn_pause.callback(it); assert "Only a captain" in it.last
    await mv.btn_pause.callback(fakes.Interaction(g, cap_of(g, st, b), message=mmsg))
    assert mid not in st["match_rosters"] and mv.btn_pause.disabled and not mv.btn_live.disabled and mv.btn_live.label == "Resume Match"
    assert "Paused" in slot["public_message"].embeds[-1].description
    assert any("Paused" in f[1] for f in x["adminlog"].messages[1].embeds[-1].fields)
    assert any("paused" in (m.content or "") for m in x["adminlog"].messages)
    # a sub during the pause counts once it's resumed
    await join_bench(g, cog, st, 201)
    turn = cog._sub_turn_team(st)
    await slot_of(st, turn)["panel"]["view"].button("Add sub").callback(fakes.Interaction(g, cap_of(g, st, turn)))
    await mv.btn_live.callback(fakes.Interaction(g, capA, message=mmsg))
    frozen = st["match_rosters"][mid]
    if turn in (a, b): assert 201 in (frozen["team_a"] | frozen["team_b"])
    assert "LIVE" in slot["public_message"].embeds[-1].description and not mv.btn_pause.disabled
    assert any("resumed" in (m.content or "") for m in x["adminlog"].messages)
    it = fakes.Interaction(g, x["admin"], message=mmsg); await cog.finalize_match(it, mid, a, b, 2, 0, mv, mmsg)
    assert mv.btn_pause.disabled and "Finished" in slot["public_message"].embeds[-1].description
    print("PAUSE OK")
asyncio.run(pause())

import sys, os, asyncio
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "run_sessions.py")).read().rsplit("asyncio.run(league())", 1)[0]
exec(src)
def changes(slot):
    f = [v for n, v in slot["public_message"].embeds[-1].fields if n == "Recent changes"]
    return f[0] if f else ""
async def events():
    g, cog, sid, st, x = await make("league", 16, 9)
    s1, s2 = st["slots"]; n_progress = 0
    assert changes(s1) == "" and changes(s2) == ""
    # a sub for one team shows only in that team's game
    await join_bench(g, cog, st, 201)
    turn = cog._sub_turn_team(st); mine, other = (s1, s2) if turn in s1["team_ids"] else (s2, s1)
    await mine["panel"]["view"].button("Add sub").callback(fakes.Interaction(g, cap_of(g, st, turn))); await settle()
    assert "<@201> joined" in changes(mine) and changes(other) == "", (changes(mine), changes(other))
    # a transfer between the two games shows in both
    src_team = mine["team_ids"][0]; cap = cap_of(g, st, src_team)
    player = next(p for p in st["teams"][src_team]["on_field"] if p != cap.id); dest = other["team_ids"][0]
    await cog.transfer_player(g, sid, st, player, dest); await settle()
    line = f"<@{player}> transferred from **{st['teams'][src_team]['club_name']}** to **{st['teams'][dest]['club_name']}**"
    assert line in changes(mine) and line in changes(other)
    # a removal and a captain change show only in that game
    victim = next(p for p in st["teams"][dest]["on_field"] if p != st["teams"][dest]["captain_id"] and p != player)
    await cog.remove_player_from_roster(g, sid, dest, victim); await settle()
    assert f"<@{victim}> removed" in changes(other) and f"<@{victim}> removed" not in changes(mine)
    await cog.reassign_captain(g, sid, dest, player); await settle()
    assert "now captaining" in changes(other) and "now captaining" not in changes(mine)
    # nothing was announced in #in-progress, and the channel still has one message
    assert len(x["progress"].messages) == n_progress
    assert len(g.get_channel(mine["public_channel_id"]).messages) == 1
    # only the latest six are kept on show
    for i in range(10): cog._note_team_event(st, {dest}, f"line {i}")
    await cog._refresh_status(sid)
    assert changes(other).count("\n") == 5 and "line 9" in changes(other) and "line 3" not in changes(other)
    print("EVENTS OK")
asyncio.run(events())

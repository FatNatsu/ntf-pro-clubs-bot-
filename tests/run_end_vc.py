"""/end_vc: when a session ends, everyone still in its calls is moved into one chosen voice channel (Founder, 28 Nov 2026)."""
import sys, os, asyncio
sys.argv = sys.argv[:2]
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "run_sessions.py")).read().rsplit("asyncio.run(league())", 1)[0]
exec(src)
import admin_cog as A


async def main():
    # ---- not set: calls just close, as before
    g, cog, sid, st, x = await make("rivals", 12, 81)
    pid = next(iter(next(iter(st["teams"].values()))["on_field"]))
    await cog.end_session(sid, x["admin"])
    assert g.get_member(pid).voice is None or g.get_member(pid).voice.channel is None or g.get_member(pid).voice.channel.deleted

    # ---- set with /end_vc: players, the Bench and spectators all land there; others are left alone
    g, cog, sid, st, x = await make("league", 16, 82)
    hangout = g._mk("Hangout", "voice")
    elsewhere = g._mk("Other game", "voice")
    acog = A.AdminCog(cog.bot)
    it = fakes.Interaction(g, x["admin"]); await acog.end_vc(it, hangout)
    assert "moved to" in it.last and db.get_guild_config(g.id)["end_vc_id"] == hangout.id
    bench = g.get_channel(st["bench_channel_id"])
    sub = g.add_member(301); db.ensure_player(g.id, 301, "sub"); sub.join(bench)
    fan = g.get_member(201)
    await st["slots"][0]["public_message"].view.children[0].callback(fakes.Interaction(g, fan))
    stranger = g.add_member(401); stranger.join(elsewhere)
    in_session = {m.id for t in st["teams"].values() for m in g.get_channel(t["voice_channel_id"]).members} | {301, 201}
    await cog.end_session(sid, x["admin"])
    assert {m.id for m in hangout.members} == in_session, ({m.id for m in hangout.members}, in_session)
    assert stranger.voice.channel is elsewhere
    assert not fan.muted

    # ---- an end VC inside the session's own categories is ignored (it's about to be deleted)
    g, cog, sid, st, x = await make("rivals", 12, 83)
    inside = g.get_channel(st["bench_channel_id"])
    db.upsert_guild_config(g.id, end_vc_id=inside.id)
    assert await cog._gather_in_end_vc(g, [g.get_channel(c) for c in st["category_ids"]]) == 0

    # ---- restart recovery moves people out before removing a cut-off session's calls
    g, cog, sid, st, x = await make("rivals", 12, 84)
    hangout = g._mk("Hangout", "voice"); db.upsert_guild_config(g.id, end_vc_id=hangout.id)
    expected = {m.id for t in st["teams"].values() for m in g.get_channel(t["voice_channel_id"]).members}
    bot2 = fakes.Bot(g); bot2.guilds = [g]
    await S.SessionCog(bot2).recover_unfinished_sessions(g)
    assert {m.id for m in hangout.members} == expected

    # ---- clearing it
    it = fakes.Interaction(g, x["admin"]); await A.AdminCog(cog.bot).end_vc(it, None)
    assert "nobody is moved" in it.last and db.get_guild_config(g.id)["end_vc_id"] is None
    print("END VC OK")


asyncio.run(main())

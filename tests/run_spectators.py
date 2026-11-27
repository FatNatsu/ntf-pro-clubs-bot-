"""Spectators: NTF-021 - the bot only undoes mutes it applied."""
import sys, os, asyncio
sys.argv = sys.argv[:2]
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "run_sessions.py")).read().rsplit("asyncio.run(league())", 1)[0]
exec(src)


async def main():
    g, cog, sid, st, x = await make("rivals", 12, 31)
    slot = st["slots"][0]
    watch = slot["public_message"].view
    assert isinstance(watch, S.SpectateView)
    fan, modded = g.get_member(201), g.get_member(202)
    modded.muted = True  # a moderator muted them before they watched
    for m in (fan, modded):
        await watch.children[0].callback(fakes.Interaction(g, m))
    assert fan.muted and modded.muted and set(st["spectators"]) == {201, 202}
    # leaving voice: the fan is unmuted, the moderated player stays muted
    for m in (fan, modded):
        before = vs(m.voice.channel); m.join(x["lobby"])
        await cog.on_voice_state_update(m, before, vs(x["lobby"]))
    assert not fan.muted and modded.muted and st["spectators"] == {}
    # session end: same rule
    for m in (fan, modded):
        await watch.children[1].callback(fakes.Interaction(g, m))
    assert fan.muted and modded.muted
    await cog.end_session(sid, x["admin"])
    assert not fan.muted and modded.muted
    print("SPECTATORS OK")


asyncio.run(main())

"""Queue stability: NTF-001 to NTF-005 and NTF-007."""
import sys, os, asyncio, tempfile
repo = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fakes; fakes.install()
os.environ["PRO_CLUBS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "qs.db")
sys.path.insert(0, repo); sys.path.insert(0, repo + "/cogs")
import config, database as db
import queue_cog as Q
db.init_db()
config.READY_COUNTDOWN_SECONDS = 0.2


class FakeSession:
    def __init__(self):
        self.started = []; self.fail = False; self.active_sessions = {}
    async def start_session(self, guild, mode, players, announce_id):
        await asyncio.sleep(0.02)  # building channels takes time in real life
        if self.fail:
            raise RuntimeError("boom")
        self.started.append((mode, len(players)))


def setup():
    g = fakes.Guild(7); ch = g._mk("ntf-queue", "text")
    bot = fakes.Bot(g); sess = FakeSession(); bot.cogs = {"SessionCog": sess}
    cog = Q.QueueCog(bot)
    admin = g.add_member(900, admin=True)
    users = [g.add_member(100 + i) for i in range(40)]
    return g, ch, bot, sess, cog, admin, users


async def fill(cog, g, users, mode, n):
    for u in users[:n]:
        await cog.handle_ready(fakes.Interaction(g, u), mode, "PC")


async def main():
    # ---- NTF-001: Rivals pops at 22 by itself
    g, ch, bot, sess, cog, admin, users = setup()
    await cog.ensure_panel_in_channel(g, ch)
    await fill(cog, g, users, "rivals", 22)
    await asyncio.sleep(0.35)
    assert sess.started == [("rivals", 22)], sess.started
    st = cog._mode_state(g.id, "rivals")
    assert st["ready"] == [] and st["starting"] is False and st["countdown_task"] is None

    # ---- NTF-002: someone leaves mid-countdown -> stops, never starts undersized
    g, ch, bot, sess, cog, admin, users = setup()
    await cog.ensure_panel_in_channel(g, ch)
    await fill(cog, g, users, "league", 24)
    st = cog._mode_state(g.id, "league")
    assert st["countdown_task"] is not None
    await cog.handle_leave(fakes.Interaction(g, users[0]))
    assert st["countdown_task"] is None and len(st["ready"]) == 23
    assert "countdown stopped" in st["message"].embeds[-1].description
    await asyncio.sleep(0.35)
    assert sess.started == [], "an undersized queue must never start by itself"
    # refills -> a fresh countdown starts and the session pops
    await cog.handle_ready(fakes.Interaction(g, users[30]), "league", "Console")
    assert st["countdown_task"] is not None
    await asyncio.sleep(0.35)
    assert sess.started == [("league", 24)], sess.started

    # ---- NTF-002: switching modes mid-countdown stops the countdown too
    g, ch, bot, sess, cog, admin, users = setup()
    await cog.ensure_panel_in_channel(g, ch)
    await fill(cog, g, users, "rivals", 22)
    st = cog._mode_state(g.id, "rivals")
    assert st["countdown_task"] is not None
    await cog.handle_ready(fakes.Interaction(g, users[5]), "league", "PC")
    assert st["countdown_task"] is None and len(st["ready"]) == 21
    await asyncio.sleep(0.35)
    assert sess.started == []

    # ---- NTF-002 backstop: queue shrinks without a leave event (player gets rostered) -> no start
    g, ch, bot, sess, cog, admin, users = setup()
    await cog.ensure_panel_in_channel(g, ch)
    await fill(cog, g, users, "rivals", 22)
    sess.active_sessions = {1: {"guild_id": g.id, "teams": {10: {"on_field": {users[3].id}}}}}
    await asyncio.sleep(0.35)
    st = cog._mode_state(g.id, "rivals")
    assert sess.started == [] and len(st["ready"]) == 21 and users[3].id not in st["ready"]

    # ---- NTF-003: countdown and Force Start at the same moment -> exactly one session
    g, ch, bot, sess, cog, admin, users = setup()
    await cog.ensure_panel_in_channel(g, ch)
    config.READY_COUNTDOWN_SECONDS = 0.01
    await fill(cog, g, users, "rivals", 22)
    await asyncio.sleep(0.012)
    await asyncio.gather(
        cog.handle_force_start(fakes.Interaction(g, admin), "rivals"),
        cog.handle_force_start(fakes.Interaction(g, admin), "rivals"),
    )
    await asyncio.sleep(0.2)
    assert len(sess.started) == 1, sess.started
    config.READY_COUNTDOWN_SECONDS = 0.2

    # ---- NTF-003: two admins press Force Start together -> exactly one session
    g, ch, bot, sess, cog, admin, users = setup()
    await cog.ensure_panel_in_channel(g, ch)
    await fill(cog, g, users, "league", 14)
    a2 = g.add_member(901, admin=True)
    i1, i2 = fakes.Interaction(g, admin), fakes.Interaction(g, a2)
    await asyncio.gather(cog.handle_force_start(i1, "league"), cog.handle_force_start(i2, "league"))
    await asyncio.sleep(0.1)
    assert sess.started == [("league", 14)], sess.started
    assert any("already starting" in (r[0] or "") for r in i1.replies + i2.replies)

    # ---- NTF-003: a failed start keeps everyone and pauses the queue (no failure loop)
    g, ch, bot, sess, cog, admin, users = setup()
    await cog.ensure_panel_in_channel(g, ch)
    sess.fail = True
    await fill(cog, g, users, "rivals", 22)
    await asyncio.sleep(0.4)
    st = cog._mode_state(g.id, "rivals")
    assert sess.started == [] and st["ready"] == [u.id for u in users[:22]], st["ready"]
    assert st["paused_by"] == fakes.BOT_ID and st["countdown_task"] is None and st["starting"] is False
    assert any("failed to start" in (m.content or "") for m in ch.messages)
    await asyncio.sleep(0.3)
    assert sess.started == [], "a failed start must not retry by itself"
    sess.fail = False
    await cog.handle_pause(fakes.Interaction(g, admin), "rivals")  # Resume = try again
    await asyncio.sleep(0.4)
    assert sess.started == [("rivals", 22)], sess.started

    # ---- NTF-004: pausing keeps the time left; resume carries on (at least 5s, at most 15s)
    g, ch, bot, sess, cog, admin, users = setup()
    await cog.ensure_panel_in_channel(g, ch)
    config.READY_COUNTDOWN_SECONDS = 15
    await fill(cog, g, users, "rivals", 22)
    st = cog._mode_state(g.id, "rivals")
    st["countdown_deadline"] -= 8  # pretend 8 of the 15 seconds have gone
    await cog.handle_pause(fakes.Interaction(g, admin), "rivals")
    assert st["countdown_task"] is None and 6 < st["paused_remaining"] < 7.5
    await cog.handle_pause(fakes.Interaction(g, admin), "rivals")
    import time as _t
    left = st["countdown_deadline"] - _t.monotonic()
    assert 6 < left <= 7.5, left
    st["countdown_task"].cancel(); st["countdown_task"] = None; st["countdown_deadline"] = None
    # leave while paused: on resume there's no countdown at all
    await cog.handle_pause(fakes.Interaction(g, admin), "rivals")
    await cog.handle_leave(fakes.Interaction(g, users[0]))
    await cog.handle_pause(fakes.Interaction(g, admin), "rivals")
    assert st["countdown_task"] is None and len(st["ready"]) == 21
    # other mode is untouched by all of this
    assert cog._mode_state(g.id, "league")["paused_by"] is None
    config.READY_COUNTDOWN_SECONDS = 0.2

    # ---- NTF-005: someone on a team in a running session can't queue; force start skips them
    g, ch, bot, sess, cog, admin, users = setup()
    await cog.ensure_panel_in_channel(g, ch)
    sess.active_sessions = {1: {"guild_id": g.id, "teams": {10: {"on_field": {users[0].id}}}}}
    it = fakes.Interaction(g, users[0]); await cog.handle_ready(it, "league", "PC")
    assert "still running" in it.last and users[0].id not in cog._mode_state(g.id, "league")["ready"]
    # a session in another server doesn't block them
    sess.active_sessions = {1: {"guild_id": 999, "teams": {10: {"on_field": {users[1].id}}}}}
    await cog.handle_ready(fakes.Interaction(g, users[1]), "league", "PC")
    assert users[1].id in cog._mode_state(g.id, "league")["ready"]
    await fill(cog, g, users[2:], "league", 11)
    sess.active_sessions = {1: {"guild_id": g.id, "teams": {10: {"on_field": {users[2].id}}}}}
    it = fakes.Interaction(g, admin); await cog.handle_force_start(it, "league")
    assert "at least 12" in it.last and sess.started == []  # 12 ready but only 11 free

    # ---- NTF-007: clean-up keeps the panel, live ready checks, a running session's posts and people's messages
    g, ch, bot, sess, cog, admin, users = setup()
    panel = await cog.ensure_panel_in_channel(g, ch)
    await cog.ensure_panel_in_channel(g, ch)  # /queue_panel twice -> still one panel
    panel = cog.panel_message[g.id]
    assert sum(1 for m in ch.messages if m.view is not None and type(m.view).__name__ == "QueuePanelView") == 1
    old = await ch.send("🟢 old session started")              # finished session: should go
    keep = await ch.send("🟢 other session started")           # still running: must stay
    sess.active_sessions = {2: {"guild_id": g.id, "teams": {}, "announce_message_ids": {keep.id}}}
    human = await ch.send("gg everyone"); human.author = fakes._User(555)
    pinned = await ch.send("rules"); pinned.pinned = True
    await fill(cog, g, users, "league", 5)
    await cog.handle_pause(fakes.Interaction(g, admin), "league")
    ready_msg = cog._mode_state(g.id, "league")["message"]
    await cog.reset_channel(g)
    left_ids = {m.id for m in ch.messages}
    assert old.id not in left_ids
    for m in (panel, keep, human, pinned, ready_msg):
        assert m.id in left_ids, m.content
    assert cog.panel_message[g.id] is panel  # not reposted when it's still there
    print("QUEUE STABILITY OK")


asyncio.run(main())

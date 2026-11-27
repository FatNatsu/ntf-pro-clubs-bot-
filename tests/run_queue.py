import sys, os, asyncio, tempfile
repo = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fakes; fakes.install()
os.environ["PRO_CLUBS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "q.db")
sys.path.insert(0, repo); sys.path.insert(0, repo + "/cogs")
import config, database as db, ui
import queue_cog as Q
db.init_db(); config.READY_COUNTDOWN_SECONDS = 0.05

async def main():
    g = fakes.Guild(5); ch = g._mk("ntf-queue", "text")
    bot = fakes.Bot(g); started = []
    class FakeSession:
        async def start_session(self, guild, mode, players, announce_id): started.append((mode, len(players)))
    bot.cogs = {"SessionCog": FakeSession()}
    cog = Q.QueueCog(bot)
    await cog.ensure_panel_in_channel(g, ch)
    panel = ch.messages[0]
    # ---- look: banner embed on top, real panel under it, picture attached
    assert len(panel.embeds) == 2 and panel.embeds[0].image == "attachment://queue.jpg" and panel.file.filename == "queue.jpg"
    pe = panel.embeds[-1]; names = [n for n, v in pe.fields]; vals = dict(pe.fields)
    assert names == ["⚔️ Rivals", "🏆 League"], names
    assert "0/22 ready" in vals["⚔️ Rivals"] and "11 per team" in vals["⚔️ Rivals"] and "6+ ready" in vals["⚔️ Rivals"]
    assert "0/24 ready" in vals["🏆 League"] and "6 + 1 on request" in vals["🏆 League"] and "12+ ready" in vals["🏆 League"]
    assert "▱" * 12 in vals["🏆 League"] and "⏳ Waiting" in vals["🏆 League"]
    assert ui.bar(18, 22) == "▰" * 10 + "▱" * 2 and ui.bar(24, 24) == "▰" * 12 and ui.bar(0, 24) == "▱" * 12
    admin = g.add_member(900, admin=True)
    users = [g.add_member(100 + i) for i in range(30)]
    ready = lambda u, mode="league": cog.handle_ready(fakes.Interaction(g, u), mode, "PC")
    for u in users[:20]: await ready(u)
    rmsg = cog._mode_state(g.id, "league")["message"]; rv = cog._mode_state(g.id, "league")["view"]
    assert rmsg.embeds[0].image == "attachment://ready_check_league.jpg" and rmsg.embeds[-1].colour == ui.accent("league")
    assert "20/24 ready" in rmsg.embeds[-1].description and "▰" * 10 in rmsg.embeds[-1].description
    assert "20/24 ready" in dict(panel.embeds[-1].fields)["🏆 League"] and panel.embeds[0].image == "attachment://queue.jpg"
    labels = [b.label for b in rv.children]
    assert labels == ["Ready Up (Console)", "Ready Up (PC)", "Not Ready / Leave", "Force Start (Admin, 12+)", "Pause Queue"], labels
    # ---- pause: admin only
    it = fakes.Interaction(g, users[0]); await rv.pause_btn.callback(it); assert "Only an admin" in it.last
    await rv.pause_btn.callback(fakes.Interaction(g, admin))
    st = cog._mode_state(g.id, "league")
    assert st["paused_by"] == 900 and rv.pause_btn.label == "Resume Queue" and rv.console_btn.disabled and rv.pc_btn.disabled and rv.force_btn.disabled
    assert "Queue paused by <@900>" in rmsg.embeds[-1].description and "⏸️ Paused" in dict(panel.embeds[-1].fields)["🏆 League"]
    it = fakes.Interaction(g, users[25]); await cog.handle_ready(it, "league", "PC")
    assert "paused" in it.last and len(st["ready"]) == 20                        # nobody new while paused
    it = fakes.Interaction(g, admin); await cog.handle_force_start(it, "league"); assert "resume it" in it.last
    it = fakes.Interaction(g, users[3]); await cog.handle_leave(it); assert len(st["ready"]) == 19   # leaving is still allowed
    assert len(cog._mode_state(g.id, "rivals")["ready"]) == 0
    await ready(users[26], "rivals"); assert len(cog._mode_state(g.id, "rivals")["ready"]) == 1      # Rivals isn't paused
    # ---- resume: back as it was, new players can join again
    await rv.pause_btn.callback(fakes.Interaction(g, admin))
    assert st["paused_by"] is None and rv.pause_btn.label == "Pause Queue" and not rv.console_btn.disabled
    assert "paused" not in rmsg.embeds[-1].description and len(st["ready"]) == 19
    # ---- pausing during the start countdown stops it; resuming a full queue restarts it
    for u in users[3:9] + users[20:25]:
        if len(st["ready"]) < 24: await ready(u)
    assert len(st["ready"]) == 24 and st["countdown_task"] is not None and "🚀 Starting" in dict(panel.embeds[-1].fields)["🏆 League"]
    await rv.pause_btn.callback(fakes.Interaction(g, admin))
    assert st["countdown_task"] is None
    await asyncio.sleep(0.15); assert started == [], "a paused queue must not start"
    await rv.pause_btn.callback(fakes.Interaction(g, admin))
    await asyncio.sleep(0.2)
    assert started == [("league", 24)], started
    print("QUEUE OK")
asyncio.run(main())

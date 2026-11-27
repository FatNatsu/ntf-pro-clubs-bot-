"""Season reset safety and backups: NTF-028 and NTF-029."""
import sys, os, asyncio, tempfile, types
repo = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fakes
fakes.install()
tmp = tempfile.mkdtemp()
os.environ["PRO_CLUBS_DB_PATH"] = os.path.join(tmp, "t.db")
os.environ["NTF_BACKUP_DIR"] = os.path.join(tmp, "backups")
os.environ["NTF_BACKUP_KEEP"] = "3"
sys.path.insert(0, repo); sys.path.insert(0, repo + "/cogs")
import config, database as db, season, backup
import season_cog
db.init_db()


async def main():
    g = fakes.Guild(88)
    lb = g._mk("leaderboard", "text"); arch = g._mk("season-archive", "text"); hist = g._mk("session-history", "text")
    db.upsert_guild_config(g.id, leaderboard_channel_id=lb.id, season_archive_channel_id=arch.id, history_channel_id=hist.id)
    bot = fakes.Bot(g); bot.guilds = [g]
    season.ensure_started(g.id)
    db.ensure_player(g.id, 5, "p5"); db.get_player_mode_stats(g.id, 5, "league")
    with db.get_conn() as c:
        c.execute("UPDATE player_mode_stats SET wins=7, mmr=640 WHERE discord_id=5 AND mode='league'")

    # ---- the automatic reset and an admin's reset at the same moment: one reset, one archive post
    results = await asyncio.gather(season.run_reset(bot, g, expected_number=1), season.run_reset(bot, g))
    assert sum(r is not None for r in results) == 1, results
    assert season.info(g.id)["number"] == 2
    assert sum("Season 1 Ended" in (m.embeds[-1].title or "") for m in arch.messages if m.embeds) == 1
    assert season.crown_holder(g.id, "league") == 5  # not wiped by a second reset
    # ---- a stale automatic reset for an already-ended season does nothing
    assert await season.run_reset(bot, g, expected_number=1) is None and season.info(g.id)["number"] == 2

    # ---- a backup was taken first, holds the old standings, and passes the integrity check
    files = sorted(os.listdir(os.environ["NTF_BACKUP_DIR"]))
    pre = [f for f in files if f.startswith("ntf-pre-season-reset-")]
    assert pre, files
    path = os.path.join(os.environ["NTF_BACKUP_DIR"], pre[0])
    assert backup.verify(path)
    import sqlite3
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT wins FROM player_mode_stats WHERE discord_id=5 AND mode='league'").fetchone()[0] == 7
    conn.close()
    # retention keeps the newest NTF_BACKUP_KEEP per label
    for _ in range(5):
        backup.make_backup("daily"); await asyncio.sleep(1.05)
    assert len([f for f in os.listdir(os.environ["NTF_BACKUP_DIR"]) if f.startswith("ntf-daily-")]) == 3

    # ---- the archive post failing doesn't stop the reset (the backup has the standings)
    async def broken_send(*a, **k): raise fakes.HTTPException()
    arch.send = broken_send
    r = await season.run_reset(bot, g)
    assert r is not None and season.info(g.id)["number"] == 3

    # ---- a session about to start blocks the reset
    bot.cogs = {"QueueCog": types.SimpleNamespace(state={g.id: {"rivals": {"starting": True}}})}
    assert await season.run_reset(bot, g) is None and season.info(g.id)["number"] == 3
    bot.cogs = {}

    # ---- the 24-hour warning is only marked as sent once it really posted
    import datetime
    start = datetime.datetime.utcnow() - datetime.timedelta(days=config.SEASON_LENGTH_DAYS) + datetime.timedelta(hours=2)
    db.upsert_guild_config(g.id, season_started_at=start.isoformat(" "), season_warned=0)
    cog = season_cog.SeasonCog.__new__(season_cog.SeasonCog); cog.bot = bot
    await cog._check(g)
    assert not db.get_guild_config(g.id).get("season_warned"), "a failed warning must be retried"
    del arch.send  # back to the working send
    await cog._check(g)
    assert db.get_guild_config(g.id).get("season_warned") == 1
    assert any("ends in less than" in (m.content or "") for m in arch.messages)
    print("SEASON SAFETY OK")


asyncio.run(main())

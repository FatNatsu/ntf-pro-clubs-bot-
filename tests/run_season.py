import sys, os, asyncio, tempfile, types, datetime
repo = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fakes
fakes.install()
os.environ["PRO_CLUBS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, repo)
import config, database as db, leaderboard_utils, season
db.init_db()

async def main():
    g = fakes.Guild(77)
    lb = g._mk("leaderboard", "text"); arch = g._mk("season-archive", "text"); hist = g._mk("session-history", "text")
    db.upsert_guild_config(g.id, leaderboard_channel_id=lb.id, season_archive_channel_id=arch.id, history_channel_id=hist.id)
    bot = fakes.Bot(g); bot.user = types.SimpleNamespace(id=1)
    assert season.info(g.id) is None and season.countdown_line(g.id) is None
    season.ensure_started(g.id)
    i = season.info(g.id)
    assert i["number"] == 1 and 29.9 * 86400 < i["seconds_left"] <= 30 * 86400, i
    assert "Season 1" in season.countdown_line(g.id) and ":R>" in season.countdown_line(g.id)
    # players: 5 has most league wins; 6 and 7 tie on rivals wins, 7 has fewer losses; -1 is a fake bot
    for pid in (5, 6, 7, -1): db.ensure_player(g.id, pid, f"p{pid}")
    def setstats(pid, mode, wins, losses, mmr):
        db.get_player_mode_stats(g.id, pid, mode)
        with db.get_conn() as c:
            c.execute("UPDATE player_mode_stats SET wins=?, losses=?, mmr=? WHERE guild_id=? AND discord_id=? AND mode=?", (wins, losses, mmr, g.id, pid, mode))
    setstats(5, "league", 9, 2, 600); setstats(6, "league", 4, 1, 700); setstats(-1, "league", 50, 0, 900)
    setstats(6, "rivals", 3, 4, 520); setstats(7, "rivals", 3, 1, 510)
    await leaderboard_utils.refresh_leaderboard_channel(bot, g)
    assert "Season 1" in lb.messages[0].embeds[-1].description and "👑" not in lb.messages[0].embeds[-1].description
    r = await season.run_reset(bot, g)
    assert r["ended"] == 1 and r["crowns"]["league"]["discord_id"] == 5 and r["crowns"]["rivals"]["discord_id"] == 7, r
    ae = arch.messages[-1].embeds[-1]
    assert "Season 1 Ended" in ae.title and any("Golden Crown" in f[0] and "<@5>" in f[1] for f in ae.fields), ae.fields
    assert season.info(g.id)["number"] == 2 and season.crown_holder(g.id, "league") == 5 and season.crown_holder(g.id, "rivals") == 7
    assert db.get_player_mode_stats(g.id, 5, "league")["wins"] == 0
    texts = [m.embeds[-1].description for m in lb.messages]
    assert all("Season 2" in t for t in texts) and sum("👑" in t for t in texts) == 2, texts
    # a season nobody played: crowns are cleared
    r = await season.run_reset(bot, g)
    assert r["ended"] == 2 and r["crowns"] == {"rivals": None, "league": None} and season.crown_holder(g.id, "league") is None
    # a live session blocks the reset
    bot.cogs = {"SessionCog": types.SimpleNamespace(active_sessions={1: {"guild_id": g.id}})}
    assert await season.run_reset(bot, g) is None and season.info(g.id)["number"] == 3
    # an expired clock reads as zero or less
    db.upsert_guild_config(g.id, season_started_at=(datetime.datetime.utcnow() - datetime.timedelta(days=31)).isoformat(" "))
    assert season.info(g.id)["seconds_left"] < 0
    print("SEASON OK")
asyncio.run(main())

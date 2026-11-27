"""Season history on profiles: the Season menu shows each ended season's own record (Founder decision, 27 Nov 2026)."""
import sys, os, asyncio, tempfile
repo = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fakes
fakes.install()
tmp = tempfile.mkdtemp()
os.environ["PRO_CLUBS_DB_PATH"] = os.path.join(tmp, "t.db")
os.environ["NTF_BACKUP_DIR"] = os.path.join(tmp, "b")
sys.path.insert(0, repo); sys.path.insert(0, repo + "/cogs")
import config, database as db, season, leaderboard_utils
async def _noop(*a, **k): return None
leaderboard_utils.refresh_leaderboard_channel = _noop
import stats_cog as SC
db.init_db()


def game(g, winners, losers, mode="league"):
    sid = db.create_session(g.id, mode)
    with db.get_conn() as c:
        ta = c.execute("INSERT INTO teams (session_id,club_name) VALUES (?,?)", (sid, "Dhafar")).lastrowid
        tb = c.execute("INSERT INTO teams (session_id,club_name) VALUES (?,?)", (sid, "NTF")).lastrowid
        mid = c.execute("INSERT INTO matches (session_id,team_a_id,team_b_id,round_no) VALUES (?,?,?,1)", (sid, ta, tb)).lastrowid
    new = {}
    for pid in winners + losers:
        st = db.get_player_mode_stats(g.id, pid, mode)
        new[pid] = st["mmr"] + (28 if pid in winners else -18)
    assert db.record_match_result(mid, ta, 2, 1, guild_id=g.id, session_id=sid, mode=mode, team_a_id=ta, team_b_id=tb,
                                  team_a_player_ids=winners, team_b_player_ids=losers, club_a="Dhafar", club_b="NTF",
                                  a_won=True, new_mmr=new, mmr_deltas={p: new[p] - 500 for p in new})
    with db.get_conn() as c:
        c.execute("UPDATE sessions SET status='ended' WHERE id=?", (sid,))


async def main():
    g = fakes.Guild(61)
    arch = g._mk("season-archive", "text")
    db.upsert_guild_config(g.id, season_archive_channel_id=arch.id)
    bot = fakes.Bot(g)
    me, mate = g.add_member(1), g.add_member(2)
    for m in (me, mate):
        db.ensure_player(g.id, m.id, m.display_name)
    season.ensure_started(g.id)

    # Season 1: I go 3W-1L in League
    game(g, [1, 2], [3]); game(g, [1, 2], [3]); game(g, [1], [3])   # mate 2W, me 3W -> my crown
    game(g, [3], [1])
    s1 = db.get_player_mode_stats(g.id, 1, "league")
    assert (s1["wins"], s1["losses"]) == (3, 1)
    assert await season.run_reset(bot, g) is not None and db.current_season(g.id) == 2

    # Season 2: I go 0W-1L
    game(g, [3], [1])

    now = SC.build_profile_embed(g.id, me)
    assert "Season 2" in now.description
    league = dict(now.fields)["🏆 League"]
    assert "0W - 1L" in league and "most wins last season" in league   # last season's crown still shows
    assert "This season" in dict(now.fields)

    old = SC.build_profile_embed(g.id, me, 1)
    assert "Season 1" in old.description and "ended" in old.description
    league = dict(old.fields)["🏆 League"]
    assert "3W - 1L (final)" in league and "👑 Golden Crown — most wins in Season 1" in league, league
    assert "Didn't play" in dict(old.fields)["⚔️ Rivals"]
    assert "<@2>" in dict(old.fields)["Season 1"]          # season 1 highlights come from season 1's games

    # the Season menu: now + season 1, and picking swaps the profile
    view = SC.ProfileSeasonView(g.id, me, db.past_seasons(g.id, me.id))
    labels = [o.label for o in view.children[0].options]
    assert labels == ["Season 2 (now)", "Season 1"], labels
    msg = await g._mk("stats", "text").send(embed=now, view=view)
    it = fakes.Interaction(g, mate, message=msg, values=["1"])
    await view._on_select(it)
    assert "Season 1" in msg.embeds[0].description and "3W - 1L" in dict(msg.embeds[0].fields)["🏆 League"]

    # someone with no ended seasons gets no menu
    assert db.past_seasons(g.id, 999) == []
    # the history is all still there
    with db.get_conn() as c:
        assert c.execute("SELECT COUNT(*) FROM sessions WHERE guild_id=?", (g.id,)).fetchone()[0] == 5
        assert c.execute("SELECT COUNT(DISTINCT season_number) FROM match_participants").fetchone()[0] == 2
    print("SEASON PROFILE OK")


asyncio.run(main())

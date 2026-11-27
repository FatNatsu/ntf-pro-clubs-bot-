import sys, os, asyncio, tempfile, types
repo = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fakes
fakes.install()
os.environ["PRO_CLUBS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, repo); sys.path.insert(0, repo + "/cogs")
import config, database as db, leaderboard_utils
async def _noop(*a, **k): return None
leaderboard_utils.refresh_leaderboard_channel = _noop
import session_cog as S
config.SUB_QUEUE_REFRESH_DELAY = 0
config.SESSION_CLOSE_DELAY_SECONDS = 0
db.init_db()

def vs(ch): return types.SimpleNamespace(channel=ch)
async def settle(): 
    for _ in range(5): await asyncio.sleep(0.01)

async def make(mode, n_players, gid):
    g = fakes.Guild(gid)
    progress = g._mk("in-progress", "text"); adminlog = g._mk("admin-log", "text"); announce = g._mk("queue", "text")
    history = g._mk("session-history", "text"); db.upsert_guild_config(g.id, history_channel_id=history.id)
    lobby = g._mk("lobby", "voice")
    db.upsert_guild_config(g.id, progress_channel_id=progress.id, admin_log_channel_id=adminlog.id)
    for name in ["Dhafar", "NTF", "Fram Esports", "The Fifth Pass"]: db.add_club(g.id, name)
    players = []
    for i in range(n_players):
        uid = 101 + i
        m = g.add_member(uid); m.join(lobby)
        db.ensure_player(g.id, uid, m.display_name)
        if i < config.MODE_TEAMS[mode]: db.set_captain(g.id, uid, True)
        p = db.get_player(g.id, uid); st = db.get_player_mode_stats(g.id, uid, mode)
        players.append({**p, "mmr": st["mmr"], "wins": st["wins"], "losses": st["losses"]})
    for uid in range(201, 206):
        m = g.add_member(uid); m.join(lobby); db.ensure_player(g.id, uid, m.display_name)
    admin = g.add_member(900, admin=True)
    cog = S.SessionCog(fakes.Bot(g))
    sid = await cog.start_session(g, mode, players, announce.id)
    return g, cog, sid, cog.active_sessions[sid], dict(history=history, progress=progress, adminlog=adminlog, announce=announce, lobby=lobby, admin=admin)

def slot_of(st, team_id): return next(s for s in st["slots"] if team_id in s["team_ids"])
def cap_of(g, st, team_id): return g.get_member(st["teams"][team_id]["captain_id"])
def cats(g): return [c for c in g.channels.values() if c.kind == "category"]

async def join_bench(g, cog, st, uid):
    m = g.get_member(uid); before = vs(m.voice.channel if m.voice else None)
    bench = g.get_channel(st["bench_channel_id"]); m.join(bench)
    await cog.on_voice_state_update(m, before, vs(bench)); await settle()

async def league():
    g, cog, sid, st, x = await make("league", 16, 1)           # force start: 4 a side
    teams = list(st["teams"])
    # ---------- layout
    c = cats(g); assert len(c) == 3, [k.name for k in c]
    assert "BENCH" in c[0].name and "MATCH 1" in c[1].name and "MATCH 2" in c[2].name, [k.name for k in c]
    bench_cat = g.get_channel(st["category_id"])
    assert sorted(k.kind for k in bench_cat.channels) == ["text", "voice"], [(k.name, k.kind) for k in bench_cat.channels]
    regular = g.get_member(next(p for p in st["teams"][teams[0]]["on_field"] if p != st["teams"][teams[0]]["captain_id"]))
    for slot in st["slots"]:
        cat = g.get_channel(slot["category_id"]); kids = sorted(cat.channels, key=lambda k: k.id)
        assert [k.kind for k in kids if k.kind == "text"] == ["text", "text"] and len([k for k in kids if k.kind == "voice"]) == 2, [(k.name, k.kind) for k in kids]
        pub, capch = g.get_channel(slot["public_channel_id"]), g.get_channel(slot["captains_channel_id"])
        assert pub.id < capch.id and "captains" in capch.name
        assert {k.id for k in kids if k.kind == "voice"} == {st["teams"][t]["voice_channel_id"] for t in slot["team_ids"]}
        assert pub.can_see(regular) and not capch.can_see(regular)
        for t in teams:
            assert capch.can_see(cap_of(g, st, t)) == (t in slot["team_ids"]), "captain access wrong"
        assert len(pub.messages) == 1 and len(pub.messages[0].view.children) == 2 and len(pub.messages[0].embeds[-1].fields) == 2
        assert "Waiting" in pub.messages[0].embeds[-1].description
        labels = [b.label for b in slot["panel"]["view"].children]
        assert not any("End" in l or "Reassign" in l for l in labels), labels
        assert any("Request 7th" in l for l in labels) and any("Transfer" in l for l in labels) and any("Remove" in l for l in labels)
    await settle()
    assert all(str(sid) in k.name for k in c) and " vs " in c[1].name, [k.name for k in c]
    al = x["adminlog"].messages
    assert "Session #" in al[0].content and al[1].pinned and len(al[1].embeds[-1].fields) == 2 and isinstance(al[2].view, S.AdminPanelView), [m.content for m in al]
    assert [b.label for b in al[2].view.children][-1] == "End Session" and len(al[2].view.children) == 5
    assert x["progress"].messages == [], "nothing should be posted to the old in-progress channel"
    sq = g.get_channel(st["sub_queue_channel_id"]); assert len(sq.messages) == 1 and sq.can_see(regular)

    # ---------- subs: turn order is session-wide, button acts on the presser's own team
    await join_bench(g, cog, st, 201); await join_bench(g, cog, st, 202)
    turn = cog._sub_turn_team(st); other = next(t for t in teams if t != turn)
    panel = lambda t: slot_of(st, t)["panel"]["view"]
    assert st["teams"][turn]["club_name"] in panel(turn).button("Add sub").label
    it = fakes.Interaction(g, regular); await panel(turn).button("Add sub").callback(it)
    assert "Only a team's captain" in it.last, it.last
    it = fakes.Interaction(g, cap_of(g, st, other)); await panel(other).button("Add sub").callback(it)
    assert "Not your turn" in it.last, it.last
    it = fakes.Interaction(g, x["admin"]); await panel(turn).button("Add sub").callback(it)
    assert "admin controls" in it.last, it.last
    it = fakes.Interaction(g, cap_of(g, st, turn)); await panel(turn).button("Add sub").callback(it)
    await settle()
    assert len(st["teams"][turn]["on_field"]) == 5, it.replies
    assert "(5)" in slot_of(st, turn)["public_message"].embeds[-1].fields[list(slot_of(st, turn)["team_ids"]).index(turn)][0]
    assert any("requested a sub" in (m.content or "") for m in x["adminlog"].messages)
    assert cog._sub_turn_team(st) != turn

    # ---------- captain transfers: own players only, to any team incl. the other game
    capA = cap_of(g, st, turn); mine = next(p for p in st["teams"][turn]["on_field"] if p != capA.id)
    dest = next(t for t in teams if t not in slot_of(st, turn)["team_ids"])            # a team in the OTHER game
    it = fakes.Interaction(g, capA); await panel(turn).button("Transfer").callback(it)
    pick = it.last_view; opts = pick.children[0].options
    assert {int(o.value) for o in opts} == st["teams"][turn]["on_field"], "captain should only see own players"
    it2 = fakes.Interaction(g, capA, values=[str(mine)]); await pick._on_select(it2)
    destview = it2.last_view; assert str(turn) not in [o.value for o in destview.children[0].options]
    it3 = fakes.Interaction(g, capA, values=[str(dest)]); await destview._on_select(it3); await settle()
    assert mine in st["teams"][dest]["on_field"] and mine not in st["teams"][turn]["on_field"], it3.replies
    assert any("transferred" in (m.content or "") for m in x["adminlog"].messages)
    theirs = next(iter(st["teams"][other]["on_field"] - {st["teams"][other]["captain_id"]}))
    it = fakes.Interaction(g, capA, values=[str(turn)])
    await S.TransferDestinationView(cog, sid, theirs, "x")._on_select(it)
    assert "only transfer players from the team you captain" in it.last, it.last
    # ---------- captain removes: own players only
    it = fakes.Interaction(g, capA); await panel(turn).button("Remove").callback(it)
    rv = it.last_view; assert all(o.value.startswith(f"{turn}:") for o in rv.children[0].options)
    it = fakes.Interaction(g, capA, values=[f"{other}:{theirs}"]); await S.RemovePlayerSelectView(cog, sid)._on_select(it)
    assert "only remove players from the team you captain" in it.last and theirs in st["teams"][other]["on_field"]
    victim = next(p for p in st["teams"][turn]["on_field"] if p != capA.id); before = len(st["teams"][turn]["on_field"])
    it = fakes.Interaction(g, capA, values=[f"{turn}:{victim}"]); await rv._on_select(it); await settle()
    assert len(st["teams"][turn]["on_field"]) == before - 1

    # ---------- League: transfers stop at 6, the 7th only through the request
    big = dest
    while len(st["teams"][big]["on_field"]) < 6:
        uid = 300 + len(st["teams"][big]["on_field"]); g.add_member(uid); db.ensure_player(g.id, uid, "x")
        st["teams"][big]["on_field"].add(uid)
    donor = next(p for p in st["teams"][other]["on_field"] if p != st["teams"][other]["captain_id"])
    added, moved, reason = await cog.transfer_player(g, sid, st, donor, big)
    assert not added and "Request 7th" in reason, reason

    # ---------- admin panel works on any team; live + result flow; status updates
    ap = al[2].view
    it = fakes.Interaction(g, regular); await ap.button("End Session").callback(it); assert "Only an admin" in it.last
    it = fakes.Interaction(g, x["admin"]); await ap.button("Reassign Captain").callback(it); assert it.last_view is not None
    r1 = {s["match_id"]: s for s in st["slots"]}
    old_public = {s["n"]: s["public_message"] for s in st["slots"]}
    old_pairs = {s["n"]: tuple(s["team_ids"]) for s in st["slots"]}
    for mid, slot in r1.items():
        capch = g.get_channel(slot["captains_channel_id"])
        mmsg = next(m for m in capch.messages if isinstance(m.view, S.MatchControlView)); mv = mmsg.view
        cap = cap_of(g, st, slot["team_ids"][0]) or x["admin"]
        it = fakes.Interaction(g, cap, message=mmsg); await mv.btn_live.callback(it)
        assert "LIVE" in slot["public_message"].embeds[-1].description
        assert any("LIVE" in f[1] for f in al[1].embeds[-1].fields)
    for mid, slot in list(r1.items()):
        capch = g.get_channel(slot["captains_channel_id"])
        mmsg = next(m for m in capch.messages if isinstance(m.view, S.MatchControlView) and m.view.match_id == mid)
        a, b = slot["team_ids"]
        it = fakes.Interaction(g, x["admin"], message=mmsg)
        await cog.finalize_match(it, mid, a, b, 3, 1, mmsg.view, mmsg)
    await settle()
    # ---------- round 2: opponents changed, channels re-sorted, captains re-seated
    assert st["current_round"] == 2 and "Round 2" in al[1].embeds[-1].title
    new_pairs = {s["n"]: tuple(s["team_ids"]) for s in st["slots"]}
    assert new_pairs != old_pairs and all(set(new_pairs[n]) != set(old_pairs[n]) for n in new_pairs), (old_pairs, new_pairs)
    for slot in st["slots"]:
        cat = g.get_channel(slot["category_id"]); capch = g.get_channel(slot["captains_channel_id"])
        assert {k.id for k in cat.channels if k.kind == "voice"} == {st["teams"][t]["voice_channel_id"] for t in slot["team_ids"]}
        for t in teams:
            cap = cap_of(g, st, t)
            if cap: assert capch.can_see(cap) == (t in slot["team_ids"]), "round 2 captain access wrong"
        assert old_public[slot["n"]].deleted and not slot["public_message"].deleted and "Round 2" in slot["public_message"].embeds[-1].title
        assert sum(isinstance(m.view, S.CaptainPanelView) for m in capch.messages) == 1, "old panel should be replaced"
    assert all(" vs " in k.name for k in cats(g)[1:])
    for slot in st["slots"]:
        res = [v for n, v in slot["public_message"].embeds[-1].fields if n == "Results so far"]
        assert res and res[0].count("R1") == 2 and "3 - 1" in res[0], res        # both games' round-1 scores, in both channels
    # ---------- play the session out: final standings land in the public match channels
    pubs = [g.get_channel(slot["public_channel_id"]) for slot in st["slots"]]
    for rnd in (2, 3):
        for slot in list(st["slots"]):
            mid = slot["match_id"]; a, b = slot["team_ids"]
            capch = g.get_channel(slot["captains_channel_id"])
            mmsg = next(m for m in capch.messages if isinstance(m.view, S.MatchControlView) and m.view.match_id == mid)
            await cog.finalize_match(fakes.Interaction(g, x["admin"], message=mmsg), mid, a, b, 2, 1, mmsg.view, mmsg)
        await settle()
    for pub in pubs:
        assert any(m.embeds and "Final Standings" in (m.embeds[-1].title or "") for m in pub.messages), [m.embeds[-1].title for m in pub.messages if m.embeds]
    hist = x["history"].messages
    posts = [m for m in hist if m.embeds and " beat " in (m.embeds[-1].title or "")]
    assert len(posts) == 6 and len(hist) == 7 and "won, captained by" in hist[-1].content, [m.content for m in hist]
    pe = posts[-1].embeds[-1]
    assert "2–1" in pe.title and "Round 3" in pe.description and len(pe.fields) == 2, (pe.title, pe.description)
    assert pe.fields[0][0].startswith("🏆 Win") and pe.fields[1][0].startswith("💀 Loss"), pe.fields
    assert "(+" in pe.fields[0][1] and "(-" in pe.fields[1][1], pe.fields
    assert any("🔥" in p.embeds[-1].fields[0][1] for p in posts), "a 3-win streak should show a flame"
    he = hist[-1].embeds[-1]
    assert "Final Standings" in he.title and he.description.count("\n") == 3 and "🥇" in he.description, he.description
    assert he.fields[0][0] == "Results" and he.fields[0][1].count("\n") == 5, he.fields      # 6 games in a League session
    assert x["progress"].messages == []
    await settle(); await asyncio.sleep(0.05)
    assert sid not in cog.active_sessions and not cats(g), "session should close itself after the final round"
    # ---------- a second session: an admin ends it early
    g, cog, sid, st, x = await make("league", 16, 11)
    al = x["adminlog"].messages; ap = al[2].view
    it = fakes.Interaction(g, x["admin"]); await ap.button("End Session").callback(it)
    await it.last_view.confirm(fakes.Interaction(g, x["admin"]), None); await settle()
    assert not cats(g) and sid not in cog.active_sessions
    assert not any(isinstance(m.view, S.AdminPanelView) for m in x["adminlog"].messages)
    assert not al[1].pinned and "ended" in al[1].embeds[-1].title
    print("LEAGUE OK")

async def rivals():
    g, cog, sid, st, x = await make("rivals", 22, 2)
    assert len(cats(g)) == 2 and len(st["slots"]) == 1
    slot = st["slots"][0]; teams = list(st["teams"])
    assert all(len(i["on_field"]) == 11 for i in st["teams"].values())
    bench = g.get_channel(st["bench_channel_id"])
    assert bench.overwrites["everyone"].k == {"view_channel": False, "connect": False}, "bench should start hidden at 22"
    for t in teams:
        vc = g.get_channel(st["teams"][t]["voice_channel_id"])
        assert vc.user_limit == 11 and vc.name.endswith("MAX Players") and vc.category_id == slot["category_id"], (vc.name, vc.user_limit)
    labels = [b.label for b in slot["panel"]["view"].children]
    assert not any("Request" in l for l in labels) and labels[0] == "No sub available", labels
    # a player is removed -> bench comes back, name goes back, sub can be added up to 11 again
    t = teams[0]; cap = cap_of(g, st, t); victim = next(p for p in st["teams"][t]["on_field"] if p != cap.id)
    await cog.remove_player_from_roster(g, sid, t, victim); await settle()
    vc = g.get_channel(st["teams"][t]["voice_channel_id"])
    assert bench.overwrites["everyone"].k == {"view_channel": True, "connect": True} and vc.name == st["teams"][t]["club_name"]
    await join_bench(g, cog, st, 201)
    it = fakes.Interaction(g, cap); await slot["panel"]["view"].button("Add sub").callback(it); await settle()
    assert len(st["teams"][t]["on_field"]) == 11 and vc.name.endswith("MAX Players"), it.replies
    assert bench.overwrites["everyone"].k["view_channel"] is False
    # admin drops into the full channel -> 12, leaves -> 11
    adm = x["admin"]; before = vs(None); adm.join(vc); await cog.on_voice_state_update(adm, before, vs(vc))
    assert vc.user_limit == 12, vc.user_limit
    adm.join(x["lobby"]); await cog.on_voice_state_update(adm, vs(vc), vs(x["lobby"]))
    assert vc.user_limit == 11
    await cog.end_session(sid, ended_by=adm); await settle()
    assert not cats(g)
    print("RIVALS OK")

asyncio.run(league()); asyncio.run(rivals())

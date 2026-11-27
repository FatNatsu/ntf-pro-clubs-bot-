# NTF stability overhaul: engineering report

- **Branch:** `ntf-stability-overhaul` (from `main` at `fa26bd7`, the audited commit; `main` had no newer commits)
- **Date:** 27 Nov 2026
- **Status:** on the branch only. Not merged, not deployed. `main`, `team-cap-7`, `match-channels` and `hidden-mmr` are untouched, and the live bot was not touched.
- **Tests:** 12 of 12 test scripts pass (`python3 tests/run_all.py`). **No live Discord testing has been done.**

## Summary

The audit was mostly right, and one finding was worse than described. **NTF-001 is critical: a League or Rivals queue that fills up by itself never starts a session.** The countdown cancels its own task on the way, the session never gets built, and the queue is wiped without an error anywhere, because a cancellation slips past the `except Exception` handler. Real testing hasn't caught it because sessions have been started with Force Start, and the old test fakes never paused the way real Discord calls do, which hid it. It is fixed, with a test that fails on the old code and passes on the new one.

Fixed and tested: 001–005, 007, 009–011, 013 (interim policy), 014, 016, 020, 021, 028, 029, and 11 issues the audit didn't list. The rest need a Founder decision, a live Discord check, or are bigger pieces of work (restart recovery and permission revocation). Each is marked below.

**The approved League and Rivals rules are unchanged.** `config.py` (MODE_RULES, the queue sizes, force-start minimums, caps, the 15-second countdown, K-factors and rank floors), `mmr.py` and `team_balance.py` have no changes on this branch.

## Issue by issue

Statuses use the handover's list. "Test" names the file in `tests/`.

| ID | Severity | Status | What was found and done | Test |
|---|---|---|---|---|
| NTF-001 | Critical | Confirmed and fixed | Reproduced. `_countdown_then_start` → `_pop_and_start` → `_reset_mode_state` cancelled the running countdown task itself, so the next real network call raised `CancelledError`. Now nothing cancels the current task, and countdown failures are logged. | run_queue, run_queue_stability (fails on old code) |
| NTF-002 | High | Confirmed and fixed | Countdown only checked "anyone left?". Now it stops as soon as the queue drops below full (leaving, or switching modes), re-checks right before starting, and drops anyone since put on a team. Never starts undersized by itself. | run_queue_stability |
| NTF-003 | High | Confirmed and fixed | No start lock. Found by reading the code: two Force Starts could start a second, empty session. Now there's a per-mode STARTING state, set before the first await, so only one start can happen. A failed start puts everyone back in the queue (original order) and pauses it, so it can't fail in a loop; Resume tries again. Regression test passes on the new code; it wasn't run against the old code. | run_queue_stability |
| NTF-004 | Medium | Confirmed and fixed | Resume restarted a full 15s. Now a paused countdown keeps its time left (at least 5s). If anyone left while paused, there's no countdown on resume; it restarts from 15s once full. Pause is refused while a session is being built. The other mode is unaffected. | run_queue, run_queue_stability |
| NTF-005 | High | Confirmed and fixed | A rostered player could ready up for another session. Now refused with an ephemeral message, and checked again at pop and at Force Start. Bench players and spectators aren't blocked, and sessions in other servers don't count. Several sessions at once with different players still work. | run_queue_stability |
| NTF-006 | Medium | Requires Founder decision + live check | Already partly handled: players the bot couldn't move are listed publicly at start and keep their roster place. Not changed: should players not in voice at pop be started anyway (current), or benched? | — |
| NTF-007 | High | Confirmed and fixed | Session end purged the whole queue channel. Now it deletes only the bot's own messages, and never the panel, a live or paused ready check, another running session's announcements, pinned messages or anything people wrote. `/queue_panel` replaces its previous panel. | run_queue_stability |
| NTF-008 | High | Confirmed and awaiting fix | Live session state is in memory only. This is the biggest remaining piece of work: persist session state and restore views on start, or at least clean up safely on start. Plan below. The README now says to update between sessions. | — |
| NTF-009 | High | Confirmed and fixed | Double counting was already blocked in practice (no await between the check and the write), but a crash could leave MMR changed without a match record. Now one transaction covers the match row, visible and hidden MMR, wins and losses, participants with exact deltas and club rows, with a database-level "only if still pending" guard. Discord updates come after and are best-effort. **Also found:** a failed button edit stopped the round from advancing. Fixed. | run_match_integrity (simulated disk error, simulated Discord error, duplicate submit) |
| NTF-010 | High | Confirmed and fixed | Reproduced: two League final games reported together posted the final standings twice. Now a "finished" flag is set before the standings start, and the winner plus the +15 bonus are awarded in one transaction that can only succeed once per session. Ending a session twice at once now runs once. | run_match_integrity (fails with the guard removed) |
| NTF-011 | High | Confirmed and fixed | Non-numbers became "no score", and negative or contradicting scores were accepted. Now scores must be whole numbers from 0 to 99, the winner must be ahead, and a level score is sent to Went to Penalties. Nothing is recorded on a bad score. Penalty wins are now stored with a `went_to_pens` flag (new column). | run_match_integrity |
| NTF-012 | Medium | Requires Founder decision | Confirmed: reporting without Match Live uses the roster at the moment of reporting, and reporting while paused is allowed. Requiring Match Live is safer but blocks a captain who forgets to press it. | — |
| NTF-013 | Medium | Confirmed and fixed (interim) | Exact deltas (visible and hidden) were already stored and reversed. Undo is now refused once the final standings are in, because the standings and bonus would no longer match. Replaying later ratings after an old correction isn't built; corrections after the final use the admin commands. | run_match_integrity |
| NTF-014 | Medium | Confirmed and fixed | Captaincy is now re-checked when the score is submitted. Went to Penalties is disabled after a result. Stale remove, transfer and reassign menus now check the player is still where they were. (A stale transfer could re-add a removed player.) | run_match_integrity |
| NTF-015 | Medium | Already resolved | Add Sub, 7th player, Admin Pull and Transfer already re-check bench, cap and turn inside one lock. Remove and Reassign aren't locked, but they can't push a team over its cap. | existing run_sessions |
| NTF-016 | Medium | Confirmed and fixed | Admin transfer and remove menus cut off at 25 of 28 League players. Over 25, admins now pick the club first. Every other menu was checked and is at most 11 options. Option labels are capped at 100 characters. | run_match_integrity |
| NTF-017 | Low | Requires live Discord verification | Server-side checks already decide who can take a sub. Needs eyes on the shared panel in real Discord. | — |
| NTF-018 | Low | Partly reviewed, no change | Covered by 005 and the existing sub rules. A full edge-case pass is still to do. | — |
| NTF-019 | Medium | Confirmed and awaiting fix | Intentional in the code: an outgoing captain keeps Move Members, and a transferred or removed player keeps their old team channel access. Needs a central permission sync. I held off because a wrong overwrite removal can lock players out mid-session, and it can only be verified in real Discord. | — |
| NTF-020 | Medium | Confirmed and fixed | Reassign Captain wrote to the permanent whitelist. It no longer does. Existing whitelist entries are untouched. | run_match_integrity |
| NTF-021 | Medium | Confirmed and fixed | The bot unmuted spectators a moderator had muted. It now only undoes mutes it applied: on leaving, on switching, and at session end. Mutes left over after a restart belong under 008. | run_spectators |
| NTF-022 | Low | Already resolved | The MAX Players suffix, capacity derived from spectators, and rate-limited renames already exist. Recruitment counts the roster, not the channel name. | existing run_sessions |
| NTF-023 | Medium | Confirmed and awaiting fix | Logging was added everywhere this branch touched. Many `except HTTPException: pass` remain in `voice_utils.py` and channel setup. | — |
| NTF-024 | — | Reviewed, one decision | Consistent with the approved policy: hidden MMR moves with match results, the bonus is visible-only, hidden survives reset, and a missing hidden value falls back to visible. **Question:** the admin visible MMR and win/loss corrections don't move hidden MMR. Should they? | existing run_hidden |
| NTF-025 | — | Proposal (not started) | Needs a draft simulation over history. | — |
| NTF-026 | — | Requires Founder decision | Current behaviour: the bonus goes to the winning team's roster at the end, so a late joiner or a 7th who never played gets it, and someone who played and left doesn't. Ranking is wins, then fewer losses, then GD. **A tie on all three is decided by team order, which is arbitrary.** | — |
| NTF-027 | — | Requires Founder decision | Season reset deletes sessions and matches by design. The backups now keep a copy. Recommend season-tagged history (additive migration). | — |
| NTF-028 | High | Confirmed and fixed | Now one reset per server at a time, and the automatic reset carries the season number so a stale one does nothing. Backup first. A reset and a queue turning into a session can't overlap (a reset could have deleted a session mid-build). The 24h warning is only marked sent once it posts. Remaining: the archive post isn't retried (the backup holds the standings), and there's no explicit policy if sessions run back-to-back at season end (it retries every 10 min). | run_season_safety |
| NTF-029 | High | Confirmed and fixed | Backups use SQLite's online backup API and are integrity-checked, kept 30 per label, taken on every bot start (before `init_db` adds columns) and before every season reset. A daily systemd timer and restore steps are in `deploy/ORACLE.md`. The database already lives outside the repo, foreign keys are on and `*.db` is git-ignored. Remaining: backups sit on the same disk; copy one off now and then. | run_season_safety, migration check |
| NTF-030 | Low | Incorrect audit finding (partly) | Deployment config exists: `deploy/` has the Oracle systemd unit and install script. Railway is no longer used. discord.py is now pinned below version 3. The Python version isn't pinned. | — |
| NTF-031 | Low | Confirmed and awaiting fix | No admin diagnostics command yet. | — |
| NTF-032 | — | Already resolved | The bot already runs on an Oracle Cloud VM with systemd auto-restart. | — |
| Banners | — | Incorrect audit finding | The assets are `.jpg` and the loader expects `.jpg`. They match; it was an intentional conversion. All 10 required banners are present. | existing run_queue |
| EA data | — | Already investigated | 26 Nov: EA's endpoints returned 403 from the server, with and without browser headers. The Founder dropped the integration, and it isn't reopened here. | — |

## Additional issues found

1. A failed Discord edit of the result buttons stopped the round advancing and the final standings appearing. Fixed.
2. `end_session` could run twice at once (the auto-close and an admin's End Session). The second raised KeyError and posted duplicates. Fixed.
3. If the "session ended" announcement failed to post, the rest of the teardown never ran. Fixed.
4. The test fakes never yielded like real network calls, which hid NTF-001. Fixed in `tests/fakes.py`.
5. A final-standings tie on wins, losses and GD is decided by team order. Needs a decision (NTF-026).
6. A season reset and a session being built could overlap; the reset would delete the new session's rows. Fixed.
7. Went to Penalties stayed clickable after a result. Fixed.
8. Penalty wins weren't marked in the database. Fixed (`matches.went_to_pens`).
9. A countdown that died did so silently. Now logged.
10. A stale transfer menu could put a removed player back on a team. Fixed.
11. One session's clean-up deleted another running session's announcements. Fixed.

## Changed files

`backup.py` (new), `bot.py`, `cogs/queue_cog.py`, `cogs/session_cog.py`, `cogs/season_cog.py`, `cogs/admin_cog.py`, `season.py`, `database.py`, `requirements.txt`, `README.md`, `.gitignore`, `deploy/ORACLE.md`, `deploy/ntf-backup.service` (new), `deploy/ntf-backup.timer` (new), `tests/` (new: the harness moved into the repo, plus 4 new test files).

Commits, oldest first: `846f8aa` tests in repo, `a98c74e` queue, `596056e` match integrity, `e5e10e5` season and backups, `607da91` spectators, `d5624f5` README, `5c1fb7a` startup backup, then this report.

## Database migration

One additive change: `ALTER TABLE matches ADD COLUMN went_to_pens INTEGER NOT NULL DEFAULT 0`, run by `init_db` on start, after an automatic backup. It was tested on a database in the old shape: existing rows are kept, the new column reads 0, and running it twice is harmless. The old code still works with the new column, so rolling back needs no database change.

## Backup and rollback

- **Before deploying:** `.venv/bin/python backup.py --verify` on the server. The bot takes another backup by itself when it starts.
- **Rollback:** `git checkout main`, then `sudo systemctl restart ntf-bot`. The database stays as it is.
- **Restore a backup** (only if data was damaged): the steps are in `deploy/ORACLE.md`.

## Automated tests

`python3 tests/run_all.py`: 12/12 pass. They use a temporary database and a stand-in for Discord. They cover: auto-start in both modes, leaving or switching mid-countdown, the countdown vs Force Start race, a double Force Start, a failed start and recovery, pause timing, rostered players, queue channel clean-up, score checks, a stale captain, a simulated disk failure mid-result, a Discord failure after saving, duplicate results, simultaneous final games, Undo after the final, double end, 28-player admin menus, stale menus, the whitelist, spectator mutes, a concurrent season reset, a stale automatic reset, backup and retention, a failed archive post, a reset during a session start, the warning retry, plus all the earlier session, sub, pause, event, hidden-MMR, reset and stats tests.

**Not executed:** anything in real Discord (permissions, voice moves, rate limits, how panels look on phone and desktop), restarts, and real network timing beyond the simulated yields. The automated checks against Discord's limits that the handover asked for are not written yet.

## Live Discord checklist (after deploying, between sessions)

1. A test queue fills to the full number by itself and starts after the countdown. **This is the NTF-001 fix: the most important check.**
2. Someone leaves mid-countdown: it stops, and starts again when the queue refills.
3. Pause mid-countdown, then resume: it carries on with the time that was left.
4. Two admins press Force Start together: one session.
5. A player on a team tries to ready up: refused with a message.
6. Report a bad score (letters, or the loser ahead): refused, nothing recorded.
7. Report a penalty win: shows as "pens" on the match channel.
8. Both League final games reported at the same moment: one set of final standings, +15 once.
9. Undo after the final: refused.
10. Admin Transfer or Remove with 26 or more players: club menu first, everyone reachable.
11. Reassign Captain: the new captain isn't added to `/captain` whitelist.
12. A moderator-muted spectator watches, then leaves: stays muted.
13. After the session ends: the queue channel keeps the panel and any live ready check; old bot posts are gone.
14. `~/ntf-data/backups/` has a `pre-start` backup.

## Production deployment plan

1. The Founder approves (optionally ChatGPT reviews the branch first).
2. Merge `ntf-stability-overhaul` into `main`.
3. Between sessions, on the server: `git pull`, `backup.py --verify`, `sudo systemctl restart ntf-bot`, then `sudo journalctl -u ntf-bot -f` to watch it start.
4. Install the daily backup timer (three commands in `deploy/ORACLE.md`).
5. Run the live checklist with a test session.

## Outstanding Founder decisions

1. **NTF-012:** require Match Live before a result can be reported? Allow reporting while a match is paused?
2. **NTF-026:** who gets the +15 (the end roster, as now, or only players who played a game for the winners?), and the tie-break after wins, losses and GD (head-to-head, goals scored, or a shared first place?).
3. **NTF-027:** keep match history across seasons (recommended), with the visible stats still resetting.
4. **NTF-024:** should the admin MMR and win/loss corrections move hidden MMR too?
5. **NTF-006:** players not in voice at pop: keep starting them (as now) or bench them?
6. **NTF-008:** full session restore after a restart (bigger), or safe clean-up on start (smaller, done first)?
7. **Phase 9 proposals** (reliability stats, achievements, longer profiles, positions, an independent hidden rating): which to plan first?

## Next engineering work, in order

1. NTF-008 restart recovery: start with safe clean-up on start (find sessions the database says are active, alert the admin log, delete only their bot-made categories, lift bot-made mutes), then full restore.
2. NTF-019 permission sync, built together with a live test plan.
3. NTF-023 observability, plus an admin `/ntf_status` diagnostic (NTF-031).
4. Automated checks against Discord's limits (embed, field, label and component sizes).

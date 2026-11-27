# NTF — Pro Clubs Discord Bot

Auto-balanced Pro Clubs sessions: ready-check → team draft → auto voice
channels → captain-run match reporting → live MMR/ranks → post-match stats.
Built for console players — almost everything is button-driven.

## 1. Create the bot application

1. https://discord.com/developers/applications → **New Application** → name it `NTF`.
2. **Bot** tab → Add Bot → copy the **Token** (`DISCORD_BOT_TOKEN`).
3. **Privileged Gateway Intents**: enable **Server Members Intent**. (Message
   Content is NOT required — everything runs on slash commands/buttons.)
4. **OAuth2 → URL Generator**: scopes `bot` + `applications.commands`.
   Bot permissions: Manage Channels, Move Members, Mute Members,
   View Channels / Connect / Speak, Send Messages / Embed Links.
5. Invite NTF using the generated URL.

## 2. Run it

```bash
pip install -r requirements.txt
export DISCORD_BOT_TOKEN="your-token-here"
export DISCORD_GUILD_ID="your-server-id"   # optional, makes slash commands sync instantly
python bot.py
```

`pro_clubs.db` (SQLite) is created in the working directory on first run —
that's your whole database.

## 3. One-time server setup (admins)

```
/ntf_setup
```
Creates (or locates, if you rename/move them) the **permanent** channels:
- the NTF queue channel, where players ready up
- 🏆 **#leaderboard** — live-updated leaderboards, top 10 by MMR for each mode
- 📜 **#session-history**, 🔒 **#admin-log** and 🏅 **#season-archive**

There is no permanent "in-progress" channel any more: while a session runs,
each game's public match channel shows everything (see step 8).

Then:
```
/club add name:Real Madrid          (repeat for every club you want in the pool)
/captain add member:@SomePlayer     (repeat for everyone who should auto-captain)
```
Wherever you want the queue itself:
```
/queue_panel
```
Posts the **NTF Pro Clubs Queue** entry point. Only admins (Manage Server)
can post this panel; any player can then ready up from it.

## 4. How a session flows

1. Player taps **Join Rivals** or **Join League**, picks their platform
   (**PS5**/**PC**) — that's their ready-up in one step. This creates/updates
   a dedicated **ready-check message** for that mode with a live list, so
   from then on players can ready up directly from that message too instead
   of going back to the main panel.
   The panel shows each mode's count, a progress bar, its status and its
   rules. Admins can **⏸️ Pause Queue** on a mode's ready check: nobody new
   can ready up and a running start countdown stops, but everyone already
   readied stays in (and can still leave). **▶️ Resume Queue** puts it back;
   if the queue is still full, the countdown carries on from where it was
   paused (at least 5 seconds). If anyone left while it was paused, the
   countdown starts from the top once the queue is full again. League and
   Rivals pause separately. If a session ever fails to start, everyone is
   put back in the queue and it pauses itself; an admin presses Resume to
   try again.
2. **League** needs 24 ready (4 teams of 6), **Rivals** needs 22 (2 teams
   of 11). The two modes have separate sizes throughout. Once a mode hits its
   number, a **15-second countdown** starts (last chance to back out) before
   the bot locks it in. If someone leaves during the countdown it stops, and
   it starts again when the queue is full. A queue never starts short by
   itself; only Force Start can do that. Someone who is on a team in a
   session that's still running can't ready up for another one.
3. **Force start**: if a queue stalls, an admin can hit **⚡ Force Start**
   on the ready-check message once there are 3 per team — **12+** ready in
   League, **6+** in Rivals. It skips the countdown and starts immediately
   with however many are ready, split evenly (16 in League seats 4 per
   team).
4. **Closing a queue**: admins can close either queue on demand with
   **`/close_queue mode:<rivals|league>`**. (There is no automatic
   time-out: a queue stays open until it fills, is force started or is
   closed.) When a session ends, the queue channel is tidied: the bot
   removes its own old messages, but never the panel, a live ready check,
   another running session's posts, pinned messages or anything people wrote.
5. At pop time, the bot lays the session out as one small category for the
   Bench and then **one category per game** being played at the same time
   (League has two games a round, Rivals one):

   ```
   PRO CLUBS SESSION #12 · BENCH (LEAGUE)
       📋 sub-queue            everyone: whose turn it is, who's waiting
       🪑 Bench                voice, no player cap
   PRO CLUBS SESSION #12 · MATCH 1 · Dhafar vs NTF
       📋 match-1              everyone, read-only: both rosters + Watch buttons
       🎛️ match-1-captains     only that game's two captains (and admins)
       Dhafar                  voice, limited to the mode's team cap
       NTF                     voice
   PRO CLUBS SESSION #12 · MATCH 2 · ...
   ```

   Each game's two captains get their own channel and panel, separate from
   the other game. In League the opponents change every round, so at the
   start of each round the bot moves every team's voice channel under the
   game it's now playing in and lets the right two captains into each
   captains channel. (Discord always lists a category's text channels above
   its voice channels, which is why each game has its own category.)
6. Captains are auto-picked from your whitelist (fills in with the
   highest-MMR remaining players if you don't have enough whitelisted
   captains queued). Everyone else is placed strongest first, each onto
   whichever team currently has the lowest total (hidden) MMR, so total team
   strength is as even as possible, and players are dragged straight into
   their team's VC. **Discord can only move people already in some voice
   channel** — get players into voice before the queue pops.
7. In each game's **captains channel**, a round starts with a
   **`━━━ ROUND N ━━━`** divider, then that fixture's panel: **▶️ Match
   Live** at kick-off, then two big red buttons to report the winner.
   **Match Live has to be pressed before a result can be reported**: it
   tells everyone the game is on and locks both rosters for MMR. A paused
   match has to be resumed first.
   Tapping a side pops up a scoreline prompt, then locks the result in —
   winner turns 👑 green, both buttons disable. If players lag out and the
   game has to be restarted, either captain (or an admin) presses
   **⏸️ Pause Match**: the match shows as paused everywhere and the rosters
   unlock, so a sub brought on during the pause counts. **Resume Match**
   puts it live again and re-locks the rosters. Underneath sits the
   **captain panel** for roster changes, and a copy of the live Sub Queue.
8. Each game's **public match channel** shows both teams, their captains
   and players, whether the game is waiting, live or finished, and
   **👀 Watch \<Club\>** buttons that let anyone already in a voice channel
   spectate (auto-muted). Under the rosters a **Recent changes** list shows
   the latest subs, transfers, removals and captain changes for **those two
   teams only** (a transfer between the two games shows in both), and a
   **Results so far** list shows every score reported this session, from
   both games. It is all one message the bot keeps up to date, so nothing
   scrolls away. The final standings are posted here too.
9. The **captain panel** acts on the team of whoever presses it:
   - **🔁 Add sub** pulls a player from Bench straight onto your team
     (longest-waiting players first, best MMR fit among them). The turn
     order runs across the whole session: the team with the fewest players
     goes next, whichever game it's in.
   - **🔄 Transfer Player** moves one of **your own** players onto any other
     team with room, including a team in the other game.
   - **❌ Remove Player** takes one of **your own** players off the roster.
   - A captain can never transfer or remove another team's player; admins
     can, from the admin panel (below).
   - In League a transfer only fills a team to 6 — the 7th seat only ever
     comes through **➕ Request 7th player**.
   - **League:** Add sub only fills a team up to **6**. A team at 6 can take
     one more, to a hard maximum of **7**, only if its captain (or an admin)
     presses **➕ Request 7th player** — and only once no other team is
     still under 6.
   - **Rivals:** Add sub fills a team all the way to its cap of **11**.
   - When a team reaches its cap, its voice channel is renamed
     "<club> - MAX Players"; the name goes back when a seat opens. Discord
     only lets a channel be renamed about twice every ten minutes, so the
     name can lag behind.
   - While **every** team is at its hard cap (7 in League, 11 in Rivals) the
     Bench channel is hidden, since nobody can be subbed on. It reappears as
     soon as a player is removed or transferred out. In League the Bench
     stays visible while teams sit at 6, because a 7th can still be requested.
   - A team channel's user limit is the cap **plus** one for every spectator
     watching it and every admin sitting in it who isn't on the team (11
     becomes 13 with two extra), and drops back when they leave.
10. **MMR updates the moment each result is reported** — a fixed amount per
    win or loss that depends on the player's own rank (from +32/0 at G to
    +10/-24 at S+; the table is `K_FACTORS` in `config.py`), so wins and
    losses matter less individually near the top and the ladder stays
    stable. A result is saved in one go (MMR, record, history) and can never
    be counted twice. Scores are checked: whole numbers from 0 to 99, the
    winner must be ahead, and a level score has to go through Went to
    Penalties. By the time the last match is in, every player's MMR for
    the session is already settled. S rank starts at 1,100 MMR and S+ at 1,500.
11. **Admins** run the session from the **admin log** channel. When a
    session starts the bot posts there: a pinned **live status** message
    (each game in the current round: waiting / LIVE / finished with the
    score) and an **admin panel** — Pull Sub For Team, Transfer Player,
    Remove Player, Reassign Captain and **🛑 End Session**. Every action
    during the session (subs, transfers, removals, captain changes, matches
    going live, results, undos) is logged underneath, as before. Ending the
    session is admin-only; captains no longer have that button. If a server
    has no admin log set up, admins still have `/force_end_session` and
    `/admin_end_all_sessions`.
12. When the final round completes, each game's public match channel and
    captains channel post the session summary — every team's win/loss
    record, ranked 🥇🥈🥉 down to last place. That stays up for **60
    seconds**, then every session category (Bench, each game's channels and
    team VCs) is torn down automatically. **#session-history** keeps the
    record for the season: the winner, the full final table and every score
    (the channel is cleared at each season reset; the season's sessions and
    results stay saved in the database, and its final records show in the
    profile Season menu). The
    final standings and the +15 session bonus happen once, even when both
    League games finish at the same moment, and a result can't be undone
    after the final standings are in.
    - **Ranking:** wins, then fewest losses, then goal difference.
    - **Decider:** if two teams are still level for 1st, they play one more
      game, the Decider. It counts for MMR like any other game, and the
      winner takes 1st. If an admin ends the session before it's played,
      the two teams share 1st.
    - **Three or more level for 1st** (League only): most goals scored
      wins; if they're still level, they share 1st.
    - **Who gets the +15:** everyone who played at least one game for the
      winning team, plus everyone on it when the session ends, once each.
    The admin panel is removed and the final status stays in the admin log
    as part of the record.

### Look: banners and colours

Panels carry a banner strip on top and an accent colour down the left edge:
cyan for League, purple for Rivals, grey-blue for admin and shared panels.
The banner pictures live in `assets/banners/` (`queue`, `ready_check_league`,
`ready_check_rivals`, `match`, `captains`, `sub_queue`, `admin`,
`leaderboard`, `session_history`, `season_archive`). A banner file that is
missing is simply skipped, so
adding one is just dropping a `.jpg` with the right name into that folder.
The shared helpers are in `ui.py`.

## 5. Stats & leaderboard, anywhere, anytime

```
/rank [member]                    → rank letter (G→S), MMR, W/L
/leaderboard                      → top 10 by MMR for a mode, with the season countdown
/player_stats [member]            → rank, record, last-10 form, best club, most-played-with teammate
/club_stats club_name:<name>      → club's record, last-10 form, best win streak, top player for that club
```
🏆 #leaderboard also keeps one message permanently up to date, refreshed
after every reported result.

## Ranks

| Rank | MMR floor |
|------|-----------|
| G    | 200       |
| F    | 300       |
| E    | 400       |
| D    | 500 (start) |
| C    | 600       |
| B    | 700       |
| A    | 900       |
| S    | 1100      |
| S+   | 1500      |

Only individual players carry MMR — clubs/teams only ever have plain
win/loss records (see `/club_stats`). K-factor: 32 normally, 20 once you're
A rank or above, 12 at S rank. Tune all of this, team size, bench size, and
the queue/timeout numbers in `config.py`.

## Adding NTF to more than one server

A single bot application/token can be in as many servers as you want — you
don't need a second bot or a second token. Just re-open the OAuth2 invite
URL from step 4 and pick the other server (whoever does it needs Manage
Server permission there).

**Every server gets its own isolated data.** Clubs, the captain whitelist,
player MMR/ranks, match history, and the leaderboard are all scoped per
server — a player who's in two of your servers has a completely separate
MMR/rank in each one, and club name pools don't leak between servers either.
Run `/ntf_setup`, `/club add`, and `/captain add` again in each new server;
they only affect that server.

Queues and live sessions were already per-server from day one, so multiple
servers can even run sessions at the same time off the one running bot
process without interfering with each other.

## Known assumptions / things worth knowing

- **Discord can't force-join someone into voice from nowhere** — it can only
  *move* someone already connected somewhere.
- **Sessions live in memory while the bot is running.** A mid-session
  restart loses that session's live state (current round, etc.) — finish or
  end sessions before restarting the bot. Everything already reported
  (MMR, match/club history) is safe in the database regardless.
- Force-start team size is `players_ready // num_teams`, so a 16-player
  League force start seats 4 per team and a 12-player one seats 3; the
  voice channels are still created at the mode's full cap so subs can fill
  the rest later.
- Round matchups use a standard round-robin **circle method** for League, so
  it's a genuine mini-league, not a random bracket.
- The 5-minute League auto-close timer starts on the *first* person readying
  up for League, not from when the panel was posted.

## Seasons, the countdown and the Golden Crown

- A season lasts `SEASON_LENGTH_DAYS` (30) days from its start. The clock
  starts by itself the first time the bot sees a server that has run
  `/ntf_setup`.
- Both leaderboards show the season number and when it ends. The date counts
  down by itself (Discord date stamps), in each member's own time zone.
- `SEASON_WARNING_HOURS` (24) before the end, a warning is posted in the
  season archive.
- When the countdown runs out the bot resets the season by itself. If a
  session is live it waits and tries again every 10 minutes.
  `/season_reset` still works and ends the season early.
- **Golden Crown:** at each reset, the player with the most wins in each
  mode (ties: fewer losses, then higher MMR) is named in the season archive
  and carries a 👑 on the leaderboard and in `/rank` for the next season.
- **Each season keeps its own history.** The reset puts MMR and records back
  to the start, but every session, match and club result stays saved under
  its season, along with every player's final record and the Golden Crown
  winners. Leaderboards, profiles, club stats, head-to-head and
  `/session_history` show the current season. Session numbers start from #1
  each season. The #session-history channel is still cleared at each reset.
- **Profiles have a Season menu** once a player has an ended season: pick a
  season to see that season's final rank, MMR, W-L, form, crown and
  highlights (best club, teammates, rival).

## Hidden MMR (admins only)

Every player has a second rating per mode that seasons never reset. It moves
by the same amount as the visible MMR after every result, and it is what
teams and sub picks are balanced on, so a new season where everyone shows the
starting MMR still gets even teams. It never appears on the leaderboard, in
`/rank` or in match posts.

- `/hidden_mmr mode:<mode> [member]` — private view: hidden MMR, where the
  player finished last season, and this season's MMR. Without a member it
  lists the top 25.
- `/hidden_mmr_set member mode value` — set it by hand, for example from an
  old season's leaderboard. Visible MMR and records are not changed.

## Tests

```
python3 tests/run_all.py
```

Runs every test in `tests/` against a throwaway database, with a stand-in
for Discord (`tests/fakes.py`), so it never touches real player data or a
real server. Run it before every update to the live bot.

## Backups and restarts

- The bot backs the database up by itself before every season reset. A daily
  backup timer and the restore steps are in `deploy/ORACLE.md`.
- A running session lives in the bot's memory, so restarting the bot ends
  it. When the bot starts again it tidies up by itself: the cut-off
  session's channels are removed (only that session's, checked by their
  saved ids and name), spectator mutes it applied are lifted, results
  already reported stay saved and count, and the admin log gets a note. No
  session bonus is given for a session that didn't finish. The queue
  channel is tidied and gets a fresh panel. Still best to update between
  sessions.
- `/ntf_status` (admins, only you see it): sessions running, both queues,
  the newest backup and the last errors. Important failures (a session that
  won't start, a failed backup or season archive, a restart mid-session)
  are also posted in the admin log.
- Voice access follows the roster: a transferred or removed player loses
  their old team's call, and a replaced captain goes back to normal player
  access. Admins and ghosts are never changed.

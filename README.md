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
Creates (or locates, if you rename/move them) the two **permanent** channels:
- 📺 **#in-progress** — live match updates, and "no games are currently in
  progress" when nothing's running
- 🏆 **#leaderboard** — one live-updated leaderboard message, top 20 by MMR

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
2. **Rivals** needs 12 ready, **League** needs 24. Once a mode hits its
   number, a **30-second countdown** starts (last chance to back out) before
   the bot locks it in.
3. **League force start**: if League stalls, an admin can hit
   **⚡ Force Start** on the ready-check message once **16+** are ready
   (enough for 4 full teams of 4) — it skips the countdown and starts
   immediately with however many are ready.
4. **League auto-close**: if League never reaches 16 within **5 minutes** of
   the first person readying up, it auto-closes itself and announces it, so
   the channel doesn't sit clogged with a queue that's never going to pop —
   players can jump straight into a fresh Rivals queue instead. Admins can
   also close either queue on demand any time with **`/close_queue
   mode:<rivals|league>`** — a quick, low-friction way to keep the channel
   tidy.
5. At pop time, the bot creates a category `NTF SESSION #<id> (MODE)`
   containing: one locked-to-6 voice channel per team (named after a
   randomly assigned club), 🪑 **Bench**, and 🎛️ **session-control**
   (visible only to that session's captains). If it was a force start with
   fewer than the full number of players, the missing head-count is added
   on top of Bench's normal capacity — e.g. a 16-player League force start
   opens Bench up to 12 (8 missing + 4 normal), leaving room for stragglers
   and rotation.
6. Captains are auto-picked from your whitelist (fills in with the
   highest-MMR remaining players if you don't have enough whitelisted
   captains queued). Everyone else is snake-drafted by MMR so total team
   strength is as even as possible, and players are dragged straight into
   their team's VC. **Discord can only move people already in some voice
   channel** — get players into voice before the queue pops.
7. In session-control, each round starts with a **`━━━ ROUND N ━━━`**
   divider, then one clearly separated panel per fixture (its own embed +
   two big red buttons) so captains always know which match they're tapping
   into. Tapping a side pops up a scoreline prompt, then locks the result in
   — winner turns 👑 green, both buttons disable.
8. **#in-progress** mirrors every round as a set of **separate cards, one
   per matchup** (not crammed into one block) — clean visual separation
   between all the teams playing that round. Once a result lands, that
   card turns 🟩 green with a 👑 crown on the winner and 🟥 red on the
   loser. When the next round starts, the whole card set is replaced with
   a fresh one. **👀 Watch \<Club\>** buttons let anyone already in a voice
   channel spectate (auto-muted).
9. Either team's captain (or an admin) can hit **🔁 Add sub** in
   session-control — it just pulls a random player currently sitting in
   Bench straight onto the team (no swap, no picking who comes off; it's
   built for filling empty seats left by a force start).
10. **MMR updates the moment each result is reported** — Elo-style, with the
    swing (K-factor) shrinking the higher a player's own rating is: full
    strength normally, smaller once they're A rank, smallest at S rank —
    so wins/losses matter less individually near the top and the ladder
    stays stable. By the time the last match is in, every player's MMR for
    the session is already settled. S rank starts at 2500 MMR.
11. When the final round completes, **#in-progress** and session-control
    both post the session summary — every team's win/loss record, ranked
    🥇🥈🥉 down to last place. That stays up for **60 seconds**, then the
    entire session category (all team VCs, Bench, session-control) is torn
    down automatically and #in-progress posts **"No games are currently in
    progress."** A captain or admin can also end early any time with
    **🛑 End Session** (with a confirm step) — or admins can use
    `/force_end_session` / `/admin_end_all_sessions`, no captain required.

## 5. Stats & leaderboard, anywhere, anytime

```
/rank [member]                    → rank letter (G→S), MMR, W/L
/leaderboard                      → top 20 by MMR, your own rank highlighted (shown separately if you're outside the top 20)
/player_stats [member]            → rank, record, last-10 form, best club, most-played-with teammate
/club_stats club_name:<name>      → club's record, last-10 form, best win streak, top player for that club
```
🏆 #leaderboard also keeps one message permanently up to date, refreshed
after every reported result.

## Ranks

| Rank | MMR floor |
|------|-----------|
| G    | 0         |
| F    | 1000      |
| E    | 1200 (start) |
| D    | 1400      |
| C    | 1700      |
| B    | 2000      |
| A    | 2300      |
| S    | 2500      |

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
  League force start seats 4 per team; the voice channels are still created
  at the normal 6-player cap so subs can fill the rest later.
- Round matchups use a standard round-robin **circle method** for League, so
  it's a genuine mini-league, not a random bracket.
- The 5-minute League auto-close timer starts on the *first* person readying
  up for League, not from when the panel was posted.

"""
Turns a popped queue (list of player dicts: discord_id, mmr, is_captain,
is_na) into balanced teams.

Algorithm:
1. If there are any NA-whitelisted players, build dedicated NA team(s)
   first - one team's worth of NA players, and if there are more than
   that, a SECOND dedicated NA team too, so more of them actually get to
   play together rather than sitting out. Each NA team is captained by an
   NA-whitelisted captain if one exists within that group, otherwise its
   highest-MMR player. Only genuine overflow beyond what two teams can
   hold falls back to the Bench. This happens BEFORE normal captain
   selection specifically so an NA player can never accidentally end up
   captaining an unrelated team by MMR-tiebreak luck.
2. The remaining teams get captains chosen normally (whitelisted first,
   highest MMR fallback) from whoever's left.
3. Everyone still unassigned is sorted by MMR descending and dealt out in
   a "snake" order (1,2,3,4,4,3,2,1,...) across ALL teams (including any
   leftover seats on the NA team) so total MMR per team stays as close as
   possible.
4. Anything beyond TEAM_SIZE per team (shouldn't normally happen given the
   queue caps, but kept for safety) overflows to the bench.
"""

import random
import config


def build_teams(queued_players: list, num_teams: int, initial_team_size: int = None):
    """
    queued_players: list of dicts {discord_id, display_name, mmr, is_captain, is_na}
    initial_team_size: how many starters to seat per team right now (defaults
        to config.TEAM_SIZE). Force-started sessions pass a smaller number
        (e.g. 4 for a 16-player league force start) - the team VOICE
        CHANNELS are still created at the normal config.TEAM_SIZE cap so
        later subs from the bench can fill the remaining seats.
    Returns: list of team dicts:
        {captain_id, members: [discord_id...], bench: [discord_id...]}
    """
    if initial_team_size is None:
        initial_team_size = config.TEAM_SIZE
    pool = list(queued_players)
    teams = []

    # --- 1. build dedicated NA team(s) first, if any NA players exist ------
    # Fill one team; if there are more NA players than that holds, fill a
    # SECOND dedicated NA team too rather than benching the extras, so more
    # of them actually get to play together. Only genuine overflow beyond
    # what two teams can hold falls back to the Bench.
    na_players = [p for p in pool if p.get("is_na")]
    if na_players:
        na_players.sort(key=lambda p: -p["mmr"])
        for p in na_players:
            pool.remove(p)

        na_chunks = [na_players[:initial_team_size], na_players[initial_team_size:initial_team_size * 2]]
        na_overflow = na_players[initial_team_size * 2:]

        for chunk in na_chunks:
            if not chunk:
                continue
            chunk_whitelisted = sorted([p for p in chunk if p["is_captain"]], key=lambda p: -p["mmr"])
            captain = chunk_whitelisted[0] if chunk_whitelisted else chunk[0]
            members = [captain] + [p for p in chunk if p is not captain]
            teams.append({"captain": captain, "members": members, "bench": []})

        if na_overflow and teams:
            # extremely rare: more NA players than 2 teams can hold between
            # them - the rest wait on the Bench rather than a third team
            teams[0]["bench"].extend(na_overflow)

    remaining_teams_needed = num_teams - len(teams)

    # --- 2. choose captains for the remaining teams -------------------------
    whitelisted = sorted([p for p in pool if p["is_captain"]], key=lambda p: -p["mmr"])
    chosen_captains = whitelisted[:remaining_teams_needed]

    if len(chosen_captains) < remaining_teams_needed:
        remaining_pool = [p for p in pool if p not in chosen_captains]
        remaining_pool.sort(key=lambda p: -p["mmr"])
        chosen_captains += remaining_pool[: remaining_teams_needed - len(chosen_captains)]

    for c in chosen_captains:
        pool.remove(c)
    for c in chosen_captains:
        teams.append({"captain": c, "members": [c], "bench": []})

    # --- 3. snake draft everyone else across ALL teams ----------------------
    pool.sort(key=lambda p: -p["mmr"])

    order = list(range(len(teams)))
    idx = 0
    direction = 1
    for player in pool:
        team = teams[order[idx]]
        if len(team["members"]) < initial_team_size:
            team["members"].append(player)
        else:
            team["bench"].append(player)

        idx += direction
        if idx == len(teams):
            idx = len(teams) - 1
            direction = -1
        elif idx < 0:
            idx = 0
            direction = 1

    return teams


def pick_random_club_names(available_clubs: list, num_teams: int):
    """Randomly assign distinct club names to each team, if enough exist."""
    pool = list(available_clubs)
    random.shuffle(pool)
    if len(pool) >= num_teams:
        return pool[:num_teams]
    # Not enough stored clubs - pad with generic placeholders
    names = pool[:]
    while len(names) < num_teams:
        names.append(f"Team {len(names) + 1}")
    return names


def generate_round_robin(team_ids: list, double_round: bool = False):
    """
    Circle-method round robin: every team plays every other team exactly
    once. For 2 teams -> 1 round of 1 match. For 4 teams -> 3 rounds of 2
    concurrent matches each (every team plays 3 games total), matching the
    "each team plays 3 times" league format.

    double_round=True repeats the whole fixture list as a second leg, so
    every team faces each opponent twice in total instead of once - used
    for Rivals, where a single match wouldn't otherwise give a real
    "best of two" session between the two teams.

    Returns: list of rounds, each a list of (team_a_id, team_b_id) tuples.
    """
    teams = list(team_ids)
    bye = None
    if len(teams) % 2 == 1:
        teams.append(bye)

    n = len(teams)
    rounds = []
    for _ in range(n - 1):
        pairs = []
        for i in range(n // 2):
            a, b = teams[i], teams[n - 1 - i]
            if a is not None and b is not None:
                pairs.append((a, b))
        rounds.append(pairs)
        teams.insert(1, teams.pop())

    if double_round:
        rounds = rounds + [list(pairs) for pairs in rounds]

    return rounds

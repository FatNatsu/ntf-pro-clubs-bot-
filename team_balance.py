"""
Turns a popped queue (list of player dicts: discord_id, mmr, is_captain)
into balanced teams.

Algorithm:
1. Pick one captain per team from whoever in the queue is captain-whitelisted
   (highest MMR captains get priority, so captains are themselves roughly
   matched too). If there aren't enough whitelisted captains in the queue,
   the highest-MMR remaining players fill in as captain instead.
2. Remaining players are sorted by MMR descending and dealt out in a
   "snake" order (1,2,3,4,4,3,2,1,...) across the teams so total MMR per
   team stays as close as possible.
3. Anything beyond TEAM_SIZE per team (shouldn't normally happen given the
   queue caps, but kept for safety) overflows to the bench.
"""

import random
import config


def build_teams(queued_players: list, num_teams: int, initial_team_size: int = None):
    """
    queued_players: list of dicts {discord_id, display_name, mmr, is_captain}
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

    # --- 1. choose captains -------------------------------------------------
    whitelisted = sorted([p for p in pool if p["is_captain"]], key=lambda p: -p["mmr"])
    chosen_captains = whitelisted[:num_teams]

    if len(chosen_captains) < num_teams:
        remaining_pool = [p for p in pool if p not in chosen_captains]
        remaining_pool.sort(key=lambda p: -p["mmr"])
        chosen_captains += remaining_pool[: num_teams - len(chosen_captains)]

    for c in chosen_captains:
        pool.remove(c)

    teams = [{"captain": c, "members": [c], "bench": []} for c in chosen_captains]

    # --- 2. snake draft the rest --------------------------------------------
    pool.sort(key=lambda p: -p["mmr"])

    order = list(range(num_teams))
    idx = 0
    direction = 1
    for player in pool:
        team = teams[order[idx]]
        if len([m for m in team["members"]]) < initial_team_size:
            team["members"].append(player)
        else:
            team["bench"].append(player)

        idx += direction
        if idx == num_teams:
            idx = num_teams - 1
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


def generate_round_robin(team_ids: list):
    """
    Circle-method round robin: every team plays every other team exactly
    once. For 2 teams -> 1 round of 1 match. For 4 teams -> 3 rounds of 2
    concurrent matches each (every team plays 3 games total), matching the
    "each team plays 3 times" league format.
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
    return rounds

"""
Turns a popped queue (list of player dicts: discord_id, mmr, is_captain,
is_na) into balanced teams.

Algorithm:
1. Pick one captain per team from whoever in the queue is captain-whitelisted
   (highest MMR captains get priority, so captains are themselves roughly
   matched too). If there aren't enough whitelisted captains in the queue,
   the highest-MMR remaining players fill in as captain instead.
2. Cluster NA-whitelisted players onto as FEW teams as possible (filling one
   team's remaining seats before moving to the next), so NA players end up
   playing together rather than scattered - helps with ping/game flow.
   Anyone who doesn't fit (all teams already full of NA players) falls back
   into the normal pool below.
3. Everyone else is sorted by MMR descending and dealt out in a "snake"
   order (1,2,3,4,4,3,2,1,...) across the teams so total MMR per team stays
   as close as possible.
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

    # --- 2. cluster NA players onto as few teams as possible ----------------
    na_players = [p for p in pool if p.get("is_na")]
    na_players.sort(key=lambda p: -p["mmr"])
    for p in na_players:
        pool.remove(p)

    team_idx = 0
    leftover_na = []
    for p in na_players:
        while team_idx < num_teams and len(teams[team_idx]["members"]) >= initial_team_size:
            team_idx += 1
        if team_idx >= num_teams:
            leftover_na.append(p)  # every team is already full of NA players
            continue
        teams[team_idx]["members"].append(p)

    # anyone who didn't fit rejoins the general pool so they still get
    # seated normally via the snake draft below, just not clustered
    pool = leftover_na + pool

    # --- 3. snake draft the rest --------------------------------------------
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

"""
Elo-style MMR maths, applied per-team-match.
Each player on the winning team is treated as "beating" the average MMR of
the opposing team, and vice versa - standard team-Elo approximation.

Only individual players carry MMR - teams/clubs never do (club stats are
plain win/loss records, tracked separately in database.py).

The K-factor (how much a single result moves your MMR) tapers down as a
player approaches the top of the ladder, so once you're A rank or higher
your rating moves in smaller steps - wins and losses matter less
individually the closer you are to S rank, which keeps the top of the
leaderboard stable instead of swinging wildly on one game.
"""

import config


def expected_score(rating_a: float, rating_b: float) -> float:
    return 1 / (1 + 10 ** ((rating_b - rating_a) / 400))


def _a_tier_floor():
    return dict(config.RANK_THRESHOLDS)["A"]


def _s_tier_floor():
    return dict(config.RANK_THRESHOLDS)["S"]


def k_factor_for_rating(rating: int) -> int:
    if rating >= _s_tier_floor():
        return config.K_FACTOR_S_TIER
    if rating >= _a_tier_floor():
        return config.K_FACTOR_A_TIER
    return config.K_FACTOR_DEFAULT


def new_rating(rating: int, expected: float, actual: float) -> int:
    k = k_factor_for_rating(rating)
    return round(rating + k * (actual - expected))


def rank_for_mmr(mmr: int) -> str:
    rank = config.RANK_THRESHOLDS[0][0]
    for letter, floor in config.RANK_THRESHOLDS:
        if mmr >= floor:
            rank = letter
        else:
            break
    return rank


def apply_match_result(team_a_players: list, team_b_players: list, a_won: bool):
    """
    team_a_players / team_b_players: list of dicts with 'discord_id' and 'mmr'.
    Returns dict {discord_id: new_mmr} for every player involved.
    Each player's own current rating decides their own K-factor - two players
    on the same team can move by different amounts if one is A-rank and the
    other isn't.
    """
    if not team_a_players or not team_b_players:
        return {}

    avg_a = sum(p["mmr"] for p in team_a_players) / len(team_a_players)
    avg_b = sum(p["mmr"] for p in team_b_players) / len(team_b_players)

    exp_a = expected_score(avg_a, avg_b)
    exp_b = 1 - exp_a

    actual_a = 1.0 if a_won else 0.0
    actual_b = 1.0 - actual_a

    results = {}
    for p in team_a_players:
        results[p["discord_id"]] = new_rating(p["mmr"], exp_a, actual_a)
    for p in team_b_players:
        results[p["discord_id"]] = new_rating(p["mmr"], exp_b, actual_b)
    return results

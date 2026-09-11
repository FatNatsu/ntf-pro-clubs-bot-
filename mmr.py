"""
Elo-style MMR maths, applied per-team-match.
Each player on the winning team is treated as "beating" the average MMR of
the opposing team, and vice versa - standard team-Elo approximation.

Only individual players carry MMR - teams/clubs never do (club stats are
plain win/loss records, tracked separately in database.py).

The K-factor (how much a single result moves your MMR) is looked up per
rank from config.K_FACTORS, and splits by outcome at every tier: wins taper
down steadily from G (the most generous, to help new/low players climb
fast) all the way to S+ (the smallest), while losses stay flat at full
strength through B and only ease off gradually from A upward - and even
then never drop below that tier's win value. So climbing out of the lower
ranks is comparatively easy, while holding onto a high rank gets
progressively harder, not easier.
"""

import config


def expected_score(rating_a: float, rating_b: float) -> float:
    return 1 / (1 + 10 ** ((rating_b - rating_a) / 400))


def k_factor_for_rating(rating: int, won: bool) -> int:
    """Looks up the win/loss K-factor for whichever rank this rating falls
    into - see config.K_FACTORS for the full table and the reasoning
    behind each tier's numbers."""
    rank = rank_for_mmr(rating)
    outcome = "win" if won else "loss"
    return config.K_FACTORS[rank][outcome]


def new_rating(rating: int, expected: float, actual: float) -> int:
    won = actual >= 1.0
    k = k_factor_for_rating(rating, won)
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

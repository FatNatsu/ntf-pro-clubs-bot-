"""
Flat win/loss MMR - every result moves a player's rating by their OWN
rank's full win or loss K-factor, regardless of how strong the opponent
was. No Elo-style expected-value scaling against the opposing team's
average MMR - a win is always worth the same amount for a given player at
a given rating, a loss always costs the same amount.

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


def simulate_correction(current_mmr: int, count: int, kind: str, sign: int) -> int:
    """Simulates `count` sequential wins or losses starting from
    current_mmr, using each step's own rank-based K-factor (since crossing
    a tier partway through changes it) - used when an admin manually
    adds/deducts wins or losses, so the MMR moves by whatever that many
    results would actually have caused.
    kind: "win" or "loss" - which K-factor column to use.
    sign: +1 to increase MMR each step, -1 to decrease it each step.
    """
    rating = current_mmr
    for _ in range(count):
        rank = rank_for_mmr(rating)
        k = config.K_FACTORS[rank][kind]
        rating = rating + sign * k
    return max(0, rating)


def k_factor_for_rating(rating: int, won: bool) -> int:
    """Looks up the win/loss K-factor for whichever rank this rating falls
    into - see config.K_FACTORS for the full table and the reasoning
    behind each tier's numbers. This IS the flat amount a result is worth,
    not scaled by anything else."""
    rank = rank_for_mmr(rating)
    outcome = "win" if won else "loss"
    return config.K_FACTORS[rank][outcome]


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
    Flat application: each player gains/loses their OWN rank's full K-factor
    for the result, regardless of the opponent's MMR - two players on the
    same team can still move by different amounts if one is a different
    rank than the other, but neither is affected by who they played against.
    """
    results = {}
    for p in team_a_players:
        k = k_factor_for_rating(p["mmr"], a_won)
        results[p["discord_id"]] = max(0, p["mmr"] + k if a_won else p["mmr"] - k)
    for p in team_b_players:
        b_won = not a_won
        k = k_factor_for_rating(p["mmr"], b_won)
        results[p["discord_id"]] = max(0, p["mmr"] + k if b_won else p["mmr"] - k)
    return results

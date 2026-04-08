from app.services.stats import get_hitting_stats, get_pitching_stats, blend_hitting, blend_pitching

# Stat weights for offensive scoring
HIT_WEIGHTS = {
    "obp": 0.35,
    "slg": 0.30,
    "avg": 0.15,
    "bb_pct": 0.10,
    "k_pct": -0.10  # negative — strikeouts hurt
}

# Stat weights for pitching scoring
PITCH_WEIGHTS = {
    "era": -0.40,   # negative — lower is better
    "whip": -0.30,  # negative — lower is better
    "k9": 0.20,
    "bb9": -0.10    # negative — lower is better
}

# Era normalization ranges (for scaling to 0-1)
HIT_RANGES = {
    "obp": (0.280, 0.420),
    "slg": (0.350, 0.550),
    "avg": (0.220, 0.320),
    "bb_pct": (0.04, 0.16),
    "k_pct": (0.10, 0.35)
}

PITCH_RANGES = {
    "era": (2.0, 6.0),
    "whip": (0.90, 1.60),
    "k9": (5.0, 13.0),
    "bb9": (1.5, 5.0)
}

def normalize(value, low, high):
    if high == low:
        return 0.5
    return max(0, min(1, (value - low) / (high - low)))

def score_lineup(roster, trades=None):
    trades = trades or []
    total_score = 0
    scored_players = 0

    hitters = [p for p in roster if p["position_type"] != "Pitcher" and p["status"] == "Active"]

    for player in hitters:
        stats = get_hitting_stats(player["id"])
        blended = blend_hitting(stats)
        if not blended:
            continue

        score = 0
        for stat, weight in HIT_WEIGHTS.items():
            low, high = HIT_RANGES[stat]
            normalized = normalize(blended[stat], low, high)
            score += normalized * weight

        total_score += score
        scored_players += 1

    return total_score / max(scored_players, 1)

def score_pitcher(player_id):
    stats = get_pitching_stats(player_id)
    blended = blend_pitching(stats)
    if not blended:
        return 0.5

    score = 0
    for stat, weight in PITCH_WEIGHTS.items():
        low, high = PITCH_RANGES[stat]
        normalized = normalize(blended[stat], low, high)
        score += normalized * weight

    return score

def predict_game(home_roster, away_roster, home_pitcher_id, away_pitcher_id):
    home_hit = score_lineup(home_roster)
    away_hit = score_lineup(away_roster)
    home_pitch = score_pitcher(home_pitcher_id)
    away_pitch = score_pitcher(away_pitcher_id)

    # Shift pitcher scores to positive range (they can be negative due to negative weights)
    home_pitch_adj = home_pitch + 0.5
    away_pitch_adj = away_pitch + 0.5

    # Combine offense vs opposing pitcher
    home_score = (home_hit * 0.55) + (home_pitch_adj * 0.45)
    away_score = (away_hit * 0.55) + (away_pitch_adj * 0.45)

    # Normalize to percentages
    total = home_score + away_score
    if total == 0:
        return {"home_win_pct": 50, "away_win_pct": 50}

    home_win_pct = round((home_score / total) * 100, 1)
    away_win_pct = round(100 - home_win_pct, 1)

    return {
        "home_win_pct": home_win_pct,
        "away_win_pct": away_win_pct,
        "home_offense_score": round(home_hit, 4),
        "away_offense_score": round(away_hit, 4),
        "home_pitcher_score": round(home_pitch, 4),
        "away_pitcher_score": round(away_pitch, 4)
    }
from app.services.stats import get_hitting_stats, get_pitching_stats, get_hitting_splits, get_batter_vs_pitcher, get_bullpen_era, get_recent_form, blend_hitting, blend_pitching
import json
import os

WEIGHTS_FILE = "data/weights.json"

DEFAULT_HIT_WEIGHTS = {
    "obp": 0.25,
    "slg": 0.20,
    "woba": 0.20,
    "avg": 0.10,
    "iso": 0.10,
    "bb_pct": 0.08,
    "k_pct": -0.07,
    "babip": 0.02
}

DEFAULT_PITCH_WEIGHTS = {
    "fip": -0.30,
    "era": -0.20,
    "whip": -0.20,
    "k9": 0.15,
    "k_bb_pct": 0.10,
    "bb9": -0.05,
    "gb_pct": 0.00
}

DEFAULT_BALANCE = {
    "offense_weight": 0.50,
    "pitching_weight": 0.35,
    "bullpen_weight": 0.08,
    "recent_form_weight": 0.05,
    "bvp_weight": 0.15,
    "park_factor_weight": 0.05
}

HIT_RANGES = {
    "obp": (0.280, 0.420),
    "slg": (0.350, 0.550),
    "avg": (0.220, 0.320),
    "woba": (0.270, 0.420),
    "iso": (0.080, 0.280),
    "babip": (0.250, 0.380),
    "bb_pct": (0.04, 0.16),
    "k_pct": (0.10, 0.35)
}

PITCH_RANGES = {
    "era": (2.0, 6.0),
    "whip": (0.90, 1.60),
    "k9": (5.0, 13.0),
    "bb9": (1.5, 5.0),
    "fip": (2.5, 5.5),
    "k_bb_pct": (-0.05, 0.25),
    "gb_pct": (0.5, 3.0)
}

# Park factors (1.0 = neutral, >1.0 = hitter friendly, <1.0 = pitcher friendly)
PARK_FACTORS = {
    121: 1.02,  # Citi Field (NYM) - slight pitcher park
    147: 1.05,  # Yankee Stadium - hitter friendly
    119: 1.08,  # Dodger Stadium - slight hitter
    144: 0.95,  # Truist Park (ATL) - slight pitcher
    111: 1.03,  # Fenway - hitter friendly
    112: 0.94,  # Wrigley - pitcher friendly
    145: 0.97,  # Guaranteed Rate
    113: 1.04,  # Great American Ball Park - very hitter friendly
    114: 0.98,  # Progressive Field
    115: 1.12,  # Coors Field - most hitter friendly
    116: 0.97,  # Comerica Park
    117: 0.98,  # Minute Maid
    118: 0.99,  # Kauffman Stadium
    108: 1.01,  # Angel Stadium
    146: 0.97,  # loanDepot park
    158: 0.98,  # American Family Field
    142: 0.99,  # Target Field
    133: 1.00,  # Oakland Coliseum
    143: 1.02,  # Citizens Bank Park
    134: 0.97,  # PNC Park
    135: 0.96,  # Petco Park
    137: 0.95,  # Oracle Park
    136: 0.97,  # T-Mobile Park
    138: 0.98,  # Busch Stadium
    139: 0.96,  # Tropicana Field
    140: 1.02,  # Globe Life Field
    141: 1.01,  # Rogers Centre
    120: 1.00,  # Nationals Park
    110: 0.99,  # Camden Yards
    109: 1.03,  # Chase Field
}

def load_weights():
    if os.path.exists(WEIGHTS_FILE):
        with open(WEIGHTS_FILE, "r") as f:
            return json.load(f)
    return {
        "hit_weights": DEFAULT_HIT_WEIGHTS,
        "pitch_weights": DEFAULT_PITCH_WEIGHTS,
        "balance": DEFAULT_BALANCE
    }

def save_weights(weights):
    with open(WEIGHTS_FILE, "w") as f:
        json.dump(weights, f, indent=2)

def normalize(value, low, high):
    if high == low:
        return 0.5
    return max(0, min(1, (value - low) / (high - low)))

def score_lineup(roster, pitcher_id=0, weights=None, split="home"):
    if weights is None:
        weights = load_weights()
    hit_weights = weights["hit_weights"]

    total_score = 0
    scored_players = 0
    hitters = [p for p in roster if p["position_type"] != "Pitcher" and p["status"] == "Active"]

    for player in hitters:
        # Try home/away split first, fall back to season stats
        split_stats = get_hitting_splits(player["id"], split)
        stats = split_stats if split_stats else get_hitting_stats(player["id"])
        blended = blend_hitting(stats)
        if not blended:
            continue

        score = 0
        for stat, weight in hit_weights.items():
            if stat not in HIT_RANGES:
                continue
            low, high = HIT_RANGES[stat]
            normalized = normalize(blended[stat], low, high)
            score += normalized * weight

        # Batter vs pitcher history bonus
        if pitcher_id:
            bvp = get_batter_vs_pitcher(player["id"], pitcher_id)
            if bvp:
                bvp_score = (normalize(bvp["obp"], 0.250, 0.500) * 0.5 +
                             normalize(bvp["slg"], 0.300, 0.700) * 0.5)
                bvp_w = weights["balance"].get("bvp_weight", 0.15)
                score = score * (1 - bvp_w) + bvp_score * bvp_w

        total_score += score
        scored_players += 1

    return total_score / max(scored_players, 1)

def score_pitcher(player_id, weights=None):
    if not player_id:
        return 0
    if weights is None:
        weights = load_weights()
    pitch_weights = weights["pitch_weights"]

    stats = get_pitching_stats(player_id)
    blended = blend_pitching(stats)
    if not blended:
        return 0

    score = 0
    for stat, weight in pitch_weights.items():
        if stat not in PITCH_RANGES:
            continue
        low, high = PITCH_RANGES[stat]
        normalized = normalize(blended[stat], low, high)
        score += normalized * weight

    return score

def predict_game(home_roster, away_roster, home_pitcher_id, away_pitcher_id, home_team_id=0, away_team_id=0):
    weights = load_weights()
    balance = weights["balance"]

    # Score lineups with home/away splits and batter vs pitcher history
    home_hit = score_lineup(home_roster, away_pitcher_id, weights, split="home")
    away_hit = score_lineup(away_roster, home_pitcher_id, weights, split="away")
    home_pitch = score_pitcher(home_pitcher_id, weights)
    away_pitch = score_pitcher(away_pitcher_id, weights)

    # Bullpen ERA (lower is better, normalize 2.5-5.5)
    home_bullpen_era = get_bullpen_era(home_team_id) if home_team_id else 4.20
    away_bullpen_era = get_bullpen_era(away_team_id) if away_team_id else 4.20
    home_bullpen_score = 1 - normalize(home_bullpen_era, 2.5, 5.5)
    away_bullpen_score = 1 - normalize(away_bullpen_era, 2.5, 5.5)

    # Recent form (win_pct over last 10)
    home_form = get_recent_form(home_team_id)["win_pct"] if home_team_id else 0.5
    away_form = get_recent_form(away_team_id)["win_pct"] if away_team_id else 0.5

    # Park factor
    home_park = PARK_FACTORS.get(home_team_id, 1.0)

    home_pitch_adj = home_pitch + 0.5
    away_pitch_adj = away_pitch + 0.5

    off_w = balance.get("offense_weight", 0.50)
    pit_w = balance.get("pitching_weight", 0.35)
    bull_w = balance.get("bullpen_weight", 0.08)
    form_w = balance.get("recent_form_weight", 0.05)
    pf_w = balance.get("park_factor_weight", 0.05)

    home_score = (home_hit * off_w) + (home_pitch_adj * pit_w) + (home_bullpen_score * bull_w) + (home_form * form_w)
    away_score = (away_hit * off_w) + (away_pitch_adj * pit_w) + (away_bullpen_score * bull_w) + (away_form * form_w)

    # Apply park factor
    home_score = home_score * (1 + (home_park - 1) * pf_w * 10)
    away_score = away_score * (1 - (home_park - 1) * pf_w * 5)

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
        "away_pitcher_score": round(away_pitch, 4),
        "home_bullpen_era": home_bullpen_era,
        "away_bullpen_era": away_bullpen_era,
        "home_form": home_form,
        "away_form": away_form,
        "park_factor": home_park
    }
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

@router.post("/predict")
async def predict(request: Request):
    try:
        payload = await request.json()
        home_team = payload["home_team"]
        away_team = payload["away_team"]
        game_date = payload.get("game_date", None)

        home_result = get_roster(home_team)
        away_result = get_roster(away_team)

        if "error" in home_result:
            return JSONResponse({"error": f"Could not find roster for {home_team}"}, status_code=400)
        if "error" in away_result:
            return JSONResponse({"error": f"Could not find roster for {away_team}"}, status_code=400)

        home_roster = home_result["roster"]
        away_roster = away_result["roster"]

        home_pitcher_id = 0
        away_pitcher_id = 0
        schedule = get_schedule(home_team, game_date)
        for game in schedule.get("games", []):
            opp_code = game.get("opponent_code", "")
            if opp_code == away_team or away_team in game.get("opponent", ""):
                our_pitcher_name = game.get("our_probable_pitcher", "")
                opp_pitcher_name = game.get("opponent_probable_pitcher", "")
                for p in home_roster:
                    if p["name"] == our_pitcher_name:
                        home_pitcher_id = p["id"]
                for p in away_roster:
                    if p["name"] == opp_pitcher_name:
                        away_pitcher_id = p["id"]

        print(f"Predicting: {home_team} vs {away_team} on {game_date}")
        print(f"Home pitcher: {home_pitcher_id}, Away pitcher: {away_pitcher_id}")

        return predict_game(home_roster, away_roster, home_pitcher_id, away_pitcher_id)
    except Exception as e:
        import traceback
        print("PREDICT ERROR:", traceback.format_exc())
        return JSONResponse({"error": str(e)}, status_code=500)
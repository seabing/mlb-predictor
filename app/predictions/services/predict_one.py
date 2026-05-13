"""Single-game prediction orchestration.

Glues together: roster lookup → boxscore lineup (if game_id) → manual
lineup overrides → fallback pitcher lookup from schedule → engine → log.

This is the function /predict, /predict/today, the auto-predict scheduler,
and the backfill tool all call into.
"""
from __future__ import annotations

from app.mlb.client import client as mlb_client
from app.predictions.services.confidence import confidence_calculator
from app.predictions.services.engine import prediction_engine
from app.predictions.services.tracking import prediction_store
from app.predictions.services.weights import weights_store


def predict_one_game(
    home_team: str,
    away_team: str,
    game_id: int | None = None,
    game_date: str | None = None,
    manual_home_lineup: list[dict] | None = None,
    manual_away_lineup: list[dict] | None = None,
    log: bool = True,
    force_replace: bool = False,
) -> dict:
    """Run one prediction. Returns the dict shape used by the API."""
    home_result = mlb_client.get_roster(home_team)
    away_result = mlb_client.get_roster(away_team)
    if "error" in home_result:
        return {"error": f"Could not find roster for {home_team}"}
    if "error" in away_result:
        return {"error": f"Could not find roster for {away_team}"}

    home_roster = home_result["roster"]
    away_roster = away_result["roster"]
    home_pitcher_id = 0
    away_pitcher_id = 0
    lineup_source = "roster"

    # Prefer the actual played lineup from the boxscore if game_id is known
    if game_id:
        lineup_data = mlb_client.get_lineup(game_id)
        home_lineup = lineup_data.get("home", {})
        away_lineup = lineup_data.get("away", {})
        if home_lineup.get("lineup_available") and away_lineup.get("lineup_available"):
            lineup_source = "mlb_api"
            home_pitcher_id = home_lineup.get("starter_id", 0)
            away_pitcher_id = away_lineup.get("starter_id", 0)
            home_ids = {p["id"] for p in home_lineup["lineup"]}
            away_ids = {p["id"] for p in away_lineup["lineup"]}
            home_roster = [p for p in home_roster if p["id"] in home_ids]
            away_roster = [p for p in away_roster if p["id"] in away_ids]

    # Manual override (Matchup tab "edit lineup" flow)
    if manual_home_lineup:
        lineup_source = "manual"
        home_roster = manual_home_lineup
    if manual_away_lineup:
        lineup_source = "manual"
        away_roster = manual_away_lineup

    # If we still don't have starters, try to read them off the day's schedule
    if not home_pitcher_id or not away_pitcher_id:
        sched = mlb_client.get_schedule(home_team, game_date)
        for g in sched.get("games", []):
            opp_code = g.get("opponent_code", "")
            if opp_code == away_team or away_team in g.get("opponent", ""):
                our_pn = g.get("our_probable_pitcher", "")
                opp_pn = g.get("opponent_probable_pitcher", "")
                for p in home_result["roster"]:
                    if p["name"] == our_pn:
                        home_pitcher_id = p["id"]
                for p in away_result["roster"]:
                    if p["name"] == opp_pn:
                        away_pitcher_id = p["id"]

    print(f"Predicting: {away_team} @ {home_team} | Source: {lineup_source}")
    home_team_id = home_result.get("team_id", 0)
    away_team_id = away_result.get("team_id", 0)
    result = prediction_engine.predict_game(
        home_roster, away_roster,
        home_pitcher_id, away_pitcher_id,
        home_team_id, away_team_id,
    )
    result["lineup_source"] = lineup_source
    confidence_calculator.annotate(
        result,
        lineup_source=lineup_source,
        home_team=home_team,
        away_team=away_team,
        home_pitcher_id=home_pitcher_id,
        away_pitcher_id=away_pitcher_id,
    )

    log_id = None
    if log and game_id:
        try:
            log_id = prediction_store.log(
                home_team=home_team,
                away_team=away_team,
                prediction=result,
                game_id=game_id,
                game_date=game_date,
                home_pitcher_id=home_pitcher_id,
                away_pitcher_id=away_pitcher_id,
                weights=weights_store.load(),
                force_replace=force_replace,
            )
        except Exception as e:
            print(f"Failed to log prediction: {e}")
    result["prediction_id"] = log_id
    return result

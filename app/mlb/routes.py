"""Routes that surface MLB Stats API data: roster, schedule, lineup, etc."""
from __future__ import annotations

from fastapi import APIRouter

from app.mlb.client import client
from app.mlb.stats import stats_service

router = APIRouter()


@router.get("/roster/{team_code}")
def roster(team_code: str):
    result = client.get_roster(team_code)
    if "error" in result:
        return result
    # Salary enrichment — degrade silently on failure
    try:
        from app.salaries.services.spotrac import spotrac
        spotrac.enrich_roster(team_code, result.get("roster", []))
    except Exception as e:
        print(f"[roster] salary enrichment failed: {e}")
    return result


@router.get("/schedule/{team_code}")
def schedule(team_code: str, date: str | None = None):
    return client.get_schedule(team_code, date)


@router.get("/upcoming/{team_code}")
def upcoming(team_code: str, days: int = 7):
    return client.get_upcoming(team_code, days)


@router.get("/lineup/{game_id}")
def lineup(game_id: int):
    return client.get_lineup(game_id)


@router.get("/last-lineup/{team_code}")
def last_lineup(team_code: str):
    return client.get_last_lineup(team_code)


@router.get("/hitters/{team_code}")
def hitters(team_code: str):
    result = client.get_roster(team_code)
    if "error" in result:
        return result
    hitters_list = [
        p for p in result["roster"]
        if p["position_type"] != "Pitcher" and p["status"] == "Active"
    ]
    return {"team": team_code, "hitters": hitters_list}


@router.get("/bullpen/{team_code}")
def bullpen(team_code: str):
    result = client.get_roster(team_code)
    if "error" in result:
        return result
    era = stats_service.get_bullpen_era(result["team_id"])
    return {"team": team_code, "bullpen_era": era}


@router.get("/form/{team_code}")
def form(team_code: str):
    result = client.get_roster(team_code)
    if "error" in result:
        return result
    return stats_service.get_recent_form(result["team_id"])

"""Salary status + admin routes."""
from __future__ import annotations

from fastapi import APIRouter

from app.salaries.services.spotrac import spotrac

router = APIRouter()


@router.get("/salaries/status")
def salaries_status():
    return spotrac.cache_status()


@router.post("/salaries/refresh/{team_code}")
def salaries_refresh(team_code: str):
    players = spotrac.fetch_team_salaries(team_code, force=True)
    return {"team": team_code.upper(), "player_count": len(players)}


@router.get("/salaries/debug/{team_code}")
def salaries_debug(team_code: str):
    return spotrac.debug_team(team_code)


@router.post("/salaries/clear-cache")
def salaries_clear():
    return spotrac.clear_cache()

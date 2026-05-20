"""HTTP routes for Jonah (Phase 2 front-office tool).

Feature 1 — natural-language roster moves:
  POST /api/jonah/roster-move  body: {"team": "NYY", "message": "..."}
    -> interpret the move (Claude) -> resolve players -> apply to roster ->
       rerun the team rating -> return before/after win probability + reasoning.

  GET /api/jonah/team/{code}/rating  -> the baseline team rating (handy for the
       UI and for debugging the engine integration).
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.llm import LLMError
from app.jonah.services import nl_moves as _nl
from app.jonah.services import player_resolver as _resolver
from app.jonah.services import roster_ops as _ops
from app.jonah.services import team_rating as _rating
from app.jonah.services import trade_sim as _trade
from app.mlb.client import client as mlb_client
from app.salaries.services.spotrac import spotrac

router = APIRouter()


@router.get("/jonah/team/{code}/rating")
async def team_rating(code: str):
    """Baseline win-probability rating for a team's current roster."""
    roster_data = await asyncio.to_thread(mlb_client.get_roster, code)
    if "error" in roster_data:
        return JSONResponse({"error": roster_data["error"]}, status_code=404)
    rating = await asyncio.to_thread(
        _rating.rate_team, roster_data["roster"], roster_data.get("team_id", 0)
    )
    return {"team": roster_data["team"], "rating": rating}


@router.get("/jonah/roster-with-salaries/{code}")
async def roster_with_salaries(code: str):
    """Roster list enriched with salary/contract data — feeds the trade builder."""
    roster_data = await asyncio.to_thread(mlb_client.get_roster, code)
    if "error" in roster_data:
        return JSONResponse({"error": roster_data["error"]}, status_code=404)
    enriched = await asyncio.to_thread(
        spotrac.enrich_roster, roster_data["team"], roster_data["roster"]
    )
    players = [
        {
            "id": p.get("id"),
            "name": p.get("name"),
            "position": p.get("position"),
            "position_type": p.get("position_type"),
            "status": p.get("status"),
            "salary": p.get("salary"),
            "years_left": p.get("years_left"),
        }
        for p in enriched
    ]
    return {"team": roster_data["team"], "players": players}


@router.post("/jonah/trade-sim")
async def trade_sim(request: Request):
    """Simulate a two-team trade; return win-probability + salary impact for both."""
    payload = await request.json() if (await request.body()) else {}
    team_a = (payload.get("team_a") or "").strip()
    team_b = (payload.get("team_b") or "").strip()
    a_sends = [int(x) for x in (payload.get("team_a_sends") or [])]
    b_sends = [int(x) for x in (payload.get("team_b_sends") or [])]

    if not team_a or not team_b:
        return JSONResponse({"error": "Both teams are required."}, status_code=400)
    if team_a.upper() == team_b.upper():
        return JSONResponse({"error": "Pick two different teams."}, status_code=400)

    result = await asyncio.to_thread(
        _trade.simulate_trade, team_a, a_sends, team_b, b_sends
    )
    if "error" in result:
        return JSONResponse({"error": result["error"]}, status_code=400)
    return result


@router.post("/jonah/roster-move")
async def roster_move(request: Request):
    """Run a natural-language roster move and return its win-probability impact."""
    payload = await request.json() if (await request.body()) else {}
    team = (payload.get("team") or "").strip()
    message = payload.get("message") or ""

    if not team:
        return JSONResponse({"error": "A team code is required (e.g. 'NYY')."}, status_code=400)

    return await asyncio.to_thread(_run_move, team, message)


def _run_move(team: str, message: str) -> dict | JSONResponse:
    roster_data = mlb_client.get_roster(team)
    if "error" in roster_data:
        return JSONResponse({"error": roster_data["error"]}, status_code=404)

    roster = roster_data["roster"]
    team_id = roster_data.get("team_id", 0)

    # 1. Baseline rating (before the move).
    before = _rating.rate_team(roster, team_id)

    # 2. Interpret the plain-English move with Claude.
    try:
        move = _nl.interpret_move(roster_data["team"], roster, message)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except LLMError as e:
        return JSONResponse({"error": str(e)}, status_code=502)

    # 3. Resolve players to acquire into real roster entries.
    resolved_adds: list[dict] = []
    unresolved: list[str] = []
    for add in move["adds"]:
        player = _resolver.resolve(add["name"])
        if player:
            player["acquired_reason"] = add.get("why", "")
            resolved_adds.append(player)
        else:
            unresolved.append(add["name"])

    drop_names = [d["name"] for d in move["drops"]]

    # 4. Apply the move to a copy of the roster.
    new_roster, report = _ops.apply_move(roster, adds=resolved_adds, drops=drop_names)

    # 5. Rerun the rating (after the move).
    after = _rating.rate_team(new_roster, team_id)

    delta = round(after["win_pct"] - before["win_pct"], 1)

    return {
        "team": roster_data["team"],
        "message": message,
        "before_win_pct": before["win_pct"],
        "after_win_pct": after["win_pct"],
        "delta": delta,
        "payroll_change_millions": move["payroll_change_millions"],
        "summary": move["summary"],
        "applied": {
            "added": report["added"],
            "dropped": report["dropped"],
            "drop_not_found": report["drop_not_found"],
            "unresolved_acquisitions": unresolved,
        },
        "detail": {"before": before, "after": after},
    }

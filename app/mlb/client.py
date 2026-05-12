"""Thin wrapper around the public MLB Stats API.

`MLBStatsClient` owns the base URL and a shared `requests.Session` so connection
pooling works across calls. All endpoints under https://statsapi.mlb.com/api/v1
that the app uses go through here — search for `.get(` in this file to see the
catalog.
"""
from __future__ import annotations

from datetime import date as _date, timedelta
from typing import Any

import requests

from app.mlb.teams import team_id


class MLBStatsClient:
    BASE_URL = "https://statsapi.mlb.com/api/v1"
    DEFAULT_TIMEOUT = 15

    def __init__(
        self,
        base_url: str | None = None,
        session: requests.Session | None = None,
        timeout: int | None = None,
    ) -> None:
        self.base_url = base_url or self.BASE_URL
        self.session = session or requests.Session()
        self.timeout = timeout or self.DEFAULT_TIMEOUT

    # ---- low-level ----

    def _get(self, path: str, **params: Any) -> dict:
        url = f"{self.base_url}{path}"
        resp = self.session.get(url, params=params or None, timeout=self.timeout)
        return resp.json()

    # ---- roster ----

    def get_roster(self, team_code: str) -> dict:
        tid = team_id(team_code)
        if not tid:
            return {"error": f"Team code '{team_code}' not found"}
        resp = self._get(f"/teams/{tid}/roster", rosterType="depthChart")
        seen: set[int] = set()
        players: list[dict] = []
        for raw in resp.get("roster", []):
            pid = raw["person"]["id"]
            if pid in seen:
                continue
            seen.add(pid)
            status_code = raw.get("status", {}).get("code", "A")
            players.append({
                "id": pid,
                "name": raw["person"]["fullName"],
                "jersey": raw.get("jerseyNumber", ""),
                "position": raw["position"]["abbreviation"],
                "position_type": raw["position"]["type"],
                "status": raw.get("status", {}).get("description", "Active"),
                "il": status_code != "A",
            })
        return {"team": team_code.upper(), "team_id": tid, "roster": players}

    # ---- schedule ----

    def get_schedule(self, team_code: str, on_date: str | None = None) -> dict:
        tid = team_id(team_code)
        if not tid:
            return {"error": f"Team code '{team_code}' not found"}
        d = on_date or _date.today().strftime("%Y-%m-%d")
        resp = self._get(
            "/schedule",
            sportId=1, teamId=tid, date=d, hydrate="team,probablePitcher",
        )
        return {
            "team": team_code.upper(),
            "date": d,
            "games": self._format_games_for_team(resp, tid, default_date=d),
        }

    def get_upcoming(self, team_code: str, days: int = 7) -> dict:
        tid = team_id(team_code)
        if not tid:
            return {"error": f"Team code '{team_code}' not found"}
        today = _date.today()
        start = today.strftime("%Y-%m-%d")
        end = (today + timedelta(days=days)).strftime("%Y-%m-%d")
        resp = self._get(
            "/schedule",
            sportId=1, teamId=tid, startDate=start, endDate=end,
            hydrate="team,probablePitcher",
        )
        return {
            "team": team_code.upper(),
            "start": start,
            "end": end,
            "games": self._format_games_for_team(resp, tid),
        }

    def _format_games_for_team(
        self,
        schedule_response: dict,
        team_id_: int,
        default_date: str | None = None,
    ) -> list[dict]:
        out: list[dict] = []
        for date_entry in schedule_response.get("dates", []):
            for game in date_entry.get("games", []):
                home = game["teams"]["home"]
                away = game["teams"]["away"]
                is_home = home["team"]["id"] == team_id_
                opponent = away["team"] if is_home else home["team"]
                our_side = home if is_home else away
                opp_side = away if is_home else home
                out.append({
                    "game_id": game["gamePk"],
                    "date": date_entry.get("date") or default_date,
                    "home_away": "Home" if is_home else "Away",
                    "opponent": opponent["name"],
                    "opponent_code": opponent.get("abbreviation", ""),
                    "our_probable_pitcher": our_side.get("probablePitcher", {}).get("fullName", "TBD"),
                    "opponent_probable_pitcher": opp_side.get("probablePitcher", {}).get("fullName", "TBD"),
                    "status": game["status"]["detailedState"],
                    "venue": game.get("venue", {}).get("name", ""),
                })
        return out

    # ---- lineups ----

    def get_lineup(self, game_id: int) -> dict:
        resp = self._get(f"/game/{game_id}/boxscore")
        result: dict[str, dict] = {}
        for side in ("home", "away"):
            team_data = resp.get("teams", {}).get(side, {})
            batting_order = team_data.get("battingOrder", [])
            players = team_data.get("players", {})

            lineup = []
            for player_id in batting_order:
                player = players.get(f"ID{player_id}", {})
                lineup.append({
                    "id": player_id,
                    "name": player.get("person", {}).get("fullName", ""),
                    "position": player.get("position", {}).get("abbreviation", ""),
                    "batting_order": batting_order.index(player_id) + 1,
                })

            pitchers = team_data.get("pitchers", [])
            starter_id = pitchers[0] if pitchers else 0
            starter_name = ""
            if starter_id:
                starter_name = (
                    players.get(f"ID{starter_id}", {})
                    .get("person", {})
                    .get("fullName", "TBD")
                )

            result[side] = {
                "team": team_data.get("team", {}).get("name", ""),
                "lineup": lineup,
                "starter_id": starter_id,
                "starter_name": starter_name,
                "lineup_available": len(lineup) > 0,
            }
        return result

    def get_last_lineup(self, team_code: str) -> dict:
        tid = team_id(team_code)
        if not tid:
            return {"error": f"Team code '{team_code}' not found"}
        today = _date.today()
        for days_back in range(1, 8):
            check = (today - timedelta(days=days_back)).strftime("%Y-%m-%d")
            resp = self._get("/schedule", sportId=1, teamId=tid, date=check)
            for date_entry in resp.get("dates", []):
                for game in date_entry.get("games", []):
                    if game["status"]["detailedState"] != "Final":
                        continue
                    game_id = game["gamePk"]
                    box = self.get_lineup(game_id)
                    side = "home" if game["teams"]["home"]["team"]["id"] == tid else "away"
                    return {
                        "lineup": box.get(side, {}).get("lineup", []),
                        "game_id": game_id,
                        "date": check,
                    }
        return {"lineup": [], "game_id": None, "date": None}


# Module-level singleton — most callers use this.
client = MLBStatsClient()

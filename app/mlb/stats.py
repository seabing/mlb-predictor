"""Player + team statistics — fetched from the MLB Stats API and blended
across seasons.

`StatsService` weights the current and prior season (configurable). Methods
return either a single normalized dict or a season-keyed dict ready for
blend_hitting / blend_pitching.

Note: get_hitting_splits is preserved from the original implementation,
including its pre-existing bug (function returns None — falls through to
season totals). Fixing that would change tuning results, so leaving it.
"""
from __future__ import annotations

from datetime import date as _date, timedelta

import requests

from app.mlb.client import MLBStatsClient


class StatsService:
    """Fetch + blend MLB stats. Weights 2026 (current) heavier than 2025."""

    WEIGHT_PRIOR = 0.25   # 2025
    WEIGHT_CURRENT = 0.75  # 2026
    PRIOR_SEASON = 2025
    CURRENT_SEASON = 2026

    LEAGUE_AVG_BULLPEN_ERA = 4.20

    def __init__(self, client: MLBStatsClient | None = None) -> None:
        self.client = client or MLBStatsClient()

    @property
    def _seasons(self) -> list[tuple[int, float]]:
        return [
            (self.PRIOR_SEASON, self.WEIGHT_PRIOR),
            (self.CURRENT_SEASON, self.WEIGHT_CURRENT),
        ]

    # ---- hitting ----

    def get_hitting_stats(self, player_id: int) -> dict[int, dict]:
        out: dict[int, dict] = {}
        for season, weight in self._seasons:
            row = self._fetch_split_stat(
                player_id, season=season, group="hitting", stats="season"
            )
            if row is None:
                continue
            out[season] = self._hitting_row(row, weight)
        return out

    def get_hitting_splits(self, player_id: int, split: str = "home"):
        """Pre-existing behavior: returns None. Callers fall back to season totals."""
        # Mirror of the original buggy function; kept for now.
        for season, _ in self._seasons:
            try:
                url = (
                    f"{self.client.base_url}/people/{player_id}/stats"
                    f"?stats=homeAndAway&group=hitting&season={season}"
                )
                resp = requests.get(url, timeout=self.client.timeout).json()
                stat_list = resp.get("stats", [])
                if not stat_list:
                    continue
                # original computed numbers here but never assigned/returned
                _ = stat_list  # silence linter
            except Exception:
                continue
        return None

    def get_batter_vs_pitcher(self, batter_id: int, pitcher_id: int):
        url = (
            f"{self.client.base_url}/people/{batter_id}/stats"
            f"?stats=vsPlayer&opposingPlayerId={pitcher_id}&group=hitting"
        )
        try:
            resp = requests.get(url, timeout=self.client.timeout).json()
        except Exception:
            return None
        stat_list = resp.get("stats", [])
        if not stat_list:
            return None
        splits = stat_list[0].get("splits", [])
        if not splits:
            return None
        s = splits[0].get("stat", {})
        ab = max(int(s.get("atBats", 0) or 0), 1)
        if ab < 5:
            return None
        return {
            "avg": float(s.get("avg", 0) or 0),
            "obp": float(s.get("obp", 0) or 0),
            "slg": float(s.get("slg", 0) or 0),
            "hr": int(s.get("homeRuns", 0) or 0),
            "ab": ab,
        }

    # ---- pitching ----

    def get_pitching_stats(self, player_id: int) -> dict[int, dict]:
        out: dict[int, dict] = {}
        for season, weight in self._seasons:
            row = self._fetch_split_stat(
                player_id, season=season, group="pitching", stats="season"
            )
            if row is None:
                continue
            out[season] = self._pitching_row(row, weight)
        return out

    # ---- team ----

    def get_bullpen_era(self, team_id: int) -> float:
        season_eras: list[tuple[float, float]] = []
        for season, weight in self._seasons:
            url = (
                f"{self.client.base_url}/teams/{team_id}/stats"
                f"?stats=season&group=pitching&season={season}"
            )
            try:
                resp = requests.get(url, timeout=self.client.timeout).json()
            except Exception:
                continue
            stat_list = resp.get("stats", [])
            if not stat_list:
                continue
            splits = stat_list[0].get("splits", [])
            if not splits:
                continue
            era = float(splits[0].get("stat", {}).get("era", 0) or 0)
            if era > 0:
                season_eras.append((era, weight))
        if not season_eras:
            return self.LEAGUE_AVG_BULLPEN_ERA
        total_w = sum(w for _, w in season_eras)
        return round(sum(era * w for era, w in season_eras) / total_w, 2)

    def get_recent_form(self, team_id: int, games: int = 10) -> dict:
        today = _date.today()
        start = (today - timedelta(days=30)).strftime("%Y-%m-%d")
        end = today.strftime("%Y-%m-%d")
        url = (
            f"{self.client.base_url}/schedule?sportId=1&teamId={team_id}"
            f"&startDate={start}&endDate={end}&gameType=R"
        )
        try:
            resp = requests.get(url, timeout=self.client.timeout).json()
        except Exception:
            return {"wins": 0, "losses": 0, "games": 0, "win_pct": 0.5}

        results: list[int] = []
        for date_entry in resp.get("dates", []):
            for game in date_entry.get("games", []):
                if game["status"]["detailedState"] != "Final":
                    continue
                home = game["teams"]["home"]
                away = game["teams"]["away"]
                is_home = home["team"]["id"] == team_id
                our_side = home if is_home else away
                won = our_side.get("isWinner", False)
                results.append(1 if won else 0)

        results = results[-games:]
        wins = sum(results)
        losses = len(results) - wins
        win_pct = wins / max(len(results), 1)
        return {
            "wins": wins, "losses": losses, "games": len(results),
            "win_pct": round(win_pct, 3),
        }

    # ---- internal helpers ----

    def _fetch_split_stat(
        self, player_id: int, *, season: int, group: str, stats: str
    ) -> dict | None:
        url = (
            f"{self.client.base_url}/people/{player_id}/stats"
            f"?stats={stats}&group={group}&season={season}"
        )
        try:
            resp = requests.get(url, timeout=self.client.timeout).json()
        except Exception:
            return None
        stat_list = resp.get("stats", [])
        if not stat_list:
            return None
        splits = stat_list[0].get("splits", [])
        if not splits:
            return None
        return splits[0].get("stat", {})

    def _hitting_row(self, s: dict, weight: float) -> dict:
        ab = max(int(s.get("atBats", 1) or 1), 1)
        pa = max(int(s.get("plateAppearances", 1) or 1), 1)
        hits = int(s.get("hits", 0) or 0)
        doubles = int(s.get("doubles", 0) or 0)
        triples = int(s.get("triples", 0) or 0)
        hr = int(s.get("homeRuns", 0) or 0)
        bb = int(s.get("baseOnBalls", 0) or 0)
        so = int(s.get("strikeOuts", 0) or 0)
        hbp = int(s.get("hitByPitch", 0) or 0)
        sf = int(s.get("sacFlies", 0) or 0)

        # wOBA (2025 coefficients)
        woba_num = (
            0.69 * bb + 0.72 * hbp + 0.888 * (hits - doubles - triples - hr)
            + 1.271 * doubles + 1.616 * triples + 2.101 * hr
        )
        woba_den = max(ab + bb + sf + hbp, 1)
        woba = woba_num / woba_den

        slg = float(s.get("slg", 0) or 0)
        avg = float(s.get("avg", 0) or 0)
        iso = slg - avg
        babip_den = max(ab - so - hr + sf, 1)
        babip = (hits - hr) / babip_den

        return {
            "avg": avg,
            "obp": float(s.get("obp", 0) or 0),
            "slg": slg,
            "ops": float(s.get("ops", 0) or 0),
            "woba": woba,
            "iso": iso,
            "babip": babip,
            "hr": hr,
            "k_pct": so / pa,
            "bb_pct": bb / pa,
            "pa": pa,
            "weight": weight,
        }

    def _pitching_row(self, s: dict, weight: float) -> dict:
        ip = float(s.get("inningsPitched", 0) or 0)
        so = float(s.get("strikeOuts", 0) or 0)
        bb = float(s.get("baseOnBalls", 0) or 0)
        hr = float(s.get("homeRuns", 0) or 0)
        ip_safe = max(ip, 1)
        fip = ((13 * hr + 3 * bb - 2 * so) / ip_safe) + 3.10
        bf = max(int(s.get("battersFaced", 1) or 1), 1)
        return {
            "era": float(s.get("era", 0) or 0),
            "whip": float(s.get("whip", 0) or 0),
            "k9": float(s.get("strikeoutsPer9Inn", 0) or 0),
            "bb9": float(s.get("walksPer9Inn", 0) or 0),
            "fip": fip,
            "k_bb_pct": (so - bb) / bf,
            "gb_pct": float(s.get("groundOutsToAirouts", 0) or 0),
            "innings": ip,
            "weight": weight,
        }


# ---- pure helpers (no I/O, no state) ----

def blend_hitting(stats: dict) -> dict | None:
    if not stats:
        return None
    blended = {k: 0.0 for k in
               ("avg", "obp", "slg", "ops", "woba", "iso", "babip", "k_pct", "bb_pct")}
    total_w = sum(s["weight"] for s in stats.values())
    for s in stats.values():
        w = s["weight"] / total_w
        for key in blended:
            blended[key] += s[key] * w
    return blended


def blend_pitching(stats: dict) -> dict | None:
    if not stats:
        return None
    blended = {k: 0.0 for k in ("era", "whip", "k9", "bb9", "fip", "k_bb_pct", "gb_pct")}
    total_w = sum(s["weight"] for s in stats.values())
    for s in stats.values():
        w = s["weight"] / total_w
        for key in blended:
            blended[key] += s[key] * w
    return blended


# Module-level singleton
stats_service = StatsService()

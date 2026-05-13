"""Spotrac scraper — fetches contract data and caches per team.

`SpotracScraper` owns the JSON cache, the URL patterns, and the HTTP fetch.
The HTML parsing lives in `parser.py` (pure, testable).
"""
from __future__ import annotations

import json
import os
from datetime import datetime

import requests

from app.core.config import settings
from app.salaries.services.parser import normalize_name, parse_contracts


# Spotrac uses URL slugs that mostly match the team's full name.
SPOTRAC_SLUGS: dict[str, str] = {
    "ARI": "arizona-diamondbacks", "ATL": "atlanta-braves",
    "BAL": "baltimore-orioles", "BOS": "boston-red-sox",
    "CHC": "chicago-cubs", "CWS": "chicago-white-sox",
    "CIN": "cincinnati-reds", "CLE": "cleveland-guardians",
    "COL": "colorado-rockies", "DET": "detroit-tigers",
    "HOU": "houston-astros", "KC": "kansas-city-royals",
    "LAA": "los-angeles-angels", "LAD": "los-angeles-dodgers",
    "MIA": "miami-marlins", "MIL": "milwaukee-brewers",
    "MIN": "minnesota-twins", "NYM": "new-york-mets",
    "NYY": "new-york-yankees", "OAK": "athletics",
    "PHI": "philadelphia-phillies", "PIT": "pittsburgh-pirates",
    "SD": "san-diego-padres", "SF": "san-francisco-giants",
    "SEA": "seattle-mariners", "STL": "st-louis-cardinals",
    "TB": "tampa-bay-rays", "TEX": "texas-rangers",
    "TOR": "toronto-blue-jays", "WSH": "washington-nationals",
}

URL_PATTERNS = [
    "https://www.spotrac.com/mlb/{slug}/contracts",
    "https://www.spotrac.com/mlb/{slug}/payroll",
    "https://www.spotrac.com/mlb/{slug}/cap",
    "https://www.spotrac.com/mlb/{slug}/cap/2026",
]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0 Safari/537.36"
)


class SpotracScraper:
    CACHE_TTL_HOURS = 24
    HTTP_TIMEOUT = 10

    def __init__(self, cache_path: str | None = None) -> None:
        self.cache_path = cache_path or settings.salaries_cache_path

    # ---- cache ----

    def _load_cache(self) -> dict:
        if not os.path.exists(self.cache_path):
            return {}
        try:
            with open(self.cache_path, "r") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_cache(self, cache: dict) -> None:
        os.makedirs(os.path.dirname(self.cache_path) or ".", exist_ok=True)
        with open(self.cache_path, "w") as f:
            json.dump(cache, f, indent=2)

    def _is_fresh(self, entry: dict | None) -> bool:
        if not entry or "fetched_at" not in entry:
            return False
        try:
            ts = datetime.fromisoformat(entry["fetched_at"])
            return (datetime.utcnow() - ts).total_seconds() < self.CACHE_TTL_HOURS * 3600
        except Exception:
            return False

    # ---- fetch ----

    def _try_fetch(self, slug: str):
        """Try a few URL shapes; return (url, html, status, http_code) of first
        that has tables."""
        last_err = None
        for tmpl in URL_PATTERNS:
            url = tmpl.format(slug=slug)
            try:
                r = requests.get(
                    url,
                    headers={
                        "User-Agent": USER_AGENT,
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9",
                        "Accept-Language": "en-US,en;q=0.9",
                    },
                    timeout=self.HTTP_TIMEOUT,
                    allow_redirects=True,
                )
                html = r.text or ""
                n_tables = html.count("<table")
                n_tr = html.count("<tr")
                print(
                    f"[salaries] GET {url} → HTTP {r.status_code}, "
                    f"{len(html)} bytes, {n_tables} tables, {n_tr} <tr> tags"
                )
                if r.status_code == 200 and n_tables >= 1:
                    return url, html, "ok", r.status_code
                last_err = f"HTTP {r.status_code}, tables={n_tables}"
            except Exception as e:
                last_err = str(e)
                print(f"[salaries] GET {url} threw: {e}")
        return None, None, last_err or "no usable response", None

    # ---- public ----

    def fetch_team_salaries(self, team_code: str, force: bool = False) -> dict:
        """Return {normalized_name: {salary, years_left, ...}}. Cached 24h."""
        team_code = (team_code or "").upper()
        cache = self._load_cache()
        entry = cache.get(team_code)
        if not force and self._is_fresh(entry):
            return entry.get("players", {})

        slug = SPOTRAC_SLUGS.get(team_code)
        if not slug:
            print(f"[salaries] no Spotrac slug for {team_code}")
            return entry.get("players", {}) if entry else {}

        url, html, status, http_code = self._try_fetch(slug)
        if not html:
            print(f"[salaries] all URL patterns failed for {team_code}: {status}")
            return entry.get("players", {}) if entry else {}

        try:
            players = parse_contracts(html)
        except Exception as e:
            print(f"[salaries] parse failed for {team_code}: {e}")
            return entry.get("players", {}) if entry else {}

        print(f"[salaries] {team_code}: parsed {len(players)} player rows from {url}")
        cache[team_code] = {
            "fetched_at": datetime.utcnow().isoformat(),
            "url": url,
            "http_status": http_code,
            "html_bytes": len(html),
            "player_count": len(players),
            "players": players,
        }
        self._save_cache(cache)
        return players

    def enrich_roster(self, team_code: str, roster_list: list[dict]) -> list[dict]:
        """Mutate roster entries in place, adding salary fields where matched."""
        salaries = self.fetch_team_salaries(team_code)
        if not salaries:
            return roster_list
        for p in roster_list:
            s = salaries.get(normalize_name(p.get("name", "")))
            if s:
                p["salary"] = s.get("salary")
                p["years_left"] = s.get("years_left")
                p["contract_end_year"] = s.get("contract_end_year")
                p["total_contract_value"] = s.get("total_value")
        return roster_list

    def cache_status(self) -> dict:
        cache = self._load_cache()
        return {
            code: {
                "fetched_at": entry.get("fetched_at"),
                "fresh": self._is_fresh(entry),
                "player_count": entry.get("player_count", 0),
            }
            for code, entry in cache.items()
        }

    def clear_cache(self) -> dict:
        if os.path.exists(self.cache_path):
            os.remove(self.cache_path)
        return {"status": "cleared"}

    def debug_team(self, team_code: str) -> dict:
        """Return diagnostic detail about a fetch + parse for one team."""
        from bs4 import BeautifulSoup
        team_code = (team_code or "").upper()
        slug = SPOTRAC_SLUGS.get(team_code)
        if not slug:
            return {"error": f"no slug for {team_code}"}
        url, html, status, http_code = self._try_fetch(slug)
        if not html:
            return {"team": team_code, "error": status, "tried": URL_PATTERNS}

        soup = BeautifulSoup(html, "html.parser")
        tables = soup.find_all("table")
        table_summaries = []
        for i, t in enumerate(tables[:10]):
            thead = t.find("thead")
            if thead:
                headers = [th.get_text(" ", strip=True) for th in thead.find_all(["th", "td"])]
            else:
                first = t.find("tr")
                headers = (
                    [c.get_text(" ", strip=True) for c in first.find_all(["th", "td"])]
                    if first else []
                )
            rows = t.find_all("tr")
            sample_row = ""
            for r in rows[1:4]:
                cells = [c.get_text(" ", strip=True) for c in r.find_all(["td", "th"])]
                if any(cells):
                    sample_row = " | ".join(cells)[:300]
                    break
            table_summaries.append({
                "index": i, "headers": headers,
                "row_count": len(rows), "sample_row": sample_row,
            })

        players = parse_contracts(html)
        return {
            "team": team_code,
            "url_used": url,
            "http_status": http_code,
            "html_bytes": len(html),
            "tables_found": len(tables),
            "table_summaries": table_summaries,
            "parsed_player_count": len(players),
            "sample_parsed_players": list(players.values())[:5],
            "body_text_excerpt": soup.get_text(" ", strip=True)[:600],
        }


# Module-level singleton
spotrac = SpotracScraper()

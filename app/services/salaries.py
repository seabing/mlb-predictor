"""Spotrac salary scraper.

Fetches each team's contracts page, parses out base salary + contract terms,
and caches to data/salaries.json (24h TTL). Built defensively because Spotrac
HTML can shift; failures degrade gracefully (returns empty / cached data).
"""
import json
import os
import re
import time
from datetime import datetime, timedelta

import requests

CACHE_FILE = "data/salaries.json"
CACHE_TTL_HOURS = 24
CURRENT_SEASON = 2026

# Spotrac uses URL slugs that mostly match the team's full name.
SPOTRAC_SLUGS = {
    "ARI": "arizona-diamondbacks",
    "ATL": "atlanta-braves",
    "BAL": "baltimore-orioles",
    "BOS": "boston-red-sox",
    "CHC": "chicago-cubs",
    "CWS": "chicago-white-sox",
    "CIN": "cincinnati-reds",
    "CLE": "cleveland-guardians",
    "COL": "colorado-rockies",
    "DET": "detroit-tigers",
    "HOU": "houston-astros",
    "KC": "kansas-city-royals",
    "LAA": "los-angeles-angels",
    "LAD": "los-angeles-dodgers",
    "MIA": "miami-marlins",
    "MIL": "milwaukee-brewers",
    "MIN": "minnesota-twins",
    "NYM": "new-york-mets",
    "NYY": "new-york-yankees",
    "OAK": "athletics",
    "PHI": "philadelphia-phillies",
    "PIT": "pittsburgh-pirates",
    "SD": "san-diego-padres",
    "SF": "san-francisco-giants",
    "SEA": "seattle-mariners",
    "STL": "st-louis-cardinals",
    "TB": "tampa-bay-rays",
    "TEX": "texas-rangers",
    "TOR": "toronto-blue-jays",
    "WSH": "washington-nationals",
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0 Safari/537.36"
)


# ---------- cache ----------

def _load_cache():
    if not os.path.exists(CACHE_FILE):
        return {}
    try:
        with open(CACHE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(cache):
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)


def _is_fresh(entry, hours=CACHE_TTL_HOURS):
    if not entry or "fetched_at" not in entry:
        return False
    try:
        ts = datetime.fromisoformat(entry["fetched_at"])
        return (datetime.utcnow() - ts).total_seconds() < hours * 3600
    except Exception:
        return False


# ---------- helpers ----------

def _normalize_name(name):
    """Lowercase, strip non-letters. Used as match key."""
    return re.sub(r"[^a-z]", "", (name or "").lower())


def _parse_money(s):
    if not s:
        return None
    s = re.sub(r"[^\d.\-]", "", str(s))
    if not s or s == "-":
        return None
    try:
        return int(float(s))
    except Exception:
        return None


def _parse_int(s):
    if s is None:
        return None
    s = str(s).strip()
    m = re.match(r"-?\d+", s)
    if not m:
        return None
    try:
        return int(m.group())
    except Exception:
        return None


# ---------- scraper ----------

def fetch_team_salaries(team_code, force=False):
    """Returns {normalized_name: {name, salary, years_left, total_value, contract_end_year}}.

    On failure, returns whatever's in the cache (possibly stale, possibly empty).
    Always non-blocking from the user's POV — 10s timeout.
    """
    team_code = (team_code or "").upper()
    cache = _load_cache()
    entry = cache.get(team_code)
    if not force and _is_fresh(entry):
        return entry.get("players", {})

    slug = SPOTRAC_SLUGS.get(team_code)
    if not slug:
        return entry.get("players", {}) if entry else {}

    url = f"https://www.spotrac.com/mlb/{slug}/contracts"
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
            timeout=10,
        )
        resp.raise_for_status()
    except Exception as e:
        print(f"[salaries] fetch failed for {team_code}: {e}")
        return entry.get("players", {}) if entry else {}

    try:
        players = parse_spotrac_html(resp.text)
    except Exception as e:
        print(f"[salaries] parse failed for {team_code}: {e}")
        return entry.get("players", {}) if entry else {}

    cache[team_code] = {
        "fetched_at": datetime.utcnow().isoformat(),
        "url": url,
        "player_count": len(players),
        "players": players,
    }
    _save_cache(cache)
    print(f"[salaries] cached {len(players)} players for {team_code}")
    return players


def parse_spotrac_html(html):
    """Best-effort parse of any Spotrac contract table on the page.

    Handles variation in column ordering by matching headers against keywords.
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    out = {}

    for table in soup.find_all("table"):
        # Build header → index map
        headers = []
        thead = table.find("thead")
        if thead:
            headers = [th.get_text(" ", strip=True).lower()
                       for th in thead.find_all(["th", "td"])]
        if not headers:
            first_row = table.find("tr")
            if first_row:
                headers = [c.get_text(" ", strip=True).lower()
                           for c in first_row.find_all(["th", "td"])]
        if not headers:
            continue

        def find_idx(*needles):
            for i, h in enumerate(headers):
                for n in needles:
                    if n in h:
                        return i
            return -1

        name_idx = find_idx("player", "name")
        salary_idx = find_idx("base salary", "salary", "base", "current salary",
                              "2026 base", "2026")
        years_idx = find_idx("yrs", "years")
        fa_idx = find_idx("free agent", "fa year", "fa", "expir")
        total_idx = find_idx("total value", "total cash", "value", "total")

        rows = table.find_all("tr")
        for row in rows:
            cells = row.find_all(["td"])
            if len(cells) < 3:
                continue
            try:
                name_cell = cells[name_idx] if 0 <= name_idx < len(cells) else cells[0]
                name = name_cell.get_text(" ", strip=True)
                # Strip leading jersey number etc.
                name = re.sub(r"^[\d\s\.\-]+", "", name).strip()
                if len(name) < 3 or any(ch.isdigit() for ch in name[:2]):
                    continue

                salary = None
                if 0 <= salary_idx < len(cells):
                    salary = _parse_money(cells[salary_idx].get_text(" ", strip=True))
                years_left = None
                if 0 <= years_idx < len(cells):
                    years_left = _parse_int(cells[years_idx].get_text(" ", strip=True))
                fa_year = None
                if 0 <= fa_idx < len(cells):
                    fa_year = _parse_int(cells[fa_idx].get_text(" ", strip=True))
                total_value = None
                if 0 <= total_idx < len(cells):
                    total_value = _parse_money(cells[total_idx].get_text(" ", strip=True))

                # Derive years_left from FA year if missing
                if years_left is None and fa_year and fa_year >= CURRENT_SEASON:
                    years_left = fa_year - CURRENT_SEASON

                # Skip rows that are clearly noise (no salary AND no years)
                if salary is None and years_left is None and total_value is None:
                    continue

                key = _normalize_name(name)
                if not key:
                    continue
                # Don't overwrite a row that already has salary with one that doesn't
                existing = out.get(key)
                if existing and existing.get("salary") and salary is None:
                    continue

                out[key] = {
                    "name": name,
                    "salary": salary,
                    "years_left": years_left,
                    "total_value": total_value,
                    "contract_end_year": fa_year,
                }
            except Exception:
                continue
    return out


def get_player_salary(team_code, player_name):
    return fetch_team_salaries(team_code).get(_normalize_name(player_name))


def enrich_roster(team_code, roster_list):
    """Mutates roster entries in place, adding salary fields where matched."""
    salaries = fetch_team_salaries(team_code)
    if not salaries:
        return roster_list
    for p in roster_list:
        s = salaries.get(_normalize_name(p.get("name", "")))
        if s:
            p["salary"] = s.get("salary")
            p["years_left"] = s.get("years_left")
            p["contract_end_year"] = s.get("contract_end_year")
            p["total_contract_value"] = s.get("total_value")
    return roster_list


def cache_status():
    """Diagnostic: return cache freshness info per team."""
    cache = _load_cache()
    out = {}
    for code, entry in cache.items():
        out[code] = {
            "fetched_at": entry.get("fetched_at"),
            "fresh": _is_fresh(entry),
            "player_count": entry.get("player_count", 0),
        }
    return out


def clear_cache():
    if os.path.exists(CACHE_FILE):
        os.remove(CACHE_FILE)
    return {"status": "cleared"}

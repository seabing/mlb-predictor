"""MLB team code ↔ team id mapping.

Some codes alias the same team (AZ ↔ ARI, CHA ↔ CWS, etc). The PREFERRED set
controls which alias the reverse lookup returns.
"""
from __future__ import annotations

TEAM_IDS: dict[str, int] = {
    "ARI": 109, "AZ": 109,
    "ATL": 144,
    "BAL": 110,
    "BOS": 111,
    "CHC": 112,
    "CWS": 145, "CHA": 145,
    "CIN": 113,
    "CLE": 114,
    "COL": 115,
    "DET": 116,
    "HOU": 117,
    "KC": 118, "KCA": 118,
    "LAA": 108,
    "LAD": 119, "LAN": 119,
    "MIA": 146,
    "MIL": 158,
    "MIN": 142,
    "NYM": 121,
    "NYY": 147,
    "ATH": 133, "OAK": 133,
    "PHI": 143,
    "PIT": 134,
    "SD": 135, "SDN": 135,
    "SF": 137, "SFN": 137,
    "SEA": 136,
    "STL": 138, "SLN": 138,
    "TB": 139, "TBA": 139,
    "TEX": 140,
    "TOR": 141,
    "WSH": 120, "WAS": 120,
}

# Canonical code per team — used when reverse-mapping id → code.
PREFERRED: set[str] = {
    "ARI", "ATL", "BAL", "BOS", "CHC", "CWS", "CIN", "CLE", "COL",
    "DET", "HOU", "KC", "LAA", "LAD", "MIA", "MIL", "MIN", "NYM",
    "NYY", "ATH", "PHI", "PIT", "SD", "SF", "SEA", "STL", "TB",
    "TEX", "TOR", "WSH",
}


def team_id(code: str) -> int | None:
    """Lookup ``code`` (case-insensitive) → MLB team id, or None."""
    if not code:
        return None
    return TEAM_IDS.get(code.upper())


def code_for_id(team_id: int) -> str | None:
    """Reverse lookup: id → canonical 2-3 letter code, preferring PREFERRED."""
    fallback: str | None = None
    for code, tid in TEAM_IDS.items():
        if tid != team_id:
            continue
        if code in PREFERRED:
            return code
        fallback = fallback or code
    return fallback

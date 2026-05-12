"""Static park-factor table.

Keyed by MLB team_id. 1.0 = neutral, >1.0 = hitter friendly, <1.0 = pitcher
friendly. Used by PredictionEngine to nudge scores up/down based on venue.
"""
from __future__ import annotations

PARK_FACTORS: dict[int, float] = {
    108: 1.01,  # Angel Stadium
    109: 1.03,  # Chase Field
    110: 0.99,  # Camden Yards
    111: 1.03,  # Fenway — hitter friendly
    112: 0.94,  # Wrigley — pitcher friendly
    113: 1.04,  # Great American Ball Park — very hitter friendly
    114: 0.98,  # Progressive Field
    115: 1.12,  # Coors Field — most hitter friendly
    116: 0.97,  # Comerica Park
    117: 0.98,  # Minute Maid
    118: 0.99,  # Kauffman Stadium
    119: 1.08,  # Dodger Stadium — slight hitter
    120: 1.00,  # Nationals Park
    121: 1.02,  # Citi Field (NYM) — slight pitcher park
    133: 1.00,  # Oakland Coliseum
    134: 0.97,  # PNC Park
    135: 0.96,  # Petco Park
    136: 0.97,  # T-Mobile Park
    137: 0.95,  # Oracle Park
    138: 0.98,  # Busch Stadium
    139: 0.96,  # Tropicana Field
    140: 1.02,  # Globe Life Field
    141: 1.01,  # Rogers Centre
    142: 0.99,  # Target Field
    143: 1.02,  # Citizens Bank Park
    144: 0.95,  # Truist Park (ATL) — slight pitcher
    145: 0.97,  # Guaranteed Rate
    146: 0.97,  # loanDepot park
    147: 1.05,  # Yankee Stadium — hitter friendly
    158: 0.98,  # American Family Field
}


def get(team_id: int) -> float:
    """Park factor for the home team's venue. Defaults to 1.0 if unknown."""
    return PARK_FACTORS.get(team_id, 1.0)

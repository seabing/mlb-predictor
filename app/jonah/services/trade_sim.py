"""Trade simulator (Jonah Feature 2).

Input a proposed trade between two teams — which players each side sends — and
see how both teams' projected win probabilities shift, the salary implications,
and a side-by-side model comparison of the players exchanged.

Reuses team_rating (win probability + per-player value), the MLB client (rosters)
and the Spotrac salaries service (contract data). CBA/luxury-tax feasibility is
deliberately NOT evaluated here — that's Jonah Feature 7's job; we surface the
raw salary/contract facts a user would need.
"""
from __future__ import annotations

from app.jonah.services import team_rating as _rating
from app.mlb.client import client as mlb_client
from app.predictions.services.weights import weights_store
from app.salaries.services.spotrac import spotrac


def _by_id(roster: list[dict], player_id: int) -> dict | None:
    for p in roster:
        if p.get("id") == player_id:
            return p
    return None


def _swap(roster: list[dict], remove_ids: list[int], add_players: list[dict]) -> list[dict]:
    remove = set(remove_ids)
    kept = [p for p in roster if p.get("id") not in remove]
    return kept + list(add_players)


def _salary_sum(players: list[dict]) -> float:
    return round(sum(float(p.get("salary") or 0) for p in players), 2)


def _player_card(player: dict, weights: dict) -> dict:
    value = _rating.player_value(player, weights)
    return {
        "id": player.get("id"),
        "name": player.get("name"),
        "position": player.get("position"),
        "kind": value["kind"],
        "model_score": value["score"],
        "salary": player.get("salary"),
        "years_left": player.get("years_left"),
        "contract_end_year": player.get("contract_end_year"),
        "total_contract_value": player.get("total_contract_value"),
    }


def simulate_trade(
    team_a: str,
    team_a_sends: list[int],
    team_b: str,
    team_b_sends: list[int],
) -> dict:
    """Run a two-team trade and return the win-probability + salary impact.

    Args:
        team_a / team_b: team codes (e.g. 'NYY', 'LAD').
        team_a_sends / team_b_sends: lists of MLB player ids each team trades away.
    """
    a_data = mlb_client.get_roster(team_a)
    if "error" in a_data:
        return {"error": a_data["error"]}
    b_data = mlb_client.get_roster(team_b)
    if "error" in b_data:
        return {"error": b_data["error"]}

    # Enrich both rosters with contract data (mutates the dicts in place).
    a_roster = spotrac.enrich_roster(a_data["team"], a_data["roster"])
    b_roster = spotrac.enrich_roster(b_data["team"], b_data["roster"])
    a_id, b_id = a_data.get("team_id", 0), b_data.get("team_id", 0)

    # Resolve the players each side is sending.
    a_out = [p for p in (_by_id(a_roster, pid) for pid in team_a_sends) if p]
    b_out = [p for p in (_by_id(b_roster, pid) for pid in team_b_sends) if p]
    if not a_out and not b_out:
        return {"error": "Select at least one player to trade."}

    weights = weights_store.load()

    # Ratings before.
    a_before = _rating.rate_team(a_roster, a_id, weights)
    b_before = _rating.rate_team(b_roster, b_id, weights)

    # Build post-trade rosters: each team loses its outgoing, gains the other's.
    a_after_roster = _swap(a_roster, team_a_sends, b_out)
    b_after_roster = _swap(b_roster, team_b_sends, a_out)

    a_after = _rating.rate_team(a_after_roster, a_id, weights)
    b_after = _rating.rate_team(b_after_roster, b_id, weights)

    def team_block(name, before, after, out_players, in_players):
        salary_out = _salary_sum(out_players)
        salary_in = _salary_sum(in_players)
        return {
            "team": name,
            "before_win_pct": before["win_pct"],
            "after_win_pct": after["win_pct"],
            "delta": round(after["win_pct"] - before["win_pct"], 1),
            "salary_out_millions": round(salary_out / 1_000_000, 1),
            "salary_in_millions": round(salary_in / 1_000_000, 1),
            "net_salary_change_millions": round((salary_in - salary_out) / 1_000_000, 1),
            "sends": [_player_card(p, weights) for p in out_players],
            "receives": [_player_card(p, weights) for p in in_players],
        }

    return {
        "team_a": team_block(a_data["team"], a_before, a_after, a_out, b_out),
        "team_b": team_block(b_data["team"], b_before, b_after, b_out, a_out),
    }

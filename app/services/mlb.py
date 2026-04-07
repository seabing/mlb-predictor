import requests

BASE_URL = "https://statsapi.mlb.com/api/v1"

TEAM_IDS = {
    "ARI": 109, "ATL": 144, "BAL": 110, "BOS": 111, "CHC": 112,
    "CWS": 145, "CIN": 113, "CLE": 114, "COL": 115, "DET": 116,
    "HOU": 117, "KC": 118, "LAA": 108, "LAD": 119, "MIA": 146,
    "MIL": 158, "MIN": 142, "NYM": 121, "NYY": 147, "OAK": 133,
    "PHI": 143, "PIT": 134, "SD": 135, "SF": 137, "SEA": 136,
    "STL": 138, "TB": 139, "TEX": 140, "TOR": 141, "WSH": 120
}

def get_roster(team_code):
    team_id = TEAM_IDS.get(team_code.upper())
    if not team_id:
        return {"error": f"Team code '{team_code}' not found"}

    # Get 40-man roster which includes IL players
    url = f"{BASE_URL}/teams/{team_id}/roster?rosterType=fullRoster&hydrate=person(stats(type=season,group=hitting,season=2026))"
    resp = requests.get(url).json()

    players = []
    for player in resp.get("roster", []):
        status_code = player.get("status", {}).get("code", "A")
        il = status_code != "A"
        players.append({
            "id": player["person"]["id"],
            "name": player["person"]["fullName"],
            "jersey": player.get("jerseyNumber", ""),
            "position": player["position"]["abbreviation"],
            "position_type": player["position"]["type"],
            "status": player.get("status", {}).get("description", "Active"),
            "il": il
        })

    return {
        "team": team_code.upper(),
        "team_id": team_id,
        "roster": players
    }


def get_schedule(team_code, date=None):
    team_id = TEAM_IDS.get(team_code.upper())
    if not team_id:
        return {"error": f"Team code '{team_code}' not found"}

    if not date:
        from datetime import date as dt
        date = dt.today().strftime("%Y-%m-%d")

    url = f"{BASE_URL}/schedule?sportId=1&teamId={team_id}&date={date}&hydrate=team,probablePitcher"
    resp = requests.get(url).json()

    games = []
    for date_entry in resp.get("dates", []):
        for game in date_entry.get("games", []):
            home = game["teams"]["home"]
            away = game["teams"]["away"]
            is_home = home["team"]["id"] == team_id
            opponent = away["team"] if is_home else home["team"]
            our_side = home if is_home else away
            opp_side = away if is_home else home

            our_pitcher = our_side.get("probablePitcher", {}).get("fullName", "TBD")
            opp_pitcher = opp_side.get("probablePitcher", {}).get("fullName", "TBD")

            games.append({
                "game_id": game["gamePk"],
                "date": date,
                "home_away": "Home" if is_home else "Away",
                "opponent": opponent["name"],
                "opponent_code": opponent.get("abbreviation", ""),
                "our_probable_pitcher": our_pitcher,
                "opponent_probable_pitcher": opp_pitcher,
                "status": game["status"]["detailedState"],
                "venue": game.get("venue", {}).get("name", "")
            })

    return {
        "team": team_code.upper(),
        "date": date,
        "games": games
    }
import requests

BASE_URL = "https://statsapi.mlb.com/api/v1"

TEAM_IDS = {
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
    "ATH": 133,
    "OAK": 133,
    "PHI": 143,
    "PIT": 134,
    "SD": 135, "SDN": 135,
    "SF": 137, "SFN": 137,
    "SEA": 136,
    "STL": 138, "SLN": 138,
    "TB": 139, "TBA": 139,
    "TEX": 140,
    "TOR": 141,
    "WSH": 120, "WAS": 120
}


def get_roster(team_code):
    team_id = TEAM_IDS.get(team_code.upper())
    if not team_id:
        return {"error": f"Team code '{team_code}' not found"}

    url = f"{BASE_URL}/teams/{team_id}/roster?rosterType=depthChart"
    resp = requests.get(url).json()

    seen_ids = set()
    players = []
    for player in resp.get("roster", []):
        player_id = player["person"]["id"]
        if player_id in seen_ids:
            continue
        seen_ids.add(player_id)
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
def get_upcoming(team_code, days=7):
    team_id = TEAM_IDS.get(team_code.upper())
    if not team_id:
        return {"error": f"Team code '{team_code}' not found"}

    from datetime import date, timedelta
    today = date.today()
    start = today.strftime("%Y-%m-%d")
    end = (today + timedelta(days=days)).strftime("%Y-%m-%d")

    url = f"{BASE_URL}/schedule?sportId=1&teamId={team_id}&startDate={start}&endDate={end}&hydrate=team,probablePitcher"
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
                "date": date_entry["date"],
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
        "start": start,
        "end": end,
        "games": games
    }

def get_lineup(game_id):
    url = f"{BASE_URL}/game/{game_id}/boxscore"
    resp = requests.get(url).json()

    result = {}
    for side in ["home", "away"]:
        team_data = resp.get("teams", {}).get(side, {})
        team_name = team_data.get("team", {}).get("name", "")
        batting_order = team_data.get("battingOrder", [])
        players = team_data.get("players", {})

        lineup = []
        for player_id in batting_order:
            key = f"ID{player_id}"
            player = players.get(key, {})
            person = player.get("person", {})
            pos = player.get("position", {})
            lineup.append({
                "id": player_id,
                "name": person.get("fullName", ""),
                "position": pos.get("abbreviation", ""),
                "batting_order": batting_order.index(player_id) + 1
            })

        # Get starting pitcher
        pitchers = team_data.get("pitchers", [])
        starter_id = pitchers[0] if pitchers else 0
        starter_name = ""
        if starter_id:
            key = f"ID{starter_id}"
            starter_name = players.get(key, {}).get("person", {}).get("fullName", "TBD")

        result[side] = {
            "team": team_name,
            "lineup": lineup,
            "starter_id": starter_id,
            "starter_name": starter_name,
            "lineup_available": len(lineup) > 0
        }

    return result

def get_last_lineup(team_code):
    team_id = TEAM_IDS.get(team_code.upper())
    if not team_id:
        return {"error": f"Team code '{team_code}' not found"}

    from datetime import date, timedelta
    # Look back up to 7 days to find the last completed game
    for days_back in range(1, 8):
        check_date = (date.today() - timedelta(days=days_back)).strftime("%Y-%m-%d")
        url = f"{BASE_URL}/schedule?sportId=1&teamId={team_id}&date={check_date}"
        resp = requests.get(url).json()
        for date_entry in resp.get("dates", []):
            for game in date_entry.get("games", []):
                if game["status"]["detailedState"] == "Final":
                    game_id = game["gamePk"]
                    lineup = get_lineup(game_id)
                    # Figure out if team was home or away
                    home_id = game["teams"]["home"]["team"]["id"]
                    side = "home" if home_id == team_id else "away"
                    return {
                        "lineup": lineup.get(side, {}).get("lineup", []),
                        "game_id": game_id,
                        "date": check_date
                    }
    return {"lineup": [], "game_id": None, "date": None}
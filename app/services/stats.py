import requests

BASE_URL = "https://statsapi.mlb.com/api/v1"

WEIGHT_2025 = 0.80
WEIGHT_2026 = 0.20

def get_hitting_stats(player_id):
    stats = {}
    for season, weight in [(2025, WEIGHT_2025), (2026, WEIGHT_2026)]:
        url = f"{BASE_URL}/people/{player_id}/stats?stats=season&group=hitting&season={season}"
        resp = requests.get(url).json()
        stat_list = resp.get("stats", [])
        if not stat_list:
            continue
        splits = stat_list[0].get("splits", [])
        if not splits:
            continue
        s = splits[0].get("stat", {})
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

        # wOBA components (2025 weights)
        woba_num = (0.69*bb + 0.72*hbp + 0.888*(hits-doubles-triples-hr) +
                    1.271*doubles + 1.616*triples + 2.101*hr)
        woba_den = max(ab + bb - 0 + sf + hbp, 1)
        woba = woba_num / woba_den

        # ISO = SLG - AVG
        slg = float(s.get("slg", 0) or 0)
        avg = float(s.get("avg", 0) or 0)
        iso = slg - avg

        # BABIP = (H - HR) / (AB - SO - HR + SF)
        babip_den = max(ab - so - hr + sf, 1)
        babip = (hits - hr) / babip_den

        stats[season] = {
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
            "weight": weight
        }
    return stats

def get_pitching_stats(player_id):
    stats = {}
    for season, weight in [(2025, WEIGHT_2025), (2026, WEIGHT_2026)]:
        url = f"{BASE_URL}/people/{player_id}/stats?stats=season&group=pitching&season={season}"
        resp = requests.get(url).json()
        stat_list = resp.get("stats", [])
        if not stat_list:
            continue
        splits = stat_list[0].get("splits", [])
        if not splits:
            continue
        s = splits[0].get("stat", {})
        ip = float(s.get("inningsPitched", 0) or 0)
        so = float(s.get("strikeOuts", 0) or 0)
        bb = float(s.get("baseOnBalls", 0) or 0)
        hr = float(s.get("homeRuns", 0) or 0)
        ip_safe = max(ip, 1)

        era = float(s.get("era", 0) or 0)
        whip = float(s.get("whip", 0) or 0)
        k9 = float(s.get("strikeoutsPer9Inn", 0) or 0)
        bb9 = float(s.get("walksPer9Inn", 0) or 0)

        # FIP = ((13*HR + 3*BB - 2*SO) / IP) + 3.10
        fip = ((13 * hr + 3 * bb - 2 * so) / ip_safe) + 3.10

        # K-BB%
        bf = max(int(s.get("battersFaced", 1) or 1), 1)
        k_bb_pct = (so - bb) / bf

        # Ground ball % (if available)
        gb_pct = float(s.get("groundOutsToAirouts", 0) or 0)

        stats[season] = {
            "era": era,
            "whip": whip,
            "k9": k9,
            "bb9": bb9,
            "fip": fip,
            "k_bb_pct": k_bb_pct,
            "gb_pct": gb_pct,
            "innings": ip,
            "weight": weight
        }
    return stats

def get_batter_vs_pitcher(batter_id, pitcher_id):
    url = f"{BASE_URL}/people/{batter_id}/stats?stats=vsPlayer&opposingPlayerId={pitcher_id}&group=hitting"
    resp = requests.get(url).json()
    stat_list = resp.get("stats", [])
    if not stat_list:
        return None
    splits = stat_list[0].get("splits", [])
    if not splits:
        return None
    s = splits[0].get("stat", {})
    ab = max(int(s.get("atBats", 0) or 0), 1)
    if ab < 5:
        return None  # too small a sample
    return {
        "avg": float(s.get("avg", 0) or 0),
        "obp": float(s.get("obp", 0) or 0),
        "slg": float(s.get("slg", 0) or 0),
        "hr": int(s.get("homeRuns", 0) or 0),
        "ab": ab
    }

def blend_hitting(stats):
    if not stats:
        return None
    blended = {"avg": 0, "obp": 0, "slg": 0, "ops": 0, "woba": 0, "iso": 0, "babip": 0, "k_pct": 0, "bb_pct": 0}
    total_weight = sum(s["weight"] for s in stats.values())
    for s in stats.values():
        w = s["weight"] / total_weight
        for key in blended:
            blended[key] += s[key] * w
    return blended

def blend_pitching(stats):
    if not stats:
        return None
    blended = {"era": 0, "whip": 0, "k9": 0, "bb9": 0, "fip": 0, "k_bb_pct": 0, "gb_pct": 0}
    total_weight = sum(s["weight"] for s in stats.values())
    for s in stats.values():
        w = s["weight"] / total_weight
        for key in blended:
            blended[key] += s[key] * w
    return blended

def get_hitting_splits(player_id, split="home"):
    """Get home or away splits for a hitter"""
    stats = {}
    for season, weight in [(2025, WEIGHT_2025), (2026, WEIGHT_2026)]:
        url = f"{BASE_URL}/people/{player_id}/stats?stats=homeAndAway&group=hitting&season={season}"
        resp = requests.get(url).json()
        stat_list = resp.get("stats", [])
        if not stat_list:
            continue
        splits = stat_list[0].get("splits", [])
        for s in splits:
            if s.get("split", {}).get("code", "").lower() == split.lower():
                stat = s.get("stat", {})
                ab = max(int(stat.get("atBats", 1) or 1), 1)
                pa = max(int(stat.get("plateAppearances", 1) or 1), 1)
                hits = int(stat.get("hits", 0) or 0)
                doubles = int(stat.get("doubles", 0) or 0)
                triples = int(stat.get("triples", 0) or 0)
                hr = int(stat.get("homeRuns", 0) or 0)
                bb = int(stat.get("baseOnBalls", 0) or 0)
                so = int(stat.get("strikeOuts", 0) or 0)
                hbp = int(stat.get("hitByPitch", 0) or 0)
                sf = int(stat.get("sacFlies", 0) or 0)
                slg = float(stat.get("slg", 0) or 0)
                avg = float(stat.get("avg", 0) or 0)

                woba_num = (0.69*bb + 0.72*hbp + 0.888*(hits-doubles-triples-hr) +
                            1.271*doubles + 1.616*triples + 2.101*hr)
                woba_den = max(ab + bb + sf + hbp, 1)
                woba = woba_num / woba_den
                iso = slg - avg
                babip_den = max(ab - so - hr + sf, 1)
                babip = (hits - hr) / babip_den

def get_bullpen_era(team_id):
    """Get team pitching ERA blended across 2025 and 2026"""
    season_eras = []
    weights = [(2025, 0.80), (2026, 0.20)]

    for season, weight in weights:
        url = f"https://statsapi.mlb.com/api/v1/teams/{team_id}/stats?stats=season&group=pitching&season={season}"
        resp = requests.get(url).json()
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
        return 4.20

    total_weight = sum(w for _, w in season_eras)
    blended = sum(era * w for era, w in season_eras) / total_weight
    return round(blended, 2)

    if not season_eras:
        return 4.20  # league average fallback

    total_weight = sum(w for _, w in season_eras)
    blended = sum(era * w for era, w in season_eras) / total_weight
    return round(blended, 2)

def get_recent_form(team_id, games=10):
    """Get win/loss record for last N games"""
    from datetime import date, timedelta
    end = date.today().strftime("%Y-%m-%d")
    start = (date.today() - timedelta(days=30)).strftime("%Y-%m-%d")

    url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&teamId={team_id}&startDate={start}&endDate={end}&gameType=R"
    resp = requests.get(url).json()

    results = []
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
        "wins": wins,
        "losses": losses,
        "games": len(results),
        "win_pct": round(win_pct, 3)
    }
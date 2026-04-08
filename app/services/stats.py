import requests

BASE_URL = "https://statsapi.mlb.com/api/v1"

# Adjust these weights as the 2026 season progresses
# e.g. by August set WEIGHT_2026 = 0.7, WEIGHT_2025 = 0.3
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
        stats[season] = {
            "avg": float(s.get("avg", 0) or 0),
            "obp": float(s.get("obp", 0) or 0),
            "slg": float(s.get("slg", 0) or 0),
            "ops": float(s.get("ops", 0) or 0),
            "hr": int(s.get("homeRuns", 0) or 0),
            "k_pct": float(s.get("strikeOuts", 0) or 0) / max(int(s.get("atBats", 1) or 1), 1),
            "bb_pct": float(s.get("baseOnBalls", 0) or 0) / max(int(s.get("atBats", 1) or 1), 1),
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
        stats[season] = {
            "era": float(s.get("era", 0) or 0),
            "whip": float(s.get("whip", 0) or 0),
            "k9": float(s.get("strikeoutsPer9Inn", 0) or 0),
            "bb9": float(s.get("walksPer9Inn", 0) or 0),
            "innings": ip,
            "weight": weight
        }
    return stats

def blend_hitting(stats):
    if not stats:
        return None
    blended = {"avg": 0, "obp": 0, "slg": 0, "ops": 0, "k_pct": 0, "bb_pct": 0}
    total_weight = sum(s["weight"] for s in stats.values())
    for s in stats.values():
        w = s["weight"] / total_weight
        for key in blended:
            blended[key] += s[key] * w
    return blended

def blend_pitching(stats):
    if not stats:
        return None
    blended = {"era": 0, "whip": 0, "k9": 0, "bb9": 0}
    total_weight = sum(s["weight"] for s in stats.values())
    for s in stats.values():
        w = s["weight"] / total_weight
        for key in blended:
            blended[key] += s[key] * w
    return blended
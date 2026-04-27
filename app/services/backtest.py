"""Backtesting + random-search weight tuning.

Fetches historical Final games in a date range, replays each through the
predictor with a given weight set, scores predictions against actual
outcomes (log loss / accuracy / Brier), and runs a random search over
the weight space to find a better configuration.

Note on purity: prediction uses *current* season-aggregate stats from the
MLB API, not snapshots as of each game date. That's a known limitation
documented for the user. The tuner is still useful for finding good
relative weight balance.
"""
import json
import math
import os
import random
import time
from datetime import date, timedelta

import requests

from app.services.predict import (
    DEFAULT_HIT_WEIGHTS, DEFAULT_PITCH_WEIGHTS, DEFAULT_BALANCE,
    PARK_FACTORS, HIT_RANGES, PITCH_RANGES, normalize, save_weights
)
from app.services.stats import (
    get_hitting_stats, get_pitching_stats, get_hitting_splits,
    get_batter_vs_pitcher, get_bullpen_era, blend_hitting, blend_pitching
)
from app.services.mlb import get_lineup

BASE_URL = "https://statsapi.mlb.com/api/v1"
CACHE_FILE = "data/backtest_cache.json"
HISTORY_FILE = "data/tuning_history.json"


# ---------- date helpers ----------

def last_n_days_from_today(n=30):
    """Return (start, end) ISO strings spanning the last N days through yesterday.
    Yesterday is the upper bound because today's games likely aren't final yet."""
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=n - 1)
    return start.isoformat(), end.isoformat()


def last_n_days_of_2025_regular_season(n=30):
    """Kept for backward compat. Last N days of 2025 regular season."""
    end = date(2025, 9, 28)
    start = end - timedelta(days=n - 1)
    return start.isoformat(), end.isoformat()


# ---------- historical game fetch ----------

def fetch_finals(start_date, end_date):
    """Return a list of Final games between start_date and end_date (inclusive).
    Each entry has game_id, date, home_team_id, away_team_id, home_score, away_score,
    home_starter_id, away_starter_id, home_lineup_ids, away_lineup_ids.
    """
    cache = _load_cache()
    cache_key = f"finals::{start_date}::{end_date}"
    if cache_key in cache:
        return cache[cache_key]

    url = (f"{BASE_URL}/schedule?sportId=1&startDate={start_date}"
           f"&endDate={end_date}&gameType=R")
    resp = requests.get(url, timeout=30).json()
    games = []
    for date_entry in resp.get("dates", []):
        for game in date_entry.get("games", []):
            if game["status"]["detailedState"] not in ("Final", "Game Over", "Completed Early"):
                continue
            home = game["teams"]["home"]
            away = game["teams"]["away"]
            home_score = home.get("score")
            away_score = away.get("score")
            if home_score is None or away_score is None:
                continue
            games.append({
                "game_id": game["gamePk"],
                "date": date_entry["date"],
                "home_team_id": home["team"]["id"],
                "away_team_id": away["team"]["id"],
                "home_score": home_score,
                "away_score": away_score,
            })

    cache[cache_key] = games
    _save_cache(cache)
    return games


def fetch_lineups_for(games):
    """Hydrate each game with starter ids and lineup id lists from the boxscore."""
    cache = _load_cache()
    enriched = []
    for g in games:
        key = f"box::{g['game_id']}"
        if key in cache:
            box = cache[key]
        else:
            box = get_lineup(g["game_id"])
            cache[key] = box
        home = box.get("home", {})
        away = box.get("away", {})
        if not home.get("lineup_available") or not away.get("lineup_available"):
            continue
        if not home.get("starter_id") or not away.get("starter_id"):
            continue
        g2 = dict(g)
        g2["home_starter_id"] = home["starter_id"]
        g2["away_starter_id"] = away["starter_id"]
        g2["home_lineup_ids"] = [p["id"] for p in home["lineup"]]
        g2["away_lineup_ids"] = [p["id"] for p in away["lineup"]]
        enriched.append(g2)
    _save_cache(cache)
    return enriched


# ---------- offline scoring (no network in inner loop) ----------

def precompute_features(games):
    """Pull stats for each player/pitcher/team once, package raw normalized
    feature inputs so random search can recombine weights without re-fetching.
    Returns a list of feature dicts keyed by stat name (already blended +
    normalized to [0,1])."""
    print(f"  Precomputing features for {len(games)} games...")
    out = []
    cache = _load_cache()
    for i, g in enumerate(games, 1):
        if i % 25 == 0:
            print(f"    {i}/{len(games)}...")
        try:
            home_lineup = _player_features(g["home_lineup_ids"], g["away_starter_id"], split="home", cache=cache)
            away_lineup = _player_features(g["away_lineup_ids"], g["home_starter_id"], split="away", cache=cache)
            home_pitch = _pitcher_features(g["home_starter_id"], cache=cache)
            away_pitch = _pitcher_features(g["away_starter_id"], cache=cache)
            home_bullpen_era = _team_bullpen(g["home_team_id"], cache=cache)
            away_bullpen_era = _team_bullpen(g["away_team_id"], cache=cache)
            out.append({
                "game_id": g["game_id"],
                "date": g["date"],
                "home_team_id": g["home_team_id"],
                "away_team_id": g["away_team_id"],
                "home_won": 1 if g["home_score"] > g["away_score"] else 0,
                "home_lineup": home_lineup,
                "away_lineup": away_lineup,
                "home_pitch": home_pitch,
                "away_pitch": away_pitch,
                "home_bullpen_era": home_bullpen_era,
                "away_bullpen_era": away_bullpen_era,
                "park_factor": PARK_FACTORS.get(g["home_team_id"], 1.0),
            })
        except Exception as e:
            print(f"    skip game {g['game_id']}: {e}")
    _save_cache(cache)
    return out


def _player_features(player_ids, opposing_pitcher_id, split, cache):
    """Return list of dicts: each player's normalized feature values + bvp info."""
    players = []
    for pid in player_ids:
        feats = _hitter_normalized(pid, split, cache)
        if feats is None:
            continue
        bvp = _bvp(pid, opposing_pitcher_id, cache)
        feats["_bvp"] = bvp
        players.append(feats)
    return players


def _hitter_normalized(player_id, split, cache):
    key = f"hit::{player_id}::{split}"
    if key in cache:
        return cache[key]
    splits = get_hitting_splits(player_id, split)
    stats = splits if splits else get_hitting_stats(player_id)
    blended = blend_hitting(stats)
    if not blended:
        cache[key] = None
        return None
    norm = {}
    for stat, (low, high) in HIT_RANGES.items():
        norm[stat] = normalize(blended.get(stat, 0), low, high)
    cache[key] = norm
    return norm


def _pitcher_normalized(pitcher_id, cache):
    key = f"pitch::{pitcher_id}"
    if key in cache:
        return cache[key]
    stats = get_pitching_stats(pitcher_id)
    blended = blend_pitching(stats)
    if not blended:
        cache[key] = None
        return None
    norm = {}
    for stat, (low, high) in PITCH_RANGES.items():
        norm[stat] = normalize(blended.get(stat, 0), low, high)
    cache[key] = norm
    return norm


def _pitcher_features(pid, cache):
    return _pitcher_normalized(pid, cache) or {}


def _bvp(batter_id, pitcher_id, cache):
    key = f"bvp::{batter_id}::{pitcher_id}"
    if key in cache:
        return cache[key]
    bvp = get_batter_vs_pitcher(batter_id, pitcher_id)
    if bvp:
        cache[key] = {
            "obp": normalize(bvp["obp"], 0.250, 0.500),
            "slg": normalize(bvp["slg"], 0.300, 0.700),
        }
    else:
        cache[key] = None
    return cache[key]


def _team_bullpen(team_id, cache):
    key = f"bullpen::{team_id}"
    if key in cache:
        return cache[key]
    cache[key] = get_bullpen_era(team_id)
    return cache[key]


# ---------- scoring with given weights (no network) ----------

def score_with_weights(features, weights):
    """Run a prediction off the precomputed features for one game."""
    hit_w = weights["hit_weights"]
    pit_w = weights["pitch_weights"]
    bal = weights["balance"]
    bvp_w = bal.get("bvp_weight", 0.15)
    pf_w = bal.get("park_factor_weight", 0.05)

    def lineup_score(players):
        if not players:
            return 0.0
        total = 0
        for p in players:
            score = sum(p.get(stat, 0) * w for stat, w in hit_w.items())
            bvp = p.get("_bvp")
            if bvp is not None:
                bvp_score = bvp["obp"] * 0.5 + bvp["slg"] * 0.5
                score = score * (1 - bvp_w) + bvp_score * bvp_w
            total += score
        return total / len(players)

    def pitcher_score(p):
        if not p:
            return 0.0
        return sum(p.get(stat, 0) * w for stat, w in pit_w.items())

    home_hit = lineup_score(features["home_lineup"])
    away_hit = lineup_score(features["away_lineup"])
    home_pitch = pitcher_score(features["home_pitch"])
    away_pitch = pitcher_score(features["away_pitch"])
    home_bull = 1 - normalize(features["home_bullpen_era"], 2.5, 5.5)
    away_bull = 1 - normalize(features["away_bullpen_era"], 2.5, 5.5)

    off_w = bal.get("offense_weight", 0.50)
    pit_balance = bal.get("pitching_weight", 0.35)
    bull_w = bal.get("bullpen_weight", 0.08)
    form_w = bal.get("recent_form_weight", 0.05)

    home_score = (home_hit * off_w + (home_pitch + 0.5) * pit_balance
                  + home_bull * bull_w + 0.5 * form_w)
    away_score = (away_hit * off_w + (away_pitch + 0.5) * pit_balance
                  + away_bull * bull_w + 0.5 * form_w)
    pf = features["park_factor"]
    home_score *= (1 + (pf - 1) * pf_w * 10)
    away_score *= (1 - (pf - 1) * pf_w * 5)
    total = home_score + away_score
    if total == 0:
        return 0.5
    return home_score / total  # P(home wins)


def evaluate(features_list, weights):
    """Return {log_loss, accuracy, brier, n} on a precomputed feature set."""
    if not features_list:
        return {"log_loss": None, "accuracy": None, "brier": None, "n": 0}
    ll = 0.0
    bs = 0.0
    correct = 0
    for f in features_list:
        p = score_with_weights(f, weights)
        p = max(min(p, 0.999), 0.001)
        actual = f["home_won"]
        ll += -(actual * math.log(p) + (1 - actual) * math.log(1 - p))
        bs += (p - actual) ** 2
        if (p >= 0.5) == (actual == 1):
            correct += 1
    n = len(features_list)
    return {
        "log_loss": round(ll / n, 4),
        "accuracy": round(correct / n, 4),
        "brier": round(bs / n, 4),
        "n": n,
    }


# ---------- random search ----------

def _random_weights(seed_weights, jitter=0.6):
    """Sample weights by jittering the seed. Hit/pitch weights are normalized
    to keep magnitudes comparable; balance weights stay in [0, 1]."""
    rng = random
    new = {
        "hit_weights": {},
        "pitch_weights": {},
        "balance": {},
    }
    for k, v in seed_weights["hit_weights"].items():
        new["hit_weights"][k] = round(v + rng.uniform(-jitter, jitter) * abs(v + 0.05), 4)
    for k, v in seed_weights["pitch_weights"].items():
        new["pitch_weights"][k] = round(v + rng.uniform(-jitter, jitter) * abs(v + 0.05), 4)
    for k, v in seed_weights["balance"].items():
        nv = v + rng.uniform(-jitter, jitter) * max(v, 0.05)
        new["balance"][k] = round(max(0.0, min(1.0, nv)), 4)
    return new


def random_search(features_list, n_iter=200, seed=None, base_weights=None):
    """Run random search; return best weights, best metrics, baseline metrics, history."""
    if seed is not None:
        random.seed(seed)
    base = base_weights or {
        "hit_weights": dict(DEFAULT_HIT_WEIGHTS),
        "pitch_weights": dict(DEFAULT_PITCH_WEIGHTS),
        "balance": dict(DEFAULT_BALANCE),
    }
    baseline = evaluate(features_list, base)
    best = {"weights": base, "metrics": baseline}
    history = [{"iter": 0, "log_loss": baseline["log_loss"],
                "accuracy": baseline["accuracy"], "brier": baseline["brier"]}]
    print(f"  baseline: log_loss={baseline['log_loss']} acc={baseline['accuracy']}")

    for i in range(1, n_iter + 1):
        # Periodically restart from the current best to refine
        seed_w = best["weights"] if i % 20 == 0 else base
        candidate = _random_weights(seed_w, jitter=0.5)
        m = evaluate(features_list, candidate)
        if m["log_loss"] is not None and (best["metrics"]["log_loss"] is None or m["log_loss"] < best["metrics"]["log_loss"]):
            best = {"weights": candidate, "metrics": m}
            print(f"    iter {i}: NEW BEST log_loss={m['log_loss']} acc={m['accuracy']}")
        history.append({"iter": i, "log_loss": m["log_loss"],
                        "accuracy": m["accuracy"], "brier": m["brier"]})
    return {
        "best_weights": best["weights"],
        "best_metrics": best["metrics"],
        "baseline_metrics": baseline,
        "history": history,
        "iterations": n_iter,
    }


# ---------- top-level orchestration ----------

def run_backtest(start_date=None, end_date=None, weights=None):
    """Score the given weights (or current saved weights) over the date range."""
    if not start_date or not end_date:
        start_date, end_date = last_n_days_from_today(30)
    print(f"[backtest] {start_date} -> {end_date}")
    games = fetch_finals(start_date, end_date)
    print(f"  {len(games)} finals")
    games = fetch_lineups_for(games)
    print(f"  {len(games)} games with lineups")
    features = precompute_features(games)
    print(f"  {len(features)} games scorable")
    if weights is None:
        from app.services.predict import load_weights
        weights = load_weights()
    metrics = evaluate(features, weights)
    return {
        "start_date": start_date,
        "end_date": end_date,
        "metrics": metrics,
        "weights_used": weights,
    }


def run_tune_from_history(n_iter=200, apply=False, seed=42, min_games=20):
    """Tune weights against the user's own graded predictions.

    This is the most honest tuning signal you can get: every game in the
    training set is one where the user has actually predicted using the
    model and seen the real outcome. Returns the same shape as run_tune.
    """
    from app.services.tracking import list_predictions
    from app.services.mlb import TEAM_IDS

    graded = [p for p in list_predictions(status="graded", limit=10000)
              if p.get("game_id") and p.get("home_score") is not None
              and p.get("away_score") is not None]
    if len(graded) < min_games:
        return {
            "error": f"Need at least {min_games} graded predictions to tune; "
                     f"you have {len(graded)}. Predict more games and let them grade.",
            "graded_count": len(graded),
        }

    print(f"[tune-from-history] {len(graded)} graded predictions in DB")
    # Build game records compatible with precompute_features
    games = []
    skipped = 0
    for p in graded:
        home_id = TEAM_IDS.get((p.get("home_team") or "").upper())
        away_id = TEAM_IDS.get((p.get("away_team") or "").upper())
        if not home_id or not away_id:
            skipped += 1
            continue
        games.append({
            "game_id": p["game_id"],
            "date": p.get("game_date"),
            "home_team_id": home_id,
            "away_team_id": away_id,
            "home_score": p["home_score"],
            "away_score": p["away_score"],
        })

    print(f"[tune-from-history] {len(games)} games mapped to team IDs ({skipped} skipped)")
    games = fetch_lineups_for(games)
    print(f"[tune-from-history] {len(games)} games with lineups available")
    features = precompute_features(games)
    print(f"[tune-from-history] {len(features)} games scorable")

    if len(features) < min_games:
        return {
            "error": f"Could only build features for {len(features)} games "
                     f"(need {min_games}). Try predicting more recent games.",
            "scorable_games": len(features),
        }

    result = random_search(features, n_iter=n_iter, seed=seed)
    result["games_evaluated"] = len(features)
    result["source"] = "graded_history"
    _append_history(result)
    if apply:
        save_weights(result["best_weights"])
        result["applied"] = True
    return result


def run_tune(start_date=None, end_date=None, n_iter=200, apply=False, seed=42):
    """Run a backtest + random search; optionally save the best weights."""
    if not start_date or not end_date:
        start_date, end_date = last_n_days_from_today(30)
    print(f"[tune] {start_date} -> {end_date}, n_iter={n_iter}")
    games = fetch_finals(start_date, end_date)
    games = fetch_lineups_for(games)
    features = precompute_features(games)
    print(f"  scoring {len(features)} games per iteration")
    result = random_search(features, n_iter=n_iter, seed=seed)
    result["start_date"] = start_date
    result["end_date"] = end_date
    result["games_evaluated"] = len(features)
    _append_history(result)
    if apply:
        save_weights(result["best_weights"])
        result["applied"] = True
    return result


# ---------- file caches ----------

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
        json.dump(cache, f)


def _append_history(result):
    os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
    history = []
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r") as f:
                history = json.load(f)
        except Exception:
            history = []
    history.append({
        "ts": time.time(),
        "source": result.get("source", "date_range"),
        "start_date": result.get("start_date"),
        "end_date": result.get("end_date"),
        "iterations": result.get("iterations"),
        "games": result.get("games_evaluated"),
        "baseline_metrics": result.get("baseline_metrics"),
        "best_metrics": result.get("best_metrics"),
    })
    with open(HISTORY_FILE, "w") as f:
        json.dump(history[-50:], f, indent=2)


def clear_cache():
    if os.path.exists(CACHE_FILE):
        os.remove(CACHE_FILE)
    return {"status": "cleared"}

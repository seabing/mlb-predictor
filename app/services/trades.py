import json
import os

TRADES_FILE = "data/trades.json"

def _load():
    if not os.path.exists(TRADES_FILE):
        return []
    with open(TRADES_FILE, "r") as f:
        return json.load(f)

def _save(trades):
    with open(TRADES_FILE, "w") as f:
        json.dump(trades, f, indent=2)

def get_trades():
    return {"trades": _load()}

def add_trade(payload):
    trades = _load()
    trades.append({
        "player_id": payload["player_id"],
        "player_name": payload["player_name"],
        "from_team": payload["from_team"],
        "to_team": payload["to_team"]
    })
    _save(trades)
    return {"status": "ok", "trades": trades}

def reset_trades():
    _save([])
    return {"status": "reset"}
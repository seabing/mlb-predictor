"""Trade simulator routes."""
from __future__ import annotations

from fastapi import APIRouter, Request

from app.trades.services.store import trades_store

router = APIRouter()


@router.get("/trades")
def trades():
    return trades_store.list()


@router.post("/trades")
async def add_trade(request: Request):
    payload = await request.json()
    return trades_store.add(payload)


@router.delete("/trades")
def reset_trades():
    return trades_store.reset()

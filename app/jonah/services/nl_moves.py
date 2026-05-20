"""Natural-language roster moves — interpret a plain-English request into a
structured set of roster operations using Claude.

The user types something like "trade for a mid-rotation starter and drop $15M in
payroll." Claude receives the team, the current roster, and the request, and
returns structured JSON: which players to add, which to drop, an optional
payroll change, and a short reasoning summary. The caller (routes.py) then
resolves the names, applies the move, reruns the rating, and reports the impact.

Reuses the shared Claude client from Item 6 (app/core/llm.py).
"""
from __future__ import annotations

import json
import re

from app.core.llm import LLMError, call_claude


def _extract_json(text: str) -> dict:
    """Parse a JSON object out of Claude's reply, tolerating fences/prose."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def _build_prompt(team: str, roster: list[dict], message: str) -> str:
    hitters = [p["name"] for p in roster if p.get("position_type") != "Pitcher"]
    pitchers = [p["name"] for p in roster if p.get("position_type") == "Pitcher"]
    roster_block = (
        f"Position players: {', '.join(hitters) or '(none)'}\n"
        f"Pitchers: {', '.join(pitchers) or '(none)'}"
    )
    return (
        f"You are a baseball front-office assistant. The {team} have this roster:\n"
        f"{roster_block}\n\n"
        f'The user wants to make this move: "{message}"\n\n'
        "Translate the request into concrete roster operations. Rules:\n"
        "- For players to ACQUIRE, give the real full name of a specific MLB "
        "player who fits the description (e.g. a 'mid-rotation starter'). Pick a "
        "plausible, currently-active player.\n"
        "- For players to REMOVE, use the exact name from the roster above when "
        "the user names someone or implies who leaves.\n"
        "- If the move implies a payroll change, estimate it in millions of "
        "dollars (negative = payroll reduced).\n\n"
        "Respond with ONLY a JSON object, no markdown, in exactly this shape:\n"
        "{\n"
        '  "adds": [{"name": "Full Name", "why": "one short phrase"}],\n'
        '  "drops": [{"name": "Full Name"}],\n'
        '  "payroll_change_millions": <number or null>,\n'
        '  "summary": "one or two plain-English sentences describing the move and '
        'its intended effect"\n'
        "}"
    )


def interpret_move(team: str, roster: list[dict], message: str) -> dict:
    """Ask Claude to turn a plain-English move into structured operations.

    Returns a dict: {adds: [...], drops: [...], payroll_change_millions, summary}.

    Raises:
        ValueError: empty message.
        LLMError: Claude call failed or returned unparseable output.
    """
    message = (message or "").strip()
    if not message:
        raise ValueError("Please describe the roster move you want to explore.")

    raw = call_claude(
        _build_prompt(team, roster, message),
        system="You are a precise assistant that returns only valid JSON.",
        max_tokens=1024,
        temperature=0.3,
    )
    try:
        parsed = _extract_json(raw)
    except json.JSONDecodeError as e:
        raise LLMError(f"Claude did not return valid JSON: {e}") from e

    adds = [a for a in parsed.get("adds", []) if isinstance(a, dict) and a.get("name")]
    drops = [d for d in parsed.get("drops", []) if isinstance(d, dict) and d.get("name")]
    return {
        "adds": adds,
        "drops": drops,
        "payroll_change_millions": parsed.get("payroll_change_millions"),
        "summary": str(parsed.get("summary") or "").strip(),
    }

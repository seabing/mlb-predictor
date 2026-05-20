"""Natural-language model tuning (Item 6).

Takes a plain-English instruction from the user plus the current slider values,
asks Claude to propose updated weights, then validates and clamps the result so
the frontend can drop the values straight onto the sliders.

The slider ranges here MIRROR the ranges used by the frontend (static/index.html
renderSliders calls):
    hit_weights   -> [-0.5, 0.5]
    pitch_weights -> [-0.5, 0.5]
    balance       -> [ 0.0, 1.0]

Output contract returned to the route:
    {"weights": {"hit_weights": {...}, "pitch_weights": {...}, "balance": {...}},
     "summary": "<plain-English explanation of what changed and why>"}
"""
from __future__ import annotations

import json
import re

from app.core.llm import LLMError, call_claude
from app.predictions.services.weights import default_weights_dict, weights_store

# Slider ranges per group — must match the frontend.
GROUP_RANGES: dict[str, tuple[float, float]] = {
    "hit_weights": (-0.5, 0.5),
    "pitch_weights": (-0.5, 0.5),
    "balance": (0.0, 1.0),
}

# Human-readable labels so Claude understands what each key means.
LABELS: dict[str, str] = {
    # hitting
    "obp": "On-Base Percentage",
    "slg": "Slugging Percentage",
    "woba": "Weighted On-Base Average (wOBA)",
    "avg": "Batting Average",
    "iso": "Isolated Power (ISO)",
    "bb_pct": "Walk Rate (BB%)",
    "k_pct": "Strikeout Rate (K%) — lower is better",
    "babip": "BABIP",
    # pitching
    "fip": "Fielding Independent Pitching (FIP) — lower is better",
    "era": "Earned Run Average — lower is better",
    "whip": "WHIP — lower is better",
    "k9": "Strikeouts per 9 (K/9)",
    "k_bb_pct": "K%-BB%",
    "bb9": "Walks per 9 (BB/9) — lower is better",
    "gb_pct": "Ground Ball Rate",
    # balance
    "offense_weight": "Offense Weight",
    "pitching_weight": "Pitching Weight",
    "bullpen_weight": "Bullpen Weight",
    "recent_form_weight": "Recent Form Weight",
    "bvp_weight": "Batter vs Pitcher History Weight",
    "park_factor_weight": "Park Factor Weight",
}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _normalize_current(current: dict | None) -> dict:
    """Validate/repair the incoming current-weights structure.

    Falls back to the saved weights, then to defaults, for any missing group.
    """
    saved = weights_store.load()
    defaults = default_weights_dict()
    out: dict = {}
    for group in ("hit_weights", "pitch_weights", "balance"):
        base = (current or {}).get(group) or saved.get(group) or defaults[group]
        # Keep only the canonical keys for this group, coercing to float.
        canonical = defaults[group]
        out[group] = {}
        for key in canonical:
            try:
                out[group][key] = float(base.get(key, canonical[key]))
            except (TypeError, ValueError):
                out[group][key] = canonical[key]
    return out


def _build_prompt(current: dict, message: str) -> str:
    lines: list[str] = []
    for group, (low, high) in GROUP_RANGES.items():
        lines.append(f"\n{group} (allowed range {low} to {high}):")
        for key, val in current[group].items():
            label = LABELS.get(key, key)
            lines.append(f"  - {key} ({label}): current = {val:.3f}")
    weights_block = "\n".join(lines)

    return (
        "You are tuning the coefficients of an MLB win-probability model. The "
        "model combines hitting stats, pitching stats, and a set of balance "
        "weights. Below are the current slider values and the allowed range for "
        "each.\n"
        f"{weights_block}\n\n"
        f'The user wants the following: "{message}"\n\n'
        "Adjust the values to reflect the user's intent. Rules:\n"
        "- Only change values that the request implies; leave the rest as-is.\n"
        "- Every value MUST stay within its allowed range.\n"
        "- Keep changes sensible and proportional — do not zero everything out.\n"
        "- Some stats are negative because lower is better (e.g. ERA, WHIP, K%); "
        "keep their sign unless the user clearly wants otherwise.\n\n"
        "Respond with ONLY a JSON object, no markdown fences, no commentary, in "
        "exactly this shape:\n"
        "{\n"
        '  "hit_weights": { ...all hitting keys... },\n'
        '  "pitch_weights": { ...all pitching keys... },\n'
        '  "balance": { ...all balance keys... },\n'
        '  "summary": "one or two short sentences in plain English describing '
        'what you changed and why"\n'
        "}"
    )


def _extract_json(text: str) -> dict:
    """Parse a JSON object out of Claude's response, tolerating stray text/fences."""
    text = text.strip()
    # Strip ```json ... ``` fences if present.
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Fall back to the first {...} block.
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def _validate_weights(proposed: dict, current: dict) -> dict:
    """Keep only canonical keys, clamp to range, fill any missing from current."""
    out: dict = {}
    for group, (low, high) in GROUP_RANGES.items():
        out[group] = {}
        proposed_group = proposed.get(group) or {}
        for key, cur_val in current[group].items():
            raw = proposed_group.get(key, cur_val)
            try:
                val = float(raw)
            except (TypeError, ValueError):
                val = cur_val
            out[group][key] = round(_clamp(val, low, high), 4)
    return out


def nl_tune(message: str, current_weights: dict | None = None) -> dict:
    """Run a natural-language tuning request.

    Args:
        message: Plain-English instruction from the user.
        current_weights: The live slider values from the UI (optional).

    Returns:
        {"weights": {...validated...}, "summary": "..."}

    Raises:
        ValueError: if the message is empty.
        LLMError: if the Claude call fails or returns unparseable output.
    """
    message = (message or "").strip()
    if not message:
        raise ValueError("Please describe what you'd like to change.")

    current = _normalize_current(current_weights)
    prompt = _build_prompt(current, message)

    raw = call_claude(
        prompt,
        system="You are a precise assistant that returns only valid JSON.",
        max_tokens=1024,
        temperature=0.2,
    )

    try:
        parsed = _extract_json(raw)
    except json.JSONDecodeError as e:
        raise LLMError(f"Claude did not return valid JSON: {e}") from e

    weights = _validate_weights(parsed, current)
    summary = str(parsed.get("summary") or "").strip() or "Updated the weights based on your request."

    return {"weights": weights, "summary": summary}

"""Spotrac HTML parser — pure functions, no I/O.

Split out from the scraper so the parsing logic is unit-testable in
isolation. See `parse_contracts(html)` for the entry point.

Spotrac contract pages typically have one big table with columns:
  Player | Pos | Start Year | Type | Age At Signing | Start | End | Yrs | Value | AAV
"""
from __future__ import annotations

import re

CURRENT_SEASON = 2026


def normalize_name(name: str) -> str:
    """Lowercase + drop non-letters. Used as the match key against MLB names."""
    return re.sub(r"[^a-z]", "", (name or "").lower())


def _parse_money(s: str | None) -> int | None:
    if not s:
        return None
    cleaned = re.sub(r"[^\d.\-]", "", str(s))
    if not cleaned or cleaned == "-":
        return None
    try:
        return int(float(cleaned))
    except Exception:
        return None


def _parse_int(s: str | None) -> int | None:
    if s is None:
        return None
    s = str(s).strip()
    m = re.match(r"-?\d+", s)
    if not m:
        return None
    try:
        return int(m.group())
    except Exception:
        return None


def _clean_player_name(cell) -> str:
    """Spotrac stuffs a sort-key (last name) plus the anchor text into the
    player cell, so plain text reads like 'Judge Aaron Judge'. Prefer the
    anchor's text. Fall back to a 'first word equals last word' heuristic."""
    a = cell.find("a")
    if a:
        text = a.get_text(" ", strip=True)
        if text:
            return text
    text = cell.get_text(" ", strip=True)
    parts = text.split()
    while parts and (parts[0].isdigit() or parts[0] in ("#",)):
        parts.pop(0)
    if len(parts) >= 3 and parts[0].lower() == parts[-1].lower():
        parts = parts[1:]
    return " ".join(parts)


def parse_contracts(html: str) -> dict:
    """Extract {normalized_name: {name, salary, years_left, total_value,
    contract_end_year}} from a Spotrac contracts page."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    out: dict[str, dict] = {}

    for table in soup.find_all("table"):
        headers: list[str] = []
        thead = table.find("thead")
        if thead:
            headers = [
                th.get_text(" ", strip=True).lower()
                for th in thead.find_all(["th", "td"])
            ]
        if not headers:
            first_row = table.find("tr")
            if first_row:
                headers = [
                    c.get_text(" ", strip=True).lower()
                    for c in first_row.find_all(["th", "td"])
                ]
        if not headers:
            continue

        def find_idx(*needles, exclude=()):
            for i, h in enumerate(headers):
                if any(x in h for x in exclude):
                    continue
                for n in needles:
                    if n in h:
                        return i
            return -1

        name_idx = find_idx("player", "name")
        # AAV (average annual value) is what people read as "salary"
        salary_idx = find_idx(
            "aav", "average annual", "current salary",
            "base salary", "2026 base", "2026 salary",
        )
        total_idx = find_idx(
            "value", "total cash", "total contract",
            exclude=("aav", "average annual"),
        )
        end_idx = find_idx(
            "end", "fa year", "free agent", "expir",
            exclude=("start", "trend", "extend"),
        )

        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 3:
                continue
            try:
                name_cell = cells[name_idx] if 0 <= name_idx < len(cells) else cells[0]
                name = _clean_player_name(name_cell)
                if len(name) < 3:
                    continue

                salary = (_parse_money(cells[salary_idx].get_text(" ", strip=True))
                          if 0 <= salary_idx < len(cells) else None)
                total_value = (_parse_money(cells[total_idx].get_text(" ", strip=True))
                               if 0 <= total_idx < len(cells) and total_idx != salary_idx
                               else None)
                end_year = (_parse_int(cells[end_idx].get_text(" ", strip=True))
                            if 0 <= end_idx < len(cells) else None)

                years_left = None
                if end_year is not None:
                    years_left = max(0, end_year - CURRENT_SEASON)

                if salary is None and total_value is None and end_year is None:
                    continue

                key = normalize_name(name)
                if not key:
                    continue
                # Prefer rows that actually have salary data
                existing = out.get(key)
                if existing and existing.get("salary") and salary is None:
                    continue

                out[key] = {
                    "name": name,
                    "salary": salary,
                    "years_left": years_left,
                    "total_value": total_value,
                    "contract_end_year": end_year,
                }
            except Exception:
                continue
    return out

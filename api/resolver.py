"""Fuzzy-resolves a user's search query to a company row. See PRD v2
Section 9.3 and Design Handoff Screen 3.

Thresholds (inferred consistently across both docs — Handoff Screen 3 calls
the no-suggestions floor "score 50", and calls the not-found ceiling "no
match above score 70"):
  score >= 70        -> confirmed match
  50 <= score < 70    -> returned as a suggestion on a 404
  score < 50          -> not shown as a suggestion at all
"""
import sys
from pathlib import Path

from rapidfuzz import fuzz, process

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ingestion.entity_linker import clean_name  # noqa: E402

MATCH_THRESHOLD = 70
SUGGESTION_MIN = 50


def resolve_company(query: str, client) -> dict | None:
    """Returns the matched company row {id, name, ...} if the best fuzzy
    match scores >= MATCH_THRESHOLD, else None."""
    name_clean = clean_name(query)
    companies = client.table("companies").select("id,name,name_clean").execute().data or []
    if not companies:
        return None

    choices = {row["name_clean"]: row for row in companies}
    best = process.extractOne(name_clean, choices.keys(), scorer=fuzz.token_sort_ratio)
    if best and best[1] >= MATCH_THRESHOLD:
        return choices[best[0]]
    return None


def get_suggestions(query: str, client, limit: int = 3) -> list[dict]:
    """Top `limit` companies scoring in [SUGGESTION_MIN, MATCH_THRESHOLD),
    for the 404 "Did you mean?" list."""
    name_clean = clean_name(query)
    companies = client.table("companies").select("id,name,name_clean").execute().data or []

    scored = []
    for row in companies:
        score = fuzz.token_sort_ratio(name_clean, row["name_clean"])
        if SUGGESTION_MIN <= score < MATCH_THRESHOLD:
            scored.append((score, row))
    scored.sort(key=lambda pair: -pair[0])

    return [
        {"company_id": row["id"], "name": row["name"], "match_score": int(score)}
        for score, row in scored[:limit]
    ]

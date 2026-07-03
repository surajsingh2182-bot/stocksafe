"""Links company/director names extracted from SEBI orders to DB entities.
See PRD v2 Section 8.4."""
import re

from rapidfuzz import fuzz, process

MATCH_THRESHOLD = 85

_SUFFIX_RE = re.compile(
    r"\b(ltd|limited|pvt|private|llp|inc|incorporated|co|company|corp|corporation)\b\.?",
    re.IGNORECASE,
)
_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")


def clean_name(name: str) -> str:
    """lowercase, strip Ltd/Limited/Pvt/etc, remove punctuation, collapse whitespace."""
    name = name.lower()
    name = _SUFFIX_RE.sub("", name)
    name = _PUNCT_RE.sub(" ", name)
    name = _WS_RE.sub(" ", name).strip()
    return name


def find_or_create_company(name: str, client) -> int:
    """rapidfuzz score >= 85 against existing companies.name_clean = match
    existing row; below threshold = insert new company. Returns company_id."""
    name_clean = clean_name(name)
    existing = client.table("companies").select("id,name_clean").execute().data or []

    if existing:
        choices = {row["name_clean"]: row["id"] for row in existing}
        best = process.extractOne(name_clean, choices.keys(), scorer=fuzz.token_sort_ratio)
        if best and best[1] >= MATCH_THRESHOLD:
            return choices[best[0]]

    inserted = client.table("companies").insert({
        "name": name.strip(),
        "name_clean": name_clean,
    }).execute()
    return inserted.data[0]["id"]


def find_or_create_director(name: str, client) -> int:
    """Same matching pattern as find_or_create_company, against directors."""
    name_clean = clean_name(name)
    existing = client.table("directors").select("id,name_clean").execute().data or []

    if existing:
        choices = {row["name_clean"]: row["id"] for row in existing}
        best = process.extractOne(name_clean, choices.keys(), scorer=fuzz.token_sort_ratio)
        if best and best[1] >= MATCH_THRESHOLD:
            return choices[best[0]]

    inserted = client.table("directors").insert({
        "name": name.strip(),
        "name_clean": name_clean,
    }).execute()
    return inserted.data[0]["id"]


def link_director_to_company(director_id: int, company_id: int, role: str, source: str, client) -> None:
    """UPSERT into director_company_map on the (director_id, company_id) unique constraint."""
    client.table("director_company_map").upsert({
        "director_id": director_id,
        "company_id": company_id,
        "role": role,
        "source": source,
    }, on_conflict="director_id,company_id").execute()

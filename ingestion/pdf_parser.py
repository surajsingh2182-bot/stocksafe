"""Parses a downloaded SEBI order PDF into structured fields.
See PRD v2 Section 8.3.

Primary entity-extraction path: SEBI adjudication/enforcement orders open with
a "Noticee" table listing each party's name next to either a CIN (companies)
or a PAN (individuals) — e.g.:

    1
    Citrus Check Inns Limited
    U55101MH2011PLC222394
    2
    Omprakash Basantlal Goenka
    AECPG3854J

This is far more reliable than generic NER, so we parse it directly via the
CIN/PAN regexes below (verified against a real order PDF during ingestion
scraper development). spaCy NER (ORG/PERSON) is used only as a fallback for
orders that lack this table (e.g. prose-only single-entity orders).
"""
import re
import sys
from datetime import datetime
from pathlib import Path

import fitz  # PyMuPDF
import spacy

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from api.risk_scorer import classify_violation_type  # noqa: E402

_nlp = None  # lazy-loaded — spaCy model load is slow, only pay for it if needed

ORDER_NUMBER_RE = re.compile(
    r"ORDER\s*NO\.?\s*[-:\[]*\s*([A-Za-z0-9/\-]+)", re.IGNORECASE
)
DATE_RE = re.compile(
    r"Date\s*:\s*([A-Za-z]+\s+\d{1,2},?\s+\d{4})"
)
CIN_RE = re.compile(r"^[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}$")
PAN_RE = re.compile(r"^[A-Z]{5}\d{4}[A-Z]$")

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], start=1
)}


def _get_nlp():
    global _nlp
    if _nlp is None:
        _nlp = spacy.load("en_core_web_sm")
    return _nlp


def _parse_date(date_str: str):
    date_str = date_str.replace(",", "").strip()
    parts = date_str.split()
    if len(parts) != 3:
        return None
    month_name, day, year = parts
    month = MONTHS.get(month_name.lower())
    if not month:
        return None
    try:
        return datetime(int(year), month, int(day)).date()
    except ValueError:
        return None


def _extract_order_number(text: str) -> str | None:
    m = ORDER_NUMBER_RE.search(text)
    return m.group(1).strip() if m else None


def _extract_order_date(text: str):
    matches = DATE_RE.findall(text)
    if not matches:
        return None
    # The order's own signing date is the LAST "Date :" match (near the
    # signature block) — earlier "dated ..." references in the body refer to
    # other, historical orders being cited.
    return _parse_date(matches[-1])


def _extract_order_type(text: str) -> str:
    upper = text[:2000].upper()
    if "ADJUDICATING OFFICER" in upper:
        return "Adjudication Order"
    if "SETTLEMENT" in upper:
        return "Settlement Order"
    if "WHOLE TIME MEMBER" in upper:
        return "WTM Order"
    if "BOARD" in upper:
        return "Board Order"
    return "Enforcement Order"


def _extract_status(text: str, order_type: str) -> str:
    lower = text.lower()
    if order_type == "Settlement Order" or "consent terms" in lower or "consent application" in lower:
        return "consent_order"
    if "settlement order" in lower or "terms of settlement" in lower:
        return "settled"
    # "active" is the reasonable default for a freshly-published order — later
    # appeals aren't knowable from the order text itself at ingestion time.
    return "active"


def _extract_noticee_table(text: str) -> tuple[list[str], list[str]]:
    """Primary extraction path — see module docstring."""
    start = text.find("Name of Noticee")
    if start == -1:
        return [], []
    end_markers = ["aforesaid entities", "BACKGROUND", "In the matter of"]
    end = len(text)
    for marker in end_markers:
        idx = text.find(marker, start)
        if idx != -1:
            end = min(end, idx)
    table_text = text[start:end]

    lines = [ln.strip() for ln in table_text.splitlines() if ln.strip()]
    companies, directors = [], []
    for i, line in enumerate(lines):
        prev_line = lines[i - 1] if i > 0 else ""
        if CIN_RE.match(line) and prev_line:
            companies.append(prev_line)
        elif PAN_RE.match(line) and prev_line:
            directors.append(prev_line)
    return companies, directors


_REDACTED_RE = re.compile(r"x{2,}", re.IGNORECASE)
_COMPANY_SUFFIX_RE = re.compile(
    r"\b(Ltd|Limited|LLP|Pvt|Private|Inc|Incorporated|Corp|Corporation|Company|Co)\b\.?",
    re.IGNORECASE,
)
_PERSON_NAME_RE = re.compile(r"^[A-Z][a-zA-Z.]*(\s+[A-Z][a-zA-Z.]*){1,3}$")

# Legal/document boilerplate that is shaped like a name (2-4 title-case
# words) but isn't one — collected empirically by running the fallback NER
# path against real SEBI orders and inspecting what it produced. Not
# exhaustive; residual noise is a documented known limitation, not a bug to
# chase indefinitely — see README.
_BOILERPLATE_PHRASES = {
    "adjudication order", "final order", "interim order", "confirmatory order",
    "consent order", "settlement order", "settlement scheme", "board order",
    "wtm order", "counterparty order", "show cause", "show cause notice",
    "public notice", "hearing notice", "post scn intimation", "noticee nos",
    "record maintenance", "authorised representative", "centralised database",
    "emphasis supplied", "chapter xiv", "para a", "para b", "the board",
    "the company", "the noticee", "roc kolkata", "roc mumbai", "roc delhi",
}


def _looks_like_company(name: str) -> bool:
    """Real company names in SEBI orders almost always carry a legal suffix.
    Requiring one is a strong, deliberately conservative filter — verified
    necessary against a real order whose Noticee table wasn't found: without
    it, NER over legal boilerplate produced entries like "BOARD OF INDIA"
    and "Supreme Court" as fake companies."""
    if _REDACTED_RE.search(name) or any(ch.isdigit() for ch in name):
        return False
    if len(name.split()) < 2:  # e.g. a bare "Company" trivially contains the suffix word
        return False
    if name.strip().lower().startswith("the "):  # sentence fragment, not a proper noun
        return False
    if "noticee" in name.lower() or re.search(r"\bAct\b|\bVs\b", name, re.IGNORECASE):
        return False
    return bool(_COMPANY_SUFFIX_RE.search(name))


def _looks_like_person(name: str) -> bool:
    """2-4 title-case words, no digits, not a redacted "Axxxxx Bxxx" name
    (some settlement/RTI orders redact identities — real signal there is
    'no name available', not a garbled one), and not known document
    boilerplate that happens to be name-shaped."""
    if _REDACTED_RE.search(name) or any(ch.isdigit() for ch in name):
        return False
    if name.strip().lower() in _BOILERPLATE_PHRASES:
        return False
    return bool(_PERSON_NAME_RE.match(name.strip()))


def _extract_entities_via_ner(text: str) -> tuple[list[str], list[str]]:
    """Fallback path when no Noticee table is found. Deliberately strict —
    better to return nothing (order still gets inserted with entity_type
    "unknown") than to fabricate a fake company/director from NER noise."""
    nlp = _get_nlp()
    doc = nlp(text[:20000])  # cap for speed — entities are always near the top
    companies = sorted({
        ent.text.strip() for ent in doc.ents
        if ent.label_ == "ORG" and _looks_like_company(ent.text.strip())
    })
    directors = sorted({
        ent.text.strip() for ent in doc.ents
        if ent.label_ == "PERSON" and _looks_like_person(ent.text.strip())
    })
    return companies, directors


def parse_order_pdf(pdf_path: str) -> dict:
    """Returns dict with: order_number, order_date, order_type, status,
    violation_type, entity_type, company_names (list), director_names (list),
    raw_text (first 5000 chars). Caller (scraper.py) fills in pdf_url, since
    that's known from the download step, not the PDF content."""
    doc = fitz.open(pdf_path)
    full_text = "".join(page.get_text() for page in doc)
    doc.close()

    order_type = _extract_order_type(full_text)
    company_names, director_names = _extract_noticee_table(full_text)
    if not company_names and not director_names:
        company_names, director_names = _extract_entities_via_ner(full_text)

    entity_type = "company" if company_names else ("individual" if director_names else "unknown")

    return {
        "order_number": _extract_order_number(full_text),
        "order_date": _extract_order_date(full_text),
        "order_type": order_type,
        "status": _extract_status(full_text, order_type),
        "violation_type": classify_violation_type(full_text),
        "entity_type": entity_type,
        "company_names": company_names,
        "director_names": director_names,
        "raw_text": full_text[:5000],
    }

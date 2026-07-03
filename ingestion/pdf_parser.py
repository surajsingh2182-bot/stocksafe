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
from rapidfuzz import fuzz

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
MATTER_OF_RE = re.compile(
    r"in the matter of\s+([A-Za-z0-9&.,'\s]+?)(?:\s{2,}|\n\s*\n|Page\s+\d)",
    re.IGNORECASE,
)

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


def _extract_status(order_type: str) -> str:
    """order_type (from the document's own title/header) is a much stronger
    signal than scanning the body for settlement-related phrases — verified
    via a real bug: an order titled "Adjudication Order" (a genuine, active
    penalty) contained the sentence "...terms of settlement should be
    attractive so that it could attract the noticees..." — SEBI explaining
    its general Settlement Scheme *policy* as procedural background, not
    saying this case was settled. Scanning the whole body for that phrase
    wrongly flagged it "settled", cutting an 87 (High Risk) score to 29
    (Low Risk). SEBI only titles a document "Settlement Order" when a
    matter is actually settled, so order_type alone decides this now.
    "appealed" isn't derivable from the order text at all (it's a future
    event) — "active" is the correct default either way."""
    if order_type == "Settlement Order":
        return "settled"
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
    # Reject suffix-only fragments like "Private Limited" with no actual
    # name attached — verified necessary against a real order where a
    # mid-page-break line wrap ("...Ambition Plaza\nPage 2 of 14\nPrivate
    # Limited was converted to...") split a real company's name away from
    # its own suffix, leaving spaCy to extract the bare suffix as an ORG.
    core = _COMPANY_SUFFIX_RE.sub("", name).strip()
    if len(core) < 3:
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


CITATION_AFTER_RE = re.compile(
    r"^\s*\(?\s*(SAT|Civil|Criminal|Writ|Special Leave|SC|WP)\s*(Appeal|Petition|SLP)\s*No\.?",
    re.IGNORECASE,
)
CITATION_BEFORE_RE = re.compile(
    r"(SAT|Supreme Court|SC|Hon.ble)\s+(Order|Appeal)\b[\s\S]{0,150}in\s+the\s+matter\s+of\s*$",
    re.IGNORECASE,
)


def _is_citation_context(text: str, start_char: int, end_char: int) -> bool:
    """True if an entity is named only as a cited legal precedent, not a
    party to *this* order — e.g. "...relied upon the SAT Order ... in the
    matter of Apollo Tyres Limited (SAT Appeal No. 23 of 2019)". Verified
    necessary against a real order: a Noticee's legal defense cited an old,
    completely unrelated case involving Apollo Tyres Limited (a legitimate
    company with nothing to do with this order), and unfiltered NER swept
    it up as if it were an accused party — including a second mention of
    the same company later in the same paragraph that lacked the trailing
    "(SAT Appeal No. ...)" marker, so both directions are checked: what
    immediately follows the mention (the appeal-number pattern), and what
    precedes it (a "SAT/SC Order ... in the matter of" lead-in, which both
    of that real order's citations shared even where the trailing marker
    didn't repeat)."""
    after = text[end_char:end_char + 60]
    if CITATION_AFTER_RE.search(after):
        return True
    before = text[max(0, start_char - 200):start_char]
    return bool(CITATION_BEFORE_RE.search(before))


def _extract_entities_via_ner(text: str) -> tuple[list[str], list[str]]:
    """Fallback path when no Noticee table is found. Deliberately strict —
    better to return nothing (order still gets inserted with entity_type
    "unknown") than to fabricate a fake company/director from NER noise."""
    nlp = _get_nlp()
    window_text = text[:20000]  # cap for speed — entities are always near the top
    doc = nlp(window_text)
    companies = sorted({
        ent.text.strip() for ent in doc.ents
        if ent.label_ == "ORG" and _looks_like_company(ent.text.strip())
        and not _is_citation_context(window_text, ent.start_char, ent.end_char)
    })
    directors = sorted({
        ent.text.strip() for ent in doc.ents
        if ent.label_ == "PERSON" and _looks_like_person(ent.text.strip())
        and not _is_citation_context(window_text, ent.start_char, ent.end_char)
    })
    return companies, directors


def _promote_primary_company(company_names: list[str], text: str) -> list[str]:
    """Every order's title states which entity it's actually about
    ("Adjudication Order in the matter of X") — but the Noticee table can
    list several related companies (a parent + subsidiaries), in arbitrary
    order. Since the pipeline treats company_names[0] as the order's primary
    attributed company, blindly taking the table's first row can attribute
    the order to the wrong entity (found via a real search: an order titled
    "...in the matter of Prime Focus Limited" had "DNEG Creative Services
    Limited" — a subsidiary — listed first in the table). Fuzzy-match the
    title's hint against the extracted candidates and promote the best
    match to the front, rather than re-parsing the (messily line-wrapped)
    title text as a name directly."""
    if len(company_names) <= 1:
        return company_names

    match = MATTER_OF_RE.search(text)
    if not match:
        return company_names

    hint = match.group(1).strip()
    best_idx = max(range(len(company_names)), key=lambda i: fuzz.partial_ratio(hint.lower(), company_names[i].lower()))
    if best_idx == 0:
        return company_names
    return [company_names[best_idx]] + company_names[:best_idx] + company_names[best_idx + 1:]


SUMMARY_LENGTH = 2000


def _extract_summary(text: str, length: int = SUMMARY_LENGTH) -> str:
    """Every order opens with ~500-1000 chars of near-identical boilerplate
    (title, "BEFORE THE ADJUDICATING OFFICER", "UNDER SECTION 15-I...") —
    verified against real data that this pushed the actual violation
    narrative (which starts at the "BACKGROUND" section) past the old
    500-char summary cutoff entirely for some orders, leaving Gemini's
    red-flag generation with nothing but header text to work from for that
    company. Starting the summary at "BACKGROUND" when present skips the
    boilerplate and gets straight to the substance; falls back to the top
    of the document for the ~1 in 20 orders that use different section
    wording."""
    idx = text.upper().find("BACKGROUND")
    start = idx if idx != -1 else 0
    return text[start:start + length]


def parse_order_pdf(pdf_path: str) -> dict:
    """Returns dict with: order_number, order_date, order_type, status,
    violation_type, entity_type, company_names (list), director_names (list),
    summary (~2000 chars starting from the actual narrative, not the
    boilerplate header — see _extract_summary), raw_text (first 5000 chars).
    Caller (scraper.py) fills in pdf_url, since that's known from the
    download step, not the PDF content."""
    doc = fitz.open(pdf_path)
    full_text = "".join(page.get_text() for page in doc)
    doc.close()

    order_type = _extract_order_type(full_text)
    company_names, director_names = _extract_noticee_table(full_text)
    if not company_names and not director_names:
        company_names, director_names = _extract_entities_via_ner(full_text)
    company_names = _promote_primary_company(company_names, full_text)

    entity_type = "company" if company_names else ("individual" if director_names else "unknown")

    return {
        "order_number": _extract_order_number(full_text),
        "order_date": _extract_order_date(full_text),
        "order_type": order_type,
        "status": _extract_status(order_type),
        "violation_type": classify_violation_type(full_text),
        "entity_type": entity_type,
        "company_names": company_names,
        "director_names": director_names,
        "summary": _extract_summary(full_text),
        "raw_text": full_text[:5000],
    }

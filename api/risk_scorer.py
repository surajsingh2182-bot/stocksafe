"""Risk scoring for SEBI enforcement orders. See PRD v2 Section 9.4."""
from datetime import date

VIOLATION_SCORES = {
    "fraudulent_scheme":    45,
    "market_manipulation":  40,
    "circular_trading":     40,
    "insider_trading":      35,
    "front_running":        30,
    "disclosure_violation": 15,
    "default":              20,
}

RECENCY_MULTIPLIER = {"lt2": 1.3, "lt5": 1.0, "gte5": 0.7}
STATUS_MULTIPLIER = {"active": 1.5, "appealed": 1.3, "consent_order": 0.6, "settled": 0.5}
DIRECTOR_FACTOR = 0.8

# Scanned against an order's raw_text by ingestion/pdf_parser.py to classify
# violation_type. Checked in this priority order — first match wins, since a
# single order can mention several violation types in passing.
#
# "pfutp"/"fraudulent and unfair trade practice" were removed from
# fraudulent_scheme — real bug: PFUTP (Prohibition of Fraudulent and Unfair
# Trade Practices) is the *name of the general regulations* nearly every
# SEBI enforcement order is charged under, market-manipulation cases
# included — it says nothing about which *specific* misconduct occurred.
# Verified against real data: 41 of 64 orders in the DB were classified
# "fraudulent_scheme" (the highest-severity, highest-scoring category)
# almost entirely because they cited PFUTP as boilerplate, while the
# one order confirmed to be a genuine fraudulent scheme (a Ponzi-style
# collective investment scheme) never even mentions "pfutp" at all — it
# has actually distinctive language ("ponzi", "collective investment
# scheme", "mobilising funds") instead, which is what the keywords below
# now require.
VIOLATION_KEYWORDS = {
    "fraudulent_scheme": [
        "fraudulent scheme", "device, scheme or artifice to defraud",
        "ponzi", "collective investment scheme", "mobilising funds",
        "mobilizing funds", "misappropriat", "duped investors",
        "diverted the funds", "siphon",
    ],
    "market_manipulation": [
        "manipulat", "artificial price", "artificial volume", "pump and dump",
        "price rigging",
    ],
    "circular_trading": [
        "circular trading", "synchronised trades", "synchronized trades",
        "reciprocal trades", "circular transactions",
    ],
    "insider_trading": [
        "insider trading", "unpublished price sensitive information", "upsi",
        "prohibition of insider trading",
    ],
    "front_running": [
        "front running", "front-running", "frontrunning",
    ],
    "disclosure_violation": [
        "failure to disclose", "non-disclosure", "non disclosure",
        "sast regulations", "substantial acquisition of shares",
        "regulation 29", "disclosure requirement",
    ],
}


def classify_violation_type(raw_text: str) -> str:
    """Scan order text against VIOLATION_KEYWORDS; first match wins."""
    text = (raw_text or "").lower()
    for violation_type, keywords in VIOLATION_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return violation_type
    return "default"


def _recency_bucket(order_date: date) -> str:
    years = (date.today() - order_date).days / 365.25
    if years < 2:
        return "lt2"
    if years < 5:
        return "lt5"
    return "gte5"


def get_verdict(score: int) -> str:
    if score >= 60:
        return "high_risk"
    if score >= 30:
        return "caution"
    return "low_risk"


def calculate_score(orders: list[dict]) -> tuple[int, str]:
    """orders: list of dicts with keys violation_type, order_date (date),
    status, entity_type. Returns (risk_score, verdict)."""
    total = 0.0
    for order in orders:
        base = VIOLATION_SCORES.get(order["violation_type"], VIOLATION_SCORES["default"])
        recency_mult = RECENCY_MULTIPLIER[_recency_bucket(order["order_date"])]
        status_mult = STATUS_MULTIPLIER.get(order["status"], 1.0)
        entity_factor = DIRECTOR_FACTOR if order.get("entity_type") in ("individual", "director") else 1.0
        total += base * recency_mult * status_mult * entity_factor

    score = min(100, int(total))
    return score, get_verdict(score)

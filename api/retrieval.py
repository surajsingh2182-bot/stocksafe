"""Queries sebi_orders and stock_outcomes for a resolved company.
See PRD v2 Section 9.3."""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from api.risk_scorer import VIOLATION_SCORES  # noqa: E402

DECLINE_THRESHOLD_PCT = -70
OUTCOME_PERIOD_MONTHS = 12  # matches stock_outcomes.outcome_period_days default of 365


def _parse_date(value):
    if isinstance(value, str):
        return datetime.fromisoformat(value).date()
    return value


def get_orders_for_company(company_id: int, client) -> list[dict]:
    """Orders directly against the company, plus orders against directors
    linked to it via director_company_map (captures "director history" —
    e.g. a director previously penalised elsewhere)."""
    direct = client.table("sebi_orders").select("*").eq("company_id", company_id).execute().data or []

    director_rows = client.table("director_company_map").select("director_id").eq("company_id", company_id).execute().data or []
    director_ids = list({row["director_id"] for row in director_rows})

    director_orders = []
    if director_ids:
        director_orders = (
            client.table("sebi_orders").select("*")
            .in_("director_id", director_ids)
            .is_("company_id", "null")
            .execute().data or []
        )

    seen_ids = {order["id"] for order in direct}
    orders = direct + [o for o in director_orders if o["id"] not in seen_ids]

    for order in orders:
        order["order_date"] = _parse_date(order["order_date"])
    return orders


def get_primary_violation_type(orders: list[dict]) -> str | None:
    """Most frequent violation_type among the company's orders; ties broken
    by highest VIOLATION_SCORES weight (most serious first)."""
    if not orders:
        return None
    counts: dict[str, int] = {}
    for order in orders:
        vt = order["violation_type"]
        counts[vt] = counts.get(vt, 0) + 1
    max_count = max(counts.values())
    tied = [vt for vt, c in counts.items() if c == max_count]
    return max(tied, key=lambda vt: VIOLATION_SCORES.get(vt, VIOLATION_SCORES["default"]))


def get_pattern_stat(violation_type: str | None, client) -> str | None:
    """e.g. '8 of 10 similar stocks declined 70%+ within 12 months', based on
    stock_outcomes rows whose signal_combo includes this violation type."""
    if not violation_type:
        return None

    rows = client.table("stock_outcomes").select("signal_combo,price_change_pct").execute().data or []
    matching = [r for r in rows if violation_type in (r.get("signal_combo") or "").split(",")]
    if not matching:
        return None

    total = len(matching)
    declined = sum(
        1 for r in matching
        if r["price_change_pct"] is not None and r["price_change_pct"] <= DECLINE_THRESHOLD_PCT
    )
    return f"{declined} of {total} similar stocks declined {abs(DECLINE_THRESHOLD_PCT)}%+ within {OUTCOME_PERIOD_MONTHS} months"

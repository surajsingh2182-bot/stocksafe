"""StockSafe API. 10 endpoints: the 8 from PRD v2 Section 9.2 plus
GET /recent-searches and GET /example-companies (see plan resolution notes)."""
import os
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from supabase import create_client

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from api.gemini_client import get_ai_analysis  # noqa: E402
from api.resolver import get_suggestions, resolve_company  # noqa: E402
from api.retrieval import get_orders_for_company, get_pattern_stat, get_primary_violation_type  # noqa: E402
from api.risk_scorer import calculate_score  # noqa: E402

load_dotenv()

app = FastAPI(title="StockSafe API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])


class SearchRequest(BaseModel):
    query: str


class SetReminderRequest(BaseModel):
    company_id: int | None = None
    verdict: str
    risk_score: int
    planned_amount: float
    fire_at: str


class LogInvestmentRequest(BaseModel):
    company_id: int | None = None
    verdict: str
    risk_score: int
    amount_invested: float
    original_planned: float
    investment_type: str


class WatchlistRequest(BaseModel):
    company_id: int | None = None
    log_id: int


class CheckInRequest(BaseModel):
    outcome_pct: float


class RequestCompanyRequest(BaseModel):
    name: str


@app.get("/health")
def health():
    companies = supabase.table("companies").select("id", count="exact").execute()
    orders = supabase.table("sebi_orders").select("id", count="exact").execute()
    return {
        "db_connected": True,
        "total_companies": companies.count or 0,
        "total_orders": orders.count or 0,
    }


@app.get("/recent-searches")
def recent_searches():
    """Not in the PRD's 8-endpoint list, but the PRD's own Screen 1 code
    calls it (the Design Handoff has the frontend query Supabase directly
    instead — see plan resolution notes). Backed as a real endpoint here so
    the frontend never needs Supabase credentials."""
    now_iso = datetime.utcnow().isoformat()
    rows = (
        supabase.table("search_cache")
        .select("company_id,verdict,risk_score,cached_at,companies(name)")
        .gt("expires_at", now_iso)
        .order("cached_at", desc=True)
        .limit(5)
        .execute()
        .data or []
    )
    searches = [
        {
            "company_id": row["company_id"],
            "company_name": (row.get("companies") or {}).get("name", ""),
            "verdict": row["verdict"],
            "risk_score": row["risk_score"],
        }
        for row in rows
    ]
    return {"searches": searches}


@app.get("/example-companies")
def example_companies(count: int = 5):
    """Random example companies for Screen 1's "Try an example" pills.
    Not in the PRD (its examples — Satyam, Karvy, PC Jeweller — are
    illustrative and aren't in this dataset), added so the pills always
    point at companies that actually resolve to a real result. Only
    samples companies with at least one sebi_orders row, so every example
    is guaranteed to return a meaningful (non-empty) verdict."""
    order_rows = supabase.table("sebi_orders").select("company_id").execute().data or []
    company_ids = list({row["company_id"] for row in order_rows if row["company_id"]})
    if not company_ids:
        return {"companies": []}

    sample_ids = random.sample(company_ids, min(count, len(company_ids)))
    rows = supabase.table("companies").select("name").in_("id", sample_ids).execute().data or []
    return {"companies": [row["name"] for row in rows]}


@app.post("/search")
def search(body: SearchRequest):
    """Core endpoint. Order of operations follows PRD v2 Section 9.3 exactly:
    resolve -> orders -> cache check -> score -> Gemini -> pattern stat ->
    cache insert -> return."""
    query = body.query.strip()
    if not query:
        raise HTTPException(400, "query is required")

    company = resolve_company(query, supabase)
    if not company:
        suggestions = get_suggestions(query, supabase)
        return JSONResponse(status_code=404, content={"suggestions": suggestions})

    company_id = company["id"]
    orders = get_orders_for_company(company_id, supabase)

    now_iso = datetime.utcnow().isoformat()
    cached_rows = (
        supabase.table("search_cache").select("*")
        .eq("company_id", company_id).gt("expires_at", now_iso)
        .order("cached_at", desc=True).limit(1).execute().data
    )
    if cached_rows:
        cached = cached_rows[0]
        return {
            "company_name": company["name"],
            "company_id": company_id,
            "risk_score": cached["risk_score"],
            "verdict": cached["verdict"],
            "orders_found": len(orders),
            "red_flags": cached["red_flags"],
            "pattern_stat": cached["pattern_stat"],
            "tip_of_day": cached["tip_of_day"],
            "from_cache": True,
        }

    risk_score, verdict = calculate_score(orders)
    ai = get_ai_analysis(orders, company["name"])
    primary_violation = get_primary_violation_type(orders)
    pattern_stat = get_pattern_stat(primary_violation, supabase)

    supabase.table("search_cache").insert({
        "company_id": company_id,
        "risk_score": risk_score,
        "verdict": verdict,
        "red_flags": ai["red_flags"],
        "pattern_stat": pattern_stat,
        "tip_of_day": ai["tip_of_day"],
    }).execute()

    return {
        "company_name": company["name"],
        "company_id": company_id,
        "risk_score": risk_score,
        "verdict": verdict,
        "orders_found": len(orders),
        "red_flags": ai["red_flags"],
        "pattern_stat": pattern_stat,
        "tip_of_day": ai["tip_of_day"],
        "from_cache": False,
    }


@app.post("/set-reminder")
def set_reminder(body: SetReminderRequest):
    result = supabase.table("reminders").insert({
        "company_id": body.company_id,
        "verdict": body.verdict,
        "risk_score": body.risk_score,
        "planned_amount": body.planned_amount,
        "fire_at": body.fire_at,
    }).execute()
    row = result.data[0]
    return {"reminder_id": row["id"], "fire_at": row["fire_at"]}


@app.delete("/reminder/{reminder_id}")
def cancel_reminder(reminder_id: int):
    supabase.table("reminders").update({
        "cancelled_at": datetime.utcnow().isoformat(),
    }).eq("id", reminder_id).execute()
    return {"cancelled": True}


@app.post("/log-investment")
def log_investment(body: LogInvestmentRequest):
    check_in_date = (date.today() + timedelta(days=30)).isoformat()
    result = supabase.table("investment_log").insert({
        "company_id": body.company_id,
        "verdict": body.verdict,
        "risk_score": body.risk_score,
        "amount_invested": body.amount_invested,
        "original_planned": body.original_planned,
        "investment_type": body.investment_type,
        "check_in_date": check_in_date,
    }).execute()
    row = result.data[0]
    return {"log_id": row["id"], "check_in_date": check_in_date}


@app.post("/watchlist")
def add_to_watchlist(body: WatchlistRequest):
    supabase.table("watchlist").insert({
        "company_id": body.company_id,
        "log_id": body.log_id,
    }).execute()
    return {"added": True}


@app.patch("/investment/{investment_id}")
def check_in_investment(investment_id: int, body: CheckInRequest):
    supabase.table("investment_log").update({
        "outcome_pct": body.outcome_pct,
    }).eq("id", investment_id).execute()
    return {"updated": True}


@app.post("/request-company")
def request_company(body: RequestCompanyRequest):
    supabase.table("company_requests").insert({"name": body.name}).execute()
    return {"logged": True}

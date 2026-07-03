"""StockSafe Streamlit frontend — all 8 screens + router.
See PRD v2 Section 10 (source of truth for logic/navigation) and the Design
Handoff for visual tokens. Session-state navigation only — no page reloads."""
import os
import time
from datetime import datetime, timedelta

import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")

DECLINE_RATES = {"high_risk": 0.70, "caution": 0.50, "low_risk": 0.20}
VERDICT_LABELS = {"high_risk": "High Risk", "caution": "Caution", "low_risk": "Low Risk"}
VERDICT_ICONS = {"high_risk": "\U0001F534", "caution": "\U0001F7E1", "low_risk": "\U0001F7E2"}


def nav(screen, **kwargs):
    """Navigate to a screen and set session state keys."""
    st.session_state["screen"] = screen
    for k, v in kwargs.items():
        st.session_state[k] = v
    st.rerun()


def render_app_bar():
    c1, c2 = st.columns([3, 1])
    c1.markdown("## \U0001F6E1️ **Stock**Safe")
    c2.success("● SEBI Data")


def call_api(method, path, **kwargs):
    """Wrapper for all API calls. Returns (data, status_code)."""
    try:
        url = f"{API_BASE_URL}{path}"
        resp = requests.request(method, url, timeout=35, **kwargs)
        return resp.json(), resp.status_code
    except requests.exceptions.Timeout:
        return {"error": "timeout"}, 504
    except Exception as e:
        return {"error": str(e)}, 500


# ---------------------------------------------------------------------------
# Screen 1 — Home / Search
# ---------------------------------------------------------------------------
def screen_home():
    render_app_bar()
    st.caption("Know before you invest. Check any penny stock for SEBI fraud orders.")
    st.write("")

    query = st.text_input(
        "",
        placeholder="Company name or paste a WhatsApp tip...",
        label_visibility="collapsed",
        key="search_query",
    )

    if st.button("Check Stock  →", use_container_width=True, type="primary"):
        if query.strip():
            nav("loading", pending_query=query.strip())
        else:
            st.warning("Enter a company name or paste a tip first.")

    st.caption("TRY AN EXAMPLE")

    def _fill_example(val):
        # Setting session_state for a key already bound to an instantiated
        # widget raises StreamlitAPIException — must go through an on_click
        # callback (runs before the widget re-instantiates), not a plain
        # `if button: ...` body.
        st.session_state["search_query"] = val

    # Fetched once per session (not on every rerun) so the pills don't
    # reshuffle every time you interact with something else on this screen.
    # Pulled from the real dataset — the PRD's own examples (Satyam, Karvy,
    # PC Jeweller) are illustrative and aren't actually in this database.
    if "example_companies" not in st.session_state:
        data, status = call_api("GET", "/example-companies", params={"count": 5})
        # A few scraped names have stray newlines from PDF line-wrapping
        # (e.g. "BSE \nLimited") — collapse whitespace for a clean pill.
        st.session_state["example_companies"] = (
            [" ".join(name.split()) for name in data.get("companies", [])] if status == 200 else []
        )

    examples = st.session_state["example_companies"]
    if examples:
        cols = st.columns(len(examples))
        for col, name in zip(cols, examples):
            label = name if len(name) <= 20 else name[:17] + "..."
            col.button(label, key=f"pill_{name}", on_click=_fill_example, args=(name,))

    st.divider()

    try:
        data, status = call_api("GET", "/recent-searches")
        if status == 200 and data.get("searches"):
            st.caption("RECENT SEARCHES")
            for row in data["searches"]:
                c1, c2 = st.columns([3, 1])
                if c1.button(row["company_name"], key=f"recent_{row['company_id']}"):
                    nav("loading", pending_query=row["company_name"])
                c2.markdown(f"{VERDICT_ICONS[row['verdict']]} {VERDICT_LABELS[row['verdict']]}")
            st.divider()
    except Exception:
        pass  # recent searches are non-critical — fail silently

    data, _ = call_api("GET", "/health")
    s1, s2, s3 = st.columns(3)
    s1.metric("SEBI Orders", f"{data.get('total_orders', '–'):,}" if isinstance(data.get("total_orders"), int) else "–")
    s2.metric("Companies", f"{data.get('total_companies', '–'):,}" if isinstance(data.get("total_companies"), int) else "–")
    s3.metric("Updated", "Daily")

    with st.expander("How does StockSafe work?"):
        st.write("1. Search any company or paste a WhatsApp tip")
        st.write("2. AI scans SEBI enforcement orders and director history")
        st.write("3. Get a verdict with red flags, source links, and worst-case loss in rupees")

    st.caption("Research tool only — not financial advice. Consult a SEBI-registered advisor.")


# ---------------------------------------------------------------------------
# Screen 2 — Loading / Scanning
# Critical: POST /search fires HERE, not on Screen 1. Screen 1 only sets
# pending_query — this avoids double-submission when Streamlit re-renders.
# ---------------------------------------------------------------------------
def screen_loading():
    query = st.session_state.get("pending_query", "")
    render_app_bar()
    st.caption("Scanning")
    st.markdown(f"### {query}")
    st.caption("Takes about 15 seconds — Render may be waking up")

    progress = st.progress(0, text="Step 1 of 3 — Checking SEBI enforcement orders...")
    cancel = st.empty()

    if cancel.button("✕  Cancel search"):
        for k in ["pending_query", "result", "suggestions"]:
            st.session_state.pop(k, None)
        nav("home")

    progress.progress(15, text="Step 1 of 3 — Checking SEBI enforcement orders...")
    time.sleep(2)
    progress.progress(40, text="Step 2 of 3 — Analysing director history...")

    data, status = call_api("POST", "/search", json={"query": query})

    progress.progress(80, text="Step 3 of 3 — Calculating risk score...")
    time.sleep(0.5)
    progress.progress(100, text="Done")

    if status == 200:
        st.session_state["result"] = data
        st.session_state.pop("pending_query", None)
        nav("verdict")
    elif status == 404:
        st.session_state["suggestions"] = data.get("suggestions", [])
        nav("not_found")
    elif status == 504:
        nav("error", error_msg="Request timed out. Render may be waking up — please try again in 30 seconds.")
    else:
        nav("error", error_msg=f"Unexpected error (status {status}). Please try again.")


# ---------------------------------------------------------------------------
# Screen 3 — Company Not Found
# ---------------------------------------------------------------------------
def screen_not_found():
    query = st.session_state.get("pending_query", "")
    suggestions = st.session_state.get("suggestions", [])
    render_app_bar()

    if st.button("‹  Search again"):
        nav("home")

    st.caption(f'No exact match for "{query}"')
    st.markdown("### Did you mean one of these?")

    if not suggestions:
        st.info("No close matches found. Try the full registered company name.")
    else:
        for s in suggestions:
            c1, c2 = st.columns([3, 1])
            c1.markdown(f"**{s['name']}**")
            c1.caption(f"{s['match_score']}% match")
            if s.get("verdict"):
                c2.markdown(f"{VERDICT_ICONS[s['verdict']]} {VERDICT_LABELS[s['verdict']]}")
            if st.button("Check this company  ›", key=f"sug_{s['company_id']}", use_container_width=True):
                nav("loading", pending_query=s["name"])

    st.divider()

    retry = st.text_input("Search again...", key="retry_query")
    if st.button("Check Stock  →", use_container_width=True, type="primary"):
        if retry.strip():
            nav("loading", pending_query=retry.strip())
        else:
            st.warning("Enter a company name first.")

    if st.button("Request a company  →", use_container_width=True):
        call_api("POST", "/request-company", json={"name": query})
        st.success("Request logged. We will add this company in the next update.")


# ---------------------------------------------------------------------------
# Screen 4 — Risk Verdict
# ---------------------------------------------------------------------------
def screen_verdict():
    r = st.session_state.get("result", {})
    verdict = r.get("verdict", "low_risk")
    score = r.get("risk_score", 0)
    render_app_bar()

    if r.get("from_cache"):
        st.caption("⚡ Cached result")

    score_colours = {"high_risk": "#F87171", "caution": "#FCD34D", "low_risk": "#4ADE80"}
    st.markdown(f"""
        <div style="text-align:center;padding:20px 0">
          <div style="font-size:52px;font-weight:800;color:{score_colours[verdict]}">{score}</div>
          <div style="font-size:16px;font-weight:700;color:{score_colours[verdict]}">{VERDICT_LABELS[verdict]}</div>
          <div style="font-size:13px;color:#94A3B8">{r.get("company_name", "")}
            &nbsp;·&nbsp; Based on {r.get("orders_found", 0)} SEBI orders</div>
        </div>
    """, unsafe_allow_html=True)

    st.markdown("**Top Risk Signals**")
    flags = r.get("red_flags", [])
    if not flags:
        st.info("No enforcement orders found for this company in the SEBI database.")
    for i, flag in enumerate(flags[:3]):
        with st.expander(f"\U0001F6A9 {flag['text'][:70]}...", expanded=(i == 0)):
            st.write(flag["text"])
            st.caption(f"Source: {flag['source']}")

    if r.get("pattern_stat"):
        st.info(f"\U0001F4CA {r['pattern_stat']}")

    if r.get("tip_of_day"):
        st.success(f"\U0001F4A1 {r['tip_of_day']}")

    cta = {
        "high_risk": "See how much you could lose  →",
        "caution": "Calculate your safe position  →",
        "low_risk": "Plan your investment amount  →",
    }
    if st.button(cta[verdict], use_container_width=True, type="primary"):
        nav("sizing")


# ---------------------------------------------------------------------------
# Screen 5 — Position Sizing (client-side calculation only, no API calls)
# ---------------------------------------------------------------------------
def screen_sizing():
    r = st.session_state.get("result", {})
    verdict = r.get("verdict", "caution")
    name = r.get("company_name", "this company")
    render_app_bar()

    st.markdown(f"### How much were you planning to invest in {name}?")

    if "planned_amount" not in st.session_state:
        st.session_state["planned_amount"] = 10000

    def _fill_amount(val):
        st.session_state["planned_amount"] = val

    pills = [("5K", 5000), ("10K", 10000), ("25K", 25000), ("50K", 50000), ("1L", 100000)]
    cols = st.columns(5)
    for col, (label, val) in zip(cols, pills):
        col.button(f"₹{label}", key=f"pill_{val}", on_click=_fill_amount, args=(val,))

    # No `value=` here — the key="planned_amount" already binds this widget
    # to session_state; passing both raises a Streamlit warning about
    # conflicting sources of truth.
    planned = st.number_input(
        "Amount (₹)",
        min_value=1000, max_value=10000000,
        step=1000, key="planned_amount",
    )

    rate = DECLINE_RATES[verdict]
    worst = int(planned * rate)
    suggested = int(planned * (1 - rate))
    protected = planned - suggested

    st.error(f"⚠️  Worst-case loss: **₹{worst:,}** ({int(rate * 100)}% of ₹{planned:,} based on similar stocks)")

    c1, c2 = st.columns(2)
    c1.metric("Suggested safe amount", f"₹{suggested:,}")
    c2.metric("Capital protected", f"₹{protected:,}")

    st.divider()

    if st.button(f"Invest ₹{suggested:,} (adjusted amount)", use_container_width=True, type="primary"):
        nav("tracking", investment_amount=suggested, investment_type="adjusted", original_planned=planned)

    if st.button("⏰  Set 48-hr reminder", use_container_width=True):
        # planned_amount is already in session_state — it's the number_input's
        # own widget key above, so re-setting it here would hit the same
        # "can't modify after widget instantiated" error as the pill buttons.
        nav("reminder")

    st.markdown("---")
    st.caption("Still want to invest the full amount?")
    if st.button(f"Invest ₹{planned:,} anyway", use_container_width=True):
        nav("ack", investment_amount=planned, investment_type="full", original_planned=planned)


# ---------------------------------------------------------------------------
# Screen 6 — 48-hr Reminder
# ---------------------------------------------------------------------------
def screen_reminder():
    r = st.session_state.get("result", {})
    planned = st.session_state.get("planned_amount", 0)
    render_app_bar()

    if not st.session_state.get("reminder_id"):
        fire_at = (datetime.utcnow() + timedelta(hours=48)).isoformat()
        data, status = call_api("POST", "/set-reminder", json={
            "company_id": r.get("company_id"),
            "verdict": r.get("verdict"),
            "risk_score": r.get("risk_score"),
            "planned_amount": planned,
            "fire_at": fire_at,
        })
        if status == 200:
            st.session_state["reminder_id"] = data["reminder_id"]
        else:
            st.error("Could not save reminder — please try again.")
            if st.button("Back"):
                nav("sizing")
            return

    st.success("✓  Reminder set")
    st.markdown(
        f"**{r.get('company_name', '')}** · {VERDICT_ICONS[r.get('verdict', 'low_risk')]} "
        f"{VERDICT_LABELS[r.get('verdict', 'low_risk')]} · {r.get('risk_score', 0)}/100"
    )

    st.markdown("---")
    st.markdown("**Now** — reminder saved")
    st.markdown("*In 48 hours* — you get a notification")
    st.markdown("*Then* — review the verdict before you decide")
    st.markdown("---")

    score = r.get("risk_score", 0)
    label = VERDICT_LABELS.get(r.get("verdict", ""), "")
    st.info(
        f"\U0001F4F1 Notification preview: StockSafe — Time to review {r.get('company_name', '')} "
        f"— {label} {score}/100. Tap to see red flags before you invest."
    )

    if st.button("Cancel reminder", use_container_width=True):
        rid = st.session_state.get("reminder_id")
        if rid:
            call_api("DELETE", f"/reminder/{rid}")
        st.session_state.pop("reminder_id", None)
        nav("sizing")


# ---------------------------------------------------------------------------
# Screen 7 — Full Amount Acknowledgement (friction gate)
# ---------------------------------------------------------------------------
def screen_ack():
    r = st.session_state.get("result", {})
    verdict = r.get("verdict", "caution")
    planned = st.session_state.get("investment_amount", 0)
    worst = int(planned * DECLINE_RATES[verdict])
    render_app_bar()

    if st.button("‹  Back to position sizing"):
        nav("sizing")

    st.markdown("## Before you invest the full amount")

    c1, c2 = st.columns(2)
    c1.metric("Risk Score", f"{r.get('risk_score', 0)}/100")
    c2.metric("Verdict", VERDICT_LABELS.get(verdict, ""))

    st.error(f"Worst-case loss on ₹{planned:,}: **₹{worst:,} gone** ({int(DECLINE_RATES[verdict] * 100)}% based on similar stocks)")

    st.info(f"I understand I am choosing to invest Rs.{planned:,} despite a {VERDICT_LABELS[verdict]} risk rating.")

    typed = st.text_input(
        "",
        placeholder="Type INVEST (all caps) to continue",
        help="Must type exactly: INVEST (all caps)",
        label_visibility="collapsed",
        key="invest_confirm",
    )
    st.caption("Must type exactly: INVEST (all caps)")

    confirmed = typed.strip() == "INVEST"

    if st.button(
        f"Proceed with Rs.{planned:,}",
        use_container_width=True,
        type="primary" if confirmed else "secondary",
        disabled=not confirmed,
    ):
        nav("tracking")


# ---------------------------------------------------------------------------
# Screen 8 — Outcome Tracking
# ---------------------------------------------------------------------------
def screen_tracking():
    r = st.session_state.get("result", {})
    amount = st.session_state.get("investment_amount", 0)
    original = st.session_state.get("original_planned", amount)
    inv_type = st.session_state.get("investment_type", "adjusted")
    render_app_bar()

    if not st.session_state.get("log_id"):
        data, status = call_api("POST", "/log-investment", json={
            "company_id": r.get("company_id"),
            "verdict": r.get("verdict"),
            "risk_score": r.get("risk_score"),
            "amount_invested": amount,
            "original_planned": original,
            "investment_type": inv_type,
        })
        if status == 200:
            st.session_state["log_id"] = data["log_id"]
            st.session_state["check_in_date"] = data["check_in_date"]
        else:
            st.warning("Could not log investment — your analysis is still saved.")

    st.success("✓  Investment logged")

    tab_logged, tab_checkin = st.tabs(["Logged", "Check-in (30 days)"])

    with tab_logged:
        c1, c2 = st.columns(2)
        c1.metric("Invested", f"Rs.{amount:,}")
        c2.metric("Original plan", f"Rs.{original:,}")

        if inv_type == "adjusted" and amount < original:
            protected = original - amount
            st.success(f"You protected Rs.{protected:,} by adjusting your position")

        check_in = st.session_state.get("check_in_date", "30 days from now")
        st.caption(f"We will check in on {check_in}")
        st.divider()

        if st.button("Add to watchlist", use_container_width=True, type="primary"):
            call_api("POST", "/watchlist", json={
                "company_id": r.get("company_id"),
                "log_id": st.session_state.get("log_id"),
            })
            st.success("Added to watchlist.")

        if st.button("Share result", use_container_width=True):
            share = (
                f"I checked {r.get('company_name', '')} on StockSafe before investing. "
                f"Score: {r.get('risk_score', 0)}/100 ({VERDICT_LABELS.get(r.get('verdict', ''), '')}). "
                f"Check it yourself: stocksafe.app"
            )
            st.code(share)
            st.caption("Copy the text above to share")

    with tab_checkin:
        check_in = st.session_state.get("check_in_date", "your check-in date")
        st.info(f"Check-in form unlocks on {check_in}. Come back to record how {r.get('company_name', '')} performed.")
        st.markdown("When the form unlocks, you will see:")
        st.write("- Did the stock go up, flat, or down?")
        st.write("- Approximate % change")
        st.write("- Your experience: was StockSafe right?")


# ---------------------------------------------------------------------------
# Error screen
# ---------------------------------------------------------------------------
def screen_error():
    render_app_bar()
    st.error("Something went wrong.")
    st.write(st.session_state.get("error_msg", "Service temporarily unavailable."))
    if st.button("Try again", use_container_width=True, type="primary"):
        for k in ["error_msg", "pending_query", "result", "suggestions"]:
            st.session_state.pop(k, None)
        nav("home")


# ---------------------------------------------------------------------------
# Router and entry point
# ---------------------------------------------------------------------------
SCREENS = {
    "home": screen_home,
    "loading": screen_loading,
    "not_found": screen_not_found,
    "verdict": screen_verdict,
    "sizing": screen_sizing,
    "reminder": screen_reminder,
    "ack": screen_ack,
    "tracking": screen_tracking,
    "error": screen_error,
}


def main():
    if "screen" not in st.session_state:
        st.session_state["screen"] = "home"
    SCREENS.get(st.session_state["screen"], screen_home)()


if __name__ == "__main__":
    st.set_page_config(page_title="StockSafe", layout="centered", initial_sidebar_state="collapsed")
    main()

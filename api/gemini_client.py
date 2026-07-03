"""Gemini-generated red flags + tip of the day. See PRD v2 Section 9.5."""
import json
import os

import google.generativeai as genai

PROMPT_TEMPLATE = """
You are a financial risk analyst. Company: {company_name}
SEBI enforcement orders: {orders_text}

Return ONLY valid JSON:
{{
  "red_flags": [
    {{"text": "20-30 word plain language flag naming violation, person, year",
     "source": "SEBI Order #EO/YYYY/NNNN, DD Mon YYYY"}}
  ],
  "tip_of_day": "25-35 word actionable investing lesson from these orders"
}}

Write exactly 3 red flags (or fewer if fewer orders exist).
No jargon. Name the violation and the year. Plain language only.
"""


def _format_orders_text(orders: list[dict]) -> str:
    if not orders:
        return "No enforcement orders found for this company."
    lines = []
    for order in orders:
        order_date = order["order_date"]
        date_str = order_date.strftime("%d %b %Y") if hasattr(order_date, "strftime") else str(order_date)
        # summary now starts at the order's "BACKGROUND" section rather than
        # the boilerplate header (see ingestion/pdf_parser.py's
        # _extract_summary) — a 200-char slice of it was verified to still
        # be too short to reach any actual violation narrative for some
        # orders, leaving Gemini nothing but header text to describe.
        lines.append(
            f"- {order['violation_type']} | Order #{order['order_number']} | {date_str} | "
            f"{(order.get('summary') or '')[:1200]}"
        )
    return "\n".join(lines)


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    return json.loads(text)


def get_ai_analysis(orders: list[dict], company_name: str) -> dict:
    """Returns {"red_flags": [...], "tip_of_day": "..."}. Retries once on
    invalid JSON, and falls back to an empty-but-valid shape on ANY failure
    (bad JSON, or the API call itself raising) so a flaky Gemini response —
    or a free-tier quota/network error — never 500s the /search endpoint.
    Verified necessary against a real production 500: a rolling alias
    silently pointed at a model with only a 20-requests/day free quota, and
    the resulting ResourceExhausted exception from generate_content() was
    completely uncaught, propagating all the way to the endpoint."""
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    # PRD pinned "gemini-1.5-flash", which Google has since retired entirely.
    # Tried "gemini-flash-latest" (a rolling alias) next, but that caused a
    # real outage: the alias silently resolved to "gemini-3.5-flash", whose
    # free tier allows only 20 requests/day — a fraction of what a rolling
    # "latest" alias implies, with no warning before it broke. Pinned to a
    # specific, verified-working lightweight model instead — "-lite" models
    # are positioned for higher-throughput free-tier use, and pinning trades
    # the (known, occasional, controllable) need to update this on future
    # deprecation for the (invisible, uncontrollable) risk of alias drift.
    model = genai.GenerativeModel("gemini-2.5-flash-lite")
    prompt = PROMPT_TEMPLATE.format(
        company_name=company_name,
        orders_text=_format_orders_text(orders),
    )

    for _attempt in range(2):
        try:
            response = model.generate_content(prompt)
            data = _extract_json(response.text)
        except Exception:
            continue
        if "red_flags" in data and "tip_of_day" in data:
            return data

    return {"red_flags": [], "tip_of_day": ""}

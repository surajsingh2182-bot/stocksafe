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
    invalid JSON; falls back to an empty-but-valid shape if both attempts
    fail, so a flaky Gemini response never 500s the /search endpoint."""
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    # PRD pinned "gemini-1.5-flash", which Google has since retired entirely
    # (confirmed via ListModels against a real key — not in the available
    # set at all). "gemini-flash-latest" is a rolling alias to the current
    # recommended flash model, which avoids this exact breakage recurring.
    model = genai.GenerativeModel("gemini-flash-latest")
    prompt = PROMPT_TEMPLATE.format(
        company_name=company_name,
        orders_text=_format_orders_text(orders),
    )

    for _attempt in range(2):
        response = model.generate_content(prompt)
        try:
            data = _extract_json(response.text)
        except (json.JSONDecodeError, AttributeError):
            continue
        if "red_flags" in data and "tip_of_day" in data:
            return data

    return {"red_flags": [], "tip_of_day": ""}

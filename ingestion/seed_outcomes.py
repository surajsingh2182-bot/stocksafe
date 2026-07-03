"""Seeds stock_outcomes with 20 historical outcome rows used for the
"pattern_stat" feature (e.g. "8 of 10 similar stocks declined 70%+..."). See
PRD v2 Section 8.5. company_id is left NULL — these are aggregate historical
patterns, not tied to a specific company row."""
import os

# (signal_combo, had_director_order, had_auditor_change, price_change_pct)
SEED_ROWS = [
    ("fraudulent_scheme", True, True, -91),
    ("market_manipulation", True, False, -72),
    ("fraudulent_scheme", True, False, -79),
    ("market_manipulation", False, False, -45),
    ("insider_trading", True, False, -61),
    ("market_manipulation", True, True, -88),
    ("insider_trading", False, False, -38),
    ("fraudulent_scheme", False, False, -55),
    ("circular_trading", True, False, -68),
    ("circular_trading", False, False, -41),
    ("disclosure_violation", False, False, -18),
    ("disclosure_violation", True, False, -29),
    ("front_running", False, False, -33),
    ("market_manipulation,insider_trading", True, True, -94),
    ("market_manipulation,circular_trading", True, False, -82),
    ("fraudulent_scheme,disclosure_violation", True, False, -71),
    ("disclosure_violation", False, False, -12),
    ("front_running", True, False, -52),
    ("market_manipulation", False, True, -63),
    ("insider_trading", True, True, -77),
]


def seed_outcomes(client) -> int:
    rows = [
        {
            "company_id": None,
            "signal_combo": combo,
            "had_director_order": had_director_order,
            "had_auditor_change": had_auditor_change,
            "price_change_pct": price_change_pct,
            "outcome_period_days": 365,
            "data_source": "manual_seed",
        }
        for combo, had_director_order, had_auditor_change, price_change_pct in SEED_ROWS
    ]
    client.table("stock_outcomes").insert(rows).execute()
    return len(rows)


if __name__ == "__main__":
    from dotenv import load_dotenv
    from supabase import create_client

    load_dotenv()
    supabase_client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    count = seed_outcomes(supabase_client)
    print(f"Inserted {count} rows into stock_outcomes")

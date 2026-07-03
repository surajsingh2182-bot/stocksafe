import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.risk_scorer import calculate_score, classify_violation_type, get_verdict


def _order(violation_type="fraudulent_scheme", years_ago=1, status="active", entity_type="company"):
    return {
        "violation_type": violation_type,
        "order_date": date.today() - timedelta(days=int(years_ago * 365.25)),
        "status": status,
        "entity_type": entity_type,
    }


def test_get_verdict_boundaries():
    assert get_verdict(0) == "low_risk"
    assert get_verdict(29) == "low_risk"
    assert get_verdict(30) == "caution"
    assert get_verdict(59) == "caution"
    assert get_verdict(60) == "high_risk"
    assert get_verdict(100) == "high_risk"


def test_no_orders_scores_zero_low_risk():
    score, verdict = calculate_score([])
    assert score == 0
    assert verdict == "low_risk"


def test_single_recent_active_fraud_order_is_high_risk():
    # 45 * 1.3 (lt2) * 1.5 (active) * 1.0 (company) = 87.75 -> 87
    score, verdict = calculate_score([_order(years_ago=1)])
    assert score == 87
    assert verdict == "high_risk"


def test_old_settled_disclosure_order_is_low_risk():
    # 15 * 0.7 (gte5) * 0.5 (settled) * 1.0 = 5.25 -> 5
    score, verdict = calculate_score([_order(violation_type="disclosure_violation", years_ago=6, status="settled")])
    assert score == 5
    assert verdict == "low_risk"


def test_director_only_order_gets_reduced_factor():
    company_score, _ = calculate_score([_order(entity_type="company")])
    director_score, _ = calculate_score([_order(entity_type="individual")])
    assert director_score < company_score


def test_score_caps_at_100():
    orders = [_order(violation_type="fraudulent_scheme", years_ago=0.5, status="active") for _ in range(5)]
    score, verdict = calculate_score(orders)
    assert score == 100
    assert verdict == "high_risk"


def test_unknown_status_defaults_to_neutral_multiplier():
    score, _ = calculate_score([_order(status="disposed")])
    # 45 * 1.3 (lt2) * 1.0 (unknown status default) * 1.0 = 58.5 -> 58
    assert score == 58


def test_classify_violation_type_matches_keywords():
    assert classify_violation_type("This order concerns insider trading based on UPSI.") == "insider_trading"
    assert classify_violation_type("The noticee engaged in circular trading via synchronised trades.") == "circular_trading"
    assert classify_violation_type("No relevant keywords here.") == "default"

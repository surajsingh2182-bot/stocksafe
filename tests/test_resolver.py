import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.resolver import get_suggestions, resolve_company
from ingestion.entity_linker import clean_name


class _FakeResult:
    def __init__(self, data):
        self.data = data


class _FakeTable:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *_args, **_kwargs):
        return self

    def execute(self):
        return _FakeResult(self._rows)


class _FakeClient:
    """Minimal stand-in for the supabase client — just enough of the
    .table().select().execute() chain that resolver.py uses."""
    def __init__(self, companies):
        self._companies = [
            {"id": i + 1, "name": name, "name_clean": clean_name(name)}
            for i, name in enumerate(companies)
        ]

    def table(self, name):
        assert name == "companies"
        return _FakeTable(self._companies)


COMPANIES = ["Satyam Computer Services", "Karvy Stock Broking", "PC Jeweller Ltd"]


def test_exact_name_resolves():
    client = _FakeClient(COMPANIES)
    result = resolve_company("Satyam Computer Services", client)
    assert result is not None
    assert result["name"] == "Satyam Computer Services"


def test_misspelled_name_still_resolves():
    client = _FakeClient(COMPANIES)
    result = resolve_company("satyam computr services", client)
    assert result is not None
    assert result["name"] == "Satyam Computer Services"


def test_unrelated_query_returns_none():
    client = _FakeClient(COMPANIES)
    assert resolve_company("asdfghjkl", client) is None


def test_no_companies_in_db_returns_none():
    client = _FakeClient([])
    assert resolve_company("Satyam", client) is None


def test_suggestions_returned_for_partial_match():
    client = _FakeClient(COMPANIES)
    # "Karvy Financial" shares one token with "Karvy Stock Broking" but not
    # the other, landing in the 50-69 suggestion band rather than resolving
    # outright.
    suggestions = get_suggestions("Karvy Financial", client)
    assert all(50 <= s["match_score"] < 70 for s in suggestions)


def test_unrelated_query_gets_no_suggestions():
    client = _FakeClient(COMPANIES)
    assert get_suggestions("asdfghjkl", client) == []


def test_short_abbreviation_resolves_against_longer_name():
    # Regression test: a real user searched "DNEG" expecting to find "DNEG
    # Creative Services Limited" and got nothing, because the resolver used
    # to score short-query-vs-longer-name pairs far too low (token_sort_ratio
    # scored this exact case at 31). token_set_ratio fixes it.
    client = _FakeClient(COMPANIES + ["DNEG Creative Services Limited"])
    result = resolve_company("DNEG", client)
    assert result is not None
    assert result["name"] == "DNEG Creative Services Limited"


def test_partial_company_name_still_resolves():
    client = _FakeClient(COMPANIES)
    result = resolve_company("Karvy Broking", client)
    assert result is not None
    assert result["name"] == "Karvy Stock Broking"

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingestion.entity_linker import clean_name, find_or_create_company


class _FakeResult:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    """One .table("companies")... chain — either a select or an insert,
    never both, matching how find_or_create_company uses the real client."""
    def __init__(self, rows, insert_row=None):
        self._rows = rows
        self._insert_row = insert_row

    def select(self, *_args, **_kwargs):
        return self

    def execute(self):
        if self._insert_row is not None:
            row = {**self._insert_row, "id": len(self._rows) + 1}
            self._rows.append(row)
            return _FakeResult([row])
        return _FakeResult(self._rows)


class _FakeClient:
    """Minimal stand-in for the supabase client — just enough of the
    .table().select()/.insert().execute() chain that find_or_create_company
    uses. Rows persist across calls via the shared self.rows list."""
    def __init__(self, companies):
        self.rows = [
            {"id": i + 1, "name": name, "name_clean": clean_name(name)}
            for i, name in enumerate(companies)
        ]

    def table(self, name):
        assert name == "companies"
        return self

    def select(self, *_args, **_kwargs):
        return _FakeQuery(self.rows)

    def insert(self, row):
        return _FakeQuery(self.rows, insert_row=row)


def test_strips_common_suffixes():
    assert clean_name("PC Jeweller Ltd") == "pc jeweller"
    assert clean_name("Karvy Stock Broking Pvt. Ltd.") == "karvy stock broking"
    assert clean_name("Satyam Computer Services Limited") == "satyam computer services"


def test_removes_punctuation_and_collapses_whitespace():
    assert clean_name("Tata   Motors,  Inc.") == "tata motors"


def test_same_company_different_spellings_clean_to_same_string():
    assert clean_name("Reliance Industries Ltd") == clean_name("Reliance Industries Limited")


def test_truncated_name_matches_existing_full_name_not_a_new_row():
    # Real bug: a PDF page-break line wrap split "Prime Focus Limited" so a
    # later mention was extracted as just "Focus Limited". token_sort_ratio
    # scored that pair only 62.5 (below MATCH_THRESHOLD=85), so the fragment
    # was inserted as a second, near-duplicate company instead of matching
    # the existing "Prime Focus Limited" — causing fuzzy search to sometimes
    # land on the incomplete duplicate. token_set_ratio fixes it.
    client = _FakeClient(["Prime Focus Limited"])
    company_id = find_or_create_company("Focus Limited", client)
    assert company_id == 1
    assert len(client.rows) == 1  # no duplicate row created


def test_genuinely_different_company_still_creates_new_row():
    client = _FakeClient(["Prime Focus Limited"])
    company_id = find_or_create_company("Unrelated Trading Company Ltd", client)
    assert company_id == 2
    assert len(client.rows) == 2

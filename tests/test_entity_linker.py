import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingestion.entity_linker import clean_name


def test_strips_common_suffixes():
    assert clean_name("PC Jeweller Ltd") == "pc jeweller"
    assert clean_name("Karvy Stock Broking Pvt. Ltd.") == "karvy stock broking"
    assert clean_name("Satyam Computer Services Limited") == "satyam computer services"


def test_removes_punctuation_and_collapses_whitespace():
    assert clean_name("Tata   Motors,  Inc.") == "tata motors"


def test_same_company_different_spellings_clean_to_same_string():
    assert clean_name("Reliance Industries Ltd") == clean_name("Reliance Industries Limited")

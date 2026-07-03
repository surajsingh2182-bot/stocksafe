import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingestion.pdf_parser import _looks_like_company, _looks_like_person, _promote_primary_company


def test_rejects_legal_boilerplate_as_company():
    # Real garbage produced by unfiltered spaCy NER against a live SEBI order
    # that lacked a Noticee table (settlement order with redacted names).
    for bad in ["BOARD OF INDIA", "Supreme Court", "Financial Intelligence",
                "Order/JS/VC/2026-27/32459", "Company", "Noticee"]:
        assert not _looks_like_company(bad), bad


def test_accepts_real_company_names():
    for good in ["Citrus Check Inns Limited", "Evexia Lifecare Limited",
                 "Gedalia Multitrading Private Limited"]:
        assert _looks_like_company(good), good


def test_rejects_redacted_names_as_person():
    assert not _looks_like_person("Axxxxx Dxxx Sxxxx")


def test_rejects_document_boilerplate_as_person():
    # "Adjudication Order" is 2 title-case words with no digits — passes the
    # shape check, so real NER output on a live order incorrectly linked it
    # as a "director" shared across a dozen unrelated companies. Needs an
    # explicit stoplist, not just shape matching.
    for bad in ["Adjudication Order", "Noticee Nos", "Settlement Scheme",
                "Para A", "Record Maintenance", "Final Order"]:
        assert not _looks_like_person(bad), bad


def test_rejects_sentence_fragments_as_company():
    for bad in ["the BSE Limited", "the Company (APPL", "the Private Limited Company",
                "The Noticee i.e Dinbandhu Construction Private Limited",
                "the Limited Liability Partnership Act", "R. S. Ispat Ltd Vs SEBI"]:
        assert not _looks_like_company(bad), bad


def test_accepts_real_person_names():
    for good in ["Omprakash Basantlal Goenka", "Bhavin Sureshbhai Thakkar", "Sanjay Agrawal"]:
        assert _looks_like_person(good), good


def test_promotes_title_named_company_to_primary():
    # Real bug: a search for "DNEG" resolved correctly, but its red flag
    # text described "Prime Focus Limited" instead — the order's Noticee
    # table listed a subsidiary (DNEG Creative Services Limited) before the
    # parent company actually named in the title, and the pipeline always
    # attributed the order to company_names[0].
    text = "Adjudication Order in the matter of Prime Focus Limited          Page 1 of 25"
    names = ["DNEG Creative Services Limited", "Prime Focus Limited", "Monsoon Studio Private Limited"]
    assert _promote_primary_company(names, text)[0] == "Prime Focus Limited"


def test_promote_primary_company_noop_when_already_first():
    text = "Adjudication order in the matter of Citrus Check Inns Limited   Page 1 of 30"
    names = ["Citrus Check Inns Limited", "Some Other Company Limited"]
    assert _promote_primary_company(names, text) == names


def test_promote_primary_company_noop_without_title_match():
    names = ["Company A Limited", "Company B Limited"]
    assert _promote_primary_company(names, "no matching phrase here") == names


def test_promote_primary_company_noop_single_entry():
    assert _promote_primary_company(["Only Company Limited"], "irrelevant text") == ["Only Company Limited"]

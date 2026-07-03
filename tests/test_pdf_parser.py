import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingestion.pdf_parser import (
    _extract_status,
    _extract_summary,
    _is_citation_context,
    _looks_like_company,
    _looks_like_person,
    _promote_primary_company,
)


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


def test_status_only_settled_for_settlement_order_type():
    # Real bug: a genuine Adjudication Order (active penalty) mentioned
    # "terms of settlement" as procedural background describing SEBI's
    # general Settlement Scheme policy, not this case's outcome — wrongly
    # scanning the body for that phrase flagged it "settled", cutting the
    # risk score from 87 (High Risk) to 29 (Low Risk) for the same
    # violation. order_type (from the title) is the only reliable signal now.
    assert _extract_status("Adjudication Order") == "active"
    assert _extract_status("Settlement Order") == "settled"
    assert _extract_status("WTM Order") == "active"
    assert _extract_status("Board Order") == "active"


def test_rejects_entity_cited_only_as_legal_precedent():
    # Real bug: an unrelated, legitimate company (Apollo Tyres Limited) was
    # cited purely as case-law precedent in a Noticee's legal defense — "...
    # relied upon the Hon'ble SAT Order ... in the matter of Apollo Tyres
    # Limited (SAT Appeal No. 23 of 2019)" — and unfiltered NER swept it up
    # as if it were an accused party in the current order, wrongly showing
    # it as High Risk / involved in fraud.
    text = (
        "The Noticee also referred and relied upon the Hon'ble SAT Order dated "
        "September 27, 2023 in the matter of Apollo Tyres Limited (SAT Appeal "
        "No. 23 of 2019); and Hon'ble SC Order dated February 04, 2024."
    )
    start = text.index("Apollo Tyres Limited")
    end = start + len("Apollo Tyres Limited")
    assert _is_citation_context(text, start, end)


def test_rejects_precedent_citation_lacking_trailing_appeal_marker():
    # The same cited case is often mentioned twice in one order — a second
    # reference without the "(SAT Appeal No. ...)" suffix must still be
    # caught via the preceding "SAT Order ... in the matter of" lead-in.
    text = (
        "affirming the Hon'ble SAT Order dated September 27, 2023 in the "
        "matter of Apollo Tyres Limited, which was challenged by SEBI"
    )
    start = text.index("Apollo Tyres Limited")
    end = start + len("Apollo Tyres Limited")
    assert _is_citation_context(text, start, end)


def test_accepts_genuine_party_mention_not_a_citation():
    text = "In the matter of Citrus Check Inns Limited\n\nBACKGROUND OF THE CASE"
    start = text.index("Citrus Check Inns Limited")
    end = start + len("Citrus Check Inns Limited")
    assert not _is_citation_context(text, start, end)


def test_rejects_suffix_only_fragment_as_company():
    # Real bug: a page-break text artifact split a real company's name away
    # from its own suffix ("...Ambition Plaza\nPage 2 of 14\nPrivate Limited
    # was converted to..."), leaving spaCy to extract the bare suffix
    # "Private Limited" as its own fake ORG entity.
    assert not _looks_like_company("Private Limited")
    assert not _looks_like_company("Limited Company")


def test_accepts_real_company_name_with_suffix():
    assert _looks_like_company("Ambition Plaza Private Limited")


def test_summary_skips_boilerplate_header_to_background_section():
    # Real bug: the summary used to be raw_text[:500], which for some
    # orders ended before the "BACKGROUND" section even started, leaving
    # Gemini's red-flag generation with nothing but generic header/legal-
    # citation text ("BEFORE THE ADJUDICATING OFFICER... UNDER SECTION
    # 15-I...") — identical across every order regardless of what actually
    # happened. Verified against a real order (IDBI Trusteeship Services):
    # switching to start at BACKGROUND turned a useless generic red flag
    # into an accurate, case-specific one.
    text = (
        "Adjudication Order in the matter of Example Corp\n"
        "BEFORE THE ADJUDICATING OFFICER\n"
        "UNDER SECTION 15-I OF THE SEBI ACT, 1992...\n" + ("x" * 400) +
        "\nBACKGROUND OF THE CASE\n1. SEBI observed that Example Corp engaged in artificial trading."
    )
    summary = _extract_summary(text, length=100)
    assert summary.startswith("BACKGROUND")
    assert "artificial trading" in summary


def test_summary_falls_back_to_start_when_no_background_section():
    text = "Adjudication Order in the matter of Example Corp with no section markers at all."
    summary = _extract_summary(text, length=20)
    assert summary == text[:20]

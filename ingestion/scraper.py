"""Scrapes SEBI enforcement (Adjudicating Officer) orders and populates the DB.
See PRD v2 Section 8.2.

Real, verified endpoints (checked against the live site during development):
  Listing: https://www.sebi.gov.in/sebiweb/home/HomeAction.do?doListing=yes&sid=2&ssid=9&smid=6
           ("Enforcement > Orders > Orders of AO" — Adjudicating Officer orders,
           the standard SEBI penalty orders relevant to fraud detection)
  Each listing row links to an HTML detail page; the actual PDF URL is inside
  that page's <iframe src="...?file=<PDF_URL>">.

Known limitation: the site paginates via a JS-driven POST (Struts/JSP,
`searchFormNewsList()`) with no documented stable API. Page 1 always works
reliably via a plain GET — which is also all the PRD requires for daily runs.
Pagination for the one-time 1-10 page historical backfill is attempted
best-effort; if the server doesn't honour it, fetch_order_links() for pages
>1 returns an empty list and run_scraper() simply stops early (safe — dedup
via get_already_scraped_urls() means partial pagination never causes bad
data, just a smaller backfill than requested).
"""
import os
import re
import sys
import time
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ingestion.entity_linker import find_or_create_company, find_or_create_director, link_director_to_company  # noqa: E402
from ingestion.pdf_parser import parse_order_pdf  # noqa: E402

BASE_URL = "https://www.sebi.gov.in"
LISTING_URL = f"{BASE_URL}/sebiweb/home/HomeAction.do"
LISTING_PARAMS = {"doListing": "yes", "sid": "2", "ssid": "9", "smid": "6"}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}
SLEEP_SECONDS = 2
DOWNLOAD_DIR = Path(__file__).resolve().parent / "downloads"
IFRAME_FILE_RE = re.compile(r"[?&]file=(https?://[^&'\"]+\.pdf)")


def _make_session() -> httpx.Client:
    return httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True)


def fetch_order_links(page_num: int, session: httpx.Client) -> list[dict]:
    """Returns [{detail_url, title, date_text}, ...] for one listing page.
    Page 1 is a plain GET. Pages > 1 attempt a best-effort POST (see module
    docstring); on any failure this returns [] rather than raising, so
    run_scraper() can stop pagination gracefully."""
    try:
        if page_num == 1:
            resp = session.get(LISTING_URL, params=LISTING_PARAMS)
        else:
            resp = session.post(
                LISTING_URL,
                params=LISTING_PARAMS,
                data={
                    "sid": "2", "ssidhidden": "9", "smidhidden": "6",
                    "sectName": "Enforcement", "ssid": "-1", "deptId": "-1",
                    "nextValue": str(page_num - 2),
                },
            )
        resp.raise_for_status()
    except httpx.HTTPError:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table", id="sample_1")
    if not table or not table.tbody:
        return []

    rows = []
    for tr in table.tbody.find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) < 2:
            continue
        link = cells[1].find("a")
        if not link or not link.get("href"):
            continue
        rows.append({
            "detail_url": link["href"],
            "title": link.get("title", link.text).strip(),
            "date_text": cells[0].text.strip(),
        })
    return rows


def get_pdf_url(detail_url: str, session: httpx.Client) -> str | None:
    resp = session.get(detail_url)
    resp.raise_for_status()
    match = IFRAME_FILE_RE.search(resp.text)
    return match.group(1) if match else None


def download_pdf(url: str, path: Path, session: httpx.Client) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    resp = session.get(url)
    resp.raise_for_status()
    path.write_bytes(resp.content)


def get_already_scraped_urls(client) -> set[str]:
    rows = client.table("sebi_orders").select("pdf_url").execute().data or []
    return {row["pdf_url"] for row in rows}


def _insert_order(parsed: dict, pdf_url: str, client) -> int:
    """Resolves entities and inserts one sebi_orders row per company named
    in the order. SEBI sometimes issues one "omnibus" PDF covering several
    unrelated companies from the same systemic investigation (e.g. illiquid
    stock options manipulation) — inserting only one row per PDF (the old
    behaviour) meant every company but the first showed zero orders despite
    being genuinely named in a real enforcement order. All companies/
    directors found are still cross-linked via director_company_map
    regardless of how many order rows get created. Returns the number of
    rows inserted (0 if skipped)."""
    if not parsed["order_number"] or not parsed["order_date"]:
        print(f"  skip (couldn't extract order_number/date): {pdf_url}")
        return 0

    primary_director_id = None
    if parsed["director_names"]:
        primary_director_id = find_or_create_director(parsed["director_names"][0], client)

    for director_name in parsed["director_names"]:
        d_id = find_or_create_director(director_name, client)
        for company_name in parsed["company_names"]:
            c_id = find_or_create_company(company_name, client)
            link_director_to_company(d_id, c_id, role="noticee", source=pdf_url, client=client)

    base_row = {
        "order_number": parsed["order_number"],
        "order_date": parsed["order_date"].isoformat(),
        "order_type": parsed["order_type"],
        "status": parsed["status"],
        "violation_type": parsed["violation_type"],
        "summary": parsed["summary"],
        "pdf_url": pdf_url,
        "raw_text": parsed["raw_text"],
    }

    inserted = 0
    if parsed["company_names"]:
        company_ids = []
        for name in parsed["company_names"]:
            cid = find_or_create_company(name, client)
            if cid not in company_ids:
                company_ids.append(cid)
        for company_id in company_ids:
            try:
                client.table("sebi_orders").insert({
                    **base_row,
                    "entity_type": "company",
                    "company_id": company_id,
                    "director_id": primary_director_id,
                }).execute()
                inserted += 1
            except Exception as e:  # duplicate (order_number, company_id) or other failure
                print(f"  skip (insert failed for company_id={company_id}: {e}): {pdf_url}")
    else:
        try:
            client.table("sebi_orders").insert({
                **base_row,
                "entity_type": "individual" if primary_director_id else "unknown",
                "company_id": None,
                "director_id": primary_director_id,
            }).execute()
            inserted += 1
        except Exception as e:
            print(f"  skip (insert failed: {e}): {pdf_url}")

    return inserted


def run_scraper(pages: int, client) -> dict:
    """Full pipeline: scrape listing -> download PDF -> parse -> link
    entities -> insert into sebi_orders. Returns a summary dict."""
    already_scraped = get_already_scraped_urls(client)
    session = _make_session()
    downloaded, inserted, skipped = 0, 0, 0

    try:
        for page_num in range(1, pages + 1):
            rows = fetch_order_links(page_num, session)
            if not rows:
                print(f"page {page_num}: no rows returned, stopping pagination")
                break
            print(f"page {page_num}: {len(rows)} orders listed")

            for row in rows:
                try:
                    time.sleep(SLEEP_SECONDS)
                    pdf_url = get_pdf_url(row["detail_url"], session)
                    if not pdf_url or pdf_url in already_scraped:
                        continue

                    time.sleep(SLEEP_SECONDS)
                    local_path = DOWNLOAD_DIR / Path(pdf_url).name
                    download_pdf(pdf_url, local_path, session)
                    downloaded += 1

                    parsed = parse_order_pdf(str(local_path))
                    rows_inserted = _insert_order(parsed, pdf_url, client)
                    if rows_inserted > 0:
                        inserted += rows_inserted
                        already_scraped.add(pdf_url)
                    else:
                        skipped += 1
                except httpx.HTTPError as e:
                    # One row's network hiccup (timeout, reset, etc.) shouldn't
                    # abort a 25+ request batch — skip it and keep going.
                    print(f"  skip (network error: {e}): {row['detail_url']}")
                    skipped += 1
    finally:
        session.close()

    summary = {"downloaded": downloaded, "inserted": inserted, "skipped": skipped}
    print(summary)
    return summary


if __name__ == "__main__":
    import argparse

    from dotenv import load_dotenv
    from supabase import create_client

    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages", type=int, default=1,
                         help="pages to scrape (10 for first-run backfill, 1 for daily)")
    args = parser.parse_args()

    supabase_client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    run_scraper(args.pages, supabase_client)

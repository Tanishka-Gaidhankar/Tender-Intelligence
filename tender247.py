"""
run_tender247_full_test.py

Combines tender247_login.py (Playwright login/navigation) and
tender247_scraper.py (HTML parsing) into a single end-to-end test:

    1. Open the entry URL from today's Tender247 email
    2. Log in if needed (or confirm already-authenticated)
    3. Grab the resulting dashboard page's HTML
    4. Parse it for today's tenders
    5. Print the results

Usage:
    python run_tender247_full_test.py "<entry_url>"

Requires tender247_login.py and tender247_scraper.py to be in the
same folder as this script.
"""

import sys
from playwright.sync_api import sync_playwright
from tender247_login import login_to_tender247
from tender247_scraper import parse_tender247_dashboard


def run(entry_url: str, headless: bool = True):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()

        print("Step 1 — Logging in / navigating to dashboard...")
        success = login_to_tender247(page, entry_url)

        print(f"   Login success: {success}")
        print(f"   Final URL: {page.url}\n")

        if not success:
            print("   Tracking link did not work (likely already used/expired).")
            print("   Trying the known dashboard URL directly, in case a")
            print("   persistent session cookie exists from a previous visit...\n")

            try:
                page.goto("https://www.tender247.com/auth/tender",
                          wait_until="networkidle", timeout=30000)
            except Exception as e:
                print(f"   Direct navigation failed: {e}")
                browser.close()
                return

            print(f"   Landed on: {page.url}")

            from tender247_login import _confirm_dashboard_loaded
            success = _confirm_dashboard_loaded(page)
            print(f"   Dashboard loaded via direct URL? {success}\n")

            if not success:
                print("Could not reach the dashboard via direct URL either.")
                print("A genuinely fresh, unused tracking link from a new")
                print("email is needed to test further.")
                browser.close()
                return

        print("Step 2 — Grabbing page HTML...")
        html = page.content()
        print(f"   HTML length: {len(html)} characters\n")

        browser.close()

    print("Step 3 — Parsing tenders from the page...")
    tenders = parse_tender247_dashboard(html)
    print(f"   Parsed {len(tenders)} tenders total\n")

    print("First 5 tenders, full detail:")
    for t in tenders[:5]:
        print(f"\n   Tender ID:      {t['tender_id']}")
        print(f"   Authority:      {t['authority']}")
        print(f"   Location:       {t['location']}")
        print(f"   Title:          {t['title'][:100] if t['title'] else None}")
        print(f"   Bid Value:      {t['bid_value']}")
        print(f"   EMD:            {t['emd']}")
        print(f"   Due date:       {t['due_date']}")
        print(f"   Days left:      {t['days_left']}")
        print(f"   AI summary URL: {t['ai_summary_url']}")

    print("\nAny tenders with missing fields (potential parsing gaps):")
    found_any_missing = False
    for t in tenders:
        missing = [k for k, v in t.items() if v is None]
        if missing:
            found_any_missing = True
            print(f"   [{t['tender_id']}] missing: {missing}")
    if not found_any_missing:
        print("   None — all tenders fully parsed.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python run_tender247_full_test.py <entry_url>")
        sys.exit(1)

    run(sys.argv[1], headless=True)
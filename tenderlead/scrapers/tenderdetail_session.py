"""
tenderdetail_session.py

Handles authentication and scraping for TenderDetail's member portal.

The new portal flow (post-2025):
  1. Login → redirects to /registeruser/dashboard
  2. Dashboard shows a table of saved query categories with "Fresh / Live" counts
  3. Clicking "View" on Fresh column → /registeruser/indiantenders/{query_id}?tendertype=1
  4. That listing page shows div.tender_row cards with pagination

USAGE:
    One-time interactive setup (if session expires):
        python tenderdetail_session.py --setup

    Automated daily scraping:
        from tenderdetail_session import get_authenticated_page, scrape_all_query_tenders
"""

import os
import re
import sys
from playwright.sync_api import sync_playwright, Page

SESSION_FILE = "tenderdetail_session.json"
LOGIN_URL    = "https://www.tenderdetail.com/Account/LogOn"
DASHBOARD_URL = "https://www.tenderdetail.com/registeruser/dashboard"
BASE_URL      = "https://www.tenderdetail.com"

# ─── Credentials ───────────────────────────────────────────────────────────────
# Update these when the password changes.
TENDERDETAIL_USERNAME = "info@kbpcivil.in"
TENDERDETAIL_PASSWORD = "kbp@234"        # ← update here if password changes
# ───────────────────────────────────────────────────────────────────────────────

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


# ══════════════════════════════════════════════════════════════════════════════
#  Interactive one-time setup
# ══════════════════════════════════════════════════════════════════════════════

def setup_session_interactively():
    """
    Opens a visible browser window, pre-fills credentials, and waits for the
    user to complete the CAPTCHA / 2FA and land on the dashboard.
    Saves the authenticated cookies to SESSION_FILE.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(user_agent=USER_AGENT)
        page = context.new_page()

        print(f"Opening {LOGIN_URL} ...")
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)

        # Pre-fill credentials
        try:
            tab = page.locator("a[href='#username'], a.ml-tab-pill:has-text('Username')").first
            if tab.count() > 0:
                tab.click()
                page.wait_for_timeout(500)
            page.fill("#txtLogin",    TENDERDETAIL_USERNAME)
            page.fill("#txtPassword", TENDERDETAIL_PASSWORD)
            print("Credentials auto-filled. Complete CAPTCHA if shown, then click Login.")
        except Exception as e:
            print(f"Auto-fill failed ({e}) — fill manually.")

        print("\nOnce the dashboard is visible in the browser, press Enter here to save the session...")
        input()

        context.storage_state(path=SESSION_FILE)
        print(f"Session saved to {SESSION_FILE}")
        browser.close()


# ══════════════════════════════════════════════════════════════════════════════
#  Authenticated page helper
# ══════════════════════════════════════════════════════════════════════════════

def get_authenticated_page(headless: bool = True, start_url: str = None):
    """
    Returns (page, browser, playwright_ctx) already on the TenderDetail dashboard.
    Uses saved cookies; falls back to headless auto-login if session expired.
    """
    p = sync_playwright().start()
    browser = p.chromium.launch(headless=headless)

    if os.path.exists(SESSION_FILE):
        context = browser.new_context(storage_state=SESSION_FILE, user_agent=USER_AGENT)
    else:
        context = browser.new_context(user_agent=USER_AGENT)

    page = context.new_page()
    target = start_url or DASHBOARD_URL

    try:
        page.goto(target, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)
    except Exception as e:
        print(f"WARNING: Could not load {target}: {e}")

    # Check if we landed on the dashboard
    if not _is_dashboard(page):
        print("Session expired or missing — attempting headless login...")
        _do_headless_login(page, context)

    return page, browser, p


def _do_headless_login(page: Page, context):
    """Attempts a headless login; saves new session if successful."""
    try:
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1000)
        
        # Click Username tab first (by default Mobile OTP tab is selected)
        tab = page.locator("a[href='#username'], a.ml-tab-pill:has-text('Username')").first
        if tab.count() > 0:
            tab.click()
            page.wait_for_timeout(500)

        page.fill("#txtLogin",    TENDERDETAIL_USERNAME)
        page.fill("#txtPassword", TENDERDETAIL_PASSWORD)
        page.click("#btnLogin")
        
        # Wait for redirect away from login page
        try:
            page.wait_for_url(lambda u: "/Account/LogOn" not in u, timeout=15000)
        except Exception:
            pass
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(2000)
    except Exception as e:
        print(f"Headless login failed: {e}")
        return

    if _is_dashboard(page):
        context.storage_state(path=SESSION_FILE)
        print("Fresh session saved.")
    else:
        print(
            "ERROR: Auto-login failed (CAPTCHA or credentials changed).\n"
            "ACTION: Run   python tenderdetail_session.py --setup   interactively."
        )


def _is_dashboard(page: Page) -> bool:
    """Returns True if the page looks like the authenticated member dashboard."""
    try:
        url = page.url
        if "/Account/LogOn" in url:
            return False
        title = page.title()
        if "login" in title.lower() or "500" in title:
            return False
        # If url explicitly shows registeruser dashboard or listings, it is authenticated
        if "/registeruser/" in url or "dashboard" in url.lower():
            return True
        # Dashboard has these nav items
        body = page.locator("body").inner_text()
        if any(kw in body for kw in ["My Tenders", "DashBoard", "My Tender Log", "registeruser"]):
            return True
    except Exception:
        pass
    return False



# ══════════════════════════════════════════════════════════════════════════════
#  Dashboard scraping: collect all query IDs
# ══════════════════════════════════════════════════════════════════════════════

def get_query_ids_from_dashboard(page: Page) -> list[dict]:
    """
    Reads the dashboard table and returns all saved query entries.
    Each entry: { 'query_id': str, 'name': str, 'fresh_count': int }
    """
    from bs4 import BeautifulSoup
    import re as _re

    html = page.content()
    soup = BeautifulSoup(html, "html.parser")

    queries = []
    table = soup.find("table")
    if not table:
        print("WARNING: No table found on dashboard — cannot extract queries.")
        return queries

    rows = table.find_all("tr")[1:]  # Skip header row
    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 3:
            continue

        name = cells[0].get_text(strip=True)
        # "Fresh" count link is typically the 3rd cell (index 2)
        fresh_link = cells[2].find("a", href=True) if len(cells) > 2 else None
        if not fresh_link:
            # Try any link in the row
            fresh_link = row.find("a", href=True)

        if not fresh_link:
            continue

        href = fresh_link["href"]
        # URL pattern: /registeruser/indiatenders/{query_id}/1 or /registeruser/indiantenders/{query_id}
        m = _re.search(r"/india?tenders/(\d+)", href)
        if not m:
            continue

        query_id = m.group(1)
        try:
            fresh_count = int(fresh_link.get_text(strip=True))
        except ValueError:
            fresh_count = 0

        if fresh_count > 0:
            queries.append({
                "query_id":    query_id,
                "name":        name,
                "fresh_count": fresh_count,
                "href":        href
            })
            print(f"  Query '{name}' (id={query_id}): {fresh_count} fresh tenders")

    return queries


# ══════════════════════════════════════════════════════════════════════════════
#  Full scrape: iterate all queries and all pages
# ══════════════════════════════════════════════════════════════════════════════

def scrape_all_query_tenders(page: Page, max_pages_per_query: int = 10) -> list[dict]:
    """
    Scrapes fresh tenders from all dashboard queries.
    Handles pagination by clicking the 'next' button via Playwright.

    Returns a flat list of all parsed tender dicts (deduplicated by tender_id).
    """
    from .tenderdetail_scraper import parse_tenderdetail_listings, parse_pagination_info

    print("Navigating to TenderDetail dashboard...")
    try:
        page.goto(DASHBOARD_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)
    except Exception as e:
        print(f"ERROR: Could not load dashboard: {e}")
        return []

    if not _is_dashboard(page):
        print("ERROR: Not on dashboard after navigation. Session likely expired.")
        return []

    # Get all query IDs with fresh tender counts
    queries = get_query_ids_from_dashboard(page)
    if not queries:
        print("No fresh tenders found in any query category.")
        return []

    all_tenders = {}  # keyed by tender_id to deduplicate

    for q in queries:
        query_id = q["query_id"]
        query_name = q["name"]
        print(f"\nScraping query: '{query_name}' (id={query_id}, {q['fresh_count']} fresh tenders)")

        listing_url = f"{BASE_URL}{q['href']}" if q.get("href", "").startswith("/") else f"{BASE_URL}/registeruser/indiatenders/{query_id}/1"
        try:
            page.goto(listing_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)
        except Exception as e:
            print(f"  ERROR loading listing page: {e}")
            continue

        page_num = 1
        while page_num <= max_pages_per_query:
            html = page.content()
            tenders = parse_tenderdetail_listings(html)
            pagination = parse_pagination_info(html)

            print(f"  Page {page_num}/{pagination['total_pages']}: {len(tenders)} tenders parsed")

            for t in tenders:
                t["category"] = query_name
                if t["tender_id"] not in all_tenders:
                    all_tenders[t["tender_id"]] = t

            # Stop if we've reached the last page
            if page_num >= pagination["total_pages"]:
                break

            # Click the 'next' pagination button
            try:
                next_btn = page.locator("#example2_paginate a#next, a#next")
                if next_btn.count() > 0:
                    next_btn.first.click()
                    page.wait_for_timeout(2500)
                    page_num += 1
                else:
                    print("  No 'next' button found — stopping pagination.")
                    break
            except Exception as e:
                print(f"  Pagination click failed: {e}")
                break

    print(f"\nTotal unique tenders collected: {len(all_tenders)}")
    return list(all_tenders.values())


# ══════════════════════════════════════════════════════════════════════════════
#  CLI entry point
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    if "--setup" in sys.argv:
        setup_session_interactively()
    else:
        print("Testing saved session (headless)...")
        page, browser, p = get_authenticated_page(headless=True)
        print(f"Final URL: {page.url}")
        print(f"Is dashboard: {_is_dashboard(page)}")

        if _is_dashboard(page):
            queries = get_query_ids_from_dashboard(page)
            print(f"Queries found: {len(queries)}")
        else:
            print("Not authenticated. Run:  python tenderdetail_session.py --setup")

        browser.close()
        p.stop()

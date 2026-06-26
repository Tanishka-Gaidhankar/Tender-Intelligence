"""
tender247_session.py

Solves the one-time-tracking-link problem by saving and reusing an
authenticated browser session (cookies), instead of relying on the
email's "View Details" link every single run.

WHY THIS IS NEEDED:
The r.tenders.bidsnrfp.com/tr/cl/... links found in Tender247 emails
appear to be single-use tracking redirects. Once clicked ONCE (by a
person manually checking the email, or by a previous script run), the
link no longer authenticates — it just lands on the public homepage.
This makes the tracking link unreliable as a repeatable, automatable
entry point.

THE FIX:
Playwright can save a logged-in browser's full cookie/session state to
a file (`storage_state`). Once saved, future browser launches can load
that file and start ALREADY authenticated — no tracking link, no
credential fill, no race condition with a human also checking the
email first.

Sessions do expire eventually (exact duration unconfirmed — likely
days to weeks). When that happens, this module automatically falls
back to a real credential-based login (reusing the logic from
tender247_login.py) and saves a fresh session afterward.

USAGE:
    One-time setup (run once, interactively):
        python tender247_session.py --setup

    Daily automated use (in the actual scraper):
        from tender247_session import get_authenticated_page
        page, browser, playwright_ctx = get_authenticated_page()
        # ... use page ...
        browser.close()
        playwright_ctx.stop()
"""

import os
import json
from playwright.sync_api import sync_playwright, Page

SESSION_FILE = "tender247_session.json"
DASHBOARD_URL = "https://www.tender247.com/auth/tender"
LOGIN_URL = "https://www.tender247.com/auth/login"  # adjust if the real login page URL differs

TENDER247_EMAIL = "info@kbpcivil.in"
TENDER247_PASSWORD = "KBPcivil@2026"


def setup_session_interactively():
    """
    One-time interactive setup: opens a REAL visible browser, lets you
    log in manually (or auto-fills credentials and waits for you to
    confirm), then saves the resulting session to SESSION_FILE.

    Run this once whenever the saved session expires.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        print(f"Opening {DASHBOARD_URL} ...")
        page.goto(DASHBOARD_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)

        if _looks_like_login_page(page):
            print("Login page detected — attempting auto-fill...")
            try:
                page.fill('input[name="emailId"]', TENDER247_EMAIL)
                page.fill('input[name="password"]', TENDER247_PASSWORD)
                page.click('button[type="submit"]')
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception as e:
                print(f"Auto-fill failed ({e}) — please log in manually in the browser window.")

        print("\nIf you're not already on the dashboard (Today Tenders visible),")
        print("please log in manually in the browser window now.")
        input("Once you can see the dashboard, press Enter here to save the session...")

        context.storage_state(path=SESSION_FILE)
        print(f"\nSession saved to {SESSION_FILE}")

        browser.close()


def get_authenticated_page(headless: bool = True, max_retries: int = 3, start_url: str = None):
    """
    Returns (page, browser, playwright_context) using the saved
    session if available and still valid. If no session file exists,
    or the saved session has expired, falls back to a fresh
    credential-based login and saves a new session afterward.

    If start_url is provided, navigates to it first (e.g., an email redirect link
    that auto-authenticates the session). Otherwise, defaults to DASHBOARD_URL.

    After reaching the dashboard, this also waits for actual tender
    data to render.

    Caller is responsible for closing browser and stopping the
    playwright context when done.
    """
    p = sync_playwright().start()
    browser = p.chromium.launch(headless=headless)

    user_agent = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    if os.path.exists(SESSION_FILE):
        context = browser.new_context(storage_state=SESSION_FILE, user_agent=user_agent)
    else:
        context = browser.new_context(user_agent=user_agent)

    page = context.new_page()
    target_url = start_url or DASHBOARD_URL
    try:
        page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)
    except Exception as e:
        print(f"WARNING: Failed to load target URL ({target_url}): {e}. Falling back to standard login...")
        if target_url != DASHBOARD_URL:
            try:
                page.goto(DASHBOARD_URL, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(2000)
            except Exception as e2:
                print(f"ERROR: Fallback to DASHBOARD_URL also failed: {e2}")

    if not _looks_like_dashboard(page) or _looks_like_login_page(page):
        print("Not on dashboard or session expired — attempting fresh login...")
        if not _looks_like_login_page(page):
            try:
                page.goto(DASHBOARD_URL, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(2000)
            except Exception as e3:
                print(f"ERROR: Failed to load login page during fallback: {e3}")
                return page, browser, p

        if _looks_like_login_page(page):
            try:
                page.fill('input[name="emailId"]', TENDER247_EMAIL)
                page.fill('input[name="password"]', TENDER247_PASSWORD)
                page.click('button[type="submit"]')
                # Wait for redirect to land in authenticated area
                page.wait_for_url("**/auth/**", timeout=15000)
                page.wait_for_load_state("domcontentloaded")
                page.wait_for_timeout(3000)
            except Exception as e:
                print(f"Fresh login failed: {e}")
                return page, browser, p

            if _looks_like_dashboard(page):
                context.storage_state(path=SESSION_FILE)
                print("Fresh session saved.")
            else:
                print(f"WARNING: still not on dashboard after fresh login attempt. Current URL: {page.url}")
                return page, browser, p

    # Now wait for actual tender data to render, not just the page shell.
    # Retry with a reload if we land on "No Record Found" — this is a
    # transient client-side data-fetch timing issue, not an auth failure.
    for attempt in range(1, max_retries + 1):
        state = _check_data_state(page)

        if state == "has_data":
            print(f"Tender data loaded (attempt {attempt}/{max_retries}).")
            break

        elif state == "no_record_found":
            print(f"Got 'No Record Found' on attempt {attempt}/{max_retries} "
                  f"— likely a timing issue, reloading...")
            if attempt < max_retries:
                page.reload(wait_until="networkidle", timeout=30000)
                page.wait_for_timeout(2000)  # small buffer for client-side fetch
            continue

        else:  # "unknown"
            print(f"Could not determine data state on attempt {attempt}/{max_retries}, "
                  f"waiting briefly and re-checking...")
            page.wait_for_timeout(2000)

    return page, browser, p


def _check_data_state(page: Page) -> str:
    """
    Returns one of:
        "has_data"        - tender entries are present
        "no_record_found" - the page explicitly shows the empty state
        "unknown"         - neither condition clearly detected yet
    """
    try:
        body_text = page.locator("body").inner_text()
    except Exception:
        return "unknown"

    if "No Record Found" in body_text:
        return "no_record_found"

    # A real tender entry always contains "T247 ID" per the confirmed
    # page structure — a more reliable positive signal than just
    # checking for "Today Tenders" text, which appears even when empty.
    if "T247 ID" in body_text:
        return "has_data"

    return "unknown"


def _looks_like_login_page(page: Page) -> bool:
    try:
        # Check if email/password inputs are already on the page
        if page.locator('input[name="emailId"]').count() > 0 and \
           page.locator('input[name="password"]').count() > 0:
            return True
        # If not, check if the "Sign Up/Log In" button is visible and click it to open modal
        if page.locator("button:has-text('Sign Up/Log In')").count() > 0:
            print("Clicking 'Sign Up/Log In' button to reveal login inputs...")
            page.click("button:has-text('Sign Up/Log In')")
            page.wait_for_timeout(2000)
            return page.locator('input[name="emailId"]').count() > 0 and \
                   page.locator('input[name="password"]').count() > 0
    except Exception:
        pass
    return False


def _looks_like_dashboard(page: Page) -> bool:
    if "/auth/" in page.url:
        try:
            body_text = page.locator("body").inner_text().lower()
            return any(
                indicator in body_text
                for indicator in ["today tenders", "active tenders", "closed tenders"]
            )
        except Exception:
            return False
    return False


if __name__ == "__main__":
    import sys

    if "--setup" in sys.argv:
        setup_session_interactively()
    else:
        print("Testing saved session (headless)...")
        page, browser, p = get_authenticated_page(headless=True)

        print(f"Final URL: {page.url}")
        data_state = _check_data_state(page)
        print(f"Data state: {data_state}")

        if data_state == "has_data":
            html = page.content()
            print(f"HTML length: {len(html)} characters")

            with open("tender247_dashboard_dump.html", "w", encoding="utf-8") as f:
                f.write(html)
            print("Saved page HTML to tender247_dashboard_dump.html for inspection.")
        else:
            print("No usable tender data on the page — not saving HTML dump.")

        browser.close()
        p.stop()
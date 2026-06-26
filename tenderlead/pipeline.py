"""
pipeline.py

The main orchestrator for the Tender Intelligence System pipeline.
Runs the daily intake and analysis jobs, staging data in SQLite.
"""

import csv
import json
import os
import sqlite3
import sys
from datetime import datetime

import requests

from . import email_reader
from .ai.stage1_filter import evaluate_tender247, evaluate_tenderdetail
from .ai.stage2_scorer import score_tender
from .scrapers.tender247_detail_scraper import fetch_tender247_detail_summary
from .scrapers.tender247_scraper import parse_tender247_dashboard
from .scrapers.tender247_session import get_authenticated_page
from .scrapers.tenderdetail_session import get_authenticated_page as get_tenderdetail_page, scrape_all_query_tenders, _is_dashboard
from .scrapers.tenderdetail_detail_scraper import fetch_tenderdetail_detail
from .scrapers.tenderdetail_scraper import parse_tenderdetail_listings

DB_FILE = "tender_intelligence.db"
SETTINGS_FILE = "tender_rules_settings.json"


def init_db():
    """Initializes the staging SQLite database tables."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # Staging table: raw_tender_feed
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS raw_tender_feed (
            tender_id TEXT PRIMARY KEY,
            source TEXT,
            title TEXT,
            authority TEXT,
            location TEXT,
            value TEXT,
            emd TEXT,
            due_date TEXT,
            status TEXT,
            ai_score REAL,
            ai_rationale TEXT,
            link TEXT,
            created_at TEXT
        )
    """)

    # Check and add link column if it is missing (migration)
    cursor.execute("PRAGMA table_info(raw_tender_feed)")
    columns = [col[1] for col in cursor.fetchall()]
    if "link" not in columns:
        cursor.execute("ALTER TABLE raw_tender_feed ADD COLUMN link TEXT")

    # Main leads table: tender_leads
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tender_leads (
            tender_id TEXT PRIMARY KEY,
            source TEXT,
            title TEXT,
            authority TEXT,
            location TEXT,
            estimated_value TEXT,
            emd TEXT,
            due_date TEXT,
            eligibility TEXT,
            scope_of_work TEXT,
            ai_score REAL,
            ai_rationale TEXT,
            source_link TEXT,
            created_at TEXT
        )
    """)

    conn.commit()
    conn.close()


def load_settings() -> dict:
    """Loads KBP settings from tender_rules_settings.json."""
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading {SETTINGS_FILE}: {e}")
    return {}


def save_raw_feed(tender: dict, source: str, status: str, ai_score: float = None, ai_rationale: str = None):
    """Inserts or updates a record in raw_tender_feed."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # If the tender is already present in raw_tender_feed and has been processed,
    # do NOT reset its status back to "new" on subsequent runs.
    cursor.execute("SELECT status FROM raw_tender_feed WHERE tender_id = ?", (tender["tender_id"],))
    row = cursor.fetchone()
    if row is not None:
        existing_status = row[0]
        if status == "new" and existing_status not in (None, "new"):
            status = existing_status

    val = tender.get("tender_value") or tender.get("bid_value")
    link = tender.get("view_tender_url") or tender.get("ai_summary_url") or tender.get("link")
    
    cursor.execute("""
        INSERT INTO raw_tender_feed (tender_id, source, title, authority, location, value, emd, due_date, status, ai_score, ai_rationale, link, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tender_id) DO UPDATE SET
            status=excluded.status,
            ai_score=excluded.ai_score,
            ai_rationale=excluded.ai_rationale,
            link=excluded.link
    """, (
        tender["tender_id"],
        source,
        tender.get("title"),
        tender.get("authority"),
        tender.get("location"),
        val,
        tender.get("emd"),
        tender.get("due_date"),
        status,
        ai_score,
        ai_rationale,
        link,
        datetime.now().isoformat()
    ))
    conn.commit()
    conn.close()


def save_tender_lead(tender: dict, source: str, score_results: dict, source_link: str):
    """Inserts or updates a record in tender_leads."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    val = tender.get("tender_value") or tender.get("bid_value")
    
    # Compile the AI rationales as one string
    rationales = [
        f"Scope Match: {score_results.get('scope_match_rationale', '')}",
        f"Location: {score_results.get('location_eligibility_rationale', '')}",
        f"Eligibility: {score_results.get('eligibility_clearance_rationale', '')}",
        f"Value: {score_results.get('value_fit_rationale', '')}",
        f"Disqualifiers: {score_results.get('disqualifier_check_rationale', '')}",
        f"Risks: {score_results.get('key_risks', '')}"
    ]
    ai_rationale_full = "\n".join(rationales)

    cursor.execute("""
        INSERT INTO tender_leads (tender_id, source, title, authority, location, estimated_value, emd, due_date, eligibility, scope_of_work, ai_score, ai_rationale, source_link, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tender_id) DO UPDATE SET
            ai_score=excluded.ai_score,
            ai_rationale=excluded.ai_rationale
    """, (
        tender["tender_id"],
        source,
        tender.get("title"),
        tender.get("authority"),
        tender.get("location"),
        val,
        tender.get("emd"),
        tender.get("due_date"),
        tender.get("eligibility_criteria"),
        tender.get("scope_of_work"),
        score_results["overall_score"],
        ai_rationale_full,
        source_link,
        datetime.now().isoformat()
    ))
    conn.commit()
    conn.close()


def export_leads_to_csv():
    """Exports all saved Tender Leads sorted by score descending to tender_leads.csv."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT tender_id, source, title, authority, location, estimated_value, emd, due_date, ai_score, source_link FROM tender_leads ORDER BY ai_score DESC")
    rows = cursor.fetchall()
    conn.close()

    csv_file = "tender_leads.csv"
    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Tender ID", "Source", "Title", "Authority", "Location", "Estimated Value", "EMD", "Due Date", "AI Score", "Source Link"])
        writer.writerows(rows)
    print(f"Exported {len(rows)} leads to {csv_file}")


def intake_tenderdetail_batch(batch: dict) -> list[dict]:
    """Downloads listings from TenderDetail email view_all URL, parses, and saves to DB as 'new'."""
    print(f"\n--- Intake: Processing TenderDetail Batch received at {batch['received_at']} ---")
    url = email_reader.extract_tenderdetail_view_all_url(batch["html"])
    if not url:
        print("ERROR: Could not find 'View All' URL in TenderDetail email.")
        return []

    print(f"Fetching listings page: {url}")
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 ..."}
    res = requests.get(url, headers=headers, timeout=30)
    if res.status_code != 200:
        print(f"Failed to fetch listings page. HTTP {res.status_code}")
        return []

    listings = parse_tenderdetail_listings(res.text)
    print(f"Parsed {len(listings)} listings from TenderDetail.")

    for idx, tender in enumerate(listings):
        arrival_date = batch["received_at"].strftime("%Y-%m-%d") if "received_at" in batch else "N/A"
        print(f"  [{idx+1}/{len(listings)}] Source: TenderDetail | ID: {tender['tender_id']} | Title: {tender['title'][:60]}... | Link: {tender['view_tender_url']} | Arrival: {arrival_date} | Due Date: {tender.get('due_date')} | Value: {tender.get('tender_value') or 'N/A'} | Org: {tender.get('authority')} | State: {tender.get('location')}")
        save_raw_feed(tender, "TenderDetail", "new")
    
    return listings


def intake_tender247_batch(batch: dict) -> list[dict]:
    """Loads Tender247 Today Tenders dashboard using the email's link directly, parses listings, and saves to DB as 'new'."""
    print(f"\n--- Intake: Processing Tender247 Batch received at {batch['received_at']} ---")
    
    # 1. Extract email URL and fetch dashboard page
    url = email_reader.extract_tender247_view_details_url(batch["html"])
    if url:
        print(f"Launching Playwright to load email link: {url}")
    else:
        print("WARNING: Could not find 'View Details' URL in email. Loading dashboard directly.")
        url = None

    page, browser, playwright_ctx = get_authenticated_page(headless=True, start_url=url)
    
    listings = []
    try:
        # Check if we successfully landed on dashboard or if it is empty
        body_text = page.locator("body").inner_text()
        if "T247 ID" not in body_text:
            print("ERROR: Dashboard did not load successfully (T247 ID not found on page).")
            return []

        # Scroll to bottom to trigger infinite scroll/lazy loading of the full batch
        print("Scrolling page to load all tenders...")
        previous_count = 0
        expected_count = batch.get("tender_count")
        for scroll_attempt in range(1, 10):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1500)
            current_count = page.locator("span:has-text('T247 ID')").count()
            print(f"  Scroll attempt {scroll_attempt}: visible tenders = {current_count}")
            if current_count == previous_count or (expected_count and current_count >= expected_count):
                break
            previous_count = current_count

        # 2. Parse listings
        html = page.content()
        listings = parse_tender247_dashboard(html)
        print(f"Parsed {len(listings)} listings from Tender247 dashboard.")

        for idx, tender in enumerate(listings):
            arrival_date = batch["received_at"].strftime("%Y-%m-%d") if "received_at" in batch else "N/A"
            print(f"  [{idx+1}/{len(listings)}] Source: Tender247 | ID: {tender['tender_id']} | Title: {tender['title'][:60]}... | Link: {tender['ai_summary_url']} | Arrival: {arrival_date} | Due Date: {tender.get('due_date')} | Bid Value: {tender.get('bid_value') or 'N/A'} | Org: {tender.get('authority')} | State: {tender.get('location')}")
            save_raw_feed(tender, "Tender247", "new")
            
    finally:
        browser.close()
        playwright_ctx.stop()
        
    return listings


def intake_tenderdetail_direct() -> list[dict]:
    """
    Logs into TenderDetail via saved session, scrapes all fresh tenders from
    all saved query categories (with pagination), and saves them to the DB.
    """
    print(f"\n--- Intake: Fetching TenderDetail directly from website ---")
    page, browser, playwright_ctx = get_tenderdetail_page(headless=True)

    listings = []
    try:
        # Verify authentication
        if not _is_dashboard(page):
            print("ERROR: TenderDetail session expired or invalid.")
            print("  ACTION: Run   python tenderdetail_session.py --setup   to refresh session.")
            return []

        # Scrape all queries and their paginated tender listings
        listings = scrape_all_query_tenders(page)

        if not listings:
            print("No TenderDetail tenders found (all query categories may have 0 fresh tenders).")
            return []

        arrival_date = datetime.now().strftime("%Y-%m-%d")
        for idx, tender in enumerate(listings):
            print(
                f"  [{idx+1}/{len(listings)}] Source: TenderDetail | ID: {tender['tender_id']} | "
                f"Title: {tender['title'][:60]}... | Link: {tender['view_tender_url']} | "
                f"Arrival: {arrival_date} | Due Date: {tender.get('due_date')} | "
                f"Value: {tender.get('tender_value') or 'N/A'} | "
                f"Org: {tender.get('authority')} | State: {tender.get('location')}"
            )
            save_raw_feed(tender, "TenderDetail", "new")

    finally:
        browser.close()
        playwright_ctx.stop()

    return listings



def intake_tender247_direct() -> list[dict]:
    """Logs into Tender247 dashboard directly using saved session, parses listings, and saves to DB."""
    print(f"\n--- Intake: Fetching Tender247 directly from website ---")
    page, browser, playwright_ctx = get_authenticated_page(headless=True)
    
    listings = []
    try:
        # Check if we successfully landed on dashboard or if it is empty
        body_text = page.locator("body").inner_text()
        if "T247 ID" not in body_text:
            print("ERROR: Dashboard did not load successfully (T247 ID not found on page).")
            return []

        # Scroll to bottom to trigger infinite scroll/lazy loading of the full batch
        print("Scrolling page to load all tenders...")
        previous_count = 0
        for scroll_attempt in range(1, 10):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1500)
            current_count = page.locator("span:has-text('T247 ID')").count()
            print(f"  Scroll attempt {scroll_attempt}: visible tenders = {current_count}")
            if current_count == previous_count:
                break
            previous_count = current_count

        # 2. Parse listings
        html = page.content()
        listings = parse_tender247_dashboard(html)
        print(f"Parsed {len(listings)} listings from Tender247 dashboard.")

        arrival_date = datetime.now().strftime("%Y-%m-%d")
        for idx, tender in enumerate(listings):
            print(f"  [{idx+1}/{len(listings)}] Source: Tender247 | ID: {tender['tender_id']} | Title: {tender['title'][:60]}... | Link: {tender['ai_summary_url']} | Arrival: {arrival_date} | Due Date: {tender.get('due_date')} | Bid Value: {tender.get('bid_value') or 'N/A'} | Org: {tender.get('authority')} | State: {tender.get('location')}")
            save_raw_feed(tender, "Tender247", "new")
            
    finally:
        browser.close()
        playwright_ctx.stop()
        
    return listings


def run_intake(direct: bool = False):
    """Runs the Intake stage: fetches listings directly from websites or via emails."""
    print("\n==================================================")
    print("STAGE 1: TENDER INTAKE START")
    print("==================================================")
    
    init_db()
    
    if direct:
        print("Scraping website dashboards directly (bypassing emails)...")
        intake_tenderdetail_direct()
        intake_tender247_direct()
        print("\n==================================================")
        print("STAGE 1: TENDER INTAKE COMPLETED")
        print("==================================================")
        return

    # Check GMail for new tender emails
    print("Checking Tender247dashboard for today's tender alerts...")
    try:
        emails = email_reader.fetch_todays_emails()
    except Exception as e:
        print(f"ERROR: Failed to fetch emails: {e}")
        return
        
    emails.pop("_debug_all_subjects_seen", None)
    
    if not emails:
        print("No matching 'New Tender' found in this business day's window.")
        print("\n==================================================")
        print("STAGE 1: TENDER INTAKE COMPLETED")
        print("==================================================")
        return
        
    # Process TenderDetail intake
    if "TenderDetail" in emails:
        for batch in emails["TenderDetail"]:
            intake_tenderdetail_batch(batch)
            
    # Process Tender247 intake
    if "Tender247" in emails:
        for batch in emails["Tender247"]:
            intake_tender247_batch(batch)
            
    print("\n==================================================")
    print("STAGE 1: TENDER INTAKE COMPLETED")
    print("==================================================")


def run_stage1():
    """Runs Stage 1 Filter (Stage A): keywords and AI title-guess on 'new' raw tenders."""
    print("\n==================================================")
    print("STAGE 2 (STAGE A): FILTER START")
    print("==================================================")
    
    settings = load_settings()
    if not settings:
        print("ERROR: Could not load settings. Exiting Stage A Filter.")
        return
        
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT tender_id, source, title, authority, location, value, emd, due_date, link, created_at 
        FROM raw_tender_feed 
        WHERE status = 'new'
    """)
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        print("No new tenders found in the database for Stage A Filter.")
        print("\n==================================================")
        print("STAGE 2 (STAGE A): FILTER COMPLETED")
        print("==================================================")
        return
        
    print(f"Found {len(rows)} new tenders in database to evaluate.")
    
    company_scope = settings.get("scope_of_work", "")
    
    for idx, row in enumerate(rows):
        tender_id, source, title, authority, location, value, emd, due_date, link, created_at = row
        
        # Recreate the tender dictionary
        tender = {
            "tender_id": tender_id,
            "title": title,
            "authority": authority,
            "location": location,
            "emd": emd,
            "due_date": due_date
        }
        if source == "TenderDetail":
            tender["view_tender_url"] = link
            tender["tender_value"] = value
        else:
            tender["ai_summary_url"] = link
            tender["bid_value"] = value
            
        arrival_date = created_at.split('T')[0] if created_at else "N/A"
        print(f"  [{idx+1}/{len(rows)}] Source: {source} | ID: {tender_id} | Title: {title[:60]}... | Link: {link} | Arrival: {arrival_date} | Due Date: {due_date} | {'Bid Value' if source == 'Tender247' else 'Value'}: {value or 'N/A'} | Org: {authority} | State: {location}")
        
        # Run Stage 1 Evaluation
        if source == "TenderDetail":
            passed, rationale = evaluate_tenderdetail(tender, company_scope)
        else:
            passed, rationale = evaluate_tender247(tender, company_scope)
            
        if passed == "no":
            print(f"    Rejected at Stage 1: {rationale}")
            save_raw_feed(tender, source, "rules_rejected", ai_rationale=rationale)
        else:
            print(f"    Passed Stage 1: {rationale}")
            save_raw_feed(tender, source, "rules_passed")
            
    print("\n==================================================")
    print("STAGE 2 (STAGE A): FILTER COMPLETED")
    print("==================================================")


def run_stage2():
    """Runs Stage 2 (Stage B): fetches detail page, runs LLM scoring, and promotes to leads."""
    print("\n==================================================")
    print("STAGE 3 (STAGE B): SCORING & PROMOTION START")
    print("==================================================")
    
    settings = load_settings()
    if not settings:
        print("ERROR: Could not load settings. Exiting Stage B Scorer.")
        return
        
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT tender_id, source, title, authority, location, value, emd, due_date, link 
        FROM raw_tender_feed 
        WHERE status = 'rules_passed'
    """)
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        print("No 'rules_passed' tenders found in the database for Stage B Scoring.")
        print("\n==================================================")
        print("STAGE 3 (STAGE B): SCORING & PROMOTION COMPLETED")
        print("==================================================")
        return
        
    print(f"Found {len(rows)} tenders with 'rules_passed' status to evaluate.")
    
    threshold = settings.get("ai_score_threshold", 70)
    
    # Initialize Playwright if there is at least one Tender247 tender
    has_t247 = any(row[1] == "Tender247" for row in rows)
    playwright_ctx = None
    browser = None
    page = None
    
    if has_t247:
        print("Launching Playwright to load detail pages for Tender247...")
        page, browser, playwright_ctx = get_authenticated_page(headless=True)
        
    try:
        for idx, row in enumerate(rows):
            tender_id, source, title, authority, location, value, emd, due_date, link = row
            
            # Recreate the tender dictionary
            tender = {
                "tender_id": tender_id,
                "title": title,
                "authority": authority,
                "location": location,
                "emd": emd,
                "due_date": due_date
            }
            if source == "TenderDetail":
                tender["view_tender_url"] = link
                tender["tender_value"] = value
            else:
                tender["ai_summary_url"] = link
                tender["bid_value"] = value
                
            print(f"  [{idx+1}/{len(rows)}] Source: {source} | ID: {tender_id} | Title: {title[:60]}...")
            
            # 4. Fetch detail details
            save_raw_feed(tender, source, "ai_processing")
            
            if source == "TenderDetail":
                print("    Fetching detail page...")
                detail = fetch_tenderdetail_detail(link)
                if detail:
                    tender.update(detail)
                else:
                    print("    Failed to fetch detail page. Skipping Stage 2.")
                    save_raw_feed(tender, source, "rules_passed")
                    continue
            else:
                if link:
                    print("    Fetching AI Summary details page...")
                    summary_text = fetch_tender247_detail_summary(page, link)
                    if summary_text:
                        tender["eligibility_criteria"] = summary_text
                        tender["scope_of_work"] = title  # Fallback to title as scope
                    else:
                        print("    Failed to fetch AI summary. Skipping Stage 2.")
                        save_raw_feed(tender, source, "rules_passed")
                        continue
                else:
                    print("    No AI Summary URL found. Skipping Stage 2.")
                    save_raw_feed(tender, source, "rejected_ai", ai_rationale="No AI Summary link available.")
                    continue
                    
            # 5. Stage 2 Scoring
            score_results = score_tender(tender, settings)
            overall_score = score_results["overall_score"]
            print(f"    AI Overall Score: {overall_score} (Action: {score_results.get('suggested_action')})")
            
            if overall_score >= threshold:
                print(f"    ===> Promoted to Tender Lead! Score {overall_score} >= {threshold}")
                save_raw_feed(tender, source, "lead_created", ai_score=overall_score, ai_rationale=score_results.get("scope_match_rationale"))
                save_tender_lead(tender, source, score_results, link)
            else:
                print(f"    Rejected at Stage 2: score {overall_score} < {threshold}")
                save_raw_feed(tender, source, "rejected_ai", ai_score=overall_score, ai_rationale=score_results.get("scope_match_rationale"))
                
    finally:
        if browser:
            browser.close()
        if playwright_ctx:
            playwright_ctx.stop()
            
    # Export final sorted CSV report
    export_leads_to_csv()
    
    print("\n==================================================")
    print("STAGE 3 (STAGE B): SCORING & PROMOTION COMPLETED")
    print("==================================================")


def main():
    if len(sys.argv) > 1:
        arg = sys.argv[1].lower()
        if arg == "--intake":
            run_intake()
        elif arg in ["--intake-direct", "--direct"]:
            run_intake(direct=True)
        elif arg in ["--stage1", "--stagea"]:
            run_stage1()
        elif arg in ["--stage2", "--stageb"]:
            run_stage2()
        elif arg == "--all":
            run_intake()
            run_stage1()
            run_stage2()
        else:
            print("Unknown argument. Usage:")
            print("  python3 pipeline.py --intake          (Runs email-based intake)")
            print("  python3 pipeline.py --intake-direct   (Runs direct dashboard scraping, bypassing emails)")
            print("  python3 pipeline.py --stage1          (Runs Stage A filter on 'new' tenders)")
            print("  python3 pipeline.py --stage2          (Runs Stage B scorer on 'rules_passed' tenders)")
            print("  python3 pipeline.py --all             (Runs intake, stage1, and stage2 sequentially)")
    else:
        # Default behavior: run all stages end-to-end
        run_intake()
        run_stage1()
        run_stage2()


if __name__ == "__main__":
    main()

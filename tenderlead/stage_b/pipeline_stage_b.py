"""
pipeline_stage_b.py
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timedelta

from .document_classifier import classify_all_documents
from .document_collector import (
    collect_tender247_document_urls,
    collect_tenderdetail_document_urls,
)
from .document_downloader import cleanup_tender_documents, download_tender_documents
from .document_extractor import extract_tender_intelligence

DB_FILE = os.path.join(
    os.path.dirname(__file__), "..", "..", "tender_intelligence.db"
)
SETTINGS_FILE = os.path.join(
    os.path.dirname(__file__), "..", "..", "tender_rules_settings.json"
)


# ---------------------------------------------------------------------------
# Database helpers
# --
def _init_stage_b_columns():
    """Initializes the Stage B columns in the tender_leads and raw_tender_feed tables."""
    conn = sqlite3.connect(os.path.abspath(DB_FILE))
    cursor = conn.cursor()

    # Add columns to tender_leads
    cursor.execute("PRAGMA table_info(tender_leads)")
    existing_cols = {row[1] for row in cursor.fetchall()}
    new_cols = {
        "stage_b_scope":             "TEXT",
        "stage_b_qualification":     "TEXT",
        "stage_b_bid_documents":     "TEXT",   # JSON list stored as string
        "stage_b_confidence":        "TEXT",
        "stage_b_notes":             "TEXT",
        "stage_b_status":            "TEXT",
        "stage_b_source_documents":  "TEXT",   # JSON dict stored as string
        "stage_b_ran_at":            "TEXT",
    }
    for col, col_type in new_cols.items():
        if col not in existing_cols:
            cursor.execute(f"ALTER TABLE tender_leads ADD COLUMN {col} {col_type}")
            print(f"  Added column: {col}")

    # Also add to raw_tender_feed for status tracking
    cursor.execute("PRAGMA table_info(raw_tender_feed)")
    existing_raw = {row[1] for row in cursor.fetchall()}
    if "stage_b_status" not in existing_raw:
        cursor.execute("ALTER TABLE raw_tender_feed ADD COLUMN stage_b_status TEXT")

    conn.commit()
    conn.close()


def _get_rules_passed_tenders(days_back: int = 2) -> list[tuple]:
    """
    Returns 'rules_passed' tenders that:
      a) Haven't had Stage B run yet (stage_b_status is NULL or empty)
      b) Were created within the last `days_back` days (avoids reprocessing
         stale old tenders whose portal links are no longer valid)
    Pass days_back=0 to process ALL rules_passed tenders regardless of age.
    """
    conn = sqlite3.connect(os.path.abspath(DB_FILE))
    cursor = conn.cursor()

    cursor.execute("PRAGMA table_info(raw_tender_feed)")
    cols = {row[1] for row in cursor.fetchall()}

    stage_b_filter = ""
    if "stage_b_status" in cols:
        stage_b_filter = "AND (stage_b_status IS NULL OR stage_b_status = '')"

    date_filter = ""
    if days_back > 0:
        cutoff = (datetime.now() - timedelta(days=days_back)).isoformat()
        date_filter = f"AND created_at >= '{cutoff}'"

    # Targets: good_match (score >= 70) AND unsure (passed keywords, pending or borderline AI)
    # Also include legacy status values for backward compat with older DB rows
    cursor.execute(f"""
        SELECT tender_id, source, title, link
        FROM raw_tender_feed
        WHERE status IN ('good_match', 'unsure', 'lead_created', 'rules_passed')
        {stage_b_filter}
        {date_filter}
        ORDER BY
            CASE status
                WHEN 'good_match'  THEN 1
                WHEN 'lead_created' THEN 1
                WHEN 'unsure'       THEN 2
                WHEN 'rules_passed' THEN 2
            END,
            created_at DESC
    """)
    rows = cursor.fetchall()
    conn.close()
    return rows



def _save_stage_b_results(tender_id: str, results: dict, doc_links: list[dict]):
    """Saves Stage B extraction results to the tender_leads and raw_tender_feed tables."""
    conn = sqlite3.connect(os.path.abspath(DB_FILE))
    cursor = conn.cursor()

    # Build source documents summary with safe local paths for web access
    all_docs = []
    for doc in doc_links:
        if not doc.get("skipped", False) and doc.get("local_path"):
            basename = os.path.basename(doc["local_path"])
            web_path = f"uploads/{tender_id}/{basename}"
            all_docs.append({
                "name": doc["name"],
                "url": doc["url"],
                "path": web_path
            })
        else:
            all_docs.append({
                "name": doc.get("name", "Document"),
                "url": doc.get("url", ""),
                "path": None,
                "skipped": doc.get("skipped", True),
                "reason": doc.get("skip_reason")
            })

    source_docs = {
        "scope":         results.get("scope_source_documents", []),
        "qualification": results.get("qualification_source_documents", []),
        "bid_documents": results.get("bid_docs_source_documents", []),
        "all_docs":      all_docs,
    }

    now = datetime.now().isoformat()

    # Update tender_leads if the record exists
    cursor.execute("SELECT tender_id FROM tender_leads WHERE tender_id = ?", (tender_id,))
    if cursor.fetchone():
        cursor.execute("""
            UPDATE tender_leads SET
                scope_of_work            = COALESCE(NULLIF(?, ''), scope_of_work),
                stage_b_scope            = ?,
                stage_b_qualification    = ?,
                stage_b_bid_documents    = ?,
                stage_b_confidence       = ?,
                stage_b_notes            = ?,
                stage_b_status           = ?,
                stage_b_source_documents = ?,
                stage_b_ran_at           = ?
            WHERE tender_id = ?
        """, (
            results.get("scope_of_work", ""),
            results.get("scope_of_work", ""),
            results.get("qualification_criteria", ""),
            json.dumps(results.get("documents_required_for_bid", []), ensure_ascii=False),
            results.get("extraction_confidence", "low"),
            results.get("notes", ""),
            results.get("stage_b_status", "failed"),
            json.dumps(source_docs, ensure_ascii=False),
            now,
            tender_id,
        ))
    else:
        # Tender has not yet been promoted to tender_leads — save to a staging row anyway
        # so results aren't lost (it will be promoted when the AI scorer runs)
        print(f"  Note: tender {tender_id} not yet in tender_leads — results saved to raw_tender_feed only")

    # Update raw_tender_feed stage_b_status
    cursor.execute("""
        UPDATE raw_tender_feed
        SET stage_b_status = ?
        WHERE tender_id = ?
    """, (results.get("stage_b_status", "failed"), tender_id))

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Per-tender Stage B runner
# ---------------------------------------------------------------------------

def run_stage_b_for_tender(
    tender_id: str,
    source: str,
    title: str,
    detail_url: str,
    playwright_page=None,
) -> dict:
    """
    Runs the full Stage B document intelligence pipeline for a single tender.

    Args:
        tender_id:       The tender's unique ID.
        source:          "TenderDetail" or "Tender247".
        title:           The tender's title (for context in AI prompts).
        detail_url:      The detail page URL (used to locate document links).

    Returns:
        The extraction result dict from document_extractor.extract_tender_intelligence().
    """
    print(f"\n  --- Stage B: {tender_id} ({source}) ---")
    print(f"  Title: {title[:80]}")

    if playwright_page is None:
        print("  ERROR: Playwright page required but not provided.")
        return {
            "stage_b_status": "failed",
            "notes": "Playwright page not available for document collection.",
        }

    # Step 1: Collect document URLs
    print(f"  Step 1: Collecting document URLs from {source}...")
    if source == "TenderDetail":
        doc_links = collect_tenderdetail_document_urls(playwright_page, detail_url)
    else:
        doc_links = collect_tender247_document_urls(playwright_page, detail_url)

    if not doc_links:
        print("  No document links found. Stage B cannot proceed for this tender.")
        return {
    
            "notes": "No document download links found on detail page.",
            "scope_of_work": "",
            "qualification_criteria": "",
            "documents_required_for_bid": [],
        }

    print(f"  Found {len(doc_links)} document link(s)")

    # Step 2: Download documents
    print(f"  Step 2: Downloading {len(doc_links)} document(s)...")
    downloaded = download_tender_documents(tender_id, doc_links, source=source)

    # Step 3: Classify documents (first-page scan)
    print(f"  Step 3: Classifying documents...")
    classified = classify_all_documents(downloaded)

    readable_count = sum(
        1 for d in classified if not d.get("skipped") and not d.get("is_scanned")
    )
    print(f"  {readable_count}/{len(classified)} document(s) are readable")

    if readable_count == 0:
        print("  No readable documents. Stage B extraction cannot proceed.")
        _save_stage_b_results(tender_id, {
            "stage_b_status": "failed",
            "notes": "All documents are either scanned PDFs or failed to download.",
            "scope_of_work": "",
            "qualification_criteria": "",
            "documents_required_for_bid": [],
            "extraction_confidence": "low",
        }, downloaded)
        return {}

    # Step 4: Extract intelligence
    print(f"  Step 4: Extracting scope, qualification, and bid document requirements...")
    results = extract_tender_intelligence(classified, title, tender_id)

    print(f"  Extraction confidence: {results.get('extraction_confidence', 'unknown')}")
    print(f"  Scope of Work: {'Found' if results.get('scope_of_work') else 'NOT FOUND'}")
    print(f"  Qualification Criteria: {'Found' if results.get('qualification_criteria') else 'NOT FOUND'}")
    print(f"  Bid Documents: {len(results.get('documents_required_for_bid', []))} item(s)")

    # Step 5: Save results
    _save_stage_b_results(tender_id, results, downloaded)

    # Print summary
    if results.get("notes"):
        print(f"  Notes: {results['notes']}")

    return results


# ---------------------------------------------------------------------------
# Batch runner
# ---------------------------------------------------------------------------

def run_stage_b(days_back: int = 2):
    """
    Picks up all 'rules_passed' tenders from the DB (created within the last
    `days_back` days) that haven't had Stage B run yet, and runs the full
    document intelligence pipeline for each.

    Args:
        days_back: Only process tenders created within this many days.
                   Pass 0 to process ALL rules_passed tenders regardless of age.
    """
    print("\n==================================================")
    print("STAGE B: DOCUMENT INTELLIGENCE START")
    print("==================================================")

    _init_stage_b_columns()

    rows = _get_rules_passed_tenders(days_back=days_back)
    if not rows:
        msg = f"No 'rules_passed' tenders pending Stage B" + (
            f" (within last {days_back} day(s))" if days_back > 0 else ""
        ) + "."
        print(msg)
        print("\n==================================================")
        print("STAGE B: DOCUMENT INTELLIGENCE COMPLETED")
        print("==================================================")
        return

    print(f"Found {len(rows)} tender(s) pending Stage B analysis.")

    # Separate by source
    td_tenders  = [(tid, src, title, link) for tid, src, title, link in rows if src == "TenderDetail"]
    t247_tenders = [(tid, src, title, link) for tid, src, title, link in rows if src == "Tender247"]

    # --------------- TenderDetail tenders (Playwright with TenderDetail session) ---------------
    if td_tenders:
        print(f"\nProcessing {len(td_tenders)} TenderDetail tender(s) via Playwright...")
        playwright_ctx = None
        browser = None
        page = None
        try:
            from ..scrapers.tenderdetail_session import get_authenticated_page
            print("  Launching Playwright for TenderDetail...")
            page, browser, playwright_ctx = get_authenticated_page(headless=True)

            for idx, (tender_id, source, title, link) in enumerate(td_tenders, 1):
                print(f"\n[{idx}/{len(td_tenders)}]")
                try:
                    run_stage_b_for_tender(tender_id, source, title, link, playwright_page=page)
                except Exception as e:
                    print(f"  ERROR running Stage B for {tender_id}: {e}")

        except Exception as e:
            print(f"  ERROR: Could not launch Playwright for TenderDetail: {e}")
        finally:
            if browser:
                browser.close()
            if playwright_ctx:
                playwright_ctx.stop()

    # --------------- Tender247 tenders (Playwright session needed) ---------------
    if t247_tenders:
        print(f"\nProcessing {len(t247_tenders)} Tender247 tender(s)...")
        playwright_ctx = None
        browser = None
        page = None

        try:
            from ..scrapers.tender247_session import get_authenticated_page
            print("  Launching Playwright for Tender247...")
            page, browser, playwright_ctx = get_authenticated_page(headless=True)

            for idx, (tender_id, source, title, link) in enumerate(t247_tenders, 1):
                print(f"\n[{idx}/{len(t247_tenders)}]")
                try:
                    run_stage_b_for_tender(tender_id, source, title, link, playwright_page=page)
                except Exception as e:
                    print(f"  ERROR running Stage B for {tender_id}: {e}")

        except Exception as e:
            print(f"  ERROR: Could not launch Playwright for Tender247: {e}")
        finally:
            if browser:
                browser.close()
            if playwright_ctx:
                playwright_ctx.stop()

    print("\n==================================================")
    print("STAGE B: DOCUMENT INTELLIGENCE COMPLETED")
    print("==================================================")


# ---------------------------------------------------------------------------
# Decoupled Scraper & AI Extractors
# ---------------------------------------------------------------------------

def parse_ec_and_dc_from_ai_summary(text: str) -> tuple[str, list[str]]:
    """
    Parses Eligibility Criteria (EC) and Document Checklist (DC) from Tender247's AI summary text,
    filtering out top-level vertical metadata (e.g. Tender Id, GEM Bid number, dates).
    """
    if not text:
        return "", []
        
    lines = text.split("\n")
    eligibility_lines = []
    documents_lines = []
    
    metadata_keys = [
        "tender id", "gem bid number", "bid end date", "bid opening date", 
        "bid offer validity", "ministry state name", "department name", 
        "organisation name", "office name", "item category", 
        "nature of requirement", "submission date", "opening date", 
        "tender estimated cost", "emd", "tender document fees", 
        "brief", "description", "quantity", "website", "msme exemption", 
        "startup exemption", "site location", "contact person", 
        "contact address", "contact number", "mse purchase preference", 
        "surety bond", "pre bid meeting date"
    ]
    
    current_section = "eligibility"
    
    i = 0
    while i < len(lines):
        line_strip = lines[i].strip()
        if not line_strip:
            i += 1
            continue
        line_lower = line_strip.lower()
        
        # Section detection
        if ("eligibility" in line_lower or "qualification" in line_lower or "pre-qualification" in line_lower) and len(line_strip) < 60:
            current_section = "eligibility"
            i += 1
            continue
        elif ("documents required" in line_lower or "document required" in line_lower or "checklist" in line_lower or "documents list" in line_lower or "bid documents" in line_lower) and len(line_strip) < 60:
            current_section = "documents"
            i += 1
            continue
        elif ("tender overview" in line_lower or "tender documents" in line_lower or "previous/similar result" in line_lower or "list of bidders" in line_lower or "disclaimer" in line_lower or "about authority" in line_lower or "about organization" in line_lower or "project background" in line_lower) and len(line_strip) < 60:
            current_section = None
            i += 1
            continue
            
        # General skip for lone ':' lines
        if line_strip == ":":
            i += 1
            continue
            
        # Skip metadata blocks (label, colon, value)
        is_metadata = False
        for key in metadata_keys:
            if line_lower.startswith(key) and len(line_strip) < 40:
                is_metadata = True
                break
                
        if is_metadata:
            i += 1
            # Skip optional following colon
            if i < len(lines) and lines[i].strip() == ":":
                i += 1
            # Skip value line
            if i < len(lines):
                i += 1
            continue
            
        if current_section == "eligibility":
            if "ai generated tender summary" in line_lower or "bid / no bid decision" in line_lower or "summary" == line_lower:
                i += 1
                continue
            eligibility_lines.append(line_strip)
        elif current_section == "documents":
            clean_doc = re.sub(r'^[-*\*•\d\.\s\)\(]+', '', line_strip).strip()
            if clean_doc:
                documents_lines.append(clean_doc)
        
        i += 1
        
    eligibility_text = "\n".join(eligibility_lines).strip()
    return eligibility_text, documents_lines


def extract_ai_summary_from_current_page(page) -> str | None:
    """
    Reads the AI summary text directly from an already open Playwright detail page.
    """
    try:
        header_selector = "h2:has-text('AI Generated Tender Summary')"
        if page.locator(header_selector).count() == 0:
            header_selector = "text=AI Generated Tender Summary"
        
        if page.locator(header_selector).count() > 0:
            header_loc = page.locator(header_selector).first
            
            # Check if card is expanded
            is_expanded = False
            for text_selector in ["text=Tender Id", "text=Checklist", "text=Generate", "text=GST", "text=Material", "text=Summary"]:
                button_loc = header_loc.locator("xpath=..")
                h3_loc = button_loc.locator("xpath=..")
                content_loc = h3_loc.locator("xpath=./following-sibling::div[1]")
                
                if content_loc.count() > 0:
                    loc = content_loc.locator(text_selector)
                    if loc.count() > 0 and loc.first.is_visible():
                        is_expanded = True
                        break
            
            if not is_expanded:
                print("  AI Summary block appears collapsed. Clicking header to expand...")
                header_loc.click()
                page.wait_for_timeout(2000)
            
            # Get the content sibling of the H3 (grandparent of the H2 header)
            button_loc = header_loc.locator("xpath=..")
            h3_loc = button_loc.locator("xpath=..")
            content_loc = h3_loc.locator("xpath=./following-sibling::div[1]")
            
            if content_loc.count() > 0:
                # Click Summary tab if present inside the content panel
                summary_tab = content_loc.locator("text=Summary")
                if summary_tab.count() > 0 and summary_tab.first.is_visible():
                    print("  Clicking 'Summary' tab in AI summary card...")
                    summary_tab.first.click()
                    page.wait_for_timeout(1500)
                
                # Check for generate button
                generate_btn = content_loc.locator("button:has-text('Generate')")
                if generate_btn.count() == 0:
                    generate_btn = content_loc.locator("text=Generate")
                if generate_btn.count() > 0 and generate_btn.first.is_visible():
                    print("  AI summary not yet generated. Clicking 'Generate'...")
                    generate_btn.first.click()
                    page.wait_for_timeout(4000)
                
                return content_loc.first.inner_text().strip()
                
            return header_loc.locator("xpath=..").inner_text().strip() # fallback
    except Exception as e:
        print(f"  Error reading AI summary card text: {e}")
    return None


def save_scraped_documents_metadata(
    tender_id: str, 
    doc_links: list[dict],
    eligibility: str = "",
    documents_checklist: list[str] = None
):
    """Saves the scraped document metadata and pre-extracted portal fields to tender_leads and raw_tender_feed."""
    conn = sqlite3.connect(os.path.abspath(DB_FILE))
    cursor = conn.cursor()

    all_docs = []
    for doc in doc_links:
        if not doc.get("skipped", False) and doc.get("local_path"):
            basename = os.path.basename(doc["local_path"])
            web_path = f"uploads/{tender_id}/{basename}"
            all_docs.append({
                "name": doc["name"],
                "url": doc["url"],
                "path": web_path
            })
        else:
            all_docs.append({
                "name": doc.get("name", "Document"),
                "url": doc.get("url", ""),
                "path": None,
                "skipped": doc.get("skipped", True),
                "reason": doc.get("skip_reason")
            })

    source_docs = {
        "scope":         [],
        "qualification": [],
        "bid_documents": [],
        "all_docs":      all_docs,
    }

    now = datetime.now().isoformat()

    # Ensure stage_b columns are initialized
    _init_stage_b_columns()

    db_docs = json.dumps(documents_checklist or [], ensure_ascii=False) if documents_checklist else None

    # Update tender_leads if the record exists
    cursor.execute("SELECT tender_id FROM tender_leads WHERE tender_id = ?", (tender_id,))
    if cursor.fetchone():
        cursor.execute("""
            UPDATE tender_leads SET
                stage_b_status           = 'scraped',
                stage_b_source_documents = ?,
                stage_b_qualification    = COALESCE(?, stage_b_qualification),
                stage_b_bid_documents    = COALESCE(?, stage_b_bid_documents),
                stage_b_ran_at           = ?
            WHERE tender_id = ?
        """, (
            json.dumps(source_docs, ensure_ascii=False),
            eligibility or None,
            db_docs,
            now,
            tender_id,
        ))
    else:
        # Create a new record in tender_leads if not promoted yet (manual uploader flow or fallback)
        cursor.execute("""
            SELECT source, title, authority, location, value, emd, due_date, link 
            FROM raw_tender_feed 
            WHERE tender_id = ?
        """, (tender_id,))
        r = cursor.fetchone()
        if r:
            cursor.execute("""
                INSERT INTO tender_leads (
                    tender_id, source, title, authority, location, estimated_value, emd, due_date, source_link, 
                    created_at, stage_b_status, stage_b_source_documents, stage_b_qualification, stage_b_bid_documents, stage_b_ran_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'scraped', ?, ?, ?, ?)
            """, (
                tender_id, r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7],
                now, json.dumps(source_docs, ensure_ascii=False), eligibility or None, db_docs, now
            ))

    # Update raw_tender_feed stage_b_status
    cursor.execute("""
        UPDATE raw_tender_feed
        SET stage_b_status = 'scraped'
        WHERE tender_id = ?
    """, (tender_id,))

    conn.commit()
    conn.close()
    print(f"  Saved {len(all_docs)} scraped documents metadata for tender {tender_id}.")


def scrape_tender_documents_only(
    tender_id: str,
    source: str,
    title: str,
    detail_url: str,
    playwright_page=None,
) -> list[dict]:
    """
    Crawls, collects document links, downloads them (including zip extraction),
    pre-scrapes portal AI summary, and saves the document metadata in the DB as 'scraped' state.
    """
    print(f"\n  --- Scrape Documents Only: {tender_id} ({source}) ---")
    
    if playwright_page is None:
        print("  ERROR: Playwright page required but not provided.")
        return []

    # Step 1: Collect document URLs (resolves detail URL internally if truncated)
    print(f"  Step 1: Collecting document URLs from {source}...")
    if source == "TenderDetail":
        doc_links = collect_tenderdetail_document_urls(playwright_page, detail_url)
    else:
        doc_links = collect_tender247_document_urls(playwright_page, detail_url)

    if not doc_links:
        print("  No document links found.")
        return []

    print(f"  Found {len(doc_links)} document link(s)")

    # Pre-scrape Tender247 AI Summary from current page
    eligibility = ""
    documents_checklist = []
    if source == "Tender247":
        try:
            print("  Extracting AI Summary / Eligibility block directly from current page...")
            summary_text = extract_ai_summary_from_current_page(playwright_page)
            if summary_text:
                eligibility, documents_checklist = parse_ec_and_dc_from_ai_summary(summary_text)
                print(f"  Successfully extracted Eligibility (length: {len(eligibility)}) and Document Checklist ({len(documents_checklist)} items) from Tender247 portal details.")
        except Exception as e:
            print(f"  Warning: failed to extract Tender247 AI Summary: {e}")

    # Step 2: Download documents
    print(f"  Step 2: Downloading {len(doc_links)} document(s)...")
    downloaded = download_tender_documents(tender_id, doc_links, source=source)

    # Step 3: Save scraped documents metadata and pre-extracted portal fields to DB
    save_scraped_documents_metadata(tender_id, downloaded, eligibility=eligibility, documents_checklist=documents_checklist)

    return downloaded


def extract_tender_details_via_ai(tender_id: str, title: str) -> dict:
    """
    Reads the list of already downloaded documents from tender_leads,
    classifies them, runs LLM extraction, and saves SOW, eligibility, and checklist.
    """
    print(f"\n  --- Extract Details via AI: {tender_id} ---")
    
    # 1. Fetch source documents from tender_leads
    conn = sqlite3.connect(os.path.abspath(DB_FILE))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT stage_b_source_documents, stage_b_qualification, stage_b_bid_documents FROM tender_leads WHERE tender_id = ?", (tender_id,))
    row = cursor.fetchone()
    conn.close()
    
    if not row or not row["stage_b_source_documents"]:
        print("  ERROR: No scraped documents found in DB. Run scraper first.")
        return {
            "stage_b_status": "failed",
            "notes": "No scraped documents found in DB. Run scraper first."
        }

    pre_eligibility = row["stage_b_qualification"] or ""
    pre_docs = None
    if row["stage_b_bid_documents"]:
        try:
            pre_docs = json.loads(row["stage_b_bid_documents"])
        except Exception:
            pass
        
    source_docs = json.loads(row["stage_b_source_documents"])
    all_docs = source_docs.get("all_docs", [])
    
    # Map back to the downloaded list format expected by classify_all_documents
    downloaded = []
    for doc in all_docs:
        local_path = None
        if doc.get("path"):
            # Web path is format: "uploads/{tender_id}/{basename}"
            # Absolute path: "frontend/uploads/{tender_id}/{basename}"
            local_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "frontend", doc["path"]))
            
        downloaded.append({
            "name": doc["name"],
            "url": doc["url"],
            "local_path": local_path,
            "skipped": doc.get("skipped", False),
            "skip_reason": doc.get("reason")
        })
        
    # Step 3: Classify documents (first-page scan)
    print(f"  Classifying documents...")
    classified = classify_all_documents(downloaded)
    
    readable_count = sum(1 for d in classified if not d.get("skipped") and not d.get("is_scanned"))
    print(f"  {readable_count}/{len(classified)} document(s) are readable")
    
    if readable_count == 0 and not pre_eligibility:
        print("  No readable documents and no pre-scraped eligibility criteria. Stage B extraction cannot proceed.")
        results = {
            "stage_b_status": "failed",
            "notes": "All documents are either scanned PDFs or failed to download.",
            "scope_of_work": "",
            "qualification_criteria": "",
            "documents_required_for_bid": [],
            "extraction_confidence": "low",
        }
        _save_stage_b_results(tender_id, results, downloaded)
        return results

    # Step 4: Extract intelligence
    print(f"  Extracting scope, qualification, and bid document requirements...")
    results = extract_tender_intelligence(
        classified, 
        title, 
        tender_id, 
        pre_extracted_eligibility=pre_eligibility, 
        pre_extracted_documents=pre_docs
    )
    
    # Save results
    if results.get("stage_b_status") != "failed":
        results["stage_b_status"] = "completed"
    _save_stage_b_results(tender_id, results, downloaded)
    
    return results




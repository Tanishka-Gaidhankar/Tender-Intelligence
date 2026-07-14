import requests
import json
import os
import time

from tenderlead.scrapers.tenderdetail_session import get_authenticated_page as get_tenderdetail_page, scrape_all_query_tenders
from tenderlead.scrapers.tender247_session import get_authenticated_page as get_tender247_page
from tenderlead.scrapers.tender247_scraper import parse_tender247_dashboard
from tenderlead.stage_b.document_collector import collect_tenderdetail_document_urls, collect_tender247_document_urls
from tenderlead.stage_b.document_downloader import download_tender_documents, cleanup_tender_documents

SITE_URL = "https://demokbp.m.frappe.cloud"
API_KEY = "7b297c61a9c0294"
API_SECRET = "47f108f27186fa4"
headers = {"Authorization": f"token {API_KEY}:{API_SECRET}"}

def run_stage1_listings(source):
    """Runs the Playwright scraper to get listing data."""
    results = []
    source_clean = "Tender Detail" if "detail" in source.lower() else "Tender247"
    if source_clean == "Tender Detail":
        page, browser, playwright_ctx = get_tenderdetail_page(headless=True)
        try:
            results = scrape_all_query_tenders(page)
        finally:
            browser.close()
            playwright_ctx.stop()
    elif source_clean == "Tender247":
        page, browser, playwright_ctx = get_tender247_page(headless=True)
        try:
            # Scroll to load all tenders
            print("[Agent] Scrolling to load all tenders on Tender247 dashboard...")
            previous_count = 0
            for scroll_attempt in range(1, 10):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(1500)
                current_count = page.locator("span:has-text('T247 ID')").count()
                if current_count == previous_count:
                    break
                previous_count = current_count
            html = page.content()
            results = parse_tender247_dashboard(html)
        finally:
            browser.close()
            playwright_ctx.stop()
            
    # Normalize results mapping
    normalized = []
    for r in results:
        normalized.append({
            "tender_id": r.get("tender_id"),
            "source": source_clean,
            "title": r.get("title"),
            "authority": r.get("authority"),
            "location": r.get("location"),
            "value": r.get("tender_value") or r.get("bid_value"),
            "emd": r.get("emd"),
            "due_date": r.get("due_date"),
            "link": r.get("view_tender_url") or r.get("ai_summary_url")
        })
    return normalized

def run_stage2_docs(tenders, source):
    """Downloads files for the given tenders list."""
    downloaded_files_map = {}
    if not tenders:
        return downloaded_files_map
    source_clean = "Tender Detail" if "detail" in source.lower() else "Tender247"
    if source_clean == "Tender Detail":
        page, browser, playwright_ctx = get_tenderdetail_page(headless=True)
    else:
        page, browser, playwright_ctx = get_tender247_page(headless=True)
    try:
        for t in tenders:
            tender_id = t["tender_id"]
            link = t["link"]
            if source_clean == "Tender Detail":
                doc_links = collect_tenderdetail_document_urls(page, link)
            else:
                doc_links = collect_tender247_document_urls(page, link)
            if doc_links:
                dl_results = download_tender_documents(tender_id, doc_links, source_clean)
                paths = [r["local_path"] for r in dl_results if r.get("local_path") and os.path.exists(r["local_path"])]
                downloaded_files_map[tender_id] = paths
    finally:
        browser.close()
        playwright_ctx.stop()
    return downloaded_files_map

def process_job(job_data):
    """Executes the claimed job."""
    job_id = job_data.get("job_id")
    job_type = job_data.get("job_type")
    docname = job_data.get("docname")
    payload = job_data.get("payload", {})
    try:
        if job_type == "Stage 1":
            source = payload.get("source")
            print(f"[Agent] Processing Stage 1 for source: {source}...")
            results = run_stage1_listings(source)
            resp = requests.post(
                f"{SITE_URL}/api/method/tenderlead.api.ingest_stage1_results",
                headers=headers,
                data={"job_id": job_id, "docname": docname, "tenders": json.dumps(results)},
                timeout=60
            )
            print(f"[Agent] Ingestion Status: {resp.status_code}, Response: {resp.text}")
        elif job_type == "Stage 2":
            tenders = payload.get("tenders", [])
            if tenders:
                source = tenders[0].get("source", "Tender247")
                print(f"[Agent] Processing Stage 2 for {len(tenders)} tenders from {source}...")
                files_map = run_stage2_docs(tenders, source)
                for tender_id, file_paths in files_map.items():
                    files_payload = {}
                    opened_files = []
                    upload_success = False
                    try:
                        for idx, path in enumerate(file_paths):
                            f = open(path, "rb")
                            opened_files.append(f)
                            files_payload[f"file_{idx}"] = (os.path.basename(path), f)
                        resp = requests.post(
                            f"{SITE_URL}/api/method/tenderlead.api.ingest_stage2_documents",
                            headers=headers,
                            data={"job_id": job_id, "tender_id": tender_id},
                            files=files_payload,
                            timeout=120
                        )
                        print(f"[Agent] Upload status for tender {tender_id}: {resp.status_code}")
                        if resp.status_code == 200:
                            upload_success = True
                    finally:
                        for f in opened_files:
                            f.close()
                        if upload_success:
                            cleanup_tender_documents(tender_id)
                        else:
                            print(f"[Warning] Ingestion failed for {tender_id}. Local files preserved.")
    except Exception as e:
        print(f"[Agent Error] Job {job_id} failed: {e}")
        try:
            requests.post(
                f"{SITE_URL}/api/method/tenderlead.api.report_job_failure",
                headers=headers,
                json={"job_id": job_id, "error_message": str(e)},
                timeout=15
            )
        except Exception as report_err:
            print(f"[Agent Critical] Failed to report job failure: {report_err}")

def poll_for_jobs():
    """Main loop that polls for and processes jobs from the queue."""
    print("\n==============================================")
    print("Local Scraper Agent started.")
    print("Polling database queue for new scrape jobs...")
    print("==============================================")
    while True:
        try:
            # Poll for the next queued job using token auth
            resp = requests.post(
                f"{SITE_URL}/api/method/tenderlead.api.claim_next_job",
                headers=headers,
                timeout=15
            )
            if resp.status_code == 200:
                job_data = resp.json().get("message")
                if job_data:
                    print(f"\n[JOB RECEIVED] Claimed job: {job_data['job_id']} ({job_data['job_type']})")
                    process_job(job_data)
                    print("[Job Finished] Polling for next job immediately...")
                    continue # Poll again immediately in case more jobs are queued
            else:
                print(f"[Agent Error] Polling request returned status code {resp.status_code}")
        except Exception as e:
            print(f"[Agent Error] Polling connection error: {e}")
        # Poll every 5 seconds if no jobs are available
        time.sleep(5)

if __name__ == "__main__":
    poll_for_jobs()
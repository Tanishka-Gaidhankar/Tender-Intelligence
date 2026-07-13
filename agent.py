import socketio
import queue
import threading
import requests
import json
import os

from tenderlead.scrapers.tenderdetail_session import get_authenticated_page as get_tenderdetail_page, scrape_all_query_tenders
from tenderlead.scrapers.tender247_session import get_authenticated_page as get_tender247_page
from tenderlead.scrapers.tender247_scraper import parse_tender247_dashboard
from tenderlead.stage_b.document_collector import collect_tenderdetail_document_urls, collect_tender247_document_urls
from tenderlead.stage_b.document_downloader import download_tender_documents, cleanup_tender_documents

SITE_URL = "https://demokbp.m.frappe.cloud"
API_KEY = "7b297c61a9c0294"
API_SECRET = "47f108f27186fa4"
headers = {"Authorization": f"token {API_KEY}:{API_SECRET}"}

sio = socketio.Client(logger=True, engineio_logger=True)
job_queue = queue.Queue()

def run_stage1_listings(source):
    """Runs the Playwright scraper to get listing data."""
    results = []
    if source == "TenderDetail":
        page, browser, playwright_ctx = get_tenderdetail_page(headless=True)
        try:
            results = scrape_all_query_tenders(page)
        finally:
            browser.close()
            playwright_ctx.stop()
    elif source == "Tender247":
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
            
    # Normalize results mapping (convert keys if necessary for Frappe compatibility)
    normalized = []
    for r in results:
        normalized.append({
            "tender_id": r.get("tender_id"),
            "source": source,
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
    downloaded_files_map = {} # tender_id -> list of file paths
    
    if not tenders:
        return downloaded_files_map
        
    # Launch browser once to collect all links
    if source == "TenderDetail":
        page, browser, playwright_ctx = get_tenderdetail_page(headless=True)
    else:
        page, browser, playwright_ctx = get_tender247_page(headless=True)
        
    try:
        for t in tenders:
            tender_id = t["tender_id"]
            link = t["link"]
            
            if source == "TenderDetail":
                doc_links = collect_tenderdetail_document_urls(page, link)
            else:
                doc_links = collect_tender247_document_urls(page, link)
                
            if doc_links:
                # Download files locally
                dl_results = download_tender_documents(tender_id, doc_links, source)
                paths = [r["local_path"] for r in dl_results if r.get("local_path") and os.path.exists(r["local_path"])]
                downloaded_files_map[tender_id] = paths
    finally:
        browser.close()
        playwright_ctx.stop()
        
    return downloaded_files_map

def worker():
    while True:
        job = job_queue.get()
        if job is None: break
        
        job_type = job.get("job_type")
        job_id = job.get("job_id")
        docname = job.get("docname")
        
        try:
            if job_type == "stage1":
                source = job.get("source")
                print(f"[Agent] Stage 1 for source: {source}")
                
                results = run_stage1_listings(source)
                
                # Ingest results directly with timeout
                resp = requests.post(
                    f"{SITE_URL}/api/method/tenderlead.api.ingest_stage1_results",
                    headers=headers,
                    data={"job_id": job_id, "docname": docname, "tenders": json.dumps(results)},
                    timeout=60
                )
                print(f"[Agent] Stage 1 Ingestion Status: {resp.status_code}, Response: {resp.text}")
                
            elif job_type == "stage2":
                tenders = job.get("tenders")  # list of {"tender_id": ..., "link": ...}
                if tenders:
                    # Get source from the first tender reference or config
                    source = tenders[0].get("source", "Tender247")
                    print(f"[Agent] Stage 2 downloading documents for {len(tenders)} tenders from {source}...")
                    
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
                            # Clean up temporary downloads ONLY if upload was successful
                            if upload_success:
                                cleanup_tender_documents(tender_id)
                            else:
                                print(f"[Warning] Ingestion failed for {tender_id}. Local files preserved under uploads/{tender_id}")
                
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
                print(f"[Agent Critical] Failed to report job failure to server: {report_err}")
        finally:
            job_queue.task_done()

threading.Thread(target=worker, daemon=True).start()

@sio.event
def connect():
    print("Connected to Frappe Socket.IO server!")

@sio.on("stage1_trigger")
def on_stage1(data):
    print(f"Received Stage 1 trigger: {data}")
    job_queue.put({
        "job_type": "stage1",
        "job_id": data.get("job_id"),
        "docname": data.get("docname"),
        "source": data.get("source"),
        "screening_date": data.get("screening_date")
    })

@sio.on("stage2_trigger")
def on_stage2(data):
    print(f"Received Stage 2 trigger: {data}")
    job_queue.put({
        "job_type": "stage2",
        "job_id": data.get("job_id"),
        "docname": data.get("docname"),
        "tenders": data.get("tenders")
    })

if __name__ == "__main__":
    # 1. Fetch a valid session cookie (sid) using API credentials
    print(f"Authenticating with {SITE_URL}...")
    try:
        response = requests.post(
            f"{SITE_URL}/api/method/frappe.auth.get_logged_user",
            headers=headers,
            timeout=15
        )
        response.raise_for_status()
        
        # Extract the sid cookie
        sid = response.cookies.get("sid")
        if not sid:
            raise RuntimeError("Authentication failed: session cookie (sid) not found in response.")
        else:
            print("Authentication successful! Session cookie obtained.")
            sio_headers = {
                "Cookie": f"sid={sid};",
                "Authorization": f"token {API_KEY}:{API_SECRET}"
            }
            
    except Exception as e:
        print(f"Authentication request failed: {e}. Connecting with default headers...")
        sio_headers = headers

    # 2. Connect to Socket.IO with cookie credentials
    sio.connect(
        SITE_URL,
        headers=sio_headers
    )
    sio.wait()


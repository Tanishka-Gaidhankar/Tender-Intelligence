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

def get_dates_to_scrape(screening_date, from_date, to_date):
    from datetime import datetime, timedelta
    
    if from_date and to_date:
        try:
            start = datetime.strptime(from_date, "%Y-%m-%d")
            end = datetime.strptime(to_date, "%Y-%m-%d")
            if start <= end:
                delta = end - start
                dates = []
                for i in range(delta.days + 1):
                    day = start + timedelta(days=i)
                    dates.append(day.strftime("%Y-%m-%d"))
                return dates
        except Exception as e:
            print(f"[Agent Warning] Error parsing date range: {e}")
            
    return [screening_date] if screening_date else [None]

def run_stage1_listings(source, screening_date=None, from_date=None, to_date=None):
    """Runs the Playwright scraper to get listing data, optionally from email or UI picker for specific dates."""
    dates_to_scrape = get_dates_to_scrape(screening_date, from_date, to_date)
    all_results = []
    seen_ids = set()
    
    source_clean = "Tender Detail" if "detail" in source.lower() else "Tender247"
    
    if source_clean == "Tender Detail":
        for d in dates_to_scrape:
            # Try email search first
            if d:
                print(f"[Agent] Attempting to find email alert for Tender Detail on {d}...")
                try:
                    from tenderlead import email_reader
                    emails_found = email_reader.fetch_todays_emails(target_date=d)
                    emails_found.pop("_debug_all_subjects_seen", None)
                    
                    if "TenderDetail" in emails_found:
                        batch = emails_found["TenderDetail"][0]
                        url = email_reader.extract_tenderdetail_view_all_url(batch["html"])
                        if url:
                            print(f"[Agent] Found TenderDetail email link for {d}: {url}")
                            headers_agent = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
                            res = requests.get(url, headers=headers_agent, timeout=30)
                            if res.status_code == 200:
                                from tenderlead.scrapers.tenderdetail_scraper import parse_tenderdetail_listings
                                raw_results = parse_tenderdetail_listings(res.text)
                                for r in raw_results:
                                    tid = r.get("tender_id")
                                    if tid and tid not in seen_ids:
                                        seen_ids.add(tid)
                                        all_results.append({
                                            "tender_id": tid,
                                            "source": source_clean,
                                            "title": r.get("title"),
                                            "authority": r.get("authority"),
                                            "location": r.get("location"),
                                            "value": r.get("tender_value") or r.get("bid_value"),
                                            "emd": r.get("emd"),
                                            "due_date": r.get("due_date"),
                                            "link": r.get("view_tender_url") or r.get("ai_summary_url")
                                        })
                                print(f"[Agent] Successfully scraped {len(all_results)} tenders so far.")
                                continue
                except Exception as e:
                    print(f"[Agent Warning] Tender Detail email intake failed for {d}: {e}")
            
            # Fallback to live dashboard query search
            print(f"[Agent] Scraping live dashboard for Tender Detail (Date: {d})...")
            page, browser, playwright_ctx = get_tenderdetail_page(headless=True)
            try:
                raw_results = scrape_all_query_tenders(page)
                for r in raw_results:
                    tid = r.get("tender_id")
                    if tid and tid not in seen_ids:
                        seen_ids.add(tid)
                        all_results.append({
                            "tender_id": tid,
                            "source": source_clean,
                            "title": r.get("title"),
                            "authority": r.get("authority"),
                            "location": r.get("location"),
                            "value": r.get("tender_value") or r.get("bid_value"),
                            "emd": r.get("emd"),
                            "due_date": r.get("due_date"),
                            "link": r.get("view_tender_url") or r.get("ai_summary_url")
                        })
            finally:
                browser.close()
                playwright_ctx.stop()
                
    elif source_clean == "Tender247":
        page, browser, playwright_ctx = get_tender247_page(headless=True)
        try:
            for d in dates_to_scrape:
                if d:
                    parts = d.split("-")
                    if len(parts) == 3:
                        target_year = int(parts[0])
                        target_month_num = int(parts[1])
                        target_day = int(parts[2])
                        
                        months_list = ["January", "February", "March", "April", "May", "June", 
                                       "July", "August", "September", "October", "November", "December"]
                        target_month_name = months_list[target_month_num - 1]
                        target_caption = f"{target_month_name} {target_year}"
                        
                        print(f"[Agent] Selecting mail date {target_caption}, Day: {target_day} on Tender247 dashboard...")
                        try:
                            # 1. Open the popover by clicking the button
                            btn = page.locator("text=Select Mail Date").locator("xpath=../button")
                            if btn.count() > 0:
                                btn.click()
                                page.wait_for_timeout(1000)
                                
                                # 2. Navigate months
                                months_map = {m.lower(): idx + 1 for idx, m in enumerate(months_list)}
                                for attempt in range(24):
                                    current_caption_el = page.locator("#react-day-picker-1, .rdp-caption_start, .rdp-caption_end").first
                                    full_text = current_caption_el.inner_text().strip()
                                    current_caption = full_text.split("\n")[0].strip()
                                    
                                    if current_caption.lower() == target_caption.lower():
                                        break
                                        
                                    curr_parts = current_caption.lower().split()
                                    curr_month_num = months_map[curr_parts[0]]
                                    curr_year = int(curr_parts[1])
                                    
                                    if curr_year > target_year or (curr_year == target_year and curr_month_num > target_month_num):
                                        page.click("button[name='previous-month']")
                                    else:
                                        page.click("button[name='next-month']")
                                    page.wait_for_timeout(500)
                                    
                                # 3. Click the day
                                day_buttons = page.locator("button[name='day']:not(.day-outside)").all()
                                day_clicked = False
                                for d_btn in day_buttons:
                                    if d_btn.inner_text().strip() == str(target_day):
                                        d_btn.click()
                                        day_clicked = True
                                        break
                                if day_clicked:
                                    print(f"[Agent] Date {d} selected successfully.")
                                    page.wait_for_timeout(4000) # Wait for table dynamic update
                                else:
                                    print(f"[Agent Warning] Day button {target_day} not found in month {current_caption}!")
                            else:
                                print("[Agent Warning] Select Mail Date button not found on page.")
                        except Exception as date_err:
                            print(f"[Agent Warning] Failed to select date {d} via UI picker: {date_err}")

                print(f"[Agent] Scrolling to load all tenders on Tender247 dashboard (Date: {d})...")
                previous_count = 0
                for scroll_attempt in range(1, 10):
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(1500)
                    current_count = page.locator("span:has-text('T247 ID')").count()
                    if current_count == previous_count:
                        break
                    previous_count = current_count
                html = page.content()
                raw_results = parse_tender247_dashboard(html)
                
                for r in raw_results:
                    tid = r.get("tender_id")
                    if tid and tid not in seen_ids:
                        seen_ids.add(tid)
                        all_results.append({
                            "tender_id": tid,
                            "source": source_clean,
                            "title": r.get("title"),
                            "authority": r.get("authority"),
                            "location": r.get("location"),
                            "value": r.get("tender_value") or r.get("bid_value"),
                            "emd": r.get("emd"),
                            "due_date": r.get("due_date"),
                            "link": r.get("view_tender_url") or r.get("ai_summary_url")
                        })
        finally:
            browser.close()
            playwright_ctx.stop()
            
    return all_results

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
            screening_date = payload.get("screening_date")
            from_date = payload.get("from_date")
            to_date = payload.get("to_date")
            print(f"[Agent] Processing Stage 1 for source: {source} (screening date: {screening_date}, from_date: {from_date}, to_date: {to_date})...")
            results = run_stage1_listings(source, screening_date, from_date, to_date)
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
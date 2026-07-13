import frappe
from frappe.utils import now_datetime
import json

@frappe.whitelist()
def trigger_stage1_scan(docname):
    """Triggers Stage 1 Playwright scraper by creating a Queued Scrape Job Log."""
    doc = frappe.get_doc("Tender Primary Screening", docname)
    
    # Store trigger parameters in JSON payload
    payload_data = {
        "docname": docname,
        "source": doc.tender_source,
        "screening_date": str(doc.screening_date) if doc.screening_date else None
    }
    
    # Create the job log entry in Queued status
    job = frappe.get_doc({
        "doctype": "Scrape Job Log",
        "job_type": "Stage 1",
        "status": "Queued",
        "payload": json.dumps(payload_data)
    }).insert(ignore_permissions=True)
    
    return {"status": "success", "job_id": job.name}

@frappe.whitelist()
def trigger_stage2_scan(docname):
    """Triggers Stage 2 Playwright document downloader by creating a Queued Scrape Job Log."""
    parent_doc = frappe.get_doc("Tender Primary Screening", docname)
    
    # Collect tender IDs that require document scraping
    tenders_to_scrape = []
    for row in parent_doc.raw_tender_leads:
        lead_status = frappe.db.get_value("Raw Tender Lead", row.tender_id, "status")
        if lead_status in ["Good Match", "May be"]:
            tenders_to_scrape.append({
                "tender_id": row.tender_id,
                "link": row.link,
                "source": row.source
            })
            
    if not tenders_to_scrape:
        frappe.throw("No approved/shortlisted tenders found for secondary screening.")
        
    # Store trigger parameters in JSON payload
    payload_data = {
        "docname": docname,
        "tenders": tenders_to_scrape
    }
    
    # Create the job log entry in Queued status
    job = frappe.get_doc({
        "doctype": "Scrape Job Log",
        "job_type": "Stage 2",
        "status": "Queued",
        "payload": json.dumps(payload_data)
    }).insert(ignore_permissions=True)
    
    return {"status": "success", "job_id": job.name}

@frappe.whitelist()
def claim_next_job():
    """Atomically claims the next Queued job, marking it as Running."""
    # Atomic select with row-level locking
    jobs = frappe.db.sql("""
        SELECT name FROM `tabScrape Job Log`
        WHERE status = 'Queued'
        ORDER BY creation ASC
        LIMIT 1
        FOR UPDATE
    """, as_dict=True)
    
    if not jobs:
        return None
        
    job_name = jobs[0].name
    
    # Mark it as Running atomically
    frappe.db.set_value("Scrape Job Log", job_name, "status", "Running")
    frappe.db.set_value("Scrape Job Log", job_name, "started_at", now_datetime())
    frappe.db.commit()
    
    # Fetch job doc and parse payload
    job_doc = frappe.get_doc("Scrape Job Log", job_name)
    payload = json.loads(job_doc.payload) if job_doc.payload else {}
    
    return {
        "job_id": job_doc.name,
        "job_type": job_doc.job_type,
        "docname": payload.get("docname"),
        "payload": payload
    }

@frappe.whitelist()
def ingest_stage1_results(job_id, docname, tenders):
    """Ingests scraped tenders, populates the child table, and updates stats."""
    job = frappe.get_doc("Scrape Job Log", job_id)
    job.status = "Running"
    job.save(ignore_permissions=True)
    
    try:
        if isinstance(tenders, str):
            tenders_list = json.loads(tenders)
        else:
            tenders_list = tenders
            
        parent_doc = frappe.get_doc("Tender Primary Screening", docname)
        
        # Clear existing entries in the child table to avoid duplicates on re-run
        parent_doc.set("raw_tender_leads", [])
        
        for t in tenders_list:
            # 1. Create or update the global Raw Tender Lead document
            if not frappe.db.exists("Raw Tender Lead", t["tender_id"]):
                lead_doc = frappe.get_doc({
                    "doctype": "Raw Tender Lead",
                    "tender_id": t["tender_id"],
                    "source": t.get("source"),
                    "title": t.get("title"),
                    "authority": t.get("authority"),
                    "location": t.get("location"),
                    "value": t.get("value"),
                    "emd": t.get("emd"),
                    "due_date": t.get("due_date"),
                    "link": t.get("link"),
                    "status": "New"
                })
                lead_doc.insert(ignore_permissions=True)
            else:
                # Update existing global lead status to New so it is clean for screening
                lead_doc = frappe.get_doc("Raw Tender Lead", t["tender_id"])
                lead_doc.status = "New"
                lead_doc.save(ignore_permissions=True)
            
            # 2. Add reference to the Tender Primary Screening child table
            parent_doc.append("raw_tender_leads", {
                "tender_id": t["tender_id"],
                "tender_title": t.get("title"),
                "due_date": t.get("due_date"),
                "authority": t.get("authority"),
                "link": t.get("link"),
                "source": t.get("source"),
                "value": t.get("value")
            })
            
        parent_doc.save(ignore_permissions=True)
        frappe.db.commit()
        
        # 3. Recalculate statistics on parent screening doc
        parent_doc.refresh_tenders()
        
        job.status = "Completed"
        job.finished_at = now_datetime()
        job.save(ignore_permissions=True)
        return {"status": "success"}
        
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Stage 1 Ingestion Error")
        report_job_failure(job_id, str(e))
        return {"status": "error", "message": str(e)}

@frappe.whitelist()
def report_job_failure(job_id, error_message):
    """Called if the agent throws an error during execution."""
    job = frappe.get_doc("Scrape Job Log", job_id)
    job.status = "Failed"
    job.error_message = error_message
    job.finished_at = now_datetime()
    job.save(ignore_permissions=True)
    return {"status": "success"}

@frappe.whitelist()
def ingest_stage2_documents(job_id, tender_id):
    """Whitelisted API for direct file uploads."""
    job = frappe.get_doc("Scrape Job Log", job_id)
    job.status = "Running"
    job.save(ignore_permissions=True)
    
    try:
        if not frappe.request.files:
            raise ValueError("No files attached to request")
            
        for file_key in frappe.request.files:
            file_data = frappe.request.files[file_key]
            # Save file attachment to ERPNext attached to 'Raw Tender Lead'
            saved_file = frappe.get_doc({
                "doctype": "File",
                "file_name": file_data.filename,
                "attached_to_doctype": "Raw Tender Lead",
                "attached_to_name": tender_id,
                "content": file_data.stream.read(),
                "is_private": 0
            })
            saved_file.save(ignore_permissions=True)
            
        job.status = "Completed"
        job.finished_at = now_datetime()
        job.save(ignore_permissions=True)
        return {"status": "success"}
    except Exception as e:
        report_job_failure(job_id, str(e))
        return {"status": "error", "message": str(e)}
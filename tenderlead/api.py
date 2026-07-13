import frappe
from frappe.utils import now_datetime
import json

@frappe.whitelist()
def trigger_stage1_scan(docname):
    """Triggers Stage 1 Playwright scraper for the given Tender Primary Screening doc."""
    doc = frappe.get_doc("Tender Primary Screening", docname)
    
    # Create a job log entry
    job = frappe.get_doc({
        "doctype": "Scrape Job Log",
        "job_type": "Stage 1",
        "status": "Queued",
        "started_at": now_datetime()
    }).insert(ignore_permissions=True)
    
    # Publish Socket.IO trigger event to the DocType room
    frappe.publish_realtime(
        event="stage1_trigger",
        message={
            "job_id": job.name,
            "docname": docname,
            "source": doc.tender_source,
            "screening_date": doc.screening_date
        },
        doctype="Tender Primary Screening"
    )
    return {"status": "success", "job_id": job.name}

@frappe.whitelist()
def ingest_stage1_results(job_id, docname, tenders):
    """Ingests scraped tenders, populates the child table, and updates stats (AI scoring skipped for now)."""
    job = frappe.get_doc("Scrape Job Log", job_id)
    job.status = "Running"
    job.save(ignore_permissions=True)
    
    try:
        # Type-safety: Handle both raw string JSON and pre-parsed python lists/dicts
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
def trigger_stage2_scan(docname):
    """Triggers Stage 2 Playwright document downloader for approved/unresolved tenders."""
    parent_doc = frappe.get_doc("Tender Primary Screening", docname)
    
    # Collect tender IDs that require document scraping (e.g. status = "Good Match" or "May be")
    tenders_to_scrape = []
    for row in parent_doc.raw_tender_leads:
        # Check global lead status to see if it was approved/shortlisted
        lead_status = frappe.db.get_value("Raw Tender Lead", row.tender_id, "status")
        if lead_status in ["Good Match", "May be"]:
            tenders_to_scrape.append({
                "tender_id": row.tender_id,
                "link": row.link,
                "source": row.source
            })
            
    if not tenders_to_scrape:
        frappe.throw("No approved/shortlisted tenders found for secondary screening.")
        
    job = frappe.get_doc({
        "doctype": "Scrape Job Log",
        "job_type": "Stage 2",
        "status": "Queued",
        "started_at": now_datetime()
    }).insert(ignore_permissions=True)
    
    # Publish Socket.IO trigger event to the DocType room
    frappe.publish_realtime(
        event="stage2_trigger",
        message={
            "job_id": job.name,
            "docname": docname,
            "tenders": tenders_to_scrape
        },
        doctype="Tender Primary Screening"
    )
    return {"status": "success", "job_id": job.name}

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

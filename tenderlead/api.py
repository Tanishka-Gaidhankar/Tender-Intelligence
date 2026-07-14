import frappe
from frappe.utils import now_datetime
import json
import re

def normalize_date_string(date_str):
    if not date_str:
        return None
    date_str = str(date_str).strip()
    parts = re.split(r'[-/]', date_str)
    if len(parts) == 3:
        if len(parts[2]) == 4 and len(parts[0]) <= 2:
            return f"{parts[2]}-{parts[1].zfill(2)}-{parts[0].zfill(2)}"
        elif len(parts[0]) == 4:
            return f"{parts[0]}-{parts[1].zfill(2)}-{parts[2].zfill(2)}"
    try:
        from frappe.utils import getdate
        return str(getdate(date_str))
    except Exception:
        return None


@frappe.whitelist()
def trigger_stage1_scan(docname):
    """Triggers Stage 1 Playwright scraper for the given Tender Primary Screening doc."""
    doc = frappe.get_doc("Tender Primary Screening", docname)
    
    payload_data = {
        "docname": docname,
        "source": doc.tender_source,
        "screening_date": str(doc.screening_date) if doc.screening_date else None
    }
    
    # Create a job log entry
    job = frappe.get_doc({
        "doctype": "Scrape Job Log",
        "job_type": "Stage 1",
        "status": "Queued",
        "payload": json.dumps(payload_data),
        "started_at": now_datetime()
    }).insert(ignore_permissions=True)
    
    # Publish Socket.IO trigger event to the DocType room
    frappe.publish_realtime(
        event="stage1_trigger",
        message={
            "job_id": job.name,
            "docname": docname,
            "source": doc.tender_source,
            "screening_date": str(doc.screening_date) if doc.screening_date else None
        },
        doctype="Tender Primary Screening"
    )
    return {"status": "success", "job_id": job.name}

@frappe.whitelist()
def claim_next_job():
    """Claims the next queued scrape job and returns its details."""
    jobs = frappe.get_all(
        "Scrape Job Log",
        filters={"status": "Queued"},
        fields=["name"],
        order_by="creation asc",
        limit=1
    )
    
    if not jobs:
        return None
        
    job = frappe.get_doc("Scrape Job Log", jobs[0].name)
    job.status = "Running"
    job.save(ignore_permissions=True)
    frappe.db.commit()
    
    payload = {}
    if job.payload:
        try:
            payload = json.loads(job.payload)
        except Exception:
            pass
            
    docname = payload.get("docname")
    
    return {
        "job_id": job.name,
        "job_type": job.job_type,
        "docname": docname,
        "payload": payload
    }


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
        table_field = "raw_tender_leads" if hasattr(parent_doc, "raw_tender_leads") else "raw_tender_leads_tbl"
        parent_doc.set(table_field, [])
        
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
                    "due_date": normalize_date_string(t.get("due_date")),
                    "link": t.get("link"),
                    "status": ""
                })
                lead_doc.insert(ignore_permissions=True)
            else:
                # Update existing global lead status to empty string so it is clean for screening
                lead_doc = frappe.get_doc("Raw Tender Lead", t["tender_id"])
                lead_doc.status = ""
                lead_doc.save(ignore_permissions=True)
            
            # 2. Add reference to the Tender Primary Screening child table
            parent_doc.append(table_field, {
                "tender_id": t["tender_id"],
                "source": t.get("source"),
                "title": t.get("title"),
                "authority": t.get("authority"),
                "location": t.get("location"),
                "value": t.get("value"),
                "due_date": normalize_date_string(t.get("due_date")),
                "status": "",
                "raw_tender_lead": lead_doc.name
            })
            
        parent_doc.save(ignore_permissions=True)
        frappe.db.commit()
        
        # 3. Recalculate statistics on parent screening doc (using fallback if method missing)
        if hasattr(parent_doc, "refresh_tenders"):
            parent_doc.refresh_tenders()
        else:
            calculate_statistics_direct(parent_doc)
        
        job.status = "Completed"
        job.finished_at = now_datetime()
        job.save(ignore_permissions=True)
        return {"status": "success"}
        
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Stage 1 Ingestion Error")
        report_job_failure(job_id, str(e))
        return {"status": "error", "message": str(e)}

def calculate_statistics_direct(parent_doc):
    """Fallback statistics calculator directly updating the parent doc fields."""
    table_field = "raw_tender_leads" if hasattr(parent_doc, "raw_tender_leads") else "raw_tender_leads_tbl"
    tenders = getattr(parent_doc, table_field, []) or []
    
    no_of_tender_screen = len(tenders)
    no_of_match = 0
    no_of_may_be = 0
    no_of_not_match = 0
    
    for t in tenders:
        status = t.status or "New"
        if status == "Good Match":
            no_of_match += 1
        elif status in ["May be", "AI Processing"]:
            no_of_may_be += 1
        elif status in ["No Match", "Rules Rejected", "Rejected AI"]:
            no_of_not_match += 1
            
    # Set fields dynamically depending on what exists on the server/doc
    if hasattr(parent_doc, "tender_screen"):
        parent_doc.tender_screen = no_of_tender_screen
        parent_doc.match = no_of_match
        parent_doc.may_be = no_of_may_be
        parent_doc.no_of_not_match = no_of_not_match
        if no_of_tender_screen > 0:
            parent_doc.matched = (no_of_match / no_of_tender_screen) * 100.0
        else:
            parent_doc.matched = 0.0
    else:
        parent_doc.no_of_tender_screen = no_of_tender_screen
        parent_doc.no_of_match = no_of_match
        parent_doc.no_of_may_be = no_of_may_be
        parent_doc.no_of_not_match = no_of_not_match
        if no_of_tender_screen > 0:
            parent_doc.percent_matched = (no_of_match / no_of_tender_screen) * 100.0
        else:
            parent_doc.percent_matched = 0.0
            
    parent_doc.save(ignore_permissions=True)
    frappe.db.commit()

@frappe.whitelist()
def trigger_stage2_scan(docname):
    """Triggers Stage 2 Playwright document downloader for approved/unresolved tenders."""
    parent_doc = frappe.get_doc("Tender Primary Screening", docname)
    
    # Collect tender IDs that require document scraping (e.g. status = "Good Match" or "May be")
    tenders_to_scrape = []
    table_field = "raw_tender_leads" if hasattr(parent_doc, "raw_tender_leads") else "raw_tender_leads_tbl"
    for row in getattr(parent_doc, table_field, []):
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
        
    payload_data = {
        "docname": docname,
        "tenders": tenders_to_scrape
    }
    
    job = frappe.get_doc({
        "doctype": "Scrape Job Log",
        "job_type": "Stage 2",
        "status": "Queued",
        "payload": json.dumps(payload_data),
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
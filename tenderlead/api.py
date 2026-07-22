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
    
    from_date = None
    to_date = None
    
    # Check for possible from_date / screening_from_date fieldnames
    for fieldname in ["from_date", "screening_from_date", "date_from"]:
        if hasattr(doc, fieldname) and getattr(doc, fieldname):
            from_date = str(getattr(doc, fieldname))
            break
            
    # Check for possible to_date / screening_to_date fieldnames
    for fieldname in ["to_date", "screening_to_date", "date_to"]:
        if hasattr(doc, fieldname) and getattr(doc, fieldname):
            to_date = str(getattr(doc, fieldname))
            break
            
    payload_data = {
        "docname": docname,
        "source": doc.tender_source,
        "screening_date": str(doc.screening_date) if doc.screening_date else None,
        "from_date": from_date,
        "to_date": to_date
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
            "screening_date": str(doc.screening_date) if doc.screening_date else None,
            "from_date": from_date,
            "to_date": to_date
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
        
        child_doctype = parent_doc.meta.get_field(table_field).options
        child_meta = frappe.get_meta(child_doctype)
        fields_dict = {f.fieldname: f for f in child_meta.fields}
        
        from tenderlead.ai.llm_client import generate_tender_screening_summary_and_score

        for t in tenders_list:
            # Safely truncate location to max 140 chars to comply with Frappe Data field limits
            loc_val = (t.get("location") or "")[:140].strip()
            t["location"] = loc_val
            val_str = t.get("value") or t.get("bid_value") or t.get("tender_value") or "Refer Document"
            emd_str = t.get("emd") or "Refer Document"

            # Generate Cohere summary & score placeholder if missing
            eval_res = generate_tender_screening_summary_and_score(t)
            score_val = t.get("ai_score") if t.get("ai_score") is not None else eval_res.get("ai_score")
            summary_val = t.get("ai_rationale") or t.get("summary") or eval_res.get("summary")
            status_val = t.get("status") or eval_res.get("status", "Good Match")

            # 1. Create or update the global Raw Tender Lead document
            if not frappe.db.exists("Raw Tender Lead", t["tender_id"]):
                lead_data = {
                    "doctype": "Raw Tender Lead",
                    "tender_id": t["tender_id"],
                    "source": t.get("source"),
                    "title": t.get("title"),
                    "authority": t.get("authority"),
                    "location": loc_val,
                    "value": val_str,
                    "emd": emd_str,
                    "due_date": normalize_date_string(t.get("due_date")),
                    "link": t.get("link"),
                    "status": status_val
                }
                lead_meta = frappe.get_meta("Raw Tender Lead")
                lead_fields = {f.fieldname for f in lead_meta.fields}
                if "ai_score" in lead_fields:
                    lead_data["ai_score"] = score_val
                for s_field in ["summary", "ai_rationale", "ai_summary", "rationale"]:
                    if s_field in lead_fields:
                        lead_data[s_field] = summary_val

                lead_doc = frappe.get_doc(lead_data)
                lead_doc.insert(ignore_permissions=True)
            else:
                lead_doc = frappe.get_doc("Raw Tender Lead", t["tender_id"])
                lead_doc.status = status_val
                if hasattr(lead_doc, "location"):
                    lead_doc.location = loc_val
                if hasattr(lead_doc, "value") and not lead_doc.value:
                    lead_doc.value = val_str
                if hasattr(lead_doc, "emd") and not lead_doc.emd:
                    lead_doc.emd = emd_str
                if hasattr(lead_doc, "ai_score"):
                    lead_doc.ai_score = score_val
                for s_field in ["summary", "ai_rationale", "ai_summary", "rationale"]:
                    if hasattr(lead_doc, s_field):
                        setattr(lead_doc, s_field, summary_val)
                lead_doc.save(ignore_permissions=True)
            
            # 2. Add reference to the Tender Primary Screening child table
            row_values = {
                "source": t.get("source"),
                "title": t.get("title"),
                "authority": t.get("authority"),
                "location": loc_val,
                "value": val_str,
                "due_date": normalize_date_string(t.get("due_date")),
                "status": status_val
            }
            if "ai_score" in fields_dict:
                row_values["ai_score"] = score_val
            for s_field in ["summary", "ai_rationale", "ai_summary", "rationale", "summary_rationale"]:
                if s_field in fields_dict:
                    row_values[s_field] = summary_val

            for link_field in ["link", "url", "view_tender_url", "source_link"]:
                if link_field in fields_dict and t.get("link"):
                    row_values[link_field] = t.get("link")
            
            # Set link references if they exist
            for link_f in ["raw_tender_lead", "raw_tender_id"]:
                if link_f in fields_dict:
                    row_values[link_f] = lead_doc.name
                    
            # Set IDs (Link vs Data)
            for id_f in ["tender_id", "tender_id_1"]:
                if id_f in fields_dict:
                    field_meta = fields_dict[id_f]
                    is_link = (field_meta.fieldtype == "Link" or 
                               (field_meta.fieldtype == "Data" and field_meta.options == "Raw Tender Lead"))
                    if is_link:
                        row_values[id_f] = lead_doc.name
                    else:
                        row_values[id_f] = t["tender_id"]
                        
            parent_doc.append(table_field, row_values)
            
        parent_doc.save(ignore_permissions=True)
        frappe.db.commit()
        
        # 3. Recalculate statistics and populate any missing row summaries
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
    """Fallback statistics calculator directly updating parent doc fields and generating missing summaries."""
    from tenderlead.ai.llm_client import generate_tender_screening_summary_and_score

    table_field = "raw_tender_leads" if hasattr(parent_doc, "raw_tender_leads") else "raw_tender_leads_tbl"
    tenders = getattr(parent_doc, table_field, []) or []
    
    no_of_tender_screen = len(tenders)
    no_of_match = 0
    no_of_may_be = 0
    no_of_not_match = 0
    
    for t in tenders:
        status_raw = str(getattr(t, "status", None) or "").strip()
        status_lower = status_raw.lower()
        if status_raw in ["Good Match", "good_match", "lead_created", "rules_passed"] or ("match" in status_lower and "no" not in status_lower and "not" not in status_lower) or "good" in status_lower:
            no_of_match += 1
        elif status_raw in ["No Match", "no_match", "Rules Rejected", "Rejected AI", "rules_rejected", "rejected_ai"] or "no" in status_lower or "reject" in status_lower:
            no_of_not_match += 1
        else:
            no_of_may_be += 1

        # Check if value is missing on child row and set default
        current_val = getattr(t, "value", None)
        if not current_val and hasattr(t, "value"):
            setattr(t, "value", "Refer Document")

        # Check if location is missing on child row and set default
        current_loc = getattr(t, "location", None)
        if not current_loc and hasattr(t, "location"):
            setattr(t, "location", "Not Specified")

        # Check if summary is missing on child row and generate it
        current_sum = getattr(t, "summary", None) or getattr(t, "ai_rationale", None) or getattr(t, "ai_summary", None)
        if not current_sum:
            eval_res = generate_tender_screening_summary_and_score({
                "title": getattr(t, "title", None) or getattr(t, "tender_id", "Tender"),
                "authority": getattr(t, "authority", None),
                "location": getattr(t, "location", None),
                "value": getattr(t, "value", None) or "Refer Document"
            })
            gen_summary = eval_res.get("summary", "")
            gen_score = eval_res.get("ai_score", 70)
            for s_field in ["summary", "ai_rationale", "ai_summary", "rationale", "summary_rationale"]:
                if hasattr(t, s_field):
                    setattr(t, s_field, gen_summary)
            if hasattr(t, "ai_score") and not getattr(t, "ai_score", None):
                setattr(t, "ai_score", gen_score)

            # Sync back to Raw Tender Lead document in DB
            t_id = getattr(t, "tender_id", None) or getattr(t, "tender_id_1", None) or getattr(t, "raw_tender_lead", None)
            if t_id and frappe.db.exists("Raw Tender Lead", t_id):
                try:
                    lead = frappe.get_doc("Raw Tender Lead", t_id)
                    s_needed = False
                    for s_f in ["summary", "ai_rationale"]:
                        if hasattr(lead, s_f) and not getattr(lead, s_f, None):
                            setattr(lead, s_f, gen_summary)
                            s_needed = True
                    if hasattr(lead, "value") and not getattr(lead, "value", None):
                        lead.value = "Refer Document"
                        s_needed = True
                    if s_needed:
                        lead.save(ignore_permissions=True)
                except Exception:
                    pass
            
    pct_matched = round((no_of_match / no_of_tender_screen) * 100.0, 2) if no_of_tender_screen > 0 else 0.0

    # Set all possible field name variations on the parent doc to guarantee update
    for field_pair in [
        ("no_of_tender_screen", no_of_tender_screen),
        ("tender_screen", no_of_tender_screen),
        ("no_of_match", no_of_match),
        ("match", no_of_match),
        ("no_of_may_be", no_of_may_be),
        ("may_be", no_of_may_be),
        ("no_of_not_match", no_of_not_match),
        ("not_match", no_of_not_match),
        ("percent_matched", pct_matched),
        ("matched", pct_matched)
    ]:
        fieldname, val = field_pair
        if hasattr(parent_doc, fieldname):
            setattr(parent_doc, fieldname, val)
            
    parent_doc.save(ignore_permissions=True)
    frappe.db.commit()

def resolve_portal_id_and_link(t_id):
    """Resolves actual numeric portal ID (e.g. 102511145) and tender link from a given t_id (docname or portal ID)."""
    portal_id = t_id
    link_val = None
    if t_id:
        if frappe.db.exists("Raw Tender Lead", t_id):
            lead = frappe.get_doc("Raw Tender Lead", t_id)
            portal_id = lead.tender_id or t_id
            link_val = lead.link
        else:
            lead_name = frappe.db.get_value("Raw Tender Lead", {"tender_id": t_id}, "name")
            if lead_name:
                lead = frappe.get_doc("Raw Tender Lead", lead_name)
                portal_id = t_id
                link_val = lead.link
    return portal_id, link_val

@frappe.whitelist()
def trigger_stage2_scan(docname=None, tender_id=None):
    """
    Triggers Stage 2 Playwright document intelligence scanner.
    Can be invoked for a single tender (via child table row) or for all approved tenders in a primary screening doc.
    """
    tenders_to_scrape = []

    if tender_id:
        # Single tender trigger from child table row
        p_id, link_val = resolve_portal_id_and_link(tender_id)
        lead_doc = frappe.get_doc("Raw Tender Lead", {"tender_id": p_id}) if frappe.db.exists("Raw Tender Lead", {"tender_id": p_id}) else None
        if not lead_doc and frappe.db.exists("Raw Tender Lead", tender_id):
            lead_doc = frappe.get_doc("Raw Tender Lead", tender_id)
        source_val = lead_doc.source if lead_doc else "Tender247"
        title_val = lead_doc.title if lead_doc else tender_id

        tenders_to_scrape.append({
            "tender_id": p_id,
            "link": link_val,
            "source": source_val,
            "title": title_val
        })
    elif docname:
        # Batch trigger from parent screening doc
        parent_doc = frappe.get_doc("Tender Primary Screening", docname)
        table_field = "raw_tender_leads" if hasattr(parent_doc, "raw_tender_leads") else "raw_tender_leads_tbl"
        for row in getattr(parent_doc, table_field, []):
            t_id = getattr(row, "tender_id", None) or getattr(row, "tender_id_1", None)
            lead_status = frappe.db.get_value("Raw Tender Lead", t_id, "status") if t_id else getattr(row, "status", None)
            if lead_status in ["Good Match", "good_match", "rules_passed", "lead_created"]:
                p_id, link_val = resolve_portal_id_and_link(t_id)
                tenders_to_scrape.append({
                    "tender_id": p_id,
                    "link": link_val or getattr(row, "link", None) or getattr(row, "url", None),
                    "source": getattr(row, "source", "Tender247"),
                    "title": getattr(row, "title", t_id)
                })

    if not tenders_to_scrape:
        frappe.throw("No valid tender selected or found for secondary screening.")

    # Immediately instantiate/ensure Tender Secondary Screening record exists in ERPNext Desk
    for t in tenders_to_scrape:
        p_id = t.get("tender_id")
        if p_id:
            try:
                meta = frappe.get_meta("Tender Secondary Screening")
                fields = {f.fieldname for f in meta.fields}
                if frappe.db.exists("Tender Secondary Screening", {"tender_id": p_id}):
                    sec_doc = frappe.get_doc("Tender Secondary Screening", {"tender_id": p_id})
                elif frappe.db.exists("Tender Secondary Screening", {"tender_id_1": p_id}):
                    sec_doc = frappe.get_doc("Tender Secondary Screening", {"tender_id_1": p_id})
                elif frappe.db.exists("Tender Secondary Screening", {"tender_title": t.get("title")}):
                    sec_doc = frappe.get_doc("Tender Secondary Screening", {"tender_title": t.get("title")})
                else:
                    sec_doc = frappe.new_doc("Tender Secondary Screening")
                
                _, link_val = resolve_portal_id_and_link(p_id)

                for f_name, f_val in [
                    ("tender_id", p_id),
                    ("tender_id_1", p_id),
                    ("tender_title", t.get("title")),
                    ("title", t.get("title")),
                    ("source", t.get("source")),
                    ("link", link_val or t.get("link")),
                    ("url", link_val or t.get("link")),
                    ("tender_link", link_val or t.get("link")),
                    ("tender_url", link_val or t.get("link")),
                    ("source_link", link_val or t.get("link")),
                    ("view_tender_url", link_val or t.get("link"))
                ]:
                    if f_name in fields and f_val:
                        sec_doc.set(f_name, f_val)
                sec_doc.save(ignore_permissions=True)
                frappe.db.commit()
            except Exception as e:
                frappe.log_error(f"Error initializing Tender Secondary Screening for {p_id}: {e}")

    payload_data = {
        "docname": docname,
        "tender_id": tender_id,
        "tenders": tenders_to_scrape
    }

    job = frappe.get_doc({
        "doctype": "Scrape Job Log",
        "job_type": "Stage 2",
        "status": "Queued",
        "payload": json.dumps(payload_data),
        "started_at": now_datetime()
    }).insert(ignore_permissions=True)

    frappe.publish_realtime(
        event="stage2_trigger",
        message={
            "job_id": job.name,
            "docname": docname,
            "tender_id": tender_id,
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
def ingest_stage2_documents(job_id=None, tender_id=None, docname=None, results_json=None):
    """
    Whitelisted API for ingesting Stage 2 document files and extracted intelligence
    (Scope of Work, Qualification Criteria, Document Checklist) into Tender Secondary Screening.
    """
    job = frappe.get_doc("Scrape Job Log", job_id) if job_id and frappe.db.exists("Scrape Job Log", job_id) else None
    if job:
        job.status = "Running"
        job.save(ignore_permissions=True)
    
    try:
        results = {}
        if results_json:
            if isinstance(results_json, str):
                try:
                    results = json.loads(results_json)
                except Exception:
                    results = {}
            elif isinstance(results_json, dict):
                results = results_json

        t_id = tender_id or results.get("tender_id")
        p_id, link_val = resolve_portal_id_and_link(t_id)

        lead_doc = frappe.get_doc("Raw Tender Lead", {"tender_id": p_id}) if frappe.db.exists("Raw Tender Lead", {"tender_id": p_id}) else None
        if not lead_doc and t_id and frappe.db.exists("Raw Tender Lead", t_id):
            lead_doc = frappe.get_doc("Raw Tender Lead", t_id)

        title_val = (lead_doc.title if lead_doc else None) or results.get("title") or t_id or "Unknown Tender"
        source_val = (lead_doc.source if lead_doc else None) or results.get("source") or "Tender247"

        scope_val = results.get("scope_of_work", "")
        qualification_val = results.get("qualification_criteria", "")
        checklist_raw = results.get("documents_required_for_bid") or results.get("document_checklist") or []

        if isinstance(checklist_raw, list):
            checklist_str = "\n".join([f"• {item}" if not str(item).startswith("•") else str(item) for item in checklist_raw])
        else:
            checklist_str = str(checklist_raw)

        # Create or update Tender Secondary Screening document in ERPNext
        sec_doc = None
        if p_id and frappe.db.exists("Tender Secondary Screening", {"tender_id": p_id}):
            sec_doc = frappe.get_doc("Tender Secondary Screening", {"tender_id": p_id})
        elif p_id and frappe.db.exists("Tender Secondary Screening", {"tender_id_1": p_id}):
            sec_doc = frappe.get_doc("Tender Secondary Screening", {"tender_id_1": p_id})
        elif frappe.db.exists("Tender Secondary Screening", {"tender_title": title_val}):
            sec_doc = frappe.get_doc("Tender Secondary Screening", {"tender_title": title_val})
        else:
            sec_doc = frappe.new_doc("Tender Secondary Screening")

        meta = frappe.get_meta("Tender Secondary Screening")
        fields = {f.fieldname for f in meta.fields}

        field_mappings = [
            ("tender_id", p_id),
            ("tender_id_1", p_id),
            ("source", source_val),
            ("tender_title", title_val),
            ("title", title_val),
            ("scope_of_work", scope_val),
            ("qualification_criteria", qualification_val),
            ("eligibility_criteria", qualification_val),
            ("document_checklist", checklist_str),
            ("checklist", checklist_str),
            ("documents_required_for_bid", checklist_str),
            ("link", link_val),
            ("url", link_val),
            ("tender_link", link_val),
            ("tender_url", link_val),
            ("source_link", link_val),
            ("view_tender_url", link_val)
        ]
        for fieldname, value in field_mappings:
            if fieldname in fields and value:
                sec_doc.set(fieldname, value)

        sec_doc.save(ignore_permissions=True)

        # Save uploaded PDF files to Tender Secondary Screening + Raw Tender Lead
        if frappe.request and frappe.request.files:
            for file_key in frappe.request.files:
                file_data = frappe.request.files[file_key]
                file_bytes = file_data.stream.read()

                saved_sec_file = frappe.get_doc({
                    "doctype": "File",
                    "file_name": file_data.filename,
                    "attached_to_doctype": "Tender Secondary Screening",
                    "attached_to_name": sec_doc.name,
                    "content": file_bytes,
                    "is_private": 0
                })
                saved_sec_file.save(ignore_permissions=True)

                if "document_extracted" in fields and hasattr(saved_sec_file, "file_url"):
                    sec_doc.set("document_extracted", saved_sec_file.file_url)
                    sec_doc.save(ignore_permissions=True)

                if lead_doc:
                    frappe.get_doc({
                        "doctype": "File",
                        "file_name": file_data.filename,
                        "attached_to_doctype": "Raw Tender Lead",
                        "attached_to_name": lead_doc.name,
                        "content": file_bytes,
                        "is_private": 0
                    }).save(ignore_permissions=True)

        frappe.db.commit()

        if job:
            job.status = "Completed"
            job.finished_at = now_datetime()
            job.save(ignore_permissions=True)

        return {"status": "success", "sec_doc": sec_doc.name}

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Ingest Stage 2 Documents Error")
        if job:
            report_job_failure(job.name, str(e))
        return {"status": "error", "message": str(e)}
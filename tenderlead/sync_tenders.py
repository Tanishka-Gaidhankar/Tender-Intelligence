import sqlite3
import os
import re
import frappe
from datetime import datetime

def parse_date(date_str):
    if not date_str:
        return None
    date_str = date_str.strip()
    
    # 1. Format: YYYY-MM-DD
    if re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
        return date_str
        
    # 2. Format: DD-MM-YYYY or DD/MM/YYYY
    m = re.match(r"^(\d{1,2})[-/](\d{1,2})[-/](\d{4})$", date_str)
    if m:
        return f"{m.group(3)}-{m.group(2).zfill(2)}-{m.group(1).zfill(2)}"
        
    # 3. Format: MMM DD, YYYY (e.g. Jul 3, 2026)
    try:
        dt = datetime.strptime(date_str, "%b %d, %Y")
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        pass
        
    # 4. Format: MMM D, YYYY (without comma or extra spaces)
    cleaned = re.sub(r'\s+', ' ', date_str).replace(',', '')
    try:
        dt = datetime.strptime(cleaned, "%b %d %Y")
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        pass

    try:
        dt = datetime.strptime(cleaned, "%B %d %Y")
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        pass

    return None

def sync():
    db_path = "/home/kbp/Documents/Tenderlead/tender_intelligence.db"
    # Fallback to local workspace relative path if not absolute
    if not os.path.exists(db_path):
        import sys
        # Check current python environment paths or common relative locations
        possible_db = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tender_intelligence.db")
        if os.path.exists(possible_db):
            db_path = possible_db
        else:
            frappe.throw(f"SQLite database not found at {db_path} or {possible_db}")
            return
        
    print(f"Connecting to SQLite database: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Query all tenders with left join to get details
    cursor.execute("""
        SELECT 
            r.tender_id, 
            r.source, 
            r.title, 
            r.authority, 
            r.location, 
            r.value, 
            r.emd, 
            r.due_date, 
            r.status, 
            r.ai_score as raw_ai_score, 
            r.ai_rationale as raw_ai_rationale, 
            r.link, 
            r.created_at,
            l.tender_id as lead_id,
            l.eligibility,
            l.scope_of_work,
            l.ai_score as lead_ai_score,
            l.ai_rationale as lead_ai_rationale
        FROM raw_tender_feed r
        LEFT JOIN tender_leads l ON r.tender_id = l.tender_id
    """)
    rows = cursor.fetchall()
    print(f"Found {len(rows)} tenders in SQLite staging database.")
    
    # Mapping for status values
    status_map = {
        "new": "New",
        "lead_created": "Lead Created",
        "rules_passed": "Rules Passed",
        "rules_rejected": "Rules Rejected",
        "rejected_ai": "Rejected AI",
        "ai_processing": "AI Processing"
    }
    
    count_inserted = 0
    count_updated = 0
    
    for r in rows:
        tender_id = r["tender_id"]
        if not tender_id or tender_id == "None":
            continue
            
        # Determine status
        is_lead = r["lead_id"] is not None
        sqlite_status = "lead_created" if is_lead else r["status"]
        status = status_map.get(sqlite_status, "New")
        
        # Determine AI score and rationale
        ai_score = r["lead_ai_score"] if r["lead_ai_score"] is not None else r["raw_ai_score"]
        ai_rationale = r["lead_ai_rationale"] if r["lead_ai_rationale"] is not None else r["raw_ai_rationale"]
        
        # Parse due date safely
        due_date = parse_date(r["due_date"])
        
        # Construct doc dictionary
        doc_data = {
            "doctype": "Raw Tender Lead",
            "tender_id": tender_id,
            "source": r["source"],
            "title": r["title"],
            "authority": r["authority"],
            "location": r["location"],
            "value": r["value"],
            "emd": r["emd"],
            "due_date": due_date,
            "status": status,
            "ai_score": float(ai_score) if ai_score is not None else None,
            "ai_rationale": ai_rationale,
            "link": r["link"],
            "created_at": r["created_at"],
            "scope_of_work": r["scope_of_work"],
            "eligibility": r["eligibility"]
        }
        
        # Check if already exists in ERPNext
        if frappe.db.exists("Raw Tender Lead", tender_id):
            # Update existing
            doc = frappe.get_doc("Raw Tender Lead", tender_id)
            doc.update(doc_data)
            doc.save(ignore_permissions=True)
            count_updated += 1
        else:
            # Insert new
            doc = frappe.get_doc(doc_data)
            doc.insert(ignore_permissions=True)
            count_inserted += 1
            
    conn.close()
    frappe.db.commit()
    print(f"Sync complete! Inserted: {count_inserted}, Updated: {count_updated}")

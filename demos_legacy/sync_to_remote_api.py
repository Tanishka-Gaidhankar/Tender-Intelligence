import sqlite3
import os
import re
import requests
import json
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

def main():
    print("=== Remote ERPNext API Sync Script ===")
    
    # Ask for remote site credentials
    base_url = input("Enter remote ERPNext base URL (e.g. https://kbpcivil.erpnext.com): ").strip().rstrip('/')
    api_key = input("Enter API Key: ").strip()
    api_secret = input("Enter API Secret: ").strip()
    
    if not base_url or not api_key or not api_secret:
        print("Error: All fields are required.")
        return
        
    db_path = "tender_intelligence.db"
    if not os.path.exists(db_path):
        print(f"Error: SQLite database not found at {db_path}")
        return
        
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Query all tenders
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
    print(f"Found {len(rows)} tenders in local SQLite database.")
    
    # Mapping for status values
    status_map = {
        "new": "New",
        "lead_created": "Lead Created",
        "rules_passed": "Rules Passed",
        "rules_rejected": "Rules Rejected",
        "rejected_ai": "Rejected AI",
        "ai_processing": "AI Processing"
    }
    
    headers = {
        "Authorization": f"token {api_key}:{api_secret}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    
    count_inserted = 0
    count_updated = 0
    count_failed = 0
    
    for idx, r in enumerate(rows):
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
        
        # Check if already exists in remote ERPNext via REST GET
        check_url = f"{base_url}/api/resource/Raw Tender Lead/{tender_id}"
        check_res = requests.get(check_url, headers=headers)
        
        try:
            if check_res.status_code == 200:
                # Update existing (PUT)
                put_url = f"{base_url}/api/resource/Raw Tender Lead/{tender_id}"
                put_res = requests.put(put_url, headers=headers, json=doc_data)
                if put_res.status_code == 200:
                    count_updated += 1
                else:
                    print(f"Failed to update {tender_id}: {put_res.text}")
                    count_failed += 1
            else:
                # Insert new (POST)
                post_url = f"{base_url}/api/resource/Raw Tender Lead"
                post_res = requests.post(post_url, headers=headers, json=doc_data)
                if post_res.status_code == 200:
                    count_inserted += 1
                else:
                    print(f"Failed to insert {tender_id}: {post_res.text}")
                    count_failed += 1
        except Exception as e:
            print(f"Error communicating with remote server: {e}")
            count_failed += 1
            
        if (idx + 1) % 20 == 0:
            print(f"Progress: Processed {idx + 1}/{len(rows)} tenders...")
            
    conn.close()
    print("\n=== Remote Sync Complete ===")
    print(f"Successfully Inserted: {count_inserted}")
    print(f"Successfully Updated:  {count_updated}")
    print(f"Failed:                {count_failed}")

if __name__ == "__main__":
    main()

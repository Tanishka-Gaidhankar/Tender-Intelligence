"""
tenderdetail_detail_scraper.py

Scraper to fetch and extract details from the TenderDetail detail page using requests.
"""

import re
import requests
from bs4 import BeautifulSoup


def fetch_tenderdetail_detail(detail_url: str) -> dict | None:
    """
    Fetches a TenderDetail detail page using authenticated AJAX, parses it, and extracts:
        - Scope of Work (Tender Brief + BOQ Items)
        - Eligibility/Qualification Criteria details
        - Document Download links
        - Full key-value details dictionary

    Args:
        detail_url: The absolute detail page URL.

    Returns:
        A dictionary with extracted details, or None.
    """
    try:
        import os
        import json
        import re

        # Extract tender_id from detail_url
        match_id = re.search(r"tenders/(\d+)", detail_url)
        extracted_tender_id = match_id.group(1) if match_id else None
        if not extracted_tender_id:
            print(f"Could not parse tender_id from detail URL: {detail_url}")
            return None

        # Load cookies from tenderdetail_session.json
        session_file = os.path.join(os.path.dirname(__file__), "tenderdetail_session.json")
        cookies_dict = {}
        if os.path.exists(session_file):
            try:
                with open(session_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if "cookies" in data:
                    for cookie in data["cookies"]:
                        if "tenderdetail.com" in cookie["domain"]:
                            cookies_dict[cookie["name"]] = cookie["value"]
            except Exception as e:
                print(f"Warning: Failed to load cookies from {session_file}: {e}")

        # Initialize authenticated session
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })

        for name, val in cookies_dict.items():
            session.cookies.set(name, val, domain="www.tenderdetail.com")

        # 1. Establish state by fetching a query listing page (we use 90371 as a reliable query ID)
        listing_url = "https://www.tenderdetail.com/registeruser/indiantenders/90371?tendertype=1"
        res1 = session.get(listing_url, timeout=30)
        if res1.status_code != 200:
            print(f"Warning: Listing page GET returned status code {res1.status_code}")

        # 2. Post to GetDomesticTenderDetail to get details dynamically
        post_url = "https://www.tenderdetail.com/RegisterUser/GetDomesticTenderDetail"
        post_data = {
            'PageIndex': '',
            'ddf': '',
            'ddt': '',
            'edf': '',
            'edt': '',
            'p': '',
            'QueryID': '90371',
            'HighlightedKeyword': '',
            'isListing': 'value',
            'HighlightKeyword': '',
            'RowNumber': '1',
            'TotalCount': '1',
            'SearchBoundary': '2',
            'si': '',
            'ownershipIDs': '',
            'SectorIDs': '',
            'AgencyIds': '',
            'TenderType': '1',
            'wk': '',
            'tvf': '',
            'tvt': '',
            'ourrefnos': '',
            'TenderID': extracted_tender_id,
            'Filtercity': ''
        }

        res = session.post(post_url, data=post_data, headers={"X-Requested-With": "XMLHttpRequest"}, timeout=30)
        if res.status_code != 200 or len(res.text.strip()) < 100:
            print(f"Failed to fetch detail page for ID {extracted_tender_id}. Status code: {res.status_code}, Length: {len(res.text) if res else 0}")
            return None

        soup = BeautifulSoup(res.text, "html.parser")
        
        # Verify if we got the actual detail content (should contain tables)
        tables = soup.find_all("table")
        if not tables:
            print(f"Warning: No tables found in detail AJAX response for ID {extracted_tender_id}. Response snippet: {res.text[:200]}")
            return None

        # 1. Parse all key-value pairs from tables
        details = {}
        for tr in soup.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) == 2:
                key_el = tds[0].find("b") or tds[0]
                key_text = key_el.get_text(strip=True).replace(":", "").strip()
                val_text = tds[1].get_text(strip=True)
                if key_text and val_text and len(key_text) < 100:
                    details[key_text] = val_text

        # 2. Parse BOQ Items table
        boq_items = []
        boq_h2 = soup.find("h2", string="BOQ Items")
        if boq_h2:
            boq_table = boq_h2.find_next("table")
            if boq_table:
                for tr in boq_table.find_all("tr"):
                    tds = tr.find_all("td")
                    if len(tds) >= 2:
                        texts = [t.get_text(strip=True) for t in tds]
                        if "sl. no." in texts[0].lower() or "item title" in texts[0].lower():
                            continue
                        
                        sl_no = texts[0]
                        title = texts[1]
                        desc = texts[2] if len(texts) > 2 else ""
                        boq_items.append({
                            "sl_no": sl_no,
                            "title": title,
                            "description": desc
                        })

        # 3. Parse Document Links
        doc_links = []
        for a in soup.find_all("a", href=True):
            href = a.get_attribute_list("href")[0]
            href_lower = href.lower()
            text = a.get_text(strip=True)
            if "download" in text.lower() or href_lower.endswith(".pdf") or href_lower.endswith(".xlsx"):
                doc_url = href
                if href.startswith("/"):
                    doc_url = "https://www.tenderdetail.com" + href
                
                desc = text
                parent_tr = a.find_parent("tr")
                if parent_tr:
                    tds = parent_tr.find_all("td")
                    if len(tds) >= 2:
                        desc = tds[1].get_text(strip=True)
                
                doc_links.append({
                    "name": desc or "Tender Document",
                    "url": doc_url
                })

        # 4. Construct Scope of Work and Eligibility texts
        tender_brief = details.get("Tender Brief", "")
        
        scope_parts = [f"Tender Brief: {tender_brief}"]
        if boq_items:
            scope_parts.append("\nBOQ Items:")
            for item in boq_items:
                scope_parts.append(f"  {item['sl_no']}. {item['title']} - {item['description']}")
        scope_of_work = "\n".join(scope_parts)

        # Look for explicit eligibility fields
        eligibility_fields = []
        for k, v in details.items():
            if "eligibility" in k.lower() or "qualification" in k.lower():
                eligibility_fields.append(f"{k}: {v}")
                
        if eligibility_fields:
            eligibility_criteria = "\n".join(eligibility_fields)
        else:
            eligibility_criteria = "Refer to Tender Brief and BOQ Items."

        parsed_id = details.get("TDR") or extracted_tender_id

        return {
            "tender_id": parsed_id,
            "authority": details.get("Tendering Authority"),
            "tender_value": details.get("Tender Value"),
            "emd": details.get("EMD"),
            "due_date": details.get("Due Date") or details.get("Last Date of Bid Submission"),
            "scope_of_work": scope_of_work,
            "eligibility_criteria": eligibility_criteria,
            "document_links": doc_links,
            "raw_details": details
        }

    except Exception as e:
        print(f"Error scraping TenderDetail detail page: {e}")
        return None

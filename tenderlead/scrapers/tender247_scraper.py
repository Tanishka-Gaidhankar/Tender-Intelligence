"""
tender247_scraper.py

 BeautifulSoup parser for the Tender247 Today Tenders dashboard page.
"""

import re
from bs4 import BeautifulSoup


def parse_tender247_dashboard(html: str) -> list[dict]:
    """
    Parses Today Tenders from the authenticated Tender247 dashboard HTML.

    Returns a list of dicts, each representing a parsed tender with keys:
        - tender_id
        - authority
        - location
        - title
        - bid_value
        - emd
        - due_date
        - days_left
        - ai_summary_url
    """
    soup = BeautifulSoup(html, "html.parser")
    tenders = []

    # Find all divs containing the "T247 ID-" spans, then climb up to the border-[#D4D4D4] card container
    t247_spans = [s for s in soup.find_all("span") if "t247 id" in s.get_text().lower()]
    
    card_containers = []
    seen_ids = set()

    for s in t247_spans:
        p = s.parent
        while p:
            # The tender card container always has the border-[#D4D4D4] class
            if p.name == "div" and p.get("class") and "border-[#D4D4D4]" in p.get("class"):
                card_containers.append(p)
                break
            p = p.parent

    for card in card_containers:
        card_text = card.get_text()

        # 1. Tender ID
        tender_id = None
        id_span = [s for s in card.find_all("span") if "t247 id" in s.get_text().lower()]
        if id_span:
            parent_text = id_span[0].parent.get_text()
            match = re.search(r"T247\s*ID\s*-\s*(\d+)", parent_text, re.IGNORECASE)
            if match:
                tender_id = match.group(1)

        if not tender_id:
            # Skip if we can't find an ID
            continue

        # Prevent duplicate parsing of cards (e.g. if spans are nested or double-matched)
        if tender_id in seen_ids:
            continue
        seen_ids.add(tender_id)

        # 2. Title
        title_p = card.find("p", class_=re.compile(r"line-clamp"))
        title = title_p.get_text(strip=True) if title_p else None

        # 3. Bid Value & EMD
        bid_value = None
        bid_match = re.search(r"(?:Bid\s*Value|Tender\s*Value|Estimated\s*(?:Cost|Value)|Value|Cost)\s*:\s*₹?\s*([^\n\r<]+)", card_text, re.IGNORECASE)
        if bid_match:
            raw_bv = bid_match.group(1).strip()
            raw_bv = re.split(r"\b(?:EMD|Due|Days|T247)\b", raw_bv, flags=re.IGNORECASE)[0].strip()
            raw_bv = raw_bv.rstrip(":- ").strip()
            if raw_bv:
                bid_value = raw_bv

        if not bid_value:
            curr_match = re.search(r"₹\s*([\d\.,]+\s*(?:Cr\.|Lakh|K|Thousand|Crore|Lakhs)?)", card_text, re.IGNORECASE)
            if curr_match:
                bid_value = curr_match.group(0).strip()
            else:
                bid_value = "Refer Document"
            
        emd = None
        emd_match = re.search(r"EMD\s*:\s*₹?\s*([^\n\r<]+)", card_text, re.IGNORECASE)
        if emd_match:
            raw_emd = emd_match.group(1).strip()
            raw_emd = re.split(r"\b(?:Due|Days|T247|Bid|Value)\b", raw_emd, flags=re.IGNORECASE)[0].strip()
            raw_emd = raw_emd.rstrip(":- ").strip()
            if raw_emd:
                emd = raw_emd
        if not emd:
            emd = "Refer Document"

        # 4. Due Date & Days Left
        due_date = None
        date_match = re.search(r"(\d{2}-\d{2}-\d{4})", card_text)
        if date_match:
            due_date = date_match.group(1)
        
        days_left = None
        days_match = re.search(r"(\d+)\s*Days?\s*Left", card_text, re.IGNORECASE)
        if days_match:
            days_left = int(days_match.group(1))

        # 5. Authority & Location
        authority = None
        location = None
        
        # Check for location pin marker "📍" or standalone location element containing "India"
        loc_pin_els = [
            el.strip() for el in card.find_all(string=True) 
            if "📍" in el or ("India" in el and " - " not in el and (not title or el.strip() != title))
        ]
        if loc_pin_els:
            location = loc_pin_els[0].replace("📍", "").strip()[:140]

        # Search for string elements in card containing " - " (excluding Title/ID/Value/EMD labels)
        candidates = []
        for el in card.find_all(string=True):
            text = el.strip()
            if not text:
                continue
            if title and (text == title or text in title or len(text) > 180):
                continue
            if " - " in text and not re.search(r"T247\s*ID|Bid\s*Value|EMD|Due\s*Date|Days?\s*Left", text, re.IGNORECASE):
                candidates.append(text)
                
        target_str = candidates[0] if candidates else None
        if target_str:
            parts = target_str.split(" - ", 1)
            if len(parts) == 2:
                authority = parts[0].strip()[:140]
                if not location:
                    location = parts[1].strip()[:140]
            elif not location:
                location = target_str[:140]

        # 6. AI Summary URL
        ai_link = card.find("a", href=re.compile(r"/auth/tender/", re.IGNORECASE))
        ai_summary_url = None
        if ai_link:
            href = ai_link.get("href")
            if href.startswith("/"):
                ai_summary_url = "https://www.tender247.com" + href
            else:
                ai_summary_url = href
        else:
            # Fallback: construct detail URL directly using tender_id
            ai_summary_url = f"https://www.tender247.com/auth/tender/{tender_id}"

        tenders.append({
            "tender_id": tender_id,
            "authority": authority,
            "location": location,
            "title": title,
            "bid_value": bid_value,
            "emd": emd,
            "due_date": due_date,
            "days_left": days_left,
            "ai_summary_url": ai_summary_url
        })

    return tenders

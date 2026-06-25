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
        bid_match = re.search(r"Bid\s*Value\s*:\s*₹?\s*([\d\.]+\s*(?:Cr\.|Lakh|K)?)", card_text, re.IGNORECASE)
        if bid_match:
            bid_value = bid_match.group(1).strip()
            
        emd = None
        emd_match = re.search(r"EMD\s*:\s*₹?\s*([\d\.]+\s*(?:Cr\.|Lakh|K)?)", card_text, re.IGNORECASE)
        if emd_match:
            emd = emd_match.group(1).strip()

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
        # Find element containing "India" to locate the authority + location text block
        india_el = [el for el in card.find_all(string=True) if "India" in el]
        authority = None
        location = None
        if india_el:
            target_str = None
            for el in india_el:
                if " - " in el:
                    target_str = el.strip()
                    break
            if not target_str:
                target_str = india_el[0].strip()

            parts = target_str.split(" - ", 1)
            if len(parts) == 2:
                authority = parts[0].strip()
                location = parts[1].strip()
            else:
                location = target_str

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

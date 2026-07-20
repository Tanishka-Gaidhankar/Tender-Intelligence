"""
tenderdetail_scraper.py

BeautifulSoup parser for the TenderDetail member portal tender listing pages.
The new structure (post-2025) uses div.tender_row cards instead of tables.

Each tender row contains:
    - h2.workDesc       → Authority / Organization name
    - p#tenderbreif     → TDR ID (bold prefix) + Title text
    - li.dd             → Due date (span.month span.day span.year)
    - li.state          → Location / State
    - li.price          → Tender value (often "Ref. Document")
"""

import re
from bs4 import BeautifulSoup


def parse_tenderdetail_listings(html: str) -> list[dict]:
    """
    Parses tender cards from a TenderDetail /registeruser/indiantenders/ page.

    Returns a list of dicts with keys:
        - tender_id      (the TDR number, e.g. "56129886")
        - authority      (issuing organization)
        - location       (state/region)
        - title          (work description)
        - tender_value   (monetary value or "Ref. Document")
        - due_date       (formatted as "Jul 3, 2026")
        - view_tender_url (direct link to tender: https://www.tenderdetail.com/tenders/{id})
    """
    soup = BeautifulSoup(html, "html.parser")
    tenders = []

    rows = soup.find_all("div", class_="tender_row")

    for row in rows:
        # ── 1. Authority ───────────────────────────────────────────────────────
        h2 = row.find("h2", class_="workDesc")
        authority = None
        if h2:
            # Remove the "- New" or status span before getting text
            for span in h2.find_all("span"):
                span.decompose()
            authority = h2.get_text(strip=True)

        # ── 2. TDR ID + Title ──────────────────────────────────────────────────
        brief_p = row.find("p", id="tenderbreif")
        tender_id = None
        title = None
        if brief_p:
            # The first <b> inside the <span> holds "TDR : 56129886"
            first_b = brief_p.find("b")
            if first_b:
                tdr_text = first_b.get_text(strip=True)
                match = re.search(r"TDR\s*:\s*(\d+)", tdr_text, re.IGNORECASE)
                if match:
                    tender_id = match.group(1)

            # Title = full span text minus the TDR prefix, collapsing bold tags
            span = brief_p.find("span")
            if span:
                full_text = span.get_text(" ", strip=True)
                # Remove "TDR : XXXXXXXX" prefix
                title = re.sub(r"^TDR\s*:\s*\d+\s*", "", full_text, flags=re.IGNORECASE).strip()
                # Collapse extra whitespace
                title = re.sub(r"\s+", " ", title).strip()

        if not tender_id:
            continue  # Skip rows without a parseable TDR ID

        # ── 3. Due Date ────────────────────────────────────────────────────────
        dd_li = row.find("li", class_="dd")
        due_date = None
        if dd_li:
            month = dd_li.find("span", class_="month")
            day   = dd_li.find("span", class_="day")
            year  = dd_li.find("span", class_="year")
            if month and day and year:
                due_date = f"{month.get_text(strip=True)} {day.get_text(strip=True)}, {year.get_text(strip=True)}"

        # ── 4. Location / State ────────────────────────────────────────────────
        state_li = row.find("li", class_="state") or row.find("li", class_=re.compile(r"loc|state|city", re.IGNORECASE))
        location = None
        if state_li:
            # Clone or work on text after decomposing icon tags
            state_clone = BeautifulSoup(str(state_li), "html.parser")
            for icon in state_clone.find_all(["i", "span"]):
                if icon.get("class") and any("fa" in c for c in icon.get("class")):
                    icon.decompose()
            location = state_clone.get_text(strip=True)
            if not location:
                location = state_li.get_text(strip=True)

        # ── 5. Tender Value ────────────────────────────────────────────────────
        price_li = row.find("li", class_="price")
        tender_value = None
        if price_li:
            # Remove icon tags
            for icon in price_li.find_all("i"):
                icon.decompose()
            raw_value = price_li.get_text(strip=True)
            # "Ref. Document" means no specific value listed
            if raw_value and raw_value.lower() not in ("ref. document", "ref.document", ""):
                tender_value = raw_value

        # ── 6. View Tender URL ─────────────────────────────────────────────────
        # The viewnotice link is a JS call, so construct the direct public URL
        view_tender_url = f"https://www.tenderdetail.com/tenders/{tender_id}"

        tenders.append({
            "tender_id":       tender_id,
            "authority":       authority,
            "location":        location,
            "title":           title,
            "tender_value":    tender_value,
            "due_date":        due_date,
            "view_tender_url": view_tender_url,
        })

    return tenders


def parse_pagination_info(html: str) -> dict:
    """
    Extracts pagination metadata from a listing page.
    Returns: { 'current_page': int, 'total_pages': int, 'total_results': int }
    """
    soup = BeautifulSoup(html, "html.parser")
    info = {"current_page": 1, "total_pages": 1, "total_results": 0}

    paging_div = soup.find("div", class_="dataTables_info")
    if paging_div:
        text = paging_div.get_text(strip=True)
        # Format: "Showing 1 of 8 Pages from 78 Results"
        m = re.search(r"Showing\s+(\d+)\s+of\s+(\d+)\s+Pages\s+from\s+(\d+)", text, re.IGNORECASE)
        if m:
            info["current_page"]  = int(m.group(1))
            info["total_pages"]   = int(m.group(2))
            info["total_results"] = int(m.group(3))

    return info

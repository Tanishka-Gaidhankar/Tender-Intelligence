"""
document_collector.py

Phase 1 of Stage B: Collects document download URLs from both TenderDetail and Tender247
detail pages for a shortlisted tender.

For TenderDetail: uses an authenticated Playwright page to load the detail page
                  and scrape all downloadable document links from the rendered HTML.
                  (The AJAX/requests approach does NOT work — the detail page requires
                  a live authenticated browser session to render correctly.)
For Tender247:    uses the same authenticated Playwright page to navigate to the
                  detail page and locate document download anchor tags.

Both sources share a single Playwright Page object passed in from the pipeline,
so there's no need to launch a new browser per tender.
"""
from __future__ import annotations
import re

def collect_tenderdetail_document_urls(page, detail_url: str) -> list[dict]:
    """
    Navigates to a TenderDetail detail page via an already-authenticated Playwright
    Page object and collects all downloadable document links.

    Args:
        page:       Authenticated Playwright Page object (from tenderdetail_session.py).
        detail_url: The full TenderDetail tender URL, e.g.
                    "https://www.tenderdetail.com/tenders/1234567"

    Returns:
        List of dicts: [{"name": "Tender Document", "url": "https://..."}, ...]
        Empty list on failure.
    """
    try:
        print(f"  Navigating to TenderDetail detail page: {detail_url}")
        page.goto(detail_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)  # allow JS rendering

        # Verify we're on an authenticated detail page (not redirected to homepage)
        current_url = page.url
        if "tenderdetail.com/tenders/" not in current_url:
            print(f"  WARNING: Redirected away from detail page → {current_url}")
            print("  Session may have expired. Attempting re-login...")
            # Try to re-authenticate using the session helper
            try:
                from ..scrapers.tenderdetail_session import _do_headless_login, _is_dashboard, DASHBOARD_URL
                # Navigate to login and re-authenticate
                _do_headless_login(page, page.context)
                # Navigate back to the detail page
                page.goto(detail_url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(3000)
                current_url = page.url
                if "tenderdetail.com/tenders/" not in current_url:
                    print(f"  Re-login failed — still on: {current_url}")
                    return []
                print("  Re-login successful. Continuing...")
            except Exception as e:
                print(f"  Re-login attempt failed: {e}")
                return []

        # Click through tabs/buttons to reveal notice document links on TenderDetail
        # 1. Click "View Notice" button
        try:
            view_notice_selectors = [
                "text=View Notice",
                "text=view notice",
                "a:has-text('View Notice')",
                "button:has-text('View Notice')",
                "span:has-text('View Notice')",
            ]
            for selector in view_notice_selectors:
                el = page.locator(selector).first
                if el.is_visible():
                    el.click()
                    page.wait_for_timeout(2000)
                    break
        except Exception as e:
            print(f"  Could not click 'View Notice' button: {e}")

        # 2. Click "View Original Notice/Documents"
        try:
            view_orig_selectors = [
                "text=View Original Notice/Documents",
                "text=View Original Notice",
                "text=Original Notice/Documents",
                "text=Original Notice",
                "a:has-text('View Original Notice')",
                "a:has-text('Original Notice')",
                "button:has-text('Original Notice')",
                "span:has-text('Original Notice')",
            ]
            for selector in view_orig_selectors:
                el = page.locator(selector).first
                if el.is_visible():
                    print(f"  Clicking 'View Original Notice/Documents' using selector: {selector}")
                    el.click()
                    page.wait_for_timeout(2000)
                    break
        except Exception as e:
            print(f"  Could not click 'View Original Notice/Documents': {e}")

        # Scrape document links from the rendered page HTML (polling up to 10s for DOM updates)
        from bs4 import BeautifulSoup
        doc_links = []
        for attempt in range(10):
            html = page.content()
            soup = BeautifulSoup(html, "html.parser")
            doc_links = _parse_tenderdetail_doc_links(soup, page)
            if doc_links:
                break
            page.wait_for_timeout(1000)

        print(f"  TenderDetail: found {len(doc_links)} document link(s)")
        return doc_links

    except Exception as e:
        print(f"  Error collecting TenderDetail document URLs: {e}")
        return []


def _parse_tenderdetail_doc_links(soup, page=None) -> list[dict]:
    """
    Extracts all downloadable document links from a parsed TenderDetail detail page.
    Returns deduped list of {"name": str, "url": str}.
    """
    from bs4 import BeautifulSoup

    seen_urls = set()
    doc_links = []

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        href_lower = href.lower()
        link_text = a.get_text(strip=True)

        # Match download links by extension or keyword
        is_doc = (
            href_lower.endswith(".pdf")
            or href_lower.endswith(".xlsx")
            or href_lower.endswith(".xls")
            or href_lower.endswith(".zip")
            or href_lower.endswith(".rar")
            or href_lower.endswith(".7z")
            or href_lower.endswith(".docx")
            or href_lower.endswith(".doc")
            or href_lower.endswith(".docm")
            or href_lower.endswith(".rtf")
            or href_lower.endswith(".txt")
            or "download" in href_lower
            or "download" in link_text.lower()
            or "/getdocument" in href_lower
            or "/document" in href_lower
            or "tenderfiles.com" in href_lower
            or "tdr_doc" in href_lower
        )
        if not is_doc:
            continue

        if href.startswith("/"):
            href = "https://www.tenderdetail.com" + href

        if not href.startswith("http"):
            continue

        if href in seen_urls:
            continue

        seen_urls.add(href)
        name = link_text
        parent_tr = a.find_parent("tr")
        if parent_tr:
            tds = parent_tr.find_all("td")
            if len(tds) >= 2:
                candidate = tds[1].get_text(strip=True)
                if candidate and len(candidate) < 200:
                    name = candidate

        doc_links.append({"name": name or "Tender Document", "url": href})

    # Also check Playwright-visible links if soup returned nothing
    if not doc_links and page is not None:
        try:
            anchors = page.locator("a[href]").all()
            for a_el in anchors:
                try:
                    href = a_el.get_attribute("href") or ""
                    href_lower = href.lower()
                    link_text = a_el.inner_text().strip()
                    is_doc = (
                        href_lower.endswith(".pdf")
                        or href_lower.endswith(".xlsx")
                        or "download" in href_lower
                        or "download" in link_text.lower()
                    )
                    if not is_doc:
                        continue
                    if href.startswith("/"):
                        href = "https://www.tenderdetail.com" + href
                    if not href.startswith("http") or href in seen_urls:
                        continue
                    seen_urls.add(href)
                    doc_links.append({"name": link_text or "Tender Document", "url": href})
                except Exception:
                    continue
        except Exception as e:
            print(f"  Error checking Playwright anchors: {e}")

    return doc_links


def collect_tender247_document_urls(page, detail_url: str) -> list[dict]:
    """
    Navigates to a Tender247 detail page via an already-authenticated Playwright
    Page object and collects all downloadable document links.
    """
    try:
        print(f"  Navigating to Tender247 detail page: {detail_url}")
        page.goto(detail_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)

        tender_id = detail_url.split("/")[-1].split("?")[0]

        # Check if page is 404 or URL is truncated (does not contain UUID or params)
        is_truncated = len(detail_url.split("/")) <= 6
        is_404 = page.locator("text=Page Not Found").count() > 0 or page.locator("text=Could not find request").count() > 0

        if is_truncated or is_404:
            print(f"  Tender247: detail URL for {tender_id} appears truncated or returned 404. Resolving via dashboard search...")
            try:
                page.goto("https://www.tender247.com/auth/dashboard", wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(3000)
                search_input = page.locator("input[placeholder*='Search'], #keyword").first
                if search_input.count() > 0:
                    search_input.fill(tender_id)
                    search_input.press("Enter")
                    page.wait_for_timeout(5000)
                    first_link = page.locator(f"a[href*='/tender/{tender_id}/']").first
                    if first_link.count() > 0:
                        new_url = first_link.get_attribute("href")
                        if new_url:
                            if not new_url.startswith("http"):
                                new_url = "https://www.tender247.com" + new_url
                            print(f"  Resolved fresh URL via search: {new_url}")
                            detail_url = new_url
                            page.goto(detail_url, wait_until="domcontentloaded", timeout=30000)
                            page.wait_for_timeout(3000)
            except Exception as se:
                print(f"  Failed to resolve fresh URL via search: {se}")

        # Expand the "Tender Documents" accordion/tab if not already visible
        is_expanded = page.locator("text=Download All Documents").count() > 0 and page.locator("text=Download All Documents").first.is_visible()
        
        if not is_expanded:
            tender_docs_selectors = [
                "#nitdocuments",
                "text=Tender Documents",
                "a:has-text('Tender Documents')",
                "button:has-text('Tender Documents')",
                "li:has-text('Tender Documents')",
                "span:has-text('Tender Documents')",
            ]

            try:
                for selector in tender_docs_selectors:
                    el = page.locator(selector).first
                    if el.is_visible():
                        print(f"  Clicking 'Tender Documents' tab/element using selector: {selector}")
                        el.click()
                        page.wait_for_timeout(3000)
                        break
            except Exception as e:
                print(f"  Could not click 'Tender Documents' tab: {e}")

        # Wait for download links/content to render
        page.wait_for_timeout(3000)

        # Check for Renewal Reminder or other blockages popup modal
        try:
            renewal_modal = page.locator("text=Renewal Reminder, text=Package Renewal").first
            if renewal_modal.count() > 0 and renewal_modal.first.is_visible():
                print("  WARNING: Renewal Reminder modal detected! Attempting to bypass...")
                name_input = page.locator("input[name*='name'], input[placeholder*='Name']").first
                phone_input = page.locator("input[name*='phone'], input[placeholder*='Mobile']").first
                submit_btn = page.locator("button:has-text('Submit'), input[type='submit']").first
                
                if name_input.is_visible():
                    name_input.fill("Sunil Phulpagar")
                if phone_input.is_visible():
                    phone_input.fill("9822013898")
                if submit_btn.is_visible():
                    submit_btn.click()
                    page.wait_for_timeout(3000)
        except Exception as me:
            print(f"  Failed to bypass Renewal modal: {me}")

        # Collect download links
        doc_links = []
        seen_urls = set()

        # 1. Look for standard a[href] links in the documents list
        try:
            anchors = page.locator("a[href]").all()
            for a_el in anchors:
                href = a_el.get_attribute("href") or ""
                href = href.strip()
                if not href or href == "#" or href.startswith("javascript:"):
                    continue

                href_lower = href.lower()
                link_text = a_el.inner_text().strip()

                is_doc = (
                    href_lower.endswith(".pdf")
                    or href_lower.endswith(".xlsx")
                    or href_lower.endswith(".xls")
                    or href_lower.endswith(".zip")
                    or href_lower.endswith(".rar")
                    or href_lower.endswith(".7z")
                    or href_lower.endswith(".docx")
                    or href_lower.endswith(".doc")
                    or "download" in href_lower
                    or "download" in link_text.lower()
                )
                if not is_doc:
                    continue

                if href.startswith("/"):
                    href = "https://www.tender247.com" + href

                if href in seen_urls:
                    continue

                seen_urls.add(href)
                doc_links.append({
                    "name": link_text or "Tender Document",
                    "url": href
                })
        except Exception as ae:
            print(f"  Error parsing standard anchors: {ae}")

        # 2. Look for "Download All Documents" or similar Playwright download triggers
        try:
            download_btn = page.locator("text=Download All Documents, text=Download Document").first
            if download_btn.count() > 0 and download_btn.is_visible():
                print("  Attempting to capture file via Playwright expect_download...")
                try:
                    with page.expect_download(timeout=10000) as download_info:
                        download_btn.click()
                    download = download_info.value
                    import os
                    from .document_downloader import _tender_temp_dir
                    target_dir = _tender_temp_dir(tender_id)
                    suggested_filename = download.suggested_filename
                    local_path = os.path.join(target_dir, f"01_{suggested_filename}")
                    download.save_as(local_path)
                    print(f"  Successfully downloaded file directly via Playwright: {local_path}")
                    doc_links.append({
                        "name": suggested_filename or "Tender Document",
                        "url": detail_url,
                        "local_path": local_path
                    })
                except Exception as de:
                    print(f"  Playwright download capture failed: {de}")
        except Exception as dbe:
            print(f"  Error checking download buttons: {dbe}")

        print(f"  Tender247: found {len(doc_links)} document link(s)")
        return doc_links

    except Exception as e:
        print(f"  Error collecting Tender247 document URLs: {e}")
        return []

"""
tender247_detail_scraper.py

Playwright scraper to fetch and extract the AI Summary/Eligibility Criteria from
the Tender247 detail page.
"""

from playwright.sync_api import Page


def fetch_tender247_detail_summary(page: Page, detail_url: str, timeout_sec: int = 15) -> str | None:
    """
    Navigates to the Tender247 detail page, expands the AI summary section
    (generating it if necessary), and extracts the text content.

    Args:
        page: Authenticated Playwright Page object.
        detail_url: The absolute detail page URL.
        timeout_sec: Timeout for waiting for the AI summary to load/generate.

    Returns:
        The text content of the AI Summary / Eligibility Criteria block, or None.
    """
    try:
        print(f"Navigating to detail page: {detail_url}")
        # Use domcontentloaded to prevent long-running analytics page-goto timeout
        page.goto(detail_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)  # brief buffer for client-side loading

        # Check if the AI Summary header is present on the page
        header_selector = "h2:has-text('AI Generated Tender Summary')"
        if page.locator(header_selector).count() == 0:
            # Fallback text selector
            header_selector = "text=AI Generated Tender Summary"
        
        if page.locator(header_selector).count() == 0:
            print("WARNING: AI Generated Tender Summary header not found on detail page.")
            return None

        # Let's locate the parent card block that wraps the header and content
        # Usually, this is the parent element of the header
        header_loc = page.locator(header_selector).first
        
        # Check if the content is collapsed (visible text check)
        is_expanded = False
        for text_selector in ["text=Tender Id", "text=Checklist", "text=Generate", "text=GST", "text=Material", "text=Summary"]:
            button_loc = header_loc.locator("xpath=..")
            h3_loc = button_loc.locator("xpath=..")
            content_loc = h3_loc.locator("xpath=./following-sibling::div[1]")
            
            if content_loc.count() > 0:
                loc = content_loc.locator(text_selector)
                if loc.count() > 0 and loc.first.is_visible():
                    is_expanded = True
                    break
        
        if not is_expanded:
            print("AI Summary block appears collapsed. Clicking header to expand...")
            header_loc.click()
            page.wait_for_timeout(2000)

        # Get the content sibling of the H3 (grandparent of the H2 header)
        button_loc = header_loc.locator("xpath=..")
        h3_loc = button_loc.locator("xpath=..")
        content_loc = h3_loc.locator("xpath=./following-sibling::div[1]")

        if content_loc.count() > 0:
            # Click Summary tab if present inside the content panel
            summary_tab = content_loc.locator("text=Summary")
            if summary_tab.count() > 0 and summary_tab.first.is_visible():
                print("Clicking 'Summary' tab in AI summary card...")
                summary_tab.first.click()
                page.wait_for_timeout(1500)

            # Check if the summary is already generated, or if a "Generate" button exists
            generate_btn = content_loc.locator("button:has-text('Generate')")
            if generate_btn.count() == 0:
                generate_btn = content_loc.locator("text=Generate")

            if generate_btn.count() > 0 and generate_btn.first.is_visible():
                print("Clicking 'Generate' button to produce AI summary...")
                generate_btn.first.click()
                # Wait for generation (typically 3-5 seconds, wait up to timeout_sec)
                print(f"Waiting up to {timeout_sec} seconds for generation...")
                for _ in range(timeout_sec):
                    page.wait_for_timeout(1000)
                    # Break if some expected text is visible
                    if content_loc.locator("text=Checklist").count() > 0 or content_loc.locator("text=GST").count() > 0 or content_loc.locator("text=Tender Id").count() > 0:
                        print("AI summary generated successfully!")
                        break

            return content_loc.first.inner_text().strip()

        return header_loc.locator("xpath=..").inner_text().strip() # fallback

    except Exception as e:
        print(f"Error scraping Tender247 detail page: {e}")
        return None

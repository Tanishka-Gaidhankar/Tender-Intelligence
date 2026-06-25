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
        # If "Tender Id" text is not visible, we should click the header to expand it
        if page.locator("text=Tender Id").count() == 0 or not page.locator("text=Tender Id").first.is_visible():
            print("AI Summary block appears collapsed. Clicking header to expand...")
            header_loc.click()
            page.wait_for_timeout(2000)

        # Check if the summary is already generated, or if a "Generate" button exists
        generate_btn = page.locator("button:has-text('Generate')")
        if generate_btn.count() == 0:
            generate_btn = page.locator("text=Generate")

        if generate_btn.count() > 0 and generate_btn.first.is_visible():
            print("Clicking 'Generate' button to produce AI summary...")
            generate_btn.first.click()
            # Wait for generation (typically 3-5 seconds, wait up to timeout_sec)
            print(f"Waiting up to {timeout_sec} seconds for generation...")
            for _ in range(timeout_sec):
                page.wait_for_timeout(1000)
                if page.locator("text=Tender Id").count() > 0 and page.locator("text=Tender Id").first.is_visible():
                    print("AI summary generated successfully!")
                    break

        # Read the inner text of the container card
        # The container is the grandparent or parent of the header h2
        # Let's locate the closest div wrapping both the header and content.
        # We can use xpath to get the parent of the header.
        parent_loc = header_loc.locator("xpath=..")
        
        # Let's extract the text from the parent
        summary_text = parent_loc.inner_text()
        
        # Clean up any trailing "AI Summary -" or extra headers if needed
        return summary_text.strip()

    except Exception as e:
        print(f"Error scraping Tender247 detail page: {e}")
        return None

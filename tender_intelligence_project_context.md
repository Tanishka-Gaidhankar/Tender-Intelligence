# Tender Intelligence System — Project Context

This document summarizes a project in progress, for the purpose of giving any LLM full context before continuing work on it. Paste this entire document at the start of a new conversation.

---

## 1. Project Purpose

KBP Civil Engineering Services receives daily tender alert emails from two paid subscription services — **TenderDetail** and **Tender247 (a.k.a. Indian Tender / bidsnrfp.com)**. These emails contain anywhere from dozens to hundreds of tender opportunities per day. Manually reviewing all of them is impractical.

The goal is to build an automated pipeline that:
1. Reads these emails automatically
2. Extracts every tender listed (including ones hidden behind "View All" links)
3. Cheaply filters out irrelevant tenders (wrong category/scope)
4. Uses AI to deeply analyze the remaining candidates against KBP's actual capabilities
5. Surfaces only genuinely promising tenders to the team, in ERPNext, ready for a Go/No-Go decision

The system is being built as a custom ERPNext app, with Python automation scripts (scraping, parsing, AI scoring) operating on ERPNext's own database — there is no separate backend.

---

## 2. The Three-Stage Funnel

```
Stage 1 — Cheap filtering (listing-level data only)
    TenderDetail: keyword rules match on title/authority/location,
                  THEN if inconclusive, AI reads the title alone
                  and guesses category fit (yes/no/unsure)
    Tender247:    Portal already pre-filters to "your category" before
                  sending the email, so Stage 1 skips keyword rules
                  entirely and goes straight to AI title-guess

Stage 2 — Thorough read (detail-page level)
    Open the shortlisted tender's detail page
    Read: Scope of Work + Eligibility/Qualification Criteria
    TenderDetail: scrape these fields directly from the detail page HTML
    Tender247:    click the AI Summary dropdown -> click "Generate"
                  (if not already generated) -> wait ~3-5 sec -> read
                  the AI-generated summary/eligibility text Tender247
                  itself produces (this is cheaper than us reading raw
                  documents — Tender247 has already summarized it)
    Output: an AI score (0-100) + written rationale, stored alongside
    the tender

Stage 3 — Final output (what the team sees)
    ERPNext "Tender Lead" list, sorted by AI score descending
    Clicking a lead shows: Scope of Work, Qualification Criteria,
    document list (as LINKS to the source portal, never downloaded
    PDFs — this avoids unnecessary storage growth and always points
    to the latest version if a corrigendum is issued later)
```

**Important escalation rule for Stage 1 (TenderDetail only):** if the title alone is inconclusive, the system may open the detail page and check the Scope of Work text, then the Eligibility text — in that order, cheapest first. If still inconclusive after both, the tender is handed to Stage 2 rather than attempting to open and parse the full NIT PDF at the Stage 1 level. Stage 1 must stay cheap; deep document reading is Stage 2's job only.

---

## 3. ERPNext Data Structure

```
Raw Tender Feed   — staging table. EVERY tender that arrives gets a
                    record here first. Used for deduplication and as
                    an audit trail. Has a status field that moves
                    through: new -> rules_passed/rules_rejected ->
                    ai_processing -> lead_created/rejected_ai

Tender Lead       — the main working record. ONLY tenders that passed
                    BOTH Stage 1 and Stage 2 (AI score >= threshold)
                    get a record here. Contains: title, authority,
                    location, estimated value, EMD, eligibility, AI
                    score, AI rationale, deadline, and a SOURCE LINK
                    (not a downloaded document) back to the original
                    tender page.

Tender Rules      — stores Stage 1 keyword include/exclude lists
                    (e.g. Include: RCC, Roads, Water Supply, Bridges)

Tender Settings   — a Single doctype (only one record ever exists).
                    Holds:
                      - Company Profile (free text: who KBP is)
                      - Scope of Work (free text: what KBP builds)
                      - Eligible States (which regions KBP operates in)
                      - Min/Max Tender Value (target value range)
                      - Disqualifiers (free text: things that always
                        rule a tender out, e.g. "building construction
                        only", "electrical works", "supply-only
                        contracts")
                      - AI Score Threshold (e.g. 70 — minimum score to
                        become a Tender Lead)
                      - API keys (OpenAI/Claude), Gmail app password,
                        TenderDetail/Tender247 login credentials —
                        all stored as encrypted Password fields, NEVER
                        exposed to any AI model. The AI only ever sees
                        tender text content, never credentials.
```

---

## 4. Scheduler / Automation Cadence

The system runs on a **business-day window**, not a literal calendar day, because TenderDetail's digest email arrives in the evening (around 7-9 PM) and KBP's office hours are 9:30 AM - 7:30 PM. Any email arriving after 7:30 PM is treated as belonging to the NEXT business day's batch.

```
Intake job   — reads the day's digest email(s), extracts the tender
               list (some sources require opening a separate "View
               All" link), stores everything in Raw Tender Feed,
               applies Stage 1 filtering
Analysis job — picks up Raw Tender Feed records that passed Stage 1,
               runs Stage 2 (detail page scrape + AI scoring), creates
               Tender Lead records for anything above the score
               threshold
```

Both portals send tenders ONCE per day as a digest (not real-time individual alerts), so the system runs once daily, timed to run after both emails are expected to have arrived.

---

## 5. Source-Specific Technical Details

### TenderDetail
- Sender address: `tenders@tenderdetail.com`
- Subject pattern to match: starts with a NUMBER followed by "New Tenders" (e.g. "255 New Tenders Date 18-June-2026..."). Subjects WITHOUT a leading number, or containing the word "Results", must be excluded (those are a different, irrelevant email type — closed/awarded tender results, not new opportunities).
- The email contains a "Click Here To View All N New Tenders" link with a **unique, single-use-per-day token URL** (e.g. `tenderdetail.com/dailytenders/{id}/{uuid}`). This URL requires **no login** — it's the access token itself. A new URL is issued in each day's email; yesterday's URL does not work today.
- The "View All" page shows tenders grouped into ~8 categories (e.g. NDT Consultancy, Investigation, Structural, Rehabilitation, Architectural, Underwater Tenders, Third Party, Assessment). These are TenderDetail's OWN categorization, not KBP's — a tender appearing under "Structural" does not guarantee it matches KBP's actual keyword rules; Stage 1 still needs to run.
- Each tender's listing entry has, in order: an index number, authority name, location, then a `<p class="m-td-brief">` paragraph containing `<strong>TDR:{id}</strong>` followed by the title text (with TenderDetail's own keyword matches highlighted in `<b style="color:red;">` tags — cosmetic only, not relevant to KBP's own filtering), then a "Tender Document" type label, then Tender Value, Due Date, and a "View Tender" link (relative URL, needs domain prepended) to the permanent detail page.
- **Confirmed working scraper exists** (`tenderdetail_scraper.py`) — tested against a live page, correctly parsed 255/255 tenders with zero missing fields.
- The DOM structure required care: `col-md-12` is a reused Bootstrap class at multiple nesting levels (NOT a reliable per-tender container) — the real per-tender container is `<div class="m-mainTR">`. The category name is reliably found in a `<p class="m-r-government-tenders" id="{category name}">` tag's `id` attribute, not by parsing visible heading text.
- No Playwright/browser automation needed for TenderDetail — a plain HTTP GET request with `requests` + BeautifulSoup parsing is sufficient, since there's no login and no JavaScript-dependent rendering required for the listing page.

### Tender247 (Indian Tender / bidsnrfp.com)
- Sender address: `admin@bidsnrfp.com`, display name "Tender247 Tender Alert"
- Sends MULTIPLE distinct email types per day from the same address — must distinguish carefully by subject line:
  - **"Tender Opening Report"** / **"Contract Award"** — these are CLOSED/AWARDED tender results, not new opportunities. EXCLUDE.
  - **"New Tender Results: Participation Insights"** — also a results-type email (contains the word "Results"). EXCLUDE.
  - **"New Tender/s, {date}"** (no leading number) — a short notification with just a count and a "View Details" link, no tender data inline. This is a valid signal but was decided to be EXCLUDED in favor of only trusting subjects WITH a leading count.
  - **"{N} New Tender/s, {date}"** (WITH a leading number, e.g. "42 New Tender/s, 18-Jun-26") — this is the authoritative, countable batch. ONLY this format is kept for processing.
- The "View Details" / "View All" link in the email is a one-time-use tracking redirect (`r.tenders.bidsnrfp.com/tr/cl/...`) that auto-authenticates the session and redirects to `https://www.tender247.com/auth/tender`. Once that specific link has been clicked ONCE (by anyone — a human checking manually, or a script), it stops working and redirects to the public homepage instead. This makes it unreliable as a repeatable automation entry point.
- **Solution implemented:** use Playwright's `storage_state` feature to save an authenticated browser session (cookies) to a JSON file ONE TIME (via an interactive manual login), then reuse that saved session on every subsequent automated run — completely bypassing the one-time tracking link problem. If the saved session expires, the system falls back to a credential-based login (email + password into a real login form) and saves a fresh session.
- No CAPTCHA or OTP confirmed on the login — safe to automate unattended.
- Login form fields (confirmed from live page inspection): `<input type="email" name="emailId">`, `<input type="password" name="password">`, `<button type="submit">Submit</button>`
- Once authenticated, `https://www.tender247.com/auth/tender` shows "Today Tenders" by default — no extra click/filter needed in theory. HOWEVER: a real bug was found where a session-loaded page can land on a transient **"No Record Found"** empty state even though the session is genuinely authenticated and real data exists (confirmed by comparing to a simultaneous manual browser check showing real tenders). This appears to be a client-side data-fetch timing issue in the page's Next.js app, not an authentication failure. The current fix retries with a page reload up to 3 times, checking for the literal text "T247 ID" (a guaranteed marker of real tender data) before accepting the page as successfully loaded.
- Each tender's listing entry (within an authenticated dashboard page) has, per tender, in one container div: an index number, "Bid Value:" label + value, "EMD:" label + value, a due date + "X Days Left" text, "T247 ID-" label + numeric ID, a title paragraph (`<p class="...line-clamp-2...">`), an authority+location string in the format `"{Authority} - {City}, {State}, India"` (split on the FIRST " - " only), and an `<a href="/auth/tender/{id}/{uuid}?tesd={date}">AI summary / Eligibility Criteria</a>` link — this link IS the Stage 2 detail page URL, obtained for free while parsing Stage 1's listing.
- **Important text-node quirk discovered:** in the real markup, `<span>T247 ID-</span> 100548590` — the label and the number are SEPARATE text nodes (the number is plain text immediately after the closing `</span>`, not inside it). A regex trying to match both in one BeautifulSoup `string=` search will fail; must search for the label substring alone, then read the PARENT element's combined `get_text()` to get the full label+number string.
- **Confirmed working scraper exists** (`tender247_scraper.py`) — tested against a hand-built realistic sample matching the real DOM, correctly parsed multiple tenders with all fields populated, including edge cases like "Lakh" vs "Cr." value units.
- The detail page (reached via the AI summary link) shows: full title, Submission Date, Opening Date, Tender Estimated Cost, EMD, Tender Document Fees, and an "AI Generated Tender Summary / Eligibility Criteria" section broken into labeled sub-fields (e.g. "Affidavit notarized documents: Yes, the following are required..."). This summary is SOMETIMES already generated and visible immediately, and SOMETIMES requires clicking a dropdown arrow then a "Generate" button and waiting ~3-5 seconds — the scraper must check for existing content first and only trigger generation if the section appears empty.
- The detail page sidebar also shows "Probable Participants" (likely competing bidders) — not currently used by the system, but noted as potentially useful competitive intelligence for later.

---

## 6. AI Scoring Design (Stage 2)

The AI prompt cross-checks a tender against KBP's profile (from Tender Settings) across 5 weighted dimensions:

```
Scope Match            (weight: 30) — does the tender's work type match
                                       what KBP actually executes?
Location Eligibility   (weight: 25) — is the tender in a state/region
                                       KBP operates in?
Eligibility Clearance  (weight: 20) — can KBP likely meet the PQ/
                                       eligibility criteria?
Value Fit              (weight: 15) — does the tender value fall in
                                       KBP's target range?
Disqualifier Check     (weight: 10) — does the tender contain any of
                                       KBP's listed disqualifiers?
```

Each dimension is scored 0-10 by the AI, then weighted and summed to a 0-100 overall score. The AI also returns a written rationale per dimension, a "key risks" field, and a "suggested action" — all stored on the Tender Lead record so the team can see WHY a tender scored the way it did, not just the number.

---

## 7. Security / Data Handling Decisions

- Login credentials (Gmail, TenderDetail, Tender247) are stored encrypted in ERPNext's `Tender Settings` Password fields. They are read directly by Python scripts (via `frappe.get_doc().get_password()`) to fill login forms via Playwright. **No AI model call ever includes credentials in its prompt** — the AI only ever receives tender title/scope/eligibility TEXT for scoring, never authentication data.
- Tender documents (NIT PDFs, BOQs) are NEVER downloaded and stored in ERPNext. Only the SOURCE URL is saved on the Tender Lead record. This was a deliberate decision: it avoids storage growth (a downloaded-PDF-per-lead approach was estimated at ~14.6 GB/year vs near-zero for storing links), and it ensures the link always reflects the latest version if a corrigendum is issued after the lead was created. TenderDetail's "View Tender" links were confirmed to be PERMANENTLY accessible (not time-limited), making this approach safe long-term.
- Raw Tender Feed (the staging table storing text fields only, no documents) was estimated to grow by roughly 73 MB/year at expected volume (~500 tenders/day combined across both sources) — not a meaningful storage concern.

---

## 8. Current Build Status (as of last working session)

```
DONE and tested:
  - IMAP email reader (email_reader.py) — connects to Gmail, correctly
    filters both sources by subject pattern and business-day time
    window, handles multiple same-day email batches per source
  - TenderDetail listing scraper (tenderdetail_scraper.py) — tested
    against a live page, 255/255 tenders parsed correctly, zero
    missing fields
  - Tender247 Playwright login (tender247_login.py) — confirmed
    working via the one-time email tracking link
  - Tender247 session persistence (tender247_session.py) — saves/
    reuses authenticated cookies via Playwright's storage_state,
    solving the one-time-link repeatability problem. Currently
    being refined to handle a "No Record Found" transient empty-
    state bug via retry-with-reload logic.
  - Tender247 listing scraper (tender247_scraper.py) — tested against
    a hand-built realistic sample matching confirmed real DOM
    structure; not yet validated against a live, fully-loaded
    real page with actual data (blocked by the "No Record Found"
    timing issue above, which is being actively debugged)

NOT YET BUILT:
  - Stage 1 rules filter + AI title-guess logic (rules_filter.py,
    ai title-check) — designed in detail, not yet coded
  - Stage 2 AI scoring (ai_scorer.py, prompt_templates.py) — designed
    in detail (the 5-dimension weighted scoring model above), not yet
    coded
  - TenderDetail detail-page scraper (Stage 2, for scope/eligibility
    text) — not yet built
  - Tender247 detail-page "AI Summary" dropdown-click-and-read logic
    (Stage 2) — designed/confirmed via screenshots, not yet coded
  - ERPNext doctypes themselves (Raw Tender Feed, Tender Lead, Tender
    Rules, Tender Settings) — schema designed, not yet created in
    ERPNext
  - Scheduler wiring (hooks.py, intake_job.py, analysis_job.py as
    actual ERPNext scheduled jobs) — designed, not yet coded as final
    production versions tied into ERPNext
  - Deduplication logic refinement for cases where the same tender
    appears across multiple categories/batches
```

---

## 9. Key Design Decisions Worth Remembering

- **Two-tier+ escalation, not a single filter pass**: cheap signals are tried first (keywords, title-only AI guess), and only genuinely ambiguous cases escalate to more expensive checks (detail page text, then full Stage 2 AI scoring). This was a deliberate, iteratively-refined design to avoid the cost of deep-reading every single tender that arrives.
- **Source-specific Stage 1 paths**: TenderDetail needs its own keyword filter (Tender247 doesn't, since the portal already pre-filters by category before sending the email).
- **Never assume a scraper guess is correct — always test against real fetched HTML.** Every scraper in this project went through at least one real bug found only by testing against live data (e.g. TenderDetail's `col-md-12` reuse, Tender247's split text nodes for "T247 ID-"). This pattern should continue: write a first version, test against something real, fix based on actual output, never assume markup based on a screenshot alone.
- **All file paths and the actual working code files** (`email_reader.py`, `tenderdetail_scraper.py`, `tender247_login.py`, `tender247_session.py`, `tender247_scraper.py`, `run_tender247_full_test.py`) exist as separate Python files and would need to be shared alongside this document if continuing development with the working code itself, since this document is a CONTEXT summary, not the code.

---

*End of context document. If continuing this project, the next concrete unblocked step was: fix the Tender247 "No Record Found" transient empty-state issue in `tender247_session.py` (a retry-with-reload mechanism was just added and awaiting a test run), then move on to building Stage 1's rules filter and Stage 2's AI scoring logic, neither of which has been coded yet.*

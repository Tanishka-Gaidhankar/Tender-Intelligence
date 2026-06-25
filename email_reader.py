"""
email_reader.py

Standalone IMAP + email parsing module.
No ERPNext, no frappe — connects to Gmail, finds the current business
day's TenderDetail and Tender247 emails, and extracts the link needed
for the next scraping step.

Business-day window: yesterday 7:30 PM → now. This is because
TenderDetail's digest arrives in the 7-9 PM range and belongs to the
NEXT business day's batch, not the literal calendar date it was sent on.

Both TenderDetail and Tender247 send multiple email types from the same
address. Only emails whose subject STARTS WITH A NUMBER followed by
"New Tender(s)" are kept — e.g. "42 New Tender/s, ..." or "255 New
Tenders Date ...". "Results" / "Tender Opening Report" emails are
excluded for both. Notification-style emails with no leading count
(e.g. "New Tender/s, 18-Jun-26 (Noon) - Tender247") are also excluded —
only genuine, countable batches are treated as authoritative.

This is the production version of the logic, written so it can be
dropped into the ERPNext app later with minimal changes — just the
frappe.get_single() calls for credentials will replace the constants
below.
"""

import imaplib
import email
import re
from email.header import decode_header
from email.utils import parsedate_to_datetime
from datetime import date, datetime, timedelta, time as dtime
from bs4 import BeautifulSoup


# ── CONFIG — these become Tender Settings fields in ERPNext later ──
GMAIL_ADDRESS = "sales@kbpcivil.in"
APP_PASSWORD  = "gbttftpzylhoqith"  # no spaces

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993

SOURCES = {
    "TenderDetail": "tenderdetail",
    "Tender247":    "bidsnrfp",
}

# Office day boundary: 9:30 AM to 7:30 PM.
# Anything arriving after 7:30 PM belongs to the NEXT business day's batch.
# So when this job runs (e.g. next morning), the window we search is:
#     [yesterday 7:30 PM]  →  [now]
# This safely captures TenderDetail's 7-9 PM digest regardless of which
# literal calendar date IMAP would otherwise bucket it under.
BUSINESS_DAY_CUTOFF = dtime(19, 30)  # 7:30 PM


def get_search_window_start() -> datetime:
    """
    Returns the datetime marking the start of the current business-day
    intake window: yesterday at 7:30 PM.
    """
    yesterday = date.today() - timedelta(days=1)
    return datetime.combine(yesterday, BUSINESS_DAY_CUTOFF)


def decode_subject(raw_subject) -> str:
    """Decodes email subject lines that may be encoded."""
    if raw_subject is None:
        return "(no subject)"
    decoded_parts = decode_header(raw_subject)
    subject = ""
    for part, encoding in decoded_parts:
        if isinstance(part, bytes):
            subject += part.decode(encoding or "utf-8", errors="replace")
        else:
            subject += part
    return subject


# Matches subjects like:
#   "49 New Tender/s, 21-Jun-26 - Tender247"
#   "194 New Tenders Date 21-June-2026 - www.TenderDetail.com"
# Requires a number at the very start of the subject, immediately
# followed (allowing whitespace) by "New Tender" (with optional "s" or "/s") —
# this is what distinguishes a genuine countable batch from a notification-style
# email.
TENDER_COUNT_PATTERN = re.compile(r"^\s*(\d+)\s+New Tender(?:s|/s)?", re.IGNORECASE)


def extract_tender_count(subject: str) -> int | None:
    """
    Returns the leading tender count from a subject line, or None if
    the subject doesn't start with a number followed by "New Tender/s" or "New Tenders".
    """
    match = TENDER_COUNT_PATTERN.match(subject)
    if match:
        return int(match.group(1))
    return None


def extract_html_body(msg) -> str:
    """Extracts HTML body from an email, preferring HTML over plain text."""
    html_body = None
    plain_body = None

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            if content_type == "text/html" and html_body is None:
                html_body = part.get_payload(decode=True).decode(
                    part.get_content_charset() or "utf-8", errors="replace"
                )
            elif content_type == "text/plain" and plain_body is None:
                plain_body = part.get_payload(decode=True).decode(
                    part.get_content_charset() or "utf-8", errors="replace"
                )
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            html_body = payload.decode(
                msg.get_content_charset() or "utf-8", errors="replace"
            )

    return html_body or plain_body or ""


def connect_to_gmail() -> imaplib.IMAP4_SSL:
    """Connects and logs into Gmail via IMAP. Raises on failure."""
    mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    mail.login(GMAIL_ADDRESS, APP_PASSWORD)
    mail.select("inbox")
    return mail


def fetch_todays_emails() -> dict:
    """
    Fetches the current business-day batch from each known tender source.

    Window used: yesterday 7:30 PM  →  now.
    This accounts for TenderDetail's evening (7-9 PM) digest belonging to
    the NEXT business day's batch, rather than relying on IMAP's literal
    calendar-date matching which would miss it.

    Both TenderDetail and Tender247 send multiple email types from the same
    address. Only subjects with a leading number followed by "New Tender(s)"
    are kept (e.g. "42 New Tender/s, ..."). "Results" emails and
    notification-style emails without a leading count (e.g. "New Tender/s,
    18-Jun-26 (Noon) - Tender247") are excluded.

    A source can send MULTIPLE valid "New Tender" batches in one window
    (e.g. a morning batch and a separate noon batch) — these are NOT
    assumed to be cumulative, so ALL of them are returned, not just the
    newest one.

    Returns a dict like:
        {
            "TenderDetail": [ {"subject": ..., "sender": ..., "html": ..., "received_at": ...} ],
            "Tender247":    [ {...}, {...} ],   # e.g. two batches same day
        }
    A source is omitted from the result if no matching email was found
    in the window.
    """
    mail = connect_to_gmail()
    window_start = get_search_window_start()

    # IMAP SINCE only supports date granularity (no time-of-day), so we
    # fetch everything since that calendar date, then filter precisely
    # by actual datetime + subject in Python below.
    since_str = window_start.strftime("%d-%b-%Y")  # IMAP format: 17-Jun-2026

    results = {}
    all_subjects_seen = {}  # for debug visibility — every candidate, pre-filter, with timestamp

    for source_name, sender_keyword in SOURCES.items():
        search_query = f'(FROM "{sender_keyword}" SINCE "{since_str}")'
        status, uids = mail.search(None, search_query)

        if status != "OK":
            continue

        uid_list = uids[0].split()
        if not uid_list:
            continue

        all_subjects_seen[source_name] = []
        matched_msgs = []  # collect ALL valid "New Tender" emails, not just newest

        for uid in reversed(uid_list):  # newest first
            status, msg_data = mail.fetch(uid, "(RFC822)")
            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email)

            # Precise time check — must be within our business-day window
            try:
                received_at = parsedate_to_datetime(msg.get("Date"))
                if received_at.tzinfo is not None:
                    received_at = received_at.replace(tzinfo=None)
            except (TypeError, ValueError):
                continue

            if received_at < window_start:
                continue  # too old, outside this business day's window

            subject = decode_subject(msg.get("Subject"))
            subject_lower = subject.lower()

            all_subjects_seen[source_name].append({
                "subject":     subject,
                "received_at": received_at,
            })

            # Both sources send a "Results" / "Tender Opening Report" stream
            # that is NOT new opportunities — exclude it for both.
            if "result" in subject_lower:
                continue

            # Only keep subjects with a genuine leading count, e.g.
            # "42  New Tender/s, 18-Jun-26 - Tender247". Subjects like
            # "New Tender/s, 18-Jun-26 (Noon) - Tender247" have no count
            # prefix and are excluded entirely — they're not treated as
            # a countable, authoritative batch.
            tender_count = extract_tender_count(subject)
            if tender_count is None:
                continue

            matched_msgs.append({
                "subject":      subject,
                "sender":       msg.get("From", ""),
                "html":         extract_html_body(msg),
                "received_at":  received_at,
                "tender_count": tender_count,
            })

        if matched_msgs:
            # Multiple "New Tender" batches can arrive in one day (e.g.
            # morning, noon). Keep ALL of them — each may contain
            # different tenders, not a superset of the previous one.
            results[source_name] = matched_msgs

    mail.logout()
    results["_debug_all_subjects_seen"] = all_subjects_seen
    return results


def extract_tenderdetail_view_all_url(html: str) -> str | None:
    """
    Finds the 'Click Here To View All N New Tenders' link inside
    a TenderDetail email. This URL is unique per email and contains
    a token — no login required to open it.
    """
    soup = BeautifulSoup(html, "html.parser")

    link = soup.find(
        "a",
        string=lambda text: text and "view all" in text.lower() and "tender" in text.lower()
    )

    if link and link.get("href"):
        return link["href"]

    # Fallback: scan all links for the tenderdetail.com/dailytenders or connect.tenderdetail.com patterns
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        if "tenderdetail.com/dailytenders" in href or "connect.tenderdetail.com" in href:
            return href

    return None


def extract_tender247_view_details_url(html: str) -> str | None:
    """
    Finds the 'View Details' link inside a Tender247 "New Tender/s" email.
    This URL requires login before the tender list is visible.
    """
    soup = BeautifulSoup(html, "html.parser")

    link = soup.find(
        "a",
        string=lambda text: text and "view details" in text.lower()
    )

    if link and link.get("href"):
        return link["href"]

    # Fallback: scan all links for known Tender247 result/login patterns
    for a_tag in soup.find_all("a", href=True):
        href_lower = a_tag["href"].lower()
        if "tender247.com" in href_lower or "bidsnrfp.com" in href_lower:
            return a_tag["href"]

    return None


def run_test():
    """Runs the full flow and prints what was found, for manual verification."""
    print("Connecting to Gmail and searching for this business day's tender emails...\n")
    print(f"Search window starts at: {get_search_window_start()}\n")

    emails_found = fetch_todays_emails()

    debug_subjects = emails_found.pop("_debug_all_subjects_seen", {})
    print("All candidate subjects seen in window (before filtering):")
    for source, candidates in debug_subjects.items():
        print(f"   {source}: {len(candidates)} email(s) total in window")
        for c in candidates:
            print(f"      [{c['received_at']}]  {c['subject']}")
    print()

    if not emails_found:
        print("No matching 'New Tender' emails found in this window from either source.")
        return

    for source, batches in emails_found.items():
        print(f"=== {source} — {len(batches)} valid 'New Tender' batch(es) kept ===")

        for i, data in enumerate(batches, start=1):
            print(f"\n   Batch {i}/{len(batches)}")
            print(f"   Received:     {data['received_at']}")
            print(f"   Subject:      {data['subject']}")
            print(f"   Tender count: {data['tender_count']}")
            print(f"   From:         {data['sender']}")
            print(f"   HTML length:  {len(data['html'])} characters")

            if source == "TenderDetail":
                url = extract_tenderdetail_view_all_url(data["html"])
                print(f"   'View All' URL found: {url}")

            elif source == "Tender247":
                url = extract_tender247_view_details_url(data["html"])
                print(f"   'View Details' (login-required) URL found: {url}")
        print()


if __name__ == "__main__":
    run_test()
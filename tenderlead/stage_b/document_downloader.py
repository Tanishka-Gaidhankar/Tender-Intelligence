"""
document_downloader.py

Phase 2 of Stage B: Downloads tender documents to a per-tender temporary folder.

Design decisions:
- Files are stored in a temp directory keyed by tender_id so runs never collide.
- Files above MAX_FILE_SIZE_MB are NOT downloaded — they are flagged for manual review.
- TenderDetail downloads require session cookies (auth-gated links).
- Tender247 downloads may also require the authenticated Playwright session for
  signed/redirected URLs; plain requests with cookies are tried first.
- Scanned PDFs (where pdfplumber extracts < MIN_TEXT_CHARS) are flagged as
  "scanned" so the classifier/extractor can skip them gracefully.
"""

import json
import os
import re
import requests
import urllib.parse

MAX_FILE_SIZE_MB = 20          # skip files larger than this
MIN_TEXT_CHARS = 50            # fewer chars than this → treat PDF as scanned image
DOWNLOAD_TIMEOUT_SEC = 60      # per-file download timeout

# Base temp directory — redirect to workspace frontend uploads directory so files can be served
_BASE_TEMP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "uploads"))


def _tender_temp_dir(tender_id: str) -> str:
    """Returns (and creates) a per-tender temp directory."""
    d = os.path.join(_BASE_TEMP_DIR, str(tender_id))
    os.makedirs(d, exist_ok=True)
    return d


def _extract_zip_recursive(zip_path: str, target_dir: str) -> list[dict]:
    """
    Recursively extracts zip files, including nested ones, and returns
    a list of dicts for each final extracted file: {"name": str, "path": str}.
    """
    import zipfile
    extracted_items = []
    zips_to_process = [(zip_path, "")]
    
    while zips_to_process:
        current_zip, prefix = zips_to_process.pop(0)
        try:
            with zipfile.ZipFile(current_zip, 'r') as zip_ref:
                zip_ref.extractall(target_dir)
                for file_info in zip_ref.infolist():
                    if file_info.is_dir():
                        continue
                    extracted_path = os.path.join(target_dir, file_info.filename)
                    extracted_path = os.path.abspath(extracted_path)
                    # safety check: ensure path is within target_dir
                    if not extracted_path.startswith(target_dir):
                        continue
                    if not os.path.exists(extracted_path):
                        continue
                    inner_name = os.path.basename(file_info.filename)
                    new_prefix = f"{prefix} / {inner_name}" if prefix else inner_name
                    if extracted_path.lower().endswith('.zip'):
                        zips_to_process.append((extracted_path, new_prefix))
                    else:
                        extracted_items.append({
                            "name": new_prefix,
                            "path": extracted_path
                        })
        except Exception as e:
            print(f"       → Failed to extract ZIP {current_zip}: {e}")
            
    return extracted_items


def _safe_filename(url: str, idx: int, name: str) -> str:
    """
    Derives a safe filename from the URL or document name.
    Appends index to avoid collisions.
    """
    url_path = url.split("?")[0].split("#")[0]
    ext = os.path.splitext(url_path)[1].lower()
    if ext not in (".pdf", ".xlsx", ".xls", ".zip", ".docx", ".doc"):
        ext = ".pdf"  # default assumption

    safe_name = re.sub(r"[^\w\s-]", "", name or "document").strip()
    safe_name = re.sub(r"\s+", "_", safe_name)[:50]
    return f"{idx:02d}_{safe_name}{ext}"


def _get_original_filename(resp, url: str, name: str, idx: int) -> str:
    """
    Retrieves the original filename from Content-Disposition header, final redirect URL,
    or the original URL, keeping original names exactly as scraped.
    """
    # 1. Try from Content-Disposition header
    disp = resp.headers.get("Content-Disposition", "") if resp is not None else ""
    if disp:
        match = re.search(r'filename=[\"\']?([^\"\';]+)[\"\']?', disp)
        if match:
            fn = match.group(1).strip()
            fn = os.path.basename(fn)
            if fn:
                return urllib.parse.unquote(fn)

    # 2. Try from final URL path (handles redirects)
    if resp is not None:
        final_url = resp.url
        url_path = final_url.split("?")[0].split("#")[0]
        fn = os.path.basename(url_path)
        fn = urllib.parse.unquote(fn)
        if fn and "." in fn:
            return fn

    # 3. Try from original URL path
    url_path = url.split("?")[0].split("#")[0]
    fn = os.path.basename(url_path)
    fn = urllib.parse.unquote(fn)
    if fn and "." in fn:
        return fn

    # 4. Fallback to safe filename if no standard name found
    return _safe_filename(url, idx, name)


def _load_tenderdetail_cookies() -> dict:
    """Loads TenderDetail session cookies for authenticated downloads."""
    candidates = [
        os.path.join(os.path.dirname(__file__), "..", "..", "tenderdetail_session.json"),
        os.path.join(os.path.dirname(__file__), "..", "tenderdetail_session.json"),
        "tenderdetail_session.json",
    ]
    for candidate in candidates:
        path = os.path.abspath(candidate)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                cookies = {}
                for c in data.get("cookies", []):
                    if "tenderdetail.com" in c.get("domain", ""):
                        cookies[c["name"]] = c["value"]
                return cookies
            except Exception as e:
                print(f"  Warning: Could not load TenderDetail cookies from {path}: {e}")
    return {}


def _load_tender247_cookies() -> dict:
    """Loads Tender247 session cookies for authenticated downloads."""
    candidates = [
        os.path.join(os.path.dirname(__file__), "..", "..", "tender247_session.json"),
        os.path.join(os.path.dirname(__file__), "..", "tender247_session.json"),
        "tender247_session.json",
    ]
    for candidate in candidates:
        path = os.path.abspath(candidate)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                cookies = {}
                for c in data.get("cookies", []):
                    if "tender247.com" in c.get("domain", ""):
                        cookies[c["name"]] = c["value"]
                return cookies
            except Exception as e:
                print(f"  Warning: Could not load Tender247 cookies from {path}: {e}")
    return {}


def _get_file_size_mb(session, url: str) -> float | None:
    """Returns size in MB, or None if unavailable."""
    try:
        resp = session.head(url, timeout=15, allow_redirects=True)
        content_length = resp.headers.get("Content-Length")
        if content_length:
            return int(content_length) / (1024 * 1024)
    except Exception:
        pass
    return None


def download_tender_documents(
    tender_id: str,
    doc_links: list[dict],
    source: str = "TenderDetail",
) -> list[dict]:
    """
    Downloads each document in doc_links for the given tender to a temporary folder.
    """
    temp_dir = _tender_temp_dir(tender_id)

    # Build an authenticated session
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    })
    cookies = (
        _load_tenderdetail_cookies()
        if source == "TenderDetail"
        else _load_tender247_cookies()
    )
    for name, val in cookies.items():
        domain = ".tenderdetail.com" if source == "TenderDetail" else ".tender247.com"
        session.cookies.set(name, val, domain=domain)

    results = []
    for idx, doc in enumerate(doc_links, start=1):
        url = doc.get("url", "")
        name = doc.get("name", "Document")
        result = {
            "name": name,
            "url": url,
            "local_path": None,
            "size_mb": None,
            "skipped": False,
            "skip_reason": None,
            "is_scanned": False,  # populated later by classifier
        }

        # Check if file has already been downloaded (e.g. by Playwright collector)
        if doc.get("local_path") and os.path.exists(doc["local_path"]):
            local_path = doc["local_path"]
            filename = os.path.basename(local_path)
            actual_mb = os.path.getsize(local_path) / (1024 * 1024)
            result["local_path"] = local_path
            result["size_mb"] = round(actual_mb, 2)
            print(f"  [{idx}] Already downloaded: {name} ({actual_mb:.2f} MB)")
            
            # Check if this downloaded file is a ZIP archive; if so, extract it
            if filename.lower().endswith(".zip"):
                try:
                    import zipfile
                    print(f"       → Extracting ZIP archive contents...")
                    extracted_files = []
                    with zipfile.ZipFile(local_path, 'r') as zip_ref:
                        # Extract everything to temp_dir
                        zip_ref.extractall(temp_dir)
                        # Build list of extracted files
                        for file_info in zip_ref.infolist():
                            if file_info.is_dir():
                                continue
                            extracted_path = os.path.join(temp_dir, file_info.filename)
                            extracted_path = os.path.abspath(extracted_path)
                            # Security check: verify path stays within temp_dir
                            if not extracted_path.startswith(temp_dir):
                                continue
                            
                            if os.path.exists(extracted_path):
                                inner_name = os.path.basename(file_info.filename)
                                extracted_files.append({
                                    "name": f"{name} / {inner_name}",
                                    "url": url,
                                    "local_path": extracted_path,
                                    "size_mb": round(os.path.getsize(extracted_path) / (1024 * 1024), 2),
                                    "skipped": False,
                                    "skip_reason": None,
                                    "is_scanned": False,
                                })
                    
                    # Mark the zip itself as completed/extracted but skipped for text classification
                    result["skipped"] = True
                    result["skip_reason"] = "Extracted zip content"
                    results.append(result)
                    
                    # Append all extracted files to results
                    results.extend(extracted_files)
                    continue
                except Exception as ze:
                    print(f"       → Failed to extract ZIP archive: {ze}")
            
            results.append(result)
            continue

        if not url:
            result["skipped"] = True
            result["skip_reason"] = "Missing URL"
            results.append(result)
            continue

        try:
            size_mb = _get_file_size_mb(session, url)
            if size_mb and size_mb > MAX_FILE_SIZE_MB:
                result["skipped"] = True
                result["skip_reason"] = f"File size ({size_mb:.1f} MB) exceeds limit of {MAX_FILE_SIZE_MB} MB"
                print(f"       → Skipped (too large: {size_mb:.1f} MB)")
                results.append(result)
                continue

            resp = session.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT_SEC)
            if resp.status_code != 200:
                result["skipped"] = True
                result["skip_reason"] = f"HTTP {resp.status_code}"
                print(f"       → Failed (HTTP {resp.status_code})")
                results.append(result)
                continue

            # Determine original filename keeping exact folder/file names
            filename = _get_original_filename(resp, url, name, idx)
            
            # Avoid collisions
            base, ext = os.path.splitext(filename)
            counter = 1
            local_path = os.path.join(temp_dir, filename)
            while os.path.exists(local_path):
                filename = f"{base}_{counter}{ext}"
                local_path = os.path.join(temp_dir, filename)
                counter += 1

            print(f"       → Saving as original name: {filename}")

            # Stream-write so we don't hold the whole file in memory
            downloaded_bytes = 0
            file_too_large = False
            with open(local_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
                        downloaded_bytes += len(chunk)
                        if downloaded_bytes > MAX_FILE_SIZE_MB * 1024 * 1024:
                            file_too_large = True
                            break

            if file_too_large:
                result["skipped"] = True
                result["skip_reason"] = "File exceeded size limit during download"
                print(f"       → Aborted (exceeded {MAX_FILE_SIZE_MB} MB)")
                if os.path.exists(local_path):
                    os.remove(local_path)
                results.append(result)
                continue

            actual_mb = downloaded_bytes / (1024 * 1024)
            result["local_path"] = local_path
            result["size_mb"] = round(actual_mb, 2)
            print(f"       → Saved ({actual_mb:.2f} MB)")

            # Check if this downloaded file is a ZIP archive; if so, extract it
            if filename.lower().endswith(".zip") and os.path.exists(local_path):
                try:
                    print(f"       → Extracting ZIP archive contents recursively...")
                    extracted_items = _extract_zip_recursive(local_path, temp_dir)
                    extracted_files = []
                    for item in extracted_items:
                        extracted_files.append({
                            "name": f"{name} / {item['name']}",
                            "url": url,
                            "local_path": item["path"],
                            "size_mb": round(os.path.getsize(item["path"]) / (1024 * 1024), 2),
                            "skipped": False,
                            "skip_reason": None,
                            "is_scanned": False,
                        })
                    
                    # Mark the zip itself as completed/extracted but skipped for text classification
                    result["skipped"] = True
                    result["skip_reason"] = "Extracted zip content"
                    results.append(result)
                    
                    # Append all extracted files to results
                    results.extend(extracted_files)
                    continue
                except Exception as ze:
                    print(f"       → Failed to extract ZIP archive: {ze}")

        except Exception as e:
            result["skipped"] = True
            result["skip_reason"] = f"Download error: {e}"
            print(f"       → Error: {e}")
            if local_path and os.path.exists(local_path):
                os.remove(local_path)

        results.append(result)

    downloaded = sum(1 for r in results if not r["skipped"])
    skipped = len(results) - downloaded
    print(f"  Download summary: {downloaded} downloaded, {skipped} skipped")
    return results


def cleanup_tender_documents(tender_id: str) -> None:
    """
    Deletes all downloaded documents for a tender (called when user rejects the tender).
    """
    import shutil
    temp_dir = os.path.join(_BASE_TEMP_DIR, str(tender_id))
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)
        print(f"  Deleted Stage B documents for tender {tender_id}")

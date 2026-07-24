"""
document_classifier.py
Phase 3 of Stage B: Reads the first 2-3 pages of each downloaded document and
classifies its type (NIT, BOQ, PQ, FINANCIAL, DRAWING, CORRIGENDUM, UNKNOWN).
"""
from __future__ import annotations
import os

NIT = "NIT"
BOQ = "BOQ"
PQ = "PQ"
FINANCIAL = "FINANCIAL"
DRAWING = "DRAWING"
CORRIGENDUM = "CORRIGENDUM"
UNKNOWN = "UNKNOWN"

ALL_TYPES = [NIT, BOQ, PQ, FINANCIAL, DRAWING, CORRIGENDUM, UNKNOWN]
SCOPE_TYPES = {NIT, BOQ}
QUALIFICATION_TYPES = {NIT, PQ}
BID_DOCS_TYPES = {NIT, FINANCIAL}
SCAN_PAGES = 3


def extract_first_pages_text(local_path: str, num_pages: int = SCAN_PAGES) -> tuple[str, bool]:
    """
    Extracts text from the first `num_pages` pages of a document using PyMuPDF (fitz).
    Returns: (text: str, is_scanned: bool)
    """
    if not local_path or not os.path.exists(local_path):
        return "", False

    ext = os.path.splitext(local_path)[1].lower()

    if ext == ".pdf":
        try:
            import fitz
            parts = []
            doc = fitz.open(local_path)
            max_p = min(len(doc), num_pages)
            for i in range(max_p):
                page = doc[i]
                txt = page.get_text("text") or ""
                if txt.strip():
                    parts.append(txt)

            text = "\n".join(parts).strip()
            is_scanned = len(text) < 50

            # If standard text is sparse, extract layout text blocks
            if is_scanned:
                block_text = []
                for i in range(max_p):
                    blocks = doc[i].get_text("blocks")
                    for b in blocks:
                        if len(b) >= 5 and isinstance(b[4], str) and b[4].strip():
                            block_text.append(b[4].strip())
                alt_text = "\n".join(block_text).strip()
                if len(alt_text) >= 50:
                    text = alt_text
                    is_scanned = False

            return text, is_scanned
        except Exception as e:
            try:
                import pdfplumber
                parts = []
                with pdfplumber.open(local_path) as pdf:
                    for i, page in enumerate(pdf.pages[:num_pages]):
                        parts.append(page.extract_text() or "")
                text = "\n".join(parts).strip()
                return text, len(text) < 50
            except Exception as e2:
                return f"[PDF read error: {e2}]", False

    elif ext == ".docx":
        try:
            from docx import Document
            doc = Document(local_path)
            text = "\n".join(p.text for p in doc.paragraphs[:100]).strip()
            return text, False
        except Exception as e:
            return f"[DOCX read error: {e}]", False

    elif ext in (".xlsx", ".xls"):
        try:
            import openpyxl
            wb = openpyxl.load_workbook(local_path, data_only=True, read_only=True)
            sheet = wb.active
            rows_text = []
            for row in sheet.iter_rows(max_row=100, values_only=True):
                row_str = " ".join(str(v) for v in row if v is not None)
                if row_str.strip():
                    rows_text.append(row_str)
            return "\n".join(rows_text), False
        except Exception as e:
            return f"[XLSX read error: {e}]", False

    elif ext in (".html", ".htm"):
        try:
            from bs4 import BeautifulSoup
            with open(local_path, "r", encoding="utf-8", errors="ignore") as f:
                soup = BeautifulSoup(f.read(), "html.parser")
            for script in soup(["script", "style"]):
                script.decompose()
            return soup.get_text(separator="\n").strip()[:10000], False
        except Exception as e:
            return f"[HTML read error: {e}]", False

    return "", False


def extract_full_text(local_path: str) -> str:
    """Extracts ALL text from a document across all pages."""
    if not local_path or not os.path.exists(local_path):
        return ""
    text, _ = extract_first_pages_text(local_path, num_pages=500)
    return text


def _heuristic_classify(text: str, name: str) -> str:
    """Keyword-based document type classification."""
    combined = (str(text or "") + " " + str(name or "")).lower()

    if any(kw in combined for kw in ["corrigendum", "addendum", "amendment", "erratum"]):
        return CORRIGENDUM
    if any(kw in combined for kw in ["bill of quantities", "schedule of work", "boq", "rate schedule", "item wise"]):
        return BOQ
    if any(kw in combined for kw in ["pre-qualification", "eligibility criteria", "technical qualification", "annual turnover", "similar works", "experience certificate"]):
        return PQ
    if any(kw in combined for kw in ["earnest money", "emd", "demand draft", "bank guarantee", "bid security"]):
        return FINANCIAL
    if any(kw in combined for kw in ["drawing", "layout", "plan", "elevation", "specification"]):
        return DRAWING
    if any(kw in combined for kw in ["notice inviting tender", "nit", "tender notice", "scope of work", "general conditions", "terms and conditions"]):
        return NIT

    return UNKNOWN


def classify_document(text: str, name: str) -> str:
    """Classifies a document by snippet text and filename."""
    return _heuristic_classify(text, name)


def classify_all_documents(downloaded_docs: list[dict]) -> list[dict]:
    """Runs first-page extraction and classification on every downloaded document."""
    for doc in downloaded_docs:
        if doc.get("skipped") or not doc.get("local_path"):
            doc["doc_type"] = UNKNOWN
            doc["first_pages_text"] = ""
            continue

        local_path = doc["local_path"]
        name = doc.get("name", os.path.basename(local_path))
        snippet, is_scanned = extract_first_pages_text(local_path)
        doc["first_pages_text"] = snippet
        doc["is_scanned"] = is_scanned

        if is_scanned:
            doc["doc_type"] = UNKNOWN
            doc["skip_reason"] = "Scanned PDF — no extractable text"
            continue

        doc_type = classify_document(snippet, name)
        doc["doc_type"] = doc_type

    return downloaded_docs

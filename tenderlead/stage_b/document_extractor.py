import json
import os
from .document_classifier import (
    BOQ, FINANCIAL, NIT, PQ,
    SCOPE_TYPES, QUALIFICATION_TYPES, BID_DOCS_TYPES,
    extract_full_text,
)

MAX_CHARS_PER_DOC = 8000
MAX_TOTAL_CHARS = 20000


def _select_relevant_docs(classified_docs: list[dict]) -> dict[str, list[dict]]:
    readable = [
        d for d in classified_docs
        if not d.get("skipped") and not d.get("is_scanned") and d.get("local_path")
    ]
    return {
        "scope": [d for d in readable if d.get("doc_type") in SCOPE_TYPES or d.get("doc_type") == NIT],
        "qualification": [d for d in readable if d.get("doc_type") in QUALIFICATION_TYPES or d.get("doc_type") == NIT],
        "bid_documents": [d for d in readable if d.get("doc_type") in BID_DOCS_TYPES or d.get("doc_type") == NIT],
        "readable": readable
    }


def _build_document_block(docs: list[dict], label: str) -> tuple[str, list[str]]:
    parts = []
    source_names = []
    chars_used = 0

    for doc in docs:
        name = doc.get("name", "Unknown Document")
        local_path = doc.get("local_path")
        if not local_path or not os.path.exists(local_path):
            continue

        full_text = extract_full_text(local_path)
        if not full_text or len(full_text.strip()) < 20:
            continue

        excerpt = full_text[:MAX_CHARS_PER_DOC]
        if chars_used + len(excerpt) > MAX_TOTAL_CHARS:
            remaining = MAX_TOTAL_CHARS - chars_used
            if remaining < 200:
                break
            excerpt = excerpt[:remaining]

        doc_type_str = doc.get("doc_type", "NIT")
        parts.append(f"=== Document: {name} (Type: {doc_type_str}) ===\n{excerpt}")
        source_names.append(name)
        chars_used += len(excerpt)

    return "\n\n".join(parts), source_names


def extract_tender_intelligence(
    classified_docs: list[dict],
    tender_title: str,
    tender_id: str = "",
    pre_extracted_eligibility: str = "",
    pre_extracted_documents: list[str] = None
) -> dict:
    has_pre_extracted = bool(pre_extracted_eligibility or pre_extracted_documents)

    empty_result = {
        "scope_of_work": f"Tender Scope: {tender_title}",
        "scope_source_documents": [],
        "qualification_criteria": pre_extracted_eligibility or "Standard technical and financial eligibility criteria apply. Minimum turnover and past experience in similar civil works required.",
        "qualification_source_documents": ["Portal AI Summary"] if pre_extracted_eligibility else [],
        "documents_required_for_bid": pre_extracted_documents or [
            "Earnest Money Deposit (EMD) receipt / BG",
            "Company / Firm Registration Certificate",
            "GST Registration & Latest Return",
            "PAN Card Copy",
            "Technical Experience / Work Completion Certificates",
            "Financial Audited Statements / Turnover Certificate"
        ],
        "bid_docs_source_documents": ["Portal AI Summary"] if pre_extracted_documents is not None else [],
        "extraction_confidence": "high" if has_pre_extracted else "low",
        "notes": "Extracted using portal summary fields." if has_pre_extracted else "Extracted with standard baseline template.",
        "stage_b_status": "success",
    }

    relevant = _select_relevant_docs(classified_docs)
    readable = relevant.get("readable", [])

    if not readable:
        return empty_result

    # Combine text from readable documents
    combined_text, source_names = _build_document_block(readable, "All Documents")
    if not combined_text or len(combined_text.strip()) < 50:
        return empty_result

    if has_pre_extracted:
        system_prompt = (
            "You are a Senior Tender Engineering Analyst reading official Indian government tender documents "
            "for KBP Civil Engineering Services, a civil engineering contractor.\n\n"
            "From the provided tender document text, extract ONLY the scope of work (physical work required, key work items, quantities, location).\n"
            "Format the scope of work as a clear, point-wise / bulleted list of physical work items for better understanding.\n"
            "Do NOT extract qualification criteria or document checklists as those are already pre-scraped.\n\n"
            "Respond ONLY in valid JSON with these exact keys:\n"
            "{\n"
            '  "scope_of_work": "Point-wise / bulleted list of physical work items, quantities, and location.",\n'
            '  "qualification_criteria": "",\n'
            '  "documents_required_for_bid": [],\n'
            '  "extraction_confidence": "high or medium or low",\n'
            '  "notes": "Any caveats, missing sections, or ambiguities found in scope"\n'
            "}"
        )
    else:
        system_prompt = (
            "You are a Senior Tender Engineering Analyst reading official Indian government tender documents "
            "for KBP Civil Engineering Services, a civil engineering contractor.\n\n"
            "From the provided tender document text, extract EXACTLY the following three items. "
            "Be precise. Extract only what is explicitly stated in the documents.\n"
            "Format the scope of work and qualification criteria as clear, point-wise / bulleted lists for better understanding.\n\n"
            "Respond ONLY in valid JSON with these exact keys:\n"
            "{\n"
            '  "scope_of_work": "Point-wise / bulleted list of physical work items, quantities, and location.",\n'
            '  "qualification_criteria": "Point-wise list of all pre-qualification / eligibility requirements: minimum annual turnover, similar work experience, class of contractor, certifications, registrations, etc.",\n'
            '  "documents_required_for_bid": [\n'
            '    "Earnest Money Deposit (EMD) receipt",\n'
            '    "Company registration certificate",\n'
            '    "Experience certificates for similar works in last 7 years"\n'
            "  ],\n"
            '  "extraction_confidence": "high or medium or low",\n'
            '  "notes": "Any caveats, missing sections, or ambiguities found"\n'
            "}"
        )

    user_prompt = (
        f"Tender Title: {tender_title}\n"
        f"Tender ID: {tender_id}\n\n"
        f"--- TENDER DOCUMENT TEXT ---\n{combined_text}\n--- END OF DOCUMENTS ---\n\n"
        "Extract the fields as JSON."
    )

    try:
        from ..ai.llm_client import call_llm
        response = call_llm(user_prompt, system_prompt, json_mode=True)
        if response and response.strip():
            import re
            json_str = response.strip()
            if not json_str.startswith("{"):
                match = re.search(r"\{.*\}", json_str, re.DOTALL)
                if match:
                    json_str = match.group(0)
            parsed = json.loads(json_str)

            # Normalize scope_of_work to string
            scope_sow = parsed.get("scope_of_work", "")
            if isinstance(scope_sow, list):
                scope_sow = "\n".join(str(item) for item in scope_sow)
            elif not isinstance(scope_sow, str):
                scope_sow = str(scope_sow) if scope_sow is not None else ""

            # Qualification criteria
            qual_criteria = pre_extracted_eligibility if pre_extracted_eligibility else parsed.get("qualification_criteria", "")
            if isinstance(qual_criteria, list):
                qual_criteria = "\n".join(str(item) for item in qual_criteria)
            elif not isinstance(qual_criteria, str):
                qual_criteria = str(qual_criteria) if qual_criteria is not None else ""

            # Documents checklist
            bid_docs = pre_extracted_documents if pre_extracted_documents is not None else parsed.get("documents_required_for_bid", [])
            if isinstance(bid_docs, str):
                bid_docs = [line.strip() for line in bid_docs.split("\n") if line.strip()]
            elif isinstance(bid_docs, list):
                bid_docs = [str(item).strip() for item in bid_docs if item]
            else:
                bid_docs = [str(bid_docs)] if bid_docs is not None else []

            notes = parsed.get("notes", "Extraction complete.")
            if has_pre_extracted:
                notes = (notes + " (Eligibility and Bid Documents parsed directly from Portal AI Summary).").strip()

            return {
                "scope_of_work": scope_sow or empty_result["scope_of_work"],
                "scope_source_documents": source_names,
                "qualification_criteria": qual_criteria or empty_result["qualification_criteria"],
                "qualification_source_documents": ["Portal AI Summary"] if pre_extracted_eligibility else source_names,
                "documents_required_for_bid": bid_docs or empty_result["documents_required_for_bid"],
                "bid_docs_source_documents": ["Portal AI Summary"] if pre_extracted_documents is not None else source_names,
                "extraction_confidence": parsed.get("extraction_confidence", "high" if has_pre_extracted else "medium"),
                "notes": notes,
                "stage_b_status": "success" if scope_sow else "partial",
            }
    except Exception as e:
        print(f"Extraction error: {e}")

    return empty_result


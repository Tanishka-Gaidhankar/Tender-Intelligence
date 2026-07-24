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


def _extract_work_relevant_text(full_text: str, max_chars: int = 8000) -> str:
    """Extracts text, prioritizing sections describing work to be performed, project work, TOR, BOQ, specs."""
    if len(full_text) <= max_chars:
        return full_text

    # Keywords for work to be performed
    work_keywords = [
        "scope of work", "project work", "expected work", "work to be done",
        "work to be performed", "nature of work", "description of work",
        "terms of reference", "technical specification", "schedule of requirement",
        "bill of quantities", "boq", "deliverables", "duties of contractor"
    ]

    import re
    paragraphs = re.split(r'\n\s*\n', full_text)
    header_part = full_text[:2000] # Always include cover/first page info
    
    matching_paragraphs = []
    seen = set()
    for p in paragraphs:
        p_strip = p.strip()
        if not p_strip or p_strip in seen:
            continue
        p_lower = p_strip.lower()
        if any(kw in p_lower for kw in work_keywords):
            matching_paragraphs.append(p_strip)
            seen.add(p_strip)

    if matching_paragraphs:
        combined = header_part + "\n\n--- EXTRACTED WORK SECTIONS & SPECIFICATIONS ---\n\n" + "\n\n".join(matching_paragraphs)
        return combined[:max_chars]

    return full_text[:max_chars]


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

        excerpt = _extract_work_relevant_text(full_text, MAX_CHARS_PER_DOC)
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
        "scope_of_work": "Scope of work is not mentioned in the downloaded tender documents.",
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
            "Analyze the provided tender document text carefully to identify the Scope of Work — the actual work, physical tasks, services, or deliverables to be performed.\n"
            "IMPORTANT: The document section may NOT be literally titled 'Scope of Work'. Look for any section or text describing the work to be executed under synonyms such as:\n"
            "- 'Project Work' / 'Expected Work to be Done' / 'Work to be Performed'\n"
            "- 'Nature of Work' / 'Description of Work' / 'Brief Description of Work'\n"
            "- 'Terms of Reference (TOR)' / 'Technical Specifications' / 'Execution Plan'\n"
            "- 'Schedule of Requirements' / 'Bill of Quantities (BOQ)' / 'Deliverables & Milestones'\n"
            "- 'Services Required' / 'Duties and Responsibilities of Contractor'\n\n"
            "If ANY of these sections or descriptions exist in the document text:\n"
            "- Extract all physical tasks, activities, quantities, site locations, and expected deliverables.\n"
            "- Format the scope of work as a clear, point-wise / bulleted list.\n"
            "Do NOT extract qualification criteria or document checklists as those are already pre-scraped.\n\n"
            "ONLY if NO section or description of the work to be performed exists anywhere in the provided document text:\n"
            "- Set 'scope_of_work' to: 'Scope of work is not mentioned in the downloaded tender documents.'\n"
            "- Do NOT simply copy or repeat the tender title.\n\n"
            "Respond ONLY in valid JSON with these exact keys:\n"
            "{\n"
            '  "scope_of_work": "Point-wise list of physical work items/tasks to be performed, or \'Scope of work is not mentioned in the downloaded tender documents.\'",\n'
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
            "Analyze the provided tender document text carefully to extract EXACTLY the following three items.\n"
            "1. Scope of Work: Extract the work to be performed, physical tasks, deliverables, quantities, and location from the document text.\n"
            "   IMPORTANT: Look for sections titled 'Scope of Work', 'Project Work', 'Expected Work to be Done', 'Work to be Performed', 'Nature of Work', 'Terms of Reference (TOR)', 'Technical Specifications', 'Schedule of Requirements', or 'BOQ Items'.\n"
            "   Format as a point-wise list. ONLY if NO work description of any kind is specified in the text, set it to: 'Scope of work is not mentioned in the downloaded tender documents.' Do NOT repeat the tender title.\n"
            "2. Qualification Criteria: Point-wise list of pre-qualification / eligibility requirements.\n"
            "3. Documents Required for Bid: List of required bid submission documents.\n\n"
            "Respond ONLY in valid JSON with these exact keys:\n"
            "{\n"
            '  "scope_of_work": "Point-wise list of physical work items/tasks to be performed, or \'Scope of work is not mentioned in the downloaded tender documents.\'",\n'
            '  "qualification_criteria": "Point-wise list of all pre-qualification / eligibility requirements.",\n'
            '  "documents_required_for_bid": [\n'
            '    "Earnest Money Deposit (EMD) receipt",\n'
            '    "Company registration certificate"\n'
            '  ],\n'
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

            # Check if scope_sow was just returning tender title or empty
            if not scope_sow or tender_title.lower() in scope_sow.lower() and len(scope_sow) < len(tender_title) + 30:
                scope_sow = "Scope of work is not mentioned in the downloaded tender documents."

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

            # AI classification: separate embedded document checklist items from qualification criteria
            try:
                from .pipeline_stage_b import parse_ec_and_dc_from_ai_summary
                clean_qual, extra_docs = parse_ec_and_dc_from_ai_summary(qual_criteria)
                if clean_qual:
                    qual_criteria = clean_qual
                if extra_docs:
                    for d in extra_docs:
                        if d not in bid_docs:
                            bid_docs.append(d)
            except Exception as classify_err:
                print(f"Warning: failed to classify embedded checklist: {classify_err}")

            notes = parsed.get("notes", "Extraction complete.")
            if has_pre_extracted:
                notes = (notes + " (Eligibility and Bid Documents parsed directly from Portal AI Summary).").strip()

            return {
                "scope_of_work": scope_sow,
                "scope_source_documents": source_names,
                "qualification_criteria": qual_criteria or empty_result["qualification_criteria"],
                "qualification_source_documents": ["Portal AI Summary"] if pre_extracted_eligibility else source_names,
                "documents_required_for_bid": bid_docs or empty_result["documents_required_for_bid"],
                "bid_docs_source_documents": ["Portal AI Summary"] if pre_extracted_documents is not None else source_names,
                "extraction_confidence": parsed.get("extraction_confidence", "high" if has_pre_extracted else "medium"),
                "notes": notes,
                "stage_b_status": "success" if scope_sow and "not mentioned" not in scope_sow.lower() else "partial",
            }
    except Exception as e:
        print(f"Extraction error: {e}")

    return empty_result


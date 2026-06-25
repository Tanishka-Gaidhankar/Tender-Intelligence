"""
stage1_filter.py

Implements Stage 1 Cheap Filtering logic:
  - TenderDetail: keyword checks -> LLM Title-Guess -> (Escalation) Scope/Eligibility Scrapes.
  - Tender247: Skip keywords, directly LLM Title-Guess.
"""

import json
import os
import re
from llm_client import call_llm
from tenderdetail_detail_scraper import fetch_tenderdetail_detail

CONFIG_FILE = "tender_rules_settings.json"


def load_filter_rules() -> dict:
    """Loads keyword include/exclude rules from settings file or defaults."""
    rules = {
    "include_keywords": ["structural audit",
    "safety audit",
    "Technical audit",
    "geotechnical investigations",
    "Non-destructive testing",
    "structural design service",
    "certification services",
    "Third party inspection"],
       
    "exclude_keywords": ["building construction only", "electrical works", "supply-only", "housekeeping", "catering", "software", "hardware", "furniture", "vehicle", "security services"]
    }

    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                rules["include_keywords"] = data.get("stage1_include_keywords", rules["include_keywords"])
                rules["exclude_keywords"] = data.get("stage1_exclude_keywords", rules["exclude_keywords"])
        except Exception as e:
            print(f"Error loading filter rules from config: {e}")

    return rules


def evaluate_keywords(text: str, rules: dict) -> str:
    """
    Evaluates keyword matches on the provided text.
    Returns: 'yes' (passed include), 'no' (matched exclude), 'unsure' (no match).
    """
    text_lower = text.lower()
    
    # 1. Check exclude list (word boundary check)
    for kw in rules["exclude_keywords"]:
        pattern = re.compile(rf"\b{re.escape(kw.lower())}\b")
        if pattern.search(text_lower):
            return "no"
            
    # 2. Check include list (word boundary check)
    for kw in rules["include_keywords"]:
        pattern = re.compile(rf"\b{re.escape(kw.lower())}\b")
        if pattern.search(text_lower):
            return "yes"

    return "unsure"


def ai_fit_guess(text_to_evaluate: str, company_scope: str) -> tuple[str, str]:
    """
    Calls the LLM to guess if the text fits within KBP's scope.
    Returns: (fit, rationale) where fit is 'yes', 'no', or 'unsure'.
    """
    system_prompt = (
        "You are an AI assistant helping a Civil Engineering firm filter tender opportunities.\n"
        "Your task is to determine if the given text (a title, scope, or eligibility text) indicates "
        "the tender is a fit for the company's scope of work.\n"
        "Respond ONLY in valid JSON format: {\"fit\": \"yes\" | \"no\" | \"unsure\", \"rationale\": \"short explanation\"}."
    )

    user_prompt = (
        f"Company Scope of Work:\n{company_scope}\n\n"
        f"Tender Details:\n{text_to_evaluate}\n\n"
        "Does this tender match the company's scope? Rate it as 'yes' (definite fit), 'no' (definite mismatch/wrong type of work), "
        "or 'unsure' (title alone is inconclusive, or needs more context)."
    )

    response = call_llm(user_prompt, system_prompt, json_mode=True)
    try:
        data = json.loads(response)
        fit = data.get("fit", "unsure").lower()
        rationale = data.get("rationale", "")
        if fit in ["yes", "no", "unsure"]:
            return fit, rationale
    except Exception as e:
        print(f"Error parsing AI fit guess response: {e}. Raw response: {response}")
    
    return "unsure", "Failed to parse AI response."


def evaluate_tenderdetail(tender: dict, company_scope: str) -> tuple[str, str]:
    """
    Runs the Stage 1 funnel for a TenderDetail listing:
    1. Keyword match on Title + Authority + Location
    2. If inconclusive, LLM Title-Guess
    3. If Title-Guess is unsure, escalate to Scope of Work (Tender Brief) from detail page
    4. If still unsure, check Eligibility criteria text from detail page
    
    Returns: (decision: 'yes' | 'no', rationale: str)
    """
    rules = load_filter_rules()
    
    # Text block for keyword matching
    keywords_text = f"{tender['title']} {tender['authority']} {tender['location']}"
    
    # 1. Keyword check
    kw_decision = evaluate_keywords(keywords_text, rules)
    if kw_decision == "no":
        return "no", "Matched exclude keywords."
    if kw_decision == "yes":
        return "yes", "Matched include keywords."
        
    # 2. AI Title Guess
    ai_decision, ai_rationale = ai_fit_guess(tender["title"], company_scope)
    if ai_decision in ["yes", "no"]:
        return ai_decision, f"AI Title Guess: {ai_decision.upper()} ({ai_rationale})"
        
    # 3. Escalation: Scrape detail page for Scope of Work
    print(f"Escalating: title inconclusive ({ai_rationale}). Fetching detail page for ID {tender['tender_id']} ...")
    detail = fetch_tenderdetail_detail(tender["view_tender_url"])
    if not detail:
        # If detail fetch fails, assume unsure and default to yes to avoid missing opportunities
        return "yes", f"Escalation failed to fetch detail page, passing to Stage 2. Rationale: {ai_rationale}"

    # Copy details to the tender object for downstream pipeline
    tender.update(detail)

    # Check Scope of Work text (Tender Brief + BOQ Items)
    scope_text = detail.get("scope_of_work", "")
    if scope_text:
        ai_scope_decision, ai_scope_rationale = ai_fit_guess(scope_text, company_scope)
        if ai_scope_decision in ["yes", "no"]:
            return ai_scope_decision, f"AI Scope Guess: {ai_scope_decision.upper()} ({ai_scope_rationale})"

    # Check Eligibility Criteria text
    eligibility_text = detail.get("eligibility_criteria", "")
    if eligibility_text and eligibility_text != "Refer to Tender Brief and BOQ Items.":
        ai_elig_decision, ai_elig_rationale = ai_fit_guess(eligibility_text, company_scope)
        if ai_elig_decision in ["yes", "no"]:
            return ai_elig_decision, f"AI Eligibility Guess: {ai_elig_decision.upper()} ({ai_elig_rationale})"

    # If still unsure after all checks, pass to Stage 2
    return "yes", f"Inconclusive at Stage 1, escalated to Stage 2 scoring. Title Rationale: {ai_rationale}"


def evaluate_tender247(tender: dict, company_scope: str) -> tuple[str, str]:
    """
    Runs the Stage 1 funnel for a Tender247 listing:
    1. Directly call LLM Title-Guess (no keyword check since source pre-filters by category).
    
    Returns: (decision: 'yes' | 'no', rationale: str)
    """
    # Check AI Title Guess
    ai_decision, ai_rationale = ai_fit_guess(tender["title"], company_scope)
    if ai_decision == "no":
        return "no", f"AI Title Guess: NO ({ai_rationale})"
    
    # Both 'yes' and 'unsure' are accepted for Stage 2
    if ai_decision == "yes":
        return "yes", f"AI Title Guess: YES ({ai_rationale})"
        
    return "yes", f"AI Title Guess: UNSURE (passed to Stage 2 scoring) ({ai_rationale})"

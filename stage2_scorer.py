"""
stage2_scorer.py

Implements Stage 2 Scoring logic.
Calculates a 0-100 overall score by calling an LLM to evaluate the tender against 
KBP's target settings across 5 weighted dimensions.
"""

import json
from llm_client import call_llm


def calculate_overall_score(scores: dict) -> float:
    """
    Computes overall score from 5 weighted dimensions (max 100):
      - Scope Match (weight: 30) -> score 0 to 10
      - Location Eligibility (weight: 25) -> score 0 to 10
      - Eligibility Clearance (weight: 20) -> score 0 to 10
      - Value Fit (weight: 15) -> score 0 to 10
      - Disqualifier Check (weight: 10) -> score 0 to 10
    """
    weights = {
        "scope_match": 3.0,
        "location_eligibility": 2.5,
        "eligibility_clearance": 2.0,
        "value_fit": 1.5,
        "disqualifier_check": 1.0
    }
    
    overall = 0.0
    for key, weight in weights.items():
        score = float(scores.get(key, 0.0))
        # Clamp score between 0 and 10
        score = max(0.0, min(10.0, score))
        overall += score * weight
        
    return round(overall, 2)


def score_tender(tender: dict, settings: dict) -> dict:
    """
    Calls LLM to score the tender against KBP company profile and rules.
    
    Args:
        tender: Dictionary containing parsed tender details (scope, eligibility, location, value).
        settings: KBP company rules (Company Profile, Scope of Work, Eligible States, Target values, Disqualifiers).
        
    Returns:
        A dictionary containing overall score, detailed rationales, risks, and action.
    """
    system_prompt = (
        "You are an expert AI Civil Engineering consultant analyzer.\n"
        "Your task is to review a tender opportunity and score its alignment with the company's business criteria.\n"
        "You must evaluate and score 5 dimensions on a scale of 0 to 10 (where 10 is perfect fit/no risk, and 0 is complete misfit/critical risk).\n"
        "Respond ONLY in valid JSON format:\n"
        "{\n"
        "  \"scope_match\": 0-10,\n"
        "  \"scope_match_rationale\": \"...\",\n"
        "  \"location_eligibility\": 0-10,\n"
        "  \"location_eligibility_rationale\": \"...\",\n"
        "  \"eligibility_clearance\": 0-10,\n"
        "  \"eligibility_clearance_rationale\": \"...\",\n"
        "  \"value_fit\": 0-10,\n"
        "  \"value_fit_rationale\": \"...\",\n"
        "  \"disqualifier_check\": 0-10, // 10 = no disqualifiers matched, 0 = matches major disqualifiers\n"
        "  \"disqualifier_check_rationale\": \"...\",\n"
        "  \"key_risks\": \"...\",\n"
        "  \"suggested_action\": \"Bid\" | \"Review Manually\" | \"Drop\"\n"
        "}"
    )

    user_prompt = (
        f"--- COMPANY SETTINGS ---\n"
        f"Company Profile:\n{settings.get('company_profile', 'Civil engineering contractor.')}\n"
        f"Scope of Work (What KBP builds):\n{settings.get('scope_of_work', 'Bridges, roads, water supply, RCC structures.')}\n"
        f"Eligible States (Where KBP operates):\n{settings.get('eligible_states', [])}\n"
        f"Target Tender Value Range:\nMin Value: {settings.get('min_tender_value', 'Any')} | Max Value: {settings.get('max_tender_value', 'Any')}\n"
        f"Disqualifiers (Things that rule a tender out):\n{settings.get('disqualifiers', 'Building construction only, electrical-only, supply-only.')}\n\n"
        
        f"--- TENDER DETAILS TO EVALUATE ---\n"
        f"T247/TDR ID: {tender.get('tender_id')}\n"
        f"Title: {tender.get('title')}\n"
        f"Authority: {tender.get('authority')}\n"
        f"Location: {tender.get('location')}\n"
        f"Estimated Value: {tender.get('tender_value') or tender.get('bid_value') or 'Refer document'}\n"
        f"EMD Value: {tender.get('emd')}\n"
        f"Scope of Work / BOQ:\n{tender.get('scope_of_work', 'Not extracted. Check title.')}\n"
        f"Eligibility / Qualification Criteria:\n{tender.get('eligibility_criteria', 'Not extracted. Check title.')}\n\n"
        
        f"Evaluate this tender across the 5 dimensions, rate each 0-10, and provide rationales."
    )

    # Call LLM
    response = call_llm(user_prompt, system_prompt, json_mode=True)
    
    # Parse results
    try:
        results = json.loads(response)
        
        # Extract dimension scores
        scores = {
            "scope_match": results.get("scope_match", 0),
            "location_eligibility": results.get("location_eligibility", 0),
            "eligibility_clearance": results.get("eligibility_clearance", 0),
            "value_fit": results.get("value_fit", 0),
            "disqualifier_check": results.get("disqualifier_check", 0)
        }
        
        # Calculate weighted score
        overall_score = calculate_overall_score(scores)
        
        results["overall_score"] = overall_score
        return results

    except Exception as e:
        print(f"Error parsing Stage 2 AI scorer response: {e}. Raw response: {response}")
        # Default fallback values
        return {
            "overall_score": 0.0,
            "scope_match": 0,
            "scope_match_rationale": f"Scoring parsing error: {e}",
            "location_eligibility": 0,
            "location_eligibility_rationale": "",
            "eligibility_clearance": 0,
            "eligibility_clearance_rationale": "",
            "value_fit": 0,
            "value_fit_rationale": "",
            "disqualifier_check": 0,
            "disqualifier_check_rationale": "",
            "key_risks": "Failed to parse AI output.",
            "suggested_action": "Review Manually"
        }

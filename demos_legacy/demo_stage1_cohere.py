"""
demo_stage1_cohere.py

A demo script to showcase Stage A filtering (Keyword Rules + Cohere API Title-Guess)
on various sample tender titles.
"""

import json
import os
import sys

from tenderlead.ai.stage1_filter import evaluate_keywords, load_filter_rules, ai_fit_guess
from tenderlead.pipeline import load_settings

def run_demo():
    print("==========================================================")
    print("      STAGE A FILTER DEMO — COHERE API INTEGRATION        ")
    print("==========================================================\n")

    from tenderlead.ai.llm_client import load_llm_config
    settings = load_settings()
    llm_config = load_llm_config()
    
    # Assert current LLM provider is Cohere
    print(f"Current LLM Settings:")
    print(f"  Provider:    {llm_config.get('provider')}")
    print(f"  Model:       {llm_config.get('model')}")
    print(f"  API Key Set: {'Yes' if llm_config.get('api_key') else 'No (Will run in mock mode)'}")
    print()
    
    if llm_config.get("provider") != "cohere":
        print("WARNING: Provider is not set to 'cohere' in tender_rules_settings.json.")
        print("To run this demo with Cohere, please set 'provider' to 'cohere' in settings.")
        print()

    company_scope = settings.get("scope_of_work", "")
    print(f"Target Company Scope:")
    print(f"  {company_scope}")
    print("\n----------------------------------------------------------\n")

    # Sample test cases representing real-world domestic tender titles
    test_tenders = [
        # Explicit fits (consultancy, auditing, geotechnical, NDT)
        {
            "id": "T001",
            "title": "Hiring of consultant for conducting structural audit and stability assessment of hospital buildings in Bidar.",
            "authority": "Health And Family Welfare Department",
            "location": "Bidar, Karnataka, India"
        },
        {
            "id": "T002",
            "title": "Geotechnical investigation and soil exploration work for preparation of DPR for rural water supply project.",
            "authority": "Public Health Engineering Department",
            "location": "Nashik, Maharashtra, India"
        },
        {
            "id": "T003",
            "title": "Non-destructive testing (NDT) of concrete structures and quality control auditing of flyover construction.",
            "authority": "National Highways Authority of India",
            "location": "Ahmedabad, Gujarat, India"
        },
        
        # Inconclusive titles (no obvious keywords, but civil engineering scope - will trigger Cohere)
        {
            "id": "T004",
            "title": "Providing third party quality audit services for development of highway road infrastructure works.",
            "authority": "State Road Development Corporation",
            "location": "Hyderabad, Telangana, India"
        },
        
        # Disqualified / Excluded categories
        {
            "id": "T005",
            "title": "Construction of double story residential staff quarters at substation premises.",
            "authority": "State Electricity Board",
            "location": "Kochi, Kerala, India"
        },
        {
            "id": "T006",
            "title": "Supply and installation of modular office furniture and tables.",
            "authority": "Municipal Corporation",
            "location": "Pune, Maharashtra, India"
        },
        {
            "id": "T007",
            "title": "Providing housekeeping and catering services for company guest house.",
            "authority": "Bharat Petroleum Corporation Limited",
            "location": "Bina, Madhya Pradesh, India"
        }
    ]

    rules = load_filter_rules()

    for idx, t in enumerate(test_tenders, start=1):
        print(f"Test Opportunity {idx}: [{t['id']}]")
        print(f"  Title:     {t['title']}")
        print(f"  Authority: {t['authority']} ({t['location']})")
        
        # 1. Keyword check (title + authority + location)
        keywords_text = f"{t['title']} {t['authority']} {t['location']}"
        kw_decision = evaluate_keywords(keywords_text, rules)
        print(f"  -> Keyword match check:  {kw_decision.upper()}")
        
        # 2. AI Title Guess (if keyword check is unsure, or for direct comparison)
        ai_decision, ai_rationale = ai_fit_guess(t['title'], company_scope)
        print(f"  -> Cohere AI fit guess:  {ai_decision.upper()} ({ai_rationale})")
        
        # Overall Stage A decision
        overall_decision = "Shortlisted (Stage B)" if (kw_decision == "yes" or (kw_decision == "unsure" and ai_decision in ["yes", "unsure"])) else "Dropped (Stage A)"
        print(f"  => Final Stage A Action:  {overall_decision.upper()}")
        print("\n----------------------------------------------------------\n")

if __name__ == "__main__":
    run_demo()

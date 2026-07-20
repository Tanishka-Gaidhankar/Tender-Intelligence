"""
llm_client.py

Lightweight wrapper to make requests to OpenAI (Chat Completion) and Anthropic (Messages)
directly via the requests library, bypassing standard SDK dependencies.
"""

import json
import os
import requests

CONFIG_FILE = "tender_rules_settings.json"


def load_llm_config() -> dict:
    """
    Loads LLM configuration from tender_rules_settings.json.
    Falls back to environment variables if settings file does not exist.
    """
    config = {
        "provider": "openai",
        "api_key": None,
        "model": "gpt-4o-mini",
        "temperature": 0.0
    }

    possible_config_paths = [
        "tender_rules_settings.json",
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "tender_rules_settings.json")),
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tender_rules_settings.json"))
    ]

    for cpath in possible_config_paths:
        if os.path.exists(cpath):
            try:
                with open(cpath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    llm_data = data.get("llm", {})
                    config.update(llm_data)
                break
            except Exception as e:
                print(f"Error loading {cpath}: {e}")

    # Environment variables override
    provider = os.getenv("LLM_PROVIDER")
    if provider:
        config["provider"] = provider.lower()

    # Load correct API key depending on provider
    env_api_key = None
    if config["provider"] == "openai":
        env_api_key = os.getenv("OPENAI_API_KEY")
    elif config["provider"] == "anthropic":
        env_api_key = os.getenv("ANTHROPIC_API_KEY")
    elif config["provider"] == "cohere":
        env_api_key = os.getenv("COHERE_API_KEY")
        # Set default model for Cohere if it was left as OpenAI default
        if config["model"] == "gpt-4o-mini" or config["model"] == "command-r-plus":
            config["model"] = "command-r-plus-08-2024"

    if env_api_key:
        config["api_key"] = env_api_key
        
    model = os.getenv("LLM_MODEL")
    if model:
        config["model"] = model

    return config


def call_llm(user_prompt: str, system_prompt: str = "", json_mode: bool = False) -> str:
    """
    Calls the configured LLM API.
    
    Args:
        user_prompt: The prompt text for the user.
        system_prompt: System instructions.
        json_mode: Attempt to enforce JSON response formatting (supported on OpenAI and Cohere).
        
    Returns:
        The response content as a string, or empty string on failure.
    """
    config = load_llm_config()
    provider = config["provider"]
    api_key = config["api_key"]
    model = config["model"]
    temp = config["temperature"]

    if not api_key:
        # Mock mode if no key is configured
        print("WARNING: No API key configured. Returning mock/dry-run response.")
        if "fit" in user_prompt.lower() or "guess" in user_prompt.lower() or "unsure" in user_prompt.lower():
            # Mock title-guess
            if any(k in user_prompt.lower() for k in ["geotechnical", "consultancy", "survey", "audit", "testing"]):
                return '{"fit": "yes", "rationale": "Mock check: matches consultancy/investigation keywords"}'
            else:
                return '{"fit": "no", "rationale": "Mock check: unrelated topic"}'
        # Return generic JSON if JSON mode
        if json_mode:
            return '{"score": 50, "rationale": "Mock API score due to missing API keys"}'
        return "Mock response: API keys missing."

    try:
        if provider == "openai":
            url = "https://api.openai.com/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "temperature": temp
            }
            if json_mode:
                payload["response_format"] = {"type": "json_object"}

            res = requests.post(url, headers=headers, json=payload, timeout=30)
            res.raise_for_status()
            res_json = res.json()
            return res_json["choices"][0]["message"]["content"].strip()

        elif provider == "anthropic":
            url = "https://api.anthropic.com/v1/messages"
            headers = {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            }
            payload = {
                "model": model,
                "system": system_prompt,
                "messages": [
                    {"role": "user", "content": user_prompt}
                ],
                "max_tokens": 4000,
                "temperature": temp
            }
            res = requests.post(url, headers=headers, json=payload, timeout=30)
            res.raise_for_status()
            res_json = res.json()
            return res_json["content"][0]["text"].strip()

        elif provider == "cohere":
            url = "https://api.cohere.ai/v1/chat"
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": model if model else "command-r-plus",
                "message": user_prompt,
                "preamble": system_prompt,
                "temperature": temp
            }
            if json_mode:
                payload["response_format"] = {"type": "json_object"}

            res = requests.post(url, headers=headers, json=payload, timeout=30)
            res.raise_for_status()
            res_json = res.json()
            return res_json["text"].strip()

        else:
            print(f"Unknown LLM provider: {provider}")
            return ""

    except Exception as e:
        print(f"LLM call failed: {e}")
        return ""


def generate_tender_screening_summary_and_score(tender: dict) -> dict:
    """
    Uses Cohere API (or loaded LLM provider) to generate a primary screening summary and AI fit score placeholder.
    Falls back gracefully if the API is offline or returns an error.
    """
    title = tender.get("title") or "Unknown Tender"
    authority = tender.get("authority") or "Unknown Authority"
    location = tender.get("location") or "Not Specified"
    value = tender.get("value") or tender.get("bid_value") or tender.get("tender_value") or "Not Specified"
    
    system_prompt = (
        "You are an AI Tender Analyst for KBP Civil Engineering Services.\n"
        "Evaluate the following tender opportunity based on its title, issuing authority, location, and estimated value.\n"
        "Generate a concise 1-2 sentence primary screening summary and an initial AI fit score from 0 to 100.\n"
        "Respond strictly in valid JSON:\n"
        "{\n"
        '  "ai_score": 75,\n'
        '  "summary": "Short 1-2 sentence summary of the tender scope and relevance to civil engineering.",\n'
        '  "status": "Good Match" | "May be" | "No Match"\n'
        "}"
    )

    user_prompt = (
        f"Tender Title: {title}\n"
        f"Authority: {authority}\n"
        f"Location: {location}\n"
        f"Value: {value}\n\n"
        "Generate JSON primary screening summary and AI score."
    )

    try:
        response = call_llm(user_prompt, system_prompt, json_mode=True)
        if response and response.strip():
            import re
            json_str = response.strip()
            if not json_str.startswith("{"):
                match = re.search(r"\{.*\}", json_str, re.DOTALL)
                if match:
                    json_str = match.group(0)
            parsed = json.loads(json_str)
            return {
                "ai_score": int(parsed.get("ai_score", 70)),
                "summary": str(parsed.get("summary", f"Primary screening completed for {title[:60]}")),
                "status": str(parsed.get("status", "Good Match"))
            }
    except Exception as e:
        print(f"[LLM Client Warning] Cohere API summary generation error: {e}")

    # Heuristic fallback if Cohere API call fails or key is missing
    title_lower = title.lower()
    positive_keywords = ["audit", "testing", "investigation", "consultancy", "inspection", "survey", "structural", "bridge", "road", "water", "civil", "construction"]
    negative_keywords = ["catering", "housekeeping", "software", "furniture", "vehicle", "security"]

    is_pos = any(k in title_lower for k in positive_keywords)
    is_neg = any(k in title_lower for k in negative_keywords)

    if is_neg:
        score = 25
        status = "No Match"
        summary = f"Primary screening: Low relevance. Title contains excluded category terms. (Location: {location})"
    elif is_pos:
        score = 80
        status = "Good Match"
        summary = f"Primary screening: Matches core civil engineering scope for {authority} in {location}."
    else:
        score = 60
        status = "May be"
        summary = f"Primary screening: Candidate opportunity in {location}. Requires secondary document review."

    return {
        "ai_score": score,
        "summary": summary,
        "status": status
    }

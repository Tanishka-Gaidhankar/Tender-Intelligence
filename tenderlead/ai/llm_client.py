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
    """Loads LLM settings from tender_rules_settings.json, .env file, or env vars."""
    # Load .env file if present
    env_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ.setdefault(k.strip(), v.strip().strip("'\""))
        except Exception as e:
            print(f"Error loading .env file: {e}")

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
    p_lower = config["provider"].lower()
    if p_lower == "openai":
        env_api_key = os.getenv("OPENAI_API_KEY")
    elif p_lower == "anthropic":
        env_api_key = os.getenv("ANTHROPIC_API_KEY")
    elif p_lower == "cohere":
        env_api_key = os.getenv("COHERE_API_KEY")
        if config["model"] in ("gpt-4o-mini", "command-r-plus"):
            config["model"] = "command-r-plus-08-2024"
    elif p_lower == "groq":
        env_api_key = os.getenv("GROQ_API_KEY")
        if config["model"] in ("gpt-4o-mini", "command-r-plus-08-2024"):
            config["model"] = "llama-3.3-70b-versatile"
    
    elif p_lower in ("gemini", "google"):
        env_api_key = os.getenv("GEMINI_API_KEY")
        if config["model"] in ("gpt-4o-mini", "command-r-plus-08-2024"):
            config["model"] = "gemini-1.5-flash"
    elif p_lower == "ollama":
        config["api_key"] = "ollama_local"
        if config["model"] in ("gpt-4o-mini", "command-r-plus-08-2024"):
            config["model"] = "llama3.1"

    if env_api_key:
        config["api_key"] = env_api_key
        
    config["groq_api_key"] = os.getenv("GROQ_API_KEY") or config.get("groq_api_key")

    model = os.getenv("LLM_MODEL")
    if model:
        config["model"] = model

    return config


def call_groq_api(api_key: str, user_prompt: str, system_prompt: str = "", model: str = "llama-3.3-70b-versatile", json_mode: bool = False) -> str:
    """Helper to directly call Groq API as a backup LLM provider."""
    url = "https://api.groq.com/openai/v1/chat/completions"
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
        "temperature": 0.0
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    res = requests.post(url, headers=headers, json=payload, timeout=30)
    res.raise_for_status()
    res_json = res.json()
    return res_json["choices"][0]["message"]["content"].strip()


def call_llm(user_prompt: str, system_prompt: str = "", json_mode: bool = False) -> str:
    """
    Calls the configured LLM API (OpenAI, Anthropic, Cohere, Groq, Grok, Gemini, Ollama).
    Automatically falls back to Groq API if primary provider fails or rate-limits.
    """
    config = load_llm_config()
    provider = config["provider"].lower()
    api_key = config["api_key"]
    model = config["model"]
    temp = config["temperature"]
    groq_backup_key = config.get("groq_api_key") or os.getenv("GROQ_API_KEY")

    if not api_key:
        if groq_backup_key and provider != "groq":
            print("[LLM Client] Primary API key missing. Using Groq API backup...")
            try:
                return call_groq_api(groq_backup_key, user_prompt, system_prompt, json_mode=json_mode)
            except Exception as ge:
                print(f"[LLM Client Warning] Groq backup failed: {ge}")

        # Mock mode if no keys are configured
        print("WARNING: No API key configured. Returning mock/dry-run response.")
        if "fit" in user_prompt.lower() or "guess" in user_prompt.lower() or "unsure" in user_prompt.lower():
            if any(k in user_prompt.lower() for k in ["geotechnical", "consultancy", "survey", "audit", "testing"]):
                return '{"fit": "yes", "rationale": "Mock check: matches consultancy/investigation keywords"}'
            else:
                return '{"fit": "no", "rationale": "Mock check: unrelated topic"}'
        if json_mode:
            return '{"score": 50, "rationale": "Mock API score due to missing API keys"}'
        return "Mock response: API keys missing."

    try:
        if provider == "ollama":
            url = os.getenv("OLLAMA_HOST", "http://localhost:11434/api/generate")
            payload = {
                "model": model if model else "llama3.1",
                "prompt": f"{system_prompt}\n\n{user_prompt}",
                "stream": False,
                "options": {
                    "temperature": temp
                }
            }
            if json_mode:
                payload["format"] = "json"

            res = requests.post(url, json=payload, timeout=180)
            res.raise_for_status()
            res_json = res.json()
            return res_json.get("response", "").strip()

        elif provider == "openai":
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

        elif provider in ("groq", "grok"):
            target_url = "https://api.groq.com/openai/v1/chat/completions" if provider == "groq" else "https://api.x.ai/v1/chat/completions"
            target_model = model if model else ("llama-3.3-70b-versatile" if provider == "groq" else "grok-beta")
            return call_groq_api(api_key, user_prompt, system_prompt, model=target_model, json_mode=json_mode)

        elif provider in ("gemini", "google"):
            target_model = model if model else "gemini-1.5-flash"
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{target_model}:generateContent?key={api_key}"
            headers = {"Content-Type": "application/json"}
            payload = {
                "contents": [
                    {
                        "parts": [
                            {"text": f"{system_prompt}\n\n{user_prompt}"}
                        ]
                    }
                ]
            }
            if json_mode:
                payload["generationConfig"] = {"responseMimeType": "application/json"}

            res = requests.post(url, headers=headers, json=payload, timeout=30)
            res.raise_for_status()
            res_json = res.json()
            candidates = res_json.get("candidates", [])
            if candidates:
                return candidates[0]["content"]["parts"][0]["text"].strip()
            return ""

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
        print(f"Primary LLM provider ({provider}) call failed: {e}")
        if groq_backup_key and provider != "groq":
            print("[LLM Client] Falling back to Groq API (Llama 3.3 70B) backup...")
            try:
                return call_groq_api(groq_backup_key, user_prompt, system_prompt, json_mode=json_mode)
            except Exception as groq_err:
                print(f"[LLM Client Warning] Groq backup call failed: {groq_err}")
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
        "You are a Senior AI Procurement Analyst for KBP Civil Engineering Services.\n"
        "Evaluate the tender opportunity against KBP's capabilities (Structural Audit, NDT Testing, Geotechnical Investigation, Quality Inspection, Civil Consultancy).\n"
        "Generate a detailed Chain-of-Thought summary explaining:\n"
        "1. Scope Summary (1 sentence)\n"
        "2. Key Matched/Mismatched Keywords\n"
        "3. Reasoning for the AI Score\n\n"
        "Respond strictly in valid JSON:\n"
        "{\n"
        '  "ai_score": 85,\n'
        '  "summary": "Scope: Structural audit of overhead tanks.\\nMatched Keywords: structural audit, NDT testing.\\nScore Rationale: High alignment with KBP core services in Rajasthan.",\n'
        '  "status": "Good Match" | "May be" | "No Match"\n'
        "}"
    )

    user_prompt = (
        f"Tender Title: {title}\n"
        f"Authority: {authority}\n"
        f"Location: {location}\n"
        f"Value: {value}\n\n"
        "Generate JSON primary screening summary with explicit Chain-of-Thought score rationale and matched keywords."
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
                "summary": str(parsed.get("summary", f"Scope: {title[:60]}\\nReasoning: Evaluated against civil engineering capabilities.")),
                "status": str(parsed.get("status", "Good Match"))
            }
    except Exception as e:
        print(f"[LLM Client Warning] Summary generation error: {e}")

    # Heuristic fallback if API call fails
    title_lower = title.lower()
    positive_keywords = ["audit", "testing", "investigation", "consultancy", "inspection", "survey", "structural", "bridge", "road", "water", "civil", "construction"]
    negative_keywords = ["catering", "housekeeping", "software", "furniture", "vehicle", "security"]

    matched_pos = [k for k in positive_keywords if k in title_lower]
    matched_neg = [k for k in negative_keywords if k in title_lower]

    if matched_neg:
        score = 25
        status = "No Match"
        summary = (
            f"Scope: {title[:80]}\n"
            f"Excluded Keywords Matched: {', '.join(matched_neg)}\n"
            f"Score Rationale: Disqualified (Score: 25/100) due to presence of excluded category terms."
        )
    elif matched_pos:
        score = 85
        status = "Good Match"
        summary = (
            f"Scope: {title[:80]}\n"
            f"Matched Keywords: {', '.join(matched_pos)}\n"
            f"Score Rationale: High Fit (Score: 85/100) — Matches core civil engineering & consultancy capabilities for {authority} in {location}."
        )
    else:
        score = 60
        status = "May be"
        summary = (
            f"Scope: {title[:80]}\n"
            f"Matched Keywords: None (General civil scope)\n"
            f"Score Rationale: Moderate Fit (Score: 60/100) — General opportunity in {location}. Requires secondary document review."
        )

    return {
        "ai_score": score,
        "summary": summary,
        "status": status
    }

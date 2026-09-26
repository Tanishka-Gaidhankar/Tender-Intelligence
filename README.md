# 🏢 Tender Intelligence System

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-3776AB.svg?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Playwright](https://img.shields.io/badge/Playwright-Automated-2EAD33.svg?style=flat-square&logo=playwright&logoColor=white)](https://playwright.dev/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](LICENSE)

An intelligent, multi-stage automated tender intake, filtering, AI scoring, and ERPNext integration pipeline tailored for civil engineering and infrastructure firms.

---

## 🎯 Overview

**Tender Intelligence** automates the end-to-end lifecycle of discovering, filtering, analyzing, and scoring daily tender opportunities received from tender digest aggregators like **TenderDetail** and **Tender247** (Indian Tender).

Instead of manually sifting through hundreds of daily notification emails and downloading heavy PDF tender documents, **Tender Intelligence** uses a cost-effective **Three-Stage Funnel** to score candidates against actual company capabilities and surface only high-confidence leads.

---

## 🏗️ Architecture: The Three-Stage Funnel

```
┌────────────────────────────────────────────────────────────────────────┐
│                        Scraping Digests Intake                         │
│                 (TenderDetail & Tender247 scrapping)                   │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ Stage 1: Lightweight Title & Keyword Filtering                         │
│ • Rapid keyword matching on title, authority, and location             │
│ • Title-level AI category estimation for borderline cases              │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ Passed Tenders
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ Stage 2: Deep Detail Scraping & Weighted AI Scoring                     │
│ • Scrapes Scope of Work & Eligibility Criteria                         │
│ • Evaluates fit across 5 weighted dimensions (0-100 score)             │
│ • Generates written risk analysis & rationale                          │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ Score >= Threshold (e.g. 70)
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ Stage 3: ERPNext Lead Creation & Web Dashboard                          │
│ • Stored directly in ERPNext "Tender Lead" doctype                     │
│ • Interactive Web UI dashboard with live logs & document links         │
│ • Stage B Document Collector & Page-level Citation Extractor           │
└────────────────────────────────────────────────────────────────────────┘
```

### 🧠 Stage 2 Weighted Scoring Model (0-100)

| Dimension | Weight | Description |
| :--- | :---: | :--- |
| **Scope Match** | **30** | Alignment with company core competencies (e.g., Structural Audit, NDT, Geotechnical) |
| **Location Fit** | **25** | Target geographic operating states/regions |
| **Eligibility Clearance** | **20** | Technical qualification and past experience capacity |
| **Value Range** | **15** | Estimated contract value fit (e.g., ₹2 Lakhs – ₹2 Crores) |
| **Disqualifiers** | **10** | Absence of strictly excluded work types (e.g., supply-only, catering) |

---

## ✨ Key Features

- **Automated Scrapers**:
  - **TenderDetail Scraper**: Pure HTTP GET scraper parsing listing tables without browser overhead.
  - **Tender247 Scraper**: Playwright-powered scraper with `storage_state` session persistence and automatic empty-state retry logic.
- **Flexible LLM Provider Support**:
  - Out-of-the-box integration with **Groq**, **Cohere**, **Gemini**, **OpenAI**, **Anthropic**, and local **Ollama** models (`llama3.1`, `qwen2.5`).
- **Stage B Document Intelligence**:
  - Extracts text from PDFs (PyMuPDF `fitz` & `pdfplumber`), DOCX, and XLSX BOQ spreadsheets.
  - Extracts page-level document citations (`Page X`) for Scope, Eligibility Criteria, and Checklist items.
- **Web UI & Dashboard**:
  - Built with Vanilla JS & Python server (`frontend/server.py`).
  - Provides real-time log monitoring via WebSockets, score threshold sliders, and manual pipeline triggers.
- **ERPNext Integration**:
  - Operates as a native ERPNext app with `Raw Tender Feed`, `Tender Lead`, `Tender Rules`, and `Tender Settings` doctypes.

---

## 📁 Repository Structure

```
.
├── agent.py                            # Main CLI orchestrator & agent runner
├── deploy_to_cloud.sh                  # Cloud deployment helper script
├── pyproject.toml                      # Project metadata & build specs
├── setup.py                            # Package installer script
├── stage2_scorer.py                    # Independent Stage 2 scoring module
├── tender_rules_settings.json          # Default configuration & filter rules
├── tender_intelligence_project_context.md # In-depth project context
│
├── frontend/                           # Interactive Web Dashboard
│   ├── app.js                          # Dashboard logic & WS handling
│   ├── index.html                      # UI layout
│   ├── server.py                       # HTTP & Socket server
│   └── style.css                       # Modern dark-mode styling
│
├── tenderlead/                         # Core Python Package
│   ├── ai/                             # LLM Client & Stage 1/2 prompts
│   │   ├── llm_client.py               # Unified LLM provider switcher
│   │   ├── stage1_filter.py            # Stage 1 keyword & AI title filter
│   │   └── stage2_scorer.py            # Stage 2 multi-criteria scorer
│   ├── scrapers/                       # Portal Scrapers
│   │   ├── tender247_scraper.py        # Tender247 dashboard scraper
│   │   ├── tender247_session.py        # Playwright session manager
│   │   └── tenderdetail_scraper.py     # TenderDetail digest scraper
│   ├── stage_b/                        # Document Processing & Citations
│   │   ├── document_classifier.py     # Document type classifier
│   │   ├── document_collector.py      # PDF/DOCX/BOQ collector
│   │   └── document_extractor.py      # Page citation extractor
│   ├── email_reader.py                 # Gmail IMAP digest reader
│   ├── local_daemon.py                 # Background execution daemon
│   └── pipeline.py                     # Master pipeline workflow
│
└── demos_legacy/                       # Example scripts & legacy utilities
```

---

## 🚀 Quickstart Guide

### 1. Prerequisites
- Python 3.8 or higher
- Node.js & npm (for Playwright browser dependencies if scraping Tender247)

### 2. Installation

Clone the repository and install dependencies:

```bash
git clone https://github.com/Tanishka-Gaidhankar/Tender-Intelligence.git
cd Tender-Intelligence

# Create a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install requirements
pip install -r requirements.txt

# Install Playwright browser binaries
playwright install chromium
```

### 3. Environment Configuration

Copy the example environment file and configure your API keys and credentials:

```bash
cp .env.example .env
```

Edit `.env` with your preferred AI provider key (`GROQ_API_KEY`, `COHERE_API_KEY`, `GEMINI_API_KEY`, etc.):

```env
GROQ_API_KEY=your_groq_api_key_here
LLM_PROVIDER=groq
LLM_MODEL=llama-3.3-70b-versatile
```

---

## 🖥️ Running the Application

### Option A: Web Dashboard & Server
Launch the interactive web dashboard to monitor and trigger pipelines:

```bash
python3 frontend/server.py
```
Open your browser at `http://localhost:5000`.

### Option B: CLI Pipeline Agent
Run the main intake and processing pipeline directly:

```bash
python3 agent.py
```

### Option C: Background Local Daemon
Run as a continuous background service that runs on schedule:

```bash
python3 -m tenderlead.local_daemon
```

---

## 🛡️ License

Distributed under the MIT License. See [`LICENSE`](LICENSE) for more information.

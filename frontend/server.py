import http.server
import socketserver
import json
import sqlite3
import os
import urllib.parse
import subprocess
import threading
import sys
import re
import time
from datetime import datetime

# Add project root to sys.path to enable importing tenderlead modules
parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

PORT = 8080
DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tender_intelligence.db"))
SETTINGS_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tender_rules_settings.json"))

# Global scraper execution state
is_scraping = False
scrape_lock = threading.Lock()

class TenderDashboardAPIHandler(http.server.SimpleHTTPRequestHandler):
    def translate_path(self, path):
        # Always serve static files from the frontend directory
        frontend_dir = os.path.abspath(os.path.dirname(__file__))
        
        # Parse the path to avoid directory traversal
        parsed_url = urllib.parse.urlparse(path)
        clean_path = parsed_url.path.lstrip('/')
        
        if not clean_path or clean_path == '/':
            clean_path = 'index.html'
            
        full_path = os.path.join(frontend_dir, clean_path)
        return full_path

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def do_GET(self):
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path

        if path == "/api/tenders":
            self.get_tenders()
        elif path == "/api/stats":
            self.get_stats()
        elif path == "/api/rules":
            self.get_rules()
        elif path == "/api/trigger-scrape":
            self.trigger_scrape()
        elif path == "/api/scrape-status":
            self.get_scrape_status()
        elif path == "/api/scrape-log":
            self.get_scrape_log()
        elif path == "/api/uploaded-tenders":
            self.get_uploaded_tenders()
        else:
            # Fallback to standard static file serving
            super().do_GET()

    def do_POST(self):
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path

        if path == "/api/upload-document":
            self.upload_document()
        elif path == "/api/uploaded-tenders/update-status":
            self.update_uploaded_tender_status()
        elif path == "/api/uploaded-tenders/remove":
            self.remove_uploaded_tender()
        else:
            self.send_error_json(404, "Endpoint not found.")

    def trigger_scrape(self):
        global is_scraping
        with scrape_lock:
            if is_scraping:
                self.send_json_response({"status": "running", "message": "Scraper is already running."})
                return
            is_scraping = True

        # Run pipeline in a background thread to prevent HTTP request hanging
        t = threading.Thread(target=self.run_pipeline_thread)
        t.daemon = True
        t.start()
        self.send_json_response({"status": "started", "message": "Scraper triggered successfully."})

    def get_python_binary(self):
        parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        venv_python = os.path.join(parent_dir, ".venv", "bin", "python3")
        if os.path.exists(venv_python):
            return venv_python
        venv_python_win = os.path.join(parent_dir, ".venv", "Scripts", "python.exe")
        if os.path.exists(venv_python_win):
            return venv_python_win
        return sys.executable

    def run_pipeline_thread(self):
        global is_scraping
        log_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "scrape.log"))
        
        # Clear existing log
        try:
            with open(log_path, "w", encoding="utf-8") as f:
                f.write("=== Scraper Pipeline Started ===\n")
        except Exception as e:
            print(f"Error clearing log: {e}")

        try:
            pipeline_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tenderlead", "pipeline.py"))
            python_bin = self.get_python_binary()
            project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

            def run_stage(args_list, stage_label):
                """Runs one pipeline stage as subprocess, streaming output to log."""
                print(f"Triggering: {python_bin} -m tenderlead.pipeline {' '.join(args_list)}")
                proc = subprocess.Popen(
                    [python_bin, "-m", "tenderlead.pipeline"] + args_list,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    cwd=project_root
                )
                for line in iter(proc.stdout.readline, ""):
                    sys.stdout.write(line)
                    sys.stdout.flush()
                    try:
                        with open(log_path, "a", encoding="utf-8") as f:
                            f.write(line)
                    except Exception as e:
                        print(f"Error writing log: {e}")
                proc.stdout.close()
                proc.wait()
                return proc.returncode

            # Stage 1: Direct intake (scrape both Tender247 and TenderDetail websites)
            run_stage(["--intake-direct"], "Intake")
            # Stage 2: Keyword filter
            run_stage(["--stage1"], "Stage1 Filter")
            # Stage 3: AI scoring and lead promotion
            process = subprocess.Popen(
                [python_bin, "-m", "tenderlead.pipeline", "--stage2"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=project_root
            )
            
            # Read line by line and write to file
            for line in iter(process.stdout.readline, ""):
                sys.stdout.write(line)
                sys.stdout.flush()
                try:
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(line)
                except Exception as e:
                    print(f"Error writing to scrape.log: {e}")
                    
            process.stdout.close()
            process.wait()

            # Automatically sync results into ERPNext and refresh screening dashboard
            try:
                print("Triggering automatic synchronization to ERPNext...")
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write("\nSyncing scraped tenders to ERPNext...\n")
                
                # 1. Sync SQLite to ERPNext
                sync_proc = subprocess.Popen(
                    ["bench", "--site", "kbpcivil.in", "execute", "tenderlead.sync_tenders.sync"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    cwd="/home/kbp/kbpcivil"
                )
                for line in iter(sync_proc.stdout.readline, ""):
                    sys.stdout.write(line)
                    sys.stdout.flush()
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(line)
                sync_proc.stdout.close()
                sync_proc.wait()

                # 2. Refresh ERPNext Screening DocType
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write("Refreshing ERPNext Screening Dashboard...\n")
                refresh_proc = subprocess.Popen(
                    ["bench", "--site", "kbpcivil.in", "execute", "tenderlead.api.refresh_dashboard"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    cwd="/home/kbp/kbpcivil"
                )
                for line in iter(refresh_proc.stdout.readline, ""):
                    sys.stdout.write(line)
                    sys.stdout.flush()
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(line)
                refresh_proc.stdout.close()
                refresh_proc.wait()
                
                # 3. Sync to Cloud ERPNext (demokbp.m.frappe.cloud)
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write("Syncing scraped tenders to Cloud ERPNext (demokbp.m.frappe.cloud)...\n")
                
                workspace_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
                cloud_sync_proc = subprocess.Popen(
                    [python_bin, "-m", "tenderlead.cloud_sync"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    cwd=workspace_dir
                )
                for line in iter(cloud_sync_proc.stdout.readline, ""):
                    sys.stdout.write(line)
                    sys.stdout.flush()
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(line)
                cloud_sync_proc.stdout.close()
                cloud_sync_proc.wait()
                
                print("ERPNext local and cloud synchronization complete.")
            except Exception as sync_err:
                print(f"Failed to sync to ERPNext: {sync_err}")
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(f"Warning: Failed to sync to ERPNext: {sync_err}\n")
            
            try:
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write("\n=== Scraper Pipeline Completed ===\n")
            except:
                pass
                
        except Exception as e:
            err_msg = f"Error executing scraper: {str(e)}\n"
            print(err_msg)
            try:
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(err_msg)
            except:
                pass
        finally:
            with scrape_lock:
                is_scraping = False

    def get_scrape_status(self):
        global is_scraping
        self.send_json_response({"running": is_scraping})

    def get_scrape_log(self):
        log_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "scrape.log"))
        if not os.path.exists(log_path):
            self.send_json_response({"log": "No log available."})
            return
            
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                content = f.read()
            self.send_json_response({"log": content})
        except Exception as e:
            self.send_error_json(500, f"Error reading log file: {str(e)}")

    def get_tenders(self):
        if not os.path.exists(DB_PATH):
            self.send_error_json(404, "Database file not found. Run pipeline first.")
            return

        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # Fetch all raw tenders, and join with tender_leads to get eligibility & scope details if promoted
            cursor.execute("""
                SELECT 
                    r.tender_id, 
                    r.source, 
                    r.title, 
                    r.authority, 
                    r.location, 
                    r.value, 
                    r.emd, 
                    r.due_date, 
                    r.status, 
                    r.ai_score as raw_ai_score, 
                    r.ai_rationale as raw_ai_rationale, 
                    r.link, 
                    r.created_at,
                    l.tender_id as lead_id,
                    l.eligibility,
                    l.scope_of_work,
                    l.ai_score as lead_ai_score,
                    l.ai_rationale as lead_ai_rationale
                FROM raw_tender_feed r
                LEFT JOIN tender_leads l ON r.tender_id = l.tender_id
                ORDER BY r.created_at DESC
            """)
            rows = cursor.fetchall()

            # Check if arrival_date column exists; add if missing
            cursor.execute("PRAGMA table_info(raw_tender_feed)")
            cols = [c[1] for c in cursor.fetchall()]
            if "arrival_date" not in cols:
                cursor.execute("ALTER TABLE raw_tender_feed ADD COLUMN arrival_date TEXT")
                conn.commit()

            conn.close()

            tenders = []
            STATUS_NORM = {
                "lead_created":   "good_match",
                "good_match":     "good_match",
                "rules_rejected": "no_match",
                "rejected_ai":    "no_match",
                "no_match":       "no_match",
                "ai_processing":  "unsure",
                "rules_passed":   "unsure",
                "unsure":         "unsure",
                "new":            "unsure",
            }

            from tenderlead.ai.llm_client import generate_tender_screening_summary_and_score

            for r in rows:
                is_lead = r["lead_id"] is not None
                raw_status = "good_match" if is_lead else r["status"]
                status = STATUS_NORM.get(raw_status, raw_status)
                raw_created = r["created_at"]
                arrival_date = raw_created.split('T')[0] if raw_created and 'T' in raw_created else (raw_created or 'N/A')

                score_val = r["lead_ai_score"] if r["lead_ai_score"] is not None else r["raw_ai_score"]
                summary_val = r["lead_ai_rationale"] if r["lead_ai_rationale"] is not None else r["raw_ai_rationale"]

                if score_val is None or not summary_val:
                    eval_placeholder = generate_tender_screening_summary_and_score({
                        "title": r["title"],
                        "authority": r["authority"],
                        "location": r["location"],
                        "value": r["value"]
                    })
                    if score_val is None:
                        score_val = eval_placeholder.get("ai_score", 70)
                    if not summary_val:
                        summary_val = eval_placeholder.get("summary", "")

                tenders.append({
                    "tender_id": r["tender_id"],
                    "source": r["source"],
                    "title": r["title"],
                    "authority": r["authority"],
                    "location": r["location"] or "Not Specified",
                    "value": r["value"],
                    "emd": r["emd"],
                    "due_date": r["due_date"],
                    "status": status,
                    "ai_score": score_val,
                    "ai_rationale": summary_val,
                    "link": r["link"],
                    "created_at": r["created_at"],
                    "arrival_date": arrival_date,
                    "eligibility": r["eligibility"],
                    "scope_of_work": r["scope_of_work"]
                })

            self.send_json_response(tenders)
        except Exception as e:
            self.send_error_json(500, f"Database error: {str(e)}")

    def get_stats(self):
        if not os.path.exists(DB_PATH):
            self.send_json_response({
                "total_intake": 0,
                "stage1_passed": 0,
                "stage1_rejected": 0,
                "stage2_leads": 0,
                "stage2_rejected": 0,
                "processing": 0
            })
            return

        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            # Count total raw intake
            cursor.execute("SELECT count(*) FROM raw_tender_feed")
            total_intake = cursor.fetchone()[0]

            # Count Stage A Passed (unsure or above)
            cursor.execute("""
                SELECT count(DISTINCT r.tender_id) FROM raw_tender_feed r
                LEFT JOIN tender_leads l ON r.tender_id = l.tender_id
                WHERE r.status IN ('unsure', 'good_match', 'no_match',
                                   'rules_passed', 'lead_created', 'rejected_ai', 'ai_processing')
                   OR l.tender_id IS NOT NULL
            """)
            stage1_passed = cursor.fetchone()[0]

            # Count rejected (Stage A keyword fail)
            cursor.execute("SELECT count(*) FROM raw_tender_feed WHERE status IN ('no_match', 'rules_rejected')")
            stage1_rejected = cursor.fetchone()[0]

            # Count Good Matches (score >= 70 — in tender_leads)
            cursor.execute("SELECT count(*) FROM tender_leads")
            stage2_leads = cursor.fetchone()[0]

            # Count AI-scored No Matches (score < 70)
            cursor.execute("SELECT count(*) FROM raw_tender_feed WHERE status IN ('no_match', 'rejected_ai') AND ai_score IS NOT NULL")
            stage2_rejected = cursor.fetchone()[0]

            # Count still Unsure (pending scoring)
            cursor.execute("SELECT count(*) FROM raw_tender_feed WHERE status IN ('new', 'unsure', 'ai_processing')")
            processing = cursor.fetchone()[0]

            conn.close()

            self.send_json_response({
                "total_intake": total_intake,
                "stage1_passed": stage1_passed,
                "stage1_rejected": stage1_rejected,
                "stage2_leads": stage2_leads,
                "stage2_rejected": stage2_rejected,
                "processing": processing
            })
        except Exception as e:
            self.send_error_json(500, f"Database error: {str(e)}")

    def get_rules(self):
        if not os.path.exists(SETTINGS_PATH):
            self.send_error_json(404, "Settings file not found.")
            return

        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                rules = json.load(f)
                
            # Strip API key for security
            if "llm" in rules and "api_key" in rules["llm"]:
                rules["llm"] = {**rules["llm"], "api_key": "********"}

            self.send_json_response(rules)
        except Exception as e:
            self.send_error_json(500, f"Error reading settings: {str(e)}")

    def parse_multipart(self):
        content_type = self.headers.get('Content-Type', '')
        if 'multipart/form-data' not in content_type:
            return None, None

        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)

        from email.parser import BytesParser
        from email.policy import default
        import re

        headers_bytes = b"Content-Type: " + content_type.encode('utf-8') + b"\r\n\r\n"
        msg = BytesParser(policy=default).parsebytes(headers_bytes + body)

        title = ""
        files = []

        if msg.is_multipart():
            for part in msg.iter_parts():
                disposition = part.get("Content-Disposition", "")
                name_match = re.search(r'name="([^"]+)"', disposition)
                if not name_match:
                    continue
                field_name = name_match.group(1)

                if field_name == "title":
                    payload = part.get_payload(decode=True)
                    if payload:
                        title = payload.decode('utf-8', errors='ignore').strip()
                elif field_name == "document":
                    file_name_match = re.search(r'filename="([^"]+)"', disposition)
                    f_name = ""
                    if file_name_match:
                        f_name = file_name_match.group(1)
                    f_data = part.get_payload(decode=True)
                    if f_name and f_data:
                        files.append((f_name, f_data))
        
        return title, files

    def upload_document(self):
        try:
            title, files = self.parse_multipart()
            if not title or not files:
                self.send_error_json(400, "Missing title, files, or invalid form data.")
                return

            uploads_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
            os.makedirs(uploads_dir, exist_ok=True)

            file_names = []
            extracted_text_blocks = []

            import document_extractor_helper
            # Load LlamaParse key from settings file
            llama_key = None
            settings_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tender_rules_settings.json")
            if os.path.exists(settings_path):
                try:
                    with open(settings_path, "r", encoding="utf-8") as f:
                        settings_data = json.load(f)
                        parser_conf = settings_data.get("parser", {})
                        llama_key = parser_conf.get("llama_cloud_api_key")
                        if not llama_key:
                            llama_key = settings_data.get("llm", {}).get("llama_cloud_api_key")
                except Exception as e:
                    print(f"Error reading settings: {e}")

            import document_extractor_helper

            for file_name, file_data in files:
                file_names.append(file_name)
                safe_filename = f"{int(time.time())}_{file_name}"
                file_path = os.path.join(uploads_dir, safe_filename)
                with open(file_path, "wb") as f:
                    f.write(file_data)

                # Try LlamaParse if key is provided
                doc_text = ""
                if llama_key and not llama_key.startswith("YOUR_") and len(llama_key.strip()) > 10:
                    doc_text = document_extractor_helper.parse_with_llamaparse(file_path, llama_key)
                
                # Fallback to local parsing
                if not doc_text or not doc_text.strip():
                    ext = os.path.splitext(file_name)[1].lower()
                    if ext == ".txt":
                        try:
                            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                                doc_text = f.read()
                        except Exception as txt_err:
                            print(f"Error reading plain text file: {txt_err}")
                    else:
                        try:
                            doc_text = document_extractor_helper.extract_full_text(file_path)
                        except Exception as ext_err:
                            print(f"Failed to use extract_full_text: {ext_err}")

                # Limit per-document length using get_relevant_content to prevent token overflow
                if doc_text and doc_text.strip():
                    excerpt = document_extractor_helper.get_relevant_content(doc_text.strip(), max_chars=12000)
                    extracted_text_blocks.append(f"=== Document: {file_name} ===\n{excerpt}")

            combined_text = "\n\n".join(extracted_text_blocks)
            file_names_str = ", ".join(file_names)
            
            # Fallback if no text could be extracted at all
            if not combined_text or len(combined_text.strip()) < 10:
                self.send_error_json(400, "No readable text could be extracted from the uploaded document(s). Please verify file formats.")
                return

            scope = None
            eligibility = None
            checklist = None

            # 1. Attempt AI LLM extraction if keys are available
            import llm_client
            llm_config = llm_client.load_llm_config()
            has_llm_keys = bool(llm_config.get("api_key") or os.getenv("COHERE_API_KEY") or os.getenv("DEEPSEEK_API_KEY") or llm_config.get("cohere_api_key") or llm_config.get("deepseek_api_key"))

            if has_llm_keys:
                print("Attempting AI LLM extraction...")
                system_prompt = (
                     "you are a senior tender/procurement analyst who has read thousands of government and corporate RFPs, tenders, and bid documents across sectors (construction, IT, consultancy, supply, EPC, PMC, etc). You are meticulous, skeptical of assumptions, and never invent facts that aren't grounded in the documents.\n"

"CONTEXT\n"
'''You will be given every document belonging to ONE tender — this may include the main tender notice/RFP, corrigenda, addenda, annexures, eligibility criteria sheets, forms, BOQs, and AI-generated summaries. Each document is marked with a "--- Document: <filename> ---" label. Document sets can be long, messy, scanned/OCR'd, inconsistently formatted, and spread across many pages or files. Treat every document as potentially relevant — do not skip content because it looks dense or repetitive.\n'''

"READING STRATEGY (do this before answering)\n"
"1. Identify what each attached document is (main notice, corrigendum, annexure, eligibility sheet, form, summary, etc).\n"
'''2. If a corrigendum or addendum contradicts or amends the original document (dates, values, eligibility thresholds, scope changes), the corrigendum/addendum takes precedence. Use the corrected figure and note the change in the relevant item's text (e.g. "turnover requirement revised to INR 40 Cr per corrigendum").\n'''

'''3. Terminology varies by issuer — "Scope of Work" may appear as "Terms of Reference,\n" "Nature of Work," "Objectives," or not be labeled at all. "Qualification Criteria" may appear as "Eligibility Criteria," "Pre-Qualification," or "Bidder Requirements." "Checklist" may appear as "Documents to be Submitted," "Annexure List," or a forms index. Recognize these regardless of label.\n'''
'''4. Ignore boilerplate that isn't substantive (cover pages, tables of contents, generic legal disclaimers unrelated to eligibility or submissions).\n'''

'''YOUR TASK — produce three clearly separate, non-overlapping parts:\n'''

'''1. SCOPE OF WORK: What must the winning bidder actually deliver? Compose this yourself — it is rarely handed to you as a ready paragraph. Draw on the project title, category/products, deliverables, site/location, duration, phases (e.g. construction + O&M), and any technical description. Write 3-6 sentences of real prose describing the work, not a copied table row or field list.\n'''

'''2. QUALIFICATION CRITERIA: Every eligibility requirement a bidder must meet BEFORE their bid is even considered — minimum turnover, years of experience, similar-project experience (with thresholds), certifications, registrations, financial standing, JV/consortium rules, technical capacity. Each as its own item. Preserve exact numbers, currencies, and time periods precisely as stated — do not round or approximate.\n'''

'''3. DOCUMENT CHECKLIST: Every document, form, certificate, annexure, or declaration the bidder must submit WITH the bid. Each as its own item, using the form/annexure name or number as given if one exists.\n'''

'''SOURCING\n'''
'''If more than one document is attached, tag each qualification-criteria and checklist item with the filename it came from. For scope of work, list which document(s) contributed to it. If only one document is attached, still populate the source field with that filename."\n'''
                    "You are an expert civil engineering analyst reading a tender document.\n"
                    "Your task is to extract three factors: the scope of work, the eligibility criteria, and the checklist of documents required for bidding.\n"
                    "\n"
                    "CRITICAL INSTRUCTIONS FOR ELIGIBILITY CRITERIA:\n"
                    "- Identify the most important qualification and eligibility criteria in the document (such as turnover, experience, registrations, JV rules, MSME exemptions, etc.).\n"
                    "- Format the eligibility criteria as a list of Question & Answer pairs based on the document contents. Frame each requirement as a question the bidder would ask, followed by the exact answer from the document.\n"
                    "- Use the format:\n"
                    "  **Question:** [The eligibility question]\n"
                    "  [The answer containing exact numbers, values, and rules]\n"
                    "- Provide between 4 to 8 relevant question-and-answer pairs based on the document. Do not use generic sample questions if they are not in the document.\n"
                    "\n"
                     "CRITICAL INSTRUCTIONS FOR DOCUMENT CHECKLIST:\n"
                    "Every document/checklist has to be mentioned. There can be an heading for Document List / Checklist. so retrieve every document mentioned.\n"
                    "\n"
                    "You must respond ONLY with a valid JSON object. Do not include any other text before or after the JSON. The JSON keys must be exactly:\n"
                    "{\n"
                    '  "scope_of_work": "Detailed scope of work summary...",\n'
                    '  "eligibility_criteria": "Formatted Q&A criteria...",\n'
                    '  "document_checklist": [\n'
                    '    "Checklist item 1...",\n'
                    '    "Checklist item 2..."\n'
                    '  ]\n'
                    "}"
                )
                truncated_text = combined_text[:150000]
                user_prompt = (
                    f"Tender Title: {title}\n\n"
                    f"--- TENDER DOCUMENT TEXT ---\n{truncated_text}\n--- END OF DOCUMENT ---\n\n"
                    "Extract Scope, Eligibility Criteria Q&A, and Document Checklist as JSON."
                )
                try:
                    llm_response = llm_client.call_llm(user_prompt, system_prompt, json_mode=True)
                    if llm_response and llm_response.strip():
                        parsed = json.loads(llm_response)
                        scope = parsed.get("scope_of_work")
                        eligibility = parsed.get("eligibility_criteria")
                        
                        chk_obj = parsed.get("document_checklist") or parsed.get("checklist")
                        if isinstance(chk_obj, list):
                            checklist = chk_obj
                        elif chk_obj:
                            checklist = [str(chk_obj)]
                            
                        print("AI LLM extraction succeeded!")
                except Exception as llm_err:
                    print(f"AI LLM extraction failed: {llm_err}. Falling back to heuristic rule-based extractor.")

            # 2. Fallback to local heuristic extractor if LLM fails or is disabled
            if not scope or not eligibility or not checklist:
                print("Running local heuristic rule-based extraction fallback...")
                import document_extractor_helper
                heuristics = document_extractor_helper.extract_heuristically(combined_text, title)
                if not scope:
                    scope = heuristics["scope_of_work"]
                if not eligibility:
                    eligibility = heuristics["eligibility_criteria"]
                if not checklist:
                    checklist = heuristics["document_checklist"]

            if not scope or not eligibility or not checklist:
                self.send_error_json(500, "Failed to parse Scope, Qualification, or Checklist parameters from the document text.")
                return

            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            created_at = datetime.now().isoformat()
            cursor.execute("""
                INSERT INTO uploaded_tenders (title, scope_of_work, eligibility, document_checklist, status, file_name, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                title,
                scope,
                eligibility,
                json.dumps(checklist),
                'pending',
                file_names_str,
                created_at
            ))
            row_id = cursor.lastrowid
            conn.commit()
            conn.close()

            result = {
                "id": row_id,
                "title": title,
                "scope_of_work": scope,
                "eligibility_criteria": eligibility,
                "document_checklist": checklist,
                "status": "pending",
                "file_name": file_names_str,
                "created_at": created_at
            }
            self.send_json_response(result)

        except Exception as e:
            self.send_error_json(500, f"Failed to upload and process document: {str(e)}")

    def get_uploaded_tenders(self):
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM uploaded_tenders ORDER BY created_at DESC")
            rows = cursor.fetchall()
            conn.close()

            tenders = []
            for r in rows:
                try:
                    checklist = json.loads(r["document_checklist"])
                except:
                    checklist = []
                tenders.append({
                    "id": r["id"],
                    "title": r["title"],
                    "scope_of_work": r["scope_of_work"],
                    "eligibility_criteria": r["eligibility"],
                    "document_checklist": checklist,
                    "status": r["status"],
                    "file_name": r["file_name"],
                    "created_at": r["created_at"]
                })
            self.send_json_response(tenders)
        except Exception as e:
            self.send_error_json(500, f"Database error: {str(e)}")

    def update_uploaded_tender_status(self):
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            data = json.loads(body.decode('utf-8'))
            
            tender_id = data.get("id")
            status = data.get("status")
            
            if not tender_id or status not in ("approved", "rejected", "pending"):
                self.send_error_json(400, "Invalid parameters.")
                return
                
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("UPDATE uploaded_tenders SET status = ? WHERE id = ?", (status, tender_id))
            conn.commit()
            conn.close()
            
            self.send_json_response({"status": "success", "message": f"Tender {tender_id} updated to {status}."})
        except Exception as e:
            self.send_error_json(500, f"Database error: {str(e)}")

    def remove_uploaded_tender(self):
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            data = json.loads(body.decode('utf-8'))
            
            tender_id = data.get("id")
            if not tender_id:
                self.send_error_json(400, "Invalid parameters.")
                return
                
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM uploaded_tenders WHERE id = ?", (tender_id,))
            conn.commit()
            conn.close()
            
            self.send_json_response({"status": "success", "message": f"Tender {tender_id} removed successfully."})
        except Exception as e:
            self.send_error_json(500, f"Database error: {str(e)}")

    def send_json_response(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def send_error_json(self, status_code, message):
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps({"error": message}).encode("utf-8"))


def init_uploader_db():
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS uploaded_tenders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                scope_of_work TEXT,
                eligibility TEXT,
                document_checklist TEXT,
                status TEXT DEFAULT 'pending',
                file_name TEXT,
                created_at TEXT
            )
        """)
        conn.commit()
        conn.close()
        print("Uploader database table initialized successfully.")
    except Exception as e:
        print(f"Error initializing uploader database: {e}")


if __name__ == "__main__":
    # Ensure current working directory is the frontend directory
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    
    # Initialize uploader database table
    init_uploader_db()
    
    # Allow address reuse to prevent "Address already in use" errors on restart
    socketserver.TCPServer.allow_reuse_address = True
    
    with socketserver.TCPServer(("", PORT), TenderDashboardAPIHandler) as httpd:
        print(f"==================================================")
        print(f" Tender Intelligence Portal Server Running")
        print(f" Local Address: http://localhost:{PORT}")
        print(f" Database Path: {DB_PATH}")
        print(f"==================================================")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down server.")

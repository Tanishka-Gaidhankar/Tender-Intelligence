import http.server
import socketserver
import json
import sqlite3
import os
import urllib.parse
import subprocess
import threading
import sys

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
        else:
            # Fallback to standard static file serving
            super().do_GET()

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
            project_dir = os.path.dirname(pipeline_path)

            def run_stage(args_list, stage_label):
                """Runs one pipeline stage as subprocess, streaming output to log."""
                print(f"Triggering: {python_bin} {pipeline_path} {' '.join(args_list)}")
                proc = subprocess.Popen(
                    [python_bin, pipeline_path] + args_list,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    cwd=project_dir
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
                [python_bin, pipeline_path, "--stage2"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=project_dir
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
                    ["bench", "--site", "kbpcivil.in", "execute", "kbp_civil.sync_tenders.sync"],
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
                    ["bench", "--site", "kbpcivil.in", "execute", "frappe.get_single('Tnder Primary Screening').refresh_tenders"],
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
            for r in rows:
                is_lead = r["lead_id"] is not None
                status = "lead_created" if is_lead else r["status"]
                # Arrival date: use date portion of created_at (first insert = first time seen)
                raw_created = r["created_at"]
                arrival_date = raw_created.split('T')[0] if raw_created and 'T' in raw_created else (raw_created or 'N/A')
                tenders.append({
                    "tender_id": r["tender_id"],
                    "source": r["source"],
                    "title": r["title"],
                    "authority": r["authority"],
                    "location": r["location"],
                    "value": r["value"],
                    "emd": r["emd"],
                    "due_date": r["due_date"],
                    "status": status,
                    # Fallback raw values or lead values
                    "ai_score": r["lead_ai_score"] if r["lead_ai_score"] is not None else r["raw_ai_score"],
                    "ai_rationale": r["lead_ai_rationale"] if r["lead_ai_rationale"] is not None else r["raw_ai_rationale"],
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

            # Count Stage 1 Passed (status in ('rules_passed', 'lead_created', 'rejected_ai', 'ai_processing'))
            # Note: We also consider any tender in the tender_leads table as stage 1 passed
            cursor.execute("""
                SELECT count(DISTINCT r.tender_id) FROM raw_tender_feed r
                LEFT JOIN tender_leads l ON r.tender_id = l.tender_id
                WHERE r.status IN ('rules_passed', 'lead_created', 'rejected_ai', 'ai_processing')
                   OR l.tender_id IS NOT NULL
            """)
            stage1_passed = cursor.fetchone()[0]

            # Count Stage 1 Rejected
            cursor.execute("SELECT count(*) FROM raw_tender_feed WHERE status = 'rules_rejected'")
            stage1_rejected = cursor.fetchone()[0]

            # Count Stage 2 Promoted Leads (from tender_leads directly)
            cursor.execute("SELECT count(*) FROM tender_leads")
            stage2_leads = cursor.fetchone()[0]

            # Count Stage 2 Rejected
            cursor.execute("SELECT count(*) FROM raw_tender_feed WHERE status = 'rejected_ai'")
            stage2_rejected = cursor.fetchone()[0]

            # Count Processing
            cursor.execute("SELECT count(*) FROM raw_tender_feed WHERE status IN ('new', 'ai_processing')")
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


if __name__ == "__main__":
    # Ensure current working directory is the frontend directory
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    
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

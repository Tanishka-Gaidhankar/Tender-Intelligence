import time
import requests
import subprocess
import os

# Cloud Configuration
CLOUD_URL  = "https://demokbp.m.frappe.cloud"
API_KEY    = "7b297c61a9c0294"
API_SECRET = "47f108f27186fa4"
HEADERS    = {
    "Authorization": f"token {API_KEY}:{API_SECRET}",
    "Content-Type":  "application/json",
    "Accept":        "application/json",
}

def poll_and_run():
    print("=================================================================")
    print("Local Scraper Daemon started.")
    print("Listening for 'Scrape Tenders' requests from demokbp.m.frappe.cloud...")
    print("Keep this terminal open to allow cloud-triggered scraping.")
    print("=================================================================")
    
    while True:
        try:
            # Query the cloud site for any screening documents where time_taken is "Running..."
            url = f"{CLOUD_URL}/api/resource/Tender%20Primary%20Screening"
            resp = requests.get(
                url,
                headers=HEADERS,
                params={
                    "fields": '["name","screening_date","time_taken"]',
                    "filters": '[["Tender Primary Screening", "time_taken", "=", "Running..."]]'
                },
                timeout=15
            )
            
            if resp.status_code == 200:
                docs = resp.json().get("data", [])
                for d in docs:
                    s_date = d.get("screening_date")
                    name = d.get("name")
                    if s_date:
                        print(f"\n[REQUEST RECEIVED] Cloud clicked for Date: {s_date} (Doc: {name})")
                        
                        # Update status on cloud to let user know it's scraping locally
                        requests.put(
                            f"{CLOUD_URL}/api/resource/Tender%20Primary%20Screening/{name}",
                            headers=HEADERS,
                            json={"time_taken": "Scraping locally on laptop..."},
                            timeout=15
                        )
                        
                        # Trigger local bench scraper command
                        print(f"-> Starting Playwright scraper locally for {s_date}...")
                        cmd = [
                            "/home/kbp/kbpcivil/env/bin/python", 
                            "-m", "frappe.utils.bench_helper", 
                            "frappe", "--site", "kbpcivil.in", 
                            "execute", "tenderlead.api.run_pipeline_and_sync", 
                            "--args", f"['{s_date}']"
                        ]
                        # Run the command and wait for it to complete
                        result = subprocess.run(cmd, cwd="/home/kbp/kbpcivil", capture_output=True, text=True)
                        
                        if result.returncode == 0:
                            print(f"-> Local scraper completed successfully for {s_date}!")
                        else:
                            print(f"-> Scraper failed: {result.stderr}")
                            # Reset status on failure
                            requests.put(
                                f"{CLOUD_URL}/api/resource/Tender%20Primary%20Screening/{name}",
                                headers=HEADERS,
                                json={"time_taken": "Scrape failed locally"},
                                timeout=15
                            )
            
        except Exception as e:
            print(f"Connection/Polling error: {e}")
            
        # Poll every 10 seconds
        time.sleep(10)

if __name__ == "__main__":
    poll_and_run()

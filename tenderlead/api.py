import frappe
import subprocess
import os
from datetime import datetime

@frappe.whitelist()
def trigger_sync():
    """
    Manually triggers the tender synchronization from the local SQLite database.
    Can be called via POST to /api/method/tenderlead.api.trigger_sync
    """
    try:
        # Import the local sync function (e.g. if you migrate sync_tenders.py to the app)
        # For now, we will dynamically try to import and execute it from this package
        from tenderlead.sync_tenders import sync
        sync()
        return {"status": "success", "message": "Tenders synced successfully from SQLite database"}
    except ImportError:
        # Fallback in case sync_tenders is placed differently
        return {"status": "error", "message": "Module tenderlead.sync_tenders not found"}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Tender Sync API Error")
        return {"status": "error", "message": str(e)}

@frappe.whitelist()
def trigger_intake_pipeline(screening_date=None):
    """
    Manually triggers the intake scraper and filter pipeline in the background.
    Can be called via POST to /api/method/tenderlead.api.trigger_intake_pipeline
    """
    # Restrict trigger to System Managers
    if "System Manager" not in frappe.get_roles():
        frappe.throw("Not authorized to trigger this pipeline", frappe.PermissionError)
        
    try:
        # Enqueue pipeline in background using Frappe's worker
        frappe.enqueue(
            "tenderlead.api.run_pipeline_and_sync",
            queue="long",
            timeout=1500,
            screening_date=screening_date,
            is_async=True
        )
        return {"status": "success", "message": "Tender intake and scoring pipeline started in the background"}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Tender Intake Pipeline API Error")
        return {"status": "error", "message": str(e)}

def run_pipeline_and_sync(screening_date=None):
    """
    Executes the intake, stage1 (filtering), stage2 (scoring) pipeline,
    then syncs the results into ERPNext and updates the screening document stats.
    """
    start_time = datetime.now()
    
    # Set workspace directory in python path just in case
    project_dir = "/home/kbp/Documents/Tenderlead"
    import sys
    if project_dir not in sys.path:
        sys.path.append(project_dir)
        
    # Import pipeline stages and sync function
    from tenderlead.pipeline import run_intake, run_stage1, run_stage2
    from tenderlead.sync_tenders import sync
    
    # Parse target date
    target_date = None
    if screening_date:
        try:
            target_date = datetime.strptime(screening_date, "%Y-%m-%d").date()
        except ValueError:
            pass

    # Run intake, filtering, scoring
    print(f"Running pipeline for date: {screening_date or 'today'}")
    run_intake(direct=True, target_date=target_date)
    run_stage1()
    run_stage2()
    
    # Sync SQLite staging to ERPNext Raw Tender Lead
    print("Syncing SQLite database to ERPNext...")
    sync()
    
    # Sync SQLite staging to Frappe Cloud (demokbp.m.frappe.cloud)
    try:
        from tenderlead.sync_to_cloud import sync as sync_cloud
        print("Syncing SQLite database to Frappe Cloud...")
        sync_cloud()
    except Exception as e:
        print(f"Frappe Cloud sync failed/skipped: {e}")
    
    end_time = datetime.now()
    duration = end_time - start_time
    minutes, seconds = divmod(duration.seconds, 60)
    time_taken_str = f"{minutes}m {seconds}s"
    
    # Update the Tender Primary Screening document(s) matching this date
    if screening_date:
        screenings = frappe.get_all("Tender Primary Screening", filters={"screening_date": screening_date})
        for s in screenings:
            try:
                doc = frappe.get_doc("Tender Primary Screening", s.name)
                doc.time_taken = time_taken_str
                # refresh_tenders recalculates statistics and saves the document
                doc.refresh_tenders()
            except Exception as e:
                frappe.log_error(frappe.get_traceback(), f"Tender Screening update failed for {s.name}")


def refresh_dashboard(screening_date=None):
    """
    Refreshes the Tender Primary Screening dashboard for a given date (defaults to today).
    """
    if not screening_date:
        screening_date = frappe.utils.today()
    screenings = frappe.get_all("Tender Primary Screening", filters={"screening_date": screening_date})
    for s in screenings:
        try:
            doc = frappe.get_doc("Tender Primary Screening", s.name)
            doc.refresh_tenders()
            print(f"Refreshed Tender Primary Screening: {s.name}")
        except Exception as e:
            frappe.log_error(frappe.get_traceback(), f"Tender Screening refresh failed for {s.name}")
            print(f"Error refreshing {s.name}: {e}")




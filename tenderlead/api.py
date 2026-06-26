import frappe
import subprocess
import os

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
def trigger_intake_pipeline():
    """
    Manually triggers the intake scraper and filter pipeline in the background.
    Can be called via POST to /api/method/tenderlead.api.trigger_intake_pipeline
    """
    # Restrict trigger to System Managers
    if "System Manager" not in frappe.get_roles():
        frappe.throw("Not authorized to trigger this pipeline", frappe.PermissionError)
        
    try:
        project_dir = "/home/kbp/Documents/Tenderlead"
        python_bin = os.path.join(project_dir, ".venv", "bin", "python3")
        pipeline_script = os.path.join(project_dir, "tenderlead", "pipeline.py")
        
        if not os.path.exists(python_bin):
            python_bin = "python3"
            
        # Execute the pipeline in the background using subprocess
        subprocess.Popen(
            [python_bin, pipeline_script, "--intake-direct", "--stage1", "--stage2"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=project_dir,
            start_new_session=True # Let it run detached
        )
        return {"status": "success", "message": "Tender intake and scoring pipeline started in the background"}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Tender Intake Pipeline API Error")
        return {"status": "error", "message": str(e)}

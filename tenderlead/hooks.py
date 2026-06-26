from . import __version__ as app_version

app_name = "tenderlead"
app_title = "Tenderlead"
app_publisher = "KBP Civil Engineering Services"
app_description = "Tender Intelligence System: Automated intake, filtering, AI scoring, and ERPNext integration for tenders"
app_email = "admin@kbpcivil.com"
app_license = "MIT"

# Document Events
# ----------------
# doc_events = {
# 	"Raw Tender Lead": {
# 		"after_insert": "tenderlead.api.on_new_tender"
# 	}
# }

# Scheduled Tasks
# ---------------
scheduler_events = {
	"daily": [
		"tenderlead.api.trigger_sync"
	]
}

# Desk Includes
# -------------
# include js, css files in header of desk.html
app_include_css = "/assets/tenderlead/css/tenderlead.css"
app_include_js = "/assets/tenderlead/js/tenderlead.js"

# Fixtures (to export/import custom Doctype modifications, fields, or properties)
fixtures = [
	"Custom Field",
	"Property Setter"
]

from setuptools import setup, find_packages
import os

# read version from tenderlead/__init__.py without importing it
version = "0.1.0"
init_path = os.path.join(os.path.dirname(__file__), "tenderlead", "__init__.py")
if os.path.exists(init_path):
    with open(init_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("__version__"):
                version = line.split("=")[1].strip().strip('"').strip("'")
                break

setup(
	name="tenderlead",
	version=version,
	description="Tender Intelligence System: Automated intake, filtering, AI scoring, and ERPNext integration for tenders",
	author="KBP Civil Engineering Services",
	author_email="admin@kbpcivil.com",
	packages=find_packages(include=["tenderlead", "tenderlead.*"]),
	zip_safe=False,
	include_package_data=True,
	install_requires=[
		"beautifulsoup4>=4.15.0",
		"playwright>=1.60.0",
		"requests>=2.34.0"
	]
)

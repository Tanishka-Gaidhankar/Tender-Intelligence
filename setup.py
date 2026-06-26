from setuptools import setup, find_packages

# get version from __version__ variable in tenderlead/__init__.py
from tenderlead import __version__ as version

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

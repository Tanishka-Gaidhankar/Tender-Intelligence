#!/bin/bash
msg="${1:-Update changes}"

echo "=================================================="
echo "1. Syncing local files to bench app..."
echo "=================================================="
cp -v /home/kbp/Documents/Tenderlead/tenderlead/email_reader.py /home/kbp/kbpcivil/apps/tenderlead/tenderlead/
cp -v /home/kbp/Documents/Tenderlead/tenderlead/pipeline.py /home/kbp/kbpcivil/apps/tenderlead/tenderlead/
cp -v /home/kbp/Documents/Tenderlead/tenderlead/api.py /home/kbp/kbpcivil/apps/tenderlead/tenderlead/
cp -v /home/kbp/Documents/Tenderlead/tenderlead/sync_to_cloud.py /home/kbp/kbpcivil/apps/tenderlead/tenderlead/
cp -v /home/kbp/Documents/Tenderlead/tenderlead/sync_tenders.py /home/kbp/kbpcivil/apps/tenderlead/tenderlead/

echo ""
echo "=================================================="
echo "2. Pushing tenderlead changes to GitHub..."
echo "=================================================="
cd /home/kbp/Documents/Tenderlead
git add tenderlead/email_reader.py tenderlead/pipeline.py tenderlead/api.py
git commit -m "$msg"
git push origin main

echo ""
echo "=================================================="
echo "3. Pushing kbp_civil DocType changes to GitHub..."
echo "=================================================="
cd /home/kbp/kbpcivil/apps/kbp_civil
git add kbp_civil/tendering/doctype/tender_primary_screening/
git add kbp_civil/tendering/doctype/raw_tender_lead/raw_tender_lead.json
git add kbp_civil/tendering/workspace/tendering/tendering.json
git commit -m "$msg"
git push upstream master

echo ""
echo "=================================================="
echo "Deployment push complete!"
echo "=================================================="

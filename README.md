# iPOSG Stock Bot: Vercel + Claude + Windows worker

## Configure Vercel
Add STOCKBOT_ADMIN_TOKEN, UPSTASH_REDIS_REST_URL, UPSTASH_REDIS_REST_TOKEN in Vercel project environment settings. Create an Upstash Redis database first. Redeploy the main branch. Open the Vercel URL and upload a PDF/photo.

## Configure Windows worker
Install Python 3.11+, then in PowerShell run:
```powershell
py -m pip install -r requirements.txt
py -m playwright install chromium
$env:ANTHROPIC_API_KEY="your-key"
$env:ANTHROPIC_MODEL="claude-sonnet-4-5"
$env:UPSTASH_REDIS_REST_URL="your-upstash-rest-url"
$env:UPSTASH_REDIS_REST_TOKEN="your-upstash-rest-token"
py bot.py --login
py worker.py
```
Log into iPOSG when the browser opens, return to PowerShell and press Enter. Keep the Windows PC and worker running. Enter the Vercel admin token when uploading an invoice.

## What is working in code
- Invoice upload to Redis job queue; status endpoint
- Claude vision extraction for PDF/photo invoices
- Background Windows worker invoking the existing Playwright stock updater
- No per-item approval
- Existing negative-stock correction and stock-addition rules

## Unresolved production blockers
This is NOT certified production-ready. The products.csv mappings have missing barcodes, prices and department fields. The worker stops with needs_mapping if it cannot find a verified product; it cannot currently research arbitrary products online or estimate missing product master data. The iPOSG selectors and new-product form are not tested against a logged-in account. The queue is not crash-safe, and Redis holds invoice contents. Do not use real inventory until tested. An unexpected interruption during stock submission must be inspected before retrying to avoid duplicates.

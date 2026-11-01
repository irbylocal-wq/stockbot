# iPOSG Invoice Stock Automation

## What is implemented
- Flask invoice-upload dashboard with token authentication and job-status API.
- PDF/photo invoice extraction using an OpenAI vision model when OPENAI_API_KEY is configured.
- Local background worker launches the existing Playwright iPOSG updater without per-item approvals.
- Existing updater supports negative-stock correction, new-product creation with complete mapped fields, and per-item duplicate tracking.
- OCR extraction reports unmatched products; it does not silently invent barcodes or prices.

## Important deployment limitation
**Vercel serves the web dashboard, but cannot run this project's persistent local Playwright browser worker.** The Vercel upload endpoint intentionally returns HTTP 503 rather than falsely claiming that inventory was updated. Deploy the dashboard and worker together on a persistent Windows PC or suitable VM. The local browser must first be logged into iPOSG.

## Windows setup
Install Python 3.11+, Tesseract OCR and Chromium for Playwright.
Run in PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium
$env:STOCKBOT_ADMIN_TOKEN = "CHOOSE-A-LONG-RANDOM-SECRET"
$env:OPENAI_API_KEY = "YOUR-OPENAI-API-KEY"
python bot.py --login
python app.py
```

Open http://127.0.0.1:5000, enter your admin token, and upload a PDF, image or CSV. Use the job ID returned by the upload endpoint and GET /api/jobs/<id> with the X-Stockbot-Token header to check progress.

**Do not expose this Flask development server to the public internet.** Use a production WSGI server and HTTPS authentication if deploying beyond localhost. Keep credentials, invoices, browser profiles and runtime data private.

## Still required before unattended production use
- The existing products.csv is only a sample and has **blank iPOSG barcodes, prices and departments**. It cannot identify arbitrary suppliers or create arbitrary missing products.
- Live iPOSG selectors and new-product forms have not been validated against an authenticated account. A live test with representative items is necessary.
- Internet lookup for missing case prices, barcodes and retail prices is **not implemented**. AI extraction only reads the invoice; it does not verify unknown products on the web.
- The local worker uses a thread and SQLite job records. It is not a durable distributed queue; restarting the process may interrupt an active job. Use a persistent queue for hosted production.
- Unknown or ambiguous invoice items are reported as unresolved and the job fails without changing stock. This prevents arbitrary wrong-barcode updates.
- Case-to-unit calculations depend on verified product mappings. Loose-unit handling is not yet supported.
- The app and bot have not been end-to-end tested against your iPOSG account. Do not treat this as production-certified.

## Stock rule
If current stock is negative, adjustment = purchased units - current stock.
Otherwise, adjustment = purchased units. For example, -1 plus 7 purchased requires +8, ending at 7; 5 plus 7 requires +7, ending at 12.

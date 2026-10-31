# iPOSG Booker Invoice Stock Bot (Windows)

This is a working **local prototype** with invoice OCR, explicit product mappings, case-to-unit conversion, negative-stock correction, duplicate protection, persistent browser login, and iPOSG browser automation. It has **not** been tested against your logged-in iPOSG account. No website changes happen unless you run with `--live`.

## Setup (Windows PowerShell)

1. Install Python 3.11+ and Tesseract OCR for Windows. Ensure `tesseract.exe` is on PATH, or set `TESSERACT_CMD` to its full path.
2. Open PowerShell in this folder:

   `py -m venv .venv`

   `.\.venv\Scripts\Activate.ps1`

   `pip install -r requirements.txt`

   `python -m playwright install chromium`

3. Run `python bot.py --login`. A browser opens. Log in yourself (never put passwords in code), then return to PowerShell and press Enter. Your browser session is saved in `.browser_profile` on your computer.
4. Open `products.csv`. Fill in each **real iPOSG barcode** and verify `units_per_case`. Booker supplier codes are NOT necessarily iPOSG barcodes. The included rows are examples, not confirmed mappings.
5. Place a clear invoice photo or PDF in `invoices` (create the folder if needed).
6. Run `python bot.py --invoice invoices\invoice.jpg` to generate a **dry-run** report. Read `reports/` and check all quantities and matched barcodes.
7. After confirming mappings and browser behavior, run `python bot.py --invoice invoices\invoice.jpg --live` to submit updates. Use one test item first. **Live mode can change inventory.**

## Stock rule (your requested rule)

- `purchased_units = invoice_cases * units_per_case`
- If current stock is negative: `adjustment = purchased_units - current_stock` (e.g. -1 + 8 adjustment -> 7)
- Otherwise: `adjustment = purchased_units` (e.g. 5 + 8 -> 13)

## Limitations and safeguards

- OCR on wrinkled/blurred receipts is imperfect. The bot **skips** lines without an unambiguous Booker code, quantity, and explicit mapped barcode. It does not invent product barcodes.
- OCR quantity extraction is conservative and depends on Booker receipt layout. Check dry-run output before live operation. When a product is missing, the bot attempts to create it first using verified fields in products.csv, then resumes the normal quantity adjustment. Missing required fields stop that item rather than inventing values.
- The iPOSG Quick Adjustment UI was inferred from screenshots, not verified by programmatic inspection. If the site markup differs, the bot stops with an error instead of guessing. The selected adjustment type is the one you demonstrated: `Stock Transfer In (Sale at Cost)`; confirm it is appropriate for your shop before live use.
- Live mode processes **one item per submission**, then checks the resulting stock. It stores completed item IDs in `state.json`, so a retry skips successful items. If verification is ambiguous it stops. Do not delete `state.json` casually.
- This bot uses a local browser profile. Do not share the `.browser_profile` folder or `state.json` with anyone.
- Full hands-off automation requires first configuring product mappings, OCR validation, and UI selectors against your own account. This prototype does not claim that all invoices will be read perfectly.

## NEW: Create missing products first

If a barcode is not found in iPOSG, the bot attempts to add the product before continuing with its stock adjustment. To permit this, add the following columns to `products.csv`: `selling_price,cost_price,vat,department`. Fill in the exact approved values for each product. Never guess prices, VAT, barcodes or department. If any required value is missing, the bot stops that item and reports the missing field.

The create form selectors and save button have **not** been verified on the live iPOSG site. A dry run never submits any changes.

For scanned invoices, OCR produces `reports/<invoice>_review.csv`. Confirm the extracted Booker codes and enter case quantities in that file, then use that CSV with `--invoice`. This is necessary because OCR alone is not reliable enough to silently update stock.

"""iPOSG stock updater. Browser selectors require verification against live account."""
import argparse, csv, hashlib, json, os, re, sys
from pathlib import Path
from decimal import Decimal
from datetime import datetime

ROOT = Path(__file__).resolve().parent
STOCK_URL = 'https://cloud.iposg.com/stock-quick-adjustment'
ADD_URL = 'https://cloud.iposg.com/stock-manager-new/stock'
STATE = ROOT / 'state.json'


def read_catalog():
    with (ROOT / 'products.csv').open(newline='', encoding='utf-8-sig') as f:
        return {r['booker_code'].strip(): r for r in csv.DictReader(f) if r.get('booker_code')}


def read_invoice(path):
    """Verified structured CSV preferred. OCR images/PDF generate a review CSV, never guess quantities."""
    if path.suffix.lower() == '.csv':
        with path.open(newline='', encoding='utf-8-sig') as f:
            return list(csv.DictReader(f))
    import pytesseract
    from PIL import Image
    if os.environ.get('TESSERACT_CMD'):
        pytesseract.pytesseract.tesseract_cmd = os.environ['TESSERACT_CMD']
    if path.suffix.lower() == '.pdf':
        import fitz
        doc = fitz.open(path)
        pages = [Image.frombytes('RGB', [p.width, p.height], p.samples) for p in []]
        texts = []
        for page in doc:
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            texts.append(pytesseract.image_to_string(Image.frombytes('RGB', (pix.width, pix.height), pix.samples)))
        raw = '\n'.join(texts)
    else:
        raw = pytesseract.image_to_string(Image.open(path))
    report = ROOT / 'reports'
    report.mkdir(exist_ok=True)
    (report / (path.stem + '_ocr.txt')).write_text(raw, encoding='utf-8')
    candidates = []
    for line in raw.splitlines():
        m = re.search(r'\b(\d{6})\b', line)
        if m:
            candidates.append({'booker_code': m.group(1), 'cases': '', 'description': line.strip()})
    out = report / (path.stem + '_review.csv')
    with out.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['booker_code','cases','description'])
        writer.writeheader(); writer.writerows(candidates)
    raise SystemExit(f'OCR extracted to {out}. Verify product codes and fill cases, then run --invoice {out}. No stock changed.')


def plan(rows, catalog):
    result = []
    for row in rows:
        code = str(row.get('booker_code','')).strip()
        entry = catalog.get(code)
        if not entry:
            result.append({'code': code, 'status':'SKIP: missing products.csv mapping'}); continue
        try:
            cases = Decimal(str(row['cases']).strip())
            pack = Decimal(str(entry['units_per_case']).strip())
            units = cases * pack
            if cases <= 0 or pack <= 0 or units != units.to_integral_value(): raise ValueError()
        except (ValueError, KeyError, ArithmeticError):
            result.append({'code':code, 'status':'SKIP: invalid cases or units_per_case'}); continue
        barcode = entry.get('iposg_barcode','').strip()
        if not barcode:
            result.append({'code':code, 'status':'SKIP: no verified iPOSG barcode'}); continue
        result.append({'code':code,'barcode':barcode,'description':entry.get('description',''),
                       'units':int(units),'status':'READY', 'entry':entry})
    return result


def locate_field(page, *names):
    for name in names:
        loc = page.get_by_label(name, exact=False)
        if loc.count() == 1: return loc
        loc = page.get_by_placeholder(name, exact=False)
        if loc.count() == 1: return loc
    raise RuntimeError(f'Cannot safely locate field {names}; inspect iPOSG form.')


def current_stock(page):
    # A text field or readonly input labelled Current QTY is expected from the supplied screenshot.
    field = locate_field(page, 'Current QTY', 'Current Qty', 'Current Stock')
    value = field.input_value() if field.evaluate('(el) => "value" in el') else field.inner_text()
    match = re.search(r'-?\d+(?:\.\d+)?', value.replace(',',''))
    if not match: raise RuntimeError('Could not read current stock')
    return Decimal(match.group())



def retry_read(action, attempts=3):
    """Retry only read/navigation operations, never stock writes."""
    import time
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    for n in range(attempts):
        try:
            return action()
        except PlaywrightTimeout:
            if n == attempts - 1:
                raise
            time.sleep(min(2 ** n, 8))


def safe_goto(page, url):
    return retry_read(lambda: page.goto(url, wait_until='domcontentloaded', timeout=30000))

def search_existing(page, barcode):
    safe_goto(page, STOCK_URL)
    locate_field(page,'PLU / Barcode','Barcode','PLU').fill(barcode)
    search = page.get_by_role('button', name=re.compile('Search', re.I))
    if search.count()!=1: raise RuntimeError('Search button not uniquely identified')
    retry_read(lambda: search.click(timeout=10000))
    page.wait_for_load_state('domcontentloaded')
    # Product identity must be visible. Missing/ambiguous result triggers new-product path.
    body = page.locator('body').inner_text()
    if barcode not in body:
        return None
    return current_stock(page)


def add_new_product(page, entry, live):
    """Create using verified product master fields only. Never invent selling price or VAT."""
    required = ['iposg_barcode','description','selling_price','cost_price','vat','department']
    missing = [k for k in required if not entry.get(k,'').strip()]
    if missing:
        raise RuntimeError('New product requires verified catalog fields: '+', '.join(missing))
    if not live: return 'WOULD_CREATE'
    safe_goto(page, ADD_URL)
    # iPOSG has an Add Stock form; these selectors need confirmation in the user's account.
    values = [('Barcode', 'iposg_barcode'),('Description','description'),
              ('Selling Price','selling_price'),('Cost Price','cost_price'),
              ('VAT','vat'),('Department','department')]
    for label,key in values:
        field = locate_field(page,label)
        if field.evaluate('(el) => el.tagName.toLowerCase()') == 'select':
            field.select_option(label=entry[key])
        else:
            field.fill(entry[key])
    save = page.get_by_role('button',name=re.compile(r'^(Save|Add Stock|Create)$',re.I))
    if save.count()!=1: raise RuntimeError('Cannot identify new-product Save button safely')
    save.click()
    page.wait_for_timeout(1000)
    # Verify it can be found before adjusting.
    found = search_existing(page, entry['iposg_barcode'])
    if found is None: raise RuntimeError('New product not verified after creation; stopping')
    return 'CREATED'


def adjust(page, units, current, live):
    adjustment = Decimal(units) - min(current, Decimal(0))
    expected = max(current, Decimal(0)) + Decimal(units)
    if not live: return {'adjustment':str(adjustment),'expected':str(expected)}
    typ = locate_field(page, 'Adjustment Type')
    if typ.evaluate('(el) => el.tagName.toLowerCase()') == 'select':
        typ.select_option(label='Stock Transfer In (Sale at Cost)')
    else:
        typ.click(); page.get_by_text('Stock Transfer In (Sale at Cost)',exact=True).click()
    locate_field(page,'Adjustment Qty','Adjustment Quantity').fill(str(adjustment))
    button = page.get_by_role('button',name=re.compile('Update Stock',re.I))
    if button.count()!=1: raise RuntimeError('Cannot safely identify Update Stock button')
    # Never retry a stock-changing click: it may have succeeded despite a timeout.
    button.click(timeout=30000)
    page.wait_for_load_state('domcontentloaded')
    return {'adjustment':str(adjustment),'expected':str(expected)}


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--invoice',type=Path)
    ap.add_argument('--login',action='store_true')
    ap.add_argument('--live',action='store_true')
    args=ap.parse_args()
    if not args.login and not args.invoice: ap.error('Provide --login or --invoice')
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(str(ROOT/'.browser_profile'),headless=False)
        page = browser.pages[0] if browser.pages else browser.new_page()
        if args.login:
            safe_goto(page, 'https://cloud.iposg.com/login')
            input('Log in using browser, then press Enter here to save session...')
            browser.close(); return
        rows=read_invoice(args.invoice)
        planned=plan(rows,read_catalog())
        reports=ROOT/'reports'; reports.mkdir(exist_ok=True)
        state=json.loads(STATE.read_text()) if STATE.exists() else {}
        digest=hashlib.sha256(args.invoice.read_bytes()).hexdigest()
        output=[]
        for item in planned:
            if item['status']!='READY': output.append(item); continue
            key=f"{digest}:{item['code']}:{item['barcode']}"
            if state.get(key)=='done':
                output.append({'code':item['code'],'status':'ALREADY_DONE'});continue
            try:
                current=search_existing(page,item['barcode'])
                created=False
                if current is None:
                    status=add_new_product(page,item['entry'],args.live)
                    if not args.live:
                        output.append({'code':item['code'],'status':status,'units':item['units']});continue
                    created=True
                    current=search_existing(page,item['barcode'])
                    if current is None: raise RuntimeError('New product not found')
                result=adjust(page,item['units'],current,args.live)
                if args.live:
                    after=search_existing(page,item['barcode'])
                    if after is None or after!=Decimal(result['expected']):
                        raise RuntimeError(f'Post-update verification failed: expected {result["expected"]}, got {after}; do not retry blindly')
                    state[key]='done'; STATE.write_text(json.dumps(state,indent=2))
                output.append({'code':item['code'],'barcode':item['barcode'],'created':created,
                               'previous':str(current),'units':item['units'],**result,
                               'status':'UPDATED' if args.live else 'DRY_RUN'})
            except Exception as exc:
                output.append({'code':item['code'],'status':'STOPPED','error':str(exc)})
                break  # Avoid a cascade of incorrect changes.
        report=reports/(datetime.now().strftime('%Y%m%d_%H%M%S')+'_report.json')
        report.write_text(json.dumps(output,indent=2),encoding='utf-8')
        print(json.dumps(output,indent=2));print('Report:',report)
        browser.close()

if __name__=='__main__':main()

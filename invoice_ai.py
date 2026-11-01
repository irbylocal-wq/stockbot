"""Supplier-independent vision extraction using an optional OpenAI API key.

Never invent a barcode or product mapping. Unmatched products are reported.
"""
import base64
import csv
import io
import json
import os
import re
import urllib.request
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent

def _images(path):
    if path.suffix.lower() == ".pdf":
        import fitz
        with fitz.open(path) as pdf:
            for page in list(pdf)[:15]:
                pix = page.get_pixmap(matrix=fitz.Matrix(1.7, 1.7), alpha=False)
                image = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
                yield image
    else:
        yield Image.open(path).convert("RGB")

def _data_url(image):
    image.thumbnail((1800, 1800))
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()

def _ask_model(messages):
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY is required for automatic invoice extraction")
    data = json.dumps({
        "model": os.getenv("STOCKBOT_AI_MODEL", "gpt-4.1-mini"),
        "messages": messages,
        "response_format": {"type": "json_object"},
        "temperature": 0
    }).encode()
    req = urllib.request.Request("https://api.openai.com/v1/chat/completions",
        data=data, headers={"Authorization": "Bearer " + key,
                            "Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as response:
        result = json.load(response)
    return json.loads(result["choices"][0]["message"]["content"])

def extract_invoice(path, output_dir):
    path = Path(path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    with (ROOT / "products.csv").open(encoding="utf-8-sig", newline="") as f:
        catalog = list(csv.DictReader(f))
    content = [{"type": "text", "text": (
        "Extract ALL invoice line items from this supplier invoice, regardless of supplier. "
        "Return JSON object with key items, array of objects: description, supplier_code, "
        "barcode, cases, units_per_case, loose_units, case_price, total_price. "
        "Use numbers for quantities, null for absent fields. "
        "Do not infer pack sizes or prices from other products. "
        "Ignore VAT summary and delivery lines. Preserve item descriptions exactly."
    )}]
    for image in _images(path):
        content.append({"type": "image_url", "image_url": {"url": _data_url(image)}})
    extracted = _ask_model([
        {"role": "system", "content": "You extract purchase invoice data precisely. Output valid JSON only."},
        {"role": "user", "content": content}
    ])
    (output_dir / (path.stem + "_extracted.json")).write_text(
        json.dumps(extracted, indent=2), encoding="utf-8")
    items = extracted.get("items", [])
    if not isinstance(items, list) or not items:
        raise RuntimeError("No invoice lines extracted")
    # Match only a verified existing product mapping; no speculative barcode assignment.
    by_barcode = {r["iposg_barcode"].strip(): r for r in catalog
                  if r.get("iposg_barcode", "").strip()}
    by_code = {r["booker_code"].strip(): r for r in catalog
               if r.get("booker_code", "").strip()}
    matched = []
    unresolved = []
    for item in items:
        if not isinstance(item, dict):
            continue
        code = str(item.get("supplier_code") or "").strip()
        barcode = str(item.get("barcode") or "").strip()
        row = by_barcode.get(barcode) or by_code.get(code)
        if not row:
            unresolved.append({"reason": "No verified product mapping", **item})
            continue
        try:
            cases = float(item.get("cases") or 0)
            invoice_pack = float(item.get("units_per_case") or 0)
            catalog_pack = float(row["units_per_case"])
            loose = float(item.get("loose_units") or 0)
            if invoice_pack and invoice_pack != catalog_pack:
                raise ValueError("Invoice pack differs from catalog")
            units = cases * catalog_pack + loose
            if units <= 0 or not units.is_integer():
                raise ValueError("Invalid or missing purchased quantity")
            if not row.get("booker_code"):
                raise ValueError("Mapping lacks product code")
            matched.append({"booker_code": row["booker_code"],
                            "cases": str(int(units / catalog_pack))})
            if loose or units % catalog_pack:
                raise ValueError("Loose units require direct unit quantity support")
        except (ValueError, TypeError, KeyError) as exc:
            if matched and matched[-1].get("booker_code") == row.get("booker_code"):
                matched.pop()
            unresolved.append({"reason": str(exc), **item})
    (output_dir / (path.stem + "_unresolved.json")).write_text(
        json.dumps(unresolved, indent=2), encoding="utf-8")
    if unresolved:
        raise RuntimeError(
            f"{len(unresolved)} invoice lines cannot be safely mapped. "
            f"Details: {output_dir / (path.stem + '_unresolved.json')}. "
            "No stock has been changed.")
    if not matched:
        raise RuntimeError("No verified products to update")
    # Aggregate repeated invoice lines before the stock updater deduplicates by product.
    totals = {}
    for row in matched:
        code = row["booker_code"]
        totals[code] = totals.get(code, 0) + int(row["cases"])
    matched = [{"booker_code": code, "cases": str(qty)} for code, qty in totals.items()]
    result = output_dir / (path.stem + "_verified.csv")
    with result.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["booker_code", "cases"])
        writer.writeheader()
        writer.writerows(matched)
    return result

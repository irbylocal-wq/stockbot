import os, json, base64, urllib.request, io
from pathlib import Path
from PIL import Image
def extract(path):
    path=Path(path)
    content=[{"type":"text","text":"Read EVERY purchased line item in this invoice from any supplier. Return JSON object with items array. Each item has description, barcode, supplier_code, cases, units_per_case, loose_units, case_price, total_price. Use null when not shown; don't fabricate barcodes, prices or pack sizes. Numbers must be numbers. Ignore delivery and VAT summary."}]
    if path.suffix.lower()==".pdf":
        content.append({"type":"document","source":{"type":"base64","media_type":"application/pdf","data":base64.b64encode(path.read_bytes()).decode()}})
    else:
        im=Image.open(path).convert("RGB");im.thumbnail((1800,1800))
        out=io.BytesIO();im.save(out,format="JPEG",quality=85)
        content.append({"type":"image","source":{"type":"base64","media_type":"image/jpeg","data":base64.b64encode(out.getvalue()).decode()}})
    req=urllib.request.Request("https://api.anthropic.com/v1/messages",data=json.dumps({"model":os.getenv("ANTHROPIC_MODEL","claude-sonnet-4-5"),"max_tokens":8192,"messages":[{"role":"user","content":content}]}).encode(),headers={"x-api-key":os.environ["ANTHROPIC_API_KEY"],"anthropic-version":"2023-06-01","content-type":"application/json"})
    with urllib.request.urlopen(req,timeout=180) as response: result=json.load(response)
    raw="\n".join(x.get("text","") for x in result["content"] if x.get("type")=="text").strip()
    if raw.startswith("```"): raw=raw.split("\n",1)[1].rsplit("```",1)[0]
    parsed=json.loads(raw)
    if not isinstance(parsed.get("items"),list) or not parsed["items"]: raise ValueError("No invoice lines extracted")
    return parsed["items"]

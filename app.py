import os, json, uuid, base64, secrets, urllib.request
from flask import Flask, request, jsonify, render_template_string
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 7 * 1024 * 1024
def redis(args):
    req = urllib.request.Request(os.environ["UPSTASH_REDIS_REST_URL"].rstrip("/"),
        data=json.dumps(args).encode(), headers={"Authorization":"Bearer "+os.environ["UPSTASH_REDIS_REST_TOKEN"],"Content-Type":"application/json"})
    with urllib.request.urlopen(req,timeout=20) as response:
        result=json.load(response)
    if result.get("error"): raise RuntimeError(result["error"])
    return result.get("result")
def authorized():
    key=os.environ.get("STOCKBOT_ADMIN_TOKEN","")
    supplied=request.headers.get("X-Stockbot-Token") or request.form.get("token","")
    return bool(key) and secrets.compare_digest(key,supplied)
PAGE="""<!doctype html><html><meta name="viewport" content="width=device-width,initial-scale=1"><title>iPOSG Stock Bot</title><style>body{font:16px system-ui;background:#f2f5fa;margin:40px auto;max-width:680px;padding:20px}section{background:white;border-radius:16px;padding:26px;margin:20px 0}input,button{display:block;padding:12px;margin:12px 0;max-width:100%}button{background:#17366c;color:white;border:0;border-radius:7px}</style><h1>iPOSG Stock Bot</h1><section><h2>Process any supplier invoice</h2><form action="/api/upload" method="post" enctype="multipart/form-data"><input type="password" name="token" placeholder="Admin token" required><input type="file" name="invoice" accept=".pdf,.png,.jpg,.jpeg,.webp" required><button>Upload and process automatically</button></form></section><section><h2>Check job status</h2><input id="id" placeholder="Job ID"><input id="key" type="password" placeholder="Admin token"><button onclick="fetch('/api/jobs/'+document.getElementById('id').value,{headers:{'X-Stockbot-Token':document.getElementById('key').value}}).then(r=>r.json()).then(v=>document.getElementById('out').textContent=JSON.stringify(v,null,2))">Check</button><pre id="out"></pre></section></html>"""
@app.get("/")
def home(): return PAGE
@app.get("/api/health")
def health(): return jsonify(web="online",queue="configured" if os.getenv("UPSTASH_REDIS_REST_URL") else "missing")
@app.post("/api/upload")
def upload():
    if not authorized(): return jsonify(error="Unauthorized"),401
    f=request.files.get("invoice")
    if not f or not f.filename: return jsonify(error="Invoice missing"),400
    ext=os.path.splitext(f.filename)[1].lower()
    if ext not in (".pdf",".png",".jpg",".jpeg",".webp"): return jsonify(error="Unsupported file"),400
    data=f.read()
    if not data: return jsonify(error="Empty invoice"),400
    job=uuid.uuid4().hex
    try:
        redis(["SET","stockbot:job:"+job,json.dumps({"status":"queued","filename":f.filename}),"EX",604800])
        redis(["LPUSH","stockbot:queue",json.dumps({"id":job,"ext":ext,"filename":f.filename,"data":base64.b64encode(data).decode()})])
    except Exception:
        app.logger.exception("queue unavailable")
        return jsonify(error="Queue unavailable"),503
    return jsonify(id=job,status="queued"),202
@app.get("/api/jobs/<job>")
def status(job):
    if not authorized(): return jsonify(error="Unauthorized"),401
    if len(job)!=32 or any(c not in "0123456789abcdef" for c in job): return jsonify(error="Invalid job"),400
    try:
        data=redis(["GET","stockbot:job:"+job])
        return (jsonify(json.loads(data)),200) if data else (jsonify(error="Not found"),404)
    except Exception: return jsonify(error="Queue unavailable"),503

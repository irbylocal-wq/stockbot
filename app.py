"""Invoice upload dashboard. Runs automation only on a persistent, configured host."""
import json
import os
import secrets
import sqlite3
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from flask import Flask, abort, jsonify, redirect, render_template_string, request, send_file, url_for
from werkzeug.utils import secure_filename

ROOT = Path(__file__).resolve().parent
DATA = Path(os.getenv("STOCKBOT_DATA_DIR", str(ROOT / "runtime"))).resolve()
IS_VERCEL = bool(os.getenv("VERCEL"))
ALLOWED = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".csv"}
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024
app.secret_key = os.getenv("FLASK_SECRET_KEY", secrets.token_hex(32))
LOCK = threading.Lock()

def db():
    DATA.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATA / "jobs.sqlite", timeout=20)
    conn.execute("""CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY, filename TEXT, status TEXT,
        created TEXT, updated TEXT, output TEXT, error TEXT
    )""")
    conn.commit()
    return conn

def now():
    return datetime.now(timezone.utc).isoformat()

def authorized():
    token = os.getenv("STOCKBOT_ADMIN_TOKEN", "")
    return bool(token) and secrets.compare_digest(
        request.headers.get("X-Stockbot-Token", "") or request.form.get("token", "")
        or request.args.get("token", ""), token
    )

def require_auth():
    if not authorized():
        abort(401, "Set STOCKBOT_ADMIN_TOKEN and provide it as X-Stockbot-Token or form token.")

def set_job(job_id, status, output="", error=""):
    with LOCK:
        conn = db()
        conn.execute("UPDATE jobs SET status=?,updated=?,output=?,error=? WHERE id=?",
                     (status, now(), output[-20000:], error[-4000:], job_id))
        conn.commit()
        conn.close()

def process(job_id, filepath):
    set_job(job_id, "running")
    try:
        # An invoice is extracted by the AI parser when configured; otherwise
        # the existing bot handles CSV and conservative OCR fallback.
        invoice_path = filepath
        if os.getenv("OPENAI_API_KEY") and filepath.suffix.lower() != ".csv":
            from invoice_ai import extract_invoice
            invoice_path = extract_invoice(filepath, DATA / "extracted")
        command = [sys.executable, str(ROOT / "bot.py"), "--invoice", str(invoice_path), "--live"]
        completed = subprocess.run(command, cwd=str(ROOT), text=True,
                                   capture_output=True, timeout=1800)
        if completed.returncode:
            set_job(job_id, "failed", completed.stdout, completed.stderr)
        else:
            set_job(job_id, "completed", completed.stdout)
    except subprocess.TimeoutExpired:
        set_job(job_id, "needs_inspection", error="Worker timed out. Check iPOSG stock before retrying.")
    except Exception as exc:
        set_job(job_id, "failed", error=str(exc))

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>iPOSG Invoice Automation</title>
<style>body{font:16px system-ui;background:#f4f6fa;color:#182233;margin:0}
main{max-width:760px;margin:48px auto;padding:24px}section{background:white;border-radius:16px;
padding:28px;margin:18px 0;box-shadow:0 4px 24px #1220330d}
h1{font-size:30px}input,button{font:inherit;padding:12px;margin:8px 0}
input[type=file]{max-width:95%}input[type=password]{width:90%;box-sizing:border-box}
button{background:#172d57;color:white;border:0;border-radius:8px;cursor:pointer}
small{color:#65748a}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style></head>
<body><main><h1>iPOSG Stock Bot</h1><p>Supplier-independent invoice upload</p>
<section><h2>Upload invoice</h2><form method="post" action="/upload" enctype="multipart/form-data">
<label>Admin token</label><input type="password" name="token" required autocomplete="off">
<label>Invoice (PDF, JPG, PNG, WebP or CSV)</label><input type="file" name="invoice"
accept=".pdf,.png,.jpg,.jpeg,.webp,.csv" required>
<button type="submit">Process invoice automatically</button></form>
<p><small>Each item is matched and processed by the configured local worker. No per-item approval.</small></p></section>
<section><h2>Execution environment</h2><p>{{ message }}</p>
<p><small>Automation requires a persistent Windows/Linux worker with iPOSG browser login.
Vercel serverless cannot reuse a local Playwright login session or guarantee long-running jobs.</small></p></section>
</main></body></html>"""

@app.get("/")
def home():
    return render_template_string(PAGE, message=(
        "Vercel dashboard is online. Stock processing is disabled here until a remote worker and persistent job storage are configured."
        if IS_VERCEL else "Local automation worker available. Log into iPOSG on this computer before processing."
    ))

@app.get("/api/health")
def health():
    return jsonify(service="iposg-stockbot", web="online",
                   automation="remote_worker_required" if IS_VERCEL else "local_worker_available")

@app.post("/upload")
def upload():
    require_auth()
    if IS_VERCEL:
        return jsonify(error="Cannot run long-lived Playwright worker on Vercel. Run the dashboard on a persistent host or connect a remote worker."), 503
    file = request.files.get("invoice")
    if not file or not file.filename:
        abort(400, "Invoice required")
    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED:
        abort(400, "Unsupported file type")
    job_id = uuid.uuid4().hex
    folder = DATA / "uploads"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (job_id + suffix)
    file.save(path)
    conn = db()
    conn.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?)",
                 (job_id, secure_filename(file.filename), "queued", now(), now(), "", ""))
    conn.commit()
    conn.close()
    threading.Thread(target=process, args=(job_id, path), daemon=True).start()
    return jsonify(job_id=job_id, status="queued",
                   status_url=url_for("job_status", job_id=job_id)), 202

@app.get("/api/jobs/<job_id>")
def job_status(job_id):
    require_auth()
    conn = db()
    row = conn.execute("SELECT id,filename,status,created,updated,output,error FROM jobs WHERE id=?",
                       (job_id,)).fetchone()
    conn.close()
    if not row:
        abort(404)
    return jsonify(dict(zip(("id","filename","status","created","updated","output","error"), row)))

if __name__ == "__main__":
    if not os.getenv("STOCKBOT_ADMIN_TOKEN"):
        raise SystemExit("Set STOCKBOT_ADMIN_TOKEN before starting.")
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5000")), debug=False)

import os, json, base64, urllib.request, time, tempfile, csv, subprocess, sys
from pathlib import Path
from invoice_ai import extract
ROOT=Path(__file__).resolve().parent
def redis(args):
    req=urllib.request.Request(os.environ["UPSTASH_REDIS_REST_URL"].rstrip("/"),data=json.dumps(args).encode(),headers={"Authorization":"Bearer "+os.environ["UPSTASH_REDIS_REST_TOKEN"],"Content-Type":"application/json"})
    with urllib.request.urlopen(req,timeout=25) as response: obj=json.load(response)
    if obj.get("error"): raise RuntimeError(obj["error"])
    return obj.get("result")
def status(job,kind,**info): redis(["SET","stockbot:job:"+job,json.dumps({"status":kind,**info}),"EX",604800])
def process(item):
    job=item["id"]
    try:
        status(job,"processing")
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/("invoice"+item["ext"]);path.write_bytes(base64.b64decode(item["data"]))
            lines=extract(path)
            with (ROOT/"products.csv").open(newline="",encoding="utf-8-sig") as f: catalog=list(csv.DictReader(f))
            by_barcode={r["iposg_barcode"].strip():r for r in catalog if r.get("iposg_barcode","").strip()}
            by_code={r["booker_code"].strip():r for r in catalog if r.get("booker_code","").strip()}
            totals={}; unresolved=[]
            for line in lines:
                try:
                    row=by_barcode.get(str(line.get("barcode") or "").strip()) or by_code.get(str(line.get("supplier_code") or "").strip())
                    if not row: raise ValueError("No verified iPOSG mapping")
                    if not row.get("iposg_barcode","").strip(): raise ValueError("Missing verified barcode")
                    pack=int(row["units_per_case"]); invoice_pack=line.get("units_per_case")
                    if invoice_pack is not None and int(invoice_pack)!=pack: raise ValueError("Case quantity differs")
                    cases=float(line.get("cases") or 0)
                    loose=float(line.get("loose_units") or 0)
                    if cases<=0 or not cases.is_integer() or loose: raise ValueError("Unverified case or loose quantity")
                    code=row["booker_code"]
                    totals[code]=totals.get(code,0)+int(cases)
                except (ValueError,TypeError,KeyError) as e: unresolved.append({"product":line.get("description"),"reason":str(e)})
            if unresolved:
                status(job,"needs_mapping",unresolved=unresolved,matched=len(totals));return
            if not totals: raise ValueError("No matched products")
            target=Path(temp)/"verified.csv"
            with target.open("w",newline="",encoding="utf-8") as f:
                writer=csv.DictWriter(f,fieldnames=["booker_code","cases"]);writer.writeheader()
                writer.writerows({"booker_code":code,"cases":qty} for code,qty in totals.items())
            result=subprocess.run([sys.executable,str(ROOT/"bot.py"),"--invoice",str(target),"--live"],cwd=ROOT,text=True,capture_output=True,timeout=1800)
            if result.returncode: status(job,"failed",error=result.stderr[-2500:],log=result.stdout[-2500:])
            else: status(job,"completed",log=result.stdout[-5000:])
    except subprocess.TimeoutExpired: status(job,"needs_inspection",error="Stock update timed out; check iPOSG before retrying")
    except Exception as e: status(job,"failed",error=str(e))
def main():
    for key in ("ANTHROPIC_API_KEY","UPSTASH_REDIS_REST_URL","UPSTASH_REDIS_REST_TOKEN"):
        if not os.getenv(key): raise SystemExit("Missing "+key)
    print("iPOSG worker running")
    while True:
        try:
            item=redis(["RPOP","stockbot:queue"])
            if item: process(json.loads(item))
            else: time.sleep(5)
        except KeyboardInterrupt: raise
        except Exception as e: print("Worker error:",e,flush=True);time.sleep(15)
if __name__=="__main__":main()

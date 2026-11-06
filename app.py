"""
Smart Print Hub - a print-shop counter dashboard (single file).

Run:
    pip install flask qrcode[pil]      # qrcode is optional (needed for QR pages)
    python smart_print_hub.py

Then open:
    http://localhost:5000/            -> shop dashboard (counter)
    http://localhost:5000/u/<shop>    -> customer upload page (share link / QR)

Customer link: same Wi-Fi works automatically. For customers on mobile data,
expose it with a tunnel and set PUBLIC_URL, e.g.
    cloudflared tunnel --url http://localhost:5000
    set PUBLIC_URL=https://xxxx.trycloudflare.com   (Windows)
    export PUBLIC_URL=https://xxxx.trycloudflare.com (Linux/macOS)

Printing:
    Windows -> os.startfile(path, "print")
    Linux/macOS -> `lp <file>` (CUPS)
"""
import hmac
import io
import os
import platform
import socket
import subprocess
import threading
import time
import uuid

from flask import (Flask, abort, jsonify, redirect, render_template_string,
                   request, send_file, session, url_for)
from werkzeug.utils import secure_filename

try:
    import qrcode
except ImportError:  # QR is optional
    qrcode = None

# ----------------------------------------------------------------- config --
SHOP_NAME = "Subajit"
SHOP_SLUG = "subajit"
USER_ID = os.environ.get("HUB_USER", "subhajit")
PASSWORD = os.environ.get("HUB_PASSWORD", "ARKCAONE")
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
ALLOWED = {"pdf", "png", "jpg", "jpeg", "doc", "docx", "txt"}
MAX_MB = 25

os.makedirs(UPLOAD_DIR, exist_ok=True)
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me-in-production")
app.config["MAX_CONTENT_LENGTH"] = MAX_MB * 1024 * 1024

# Shared state (in memory). status: pending | printing | printed | failed
JOBS = []
LOCK = threading.Lock()
SETTINGS = {
    "auto_open": True,       # open the file preview when it arrives
    "auto_print": True,      # send to printer automatically
    "permission": False,     # if True, jobs wait for manual approval
    "auto_delete_min": 5,    # delete files this many minutes after printing
    "queue_active": True,    # pause / resume the queue
}
WALKINS = set()              # unique customer ids seen


# ---------------------------------------------------------------- printing --
def print_file(path):
    """Send a file to the default printer. Returns True on success."""
    try:
        if platform.system() == "Windows":
            os.startfile(path, "print")  # noqa: S606
            return True
        subprocess.run(["lp", path], check=True, timeout=30)
        return True
    except Exception as exc:  # noqa: BLE001
        print("print failed:", exc)
        return False


def worker():
    """Background loop: prints pending jobs and removes old ones."""
    while True:
        time.sleep(1.5)
        now = time.time()
        with LOCK:
            for job in JOBS:
                if (job["status"] == "pending" and SETTINGS["queue_active"]
                        and SETTINGS["auto_print"] and not SETTINGS["permission"]):
                    job["status"] = "printing"
                    ok = print_file(job["path"])
                    job["status"] = "printed" if ok else "failed"
                    job["done_at"] = now

            minutes = SETTINGS["auto_delete_min"]
            if minutes:
                for job in list(JOBS):
                    if job["done_at"] and now - job["done_at"] > minutes * 60:
                        remove_job(job)


def remove_job(job):
    try:
        os.remove(job["path"])
    except OSError:
        pass
    if job in JOBS:
        JOBS.remove(job)


threading.Thread(target=worker, daemon=True).start()


# ------------------------------------------------------------------- utils --
def allowed(name):
    return "." in name and name.rsplit(".", 1)[1].lower() in ALLOWED


def save_upload(file, customer):
    name = secure_filename(file.filename or "")
    if not name or not allowed(name):
        return False
    job_id = uuid.uuid4().hex[:10]
    path = os.path.join(UPLOAD_DIR, f"{job_id}_{name}")
    file.save(path)
    with LOCK:
        WALKINS.add(customer)
        JOBS.insert(0, {"id": job_id, "name": name, "path": path,
                        "customer": customer, "status": "pending",
                        "at": time.time(), "done_at": None})
    return True


def login_required(fn):
    from functools import wraps

    @wraps(fn)
    def wrapper(*a, **kw):
        if not session.get("ok"):
            return redirect(url_for("login"))
        return fn(*a, **kw)
    return wrapper


def lan_ip():
    """This computer's address on the local Wi-Fi/LAN."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def customer_link():
    """Link customers open. Set PUBLIC_URL to your internet address
    (e.g. an ngrok / Cloudflare tunnel or hosting URL); otherwise the
    Wi-Fi address is used, which works for phones on the same network."""
    base = os.environ.get("PUBLIC_URL", "").rstrip("/")
    if not base:
        base = f"http://{lan_ip()}:5000"
    return f"{base}/u/{SHOP_SLUG}"


# ------------------------------------------------------------------ routes --
@app.route("/login", methods=["GET", "POST"])
def login():
    err = ""
    if request.method == "POST":
        uid = request.form.get("userid", "").strip().lower()
        if (hmac.compare_digest(uid, USER_ID.lower()) and
                hmac.compare_digest(request.form.get("password", ""), PASSWORD)):
            session["ok"] = True
            return redirect(url_for("dashboard"))
        err = "Wrong user ID or password."
    return render_template_string(LOGIN_HTML, err=err)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def dashboard():
    return render_template_string(DASH_HTML, shop=SHOP_NAME,
                                  link=customer_link())


@app.route("/api/state")
@login_required
def api_state():
    with LOCK:
        jobs = [{k: j[k] for k in ("id", "name", "status", "at")} for j in JOBS]
        stats = {
            "walkins": len(WALKINS),
            "pending": sum(j["status"] in ("pending", "printing") for j in JOBS),
            "printed": sum(j["status"] == "printed" for j in JOBS),
            "failed": sum(j["status"] == "failed" for j in JOBS),
        }
        return jsonify(jobs=jobs, stats=stats, settings=SETTINGS)


@app.route("/api/settings", methods=["POST"])
@login_required
def api_settings():
    data = request.get_json(force=True)
    with LOCK:
        for key in SETTINGS:
            if key in data:
                SETTINGS[key] = (int(data[key]) if key == "auto_delete_min"
                                 else bool(data[key]))
    return jsonify(ok=True)


@app.route("/api/upload", methods=["POST"])
@login_required
def api_upload():
    files = request.files.getlist("file")
    n = sum(save_upload(f, "counter") for f in files)
    return jsonify(saved=n)


@app.route("/api/job/<job_id>/<action>", methods=["POST"])
@login_required
def api_job(job_id, action):
    with LOCK:
        job = next((j for j in JOBS if j["id"] == job_id), None)
        if not job:
            abort(404)
        if action in ("print", "retry"):
            job["status"] = "printing"
            ok = print_file(job["path"])
            job["status"] = "printed" if ok else "failed"
            job["done_at"] = time.time()
        elif action == "reject":
            job["status"] = "failed"
            job["done_at"] = time.time()
        elif action == "delete":
            remove_job(job)
    return jsonify(ok=True)


@app.route("/api/clear", methods=["POST"])
@login_required
def api_clear():
    with LOCK:
        for job in list(JOBS):
            if job["status"] in ("printed", "failed"):
                remove_job(job)
    return jsonify(ok=True)


@app.route("/file/<job_id>")
@login_required
def file_view(job_id):
    with LOCK:
        job = next((j for j in JOBS if j["id"] == job_id), None)
    if not job:
        abort(404)
    return send_file(job["path"], download_name=job["name"])


@app.route("/qr.png")
@login_required
def qr_png():
    if qrcode is None:
        abort(501, "Install qrcode: pip install qrcode[pil]")
    img = qrcode.make(customer_link())
    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")


@app.route("/poster")
@login_required
def poster():
    return render_template_string(POSTER_HTML, shop=SHOP_NAME,
                                  link=customer_link())


@app.route("/u/<slug>", methods=["GET", "POST"])
def customer(slug):
    if slug != SHOP_SLUG:
        abort(404)
    msg = ""
    if request.method == "POST":
        cid = request.cookies.get("cid") or uuid.uuid4().hex
        n = sum(save_upload(f, cid) for f in request.files.getlist("file"))
        msg = (f"{n} file(s) sent to {SHOP_NAME}." if n
               else "No valid file. Allowed: " + ", ".join(sorted(ALLOWED)))
        resp = app.make_response(render_template_string(
            CUSTOMER_HTML, shop=SHOP_NAME, msg=msg, mb=MAX_MB))
        resp.set_cookie("cid", cid, max_age=86400)
        return resp
    return render_template_string(CUSTOMER_HTML, shop=SHOP_NAME, msg=msg,
                                  mb=MAX_MB)


# --------------------------------------------------------------- templates --
BASE_CSS = """
*{box-sizing:border-box}
body{margin:0;font-family:Inter,system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
 background:#f8fafc;color:#1e293b;font-size:14px}
.card{background:#fff;border:1px solid #e2e8f0;border-radius:14px;padding:16px;
 box-shadow:0 1px 2px rgba(0,0,0,.04)}
.btn{display:inline-flex;align-items:center;gap:6px;border:1px solid #e2e8f0;
 background:#f8fafc;color:#334155;padding:8px 12px;border-radius:8px;
 font-size:12px;font-weight:600;cursor:pointer;text-decoration:none}
.btn.primary{background:#4f46e5;border-color:#4f46e5;color:#fff}
.btn.danger{background:#fef2f2;border-color:#fecaca;color:#dc2626}
.btn.ok{background:#ecfdf5;border-color:#a7f3d0;color:#059669}
.btn:hover{filter:brightness(.97)}
"""

LOGIN_HTML = """<!doctype html><meta name=viewport content="width=device-width,initial-scale=1">
<title>Login | Smart Print Hub</title><style>""" + BASE_CSS + """
.wrap{max-width:340px;margin:12vh auto;padding:0 16px}
input{width:100%;padding:10px;border:1px solid #cbd5e1;border-radius:8px;margin:10px 0}
</style><div class=wrap><div class=card>
<h2 style="margin:0">Smart Print Hub</h2><p style="color:#64748b">Counter login</p>
<form method=post><input name=userid placeholder="User ID" autocomplete=username autofocus>
<input type=password name=password placeholder=Password autocomplete=current-password>
<button class="btn primary" style="width:100%;justify-content:center">Log in</button>
<p style="color:#dc2626;font-size:12px">{{err}}</p></form></div></div>"""

DASH_HTML = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>{{shop}} | Smart Print Hub</title><style>""" + BASE_CSS + """
header{background:#fff;border-bottom:1px solid #e2e8f0;padding:10px 16px;
 display:flex;justify-content:space-between;align-items:center}
.logo{display:flex;gap:10px;align-items:center;font-weight:700}
.logo i{background:#2563eb;color:#fff;font-style:normal;font-size:11px;
 padding:7px 8px;border-radius:8px}
main{max-width:760px;margin:0 auto;padding:16px;display:grid;gap:14px}
.row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.shop{display:flex;gap:12px;align-items:center;margin-bottom:12px}
.shop .ic{width:42px;height:42px;border-radius:10px;background:#eef2ff;
 display:grid;place-items:center;font-size:20px}
.live{font-size:10px;color:#059669;background:#ecfdf5;border:1px solid #bbf7d0;
 padding:2px 8px;border-radius:99px;font-weight:600}
.live.paused{color:#d97706;background:#fffbeb;border-color:#fde68a}
.tg{display:flex;align-items:center;gap:8px;font-size:12px;font-weight:600;
 padding-right:12px;border-right:1px solid #e2e8f0}
.sw{position:relative;width:38px;height:22px;background:#cbd5e1;border-radius:99px;
 cursor:pointer;transition:.2s;flex:none}
.sw:after{content:"";position:absolute;top:2px;left:2px;width:18px;height:18px;
 background:#fff;border-radius:50%;transition:.2s}
.sw.on{background:#10b981}.sw.on:after{left:18px}
select{border:1px solid #e2e8f0;border-radius:8px;padding:6px;font-size:12px;
 background:#f8fafc}
.stats{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.stat{display:flex;gap:12px;align-items:center;padding:14px}
.stat .ic{width:36px;height:36px;border-radius:9px;display:grid;place-items:center}
.stat b{font-size:20px;display:block;line-height:1}
.stat small{font-size:9px;color:#64748b;font-weight:700;letter-spacing:.04em}
h3{margin:4px 0 0;font-size:14px}
.empty{text-align:center;padding:36px 12px;color:#94a3b8;border:1px dashed #cbd5e1;
 background:#fff;border-radius:14px}
.empty b{display:block;color:#64748b;margin:8px 0 4px}
.job{display:flex;justify-content:space-between;align-items:center;gap:10px;
 padding:12px;margin-bottom:8px}
.job .n{font-weight:600;word-break:break-all}
.job .s{font-size:11px;color:#64748b}
.tag{font-size:10px;font-weight:700;padding:2px 8px;border-radius:99px}
.t-pending,.t-printing{background:#fef3c7;color:#b45309}
.t-printed{background:#d1fae5;color:#047857}
.t-failed{background:#fee2e2;color:#b91c1c}
</style></head><body>
<header><div class=logo><i>SP</i><div>Smart Print Hub<br>
<small style="font-weight:400;color:#94a3b8;font-size:9px">smartprint.local</small></div></div>
<div class=row><a class=btn href="#" onclick="return false">&#8962; Home Page</a>
<a class="btn danger" href="/logout">Logout</a></div></header>
<main>
 <div class=card>
  <div class=shop><div class=ic>&#127978;</div><div>
   <div style="font-size:16px;font-weight:700">{{shop}}</div>
   <span id=live class=live>&#9679; Live Counter Queue Active</span></div></div>
  <div class=row>
   <label class="btn primary">&#9729; Direct Upload File
    <input id=up type=file multiple hidden></label>
   <button class=btn onclick="copyLink()">&#10697; Copy Customer Link</button>
   <a class=btn target=_blank href="/qr.png">&#9638; View / Download QR</a>
   <a class=btn target=_blank href="/poster">&#9113; QR Poster (A4)</a>
  </div>
 </div>

 <div class=card>
  <div class=row style="gap:14px">
   <div class=tg>Auto-Open Layout <div class=sw data-k=auto_open></div></div>
   <div class=tg>Auto-Print Mode <div class=sw data-k=auto_print></div></div>
   <div class=tg>Permission Mode <div class=sw data-k=permission></div></div>
   <div class=tg style="border:0">Auto-Delete
    <select id=del><option value=0>Never</option><option value=1>1 Minute</option>
    <option value=5>5 Minutes</option><option value=15>15 Minutes</option>
    <option value=60>1 Hour</option></select></div>
  </div>
  <div class=row style="margin-top:12px">
   <button id=qbtn class="btn ok" onclick="toggleQueue()">&#10074;&#10074; Queue Active</button>
   <button class=btn onclick="clearDone()">&#129529; Clear Completed Jobs</button>
  </div>
 </div>

 <div class=stats>
  <div class="card stat"><div class=ic style="background:#e0e7ff">&#128101;</div>
   <div><b id=s-walkins>0</b><small>WALK-IN CUSTOMERS</small></div></div>
  <div class="card stat"><div class=ic style="background:#fef3c7">&#8987;</div>
   <div><b id=s-pending>0</b><small>PENDING FILES</small></div></div>
  <div class="card stat"><div class=ic style="background:#d1fae5">&#10003;</div>
   <div><b id=s-printed>0</b><small>PRINTED FILES</small></div></div>
  <div class="card stat"><div class=ic style="background:#fee2e2">&#10005;</div>
   <div><b id=s-failed>0</b><small>REJECTED / FAILED</small></div></div>
 </div>

 <div class=row style="justify-content:space-between">
  <h3>&#9776; Live Document Print Queue</h3>
  <button class=btn onclick="load()">&#8635; Refresh</button></div>
 <div id=queue></div>
</main>
<script>
const $=s=>document.querySelector(s);
let S={};
const post=(u,d)=>fetch(u,{method:"POST",headers:{"Content-Type":"application/json"},
  body:JSON.stringify(d||{})});

async function load(){
  const r=await fetch("/api/state"); if(r.status!=200) return location="/login";
  const d=await r.json(); S=d.settings;
  for(const k in d.stats) $("#s-"+k).textContent=d.stats[k];
  document.querySelectorAll(".sw").forEach(e=>e.classList.toggle("on",S[e.dataset.k]));
  $("#del").value=S.auto_delete_min;
  $("#qbtn").innerHTML=S.queue_active?"&#10074;&#10074; Queue Active":"&#9654; Queue Paused";
  $("#live").className="live"+(S.queue_active?"":" paused");
  $("#live").innerHTML=S.queue_active?"&#9679; Live Counter Queue Active":"&#9679; Queue Paused";
  render(d.jobs);
}
let seen=new Set(), first=true;
function render(jobs){
  const q=$("#queue");
  if(!jobs.length){
    q.innerHTML=`<div class=empty><div style="font-size:30px">&#128194;</div>
    <b>No Customer Uploads Yet</b>Share your customer link or show your counter QR Code to receive instant print files.</div>`;
    return;
  }
  q.innerHTML=jobs.map(j=>`<div class="card job"><div><div class=n>${esc(j.name)}</div>
   <div class=s>${new Date(j.at*1000).toLocaleTimeString()}</div></div>
   <div class=row><span class="tag t-${j.status}">${j.status}</span>
   <a class=btn target=_blank href="/file/${j.id}">Open</a>
   ${j.status=="pending"?`<button class="btn primary" onclick="act('${j.id}','print')">Print</button>
     <button class="btn danger" onclick="act('${j.id}','reject')">Reject</button>`:""}
   ${j.status=="failed"?`<button class=btn onclick="act('${j.id}','retry')">Retry</button>`:""}
   <button class=btn onclick="act('${j.id}','delete')">&#128465;</button></div></div>`).join("");
  if(S.auto_open&&!first) jobs.filter(j=>!seen.has(j.id)).forEach(j=>window.open("/file/"+j.id,"_blank"));
  jobs.forEach(j=>seen.add(j.id)); first=false;
}
const esc=s=>s.replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const act=(id,a)=>post(`/api/job/${id}/${a}`).then(load);
const clearDone=()=>post("/api/clear").then(load);
const toggleQueue=()=>post("/api/settings",{queue_active:!S.queue_active}).then(load);
document.querySelectorAll(".sw").forEach(e=>e.onclick=()=>
  post("/api/settings",{[e.dataset.k]:!S[e.dataset.k]}).then(load));
$("#del").onchange=e=>post("/api/settings",{auto_delete_min:+e.target.value}).then(load);
$("#up").onchange=async e=>{
  const f=new FormData(); [...e.target.files].forEach(x=>f.append("file",x));
  await fetch("/api/upload",{method:"POST",body:f}); e.target.value=""; load();
};
function copyLink(){navigator.clipboard.writeText("{{link}}");alert("Customer link copied");}
load(); setInterval(load,3000);
</script></body></html>"""

CUSTOMER_HTML = """<!doctype html><meta name=viewport content="width=device-width,initial-scale=1">
<title>Send files to {{shop}}</title><style>""" + BASE_CSS + """
.wrap{max-width:420px;margin:8vh auto;padding:0 16px;text-align:center}
.drop{border:2px dashed #a5b4fc;border-radius:14px;padding:30px;margin:16px 0;
 background:#eef2ff;cursor:pointer;display:block}
</style><div class=wrap><div class=card>
<h2 style="margin:0">{{shop}}</h2>
<p style="color:#64748b">Upload a file and it prints at the counter.</p>
<form method=post enctype=multipart/form-data>
<label class=drop>Tap to choose files<input type=file name=file multiple hidden
 onchange="this.form.submit()"></label></form>
<small style="color:#94a3b8">PDF, images, Word, text. Max {{mb}} MB each.</small>
{% if msg %}<p style="color:#059669;font-weight:600">{{msg}}</p>{% endif %}
</div></div>"""

POSTER_HTML = """<!doctype html><title>QR Poster</title><style>
@page{size:A4;margin:0}
body{margin:0;font-family:system-ui,sans-serif;text-align:center}
.p{width:210mm;height:297mm;padding:30mm 20mm;box-sizing:border-box}
h1{font-size:54px;margin:0 0 8px}p{font-size:22px;color:#475569}
img{width:110mm;margin:20mm 0}
</style><div class=p><h1>{{shop}}</h1><p>Scan to send your file for printing</p>
<img src="/qr.png"><p style="font-size:14px">{{link}}</p></div>
<script>window.onload=()=>window.print()</script>"""


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)

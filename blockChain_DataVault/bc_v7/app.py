import os, csv, json, io, urllib.request
from functools import wraps
from datetime import datetime

from flask import (Flask, render_template, request, redirect, url_for,
                   session, flash, send_from_directory, abort, jsonify, send_file)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

import database as db
import ai_engine as ai
import report_export

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
AVATAR_FOLDER = os.path.join(BASE_DIR, "static", "uploads", "avatars")

app = Flask(__name__)
app.secret_key = "change-this-secret-key-in-production"
app.config["UPLOAD_FOLDER"]         = UPLOAD_FOLDER
app.config["AVATAR_FOLDER"]         = AVATAR_FOLDER
app.config["MAX_CONTENT_LENGTH"]    = 25 * 1024 * 1024

os.makedirs(AVATAR_FOLDER, exist_ok=True)

@app.template_filter("hms")
def format_duration(total_seconds):
    """Format a seconds count as e.g. '2h 14m' or '38m' or '12s'."""
    try:
        total_seconds = int(total_seconds or 0)
    except (TypeError, ValueError):
        return "0s"
    h, rem = divmod(total_seconds, 3600)
    m, s = divmod(rem, 60)
    if h: return f"{h}h {m}m"
    if m: return f"{m}m {s}s"
    return f"{s}s"

if not os.path.exists(db.DB_PATH):
    db.init_db()
else:
    db.migrate_db()

SAMPLE_DATASETS = [
    {"id":"sample_iris",      "name":"Iris Flower Dataset",  "description":"Sepal/petal measurements for 3 species", "filename":"sample_iris.csv"},
    {"id":"sample_titanic",   "name":"Titanic Passenger Data","description":"Survival data with demographics",          "filename":"sample_titanic.csv"},
    {"id":"sample_stocks",    "name":"Stock Market Data",     "description":"Daily OHLCV data – Apple & Google",       "filename":"sample_stocks.csv"},
    {"id":"sample_ecommerce", "name":"E-Commerce Orders",     "description":"Orders across regions, categories, pricing","filename":"sample_ecommerce.csv"},
]

ALLOWED_AVATAR = {"png","jpg","jpeg","gif","webp"}

# ──────────────────────────────────────────────────────────────────────
# Decorators
# ──────────────────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def w(*a,**k):
        if "user_id" not in session:
            flash("Please log in to continue.","warning")
            return redirect(url_for("login"))
        return f(*a,**k)
    return w

def admin_required(f):
    @wraps(f)
    def w(*a,**k):
        if "user_id" not in session:
            flash("Please log in to continue.","warning")
            return redirect(url_for("login"))
        if session.get("role")!="admin": abort(403)
        return f(*a,**k)
    return w

def user_required(f):
    @wraps(f)
    def w(*a,**k):
        if "user_id" not in session:
            flash("Please log in to continue.","warning")
            return redirect(url_for("login"))
        if session.get("role")!="user": abort(403)
        return f(*a,**k)
    return w

# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────
def current_notifications():
    conn = db.get_db()
    rows = conn.execute(
        "SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 8",
        (session.get("user_id"),)).fetchall()
    unread = conn.execute(
        "SELECT COUNT(*) c FROM notifications WHERE user_id=? AND is_read=0",
        (session.get("user_id"),)).fetchone()["c"]
    conn.close()
    return rows, unread

def read_csv(filepath, maxrows=500):
    try:
        with open(filepath, newline="", encoding="utf-8", errors="ignore") as f:
            reader = csv.DictReader(f)
            headers = list(reader.fieldnames or [])
            rows = []
            for i, r in enumerate(reader):
                if i >= maxrows: break
                rows.append(dict(r))
        return headers, rows
    except Exception:
        return [], []

def read_xlsx(filepath, maxrows=500):
    """Read the first sheet of an .xlsx/.xls workbook into (headers, rows),
    matching the dict-per-row shape that read_csv produces."""
    try:
        from openpyxl import load_workbook
        wb = load_workbook(filepath, read_only=True, data_only=True)
        ws = wb.active
        rows_iter = ws.iter_rows(values_only=True)
        try:
            header_row = next(rows_iter)
        except StopIteration:
            return [], []
        headers = [str(h) if h is not None else "" for h in header_row]
        rows = []
        for i, r in enumerate(rows_iter):
            if i >= maxrows: break
            row = {}
            for h, v in zip(headers, r):
                if v is None:
                    row[h] = ""
                elif isinstance(v, float) and v.is_integer():
                    row[h] = str(int(v))
                else:
                    row[h] = str(v)
            rows.append(row)
        wb.close()
        return headers, rows
    except Exception:
        return [], []

def read_json_dataset(filepath, maxrows=500):
    """Read a JSON file (array of objects, or a single object) into (headers, rows)."""
    try:
        with open(filepath, encoding="utf-8", errors="ignore") as f:
            parsed = json.load(f)
        if isinstance(parsed, list) and parsed:
            headers = list(parsed[0].keys())
            rows = parsed[:maxrows]
        elif isinstance(parsed, dict):
            headers = list(parsed.keys())
            rows = [parsed]
        else:
            return [], []
        return headers, rows
    except Exception:
        return [], []

def read_dataset_file(filepath, maxrows=500):
    """Dispatch to the right reader based on file extension, so CSV, Excel,
    and JSON uploads all render correctly wherever a dataset is loaded."""
    ext = filepath.rsplit(".", 1)[-1].lower() if "." in filepath else ""
    if ext in ("xlsx", "xls"):
        return read_xlsx(filepath, maxrows)
    if ext == "json":
        return read_json_dataset(filepath, maxrows)
    return read_csv(filepath, maxrows)

def load_dataset(ds_type, ds_id):
    """Return (headers, rows, name) for any dataset type."""
    if ds_type == "sample":
        s = next((x for x in SAMPLE_DATASETS if x["id"]==ds_id), None)
        if not s: return [], [], ""
        fp = os.path.join(UPLOAD_FOLDER, s["filename"])
        h, r = read_dataset_file(fp)
        return h, r, s["name"]
    elif ds_type in ("admin","user_upload"):
        conn = db.get_db()
        ds = conn.execute("SELECT * FROM datasets WHERE id=?", (ds_id,)).fetchone()
        conn.close()
        if not ds: return [], [], ""
        fp = os.path.join(UPLOAD_FOLDER, ds["filepath"])
        h, r = read_dataset_file(fp)
        return h, r, ds["name"]
    return [], [], ""

ACTIVE_PAGE_MAP = {
    "admin_datasets":"datasets","admin_requests":"requests",
    "admin_blockchain":"blockchain","admin_history":"history","admin_profile":"profile",
    "admin_user_activity":"user_activity","admin_user_activity_detail":"user_activity",
    "user_dataset_selection":"dataset_selection","user_dashboard":"dashboard",
    "user_chatbot":"chatbot","user_chatbot_session":"chatbot",
    "user_visualize":"visualize",
    "user_history":"history","user_my_requests":"my_requests",
    "user_profile":"profile","logout_page":"logout",
}

@app.context_processor
def inject_globals():
    notes, unread = ([], 0)
    if "user_id" in session:
        notes, unread = current_notifications()
    return dict(logged_in_name=session.get("name"),
                logged_in_role=session.get("role"),
                logged_in_avatar=session.get("avatar"),
                notifications=notes, unread_count=unread,
                active_page=ACTIVE_PAGE_MAP.get(request.endpoint,""))

# ──────────────────────────────────────────────────────────────────────
# Auth
# ──────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("admin_datasets") if session.get("role")=="admin" else url_for("user_dataset_selection"))
    return render_template("landing.html")

@app.route("/home")
def landing():
    if "user_id" in session:
        return redirect(url_for("admin_datasets") if session.get("role")=="admin" else url_for("user_dataset_selection"))
    return render_template("landing.html")

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method=="POST":
        email = request.form.get("email","").strip().lower()
        pw    = request.form.get("password","")
        role  = request.form.get("role","")
        conn  = db.get_db()
        user  = conn.execute("SELECT * FROM users WHERE email=? AND role=?",(email,role)).fetchone()
        conn.close()
        if user and check_password_hash(user["password_hash"],pw):
            session.update(user_id=user["id"],name=user["name"],role=user["role"],
                           email=user["email"],avatar=user["avatar"])
            flash(f"Welcome back, {user['name']}!","success")
            conn2=db.get_db()
            conn2.execute("UPDATE users SET last_login=current_login,current_login=datetime('now') WHERE id=?",(user["id"],))
            conn2.commit(); conn2.close()
            db.close_stale_session(user["id"])
            session["login_session_id"] = db.start_login_session(user["id"])
            db.log_history(user["id"],user["name"],"Logged in")
            if user["role"]=="admin":
                return redirect(url_for("admin_datasets"))
            return redirect(url_for("user_dataset_selection"))
        flash("Invalid email, password, or role selected.","danger")
    return render_template("login.html")

@app.route("/forgot-password", methods=["GET","POST"])
def forgot_password():
    if request.method=="POST":
        email = request.form.get("email","").strip().lower()
        conn = db.get_db()
        user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        conn.close()
        # Always show the same message (don't leak which emails exist)
        if user:
            import secrets
            token = secrets.token_urlsafe(24)
            db.create_password_reset(user["id"], token, minutes_valid=30)
            db.log_history(user["id"], user["name"], "Requested password reset")
            reset_link = url_for("reset_password", token=token, _external=True)
            # In production this would be emailed. We surface it directly here
            # so the flow is fully testable without an email server.
            flash("If that email exists, a reset link has been generated below.", "info")
            return render_template("forgot_password.html", reset_link=reset_link)
        flash("If that email exists, a reset link has been generated below.", "info")
    return render_template("forgot_password.html", reset_link=None)

@app.route("/reset-password/<token>", methods=["GET","POST"])
def reset_password(token):
    reset_row = db.get_valid_reset(token)
    if not reset_row:
        flash("This reset link is invalid or has expired.", "danger")
        return redirect(url_for("forgot_password"))
    if request.method=="POST":
        pw = request.form.get("password","")
        confirm = request.form.get("confirm_password","")
        if not pw or pw != confirm:
            flash("Passwords do not match.", "danger")
            return render_template("reset_password.html", token=token)
        conn = db.get_db()
        conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                     (generate_password_hash(pw), reset_row["user_id"]))
        conn.commit()
        user = conn.execute("SELECT * FROM users WHERE id=?", (reset_row["user_id"],)).fetchone()
        conn.close()
        db.use_password_reset(token)
        db.log_history(reset_row["user_id"], user["name"] if user else "User", "Password reset completed")
        flash("Your password has been reset. Please sign in.", "success")
        return redirect(url_for("login"))
    return render_template("reset_password.html", token=token)

@app.route("/signup", methods=["GET","POST"])
def signup():
    if request.method=="POST":
        name=request.form.get("name","").strip()
        email=request.form.get("email","").strip().lower()
        pw=request.form.get("password","")
        confirm=request.form.get("confirm_password","")
        role=request.form.get("role","")
        if not all([name,email,pw,role]): flash("Please fill in all fields.","danger"); return redirect(url_for("signup"))
        if pw!=confirm: flash("Passwords do not match.","danger"); return redirect(url_for("signup"))
        if role not in ("admin","user"): flash("Select a valid role.","danger"); return redirect(url_for("signup"))
        conn=db.get_db()
        if conn.execute("SELECT id FROM users WHERE email=?",(email,)).fetchone():
            conn.close(); flash("Email already exists.","danger"); return redirect(url_for("signup"))
        conn.execute("INSERT INTO users (name,email,password_hash,role) VALUES (?,?,?,?)",(name,email,generate_password_hash(pw),role))
        conn.commit(); conn.close()
        flash("Account created. Please log in.","success")
        return redirect(url_for("login"))
    return render_template("signup.html")

@app.route("/logout-page")
@login_required
def logout_page():
    return render_template("user/logout.html")

@app.route("/logout", methods=["POST"])
@login_required
def logout():
    uid,name,role=session.get("user_id"),session.get("name"),session.get("role")
    db.end_login_session(session.get("login_session_id"))
    db.log_history(uid,name,"Logged out")
    session.clear()
    flash("You have been logged out.","info")
    return redirect(url_for("login"))

# ──────────────────────────────────────────────────────────────────────
# User: Dataset Selection
# ──────────────────────────────────────────────────────────────────────
@app.route("/user/datasets")
@user_required
def user_dataset_selection():
    conn = db.get_db()
    admin_datasets = conn.execute(
        "SELECT d.*, u.name AS uploader_name FROM datasets d JOIN users u ON u.id=d.uploaded_by "
        "WHERE d.is_locked=0 AND d.source_type='admin' ORDER BY d.id DESC"
    ).fetchall()
    user_datasets = conn.execute(
        "SELECT * FROM datasets WHERE is_locked=0 AND source_type='user_upload' AND uploaded_by=? ORDER BY id DESC",
        (session["user_id"],)
    ).fetchall()
    # Build access map: dataset_id -> latest request from this user
    reqs = conn.execute(
        "SELECT * FROM requests WHERE user_id=? ORDER BY id DESC",
        (session["user_id"],)
    ).fetchall()
    conn.close()
    # Keep only most recent request per dataset
    access_map = {}
    for r in reqs:
        if r["dataset_id"] not in access_map:
            access_map[r["dataset_id"]] = dict(r)
    return render_template("user/dataset_selection.html",
        admin_datasets=admin_datasets,
        user_datasets=user_datasets,
        sample_datasets=SAMPLE_DATASETS,
        access_map=access_map)

@app.route("/user/datasets/upload", methods=["POST"])
@user_required
def user_upload_dataset():
    name = request.form.get("name","").strip()
    file = request.files.get("file")
    if not name or not file or not file.filename:
        flash("Dataset name and file are required.","danger")
        return redirect(url_for("user_dataset_selection"))
    ext = file.filename.rsplit(".",1)[-1].lower() if "." in file.filename else ""
    if ext not in ("csv","json"):
        flash("Only CSV and JSON files are supported.","danger")
        return redirect(url_for("user_dataset_selection"))
    filename = secure_filename(file.filename)
    unique   = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}"
    filepath = os.path.join(UPLOAD_FOLDER, unique)
    file.save(filepath)
    conn=db.get_db()
    conn.execute("INSERT INTO datasets (name,description,filename,filepath,uploaded_by,source_type) VALUES (?,?,?,?,?,?)",
                 (name,"User uploaded dataset",filename,unique,session["user_id"],"user_upload"))
    conn.commit(); conn.close()
    db.add_block(f"User dataset uploaded: '{name}' by {session['name']}")
    db.log_history(session["user_id"], session["name"], "Dataset uploaded", name)
    conn2=db.get_db()
    ds=conn2.execute("SELECT id FROM datasets WHERE filepath=?",(unique,)).fetchone()
    conn2.close()
    if not ds:
        flash(f"Dataset '{name}' uploaded successfully.","success")
        return redirect(url_for("user_dataset_selection"))

    # Scan before doing anything else with it. If it's not clean, stop here
    # and send the user straight to the cleaning screen instead of the dashboard.
    headers, rows = read_dataset_file(filepath)
    quality = ai.data_quality_report(headers, rows)
    if quality.get("has_issues"):
        flash(f"Dataset '{name}' uploaded, but it needs cleaning before use.","warning")
        return redirect(url_for("user_clean_dataset", type="user_upload", id=ds["id"]))

    flash(f"Dataset '{name}' uploaded successfully.","success")
    return redirect(url_for("user_dashboard",type="user_upload",id=ds["id"]))

@app.route("/user/datasets/json-import", methods=["POST"])
@user_required
def user_import_json():
    name=request.form.get("name","").strip()
    json_data=request.form.get("json_data","").strip()
    if not name or not json_data:
        flash("Name and JSON data are required.","danger")
        return redirect(url_for("user_dataset_selection"))
    try:
        parsed=json.loads(json_data)
        if isinstance(parsed,list) and parsed:
            headers=list(parsed[0].keys()); rows=parsed
        elif isinstance(parsed,dict):
            headers=list(parsed.keys()); rows=[parsed]
        else:
            flash("JSON must be an object or array of objects.","danger")
            return redirect(url_for("user_dataset_selection"))
        filename=secure_filename(f"{name.replace(' ','_')}.csv")
        unique=f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}"
        fp=os.path.join(UPLOAD_FOLDER,unique)
        with open(fp,"w",newline="",encoding="utf-8") as f:
            writer=csv.DictWriter(f,fieldnames=headers); writer.writeheader(); writer.writerows(rows)
        conn=db.get_db()
        conn.execute("INSERT INTO datasets (name,description,filename,filepath,uploaded_by,source_type) VALUES (?,?,?,?,?,?)",
                     (name,"Imported from JSON",filename,unique,session["user_id"],"user_upload"))
        conn.commit()
        ds=conn.execute("SELECT id FROM datasets WHERE filepath=?",(unique,)).fetchone()
        conn.close()
        db.log_history(session["user_id"], session["name"], "Dataset imported (JSON)", name)
        if ds:
            quality = ai.data_quality_report(headers, rows)
            if quality.get("has_issues"):
                flash(f"JSON dataset '{name}' imported, but it needs cleaning before use.","warning")
                return redirect(url_for("user_clean_dataset", type="user_upload", id=ds["id"]))
            flash(f"JSON dataset '{name}' imported.","success")
            return redirect(url_for("user_dashboard",type="user_upload",id=ds["id"]))
    except json.JSONDecodeError:
        flash("Invalid JSON format.","danger")
    return redirect(url_for("user_dataset_selection"))

# ──────────────────────────────────────────────────────────────────────
# User: Dashboard
# ──────────────────────────────────────────────────────────────────────
@app.route("/user/dashboard")
@user_required
def user_dashboard():
    ds_type = request.args.get("type","")
    ds_id   = request.args.get("id","")
    headers, rows, dataset_name = [], [], ""
    if ds_type and ds_id:
        headers, rows, dataset_name = load_dataset(ds_type, ds_id)
        if dataset_name:
            db.log_history(session["user_id"], session["name"], "Viewed dataset", dataset_name)
    return render_template("user/dashboard.html",
        headers=headers, rows=rows,
        dataset_name=dataset_name,
        dataset_type=ds_type,
        selected_dataset_id=ds_id)

# ──────────────────────────────────────────────────────────────────────
# User: Clean Dataset (gate shown right after an unclean upload)
# ──────────────────────────────────────────────────────────────────────
@app.route("/user/datasets/clean")
@user_required
def user_clean_dataset():
    ds_type = request.args.get("type", "")
    ds_id   = request.args.get("id", "")
    headers, rows, dataset_name = ([], [], "")
    if ds_type and ds_id:
        headers, rows, dataset_name = load_dataset(ds_type, ds_id)
    if not dataset_name:
        flash("Dataset not found.", "danger")
        return redirect(url_for("user_dataset_selection"))

    quality = ai.data_quality_report(headers, rows)
    if not quality.get("has_issues"):
        # Nothing to clean after all — just go straight to the dashboard.
        return redirect(url_for("user_dashboard", type=ds_type, id=ds_id))

    return render_template("user/clean_dataset.html",
        dataset_name=dataset_name, dataset_type=ds_type, selected_dataset_id=ds_id,
        quality=quality)

# ──────────────────────────────────────────────────────────────────────
# User: Chatbot  (session-based)
# ──────────────────────────────────────────────────────────────────────
# Visualization Studio
# ──────────────────────────────────────────────────────────────────────
@app.route("/user/visualize")
@user_required
def user_visualize():
    ds_type = request.args.get("type", "")
    ds_id   = request.args.get("id", "")
    headers, rows, dataset_name = [], [], ""
    quality = None
    needs_cleaning = False
    if ds_type and ds_id:
        headers, rows, dataset_name = load_dataset(ds_type, ds_id)
        if dataset_name:
            db.log_history(session["user_id"], session["name"], "Opened Visualization Studio", dataset_name)
            # Scan the dataset before letting the user build charts. If the
            # data isn't in a clean/proper shape yet, we gate the Studio and
            # offer a one-click clean instead of silently plotting bad data.
            quality = ai.data_quality_report(headers, rows)
            needs_cleaning = bool(quality and quality.get("has_issues")) and not request.args.get("force")

    # Categorize columns so the UI knows which are numeric / categorical / date
    def col_type(col):
        vals = [r.get(col, "") for r in rows if r.get(col, "") not in ("", None)]
        numeric = 0
        for v in vals[:30]:
            try: float(str(v).replace(",","")); numeric += 1
            except: pass
        if numeric > len(vals[:30]) * 0.7:
            return "numeric"
        any_date = any(w in col.lower() for w in ["date","time","year","month","day","period","week"])
        if any_date:
            return "date"
        return "categorical"

    col_meta = [{"name": h, "type": col_type(h)} for h in headers]

    # Build slim row data (max 500 rows) for JS
    rows_slim = [{h: r.get(h,"") for h in headers} for r in rows[:500]]

    return render_template("user/visualize.html",
        headers=headers, rows=rows, rows_slim=rows_slim,
        col_meta=col_meta, dataset_name=dataset_name,
        dataset_type=ds_type, selected_dataset_id=ds_id,
        quality=quality, needs_cleaning=needs_cleaning)

# ──────────────────────────────────────────────────────────────────────
@app.route("/user/chatbot")
@user_required
def user_chatbot():
    ds_type = request.args.get("type","")
    ds_id   = request.args.get("id","")
    headers, rows, dataset_name = [], [], ""
    if ds_type and ds_id:
        headers, rows, dataset_name = load_dataset(ds_type, ds_id)

    conn = db.get_db()
    chat_sessions = conn.execute(
        "SELECT * FROM chat_sessions WHERE user_id=? ORDER BY id DESC",
        (session["user_id"],)
    ).fetchall()
    conn.close()
    return render_template("user/chatbot.html",
        headers=headers, rows=rows, dataset_name=dataset_name,
        dataset_type=ds_type, selected_dataset_id=ds_id,
        chat_sessions=chat_sessions, active_session=None, messages=[])

@app.route("/user/chatbot/<int:session_id>")
@user_required
def user_chatbot_session(session_id):
    conn = db.get_db()
    active = conn.execute("SELECT * FROM chat_sessions WHERE id=? AND user_id=?",
                          (session_id,session["user_id"])).fetchone()
    if not active: conn.close(); abort(404)
    messages = conn.execute("SELECT * FROM chat_messages WHERE session_id=? ORDER BY id ASC",(session_id,)).fetchall()
    chat_sessions = conn.execute("SELECT * FROM chat_sessions WHERE user_id=? ORDER BY id DESC",(session["user_id"],)).fetchall()
    conn.close()

    ds_type = active["ds_type"] or ""
    ds_id   = active["ds_id"]   or ""
    headers, rows, dataset_name = [], [], ""
    if ds_type and ds_id:
        headers, rows, dataset_name = load_dataset(ds_type, ds_id)

    return render_template("user/chatbot.html",
        headers=headers, rows=rows, dataset_name=dataset_name,
        dataset_type=ds_type, selected_dataset_id=ds_id,
        chat_sessions=chat_sessions, active_session=active, messages=messages)

@app.route("/user/chat/new", methods=["POST"])
@user_required
def user_new_chat():
    ds_type = request.form.get("ds_type","")
    ds_id   = request.form.get("ds_id","")
    _, _, name = load_dataset(ds_type, ds_id) if ds_type and ds_id else ([], [], "")
    title = f"Chat – {name}" if name else "New Chat"
    conn=db.get_db()
    cur=conn.execute("INSERT INTO chat_sessions (user_id,title,ds_type,ds_id) VALUES (?,?,?,?)",
                     (session["user_id"],title,ds_type,ds_id))
    nid=cur.lastrowid; conn.commit(); conn.close()
    return redirect(url_for("user_chatbot_session",session_id=nid))

@app.route("/user/chat/ask", methods=["POST"])
@user_required
def user_chatbot_ask():
    question   = request.form.get("question","").strip()
    session_id = request.form.get("session_id","")
    if not question or not session_id:
        return jsonify({"answer":"Invalid request."})

    conn=db.get_db()
    cs=conn.execute("SELECT * FROM chat_sessions WHERE id=? AND user_id=?",(session_id,session["user_id"])).fetchone()
    if not cs: conn.close(); return jsonify({"answer":"Session not found."})

    ds_type=cs["ds_type"] or ""; ds_id=cs["ds_id"] or ""
    headers,rows,dataset_name=load_dataset(ds_type,ds_id) if ds_type and ds_id else ([],[],  "")

    # Save user message
    conn.execute("INSERT INTO chat_messages (session_id,role,content) VALUES (?,?,?)",(session_id,"user",question))
    conn.commit()

    # Generate AI answer
    answer = ai_answer(question, headers, rows, dataset_name)

    conn.execute("INSERT INTO chat_messages (session_id,role,content) VALUES (?,?,?)",(session_id,"assistant",answer))
    conn.commit(); conn.close()
    db.log_history(session["user_id"], session["name"], "Asked chatbot a question", dataset_name or "—")
    return jsonify({"answer":answer})

def ai_answer(question, headers, rows, dataset_name):
    """
    Smart AI answer engine.
    Returns either a plain markdown string  OR
    a JSON string  {"__type":"chart", "title":…, "chartType":…,
                    "labels":[…], "values":[…], "valueLabel":…, "text":…}
    so the front-end can render both text + an inline Chart.js chart.
    """
    import json as _json
    import re as _re

    if not dataset_name or not headers:
        return ("No dataset loaded. Please select a dataset from Dataset Selection first, "
                "then open the chatbot via the Dashboard or Dataset Selection page.")

    q = question.lower()
    n = len(rows)
    ncols = len(headers)

    # ── Data helpers ─────────────────────────────────────────
    def col_vals(col):
        return [r.get(col,"") for r in rows
                if str(r.get(col,"")).strip() not in ("","None","null","NULL","nan","NaN")]

    def num_vals(col):
        out = []
        for r in rows:
            try: out.append(float(str(r.get(col,"")).replace(",","")))
            except: pass
        return out

    def is_num(col):
        nv = num_vals(col); cv = col_vals(col)
        return len(nv) > len(cv) * 0.6 if cv else False

    def avg(a): return sum(a)/len(a) if a else 0

    def freq_map(col):
        d = {}
        for r in rows:
            v = str(r.get(col,"")).strip()
            if v: d[v] = d.get(v,0) + 1
        return d

    num_cols = [h for h in headers if is_num(h)]
    txt_cols = [h for h in headers if not is_num(h)]

    # ── Detect chart keyword + optional "of <column>" ────────
    chart_kw = {
        "bar":        ["bar chart","bar graph","bar plot","barchart"],
        "line":       ["line chart","line graph","line plot","trend chart","trend over"],
        "pie":        ["pie chart","pie graph","piechart"],
        "doughnut":   ["donut chart","doughnut chart","donut graph"],
        "scatter":    ["scatter plot","scatter chart","scatter graph","scatterplot"],
        "histogram":  ["histogram","distribution","frequency distribution","show distribution"],
        "area":       ["area chart","area graph","area plot"],
    }
    detected_chart = None
    for ctype, kws in chart_kw.items():
        if any(kw in q for kw in kws):
            detected_chart = ctype
            break

    def make_chart(title, ctype, labels, values, value_label="Value", text=""):
        return _json.dumps({
            "__type":     "chart",
            "title":      title,
            "chartType":  ctype,
            "labels":     [str(l)[:40] for l in labels],
            "values":     [round(float(v),3) if isinstance(v,(int,float)) else 0 for v in values],
            "valueLabel": value_label,
            "text":       text
        })

    # ── Try to detect which column the user mentions ──────────
    def find_col(q_lower):
        for h in headers:
            if h.lower() in q_lower:
                return h
        return None

    mentioned_col = find_col(q)

    # ── CHART REQUESTS ────────────────────────────────────────
    if detected_chart:

        # Bar / Histogram: numeric column → bucket distribution OR top values
        if detected_chart in ("bar","histogram"):
            # If user mentions a specific numeric col → histogram (binned)
            if mentioned_col and is_num(mentioned_col):
                nv = num_vals(mentioned_col)
                mn, mx = min(nv), max(nv)
                bw = (mx - mn) / 8 if mx != mn else 1
                buckets = {}
                for v in nv:
                    b = int((v - mn) / bw)
                    b = min(b, 7)
                    label = f"{mn+b*bw:.1f}–{mn+(b+1)*bw:.1f}"
                    buckets[label] = buckets.get(label,0) + 1
                labs = list(buckets.keys())
                vals = list(buckets.values())
                text = f"Distribution of **{mentioned_col}** in {dataset_name}."
                return make_chart(f"Distribution of {mentioned_col}", "bar", labs, vals, "Frequency", text)

            # If user mentions a text col → frequency bar
            if mentioned_col and not is_num(mentioned_col):
                fm = freq_map(mentioned_col)
                top = sorted(fm.items(), key=lambda x:-x[1])[:10]
                text = f"Top values in **{mentioned_col}**."
                return make_chart(f"Top values — {mentioned_col}", "bar",
                                  [x[0] for x in top], [x[1] for x in top], "Count", text)

            # Default bar: numeric column averages
            if num_cols:
                cols8 = num_cols[:8]
                vals  = [round(avg(num_vals(c)),2) for c in cols8]
                text  = f"Average of each numeric column in **{dataset_name}**."
                return make_chart(f"Column Averages — {dataset_name}", "bar",
                                  cols8, vals, "Average", text)

            # Fallback: first text col frequency
            if txt_cols:
                fm  = freq_map(txt_cols[0])
                top = sorted(fm.items(), key=lambda x:-x[1])[:8]
                text = f"Value frequency for **{txt_cols[0]}**."
                return make_chart(f"Frequency — {txt_cols[0]}", "bar",
                                  [x[0] for x in top], [x[1] for x in top], "Count", text)

        # Line / Area chart: best with numeric col over rows or time col
        if detected_chart in ("line","area"):
            if mentioned_col and is_num(mentioned_col):
                nv = num_vals(mentioned_col)
                step = max(1, len(nv)//40)
                labs = [str(i+1) for i in range(0, len(nv), step)]
                vals = [nv[i] for i in range(0, len(nv), step)]
                text = f"**{mentioned_col}** values across rows in **{dataset_name}**."
                return make_chart(f"{mentioned_col} — trend", "line", labs, vals, mentioned_col, text)

            # Detect time/date column
            time_col = next((h for h in headers if any(w in h.lower() for w in ["date","time","year","month","day","period","week"])), None)
            num_col  = num_cols[0] if num_cols else None
            if time_col and num_col:
                paired = []
                for r in rows:
                    try: paired.append((str(r.get(time_col,"")), float(str(r.get(num_col,"")).replace(",",""))))
                    except: pass
                paired.sort(key=lambda x: x[0])
                step = max(1, len(paired)//30)
                labs = [p[0][:12] for p in paired[::step]]
                vals = [p[1] for p in paired[::step]]
                text = f"**{num_col}** over **{time_col}** in **{dataset_name}**."
                return make_chart(f"{num_col} over time", "line", labs, vals, num_col, text)

            if num_cols:
                nv = num_vals(num_cols[0])
                step = max(1, len(nv)//40)
                labs = [str(i+1) for i in range(0, len(nv), step)]
                vals = [nv[i] for i in range(0, len(nv), step)]
                text = f"**{num_cols[0]}** values in **{dataset_name}**."
                return make_chart(f"{num_cols[0]} — trend", detected_chart,
                                  labs, vals, num_cols[0], text)

        # Pie / Doughnut: best for categorical column
        if detected_chart in ("pie","doughnut"):
            col = mentioned_col if (mentioned_col and not is_num(mentioned_col)) else (txt_cols[0] if txt_cols else None)
            if col:
                fm  = freq_map(col)
                top = sorted(fm.items(), key=lambda x:-x[1])[:8]
                text = f"**{col}** distribution in **{dataset_name}** (top {len(top)} values)."
                return make_chart(f"{col} distribution", detected_chart,
                                  [x[0] for x in top], [x[1] for x in top], "Count", text)
            # Fallback: numeric col share
            if num_cols:
                cols5 = num_cols[:6]
                vals  = [round(avg(num_vals(c)),2) for c in cols5]
                text  = f"Share of average values across numeric columns in **{dataset_name}**."
                return make_chart("Numeric column share", detected_chart,
                                  cols5, vals, "Average", text)

        # Scatter: first two numeric cols
        if detected_chart == "scatter":
            if len(num_cols) >= 2:
                c1, c2 = (num_cols if not mentioned_col else
                          ([mentioned_col] + [c for c in num_cols if c != mentioned_col]))[:2]
                pts = []
                for r in rows:
                    try:
                        x = float(str(r.get(c1,"")).replace(",",""))
                        y = float(str(r.get(c2,"")).replace(",",""))
                        pts.append((x,y))
                    except: pass
                step = max(1, len(pts)//80)
                pts  = pts[::step]
                labs = [str(round(p[0],2)) for p in pts]
                vals = [round(p[1],2) for p in pts]
                text = f"Scatter of **{c1}** vs **{c2}** in **{dataset_name}**."
                return make_chart(f"{c1} vs {c2}", "scatter", labs, vals, c2, text)
            return f"Need at least 2 numeric columns for a scatter plot. Found: {', '.join(num_cols) or 'none'}."

        return f"I couldn't build that chart type for this dataset. Try asking for a bar, line, pie or scatter chart."

    # ── "top N" shortcut ──────────────────────────────────────
    top_m = _re.search(r"top\s+(\d+)", q)
    top_n = int(top_m.group(1)) if top_m else None

    if top_n or any(w in q for w in ["top ","most common","most frequent","popular","highest"]):
        n_vals = top_n or 10
        col = mentioned_col or (txt_cols[0] if txt_cols else (num_cols[0] if num_cols else None))
        if col:
            if is_num(col):
                vals_sorted = sorted(num_vals(col), reverse=True)[:n_vals]
                text = f"**Top {n_vals} values in {col}** ({dataset_name}):\n\n"
                text += "\n".join(f"• **{i+1}.** {v:.3f}" for i,v in enumerate(vals_sorted))
                return text
            else:
                fm   = freq_map(col)
                top  = sorted(fm.items(), key=lambda x:-x[1])[:n_vals]
                text = f"**Top {n_vals} values in {col}** ({dataset_name}):\n\n"
                text += "\n".join(f"• **{i+1}.** {v} — {c} times ({c/n*100:.1f}%)" for i,(v,c) in enumerate(top))
                # Also return a bar chart
                return make_chart(f"Top {n_vals} — {col}", "bar",
                                  [x[0] for x in top], [x[1] for x in top],
                                  "Count", text)

    # ── Specific column query ──────────────────────────────────
    if mentioned_col:
        col = mentioned_col
        nv  = num_vals(col); cv = col_vals(col)
        if nv:
            m   = avg(nv); sd = (avg([(x-m)**2 for x in nv]))**.5
            lo  = min(nv); hi = max(nv); mid = (lo+hi)/2
            lo_count = len([v for v in nv if v <= mid])
            hi_count = len([v for v in nv if v > mid])
            stat_text = (
                f"**Column `{col}` in {dataset_name}:**\n\n"
                f"- Type: numeric  Count: {len(nv)}\n"
                f"- Min: {lo:.3f}  Max: {hi:.3f}\n"
                f"- Average: {m:.3f}  Std Dev: {sd:.3f}"
            )
            labs = [f"{lo:.1f} to {mid:.1f}", f"{mid:.1f} to {hi:.1f}"] if len(nv)>1 else [col]
            vals = [lo_count, hi_count] if len(nv)>1 else [len(nv)]
            return make_chart(f"Distribution of {col}", "bar", labs, vals, "Count", stat_text)
        else:
            fm   = freq_map(col)
            top5 = sorted(fm.items(), key=lambda x:-x[1])[:5]
            stat_text = (
                f"**Column `{col}` in {dataset_name}:**\n\n"
                f"- Type: text  Non-null: {len(cv)}  Unique: {len(fm)}\n"
                "- Top values:\n" + "\n".join(f"  {v}: {c}" for v,c in top5)
            )
            return make_chart(
                f"Top values in {col}", "bar",
                [x[0] for x in top5], [x[1] for x in top5], "Count", stat_text
            )
    # ── Text queries ───────────────────────────────────────────
    if any(w in q for w in ["how many row","row count","size","total row","number of row"]):
        return (f"**{dataset_name}** has **{n} rows** and **{ncols} columns**.\n\n"
                f"Columns: {', '.join(headers[:12])}{'…' if ncols>12 else ''}")

    if any(w in q for w in ["column","field","feature","variable","what col"]):
        lines = "\n".join(f"• **{h}** - `{'numeric' if is_num(h) else 'text'}`" for h in headers)
        return f"**{dataset_name}** has {ncols} columns:\n\n{lines}"

    if any(w in q for w in ["summary","describe","overview","about this","tell me about"]):
        missing = sum(1 for r in rows for h in headers
                      if str(r.get(h,"")).strip() in ("","None","null","NULL","nan","NaN"))
        lines = [f"• **Rows:** {n}", f"• **Columns:** {ncols}",
                 f"• **Numeric columns ({len(num_cols)}):** {', '.join(num_cols[:8])}{'…' if len(num_cols)>8 else ''}",
                 f"• **Text columns ({len(txt_cols)}):** {', '.join(txt_cols[:8])}{'…' if len(txt_cols)>8 else ''}",
                 f"• **Missing values:** {missing}"]
        return f"**Summary of {dataset_name}:**\n\n" + "\n".join(lines)

    if any(w in q for w in ["average","mean","avg"]):
        if not num_cols: return f"No numeric columns found in **{dataset_name}**."
        lines = [f"• **{c}:** {avg(num_vals(c)):.3f}" for c in num_cols[:8]]
        return (f"**Averages in {dataset_name}:**\n\n" + "\n".join(lines))

    if any(w in q for w in ["maximum","max ","highest","largest"]):
        if not num_cols: return "No numeric columns found."
        lines = [f"• **{c}:** {max(num_vals(c)):.3f}" for c in num_cols[:8] if num_vals(c)]
        return f"**Maximum values in {dataset_name}:**\n\n" + "\n".join(lines)

    if any(w in q for w in ["minimum","min ","lowest","smallest"]):
        if not num_cols: return "No numeric columns found."
        lines = [f"• **{c}:** {min(num_vals(c)):.3f}" for c in num_cols[:8] if num_vals(c)]
        return f"**Minimum values in {dataset_name}:**\n\n" + "\n".join(lines)

    if any(w in q for w in ["missing","null","empty","blank","nan"]):
        total_cells = n * ncols
        missing = sum(1 for r in rows for h in headers
                      if str(r.get(h,"")).strip() in ("","None","null","NULL","nan","NaN"))
        pct = missing/total_cells*100 if total_cells else 0
        per_col = [f"• **{h}:** {sum(1 for r in rows if str(r.get(h,'')).strip() in ('','None','null','NULL','nan','NaN'))}"
                   for h in headers[:10]]
        return (f"**Missing values in {dataset_name}:**\n\n"
                f"Total: **{missing}** out of {total_cells} cells ({pct:.1f}%)\n\nBy column:\n" + "\n".join(per_col))

    if any(w in q for w in ["preview","first","sample row","top 5","show me"]):
        if not rows: return "The dataset appears to be empty."
        header_line = " | ".join(headers[:6])
        sep  = " | ".join(["---"]*min(6,ncols))
        data_lines = [" | ".join(str(r.get(h,""))[:15] for h in headers[:6]) for r in rows[:5]]
        return (f"**Preview of {dataset_name}** (first 5 rows):\n\n"
                f"| {header_line} |\n| {sep} |\n" + "".join(f"| {l} |\n" for l in data_lines))

    if any(w in q for w in ["unique","distinct","different value"]):
        lines = [f"• **{h}:** {len(set(str(r.get(h,'')) for r in rows))} unique" for h in headers[:10]]
        return f"**Unique values per column in {dataset_name}:**\n\n" + "\n".join(lines)

    if any(w in q for w in ["std","standard deviation","variance","spread"]):
        if not num_cols: return "No numeric columns found."
        lines = []
        for c in num_cols[:8]:
            nv = num_vals(c)
            if nv:
                m = avg(nv); sd = (avg([(x-m)**2 for x in nv]))**.5
                lines.append(f"• **{c}:** std=`{sd:.3f}`, var=`{sd**2:.3f}`")
        return f"**Standard deviations in {dataset_name}:**\n\n" + "\n".join(lines)

    if any(w in q for w in ["insight","pattern","trend","notable","interesting","key finding"]):
        insights = []
        if num_cols:
            biggest = max(num_cols, key=lambda c: avg(num_vals(c)) if num_vals(c) else 0)
            insights.append(f"🔢 **{biggest}** has the highest average ({avg(num_vals(biggest)):.2f}).")
        if txt_cols:
            c = txt_cols[0]; fm = freq_map(c)
            if fm:
                top = max(fm, key=fm.get)
                insights.append(f"📊 Most common **{c}** value: `{top}` ({fm[top]} times, {fm[top]/n*100:.1f}%).")
        missing = sum(1 for r in rows for h in headers
                      if str(r.get(h,"")).strip() in ("","None","null","NULL","nan","NaN"))
        pct = missing/(n*ncols)*100 if n*ncols else 0
        insights.append(f"✅ Data completeness: **{100-pct:.1f}%** ({missing} missing cells).")
        insights.append(f"📏 {n} rows · {ncols} columns ({len(num_cols)} numeric, {len(txt_cols)} text).")
        return f"**Key Insights — {dataset_name}:**\n\n" + "\n".join(insights)

    if any(w in q for w in ["compar","numeric col","range"]):
        if not num_cols: return "No numeric columns found."
        lines = [f"• **{c}:** min={min(num_vals(c)):.2f}, max={max(num_vals(c)):.2f}, avg={avg(num_vals(c)):.2f}"
                 for c in num_cols[:8] if num_vals(c)]
        return f"**Numeric column ranges in {dataset_name}:**\n\n" + "\n".join(lines)

    # ── Default help ───────────────────────────────────────────
    return (f"I'm analysing **{dataset_name}** ({n} rows, {ncols} columns). You can ask me:\n\n"
            f"• **Summary / Overview**\n"
            f"• **Average / Min / Max / Std Dev**\n"
            f"• **Missing values**\n"
            f"• **Top 10 values in [column]**\n"
            f"• **Preview first rows**\n"
            f"• **Show bar chart / line chart / pie chart / scatter plot**\n"
            f"• **Distribution of [column]**\n"
            f"• **[column name]** — instant stats for any column\n\n"
            f"What would you like to know?")


@app.route("/user/chat/session/delete/<int:session_id>", methods=["POST"])
@user_required
def user_delete_chat_session(session_id):
    conn=db.get_db()
    conn.execute("DELETE FROM chat_messages WHERE session_id=?",(session_id,))
    conn.execute("DELETE FROM chat_sessions WHERE id=? AND user_id=?",(session_id,session["user_id"]))
    conn.commit(); conn.close()
    flash("Chat deleted.","info")
    return redirect(url_for("user_chatbot"))

@app.route("/user/chat/delete-all", methods=["POST"])
@user_required
def user_delete_all_chat_sessions():
    conn=db.get_db()
    sessions=conn.execute("SELECT id FROM chat_sessions WHERE user_id=?",(session["user_id"],)).fetchall()
    for s in sessions:
        conn.execute("DELETE FROM chat_messages WHERE session_id=?",(s["id"],))
    conn.execute("DELETE FROM chat_sessions WHERE user_id=?",(session["user_id"],))
    conn.commit(); conn.close()
    flash("All chat history deleted.","info")
    return redirect(url_for("user_chatbot"))

# ──────────────────────────────────────────────────────────────────────
# User: Chat History (legacy table-based)
# ──────────────────────────────────────────────────────────────────────
@app.route("/user/history")
@user_required
def user_history():
    chat_history = db.get_chat_history(session["user_id"], limit=100)
    conn = db.get_db()
    activity = conn.execute(
        "SELECT * FROM history WHERE admin_id=? ORDER BY id DESC LIMIT 200",
        (session["user_id"],)
    ).fetchall()
    conn.close()
    return render_template("user/history.html",
        chat_history=chat_history, activity=activity, major_work_actions=MAJOR_WORK_ACTIONS)

@app.route("/user/history/delete/<int:chat_id>", methods=["POST"])
@user_required
def user_delete_chat(chat_id):
    db.delete_chat(chat_id, session["user_id"])
    flash("Entry deleted.","info")
    return redirect(url_for("user_history"))

@app.route("/user/history/delete-all", methods=["POST"])
@user_required
def user_delete_all_chat():
    db.delete_all_chat(session["user_id"])
    flash("All conversation history cleared.","info")
    return redirect(url_for("user_history"))

# ──────────────────────────────────────────────────────────────────────
# User: Activity History (what the user actually did — uploads, edits,
# requests, logins, reports, etc). Entries that represent the user's
# actual work are protected and can't be deleted, individually or in bulk.
# ──────────────────────────────────────────────────────────────────────
@app.route("/user/activity/delete/<int:history_id>", methods=["POST"])
@user_required
def user_delete_activity(history_id):
    conn = db.get_db()
    entry = conn.execute("SELECT * FROM history WHERE id=? AND admin_id=?",
                          (history_id, session["user_id"])).fetchone()
    if not entry:
        conn.close(); abort(404)
    if entry["action"] in MAJOR_WORK_ACTIONS:
        conn.close()
        flash("This entry is part of your major work and can't be deleted.","danger")
        return redirect(url_for("user_history"))
    conn.execute("DELETE FROM history WHERE id=?", (history_id,))
    conn.commit(); conn.close()
    flash("Activity entry deleted.","info")
    return redirect(url_for("user_history"))

@app.route("/user/activity/delete-all", methods=["POST"])
@user_required
def user_delete_all_activity():
    conn = db.get_db()
    placeholders = ",".join("?" * len(MAJOR_WORK_ACTIONS))
    conn.execute(
        f"DELETE FROM history WHERE admin_id=? AND action NOT IN ({placeholders})",
        (session["user_id"], *MAJOR_WORK_ACTIONS)
    )
    conn.commit(); conn.close()
    flash("Cleared minor activity. Entries for your major work were kept.","info")
    return redirect(url_for("user_history"))

# ──────────────────────────────────────────────────────────────────────
# User: Profile, Requests
# ──────────────────────────────────────────────────────────────────────
@app.route("/user/my-requests")
@user_required
def user_my_requests():
    conn=db.get_db()
    rows=conn.execute("SELECT r.*,d.name AS dataset_name,d.is_locked AS dataset_locked FROM requests r JOIN datasets d ON d.id=r.dataset_id WHERE r.user_id=? ORDER BY r.id DESC",(session["user_id"],)).fetchall()
    conn.close()
    return render_template("user/my_requests.html", requests=rows)

@app.route("/user/profile")
@user_required
def user_profile():
    conn=db.get_db()
    user=conn.execute("SELECT * FROM users WHERE id=?",(session["user_id"],)).fetchone()
    stats={
        "uploads": conn.execute("SELECT COUNT(*) c FROM datasets WHERE uploaded_by=? AND source_type='user_upload'",(session["user_id"],)).fetchone()["c"],
        "requests": conn.execute("SELECT COUNT(*) c FROM requests WHERE user_id=?",(session["user_id"],)).fetchone()["c"],
        "approved": conn.execute("SELECT COUNT(*) c FROM requests WHERE user_id=? AND status='Approved'",(session["user_id"],)).fetchone()["c"],
    }
    conn.close()
    return render_template("user/profile.html", user=user, stats=stats)

@app.route("/user/profile/edit", methods=["POST"])
@user_required
def user_edit_profile():
    name=request.form.get("name","").strip(); email=request.form.get("email","").strip().lower()
    bio=request.form.get("bio","").strip(); new_pw=request.form.get("new_password","")
    av_file=request.files.get("avatar")
    if not name or not email: flash("Name and email required.","danger"); return redirect(url_for("user_profile"))
    conn=db.get_db()
    if conn.execute("SELECT id FROM users WHERE email=? AND id!=?",(email,session["user_id"])).fetchone():
        conn.close(); flash("Email in use.","danger"); return redirect(url_for("user_profile"))
    av_fn=session.get("avatar")
    if av_file and av_file.filename:
        ext=av_file.filename.rsplit(".",1)[-1].lower()
        if ext not in ALLOWED_AVATAR: conn.close(); flash("Invalid image type.","danger"); return redirect(url_for("user_profile"))
        av_fn=f"user_{session['user_id']}_{datetime.now().strftime('%Y%m%d%H%M%S')}.{ext}"
        av_file.save(os.path.join(AVATAR_FOLDER,av_fn))
    if new_pw:
        conn.execute("UPDATE users SET name=?,email=?,bio=?,avatar=?,password_hash=? WHERE id=?",
                     (name,email,bio,av_fn,generate_password_hash(new_pw),session["user_id"]))
    else:
        conn.execute("UPDATE users SET name=?,email=?,bio=?,avatar=? WHERE id=?",
                     (name,email,bio,av_fn,session["user_id"]))
    conn.commit(); conn.close()
    session["name"]=name; session["email"]=email; session["avatar"]=av_fn
    db.log_history(session["user_id"], name, "Profile updated")
    flash("Profile updated.","success"); return redirect(url_for("user_profile"))

@app.route("/notifications/mark-read", methods=["POST"])
@login_required
def mark_notifications_read():
    conn=db.get_db(); conn.execute("UPDATE notifications SET is_read=1 WHERE user_id=?",(session["user_id"],)); conn.commit(); conn.close()
    return redirect(request.referrer or url_for("index"))

# ──────────────────────────────────────────────────────────────────────
# Admin routes (unchanged)
# ──────────────────────────────────────────────────────────────────────
@app.route("/admin/datasets", methods=["GET","POST"])
@admin_required
def admin_datasets():
    conn=db.get_db()
    if request.method=="POST":
        name=request.form.get("name","").strip(); desc=request.form.get("description","").strip(); file=request.files.get("file")
        if not name or not file or not file.filename: flash("Name and file required.","danger"); return redirect(url_for("admin_datasets"))
        filename=secure_filename(file.filename); unique=f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}"
        filepath=os.path.join(UPLOAD_FOLDER,unique)
        file.save(filepath)

        # Data-quality gate: admin uploads must already be clean. CSV, Excel (.xlsx/.xls)
        # and JSON files are all scanned immediately; if issues are found, the upload is
        # rejected outright (no redirect to a cleaning screen — admin data must arrive clean).
        ext = filename.rsplit(".",1)[-1].lower() if "." in filename else ""
        if ext in ("csv","xlsx","xls","json"):
            headers, rows = read_dataset_file(filepath)
            quality = ai.data_quality_report(headers, rows)
            if quality.get("has_issues"):
                os.remove(filepath); conn.close()
                problems=[]
                if quality["duplicate_rows"]: problems.append(f"{quality['duplicate_rows']} duplicate row(s)")
                if quality["missing_values"]: problems.append(f"{quality['missing_values']} missing value(s)")
                if quality["wrong_type_values"]: problems.append(f"{quality['wrong_type_values']} wrong-type value(s)")
                if quality["outlier_values"]: problems.append(f"{quality['outlier_values']} outlier value(s)")
                flash("Upload rejected — dataset isn't clean: " + ", ".join(problems) +
                      f" (quality score {quality['quality_score']}/100). Please clean the data before uploading.","danger")
                return redirect(url_for("admin_datasets"))

        conn.execute("INSERT INTO datasets (name,description,filename,filepath,uploaded_by,source_type) VALUES (?,?,?,?,?,?)",
                     (name,desc,filename,unique,session["user_id"],"admin"))
        conn.commit()
        db.add_block(f"Dataset uploaded: '{name}' by {session['name']}")
        db.log_history(session["user_id"],session["name"],"Dataset uploaded",name)
        for u in conn.execute("SELECT id FROM users WHERE role='user'").fetchall():
            db.add_notification(u["id"],f"New dataset available: {name}")
        conn.close(); flash(f"Dataset '{name}' uploaded.","success"); return redirect(url_for("admin_datasets"))
    datasets=conn.execute("SELECT d.*,u.name AS uploader_name FROM datasets d JOIN users u ON u.id=d.uploaded_by WHERE d.source_type='admin' ORDER BY d.id DESC").fetchall()
    conn.close(); return render_template("admin/datasets.html",datasets=datasets)

@app.route("/admin/datasets/rename/<int:dataset_id>", methods=["POST"])
@admin_required
def admin_rename_dataset(dataset_id):
    new_name = request.form.get("name","").strip()
    conn=db.get_db(); ds=conn.execute("SELECT * FROM datasets WHERE id=?",(dataset_id,)).fetchone()
    if not ds: conn.close(); abort(404)
    if not new_name:
        conn.close(); flash("Dataset name cannot be empty.","danger")
        return redirect(url_for("admin_datasets"))
    old_name=ds["name"]
    conn.execute("UPDATE datasets SET name=? WHERE id=?",(new_name,dataset_id)); conn.commit(); conn.close()
    db.add_block(f"Dataset renamed: '{old_name}' -> '{new_name}' by {session['name']}")
    db.log_history(session["user_id"],session["name"],"Dataset renamed",f"{old_name} → {new_name}")
    flash(f"Dataset renamed to '{new_name}'.","success")
    return redirect(url_for("admin_datasets"))

@app.route("/admin/datasets/delete/<int:dataset_id>", methods=["POST"])

@admin_required
def admin_delete_dataset(dataset_id):
    conn=db.get_db(); ds=conn.execute("SELECT * FROM datasets WHERE id=?",(dataset_id,)).fetchone()
    if not ds: conn.close(); abort(404)
    fp=os.path.join(UPLOAD_FOLDER,ds["filepath"])
    if os.path.exists(fp): os.remove(fp)
    conn.execute("DELETE FROM requests WHERE dataset_id=?",(dataset_id,)); conn.execute("DELETE FROM datasets WHERE id=?",(dataset_id,)); conn.commit(); conn.close()
    db.add_block(f"Dataset deleted: '{ds['name']}' by {session['name']}")
    db.log_history(session["user_id"],session["name"],"Dataset deleted",ds["name"])
    flash(f"Dataset '{ds['name']}' deleted.","info"); return redirect(url_for("admin_datasets"))

@app.route("/admin/datasets/lock/<int:dataset_id>", methods=["POST"])
@admin_required
def admin_toggle_lock_dataset(dataset_id):
    conn=db.get_db(); ds=conn.execute("SELECT * FROM datasets WHERE id=?",(dataset_id,)).fetchone()
    if not ds: conn.close(); abort(404)
    ns=0 if ds["is_locked"] else 1
    conn.execute("UPDATE datasets SET is_locked=? WHERE id=?",(ns,dataset_id)); conn.commit(); conn.close()
    aw="locked" if ns else "unlocked"
    db.add_block(f"Dataset {aw}: '{ds['name']}' by {session['name']}")
    db.log_history(session["user_id"],session["name"],f"Dataset {aw}",ds["name"])
    flash(f"Dataset '{ds['name']}' {aw}.","success" if ns==0 else "info")
    return redirect(request.referrer or url_for("admin_datasets"))

@app.route("/datasets/download/<int:dataset_id>")
@login_required
def download_dataset(dataset_id):
    conn=db.get_db(); ds=conn.execute("SELECT * FROM datasets WHERE id=?",(dataset_id,)).fetchone(); conn.close()
    if not ds: abort(404)
    return send_from_directory(UPLOAD_FOLDER,ds["filepath"],as_attachment=True,download_name=ds["filename"])

@app.route("/admin/requests")
@admin_required
def admin_requests():
    conn=db.get_db()
    rows=conn.execute("SELECT r.*,u.name AS user_name,d.name AS dataset_name,d.is_locked AS dataset_locked,d.source_type FROM requests r JOIN users u ON u.id=r.user_id JOIN datasets d ON d.id=r.dataset_id ORDER BY r.id DESC").fetchall()
    conn.close(); return render_template("admin/requests.html",requests=rows)

@app.route("/admin/requests/<int:request_id>/<string:decision>", methods=["POST"])
@admin_required
def admin_decide_request(request_id, decision):
    if decision not in ("approve","reject"): abort(400)
    ns="Approved" if decision=="approve" else "Rejected"
    conn=db.get_db()
    req=conn.execute("SELECT r.*,u.name AS user_name,d.name AS dataset_name FROM requests r JOIN users u ON u.id=r.user_id JOIN datasets d ON d.id=r.dataset_id WHERE r.id=?",(request_id,)).fetchone()
    if not req: conn.close(); abort(404)
    conn.execute("UPDATE requests SET status=?,updated_at=datetime('now') WHERE id=?",(ns,request_id)); conn.commit(); conn.close()
    db.add_block(f"Request {ns.lower()}: '{req['dataset_name']}' for {req['user_name']}")
    db.log_history(session["user_id"],session["name"],f"Request {ns.lower()}",f"{req['user_name']} / {req['dataset_name']}")
    db.add_notification(req["user_id"],f"Your request for '{req['dataset_name']}' was {ns.lower()}.")
    flash(f"Request {ns.lower()}.","success" if decision=="approve" else "info")
    return redirect(url_for("admin_requests"))

@app.route("/admin/blockchain")
@admin_required
def admin_blockchain():
    conn=db.get_db(); blocks=conn.execute("SELECT * FROM blockchain ORDER BY block_index DESC").fetchall(); conn.close()
    return render_template("admin/blockchain.html",blocks=blocks)

@app.route("/admin/history")
@admin_required
def admin_history():
    conn=db.get_db(); rows=conn.execute("SELECT * FROM history ORDER BY id DESC").fetchall(); conn.close()
    return render_template("admin/history.html",history=rows)

PROTECTED = {"Logged in","Logged out"}

# Actions that represent a user's actual "work" on the platform. These entries
# can never be deleted from a user's own Activity History (individually, or
# via "clear all") — only lightweight/informational entries can be removed.
MAJOR_WORK_ACTIONS = {
    "Logged in", "Logged out",
    "Dataset uploaded", "Dataset imported (JSON)", "Dataset deleted",
    "Dataset edited", "Dataset renamed",
    "Cleaned dataset with AI",
    "Edit access requested", "Request approved", "Request rejected",
    "Generated AI Report", "Downloaded AI Report (PDF)", "Downloaded AI Report (Excel)",
}

@app.route("/admin/history/delete/<int:history_id>", methods=["POST"])
@admin_required
def admin_delete_history(history_id):
    conn=db.get_db(); entry=conn.execute("SELECT * FROM history WHERE id=?",(history_id,)).fetchone()
    if not entry: conn.close(); abort(404)
    if entry["action"] in PROTECTED: conn.close(); flash("Login/logout records can't be deleted.","danger"); return redirect(url_for("admin_history"))
    conn.execute("DELETE FROM history WHERE id=?",(history_id,)); conn.commit(); conn.close()
    flash("Entry deleted.","info"); return redirect(url_for("admin_history"))

# ──────────────────────────────────────────────────────────────────────
# Admin: User Activity (time-on-portal + action log per user)
# ──────────────────────────────────────────────────────────────────────
@app.route("/admin/user-activity")
@admin_required
def admin_user_activity():
    conn = db.get_db()
    users = conn.execute(
        "SELECT * FROM users WHERE role='user' ORDER BY name COLLATE NOCASE"
    ).fetchall()
    summary = []
    for u in users:
        action_count = conn.execute(
            "SELECT COUNT(*) c FROM history WHERE admin_id=?", (u["id"],)
        ).fetchone()["c"]
        session_count = conn.execute(
            "SELECT COUNT(*) c FROM login_sessions WHERE user_id=?", (u["id"],)
        ).fetchone()["c"]
        is_online = conn.execute(
            "SELECT COUNT(*) c FROM login_sessions WHERE user_id=? AND logout_at IS NULL", (u["id"],)
        ).fetchone()["c"] > 0
        summary.append({
            "user": u,
            "total_seconds": db.total_time_spent(u["id"]),
            "action_count": action_count,
            "session_count": session_count,
            "is_online": is_online,
        })
    conn.close()
    return render_template("admin/user_activity.html", summary=summary)

@app.route("/admin/user-activity/<int:user_id>")
@admin_required
def admin_user_activity_detail(user_id):
    conn = db.get_db()
    user = conn.execute("SELECT * FROM users WHERE id=? AND role='user'", (user_id,)).fetchone()
    if not user:
        conn.close(); abort(404)

    actions = conn.execute(
        "SELECT * FROM history WHERE admin_id=? ORDER BY id DESC LIMIT 200", (user_id,)
    ).fetchall()
    sessions = conn.execute(
        "SELECT * FROM login_sessions WHERE user_id=? ORDER BY id DESC LIMIT 100", (user_id,)
    ).fetchall()
    uploads = conn.execute(
        "SELECT COUNT(*) c FROM datasets WHERE uploaded_by=? AND source_type='user_upload'", (user_id,)
    ).fetchone()["c"]
    req_total = conn.execute(
        "SELECT COUNT(*) c FROM requests WHERE user_id=?", (user_id,)
    ).fetchone()["c"]
    req_approved = conn.execute(
        "SELECT COUNT(*) c FROM requests WHERE user_id=? AND status='Approved'", (user_id,)
    ).fetchone()["c"]
    chat_count = conn.execute(
        "SELECT COUNT(*) c FROM chat_sessions WHERE user_id=?", (user_id,)
    ).fetchone()["c"]
    conn.close()

    # Action-type breakdown (for pie/bar chart)
    action_breakdown = {}
    for a in actions:
        action_breakdown[a["action"]] = action_breakdown.get(a["action"], 0) + 1

    # Daily time spent for the last 14 days (for line chart) — built from
    # closed sessions, attributing each session's duration to its login date.
    from collections import OrderedDict
    from datetime import timedelta
    today = datetime.now().date()
    daily = OrderedDict()
    for i in range(13, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        daily[d] = 0
    for s in sessions:
        if s["login_at"] and s["duration_seconds"]:
            day = s["login_at"][:10]
            if day in daily:
                daily[day] += s["duration_seconds"]

    chart_data = {
        "actionLabels": list(action_breakdown.keys()),
        "actionValues": list(action_breakdown.values()),
        "dayLabels": list(daily.keys()),
        "dayMinutes": [round(v/60, 1) for v in daily.values()],
    }

    return render_template("admin/user_activity_detail.html",
        target_user=user,
        actions=actions,
        sessions=sessions,
        total_seconds=db.total_time_spent(user_id),
        uploads=uploads,
        req_total=req_total,
        req_approved=req_approved,
        chat_count=chat_count,
        chart_data=json.dumps(chart_data))

@app.route("/admin/user-activity/<int:user_id>/reset", methods=["POST"])
@admin_required
def admin_reset_user_activity(user_id):
    """Clear all session history and action log for one user, keeping the account intact."""
    conn = db.get_db()
    user = conn.execute("SELECT * FROM users WHERE id=? AND role='user'", (user_id,)).fetchone()
    if not user:
        conn.close(); abort(404)
    # Wipe login sessions for this user
    conn.execute("DELETE FROM login_sessions WHERE user_id=?", (user_id,))
    # Wipe action history entries (non-login) for this user
    conn.execute("DELETE FROM history WHERE admin_id=?", (user_id,))
    # Wipe chat sessions + messages
    sids = [r["id"] for r in conn.execute("SELECT id FROM chat_sessions WHERE user_id=?", (user_id,)).fetchall()]
    for sid in sids:
        conn.execute("DELETE FROM chat_messages WHERE session_id=?", (sid,))
    conn.execute("DELETE FROM chat_sessions WHERE user_id=?", (user_id,))
    conn.execute("DELETE FROM chat_history WHERE user_id=?", (user_id,))
    # Reset login timestamps on the user row
    conn.execute("UPDATE users SET last_login=NULL, current_login=NULL WHERE id=?", (user_id,))
    conn.commit()
    conn.close()
    db.log_history(session["user_id"], session["name"], "User activity reset", user["name"])
    flash(f"All activity data for '{user['name']}' has been reset.", "success")
    return redirect(url_for("admin_user_activity"))


@app.route("/admin/clear-all-login-data", methods=["POST"])
@admin_required
def admin_clear_all_login_data():
    """Wipe ALL login_sessions rows and login/logout history entries across every user.
    User accounts, datasets and blockchain are preserved."""
    conn = db.get_db()
    conn.execute("DELETE FROM login_sessions")
    conn.execute("DELETE FROM history WHERE action IN ('Logged in','Logged out')")
    conn.execute("UPDATE users SET last_login=NULL, current_login=NULL")
    conn.commit()
    conn.close()
    db.log_history(session["user_id"], session["name"], "All login data cleared",
                   "Admin wiped login_sessions and login/logout history")
    flash("All login session data has been cleared.", "success")
    return redirect(url_for("admin_user_activity"))


@app.route("/admin/profile")
@admin_required
def admin_profile():
    conn=db.get_db(); user=conn.execute("SELECT * FROM users WHERE id=?",(session["user_id"],)).fetchone()
    stats={"datasets":conn.execute("SELECT COUNT(*) c FROM datasets").fetchone()["c"],
           "pending":conn.execute("SELECT COUNT(*) c FROM requests WHERE status='Pending'").fetchone()["c"],
           "blocks":conn.execute("SELECT COUNT(*) c FROM blockchain").fetchone()["c"]}
    conn.close(); return render_template("admin/profile.html",user=user,stats=stats)

@app.route("/admin/profile/edit", methods=["POST"])
@admin_required
def admin_edit_profile():
    name=request.form.get("name","").strip(); email=request.form.get("email","").strip().lower()
    bio=request.form.get("bio","").strip(); new_pw=request.form.get("new_password",""); av_file=request.files.get("avatar")
    if not name or not email: flash("Name and email required.","danger"); return redirect(url_for("admin_profile"))
    conn=db.get_db()
    if conn.execute("SELECT id FROM users WHERE email=? AND id!=?",(email,session["user_id"])).fetchone():
        conn.close(); flash("Email in use.","danger"); return redirect(url_for("admin_profile"))
    av_fn=session.get("avatar")
    if av_file and av_file.filename:
        ext=av_file.filename.rsplit(".",1)[-1].lower()
        if ext not in ALLOWED_AVATAR: conn.close(); flash("Invalid image type.","danger"); return redirect(url_for("admin_profile"))
        av_fn=f"admin_{session['user_id']}_{datetime.now().strftime('%Y%m%d%H%M%S')}.{ext}"
        av_file.save(os.path.join(AVATAR_FOLDER,av_fn))
    if new_pw:
        conn.execute("UPDATE users SET name=?,email=?,bio=?,avatar=?,password_hash=? WHERE id=?",(name,email,bio,av_fn,generate_password_hash(new_pw),session["user_id"]))
    else:
        conn.execute("UPDATE users SET name=?,email=?,bio=?,avatar=? WHERE id=?",(name,email,bio,av_fn,session["user_id"]))
    conn.commit(); conn.close()
    session["name"]=name; session["email"]=email; session["avatar"]=av_fn
    db.log_history(session["user_id"],name,"Profile updated")
    flash("Profile updated.","success"); return redirect(url_for("admin_profile"))



# ──────────────────────────────────────────────────────────────────────
# User: Request edit access to admin dataset
# ──────────────────────────────────────────────────────────────────────
@app.route("/user/datasets/request-access", methods=["POST"])
@user_required
def user_request_edit_access():
    dataset_id       = request.form.get("dataset_id","").strip()
    requested_change = request.form.get("requested_change","").strip()
    if not dataset_id or not requested_change:
        flash("Please provide a reason for your request.","danger")
        return redirect(url_for("user_dataset_selection"))

    conn=db.get_db()
    ds=conn.execute("SELECT * FROM datasets WHERE id=?",(dataset_id,)).fetchone()
    if not ds:
        conn.close(); flash("Dataset not found.","danger"); return redirect(url_for("user_dataset_selection"))

    # Check for existing pending request
    existing=conn.execute(
        "SELECT * FROM requests WHERE user_id=? AND dataset_id=? AND status='Pending'",
        (session["user_id"], dataset_id)
    ).fetchone()
    if existing:
        conn.close(); flash("You already have a pending request for this dataset.","warning")
        return redirect(url_for("user_dataset_selection"))

    conn.execute(
        "INSERT INTO requests (user_id, dataset_id, requested_change) VALUES (?,?,?)",
        (session["user_id"], dataset_id, requested_change)
    )
    conn.commit()

    # Notify all admins
    admins=conn.execute("SELECT id FROM users WHERE role='admin'").fetchall()
    for a in admins:
        db.add_notification(a["id"],
            f"{session['name']} requested edit access to '{ds['name']}'")
    conn.close()

    db.add_block(f"Edit access requested: '{ds['name']}' by {session['name']}")
    db.log_history(session["user_id"], session["name"], "Edit access requested", ds["name"])
    flash(f"Request sent for '{ds['name']}'. You'll be notified once approved.","success")
    return redirect(url_for("user_my_requests"))


# ──────────────────────────────────────────────────────────────────────
# User: Edit dataset page (requires approved request OR own upload)
# ──────────────────────────────────────────────────────────────────────
@app.route("/user/datasets/<int:dataset_id>/edit")
@user_required
def user_edit_dataset_page(dataset_id):
    conn=db.get_db()
    ds=conn.execute("SELECT * FROM datasets WHERE id=?",(dataset_id,)).fetchone()
    if not ds: conn.close(); abort(404)

    # Access check: own upload OR approved request
    if ds["source_type"]=="user_upload" and ds["uploaded_by"]==session["user_id"]:
        pass  # allowed
    else:
        approved=conn.execute(
            "SELECT * FROM requests WHERE user_id=? AND dataset_id=? AND status='Approved'",
            (session["user_id"], dataset_id)
        ).fetchone()
        if not approved:
            conn.close()
            flash("You need approved edit access to edit this dataset. Submit a request first.","warning")
            return redirect(url_for("user_dataset_selection"))

    conn.close()
    headers, rows = load_dataset(ds["source_type"], str(dataset_id))[0:2]
    # Locked datasets stay viewable but read-only: once the admin locks a dataset
    # after approving access, the requester can look but can't change it again
    # until the admin unlocks it.
    if ds["is_locked"]:
        flash("This dataset is locked by the admin — you can view it but can't make changes right now.","warning")
    return render_template("user/edit_dataset.html",
        dataset=ds, headers=headers, rows=rows, is_locked=bool(ds["is_locked"]))


# ──────────────────────────────────────────────────────────────────────
# User: Save edited dataset
# ──────────────────────────────────────────────────────────────────────
@app.route("/user/datasets/<int:dataset_id>/save", methods=["POST"])
@user_required
def user_save_dataset(dataset_id):
    conn=db.get_db()
    ds=conn.execute("SELECT * FROM datasets WHERE id=?",(dataset_id,)).fetchone()
    if not ds: conn.close(); abort(404)

    # Access check
    if ds["source_type"]=="user_upload" and ds["uploaded_by"]==session["user_id"]:
        pass
    else:
        approved=conn.execute(
            "SELECT * FROM requests WHERE user_id=? AND dataset_id=? AND status='Approved'",
            (session["user_id"], dataset_id)
        ).fetchone()
        if not approved:
            conn.close(); abort(403)

    if ds["is_locked"]:
        conn.close()
        flash("This dataset is locked by the admin and can't be changed until it's unlocked.","danger")
        return redirect(url_for("user_edit_dataset_page", dataset_id=dataset_id))

    csv_data = request.form.get("csv_data","")
    if not csv_data.strip():
        flash("No data received. Please try again.","danger")
        return redirect(url_for("user_edit_dataset_page", dataset_id=dataset_id))

    # The editor always saves back as CSV text, regardless of the original upload
    # format. If the dataset was originally an .xlsx/.xls/.json file, keeping the
    # old extension would leave a file whose name says one format but whose actual
    # content is CSV — every later read (dashboard, visualize, etc.) would then try
    # to parse it as that old format and fail/come back empty. So: if the stored
    # file isn't already a .csv, write the new content to a fresh .csv file and
    # update the dataset's filename/filepath in the DB, then remove the old file.
    old_filepath = os.path.join(UPLOAD_FOLDER, ds["filepath"])
    current_ext = ds["filepath"].rsplit(".",1)[-1].lower() if "." in ds["filepath"] else ""
    if current_ext == "csv":
        new_filepath = old_filepath
        new_relpath  = ds["filepath"]
        new_filename = ds["filename"]
    else:
        base_name    = ds["filepath"].rsplit(".",1)[0]
        new_relpath  = f"{base_name}.csv"
        new_filepath = os.path.join(UPLOAD_FOLDER, new_relpath)
        fn_base      = ds["filename"].rsplit(".",1)[0] if "." in ds["filename"] else ds["filename"]
        new_filename = f"{fn_base}.csv"

    try:
        with open(new_filepath,"w",newline="",encoding="utf-8") as f:
            f.write(csv_data)
        if new_filepath != old_filepath and os.path.exists(old_filepath):
            os.remove(old_filepath)
    except Exception as e:
        conn.close()
        flash(f"Error saving file: {str(e)}","danger")
        return redirect(url_for("user_edit_dataset_page", dataset_id=dataset_id))

    if new_relpath != ds["filepath"]:
        conn.execute("UPDATE datasets SET filepath=?, filename=? WHERE id=?",
                     (new_relpath, new_filename, dataset_id))
        conn.commit()

    conn.close()
    db.add_block(f"Dataset edited: '{ds['name']}' by {session['name']}")
    db.log_history(session["user_id"], session["name"], "Dataset edited", ds["name"])

    # Notify admins
    conn2=db.get_db()
    admins=conn2.execute("SELECT id FROM users WHERE role='admin'").fetchall()
    for a in admins:
        db.add_notification(a["id"], f"{session['name']} saved changes to '{ds['name']}'")
    conn2.close()

    flash(f"Dataset '{ds['name']}' saved successfully!","success")
    return redirect(url_for("user_edit_dataset_page", dataset_id=dataset_id))


# ══════════════════════════════════════════════════════════════════════
#  AI FEATURES  -  Business Recommendations . Data Storytelling .
#                  Report Generator . Predictive Analytics . Data Cleaning
# ══════════════════════════════════════════════════════════════════════
def _ai_dataset():
    """Loads (headers, rows, name) for the dataset referenced in the request
    (works for both form POSTs and query-string GETs)."""
    ds_type = request.values.get("type", "")
    ds_id   = request.values.get("id", "")
    if not ds_type or not ds_id:
        return [], [], "", ds_type, ds_id
    headers, rows, name = load_dataset(ds_type, ds_id)
    return headers, rows, name, ds_type, ds_id


@app.route("/user/ai/business", methods=["POST"])
@user_required
def ai_business_recommendations():
    headers, rows, name, _t, _i = _ai_dataset()
    if not name:
        return jsonify({"available": False, "message": "No dataset loaded."})
    result = ai.business_recommendations(headers, rows)
    db.log_history(session["user_id"], session["name"], "Generated AI Business Recommendation", name)
    return jsonify(result)


@app.route("/user/ai/story", methods=["POST"])
@user_required
def ai_data_storytelling():
    headers, rows, name, _t, _i = _ai_dataset()
    if not name:
        return jsonify({"available": False, "message": "No dataset loaded."})
    result = ai.data_story(headers, rows)
    db.log_history(session["user_id"], session["name"], "Generated AI Data Story", name)
    return jsonify(result)


@app.route("/user/ai/predict", methods=["POST"])
@user_required
def ai_predictive_analytics():
    headers, rows, name, _t, _i = _ai_dataset()
    if not name:
        return jsonify({"available": False, "message": "No dataset loaded."})
    column = request.values.get("column") or None
    metric_hint = request.values.get("metric") or None
    periods = request.values.get("periods", 6)
    result = ai.predictive_forecast(headers, rows, column=column, metric_hint=metric_hint, periods=periods)
    db.log_history(session["user_id"], session["name"], "Ran Predictive Analytics", name)
    return jsonify(result)


@app.route("/user/ai/quality", methods=["POST"])
@user_required
def ai_data_quality():
    headers, rows, name, _t, _i = _ai_dataset()
    if not name:
        return jsonify({"has_issues": False, "message": "No dataset loaded."})
    result = ai.data_quality_report(headers, rows)
    return jsonify(result)


@app.route("/user/ai/clean", methods=["POST"])
@user_required
def ai_clean_dataset():
    headers, rows, name, ds_type, ds_id = _ai_dataset()
    if not name:
        return jsonify({"success": False, "message": "No dataset loaded."})

    cleaned_rows, report = ai.clean_dataset(headers, rows)

    new_filename = datetime.now().strftime("%Y%m%d%H%M%S") + "_cleaned_" + secure_filename(name) + ".csv"
    filepath = os.path.join(UPLOAD_FOLDER, new_filename)
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(cleaned_rows)

    clean_name = name + " (Cleaned)"
    conn = db.get_db()
    cur = conn.execute(
        "INSERT INTO datasets (name,description,filename,filepath,uploaded_by,source_type) VALUES (?,?,?,?,?,?)",
        (clean_name, "Auto-cleaned by AI Data Cleaning Module", new_filename, new_filename,
         session["user_id"], "user_upload"))
    new_id = cur.lastrowid
    conn.commit(); conn.close()

    db.add_block("Dataset cleaned by AI Data Cleaning Module: '" + name + "' -> '" + clean_name + "'")
    db.log_history(session["user_id"], session["name"], "Cleaned dataset with AI", name)

    return jsonify({
        "success": True, "report": report,
        "new_dataset_id": new_id, "new_dataset_name": clean_name,
        "redirect_url": url_for("user_visualize", type="user_upload", id=new_id),
        "download_url": url_for("download_dataset", dataset_id=new_id),
        "dashboard_url": url_for("user_dashboard", type="user_upload", id=new_id),
    })


@app.route("/user/ai/report", methods=["GET"])
@user_required
def ai_report_data():
    headers, rows, name, _t, _i = _ai_dataset()
    if not name:
        return jsonify({"available": False, "message": "No dataset loaded."})
    report = ai.build_full_report(headers, rows, name)
    db.log_history(session["user_id"], session["name"], "Generated AI Report", name)
    return jsonify({"available": True, "report": report})


@app.route("/user/ai/report/pdf", methods=["GET"])
@user_required
def ai_report_pdf():
    headers, rows, name, _t, _i = _ai_dataset()
    if not name:
        abort(404)
    report = ai.build_full_report(headers, rows, name)
    buf = report_export.build_pdf(report)
    safe_name = secure_filename(name) or "report"
    db.log_history(session["user_id"], session["name"], "Downloaded AI Report (PDF)", name)
    return send_file(buf, mimetype="application/pdf", as_attachment=True,
                      download_name=safe_name + "_ai_report.pdf")


@app.route("/user/ai/report/excel", methods=["GET"])
@user_required
def ai_report_excel():
    headers, rows, name, _t, _i = _ai_dataset()
    if not name:
        abort(404)
    report = ai.build_full_report(headers, rows, name)
    buf = report_export.build_excel(report)
    safe_name = secure_filename(name) or "report"
    db.log_history(session["user_id"], session["name"], "Downloaded AI Report (Excel)", name)
    return send_file(buf, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                      as_attachment=True, download_name=safe_name + "_ai_report.xlsx")


@app.errorhandler(403)
def forbidden(e): return render_template("error.html",code=403,message="You don't have access to this page."),403

@app.errorhandler(404)
def not_found(e): return render_template("error.html",code=404,message="Page not found."),404

if __name__=="__main__":
    app.run(debug=True,host="0.0.0.0",port=5000)
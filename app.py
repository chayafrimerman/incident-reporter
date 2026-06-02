from flask import Flask, request, jsonify, send_from_directory
import requests
from flask_cors import CORS
import os
import uuid
import json
import hashlib
import smtplib
import random
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from pathlib import Path
from utils import (ANTHROPIC_API_KEY,
    phase1_extract, phase2_check, phase2_update_state,
    phase3_check, phase4_check, phase4_update_who, finalize,
    EMPTY_STATE,
    emp_phase2_check, emp_phase2_update_state,
    emp_phase4_check, emp_finalize,
    EMPTY_STATE_EMPLOYEE, FORM_KEY, PROJECT_KEY, EMPLOYEE_FORM_KEY
)
from credentials import get_cached_token, get_db_conn,  DOFORMS_BASE, IMAP_EMAIL, IMAP_APP_PASSWORD


# ── Email / SMTP config ─────────────────────────────────────────
# Set these in your environment or credentials file.
# Example for Gmail: use an App Password (not your real password).
# AFTER
SMTP_HOST     = "smtp.gmail.com"
SMTP_PORT     = 587
SMTP_USER     = IMAP_EMAIL       
SMTP_PASSWORD = IMAP_APP_PASSWORD 
SMTP_FROM     = IMAP_EMAIL

# How long (seconds) an OTP stays valid
OTP_TTL = 600   # 10 minutes

# How long (seconds) a device token stays valid
DEVICE_TOKEN_TTL = 60 * 60 * 24 * 365 * 20   


app = Flask(__name__, static_folder="static")
app.secret_key = os.urandom(24)
CORS(app, supports_credentials=True)

# ── File logging ─────────────────────────────────────────────────
import logging
_log_path = Path(__file__).parent / "server.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(_log_path, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

# ── In-memory stores ─────────────────────────────────────────────
# OTP store: { email: { "otp_hash": str, "expires": float } }
_otp_store = {}

# Chat session store (unchanged)
_sessions = {}

# ── Persistent device token store ────────────────────────────────
_TOKENS_FILE = Path(__file__).parent / ".device_tokens.json"

def _load_tokens():
    if _TOKENS_FILE.exists():
        try:
            data = json.loads(_TOKENS_FILE.read_text())
            # Drop already-expired tokens on load
            now = time.time()
            return {k: v for k, v in data.items() if v.get("expires", 0) > now}
        except Exception:
            pass
    return {}

def _save_tokens(tokens):
    try:
        _TOKENS_FILE.write_text(json.dumps(tokens))
    except Exception as e:
        print(f"[AUTH] Warning: could not save device tokens: {e}")

_device_tokens = _load_tokens()


# ════════════════════════════════════════════════════════════════
# AUTH HELPERS
# ════════════════════════════════════════════════════════════════

def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _email_exists(email: str) -> bool:
    """Return True if email is @opusoperations.com OR matches employee.email / personal_email."""
    
    if email.endswith("@opusoperations.com"):
        return True
    
    try:
        conn   = get_db_conn()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT TOP 1 1
            FROM employee
            WHERE (LOWER(email) = LOWER(?) OR LOWER(personal_email) = LOWER(?))
              AND employee_status != 'T'
            """,
            email, email
        )
        found = cursor.fetchone() is not None
        conn.close()
        return found
    except Exception as e:
        print(f"[AUTH] DB error checking email: {e}")
        return False


def _send_otp_email(to_email: str, otp: str):
    """Send a plain OTP email via SMTP."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Your Opus Operations login code"
    msg["From"]    = SMTP_FROM
    msg["To"]      = to_email

    text_body = f"""\
Your one-time login code for Opus Operations Incident Reporter:

    {otp}

This code expires in 10 minutes. Do not share it with anyone.
"""
    html_body = f"""\
<html><body style="font-family:sans-serif;background:#0f1117;color:#e8eaf0;padding:32px;">
  <div style="max-width:400px;margin:0 auto;background:#181c27;border:1px solid #252a38;border-radius:12px;padding:32px;">
    <div style="background:#e8622a;border-radius:8px;width:40px;height:40px;display:flex;align-items:center;
                justify-content:center;font-weight:600;color:#fff;font-size:14px;margin-bottom:20px;">OO</div>
    <h2 style="margin:0 0 8px;font-size:18px;">Your login code</h2>
    <p style="color:#6b7280;font-size:13px;margin:0 0 24px;">Opus Operations · Incident Reporter</p>
    <div style="background:#13161f;border:1px solid #252a38;border-radius:10px;padding:20px;
                text-align:center;font-family:monospace;font-size:32px;letter-spacing:8px;
                color:#e8622a;font-weight:600;">{otp}</div>
    <p style="color:#6b7280;font-size:12px;margin:20px 0 0;text-align:center;">
      Expires in 10 minutes &nbsp;·&nbsp; Do not share this code
    </p>
  </div>
</body></html>
"""
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.ehlo()
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_FROM, to_email, msg.as_string())


# ════════════════════════════════════════════════════════════════
# AUTH ROUTES
# ════════════════════════════════════════════════════════════════

@app.route("/api/auth/request-otp", methods=["POST"])
def auth_request_otp():
    """
    Body: { "email": "user@example.com" }
    Validates email against employee table, generates OTP, sends it.
    """
    data  = request.json or {}
    email = (data.get("email") or "").strip().lower()

    if not email:
        return jsonify({"error": "Email is required."}), 400

    if not _email_exists(email):
        return jsonify({"error": "invalid_email"}), 403

    # Generate 6-digit OTP
    otp = str(random.randint(100000, 999999))

    # Store hashed OTP
    _otp_store[email] = {
        "otp_hash": _hash(otp),
        "expires":  time.time() + OTP_TTL,
    }

    try:
        _send_otp_email(email, otp)
    except Exception as e:
        print(f"[AUTH] Failed to send OTP email to {email}: {e}")
        return jsonify({"error": "Failed to send email. Please try again."}), 500

    print(f"[AUTH] OTP sent to {email}")
    return jsonify({"ok": True})


@app.route("/api/auth/verify-otp", methods=["POST"])
def auth_verify_otp():
    """
    Body: { "email": "...", "otp": "123456" }
    On success: sets a long-lived device_token cookie.
    """
    data  = request.json or {}
    email = (data.get("email") or "").strip().lower()
    otp   = (data.get("otp")   or "").strip()

    record = _otp_store.get(email)
    if not record:
        return jsonify({"error": "No OTP was requested for this email."}), 400

    if time.time() > record["expires"]:
        del _otp_store[email]
        return jsonify({"error": "Code expired. Please request a new one."}), 400

    if _hash(otp) != record["otp_hash"]:
        return jsonify({"error": "Incorrect code. Please try again."}), 400

    # Valid — clean up
    del _otp_store[email]

    # Issue device token (long-lived)
    token = str(uuid.uuid4())
    _device_tokens[token] = {
        "email":   email,
        "expires": time.time() + DEVICE_TOKEN_TTL,
    }
    _save_tokens(_device_tokens)

    resp = jsonify({"ok": True, "email": email})
    resp.set_cookie(
        "device_token", token,
        max_age=DEVICE_TOKEN_TTL,
        httponly=True,
        samesite="Lax",
        secure = True,
    )
    print(f"[AUTH] Verified OTP for {email}, device token issued.")
    return resp


@app.route("/api/auth/check", methods=["GET"])
def auth_check():
    """
    Called on page load. Returns { ok: true, email } if device token is valid,
    otherwise { ok: false }.
    """
    token  = request.cookies.get("device_token")
    record = _device_tokens.get(token) if token else None

    if record and time.time() < record["expires"]:
        return jsonify({"ok": True, "email": record["email"]})

    # Expired or missing — remove stale entry
    if token and token in _device_tokens:
        del _device_tokens[token]
        _save_tokens(_device_tokens)

    return jsonify({"ok": False})


@app.route("/api/auth/logout", methods=["POST"])
def auth_logout():
    """Clears device token cookie and removes from store."""
    token = request.cookies.get("device_token")
    if token and token in _device_tokens:
        del _device_tokens[token]
        _save_tokens(_device_tokens)
    resp = jsonify({"ok": True})
    resp.set_cookie("device_token", "", expires=0)
    return resp


# ════════════════════════════════════════════════════════════════
# EXISTING CODE — unchanged below this line
# ════════════════════════════════════════════════════════════════

def save_conversation(sid, state, conversation):
    log_dir = Path(__file__).parent / "conversation_logs"
    log_dir.mkdir(exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = log_dir / f"{timestamp}_{sid[:8]}.json"
    
    payload = {
        "session_id": sid,
        "timestamp": datetime.now().isoformat(),
        "final_state": state,
        "conversation": conversation,
    }
    
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    
    print(f"[LOG] Conversation saved to {filename}")


def get_session_id():
    sid = request.cookies.get("incident_session")
    if not sid or sid not in _sessions:
        sid = str(uuid.uuid4())
    return sid


def get_or_create_session(sid):
    if sid not in _sessions:
        _sessions[sid] = {
            "state":       __import__("copy").deepcopy(EMPTY_STATE),
            "conversation": [],
            "phase":        1,
            "report_type":  None,   # set after phase 1 classification
            "phase2_turns": 0,
            "phase3_turns": 0,
            "phase4_turns": 0,
        }
    return _sessions[sid]


# ── Dropdown data endpoints ──────────────────────────────────────

@app.route("/api/dropdown/addresses", methods=["GET"])
def dropdown_addresses():
    try:
        conn   = get_db_conn()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT DISTINCT name 
            FROM customer 
            WHERE name IS NOT NULL
              AND in_drop_down = 1
              AND active = 1
            ORDER BY name
        """)
        names = [row[0] for row in cursor.fetchall()]
        conn.close()
        return jsonify(names)
    except Exception as e:
        print("DB error (dropdown addresses):", e)
        return jsonify([]), 500


@app.route("/api/dropdown/customers", methods=["GET"])
def dropdown_customers():
    name = request.args.get("name", "").strip()
    if not name:
        return jsonify([])
    try:
        conn   = get_db_conn()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT DISTINCT parent_name FROM customer WHERE name = ? AND parent_name IS NOT NULL ORDER BY parent_name",
            name
        )
        parents = [row[0] for row in cursor.fetchall()]
        conn.close()
        return jsonify(parents)
    except Exception as e:
        print("DB error (dropdown customers):", e)
        return jsonify([]), 500


@app.route("/api/dropdown/employees", methods=["GET"])
def dropdown_employees():
    try:
        conn   = get_db_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT full_name FROM employee WHERE full_name IS NOT NULL AND employee_status != 'T' ORDER BY full_name")
        names = [row[0] for row in cursor.fetchall()]
        conn.close()
        return jsonify(names)
    except Exception as e:
        print("DB error (dropdown employees):", e)
        return jsonify([]), 500


# ── Static frontend ──────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory("static", "index.html")


# ── Chat endpoint (phased state machine) ────────────────────────
@app.route("/api/chat", methods=["POST"])
def chat():
    if not ANTHROPIC_API_KEY:
        return jsonify({"error": "ANTHROPIC_API_KEY not set"}), 500

    data     = request.json
    messages = data.get("messages", [])

    sid  = get_session_id()
    sess = get_or_create_session(sid)
    sess["sid"] = sid
    print(f"[SESSION] sid={sid[:8]}... phase={sess['phase']} turns={len(sess['conversation'])}")

    state        = sess["state"]
    conversation = sess["conversation"]
    phase        = sess["phase"]

    if not messages:
        resp = jsonify({"reply": "Hi! What happened? Tell me as much detail as you can."})
        resp.set_cookie("incident_session", sid, samesite="Lax", secure = True)
        return resp

    latest_user_msg = messages[-1]["content"]

    if phase == 1:
        conversation.append({"role": "user", "content": latest_user_msg})

        extracted = phase1_extract(latest_user_msg)
        report_type = extracted.pop("report_type", "incident")
        multi = extracted.pop("multi_incident", "")

        # Set session report type and re-initialize state if employee occurrence
        sess["report_type"] = report_type
        if report_type == "employee_occurrence":
            import copy
            sess["state"] = copy.deepcopy(EMPTY_STATE_EMPLOYEE)
            state = sess["state"]
            for key in EMPTY_STATE_EMPLOYEE:
                if key in extracted and extracted[key] not in ("", [], None):
                    state[key] = extracted[key]
            # "what" from phase1 seeds reason_for_action
            if extracted.get("what") and not state.get("reason_for_action"):
                state["reason_for_action"] = extracted["what"]
        else:
            for key in EMPTY_STATE:
                if key in extracted and extracted[key] not in ("", [], None):
                    state[key] = extracted[key]

        sess["phase"] = 2

        # multi_incident check only applies to incident reports
        if report_type == "incident" and multi and not state.get("multi_incident_handled"):
            q = f"You mentioned a prior incident: \"{multi}\" — was a report already filed for that?"
            conversation.append({"role": "assistant", "content": q})
            sess["_pending_multi"] = multi
            reply = q
        else:
            reply = _advance_phase2(sess)

        resp = jsonify({"reply": reply})
        resp.set_cookie("incident_session", sid, samesite="Lax")
        return resp

    conversation.append({"role": "user", "content": latest_user_msg})

    if sess.get("_pending_multi"):
        multi = sess.pop("_pending_multi")
        lower = latest_user_msg.lower()
        if any(w in lower for w in ["yes", "already", "filed", "done", "yep", "yeah"]):
            state["previous_incidents"] = ""
        else:
            state["previous_incidents"] = f"Undocumented prior incident: {multi}"
        state["multi_incident_handled"] = True

    if phase == 2:
        reply = _advance_phase2(sess)
    elif phase == 3:
        reply = _advance_phase3(sess)
    elif phase == 4:
        reply = _advance_phase4(sess)
    elif phase == 5:
        import copy
        _sessions[sid] = {
            "state":        copy.deepcopy(EMPTY_STATE),
            "conversation": [],
            "phase":        1,
            "report_type":  None,
            "phase2_turns": 0,
            "phase3_turns": 0,
            "phase4_turns": 0,
        }
        sess = _sessions[sid]
        sess["conversation"].append({"role": "user", "content": latest_user_msg})
        extracted = phase1_extract(latest_user_msg)
        report_type = extracted.pop("report_type", "incident")
        extracted.pop("multi_incident", "")
        sess["report_type"] = report_type
        if report_type == "employee_occurrence":
            sess["state"] = copy.deepcopy(EMPTY_STATE_EMPLOYEE)
            for key in EMPTY_STATE_EMPLOYEE:
                if key in extracted and extracted[key] not in ("", [], None):
                    sess["state"][key] = extracted[key]
            if extracted.get("what") and not sess["state"].get("reason_for_action"):
                sess["state"]["reason_for_action"] = extracted["what"]
        else:
            for key in EMPTY_STATE:
                if key in extracted and extracted[key] not in ("", [], None):
                    sess["state"][key] = extracted[key]
        sess["phase"] = 2
        reply = _advance_phase2(sess)
    else:
        reply = "Something went wrong. Please refresh and start over."

    resp = jsonify({"reply": reply})
    resp.set_cookie("incident_session", sid, samesite="Lax", secure = True)
    return resp


# ── Phase helpers ────────────────────────────────────────────────

def _advance_phase2(sess):
    state        = sess["state"]
    conversation = sess["conversation"]
    report_type  = sess.get("report_type", "incident")

    sess["phase2_turns"] = sess.get("phase2_turns", 0) + 1
    if sess["phase2_turns"] > 12:
        if report_type == "employee_occurrence":
            sess["state"] = emp_phase2_update_state(state, conversation)
        else:
            sess["state"] = phase2_update_state(state, conversation)
        sess["phase"] = 3
        return _advance_phase3(sess)

    if report_type == "employee_occurrence":
        check = emp_phase2_check(state, conversation)
    else:
        check = phase2_check(state, conversation)

    if check.get("done"):
        if report_type == "employee_occurrence":
            sess["state"] = emp_phase2_update_state(state, conversation)
        else:
            sess["state"] = phase2_update_state(state, conversation)
        sess["phase"] = 3
        return _advance_phase3(sess)

    q = check.get("question", "Can you tell me more about what happened?")
    conversation.append({"role": "assistant", "content": q})
    return q


def _advance_phase3(sess):
    state        = sess["state"]
    conversation = sess["conversation"]
    report_type  = sess.get("report_type", "incident")

    sess["phase3_turns"] = sess.get("phase3_turns", 0) + 1
    if sess["phase3_turns"] > 8:
        sess["phase"] = 4
        return _advance_phase4(sess)

    check = phase3_check(state, conversation)

    if check.get("done"):
        if check.get("when"):
            state["when"] = check["when"]
            sess["state"] = state
        sess["phase"] = 4
        return _advance_phase4(sess)

    default_q = ("What was the exact date and time of the occurrence?"
                 if report_type == "employee_occurrence"
                 else "What was the exact date and time of the incident?")
    q = check.get("question", default_q)
    conversation.append({"role": "assistant", "content": q})
    return q


def _advance_phase4(sess):
    state        = sess["state"]
    conversation = sess["conversation"]
    report_type  = sess.get("report_type", "incident")

    sess["state"]["who"] = phase4_update_who(state, conversation)
    state = sess["state"]

    sess["phase4_turns"] = sess.get("phase4_turns", 0) + 1
    if sess["phase4_turns"] > 12:
        return _generate_report(sess)

    if report_type == "employee_occurrence":
        check = emp_phase4_check(state, conversation)
    else:
        check = phase4_check(state, conversation)

    if check.get("done"):
        return _generate_report(sess)

    q = check.get("question", "Can you confirm who was involved?")
    conversation.append({"role": "assistant", "content": q})
    return q


def _generate_report(sess):
    sess["phase"] = 5
    report_type = sess.get("report_type", "incident")
    if report_type == "employee_occurrence":
        result = emp_finalize(sess["state"])
    else:
        result = finalize(sess["state"])
    json_str = json.dumps(result, indent=2)
    save_conversation(sess.get("sid", "unknown"), sess["state"], sess["conversation"])
    return f"Got it — let me write that up for you.\n\nSTORY_READY\n```json\n{json_str}\n```"


# ── Reset session ────────────────────────────────────────────────
@app.route("/api/reset", methods=["POST"])
def reset_session():
    sid = get_session_id()
    if sid in _sessions:
        del _sessions[sid]
    resp = jsonify({"ok": True})
    resp.set_cookie("incident_session", "", expires=0)
    return resp


# ── Submit to doForms ────────────────────────────────────────────
@app.route("/api/submit", methods=["POST"])
def submit():
    data = request.json

    # Route employee occurrence reports to a separate handler
    if data.get("report_type") == "employee_occurrence":
        return _submit_employee_occurrence(data)

    try:
        token = get_cached_token()
    except Exception as e:
        return jsonify({"error": f"Token error: {e}"}), 500

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    # Determine Report_Type from Incident_Type (fallback if AI didn't set it)
    _incident_type = data.get("Incident_Type", "")
    _jobsite_types = {"Trespassing / Unathorized access", "Disturbance", "Missing / Stolen Package", "Resident Issue"}
    _emergency_types = {"Criminal Activity", "Violence and Altercations", "Emergencies"}
    if data.get("Report_Type"):
        report_type = data["Report_Type"]
    elif _incident_type in _jobsite_types:
        report_type = "Jobsite Incident Report"
    elif _incident_type in _emergency_types:
        report_type = "Emergency Incident Report"
    else:
        report_type = ""

    # Follow_Up_Actions_Needed: AI-generated next steps + any manual additions
    follow_up_parts = [
        data.get("Follow_Up_Actions_Needed", ""),
        data.get("Additional_Information", ""),
        data.get("Previous_Undocumented_Incidents", ""),
    ]
    follow_up = "\n\n".join(p for p in follow_up_parts if p.strip())

    # Build Email_To recipient list
    _supervisor = data.get("Name_Of_Supervisor", "").strip()
    # _email_list = [
    #     "hr@opusoperations.com",
    #     "reports@opusoperations.com",
    #     "aharon@opusoperations.com",
    #     "thomas@opusoperations.com",
    #     "jasonw@opusoperations.com",
    # ]
    _email_list = [
        "chayaf@opusoperations.com",
    ]
    if _supervisor == "Stacy Nunez":
        # _email_list.append("stacyn@opusoperations.com")
        _email_list.append("mushkafrimsch@gmail.com")
    # elif _supervisor == "Jesus Ramos":
    #     _email_list.extend(["agusting@opusoperations.com", "yidi@opusoperations.com"])
    user_email = ";".join(_email_list)

    def fmt_dt(value):
        """Format a datetime-local string as MM/DD/YYYY HH:MM:SS AM/PM."""
        if not value:
            return ""
        clean = value.strip().replace("Z", "").split(".")[0].replace(" ", "T")
        if len(clean) == 16:
            clean += ":00"
        try:
            return datetime.fromisoformat(clean).strftime("%m/%d/%Y %I:%M:%S %p")
        except Exception:
            return value

    print("Incoming data keys:", list(data.keys()))
    print("Date_Time_Of_Incident:", data.get("Date_Time_Of_Incident"))
    print("Date_Time_Of_Report:", data.get("Date_Time_Of_Report"))

    # Build nested payload mirroring the form's grid/section structure:
    # Top-level: Email_To
    # Section "untitled2": Jobsite_Address, Customer_Name, Date_Time_of_Report, Name_of_Supervisor
    # Section "Incident_Information": Employee_Name, Report_Type, Incident_Type,
    #   Unit_Number_or_Location, What_Happened, Who_Was_Notified,
    #   Date_Time_of_Incident, How_Was_it_Resolved, Follow_Up_Actions_Needed

    def tf(value):
        """Return a text field dict, or None if empty."""
        return {"text": value} if value else None

    section_untitled2 = []
    for fname, val in [
        ("Jobsite_Address",      data.get("Job_Address", "")),
        ("Customer_Name",        data.get("Customer_Name", "")),
        ("Date_Time_of_Report",  fmt_dt(data.get("Date_Time_Of_Report", ""))),
        ("Name_of_Supervisor",   data.get("Name_Of_Supervisor", "")),
    ]:
        if val:
            section_untitled2.append({"name": fname, "text": val})

    section_incident = []
    for fname, val in [
        ("Employee_Name",            data.get("Employee_name", "")),
        ("Report_Type",              report_type),
        ("Incident_Type",            data.get("Incident_Type", "")),
        ("Unit_Number_or_Location",  data.get("Unit_Number_Or_Location", "")),
        ("What_Happened",            data.get("Describe_What_Happened", "")),
        ("Who_Was_Notified",         data.get("Who_Was_Notified", "")),
        ("Date_Time_of_Incident",    fmt_dt(data.get("Date_Time_Of_Incident", ""))),
        ("How_Was_it_Resolved",      data.get("How_Was_It_Resolved", "")),
        ("Follow_Up_Actions_Needed", follow_up),
    ]:
        if val:
            section_incident.append({"name": fname, "text": val})

    fields = []
    if section_untitled2:
        fields.append({"name": "untitled2", "fields": section_untitled2})
    if section_incident:
        fields.append({"name": "Incident_Information", "fields": section_incident})
    payload = {
        "formKey": FORM_KEY,
        "projectKey": PROJECT_KEY,
        "fields": fields,
    }
    print("Submitting payload:", json.dumps(payload, indent=2))
    r = requests.post(f"{DOFORMS_BASE}/api/v2/submissions", headers=headers, json=payload)
    print("doForms submit status:", r.status_code)
    print("doForms submit response:", r.text)

    if r.status_code in (200, 201):
        sid = get_session_id()
        if sid in _sessions:
            del _sessions[sid]
        return jsonify({"success": True, "response": r.json()})
    else:
        return jsonify({"success": False, "error": r.text}), r.status_code


# ── Employee Occurrence submit helper ────────────────────────────
def _submit_employee_occurrence(data):
    """Submit an Employee Occurrence Report to doForms (Employee_Occurance_Test form).

    Form structure (from get_employee_form.py):
      Section "untitled2": Employee_Name, Employee_Title, Name_of_Supervisor,
                           Incident_Type, Date_Time_of_Incident, Date_Time_of_Communication
      Top-level: Reason_for_Action, Action_Taken (strings), Conversation_Summary_and_Expec,
                 Employee_s_Reaction, Email_To
    """
    try:
        token = get_cached_token()
    except Exception as e:
        return jsonify({"error": f"Token error: {e}"}), 500

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    def fmt_dt(value):
        if not value:
            return ""
        clean = value.strip().replace("Z", "").split(".")[0].replace(" ", "T")
        if len(clean) == 16:
            clean += ":00"
        try:
            return datetime.fromisoformat(clean).strftime("%m/%d/%Y %I:%M:%S %p")
        except Exception:
            return value

    # ── Section "untitled2" ──────────────────────────────────────
    section_untitled2 = []
    for fname, val in [
        ("Employee_Name",              data.get("Employee_name", "")),
        ("Employee_Title",             data.get("Employee_Title", "")),
        ("Name_of_Supervisor",         data.get("Name_Of_Supervisor", "")),
        ("Incident_Type",              data.get("Incident_Type", "")),
        ("Date_Time_of_Incident",      fmt_dt(data.get("Date_Time_Of_Incident", ""))),
        ("Date_Time_of_Communication", fmt_dt(data.get("Date_Time_Of_Report", ""))),
    ]:
        if val:
            section_untitled2.append({"name": fname, "text": val})

    # ── Top-level fields ─────────────────────────────────────────
    fields = []
    if section_untitled2:
        fields.append({"name": "untitled2", "fields": section_untitled2})

    if data.get("Reason_for_Action"):
        fields.append({"name": "Reason_for_Action", "text": data["Reason_for_Action"]})

    # Action_Taken is a "strings" (multi-select) field in doForms — value is a list from the frontend
    action_taken = data.get("Action_Taken", [])
    if isinstance(action_taken, str):
        action_taken = [action_taken] if action_taken else []
    if action_taken:
        fields.append({"name": "Action_Taken", "strings": action_taken})

    if data.get("Conversation_Summary_and_Expec"):
        fields.append({"name": "Conversation_Summary_and_Expec", "text": data["Conversation_Summary_and_Expec"]})

    if data.get("Employee_Reaction"):
        fields.append({"name": "Employee_s_Reaction", "text": data["Employee_Reaction"]})

    # Photos — submitted as base64 blobs to the Attachments field
    photos = data.get("photos", [])
    for i, photo in enumerate(photos):
        fields.append({
            "name": "Attachments",
            "blob": photo.get("data", ""),
            "filename": photo.get("filename", f"photo_{i+1}.jpg"),
            "content_type": photo.get("content_type", "image/jpeg"),
        })

    # Email recipients
    _email_list = ["chayaf@opusoperations.com"]
    fields.append({"name": "Email_To", "text": ";".join(_email_list)})

    payload = {
        "formKey":    EMPLOYEE_FORM_KEY,
        "projectKey": PROJECT_KEY,
        "fields":     fields,
    }

    # Strip photo data from log to keep it readable
    log_payload = json.loads(json.dumps(payload))
    for f in log_payload.get("fields", []):
        if f.get("name") == "Attachments":
            f["blob"] = f"<{len(payload.get('fields',[]))} bytes>"
    log.info("EMPLOYEE OCCURRENCE SUBMIT payload: %s", json.dumps(log_payload, indent=2))
    r = requests.post(f"{DOFORMS_BASE}/api/v2/submissions", headers=headers, json=payload)
    log.info("EMPLOYEE OCCURRENCE SUBMIT status: %s", r.status_code)
    log.info("EMPLOYEE OCCURRENCE SUBMIT response: %s", r.text)

    if r.status_code in (200, 201):
        sid = get_session_id()
        if sid in _sessions:
            del _sessions[sid]
        return jsonify({"success": True, "response": r.json()})
    else:
        return jsonify({"success": False, "error": r.text}), r.status_code


def _send_incident_email(recipients_str, data, report_type):
    """Send incident report notification email to all recipients."""
    subject = f"[{report_type or 'Incident Report'}] {data.get('Incident_Type', '')} — {data.get('Job_Address', '')}"

    body_lines = [
        f"<b>Report Type:</b> {report_type}",
        f"<b>Incident Type:</b> {data.get('Incident_Type', '')}",
        f"<b>Address:</b> {data.get('Job_Address', '')}",
        f"<b>Customer:</b> {data.get('Customer_Name', '')}",
        f"<b>Supervisor:</b> {data.get('Name_Of_Supervisor', '')}",
        f"<b>Employee:</b> {data.get('Employee_name', '')}",
        f"<b>Unit / Location:</b> {data.get('Unit_Number_Or_Location', '')}",
        f"<b>Date/Time of Incident:</b> {data.get('Date_Time_Of_Incident', '')}",
        f"<b>Date/Time of Report:</b> {data.get('Date_Time_Of_Report', '')}",
        "",
        f"<b>What Happened:</b><br>{data.get('Describe_What_Happened', '').replace(chr(10), '<br>')}",
        "",
        f"<b>Who Was Notified:</b><br>{data.get('Who_Was_Notified', '')}",
        "",
        f"<b>How Was It Resolved:</b><br>{data.get('How_Was_It_Resolved', '').replace(chr(10), '<br>')}",
    ]
    if data.get("Additional_Information"):
        body_lines += ["", f"<b>Additional Information:</b><br>{data['Additional_Information']}"]
    if data.get("Previous_Undocumented_Incidents"):
        body_lines += ["", f"<b>Prior Undocumented Incidents:</b><br>{data['Previous_Undocumented_Incidents']}"]

    html_body = f"""
<html><body style="font-family:sans-serif;font-size:14px;color:#222;line-height:1.6;">
  <div style="max-width:700px;margin:0 auto;border:1px solid #ddd;border-radius:8px;padding:24px;">
    <div style="background:#e8622a;color:#fff;padding:12px 16px;border-radius:6px;margin-bottom:20px;">
      <strong>Opus Operations — Incident Report</strong>
    </div>
    {'<br>'.join(body_lines)}
    <hr style="margin-top:24px;border:none;border-top:1px solid #eee;">
    <p style="color:#888;font-size:12px;">Submitted via Opus Operations Incident Reporter</p>
  </div>
</body></html>"""

    text_body = "\n".join(
        line.replace("<br>", "\n").replace("<b>", "").replace("</b>", "")
        for line in body_lines
    )

    recipients = [r.strip() for r in recipients_str.replace(",", ";").split(";") if r.strip()]
    if not recipients:
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = SMTP_FROM
    msg["To"]      = ", ".join(recipients)
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.ehlo()
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_FROM, recipients, msg.as_string())
    print(f"[EMAIL] Incident report sent to: {', '.join(recipients)}")


@app.route("/api/feedback", methods=["POST"])
def feedback():
    sid  = request.cookies.get("incident_session")
    data = request.json
    fb   = data.get("feedback", "").strip()
    if not fb or not sid:
        return jsonify({"ok": False})

    log_dir = Path(__file__).parent / "conversation_logs"
    matches = sorted(log_dir.glob(f"*_{sid[:8]}.json"), reverse=True)
    if not matches:
        return jsonify({"ok": False, "error": "Log file not found"})
    
    log_file = matches[0]
    with open(log_file, "r", encoding="utf-8") as f:
        payload = json.load(f)
    
    payload["feedback"] = fb
    payload["feedback_timestamp"] = datetime.now().isoformat()
    
    with open(log_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    
    print(f"[FEEDBACK] Saved to {log_file}")
    return jsonify({"ok": True})


@app.route("/api/logs")
def view_logs():
    """View recent server logs — checks server.log and nohup.out."""
    base = Path(__file__).parent
    candidates = [base / "server.log", base / "nohup.out"]
    content = []
    for p in candidates:
        content.append(f"=== {p} ===")
        if p.exists():
            try:
                lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
                content.extend(lines[-150:])
            except Exception as e:
                content.append(f"  [read error: {e}]")
        else:
            content.append("  [file not found]")
        content.append("")
    html = "<pre style='font-family:monospace;font-size:12px;padding:16px;white-space:pre-wrap;word-break:break-all'>"
    html += "\n".join(content)
    html += "</pre>"
    return html


@app.route("/robots.txt")
def robots():
    return send_from_directory("static", "robots.txt")


if __name__ == "__main__":
    app.run(debug=False, port=5000)
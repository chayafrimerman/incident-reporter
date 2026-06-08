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
from credentials import get_cached_token, get_db_conn, DOFORMS_BASE, EMAIL, APP_PASSWORD
from io import BytesIO
import base64 as b64mod
from email.mime.application import MIMEApplication
from pdf_generator import generate_incident_pdf, generate_employee_occurrence_pdf


# ── Email / SMTP config ─────────────────────────────────────────
# Set these in your environment or credentials file.
# Example for Gmail: use an App Password (not your real password).
# AFTER
SMTP_HOST     = "smtp.gmail.com"
SMTP_PORT     = 587
SMTP_USER     = EMAIL
SMTP_PASSWORD = APP_PASSWORD
SMTP_FROM     = EMAIL

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


# ── Email recipients preview ─────────────────────────────────────
@app.route("/api/email-recipients", methods=["GET"])
def email_recipients():
    report_type = request.args.get("report_type", "incident")
    supervisor  = request.args.get("supervisor", "")
    if report_type == "employee_occurrence":
        recipients = _employee_occurrence_recipients()
    else:
        recipients = _incident_recipients(supervisor)
    return jsonify({"recipients": recipients, "testing": TESTING_MODE})


# ── Submit ────────────────────────────────────────────────────────
@app.route("/api/submit", methods=["POST"])
def submit():
    data = request.json

    # Route employee occurrence reports to a separate handler
    if data.get("report_type") == "employee_occurrence":
        return _submit_employee_occurrence(data)

    log.info("Incident submit — keys: %s", list(data.keys()))

    sid = get_session_id()

    # 1 — Generate PDF + upload to get link
    pdf_link = ""
    pdf_bytes = None
    try:
        incident_type = data.get("Incident_Type", "")
        address       = data.get("Job_Address", "")
        ts_label      = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename      = f"Incident_Report_{ts_label}.pdf"
        pdf_bytes     = generate_incident_pdf(data)
        pdf_link      = _upload_pdf_report(pdf_bytes, filename)
        log.info("Incident PDF uploaded: %s", pdf_link)
    except Exception as e:
        log.error("Failed to generate/upload incident PDF: %s", e)

    # 2 — Save to SQL Server (with link)
    _save_incident_to_db(data, sid, link=pdf_link)

    # 3 — Email PDF
    try:
        if pdf_bytes:
            incident_type = data.get("Incident_Type", "")
            address       = data.get("Job_Address", "")
            ts_label      = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename      = f"Incident_Report_{ts_label}.pdf"
            subject  = f"[Incident Report] {incident_type} — {address}"
            link_line = f'<p><a href="{pdf_link}">View report online</a></p>' if pdf_link else ""
            html_intro = f"""
<html><body style="font-family:sans-serif;font-size:14px;color:#222;line-height:1.6;">
  <div style="max-width:680px;margin:0 auto;border:1px solid #ddd;border-radius:8px;padding:24px;">
    <div style="background:#00D2CB;color:#fff;padding:12px 16px;border-radius:6px;margin-bottom:16px;">
      <strong>Opus Operations — Incident Report</strong>
    </div>
    <p>An Incident Report has been submitted. The full report is attached as a PDF.</p>
    <p style="color:#555;font-size:13px;">
      <b>Incident Type:</b> {incident_type}<br>
      <b>Address:</b> {address}<br>
      <b>Employee:</b> {data.get('Employee_name','')}<br>
      <b>Supervisor:</b> {data.get('Name_Of_Supervisor','')}
    </p>
    {link_line}
    <hr style="border:none;border-top:1px solid #eee;margin-top:20px;">
    <p style="color:#aaa;font-size:11px;">Submitted via Opus Operations Incident Reporter</p>
  </div>
</body></html>"""
            extras     = [e.strip() for e in data.get("extra_recipients", []) if e.strip()]
            recipients = _incident_recipients(data.get("Name_Of_Supervisor", "")) + extras
            _send_pdf_report_email(recipients, subject, html_intro, pdf_bytes, filename)
    except Exception as e:
        log.error("Failed to email incident PDF: %s", e)

    # 3 — Clear session
    if sid in _sessions:
        del _sessions[sid]

    return jsonify({"success": True})


# ── Email recipient logic ────────────────────────────────────────
# Set to True to send to full recipient lists; False = test mode (chayaf only)
TESTING_MODE = True

_INCIDENT_BASE = [
    "hr@opusoperations.com",
    "reports@opusoperations.com",
    "aharon@opusoperations.com",
    "thomas@opusoperations.com",
    "jasonw@opusoperations.com",
]
_SUPERVISOR_EXTRAS = {
    "stacy nunez":  ["stacyn@opusoperations.com"],
    "jesus ramos":   ["agusting@opusoperations.com", "yidi@opusoperations.com"],
}
_EMPLOYEE_OCCURRENCE_BASE = [
    "hr@opusoperations.com",
    "reports@opusoperations.com",
    "susank@opusoperations.com",
    "aharon@opusoperations.com",
    "thomas@opusoperations.com",
    "jasonw@opusoperations.com",
    "carlosc@opusoperations.com",
]

def _incident_recipients(supervisor: str) -> list:
    recipients = list(_INCIDENT_BASE)
    extras = _SUPERVISOR_EXTRAS.get(supervisor.strip().lower(), [])
    recipients.extend(extras)
    if TESTING_MODE:
        return ["chayaf@opusoperations.com"]
    return recipients

def _employee_occurrence_recipients() -> list:
    if TESTING_MODE:
        return ["chayaf@opusoperations.com"]
    return list(_EMPLOYEE_OCCURRENCE_BASE)


# ── DB / PDF / Email helpers ─────────────────────────────────────

def _parse_dt(v):
    """Parse a datetime-local string to a datetime object, or None."""
    if not v:
        return None
    clean = v.strip().replace("Z", "").split(".")[0].replace(" ", "T")
    if len(clean) == 16:
        clean += ":00"
    try:
        return datetime.fromisoformat(clean)
    except Exception:
        return None


def _save_incident_to_db(data, sid="", link=""):
    """Insert an incident report row into SQL Server."""
    try:
        conn   = get_db_conn()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO dbo.incident_reports2 (
                session_id, employee_name, supervisor_name, job_address, customer_name,
                report_type, incident_type, unit_location,
                date_time_incident, date_time_report,
                what_happened, who_notified, how_resolved,
                follow_up_actions, additional_info, previous_incidents, link
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            sid,
            data.get("Employee_name", ""),
            data.get("Name_Of_Supervisor", ""),
            data.get("Job_Address", ""),
            data.get("Customer_Name", ""),
            data.get("Report_Type", ""),
            data.get("Incident_Type", ""),
            data.get("Unit_Number_Or_Location", ""),
            _parse_dt(data.get("Date_Time_Of_Incident", "")),
            _parse_dt(data.get("Date_Time_Of_Report", "")),
            data.get("Describe_What_Happened", ""),
            data.get("Who_Was_Notified", ""),
            data.get("How_Was_It_Resolved", ""),
            data.get("Follow_Up_Actions_Needed", ""),
            data.get("Additional_Information", ""),
            data.get("Previous_Undocumented_Incidents", ""),
            link,
        )
        conn.commit()
        conn.close()
        log.info("Incident report saved to DB (session=%s)", sid)
    except Exception as e:
        log.error("Failed to save incident to DB: %s", e)


def _save_employee_occurrence_to_db(data, sid="", photo_count=0, link=""):
    """Insert an employee occurrence report row into SQL Server."""
    action_taken = data.get("Action_Taken", [])
    if isinstance(action_taken, str):
        action_taken = [action_taken] if action_taken else []
    action_str = ", ".join(action_taken)

    try:
        conn   = get_db_conn()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO dbo.employee_occurrence_reports (
                session_id, employee_name, employee_title, supervisor_name,
                incident_type, date_time_incident, date_time_report,
                reason_for_action, action_taken,
                conversation_summary, employee_reaction, photo_count, link
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            sid,
            data.get("Employee_name", ""),
            data.get("Employee_Title", ""),
            data.get("Name_Of_Supervisor", ""),
            data.get("Incident_Type", ""),
            _parse_dt(data.get("Date_Time_Of_Incident", "")),
            _parse_dt(data.get("Date_Time_Of_Report", "")),
            data.get("Reason_for_Action", ""),
            action_str,
            data.get("Conversation_Summary_and_Expec", ""),
            data.get("Employee_Reaction", ""),
            photo_count,
            link,
        )
        conn.commit()
        conn.close()
        log.info("Employee occurrence report saved to DB (session=%s)", sid)
    except Exception as e:
        log.error("Failed to save employee occurrence to DB: %s", e)


def _send_pdf_report_email(recipients, subject, html_intro, pdf_bytes, pdf_filename):
    """Send an email with a PDF report attached."""
    if isinstance(recipients, str):
        recipients = [r.strip() for r in recipients.replace(",", ";").split(";") if r.strip()]

    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"]    = SMTP_FROM
    msg["To"]      = ", ".join(recipients)

    # Body
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText("Please see the attached PDF report.", "plain"))
    alt.attach(MIMEText(html_intro, "html"))
    msg.attach(alt)

    # PDF attachment
    pdf_part = MIMEApplication(pdf_bytes, _subtype="pdf")
    pdf_part.add_header("Content-Disposition", "attachment", filename=pdf_filename)
    msg.attach(pdf_part)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.ehlo()
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_FROM, recipients, msg.as_string())

    log.info("PDF report emailed to %s (%s)", recipients, pdf_filename)


# ── Employee Occurrence submit helper ────────────────────────────
def _submit_employee_occurrence(data):
    """Save an Employee Occurrence Report to SQL, generate a PDF, and email it."""
    photos    = data.get("photos", [])
    sid       = get_session_id()

    log.info("Employee occurrence submit — employee=%s photos=%d",
             data.get("Employee_name", ""), len(photos))

    # 1 — Generate PDF + upload to get link
    pdf_link = ""
    pdf_bytes = None
    employee   = data.get("Employee_name", "")
    action_raw = data.get("Action_Taken", [])
    if isinstance(action_raw, str):
        action_raw = [action_raw] if action_raw else []
    action_str = ", ".join(action_raw)
    try:
        ts_label  = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename  = f"Employee_Occurrence_{ts_label}.pdf"
        pdf_bytes = generate_employee_occurrence_pdf(data)
        pdf_link  = _upload_pdf_report(pdf_bytes, filename)
        log.info("Employee occurrence PDF uploaded: %s", pdf_link)
    except Exception as e:
        log.error("Failed to generate/upload employee occurrence PDF: %s", e)

    # 2 — Save to SQL Server (with link)
    _save_employee_occurrence_to_db(data, sid, len(photos), link=pdf_link)

    # 3 — Email PDF
    try:
        if pdf_bytes:
            ts_label   = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename   = f"Employee_Occurrence_{ts_label}.pdf"
            subject    = f"[Employee Occurrence] {employee} — {action_str}"
            link_line  = f'<p><a href="{pdf_link}">View report online</a></p>' if pdf_link else ""
            html_intro = f"""
<html><body style="font-family:sans-serif;font-size:14px;color:#222;line-height:1.6;">
  <div style="max-width:680px;margin:0 auto;border:1px solid #ddd;border-radius:8px;padding:24px;">
    <div style="background:#00D2CB;color:#fff;padding:12px 16px;border-radius:6px;margin-bottom:16px;">
      <strong>Opus Operations — Employee Occurrence Report</strong>
    </div>
    <p>An Employee Occurrence Report has been submitted. The full report is attached as a PDF.</p>
    <p style="color:#555;font-size:13px;">
      <b>Employee:</b> {employee}<br>
      <b>Supervisor:</b> {data.get('Name_Of_Supervisor','')}<br>
      <b>Action Taken:</b> {action_str}<br>
      <b>Incident Type:</b> {data.get('Incident_Type','')}
    </p>
    {'<p style="font-size:13px;"><b>' + str(len(photos)) + ' photo(s) embedded in the attached PDF.</b></p>' if photos else ''}
    {link_line}
    <hr style="border:none;border-top:1px solid #eee;margin-top:20px;">
    <p style="color:#aaa;font-size:11px;">Submitted via Opus Operations Incident Reporter</p>
  </div>
</body></html>"""
            extras     = [e.strip() for e in data.get("extra_recipients", []) if e.strip()]
            recipients = _employee_occurrence_recipients() + extras
            _send_pdf_report_email(recipients, subject, html_intro, pdf_bytes, filename)
    except Exception as e:
        log.error("Failed to email employee occurrence PDF: %s", e)

    # 3 — Clear session
    if sid in _sessions:
        del _sessions[sid]

    return jsonify({"success": True})


def _upload_pdf_report(pdf_bytes, filename):
    """Upload a PDF to the Opus file server and return the public URL."""
    files = {
        "file": (filename, pdf_bytes, "application/pdf"),
        "type": (None, "pdf"),
    }
    r = requests.post("https://dev.opusoperations.com/upload/upload-file", files=files)
    r.raise_for_status()
    return r.text.strip()


def _upload_photo(photo, base_url="https://reports.opusoperations.com"):
    # Decode image bytes
    img_bytes = b64mod.b64decode(photo["data"])
    img_buffer = BytesIO(img_bytes)

    # Get image dimensions
    img_reader = ImageReader(img_buffer)
    img_w, img_h = img_reader.getSize()

    # Create a PDF sized to the image
    pdf_buffer = BytesIO()
    c = canvas.Canvas(pdf_buffer, pagesize=(img_w, img_h))
    img_buffer.seek(0)
    c.drawImage(ImageReader(img_buffer), 0, 0, width=img_w, height=img_h)
    c.save()
    pdf_buffer.seek(0)
    pdf_bytes = pdf_buffer.read()

    # Upload as PDF using the working path
    filename = photo.get("filename", "photo").rsplit(".", 1)[0] + ".pdf"
    files = {
        "file": (filename, pdf_bytes, "application/pdf"),
        "type": (None, "pdf"),
    }
    r = requests.post("https://dev.opusoperations.com/upload/upload-file", files=files)
    r.raise_for_status()
    return r.text.strip()


def _send_employee_photos_email(recipients, data, photos):
    """Email photos from an employee occurrence report as attachments."""
    import base64
    from email.mime.image import MIMEImage

    employee  = data.get("Employee_name", "Unknown Employee")
    action    = ", ".join(data.get("Action_Taken", [])) if isinstance(data.get("Action_Taken"), list) else data.get("Action_Taken", "")
    subject   = f"[Employee Occurrence] Photos — {employee}"

    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"]    = SMTP_FROM
    msg["To"]      = ", ".join(recipients)

    body = MIMEText(
        f"Photos attached for Employee Occurrence Report.\n\n"
        f"Employee: {employee}\n"
        f"Action Taken: {action}\n"
        f"Supervisor: {data.get('Name_Of_Supervisor', '')}\n\n"
        f"These photos were submitted alongside the doForms occurrence report.",
        "plain"
    )
    msg.attach(body)

    for photo in photos:
        try:
            img_data = base64.b64decode(photo["data"])
            img = MIMEImage(img_data, _subtype=photo.get("content_type", "image/jpeg").split("/")[-1])
            img.add_header("Content-Disposition", "attachment", filename=photo.get("filename", "photo.jpg"))
            msg.attach(img)
        except Exception as e:
            log.warning("Could not attach photo %s: %s", photo.get("filename"), e)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.ehlo()
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_FROM, recipients, msg.as_string())


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


@app.route("/api/view-photo")
def view_photo():
    """Proxy an uploaded photo and serve it inline (opens in browser instead of downloading)."""
    from urllib.parse import unquote
    from flask import Response, stream_with_context
    url = request.args.get("url", "")
    if not url.startswith("https://devstoragedocument.blob.core.windows.net/"):
        return "Invalid URL", 400
    try:
        r = requests.get(url, stream=True, timeout=15)
        r.raise_for_status()
        ctype = r.headers.get("Content-Type", "image/jpeg")
        return Response(
            stream_with_context(r.iter_content(chunk_size=8192)),
            content_type=ctype,
            headers={"Content-Disposition": "inline"},
        )
    except Exception as e:
        return f"Could not load photo: {e}", 502


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
from flask import Flask, request, jsonify, send_from_directory
import requests
from flask_cors import CORS
import os
import uuid
import json
from datetime import datetime
from pathlib import Path
from utils import (ANTHROPIC_API_KEY,
    phase1_extract, phase2_check, phase2_update_state,
    phase3_check, phase4_check, phase4_update_who, finalize,
    EMPTY_STATE
)
from credentials import get_cached_token,  get_db_conn

app = Flask(__name__, static_folder="static")
app.secret_key = os.urandom(24)
CORS(app, supports_credentials=True)

# ── In-memory session store (keyed by session_id cookie) ────────
# Structure: { session_id: { "state": {...}, "conversation": [...], "phase": int, "done": bool } }
_sessions = {}


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
            "state": __import__("copy").deepcopy(EMPTY_STATE),  # deep copy prevents shared who list
            "conversation": [],
            "phase": 1,          # 1=story, 2=narrative, 3=when, 4=people, 5=done
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

    # The latest user message
    if not messages:
        resp = jsonify({"reply": "Hi! What happened? Tell me as much detail as you can."})
        resp.set_cookie("incident_session", sid, samesite="Lax")
        return resp

    latest_user_msg = messages[-1]["content"]

    # ── PHASE 1: First message — extract from opening story ──────
    if phase == 1:
        conversation.append({"role": "user", "content": latest_user_msg})

        extracted = phase1_extract(latest_user_msg)
        multi = extracted.pop("multi_incident", "")

        for key in EMPTY_STATE:
            if key in extracted and extracted[key] not in ("", [], None):
                state[key] = extracted[key]

        sess["phase"] = 2

        # If a multi-incident was detected, ask about it first
        if multi and not state.get("multi_incident_handled"):
            q = f"You mentioned a prior incident: \"{multi}\" — was a report already filed for that?"
            conversation.append({"role": "assistant", "content": q})
            sess["_pending_multi"] = multi
            reply = q
        else:
            # Move straight into phase 2 check
            reply = _advance_phase2(sess)

        resp = jsonify({"reply": reply})
        resp.set_cookie("incident_session", sid, samesite="Lax")
        return resp

    # ── PHASES 2–4: Ongoing conversation ────────────────────────
    conversation.append({"role": "user", "content": latest_user_msg})

    # Handle pending multi-incident answer
    if sess.get("_pending_multi"):
        multi = sess.pop("_pending_multi")
        lower = latest_user_msg.lower()
        if any(w in lower for w in ["yes", "already", "filed", "done", "yep", "yeah"]):
            state["previous_incidents"] = ""
        else:
            state["previous_incidents"] = f"Undocumented prior incident: {multi}"
        state["multi_incident_handled"] = True

    # Run the appropriate phase
    if phase == 2:
        reply = _advance_phase2(sess)
    elif phase == 3:
        reply = _advance_phase3(sess)
    elif phase == 4:
        reply = _advance_phase4(sess)
    elif phase == 5:
        # Session already complete — wipe it and start fresh
        import copy
        _sessions[sid] = {
            "state": copy.deepcopy(EMPTY_STATE),
            "conversation": [],
            "phase": 1,
            "phase2_turns": 0,
            "phase3_turns": 0,
            "phase4_turns": 0,
        }
        sess = _sessions[sid]
        sess["conversation"].append({"role": "user", "content": latest_user_msg})
        extracted = phase1_extract(latest_user_msg)
        extracted.pop("multi_incident", "")
        for key in EMPTY_STATE:
            if key in extracted and extracted[key] not in ("", [], None):
                sess["state"][key] = extracted[key]
        sess["phase"] = 2
        reply = _advance_phase2(sess)
    else:
        reply = "Something went wrong. Please refresh and start over."

    resp = jsonify({"reply": reply})
    resp.set_cookie("incident_session", sid, samesite="Lax")
    return resp


# ── Phase helpers ────────────────────────────────────────────────

def _advance_phase2(sess):
    """Run one step of phase 2 (narrative completeness). Returns bot reply string."""
    state        = sess["state"]
    conversation = sess["conversation"]

    sess["phase2_turns"] = sess.get("phase2_turns", 0) + 1
    if sess["phase2_turns"] > 12:
        # Give up and move on
        state = phase2_update_state(state, conversation)
        sess["state"] = state
        sess["phase"] = 3
        return _advance_phase3(sess)

    check = phase2_check(state, conversation)

    if check.get("done"):
        # Update state from full conversation
        sess["state"] = phase2_update_state(state, conversation)
        sess["phase"] = 3
        return _advance_phase3(sess)

    q = check.get("question", "Can you tell me more about what happened?")
    conversation.append({"role": "assistant", "content": q})
    return q


def _advance_phase3(sess):
    """Run one step of phase 3 (date/time). Returns bot reply string."""
    state        = sess["state"]
    conversation = sess["conversation"]

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

    q = check.get("question", "What was the exact date and time of the incident?")
    conversation.append({"role": "assistant", "content": q})
    return q


def _advance_phase4(sess):
    """Run one step of phase 4 (people). Returns bot reply string or STORY_READY JSON."""
    state        = sess["state"]
    conversation = sess["conversation"]

    # Sync who array before checking
    sess["state"]["who"] = phase4_update_who(state, conversation)
    state = sess["state"]

    sess["phase4_turns"] = sess.get("phase4_turns", 0) + 1
    if sess["phase4_turns"] > 12:
        return _generate_report(sess)

    check = phase4_check(state, conversation)

    if check.get("done"):
        return _generate_report(sess)

    q = check.get("question", "Can you confirm who was involved?")
    conversation.append({"role": "assistant", "content": q})
    return q


def _generate_report(sess):
    """Run phase 5 — finalize and return STORY_READY payload."""
    sess["phase"] = 5
    result = finalize(sess["state"])

    # Build the STORY_READY string that the frontend expects
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


# # ── Submit to doForms ────────────────────────────────────────────
# @app.route("/api/submit", methods=["POST"])
# def submit():
#     data = request.json

#     try:
#         token = get_cached_token()
#     except Exception as e:
#         return jsonify({"error": f"Token error: {e}"}), 500

#     headers = {
#         "Authorization": f"Bearer {token}",
#         "Content-Type": "application/json",
#     }

#     field_map = {
#         "Address":         ("text",     data.get("Job_Address", "")),
#         "Customer":        ("text",     data.get("Customer_Name", "")),
#         "Supervisor":      ("text",     data.get("Name_Of_Supervisor", "")),
#         "Employee":        ("text",     data.get("Employee_name", "")),
#         "Incident_Type":   ("text",     data.get("Incident_Type", "")),
#         "Unit":            ("text",     data.get("Unit_Number_Or_Location", "")),
#         "What_Happened":   ("text",     data.get("Describe_What_Happened", "")),
#         "Notified":        ("text",     data.get("Who_Was_Notified", "")),
#         "Resolution":      ("text",     data.get("How_Was_It_Resolved", "")),
#         "Notes":           ("text",     data.get("Additional_Information", "")),
#         "Multi_Incident":  ("text",     data.get("Previous_Undocumented_Incidents", "")),
#         "Date_of_Incident": ("datetime", data.get("Date_Time_Of_Incident", "")),
#         "Date_of_Report":   ("datetime", data.get("Date_Time_Of_Report", "")),
#     }

#     fields = []
#     for name, (dtype, value) in field_map.items():
#         if value:
#             if dtype == "text":
#                 fields.append({"name": name, "text": value})
#             elif dtype == "datetime":
#                 clean_dt = value.replace("Z", "").split(".")[0]
#                 fields.append({"name": name, "dateTime": clean_dt})

#     payload = {
#         "formKey": FORM_KEY,
#         "projectKey": PROJECT_KEY,
#         "fields": fields,
#     }
#     print("Submitting payload:", json.dumps(payload, indent=2))
#     r = requests.post(f"{DOFORMS_BASE}/api/v2/submissions", headers=headers, json=payload)
#     print("doForms submit status:", r.status_code)
#     print("doForms submit response:", r.text)

#     if r.status_code in (200, 201):
#         # Clear the session after successful submit
#         sid = get_session_id()
#         if sid in _sessions:
#             del _sessions[sid]
#         return jsonify({"success": True, "response": r.json()})
#     else:
#         return jsonify({"success": False, "error": r.text}), r.status_code

# For testing purposes 
@app.route("/api/submit", methods=["POST"])
def submit():
    data = request.json
    print("=== MOCK SUBMIT (doForms disabled) ===")
    print(json.dumps(data, indent=2))
    return jsonify({"success": True, "response": {"mock": True}})


@app.route("/api/feedback", methods=["POST"])
def feedback():
    sid  = request.cookies.get("incident_session")
    data = request.json
    fb   = data.get("feedback", "").strip()
    if not fb or not sid:
        return jsonify({"ok": False})

    log_dir = Path(__file__).parent / "conversation_logs"
    
    # Find the most recent log file for this session
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


if __name__ == "__main__":
    app.run(debug=False, port=5000)
import json
import time
import requests
from datetime import datetime
from credentials import ANTHROPIC_API_KEY



FORM_KEY          = "ag9zfm15ZG9mb3Jtcy1ocmRyEQsSBEZvcm0YgICSgbi-4QoM"
PROJECT_KEY       = "ag9zfm15ZG9mb3Jtcy1ocmRyFAsSB1Byb2plY3QYgIDE0O_qzgoM"
EMPLOYEE_FORM_KEY = "ag9zfm15ZG9mb3Jtcy1ocmRyEQsSBEZvcm0YgICSkba90wgM"  # Employee_Occurance_Test

# ── Empty state (Incident Report) ───────────────────────────────
EMPTY_STATE = {
    "when":         "",
    "who":          [],
    "where":        "",
    "what":         "",
    "notification": "",
    "action_taken": "",
    "next_steps":   "",
    "multi_incident_handled": False,
    "previous_incidents": ""
}

# ── Empty state (Employee Occurrence Report) ─────────────────────
EMPTY_STATE_EMPLOYEE = {
    "when":               "",
    "who":                [],
    "action_taken":       "",   # What disciplinary action was taken
    "reason_for_action":  "",   # What the employee did / why action was taken
    "standards_expected": "",   # What was communicated and expected going forward
    "employee_reaction":  "",   # How the employee responded
    "violation_category": "",   # Time and Attendance / Job Performance / etc.
}


# ── Claude call ──────────────────────────────────────────────────
def call_claude(system_prompt, messages, max_tokens=1024, _retries=3):
    last_error = None
    for attempt in range(_retries):
        try:
            response = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": max_tokens,
                    "system": system_prompt,
                    "messages": messages,
                },
                timeout=60,
            )
            # Retry on overload/server errors
            if response.status_code in (429, 529, 500, 502, 503, 504):
                last_error = f"Claude API error {response.status_code}: {response.text}"
                time.sleep(2 ** attempt)
                continue
            if response.status_code != 200:
                raise Exception(f"Claude API error: {response.text}")
            text = response.json()["content"][0]["text"]
            if not text or not text.strip():
                last_error = "Claude API returned empty response"
                time.sleep(2 ** attempt)
                continue
            return text
        except requests.exceptions.Timeout:
            last_error = "Claude API request timed out"
            time.sleep(2 ** attempt)
            continue
    raise Exception(f"Claude API failed after {_retries} attempts: {last_error}")


def parse_json(raw):
    if not raw or not raw.strip():
        raise ValueError("Empty response passed to parse_json")
    clean = raw.strip()
    if "```" in clean:
        parts = clean.split("```")
        for part in parts:
            if part.startswith("json"):
                clean = part[4:].strip()
                break
            elif part.strip().startswith("{"):
                clean = part.strip()
                break
    start = clean.find("{")
    end = clean.rfind("}") + 1
    if start != -1 and end > start:
        clean = clean[start:end]
    return json.loads(clean)


def fmt_convo(conversation):
    lines = []
    for msg in conversation:
        role = "User" if msg["role"] == "user" else "Assistant"
        lines.append(f"{role}: {msg['content']}")
    return "\n".join(lines)


# ════════════════════════════════════════════════════════════════
# PHASE 1 — Initial extraction from the opening story
# ════════════════════════════════════════════════════════════════
def phase1_extract(story):
    today = datetime.now().strftime("%A, %B %d, %Y")
    system = f"""Today is {today}.

You are a report data extractor for Opus Operations (security/concierge company).

The user just described a situation. First classify the report type, then extract all information.

Return ONLY valid JSON with this exact structure — no markdown, no explanation:
{{
  "report_type": "incident",
  "when": "",
  "who": [],
  "where": "",
  "what": "",
  "notification": "",
  "action_taken": "",
  "next_steps": "",
  "multi_incident": "",
  "discipline_type": "",
  "violation_category": ""
}}

REPORT TYPE — classify first:
- "employee_occurrence": The story is primarily about an Opus Operations EMPLOYEE's MISCONDUCT or DISCIPLINARY INFRACTION. The employee is being disciplined or documented. Examples: arriving late, no-call/no-show, sleeping on the job, dress code violation, insubordination, unprofessional conduct, employee harassment. KEY: the report exists because the employee did something WRONG and action is being taken against them.
- "incident": Something that happened AT A PROPERTY or TO a person.  Examples: trespassing, disturbance, package theft, assault, fire, medical emergency, resident complaint, missing person, criminal activity, employee injured on the job. 

IMPORTANT: An employee getting hurt, falling, having a medical event, or being injured on the job is ALWAYS "incident" — never "employee_occurrence".

FIELD RULES:
- "when": Date and/or time if mentioned. Format: "May 14, 2026 at 7:00 AM". Leave blank if not mentioned.
- "who": Array of people: [{{"name": "John Smith", "title": "Security Guard"}}].
  Only use "Male Subject 1", "Female Subject 1", etc. if the person is completely anonymous. If any partial name was mentioned, use it.
- "where": Exact location if mentioned.
- "what": Full story of what happened - do not drop details.
- "notification": Who was notified, if mentioned.
- "action_taken": Actions already taken, if mentioned.
- "next_steps": Future actions, if mentioned.
- "multi_incident": ONLY populate if the user explicitly describes events on MORE THAN ONE separate date. Leave blank for a single incident.
- "discipline_type": Leave blank always — this is filled in manually by the supervisor.
- "violation_category": Only for employee_occurrence — "Time and Attendance" | "Job Performance" | "Professional Conduct" | "Harassment and Misconduct" | "Medical and Injury" | "Fraternization" | "Appearance" if clear from story, else "".

Never invent information. Only extract what was actually stated.
Don't drop details."""

    raw = call_claude(system, [{"role": "user", "content": story}], max_tokens=900)
    return parse_json(raw)
# ════════════════════════════════════════════════════════════════
# PHASE 2 — Story completion
# ════════════════════════════════════════════════════════════════
def phase2_check(state, conversation):
    today = datetime.now().strftime("%A, %B %d, %Y")
    system = f"""Today is {today}.
 
You are helping document an incident report for Opus Operations (security/concierge company).
 
CURRENT STATE:
{json.dumps(state, indent=2)}
 
CONVERSATION SO FAR:
{fmt_convo(conversation)}
 
Your job: decide if we have enough narrative detail to write a complete report.
 
We need all of these covered:
1. WHERE — must include a street address OR property name (either one is sufficient — do not ask for both). If the incident happened over the phone, by text, email, or any remote communication, do NOT ask for a location — set it as "Remote / Phone Call" and move on. If the user gave a street address like "22 Main Street", that is complete — do not ask for a property name on top of it.
2. WHAT happened (clear factual account)
3. WHO was notified
4. What ACTION was already taken
5. What NEXT STEPS are planned (or explicitly none)

IMPORTANT: Do NOT ask about previous incidents or prior reporting unless the user's own story explicitly mentioned events on more than one separate date. If their story is a single incident, never bring up prior incidents at all.
Do NOT ask about date or time under any circumstances — that is handled in a separate phase.
Don't drop details.

CRITICAL — NO REPEATED QUESTIONS: Before asking anything, carefully read the full conversation above. If a question has already been asked and answered — even if the answer was partial, "I don't know", or a variation — do NOT ask it again. Accept what was given and move on.

If anything from the list above is missing, pick the SINGLE most important missing piece and ask ONE question about it only.
No "and", no two-part questions.
CRITICAL: If the user has already answered a question — even with "I don't know", "the client didn't specify", "all of the above", or any variation — accept that answer and move on.

 
Return ONLY valid JSON — no markdown, no explanation:
- If more info needed: {{"done": false, "question": "your question here"}}
- If complete: {{"done": true}}"""
 
    raw = call_claude(system, [{"role": "user", "content": "Check if the narrative is complete."}], max_tokens=300)
    return parse_json(raw)
 
 
def phase2_update_state(state, conversation):
    today = datetime.now().strftime("%A, %B %d, %Y")
    system = f"""Today is {today}.
 
You are an incident data extractor for Opus Operations.
 
Based on this entire conversation, extract and compile the incident information.
 
CONVERSATION:
{fmt_convo(conversation)}
 
CURRENT STATE (merge/update this):
{json.dumps(state, indent=2)}
 
Return ONLY valid JSON — no markdown, no explanation:
{{
  "when": "",
  "who": [],
  "where": "",
  "what": "",
  "notification": "",
  "action_taken": "",
  "next_steps": "",
  "multi_incident_handled": false,
  "previous_incidents": ""
}}
 
RULES:
- Keep existing state values if the conversation didn't change them.
- Don't drop details.
- "what": Full story of what happened - do not drop details
- "who": full array [{{"name": "...", "title": "..."}}]
- "notification": "Nobody was notified" if user said no one was notified.
- "next_steps": "No further action planned" if user said nothing further.
- "where": must include a street address OR property name (either is sufficient — a street address alone like "22 Main Street" is complete, do not require a property name on top of it), plus a specific area within the building (unit, floor, lobby, etc.).
- "multi_incident_handled": true if a prior incident was discussed and resolved in the conversation.
- "previous_incidents": summarize any undocumented prior incident. Leave blank if already reported."""
 
    raw = call_claude(system, [{"role": "user", "content": "Extract state from conversation."}], max_tokens=800)
    extracted = parse_json(raw)
    if "multi_incident_handled" not in extracted:
        extracted["multi_incident_handled"] = state.get("multi_incident_handled", False)
    return extracted
 

# ════════════════════════════════════════════════════════════════
# PHASE 3 — When (date and time)
# ════════════════════════════════════════════════════════════════
def phase3_check(state, conversation):
    today = datetime.now().strftime("%A, %B %d, %Y")
    current_when = state.get("when", "")

    system = f"""Today is {today}.

You are helping document an incident report. We now need to confirm the exact date and time.

CURRENT "when" VALUE: "{current_when}"

CONVERSATION SO FAR (may contain date/time hints):
{fmt_convo(conversation)}

A complete "when" requires ALL of:
1. A specific date (month, day, year)
2. A specific time (hour and minute)
3. AM or PM explicitly confirmed

YEAR RULE: If no year is mentioned, assume the current year unless that date is in the future, in which case assume the previous year. Never ask for the year.

If any piece is still missing or ambiguous, ask ONE focused question to resolve it.
CRITICAL: If the user has already stated they don't know the date or time, or that it was not specified, accept that and mark it as done using whatever information is available

Return ONLY valid JSON — no markdown, no explanation:
- If still incomplete: {{"done": false, "question": "your question"}}
- If complete: {{"done": true, "when": "formatted full datetime string e.g. May 14, 2026 at 7:00 AM"}}"""

    raw = call_claude(system, [{"role": "user", "content": "Check if when is complete."}], max_tokens=200)
    return parse_json(raw)


# ════════════════════════════════════════════════════════════════
# PHASE 4 — People (names and titles)
# ════════════════════════════════════════════════════════════════
def phase4_check(state, conversation):
    today = datetime.now().strftime("%A, %B %d, %Y")

    all_text = " ".join([
        state.get("what", ""),
        state.get("notification", ""),
        state.get("action_taken", ""),
        state.get("next_steps", ""),
        state.get("previous_incidents", ""),
    ])

    system = f"""Today is {today}.

You are reviewing an incident report to make sure every person is properly identified.

CURRENT "who" ARRAY:
{json.dumps(state.get("who", []), indent=2)}

ALL INCIDENT TEXT:
{all_text}

CONVERSATION SO FAR:
{fmt_convo(conversation)}

Check every person — do they have a full name AND a title?
This includes the REPORTER — the person who submitted this report. If their name and title have not been collected, ask for them too.
- Full name = first + last, or accepted placeholder like "Male Subject 1"
- Title = their role - who they are in relation to the story
- Empty string for title = MISSING
- Never guess a title — only use what was explicitly stated

CRITICAL — NO REPEATED QUESTIONS: Read the full conversation above before asking anything. If a name or title has already been asked about and answered — even partially — do NOT ask again. Accept what was given.

NAME BEFORE PLACEHOLDER: If anyone is currently listed as "Male Subject 1", "Female Subject 1", or similar, ask if the reporter knows their actual name BEFORE accepting the placeholder. Only keep the placeholder if the reporter confirms they don't know.

OBVIOUS TITLES: Do not ask for the title/role of someone whose role is already self-evident from how they were described — "police", "police officer", "resident", "nurse", "security guard", "security officer" are their own titles. Only ask if the role is genuinely unclear.

If anyone is still incomplete after the above rules, ask ONE specific question about ONE person only.
CRITICAL: If the user said "I don't know", "I don't have that", or any similar response about a person's name or title, accept it and move on.

Return ONLY valid JSON — no markdown, no explanation:
- If more info needed: {{"done": false, "question": "your question"}}
- If complete: {{"done": true}}"""

    raw = call_claude(system, [{"role": "user", "content": "Check if all people are identified."}], max_tokens=300)
    return parse_json(raw)


def phase4_update_who(state, conversation):
    today = datetime.now().strftime("%A, %B %d, %Y")

    all_text = " ".join([
        state.get("what", ""),
        state.get("notification", ""),
        state.get("action_taken", ""),
        state.get("next_steps", ""),
    ])

    system = f"""Today is {today}.

Based on this conversation, compile the complete list of people involved in or related to the incident.

CURRENT "who" ARRAY:
{json.dumps(state.get("who", []), indent=2)}

INCIDENT TEXT:
{all_text}

CONVERSATION:
{fmt_convo(conversation)}

Return ONLY a JSON array — no markdown, no explanation:
[{{"name": "Full Name", "title": "their role/title"}}]

RULES:
- Include everyone mentioned.
- If name is unknown: use "Male Subject 1", "Female Subject 1", etc.
- Do not duplicate people.
- Never guess a title — leave as "" if not explicitly stated."""

    raw = call_claude(system, [{"role": "user", "content": "Compile who array."}], max_tokens=600)
    clean = raw.strip()
    if "```" in clean:
        parts = clean.split("```")
        for part in parts:
            if part.startswith("json"):
                clean = part[4:].strip()
                break
            elif part.strip().startswith("["):
                clean = part.strip()
                break
    start = clean.find("[")
    end = clean.rfind("]") + 1
    if start != -1 and end > start:
        clean = clean[start:end]
    return json.loads(clean)


# ════════════════════════════════════════════════════════════════
# PHASE 5 — Finalize and generate report
# ════════════════════════════════════════════════════════════════
def finalize(state):
    today = datetime.now().strftime("%A, %B %d, %Y")
    system = f"""Today is {today}.

You are a professional incident report writer for Opus Operations (security/concierge company).

Generate a complete incident report from this data:
{json.dumps(state, indent=2)}

WRITING STYLE — mandatory for ALL narrative fields:
- Write in formal, professional, third-person past tense.
- Use complete sentences and proper paragraph structure. No bullet points. No fragments.
- Use full names and titles for every person on first reference (e.g. "Security Officer Jane Smith"). Use last name only on subsequent references.
- Use "approximately" before any time that was not confirmed to the minute.
- Name both sender and recipient explicitly for every communication (phone call, text, email, voice note, radio) — never use ambiguous pronouns.
- Include every specific detail from the state: exact names, titles, locations, times, sequence of events, communications, actions taken, and outcomes. Do not compress, summarize, or omit anything.
- Do not repeat the same fact more than once.
- Do not invent or infer any detail not present in the state data.

Return ONLY valid JSON — no markdown, no explanation:
{{
  "status": "complete",
  "Describe_What_Happened": "Full factual chronological narrative written in formal third-person past tense. Include every detail — the sequence of events, who did or said what, all communications (method, sender, recipient, content), all actions taken at the scene, and any relevant background. This field must be complete enough that a reader who was not present can fully understand exactly what occurred.",
  "Who_Was_Notified": "Write in complete sentences. Identify each person notified by full name and title, the method of notification (phone, radio, in person, etc.), and the approximate time they were notified. If nobody was notified, state that explicitly.",
  "How_Was_It_Resolved": "Write in complete sentences. Describe every action taken to address or contain the incident, in chronological order, with the names and titles of those who took each action.",
  "Next_Steps": "Write in complete sentences. List all follow-up actions still required, who is responsible, and any expected timelines. If no further action is planned, state: 'No further action is planned at this time.'",
  "Incident_Type": "Choose the single best match from these exact options: 'Trespassing / Unathorized access' | 'Disturbance' | 'Missing / Stolen Package' | 'Resident Issue' | 'Criminal Activity' | 'Violence and Altercations' | 'Emergencies'. Use the examples as guidance: Trespassing=unauthorized visitors/domestic partners barred from property; Disturbance=narcotics, loud music, disorderly conduct, neighbor complaints; Missing/Stolen Package=missing/stolen/unaccounted packages; Resident Issue=complaints, lockouts, missing child, front desk issues; Criminal Activity=theft, vandalism, drug distribution, trespassing, weapons, shooting; Violence and Altercations=domestic violence, fights, threats, assault, harassment, stalking; Emergencies=fire, smoke, medical, elevator entrapment, gas leak, flooding, structural damage, power outage.",
  "Report_Type": "Based on the Incident_Type chosen above, set this to exactly 'Jobsite Incident Report' if Incident_Type is Trespassing/Unathorized access, Disturbance, Missing/Stolen Package, or Resident Issue. Set to exactly 'Emergency Incident Report' if Incident_Type is Criminal Activity, Violence and Altercations, or Emergencies.",
  "Unit_Number_Or_Location": "Specific unit, floor, or area",
  "Date_Time_Of_Incident": "YYYY-MM-DDTHH:MM:SS",
  "Previous_Undocumented_Incidents": "Write in complete sentences. Summarize any undocumented prior incidents, including dates, parties involved, and nature of the prior incident. Leave blank if none."
}}"""

    raw = call_claude(system, [{"role": "user", "content": "Generate the report."}], max_tokens=3000)
    return parse_json(raw)


# ════════════════════════════════════════════════════════════════
# EMPLOYEE OCCURRENCE REPORT — Phase functions
# ════════════════════════════════════════════════════════════════

def emp_phase2_check(state, conversation):
    today = datetime.now().strftime("%A, %B %d, %Y")
    system = f"""Today is {today}.

You are helping document an Employee Occurrence Report for Opus Operations (security/concierge company).

CURRENT STATE:
{json.dumps(state, indent=2)}

CONVERSATION SO FAR:
{fmt_convo(conversation)}

Decide if we have enough detail to write a complete occurrence report.

We need ALL of these things:
1. ACTION TAKEN — what disciplinary action was taken (e.g. verbal warning, written warning, counseling session, suspension, termination, record of discussion). Ask if not stated.
2. REASON FOR ACTION — what specifically the employee did that warranted this action. Must be specific, not vague.
3. STANDARDS EXPECTED — TERMINATION EXCEPTION: First, scan both the CURRENT STATE and the full CONVERSATION above for any mention of "termination" or "terminated". If termination is mentioned anywhere — in the state OR in any user message — SKIP this field entirely. Do not ask about future expectations or standards for a terminated employee. Only ask about standards/expectations if the action is NOT a termination.
4. EMPLOYEE'S REACTION — how the employee responded. Ask if not stated. Accept "no reaction" or "not applicable" if there was no direct interaction.

VIOLATION CATEGORY — identify from the story if obvious. If unclear, ask. Options:
   Time and Attendance | Job Performance | Professional Conduct | Harassment and Misconduct | Medical and Injury | Fraternization | Appearance

CRITICAL — NO REPEATED QUESTIONS: If already answered (even partially or with "I don't know"), accept and move on.
Ask ONE question at a time.

Do NOT ask about date/time — handled separately.
Do NOT ask for full names — handled separately.
CRITICAL — NO REPEATED QUESTIONS: If already answered (even partially or with "I don't know"), accept and move on.

If anything is missing, ask ONE focused question.

Return ONLY valid JSON — no markdown, no explanation:
- If more info needed: {{"done": false, "question": "your question here"}}
- If complete: {{"done": true}}"""

    raw = call_claude(system, [{"role": "user", "content": "Check if the occurrence narrative is complete."}], max_tokens=300)
    return parse_json(raw)


def emp_phase2_update_state(state, conversation):
    today = datetime.now().strftime("%A, %B %d, %Y")
    system = f"""Today is {today}.

You are extracting data for an Employee Occurrence Report for Opus Operations.

CONVERSATION:
{fmt_convo(conversation)}

CURRENT STATE (merge/update this):
{json.dumps(state, indent=2)}

Return ONLY valid JSON — no markdown, no explanation:
{{
  "when": "",
  "who": [],
  "action_taken": "",
  "reason_for_action": "",
  "standards_expected": "",
  "employee_reaction": "",
  "violation_category": ""
}}

RULES:
- "action_taken": what disciplinary action was taken (e.g. "Written Warning", "Verbal Warning", "Suspension", "Termination", etc.)
- "reason_for_action": full description of what the employee did — do not drop details
- "standards_expected": what was communicated to the employee and what expectations or standards were set going forward
- "employee_reaction": how the employee responded. Leave blank if no direct interaction.
- "violation_category": "Time and Attendance" | "Job Performance" | "Professional Conduct" | "Harassment and Misconduct" | "Medical and Injury" | "Fraternization" | "Appearance"
- "who": [{{"name": "...", "title": "..."}}] — include the employee being documented and the supervisor filing the report"""

    raw = call_claude(system, [{"role": "user", "content": "Extract state from conversation."}], max_tokens=900)
    return parse_json(raw)


def emp_phase4_check(state, conversation):
    today = datetime.now().strftime("%A, %B %d, %Y")
    system = f"""Today is {today}.

You are reviewing an Employee Occurrence Report to confirm the key people are identified.

CURRENT "who" ARRAY:
{json.dumps(state.get("who", []), indent=2)}

OCCURRENCE TEXT:
{state.get("reason_for_action", "") or state.get("what", "")}

CONVERSATION SO FAR:
{fmt_convo(conversation)}

You need:
1. The EMPLOYEE being documented — FULL name (first AND last) + their job title/role
2. The SUPERVISOR filing this report — FULL name (first AND last) + their title
3. The PERSON SUBMITTING — if not already captured, ask for their name and title

FULL NAME RULE: A single name like "Jason" or "Maria" is NOT complete — always ask for the last name unless the user explicitly says they don't know it.

CRITICAL — NO REPEATED QUESTIONS: If already asked and answered (even partially), accept and move on.
OBVIOUS TITLES: Don't ask for titles that are self-evident (security guard, security officer, supervisor).
If the user said "I don't know the last name", accept it and move on.

If anyone is still missing name or title, ask ONE specific question.

Return ONLY valid JSON:
- If more info needed: {{"done": false, "question": "your question"}}
- If complete: {{"done": true}}"""

    raw = call_claude(system, [{"role": "user", "content": "Check if people are identified."}], max_tokens=300)
    return parse_json(raw)


def emp_finalize(state):
    today = datetime.now().strftime("%A, %B %d, %Y")
    system = f"""Today is {today}.

You are a professional occurrence report writer for Opus Operations (security/concierge company).

Generate a complete Employee Occurrence Report from this data:
{json.dumps(state, indent=2)}

From the "who" array, identify:
- The EMPLOYEE being documented (the person whose behavior is being reported) — include their name AND job title
- The SUPERVISOR or manager filing the report

WRITING STYLE — mandatory for ALL narrative fields:
- Write in formal, professional, third-person past tense.
- Use complete sentences and proper paragraph structure. No bullet points. No fragments.
- Use full name and title for every person on first reference. Last name only on subsequent references.
- Include every specific detail from the state — exact names, titles, dates, times, locations, what was said, what was done, and the employee's response. Do not compress, summarize, or omit anything.
- Do not repeat the same fact more than once.
- Do not invent or infer any detail not present in the state data.

Return ONLY valid JSON — no markdown, no explanation:
{{
  "status": "complete",
  "report_type": "employee_occurrence",
  "Employee_Name": "Full name of the employee being documented",
  "Employee_Title": "The employee's job title or role (e.g. Security Guard, Concierge, etc.)",
  "Supervisor_Name": "Full name of the supervisor filing the report",
  "Incident_Type": "Violation category — one of: Time and Attendance | Job Performance | Professional Conduct | Harassment and Misconduct | Medical and Injury | Fraternization | Appearance",
  "Reason_for_Action": "Full factual narrative, written in formal third-person past tense, explaining exactly what the employee did, when it occurred, where it occurred, and why this action was warranted. Include every specific detail — dates, times, locations, prior occurrences, communications, and the impact of the employee's behavior. This field must be thorough enough to stand alone as a formal record.",
  "Conversation_Summary_and_Expec": "Write in complete sentences. If the action taken is Termination: describe the termination conversation — what was communicated to the employee about the reason for termination, how and when they were notified, and any relevant details of that conversation. Do NOT write about future expectations or standards — the employee has been terminated. If the action is anything other than Termination: describe the full conversation with the employee, including what was communicated about the violation, what specific standards and expectations were set going forward, and any commitments or acknowledgments made.",
  "Employee_Reaction": "Write in complete sentences. Describe exactly how the employee responded — verbally, emotionally, or in writing. If there was no direct interaction, state that explicitly.",
  "Action_Taken_Suggested": "The action_taken value from state — used to pre-check the correct checkbox. E.g. 'Written Warning', 'Verbal Warning', 'Suspension', 'Termination', 'Counseling Session', 'Record of Discussion'.",
  "Date_Time_Of_Occurrence": "YYYY-MM-DDTHH:MM:SS"
}}"""

    raw = call_claude(system, [{"role": "user", "content": "Generate the occurrence report."}], max_tokens=3000)
    return parse_json(raw)


def review_and_patch(result, conversation):
    """Post-generation pass: compare user turns to narrative fields and fill any gaps."""
    is_employee = result.get("report_type") == "employee_occurrence"
    if is_employee:
        fields = {
            "Reason_for_Action":              result.get("Reason_for_Action", ""),
            "Conversation_Summary_and_Expec": result.get("Conversation_Summary_and_Expec", ""),
            "Employee_Reaction":              result.get("Employee_Reaction", ""),
        }
    else:
        fields = {
            "Describe_What_Happened":           result.get("Describe_What_Happened", ""),
            "Who_Was_Notified":                 result.get("Who_Was_Notified", ""),
            "How_Was_It_Resolved":              result.get("How_Was_It_Resolved", ""),
            "Next_Steps":                       result.get("Next_Steps", ""),
            "Previous_Undocumented_Incidents":  result.get("Previous_Undocumented_Incidents", ""),
        }

    user_turns = [m["content"] for m in conversation if m["role"] == "user"]
    user_text  = "\n\n".join(user_turns)
    field_block = "\n".join(f'  "{k}": "{v}"' for k, v in fields.items())

    system = f"""You are reviewing a completed incident report to ensure no detail mentioned by the user was dropped.

USER'S ORIGINAL MESSAGES:
{user_text}

CURRENT REPORT FIELDS:
{{{field_block}}}

Instructions:
- If any fact from the user's messages is missing from the report fields, add it in the correct field.
- Do NOT invent or add details not mentioned by the user.
- Do NOT remove or shorten existing content.
- All narrative content must be written in formal, professional, third-person past tense, using complete sentences. No bullet points or fragments.
- Return ONLY valid JSON with the same keys as the CURRENT REPORT FIELDS (only the fields listed above).
  Example: {{"Reason_for_Action": "...", "Conversation_Summary_and_Expec": "...", "Employee_Reaction": "..."}}
- If nothing needs to change, return the fields unchanged."""

    try:
        raw    = call_claude(system, [{"role": "user", "content": "Review and patch the report."}], max_tokens=2048)
        patched = parse_json(raw)
        if isinstance(patched, dict):
            for key in fields:
                if key in patched and patched[key] and patched[key] != fields[key]:
                    result[key] = patched[key]
    except Exception:
        pass
    return result


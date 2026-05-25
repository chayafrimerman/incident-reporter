import json
import requests
from datetime import datetime
from credentials import ANTHROPIC_API_KEY





# ── Empty state ──────────────────────────────────────────────────
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


# ── Claude call ──────────────────────────────────────────────────
def call_claude(system_prompt, messages, max_tokens=1024):
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
    )
    if response.status_code != 200:
        raise Exception(f"Claude API error: {response.text}")
    return response.json()["content"][0]["text"]


def parse_json(raw):
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

You are an incident data extractor for Opus Operations (security/concierge company).

The user just described an incident. Extract every piece of information you can find.

Return ONLY valid JSON with this exact structure — no markdown, no explanation:
{{
  "when": "",
  "who": [],
  "where": "",
  "what": "",
  "notification": "",
  "action_taken": "",
  "next_steps": "",
  "multi_incident": ""
}}

FIELD RULES:
- "when": Date and/or time if mentioned. Format: "May 14, 2026 at 7:00 AM". Leave blank if not mentioned.
- "who": Array of people: [{{"name": "John Smith", "title": "Security Guard"}}].
  If name unknown use "Male Subject 1", "Female Subject 1", etc.
- "where": Exact location if mentioned.
- "what": Full story of what happened - do not drop details.
- "notification": Who was notified, if mentioned.
- "action_taken": Actions already taken, if mentioned.
- "next_steps": Future actions, if mentioned.
- "multi_incident": ONLY populate this if the user explicitly describes events on MORE THAN ONE separate date or occasion. If it is a single incident, leave this blank. Do not infer or guess.

Never invent information. Only extract what was actually stated.
Don't drop details."""

    raw = call_claude(system, [{"role": "user", "content": story}], max_tokens=800)
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
1. WHERE — must include a street address OR property name (either one is sufficient — do not ask for both), ask about the specific area only if not mentioned and it is something that actually happened in a specific area - not something that happened over the phone. If the user gave a street address like "22 Main Street", that is complete — do not ask for a property name on top of it.
2. WHAT happened (clear factual account)
3. WHO was notified
4. What ACTION was already taken
5. What NEXT STEPS are planned (or explicitly none)
 
IMPORTANT: Do NOT ask about previous incidents or prior reporting unless the user's own story explicitly mentioned events on more than one separate date. If their story is a single incident, never bring up prior incidents at all.
Do NOT ask about date or time under any circumstances — that is handled in a separate phase.
Don't drop details. 

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

If anyone is incomplete, ask ONE specific question about ONE person only.
CRITICAL: If the user said "I don't know", "I don't have that", or any similar response about a person's title, accept it and move on.

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

You are an incident report writer for Opus Operations (security/concierge company).

Generate a complete incident report from this data:
{json.dumps(state, indent=2)}

Return ONLY valid JSON — no markdown, no explanation:
{{
  "status": "complete",
  "Describe_What_Happened": "Full factual chronological narrative. Use full names and titles for all people. Use 'approximately' for unverified times. Facts only. CRITICAL: Include every single detail from the state data — do not summarize, compress, or drop anything. Do not invent or infer details not in the state. Preserve the exact method of communication (email, phone call, in person, etc.). The narrative should be as detailed and complete as the source data allows.",
  "Who_Was_Notified": "Full names and roles of everyone notified",
  "How_Was_It_Resolved": "Actions already taken",
  "Next_Steps": "Actions still needed, or 'No further action planned'",
  "Incident_Type": "One of: Trespassing/Unauthorized Access | Disturbance | Missing/Stolen Package | Resident Issue | Criminal Activity | Violence/Altercation | Emergency | Time and Attendance | Job Performance | Professional Conduct | Harassment/Misconduct | Medical/Injury | Fraternization | Appearance",
  "Unit_Number_Or_Location": "Specific unit, floor, or area",
  "Date_Time_Of_Incident": "YYYY-MM-DDTHH:MM:SS",
  "Previous_Undocumented_Incidents": "Summary of any undocumented prior incidents. Leave blank if none."
}}"""

    raw = call_claude(system, [{"role": "user", "content": "Generate the report."}], max_tokens=2048)
    return parse_json(raw)
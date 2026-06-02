"""
Run this once to discover the employee_occurance_test form key and field structure.

    python get_employee_form.py

Copy the form key printed below into credentials.py as EMPLOYEE_FORM_KEY.
Then update the section/field names in the submit_employee_occurrence() function in app.py
to match what this script prints.
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))

import requests
from credentials import get_cached_token, PROJECT_KEY, DOFORMS_BASE

token = get_cached_token()
headers = {"Authorization": f"Bearer {token}"}

# ── 1. List all forms in the project ────────────────────────────
print("=" * 60)
print("ALL FORMS IN PROJECT")
print("=" * 60)
r = requests.get(f"{DOFORMS_BASE}/api/v2/projects/{PROJECT_KEY}/forms", headers=headers)
print(f"Status: {r.status_code}")
if r.status_code == 200:
    forms = r.json()
    if isinstance(forms, list):
        for f in forms:
            print(f"  name={f.get('name',''):<45}  key={f.get('key','')}")
    else:
        print(json.dumps(forms, indent=2)[:500])
else:
    print(r.text[:500])

# ── 2. Try fetching the employee occurrence form directly ────────
candidate_names = [
    "employee_occurance_test",
    "employee_occurrence_test",
    "Employee_Occurance_Test",
    "Employee_Occurrence_Test",
    "employee_occurance",
    "Employee_Occurance_Report",
]

found_key = None
found_form = None

for name in candidate_names:
    r2 = requests.get(f"{DOFORMS_BASE}/api/v2/forms/{name}", headers=headers)
    if r2.status_code == 200 and r2.text.strip():
        try:
            data = r2.json()
            if data.get("key"):
                found_key = data["key"]
                found_form = data
                print(f"\n✓ Found form: {name}  key={found_key}")
                break
        except Exception:
            pass

if not found_key:
    print("\n⚠  Could not auto-find the form by name.")
    print("   Check the list above and paste the correct key into credentials.py manually.")
    sys.exit(1)

# ── 3. Print full field structure ────────────────────────────────
print("\n" + "=" * 60)
print("FIELD STRUCTURE (paste section/field names into app.py)")
print("=" * 60)

def print_fields(fields, indent=0):
    for f in fields:
        label = (f.get("options") or {}).get("label") or {}
        label_str = label.get("en") or ""
        print(" " * indent + f"name={f['name']:<35} type={f.get('data',''):<12} label={label_str}")
        if f.get("fields"):
            print_fields(f["fields"], indent + 4)

print_fields(found_form.get("fields", []))

print(f"\n{'='*60}")
print(f"EMPLOYEE_FORM_KEY = \"{found_key}\"")
print(f"{'='*60}")
print("Add the line above to credentials.py")

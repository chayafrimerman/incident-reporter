"""
Run this to see the exact option values for the Action_Taken strings field.
    python check_action_taken_options.py
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))
import requests
from credentials import get_cached_token, DOFORMS_BASE, EMPLOYEE_FORM_KEY

token = get_cached_token()
headers = {"Authorization": f"Bearer {token}"}

r = requests.get(f"{DOFORMS_BASE}/api/v2/forms/Employee_Occurance_Test", headers=headers)
print(f"Status: {r.status_code}")

data = r.json()

def find_field(fields, name):
    for f in fields:
        if f["name"] == name:
            return f
        if f.get("fields"):
            result = find_field(f["fields"], name)
            if result:
                return result
    return None

field = find_field(data.get("fields", []), "Action_Taken")
if field:
    print("\nAction_Taken field (full definition):")
    print(json.dumps(field, indent=2))
else:
    print("Action_Taken field not found")

"""
Check field structure of Employee_Occurance_Test4.
Run: python check_test4_fields.py
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))
import requests
from credentials import get_cached_token, DOFORMS_BASE
from utils import EMPLOYEE_FORM_KEY

token = get_cached_token()
headers = {"Authorization": f"Bearer {token}"}

# Use the current EMPLOYEE_FORM_KEY
r = requests.get(f"{DOFORMS_BASE}/api/v2/forms/Employee_Occurance_Test4", headers=headers)
print(f"Status: {r.status_code}")

def print_fields(fields, indent=0):
    for f in fields:
        label = (f.get("options") or {}).get("label") or {}
        print(" " * indent + f"name={f['name']:<35} type={f.get('data',''):<12} label={label.get('en','')}")
        if f.get("fields"):
            print_fields(f["fields"], indent + 4)

print_fields(r.json().get("fields", []))

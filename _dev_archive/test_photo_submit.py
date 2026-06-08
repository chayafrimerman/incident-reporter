"""
Test submitting a photo to the Employee_Occurance_Test form.
Run: python test_photo_submit.py
"""
import sys, os, json, base64, requests
sys.path.insert(0, os.path.dirname(__file__))
from credentials import get_cached_token, DOFORMS_BASE
from utils import EMPLOYEE_FORM_KEY,  PROJECT_KEY

token   = get_cached_token()
headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

# Create a tiny 1x1 red PNG (valid image, minimal size)
TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
    "z8BQDwADhQGAWjR9awAAAABJRU5ErkJggg=="
)

print("=" * 60)
print("Approach 1: blob as nested object with base64 data field")
print("=" * 60)
payload1 = {
    "formKey": EMPLOYEE_FORM_KEY,
    "projectKey": PROJECT_KEY,
    "fields": [
        {"name": "untitled2", "fields": [
            {"name": "Employee_Name", "text": "Test Employee"},
            {"name": "Name_of_Supervisor", "text": "Test Supervisor"},
        ]},
        {"name": "Reason_for_Action", "text": "Photo submission test"},
        {"name": "Attachments", "data": "blob", "type": "image",
         "blob": {"data": TINY_PNG_B64, "type": "png", "fileName": "test.png"}},
    ]
}
r = requests.post(f"{DOFORMS_BASE}/api/v2/submissions", headers=headers, json=payload1)
resp1 = r.json()
print(f"Status: {r.status_code}")
print("Attachments in response:", "Attachments" in json.dumps(resp1))
print("Full response fields:", [f.get("name") for f in resp1.get("submission", {}).get("fields", [])])

print()
print("=" * 60)
print("Approach 2: blob as flat base64 string")
print("=" * 60)
payload2 = {
    "formKey": EMPLOYEE_FORM_KEY,
    "projectKey": PROJECT_KEY,
    "fields": [
        {"name": "untitled2", "fields": [
            {"name": "Employee_Name", "text": "Test Employee"},
            {"name": "Name_of_Supervisor", "text": "Test Supervisor"},
        ]},
        {"name": "Reason_for_Action", "text": "Photo submission test 2"},
        {"name": "Attachments", "blob": TINY_PNG_B64},
    ]
}
r = requests.post(f"{DOFORMS_BASE}/api/v2/submissions", headers=headers, json=payload2)
resp2 = r.json()
print(f"Status: {r.status_code}")
print("Attachments in response:", "Attachments" in json.dumps(resp2))
print("Full response fields:", [f.get("name") for f in resp2.get("submission", {}).get("fields", [])])

print()
print("=" * 60)
print("Approach 3: text field with base64 data URI")
print("=" * 60)
payload3 = {
    "formKey": EMPLOYEE_FORM_KEY,
    "projectKey": PROJECT_KEY,
    "fields": [
        {"name": "untitled2", "fields": [
            {"name": "Employee_Name", "text": "Test Employee"},
            {"name": "Name_of_Supervisor", "text": "Test Supervisor"},
        ]},
        {"name": "Reason_for_Action", "text": "Photo submission test 3"},
        {"name": "Attachments", "text": f"data:image/png;base64,{TINY_PNG_B64}"},
    ]
}
r = requests.post(f"{DOFORMS_BASE}/api/v2/submissions", headers=headers, json=payload3)
resp3 = r.json()
print(f"Status: {r.status_code}")
print("Attachments in response:", "Attachments" in json.dumps(resp3))
print("Full response fields:", [f.get("name") for f in resp3.get("submission", {}).get("fields", [])])

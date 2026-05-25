# Incident Report Assistant — Opus Operations

An AI-powered incident report tool for Opus Operations security and concierge staff. Staff describe what happened in a chat interface, and the AI guides them through collecting all required details before generating a structured report and submitting it to doForms.

## How It Works

1. Staff open the app and describe the incident in plain language
2. The AI asks follow-up questions to fill in any missing details (who, what, where, when, notifications, actions taken, next steps)
3. The form is auto-filled and reviewed by the staff member
4. Staff select the address, supervisor, and employee from dropdowns and submit to doForms

## Tech Stack

- **Backend**: Python, Flask
- **AI**: Anthropic Claude API (claude-sonnet-4)
- **Database**: SQL Server (via pyodbc) — employee and customer dropdowns
- **Form submission**: doForms API
- **Frontend**: Vanilla HTML/CSS/JavaScript

## Project Structure

```
incident-reporter/
├── app.py              # Flask server, API routes, session state machine
├── utils.py            # Claude API calls, phase logic, prompt engineering
├── credentials.py      # API keys and DB connection (not in repo)
├── static/
│   └── index.html      # Frontend UI
├── conversation_logs/  # Saved conversations for training (not in repo)
└── requirements.txt
```

## Setup

### 1. Clone the repo
```bash
git clone https://github.com/yourorg/incident-reporter.git
cd incident-reporter
```

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Create credentials.py
This file is not in the repo. Create it manually on the server:
```python
ANTHROPIC_API_KEY = "your_key"
DOFORMS_BASE      = "https://api.mydoforms.com"
FORM_KEY          = "your_form_key"
PROJECT_KEY       = "your_project_key"
DOFORMS_USERNAME  = "your_doforms_email"
DOFORMS_PASSWORD  = "your_doforms_password"

import pyodbc
def get_db_conn():
    return pyodbc.connect(
        "DRIVER={ODBC Driver 17 for SQL Server};"
        "SERVER=your_server_ip,1433;"
        "DATABASE=OPUS2;"
        "UID=your_uid;"
        "PWD=your_password;"
        "Encrypt=yes;"
        "TrustServerCertificate=yes;"
    )
```

### 4. Run locally
```bash
python app.py
```

Visit `http://localhost:5000`

## Deployment

Deployed on a Linux VM (Ubuntu 24.04) behind nginx with SSL.
See deployment notes for server setup details.

## Conversation Logs

Every completed report is saved to `conversation_logs/` as a JSON file containing:
- Full conversation history
- Final extracted state
- User feedback (if provided)

These logs are used for model fine-tuning and quality review. They are not committed to the repo.

## Notes

- The app uses a server-side session state machine with 5 phases: story extraction, narrative completion, date/time, people identification, and report generation
- Sessions are stored in memory and reset on server restart
- The doForms submit is currently in mock mode for testing — uncomment the real submit route in `app.py` when ready for production

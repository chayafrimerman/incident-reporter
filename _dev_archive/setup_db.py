"""
Run once to create the report tables in SQL Server.
Usage:  python setup_db.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from credentials import get_db_conn

CREATE_INCIDENT = """
IF NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.TABLES
    WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = 'incident_reports'
)
CREATE TABLE dbo.incident_reports (
    id                  INT            IDENTITY(1,1) PRIMARY KEY,
    submitted_at        DATETIME       DEFAULT GETDATE(),
    session_id          NVARCHAR(100),
    employee_name       NVARCHAR(200),
    supervisor_name     NVARCHAR(200),
    job_address         NVARCHAR(500),
    customer_name       NVARCHAR(200),
    report_type         NVARCHAR(100),
    incident_type       NVARCHAR(200),
    unit_location       NVARCHAR(300),
    date_time_incident  DATETIME,
    date_time_report    DATETIME,
    what_happened       NVARCHAR(MAX),
    who_notified        NVARCHAR(MAX),
    how_resolved        NVARCHAR(MAX),
    follow_up_actions   NVARCHAR(MAX),
    additional_info     NVARCHAR(MAX),
    previous_incidents  NVARCHAR(MAX)
);
"""

CREATE_EMPLOYEE = """
IF NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.TABLES
    WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = 'employee_occurrence_reports'
)
CREATE TABLE dbo.employee_occurrence_reports (
    id                   INT            IDENTITY(1,1) PRIMARY KEY,
    submitted_at         DATETIME       DEFAULT GETDATE(),
    session_id           NVARCHAR(100),
    employee_name        NVARCHAR(200),
    employee_title       NVARCHAR(200),
    supervisor_name      NVARCHAR(200),
    incident_type        NVARCHAR(200),
    date_time_incident   DATETIME,
    date_time_report     DATETIME,
    reason_for_action    NVARCHAR(MAX),
    action_taken         NVARCHAR(500),
    conversation_summary NVARCHAR(MAX),
    employee_reaction    NVARCHAR(MAX),
    photo_count          INT            DEFAULT 0
);
"""

def main():
    conn   = get_db_conn()
    cursor = conn.cursor()
    for name, sql in [("incident_reports", CREATE_INCIDENT),
                      ("employee_occurrence_reports", CREATE_EMPLOYEE)]:
        cursor.execute(sql)
        conn.commit()
        print(f"  {name}: OK")
    conn.close()
    print("Done — tables are ready.")

if __name__ == "__main__":
    main()

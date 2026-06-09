-- ============================================================
-- Termination Reports Table
-- Related to employee_occurrence_reports via session_id
-- ============================================================

CREATE TABLE dbo.termination_reports (
    id                  INT IDENTITY(1,1)   PRIMARY KEY,
    session_id          NVARCHAR(128)       NOT NULL,       -- FK to employee_occurrence_reports.session_id
    employee_name       NVARCHAR(255)       NOT NULL,
    supervisor_name     NVARCHAR(255)       NULL,
    termination_date    DATETIME            NULL,
    reason_for_action   NVARCHAR(MAX)       NULL,
    work_division       NVARCHAR(255)       NULL,
    uniform_return      NVARCHAR(10)        NULL,           -- 'Yes' or 'No'
    link                NVARCHAR(1024)      NULL,           -- URL of uploaded termination PDF
    created_at          DATETIME            DEFAULT GETDATE()
);

-- Optional: index on session_id for fast lookups
CREATE INDEX IX_termination_reports_session_id
    ON dbo.termination_reports (session_id);

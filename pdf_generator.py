"""
PDF report generator for Opus Operations Incident Reporter.

Matches the doForms Termination Form layout:
  - White header bar with report title (teal text)
  - Teal stripe  (#00D2CB)
  - White logo area with the Opus Operations logo
  - Alternating teal label rows / white value rows (16 pt)
  - 0.5 pt black cell borders
  - Margins x=72/540, column split x=306

Public API:
    generate_incident_pdf(data: dict) -> bytes
    generate_employee_occurrence_pdf(data: dict) -> bytes
"""
from io import BytesIO
from datetime import datetime
import base64 as _b64

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.lib.colors import HexColor, white, black
from reportlab.lib.utils import ImageReader

# ── Brand colours ─────────────────────────────────────────────────
TEAL   = HexColor("#00D2CB")   # label rows + header stripes
BLACK  = black
WHITE  = white

# ── Page geometry (matching doForms exactly) ──────────────────────
PW, PH = letter        # 612 × 792 pts
ML  = 72               # left margin
MR  = 540              # right margin
CW  = MR - ML          # 468 pts total width
MID = 306              # two-column split
LW  = 0.5              # border line width

# ── Row heights ───────────────────────────────────────────────────
LBL_H   = 16           # label row (fixed)
MIN_V_H = 16           # minimum value row height
LINE_H  = 12           # wrapped-text line height
PAD_X   = 4            # horizontal text padding inside cells
LBL_FS  = 8            # label font size
VAL_FS  = 9.5          # value font size

# ── Header zone heights ───────────────────────────────────────────
HDR_H    = 48          # white bar with report title
STRIPE_H = 14          # teal stripe below title bar
LOGO_H   = 90          # white area containing the Opus logo

_LOGO_B64 = (
    "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"
)


def _logo_reader():
    """Return an ImageReader for the embedded Opus Operations logo."""
    import os, pathlib
    logo_path = pathlib.Path(__file__).parent / "extracted_logo_1.png"
    if logo_path.exists():
        return ImageReader(str(logo_path))
    raw = _b64.b64decode(_LOGO_B64)
    return ImageReader(BytesIO(raw))


# ── Helpers ───────────────────────────────────────────────────────

def _fmt_dt(v: str) -> str:
    if not v:
        return ""
    v = v.strip().replace("Z", "").split(".")[0].replace(" ", "T")
    if len(v) == 16:
        v += ":00"
    try:
        return datetime.fromisoformat(v).strftime("%m/%d/%Y %I:%M %p")
    except Exception:
        return v


def _wrap(text: str, max_w: float) -> list:
    """Word-wrap text to fit within max_w pts."""
    cpp = max(1, int(max_w / (VAL_FS * 0.55)))
    out = []
    for para in str(text or "").split("\n"):
        words = para.split()
        if not words:
            out.append("")
            continue
        cur = ""
        for w in words:
            test = (cur + " " + w).strip()
            if len(test) <= cpp:
                cur = test
            else:
                if cur:
                    out.append(cur)
                cur = w
        if cur:
            out.append(cur)
    return out or [""]


# ── Canvas wrapper ────────────────────────────────────────────────

class _Form:
    def __init__(self):
        self.buf = BytesIO()
        self.c   = rl_canvas.Canvas(self.buf, pagesize=letter)
        self.y   = PH

    def _need(self, h: float):
        """Open a new page if h pts won't fit below current y."""
        if self.y - h < 55:
            self.c.showPage()
            self.y = PH - 36

    def header(self, report_title: str):
        """
        Draw the three-zone page header:
          1. White title bar with teal report title text
          2. Teal stripe
          3. White logo area with the Opus Operations logo
        """
        top = PH - 36   # top of header zone (leave 36-pt top margin)

        # Zone 1 — white title bar
        hdr_y = top - HDR_H
        self.c.setFillColor(WHITE)
        self.c.rect(ML, hdr_y, CW, HDR_H, fill=1, stroke=0)
        self.c.setFillColor(TEAL)
        self.c.setFont("Helvetica-Bold", 16)
        self.c.drawCentredString(ML + CW / 2, hdr_y + (HDR_H - 16) / 2, report_title)

        # Zone 2 — teal stripe
        stripe_y = hdr_y - STRIPE_H
        self.c.setFillColor(TEAL)
        self.c.rect(ML, stripe_y, CW, STRIPE_H, fill=1, stroke=0)

        # Zone 3 — white logo area
        logo_y = stripe_y - LOGO_H
        self.c.setFillColor(WHITE)
        self.c.rect(ML, logo_y, CW, LOGO_H, fill=1, stroke=0)
        try:
            ir = _logo_reader()
            iw, ih = ir.getSize()
            scale = min((CW * 0.8) / iw, (LOGO_H * 0.8) / ih, 1.0)
            dw, dh = iw * scale, ih * scale
            lx = ML + (CW - dw) / 2
            ly = logo_y + (LOGO_H - dh) / 2
            self.c.drawImage(ir, lx, ly, dw, dh, mask="auto")
        except Exception:
            pass

        # outer border around entire header zone
        self.c.setStrokeColor(BLACK)
        self.c.setLineWidth(LW)
        self.c.rect(ML, logo_y, CW, HDR_H + STRIPE_H + LOGO_H, fill=0, stroke=1)

        self.y = logo_y - 2   # form rows begin here

    def row2(self, lbl1: str, val1: str, lbl2: str, val2: str):
        """Two-column field: left half and right half."""
        col_w  = (MID - ML) - PAD_X * 2
        lines1 = _wrap(val1, col_w)
        lines2 = _wrap(val2, col_w)
        n      = max(len(lines1), len(lines2))
        val_h  = max(MIN_V_H, n * LINE_H + 4)

        self._need(LBL_H + val_h)
        y = self.y

        # teal label row
        self.c.setFillColor(TEAL)
        self.c.rect(ML,  y - LBL_H, MID - ML, LBL_H, fill=1, stroke=0)
        self.c.rect(MID, y - LBL_H, MR - MID, LBL_H, fill=1, stroke=0)
        self.c.setStrokeColor(BLACK)
        self.c.setLineWidth(LW)
        self.c.rect(ML,  y - LBL_H, MID - ML, LBL_H, fill=0, stroke=1)
        self.c.rect(MID, y - LBL_H, MR - MID, LBL_H, fill=0, stroke=1)
        self.c.setFillColor(WHITE)
        self.c.setFont("Helvetica", LBL_FS)
        self.c.drawString(ML  + PAD_X, y - 11, lbl1)
        self.c.drawString(MID + PAD_X, y - 11, lbl2)
        y -= LBL_H

        # white value row
        self.c.setFillColor(WHITE)
        self.c.rect(ML,  y - val_h, MID - ML, val_h, fill=1, stroke=0)
        self.c.rect(MID, y - val_h, MR - MID, val_h, fill=1, stroke=0)
        self.c.setStrokeColor(BLACK)
        self.c.rect(ML,  y - val_h, MID - ML, val_h, fill=0, stroke=1)
        self.c.rect(MID, y - val_h, MR - MID, val_h, fill=0, stroke=1)
        self.c.setFillColor(BLACK)
        self.c.setFont("Helvetica", VAL_FS)
        ty = y - 11
        for line in lines1:
            self.c.drawString(ML + PAD_X, ty, line)
            ty -= LINE_H
        ty = y - 11
        for line in lines2:
            self.c.drawString(MID + PAD_X, ty, line)
            ty -= LINE_H

        self.y -= (LBL_H + val_h)

    def row1(self, label: str, value: str):
        """Full-width field."""
        lines = _wrap(value, CW - PAD_X * 2)
        val_h = max(MIN_V_H, len(lines) * LINE_H + 4)

        self._need(LBL_H + val_h)
        y = self.y

        # teal label row
        self.c.setFillColor(TEAL)
        self.c.rect(ML, y - LBL_H, CW, LBL_H, fill=1, stroke=0)
        self.c.setStrokeColor(BLACK)
        self.c.setLineWidth(LW)
        self.c.rect(ML, y - LBL_H, CW, LBL_H, fill=0, stroke=1)
        self.c.setFillColor(WHITE)
        self.c.setFont("Helvetica", LBL_FS)
        self.c.drawString(ML + PAD_X, y - 11, label)
        y -= LBL_H

        # white value row
        self.c.setFillColor(WHITE)
        self.c.rect(ML, y - val_h, CW, val_h, fill=1, stroke=0)
        self.c.setStrokeColor(BLACK)
        self.c.rect(ML, y - val_h, CW, val_h, fill=0, stroke=1)
        self.c.setFillColor(BLACK)
        self.c.setFont("Helvetica", VAL_FS)
        ty = y - 11
        for line in lines:
            self.c.drawString(ML + PAD_X, ty, line)
            ty -= LINE_H

        self.y -= (LBL_H + val_h)

    def embed_photos(self, photos: list):
        """Embed photos inline in the report flow (no separate pages)."""
        import base64 as _b
        MAX_IMG_H = PH - 120   # tallest an image can be on a single page

        for i, photo in enumerate(photos):
            fname = photo.get("filename", f"photo_{i + 1}")
            label = f"Photo {i + 1}:  {fname}"

            # Decode & measure image
            try:
                img_data = _b.b64decode(photo["data"])
                img_buf  = BytesIO(img_data)
                ir       = ImageReader(img_buf)
                iw, ih   = ir.getSize()
                scale    = min(CW / iw, MAX_IMG_H / ih, 1.0)
                dw, dh   = iw * scale, ih * scale
            except Exception as exc:
                # If decoding fails just show an error row
                self.row1(label, f"[Could not decode image: {exc}]")
                continue

            # Make sure label row + image fit; start new page if needed
            needed = LBL_H + dh + 4
            self._need(needed)
            y = self.y

            # Teal label row
            self.c.setFillColor(TEAL)
            self.c.rect(ML, y - LBL_H, CW, LBL_H, fill=1, stroke=0)
            self.c.setStrokeColor(BLACK)
            self.c.setLineWidth(LW)
            self.c.rect(ML, y - LBL_H, CW, LBL_H, fill=0, stroke=1)
            self.c.setFillColor(WHITE)
            self.c.setFont("Helvetica", LBL_FS)
            self.c.drawString(ML + PAD_X, y - 11, label)
            y -= LBL_H

            # White image area with border
            img_area_h = dh + 8
            self.c.setFillColor(WHITE)
            self.c.rect(ML, y - img_area_h, CW, img_area_h, fill=1, stroke=0)
            self.c.setStrokeColor(BLACK)
            self.c.rect(ML, y - img_area_h, CW, img_area_h, fill=0, stroke=1)

            # Draw image centred horizontally
            x = ML + (CW - dw) / 2
            img_buf.seek(0)
            self.c.drawImage(ImageReader(img_buf), x, y - img_area_h + 4, dw, dh)

            self.y -= (LBL_H + img_area_h)

    def footer(self):
        self.c.setStrokeColor(TEAL)
        self.c.setLineWidth(1)
        self.c.line(ML, 36, MR, 36)
        self.c.setFillColor(TEAL)
        self.c.setFont("Helvetica", 8)
        self.c.drawString(ML, 23, "Submitted via Opus Operations Incident Reporter")
        self.c.drawRightString(MR, 23, datetime.now().strftime("%m/%d/%Y %I:%M %p"))

    def get_bytes(self) -> bytes:
        self.c.save()
        self.buf.seek(0)
        return self.buf.read()


# ── Action taken labels ───────────────────────────────────────────

_ACTION_LABELS = {
    "Record_of_Discussion": "Record of Discussion",
    "Verbal_Warning":       "Verbal Warning",
    "Written_Warning":      "Written Warning",
    "Counseling_Session":   "Counseling Session",
    "Text":                 "Documentation / Text",
    "Suspension":           "Suspension",
    "Termination":          "Termination",
}


# ── Public API ────────────────────────────────────────────────────

def generate_incident_pdf(data: dict) -> bytes:
    f = _Form()
    f.header("Incident Report")

    _it = data.get("Incident_Type", "")
    if data.get("Report_Type"):
        rtype = data["Report_Type"]
    elif _it in {"Trespassing / Unathorized access", "Disturbance",
                 "Missing / Stolen Package", "Resident Issue"}:
        rtype = "Jobsite Incident Report"
    elif _it in {"Criminal Activity", "Violence and Altercations", "Emergencies"}:
        rtype = "Emergency Incident Report"
    else:
        rtype = ""

    f.row2("Employee name",           data.get("Employee_name", ""),
           "Date / time of report",   _fmt_dt(data.get("Date_Time_Of_Report", "")))
    f.row2("Supervisor",              data.get("Name_Of_Supervisor", ""),
           "Date / time of incident", _fmt_dt(data.get("Date_Time_Of_Incident", "")))
    f.row2("Job address",             data.get("Job_Address", ""),
           "Customer",                data.get("Customer_Name", ""))
    f.row2("Report type",             rtype,
           "Incident type",           _it)
    if data.get("Unit_Number_Or_Location"):
        f.row1("Unit / location", data["Unit_Number_Or_Location"])
    f.row1("What happened",       data.get("Describe_What_Happened", ""))
    f.row1("Who was notified",    data.get("Who_Was_Notified", ""))
    f.row1("How was it resolved", data.get("How_Was_It_Resolved", ""))
    fu_parts = [
        data.get("Follow_Up_Actions_Needed", ""),
        data.get("Additional_Information", ""),
        data.get("Previous_Undocumented_Incidents", ""),
    ]
    fu = "\n\n".join(p for p in fu_parts if str(p).strip())
    if fu:
        f.row1("Follow-up / additional information", fu)
    f.footer()
    return f.get_bytes()


def generate_termination_pdf(data: dict) -> bytes:
    """Generate a Termination Form PDF from employee occurrence data."""
    from io import BytesIO as _BIO
    buf = _BIO()
    c   = rl_canvas.Canvas(buf, pagesize=letter)
    W, H = letter

    # ── Logo ────────────────────────────────────────────────────────
    logo = _logo_reader()
    if logo:
        lw, lh = 160, 50
        c.drawImage(logo, (W - lw) / 2, H - PAD_X - lh, width=lw, height=lh, preserveAspectRatio=True, mask="auto")
    y = H - PAD_X - 60

    # ── Title ───────────────────────────────────────────────────────
    c.setFont("Helvetica-Bold", 14)
    c.setFillColor(TEAL)
    c.drawCentredString(W / 2, y, "TERMINATION FORM")
    y -= 24
    c.setFillColor(BLACK)

    # ── Helper to draw a labeled field row ──────────────────────────
    def _row(label, value, y_pos):
        c.setFont("Helvetica-Bold", VAL_FS)
        c.setFillColor(TEAL)
        c.drawString(PAD_X, y_pos, label + ":")
        c.setFont("Helvetica", VAL_FS)
        c.setFillColor(BLACK)
        label_w = c.stringWidth(label + ": ", "Helvetica-Bold", VAL_FS)
        c.drawString(PAD_X + label_w, y_pos, str(value or ""))
        return y_pos - LINE_H

    # ── Row 1: Employee Name | Termination Date ──────────────────────
    emp_name = data.get("Employee_name", data.get("Employee_Name", ""))
    term_date = _fmt_dt(data.get("Date_Time_Of_Incident", data.get("Date_Time_Of_Occurrence", "")))
    c.setFont("Helvetica-Bold", VAL_FS); c.setFillColor(TEAL)
    c.drawString(PAD_X, y, "Employee Name:")
    c.setFont("Helvetica", VAL_FS); c.setFillColor(BLACK)
    c.drawString(PAD_X + 100, y, emp_name)
    c.setFont("Helvetica-Bold", VAL_FS); c.setFillColor(TEAL)
    c.drawString(W / 2, y, "Termination Date:")
    c.setFont("Helvetica", VAL_FS); c.setFillColor(BLACK)
    c.drawString(W / 2 + 110, y, term_date)
    y -= LINE_H

    # ── Row 2: Reason | Work Division ────────────────────────────────
    reason = data.get("Reason_for_Action", "")
    division = data.get("Work_Division", "")
    c.setFont("Helvetica-Bold", VAL_FS); c.setFillColor(TEAL)
    c.drawString(PAD_X, y, "Reason for Termination:")
    c.setFont("Helvetica", VAL_FS); c.setFillColor(BLACK)
    wrapped = _wrap(reason, 45)
    for i, line in enumerate(wrapped):
        c.drawString(PAD_X + 158, y - i * 14, line)
    y -= max(len(wrapped), 1) * 14 + 4
    c.setFont("Helvetica-Bold", VAL_FS); c.setFillColor(TEAL)
    c.drawString(PAD_X, y, "Work Division:")
    c.setFont("Helvetica", VAL_FS); c.setFillColor(BLACK)
    c.drawString(PAD_X + 95, y, division)
    y -= LINE_H

    # ── Row 3: Manager | Supervisor ──────────────────────────────────
    supervisor = data.get("Name_Of_Supervisor", "")
    y = _row("Manager / Supervisor", supervisor, y)

    # ── Row 4: Uniform Return (mandatory) ────────────────────────────
    uniform = data.get("Uniform_Return", "")
    y -= 4
    c.setFont("Helvetica-Bold", VAL_FS); c.setFillColor(TEAL)
    c.drawString(PAD_X, y, "Does the employee have to return their uniform?")
    c.setFont("Helvetica", VAL_FS); c.setFillColor(BLACK)
    c.drawString(PAD_X + 316, y, str(uniform or "Not specified"))
    y -= LINE_H

    # ── Footer line ──────────────────────────────────────────────────
    c.setStrokeColor(TEAL)
    c.line(PAD_X, y, W - PAD_X, y)

    c.save()
    buf.seek(0)
    return buf.read()


def generate_employee_occurrence_pdf(data: dict) -> bytes:
    f = _Form()
    f.header("Employee Occurrence Report")

    action_taken = data.get("Action_Taken", [])
    if isinstance(action_taken, str):
        action_taken = [action_taken] if action_taken else []
    action_str = ", ".join(_ACTION_LABELS.get(a, a) for a in action_taken)

    f.row2("Employee name",             data.get("Employee_name", ""),
           "Employee title",            data.get("Employee_Title", ""))
    f.row2("Supervisor",                data.get("Name_Of_Supervisor", ""),
           "Incident type",             data.get("Incident_Type", ""))
    f.row2("Date / time of occurrence", _fmt_dt(data.get("Date_Time_Of_Incident", "")),
           "Date / time of report",     _fmt_dt(data.get("Date_Time_Of_Report", "")))
    f.row1("Action taken",              action_str)
    f.row1("Reason for action",         data.get("Reason_for_Action", ""))
    f.row1("Standards & expectations discussed",
           data.get("Conversation_Summary_and_Expec", ""))
    f.row1("Employee reaction",         data.get("Employee_Reaction", ""))
    photos = data.get("photos", [])
    if photos:
        f.embed_photos(photos)
    f.footer()
    return f.get_bytes()
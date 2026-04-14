"""
Module 4 -- The Forensic RAMS
==============================
Generates a formatted, professional PDF Risk Assessment & Method Statement
(RAMS) document using ReportLab.

Input JSON schema
-----------------
{
    "worker_name":    str,            # Full name of the operative
    "site_location":  str,            # Site address / description
    "job_type":       str,            # Nature of the work
    "hazard_checks": {                # Dict of hazard flag -> bool
        "working_at_height":    bool,
        "high_pressure_water":  bool,
        "traffic_management":   bool,
        "night_shift":          bool,
        "public_footfall":      bool,
        "coshh":                bool,
        "confined_space":       bool,
        "electrical":           bool,
    },
    # Optional enrichment fields
    "prepared_by":    str | None,
    "reviewed_by":    str | None,
    "approved_by":    str | None,
    "rams_ref":       str | None,     # Auto-generated if omitted
    "issue_date":     str | None,     # ISO date; defaults to today
    "review_date":    str | None,     # ISO date; defaults to today + 12 months
    "risk_level":     str | None,     # "Low" | "Medium" | "High" | "Critical"
    "ppe_required":   list[str] | None,
    "method_statement": str | None,   # Free-text method statement override
}

Output
------
Saves a PDF to /rams_vault/<rams_ref>.pdf and returns the absolute path.

Usage (CLI)
-----------
    python rams_pdf_generator.py '{"worker_name": "John Smith", ...}'
    python rams_pdf_generator.py --input job.json

Usage (library)
---------------
    from rams_pdf_generator import generate_rams_pdf
    path = generate_rams_pdf(data)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, mm
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# ---------------------------------------------------------------------------
# Output directory
# ---------------------------------------------------------------------------

RAMS_VAULT = Path(os.getenv("RAMS_VAULT_PATH", "/rams_vault"))

# ---------------------------------------------------------------------------
# Brand colours (Lilieth Sovereign Network palette)
# ---------------------------------------------------------------------------

_BRAND_DARK = colors.HexColor("#0A1628")    # Deep navy
_BRAND_GOLD = colors.HexColor("#C9A84C")    # Sovereign gold
_BRAND_MID  = colors.HexColor("#1E3A5F")    # Mid blue
_GREY_LIGHT = colors.HexColor("#F2F4F7")    # Table zebra stripe
_TEXT_DARK  = colors.HexColor("#1A1A2E")
_RED_RISK   = colors.HexColor("#C0392B")
_ORANGE_RISK = colors.HexColor("#E67E22")
_GREEN_RISK = colors.HexColor("#27AE60")

# ---------------------------------------------------------------------------
# Hazard library
# Each entry: description, severity (1-5), likelihood (1-5), controls, ppe
# ---------------------------------------------------------------------------

_HAZARD_LIBRARY: dict[str, dict[str, Any]] = {
    "working_at_height": {
        "description": "Working at Height (>=2 m)",
        "severity": 5,
        "likelihood": 3,
        "controls": [
            "Use of PASMA/IPAF-inspected tower scaffold or MEWP.",
            "Exclusion zone erected below work area.",
            "Harness and lanyard worn at all times above 2 m.",
            "Pre-use inspection of all equipment before each shift.",
            "Two-person minimum for scaffold erection/dismantling.",
        ],
        "ppe": [
            "Safety helmet (EN 397)",
            "Fall-arrest harness (EN 361)",
            "Non-slip safety boots (EN ISO 20345 S3)",
        ],
    },
    "high_pressure_water": {
        "description": "High-Pressure Water Jetting (>100 bar)",
        "severity": 4,
        "likelihood": 3,
        "controls": [
            "Operator holds current WJTA or WaterJet UK certificated training.",
            "Equipment pressure-tested and hose inspected before use.",
            "Dead-man trigger gun with auto-shutoff fitted.",
            "No bystanders within 6 m of jetting operations.",
            "All drainage outflows identified and protected.",
        ],
        "ppe": [
            "Full-face visor (EN 166)",
            "Waterproof cut-resistant gloves",
            "Waterproof over-trousers and jacket",
            "Safety boots (S3 WR)",
        ],
    },
    "traffic_management": {
        "description": "Working in/adjacent to Live Traffic",
        "severity": 5,
        "likelihood": 4,
        "controls": [
            "Chapter 8-compliant TM scheme approved and in place.",
            "All operatives hold current NHSS 12AB or equivalent.",
            "Stop/Go board or RTMC signals manned at all times.",
            "High-visibility clothing worn at all times on site.",
            "Emergency vehicle access maintained throughout.",
            "Works notified to Highways Authority / permit obtained.",
        ],
        "ppe": [
            "Class 3 Hi-Vis vest (EN ISO 20471)",
            "Safety helmet (EN 397)",
            "Steel-toe boots (EN ISO 20345 S3)",
        ],
    },
    "night_shift": {
        "description": "Night-Shift / Out-of-Hours Working",
        "severity": 3,
        "likelihood": 3,
        "controls": [
            "Enhanced lighting (500 lux min) at all work areas.",
            "Lone-worker check-in protocol every 30 minutes.",
            "Supervisor briefing before shift detailing emergency contacts.",
            "Fatigue management -- max 12 h shift with 30 min break at 6 h.",
            "High-visibility clothing mandatory throughout.",
        ],
        "ppe": [
            "Class 3 Hi-Vis vest",
            "Head torch (backup lighting)",
        ],
    },
    "public_footfall": {
        "description": "Work in High-Footfall Public Areas",
        "severity": 4,
        "likelihood": 4,
        "controls": [
            "Pedestrian management barriers erected and stewarded.",
            "Diversionary signage installed >=10 m from work zone.",
            "Works notified to local authority and BID (where applicable).",
            "Debris netting / catch platforms deployed overhead.",
            "Dust and noise minimised; work paused during peak pedestrian hours.",
        ],
        "ppe": [
            "Hi-Vis vest (Class 2 min)",
            "Safety helmet",
            "Safety boots",
        ],
    },
    "coshh": {
        "description": "Use of Hazardous Substances (COSHH)",
        "severity": 4,
        "likelihood": 2,
        "controls": [
            "COSHH assessment completed for each substance used.",
            "SDS sheets accessible on-site at all times.",
            "Biocides / chemicals stored in marked, locked containers.",
            "Spill kit present on vehicle; Environment Agency spillage protocol followed.",
            "No mixing of chemicals unless SDS confirms compatibility.",
        ],
        "ppe": [
            "Chemical-resistant nitrile gloves",
            "Safety goggles (EN 166)",
            "Chemical-resistant apron",
            "Respirator FFP3 (if atomised)",
        ],
    },
    "confined_space": {
        "description": "Entry into Confined Space",
        "severity": 5,
        "likelihood": 2,
        "controls": [
            "Confined-space entry permit issued before any entry.",
            "Atmospheric monitoring (O2, CO, H2S, LEL) throughout.",
            "Trained standby person stationed at entry point.",
            "Non-sparking tools used where flammable atmosphere possible.",
            "SCBA set available on-site.",
            "Emergency retrieval system rigged before entry.",
        ],
        "ppe": [
            "SCBA (EN 137)",
            "Full-body harness with retrieval line",
            "Chemical-resistant suit (if required by atmosphere)",
        ],
    },
    "electrical": {
        "description": "Proximity to Electrical Services",
        "severity": 5,
        "likelihood": 2,
        "controls": [
            "Cable-avoidance tool (CAT) used before any ground break.",
            "Minimum safe-approach distances observed per GS6 guidance.",
            "DNO / ICP notified; isolation confirmed before works commence.",
            "All work on or near live HV assets stopped; DNO to attend.",
            "Insulated tools and gloves rated for voltage present.",
        ],
        "ppe": [
            "Class-1 insulating gloves",
            "Arc-flash face shield",
            "Flame-retardant clothing (IEC 61482-2)",
        ],
    },
}

_DEFAULT_PPE = [
    "Safety helmet (EN 397)",
    "High-visibility vest Class 2+ (EN ISO 20471)",
    "Safety boots S1P/S3 (EN ISO 20345)",
    "Nitrile work gloves",
]

# ---------------------------------------------------------------------------
# Risk matrix (severity x likelihood -> label)
# ---------------------------------------------------------------------------

_RISK_MATRIX: dict[tuple[int, int], str] = {}
for _s in range(1, 6):
    for _l in range(1, 6):
        _score = _s * _l
        if _score >= 15:
            _RISK_MATRIX[(_s, _l)] = "Critical"
        elif _score >= 9:
            _RISK_MATRIX[(_s, _l)] = "High"
        elif _score >= 4:
            _RISK_MATRIX[(_s, _l)] = "Medium"
        else:
            _RISK_MATRIX[(_s, _l)] = "Low"

_RISK_PRIORITY = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}

_RISK_COLOURS = {
    "Critical": _RED_RISK,
    "High":     _ORANGE_RISK,
    "Medium":   colors.HexColor("#F1C40F"),
    "Low":      _GREEN_RISK,
}


def _risk_colour(label: str) -> Any:
    return _RISK_COLOURS.get(label, colors.grey)


# ---------------------------------------------------------------------------
# ReportLab style registry
# ---------------------------------------------------------------------------


def _build_styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()

    styles: dict[str, ParagraphStyle] = {}

    styles["doc_title"] = ParagraphStyle(
        "doc_title",
        parent=base["Title"],
        fontName="Helvetica-Bold",
        fontSize=22,
        textColor=colors.white,
        spaceAfter=4,
        leading=26,
    )
    styles["doc_subtitle"] = ParagraphStyle(
        "doc_subtitle",
        parent=base["Normal"],
        fontName="Helvetica",
        fontSize=11,
        textColor=_BRAND_GOLD,
        spaceAfter=0,
    )
    styles["section_heading"] = ParagraphStyle(
        "section_heading",
        parent=base["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=13,
        textColor=_BRAND_DARK,
        spaceBefore=14,
        spaceAfter=4,
        borderPad=4,
        borderColor=_BRAND_GOLD,
        borderWidth=0,
        leftIndent=0,
    )
    styles["body"] = ParagraphStyle(
        "body",
        parent=base["Normal"],
        fontName="Helvetica",
        fontSize=10,
        textColor=_TEXT_DARK,
        leading=14,
        spaceAfter=4,
    )
    styles["bullet"] = ParagraphStyle(
        "bullet",
        parent=styles["body"],
        leftIndent=14,
        bulletIndent=4,
        spaceAfter=2,
    )
    styles["table_header"] = ParagraphStyle(
        "table_header",
        parent=base["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9,
        textColor=colors.white,
    )
    styles["table_cell"] = ParagraphStyle(
        "table_cell",
        parent=base["Normal"],
        fontName="Helvetica",
        fontSize=9,
        textColor=_TEXT_DARK,
        leading=12,
    )
    styles["footer"] = ParagraphStyle(
        "footer",
        parent=base["Normal"],
        fontName="Helvetica",
        fontSize=8,
        textColor=colors.grey,
    )
    return styles


# ---------------------------------------------------------------------------
# PDF generation
# ---------------------------------------------------------------------------


def generate_rams_pdf(data: dict[str, Any]) -> Path:
    """
    Generate a professional RAMS PDF from *data* and save to RAMS_VAULT.

    Returns the absolute Path to the generated file.
    """
    # ------------------------------------------------------------------
    # Resolve / validate input
    # ------------------------------------------------------------------
    worker_name: str = data.get("worker_name", "Unknown Worker")
    site_location: str = data.get("site_location", "Unknown Site")
    job_type: str = data.get("job_type", "General Works")
    hazard_checks: dict[str, bool] = data.get("hazard_checks", {})

    prepared_by: str = data.get("prepared_by") or "Lilieth Orchestrator"
    reviewed_by: str = data.get("reviewed_by") or "Site Supervisor"
    approved_by: str = data.get("approved_by") or "Operations Manager"

    today = date.today()
    rams_ref: str = (
        data.get("rams_ref")
        or f"RAMS-{today.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
    )
    issue_date_str: str = data.get("issue_date") or today.isoformat()
    review_date_str: str = (
        data.get("review_date")
        or (today + timedelta(days=365)).isoformat()
    )

    custom_method_statement: str | None = data.get("method_statement")
    custom_ppe: list[str] | None = data.get("ppe_required")

    # ------------------------------------------------------------------
    # Resolve active hazards
    # ------------------------------------------------------------------
    active_hazards: list[dict[str, Any]] = []
    all_ppe: list[str] = list(_DEFAULT_PPE)

    for flag, is_active in hazard_checks.items():
        if not is_active:
            continue
        hazard = _HAZARD_LIBRARY.get(flag, {
            "description": flag.replace("_", " ").title(),
            "severity": 3,
            "likelihood": 3,
            "controls": ["Risk assessed by supervisor before task commences."],
            "ppe": [],
        })
        active_hazards.append(hazard)
        all_ppe.extend(hazard.get("ppe", []))

    if custom_ppe:
        all_ppe.extend(custom_ppe)
    all_ppe = sorted(set(all_ppe))

    # Overall risk
    if data.get("risk_level"):
        overall_risk = data["risk_level"]
    elif active_hazards:
        scored = [
            _RISK_MATRIX.get((h["severity"], h["likelihood"]), "Low")
            for h in active_hazards
        ]
        overall_risk = min(scored, key=lambda r: _RISK_PRIORITY.get(r, 99))
    else:
        overall_risk = "Low"

    # ------------------------------------------------------------------
    # Build method statement
    # ------------------------------------------------------------------
    if custom_method_statement:
        method_statement = custom_method_statement
    else:
        method_lines = [
            f"This RAMS covers all works associated with: {job_type}",
            f"Location: {site_location}",
            f"Operative: {worker_name}",
            "",
            "Sequence of Work:",
            "1. Site induction and emergency briefing completed.",
            "2. PPE donned and inspected before entering work zone.",
            "3. Exclusion zones and signage established.",
            "4. Work carried out in accordance with identified controls.",
            "5. Continuous monitoring for hazard changes throughout shift.",
            "6. Safe clearance, waste disposal, and site reinstatement on completion.",
            "7. Supervisor sign-off and incident report filed if applicable.",
        ]
        method_statement = "\n".join(method_lines)

    # ------------------------------------------------------------------
    # Ensure output directory exists
    # ------------------------------------------------------------------
    RAMS_VAULT.mkdir(parents=True, exist_ok=True)
    output_path = RAMS_VAULT / f"{rams_ref}.pdf"

    # ------------------------------------------------------------------
    # Build PDF document
    # ------------------------------------------------------------------
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
        title=f"RAMS - {rams_ref}",
        author=prepared_by,
        subject=f"{job_type} at {site_location}",
    )

    styles = _build_styles()
    story: list[Any] = []

    page_width = A4[0] - 4 * cm  # usable width

    # ------ Header banner ------------------------------------------------
    header_table = Table(
        [[Paragraph("RISK ASSESSMENT &amp; METHOD STATEMENT", styles["doc_title"]),
          Paragraph(rams_ref, styles["doc_subtitle"])]],
        colWidths=[page_width * 0.7, page_width * 0.3],
    )
    header_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), _BRAND_DARK),
        ("TOPPADDING", (0, 0), (-1, -1), 12),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
        ("LEFTPADDING", (0, 0), (-1, -1), 14),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("RIGHTPADDING", (1, 0), (1, 0), 14),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 8))

    # ------ Document details table ---------------------------------------
    risk_colour = _risk_colour(overall_risk)
    detail_rows = [
        [
            Paragraph("<b>Worker Name</b>", styles["table_header"]),
            Paragraph(worker_name, styles["table_cell"]),
            Paragraph("<b>Risk Level</b>", styles["table_header"]),
            Paragraph(
                f'<font color="white"><b>{overall_risk}</b></font>',
                styles["table_header"],
            ),
        ],
        [
            Paragraph("<b>Site Location</b>", styles["table_header"]),
            Paragraph(site_location, styles["table_cell"]),
            Paragraph("<b>Issue Date</b>", styles["table_header"]),
            Paragraph(issue_date_str, styles["table_cell"]),
        ],
        [
            Paragraph("<b>Job Type</b>", styles["table_header"]),
            Paragraph(job_type, styles["table_cell"]),
            Paragraph("<b>Review Date</b>", styles["table_header"]),
            Paragraph(review_date_str, styles["table_cell"]),
        ],
        [
            Paragraph("<b>Prepared By</b>", styles["table_header"]),
            Paragraph(prepared_by, styles["table_cell"]),
            Paragraph("<b>Reviewed By</b>", styles["table_header"]),
            Paragraph(reviewed_by, styles["table_cell"]),
        ],
        [
            Paragraph("<b>Approved By</b>", styles["table_header"]),
            Paragraph(approved_by, styles["table_cell"]),
            Paragraph("", styles["table_cell"]),
            Paragraph("", styles["table_cell"]),
        ],
    ]

    cw = page_width / 4
    detail_table = Table(detail_rows, colWidths=[cw, cw, cw, cw])
    detail_style = TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), _BRAND_MID),
        ("BACKGROUND", (2, 0), (2, -1), _BRAND_MID),
        ("BACKGROUND", (1, 0), (1, -1), _GREY_LIGHT),
        ("BACKGROUND", (3, 0), (3, -1), _GREY_LIGHT),
        # Override risk-level cell background
        ("BACKGROUND", (3, 0), (3, 0), risk_colour),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.white),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ])
    detail_table.setStyle(detail_style)
    story.append(detail_table)
    story.append(Spacer(1, 12))

    # ------ Section 1: Hazard Register -----------------------------------
    story.append(HRFlowable(width="100%", thickness=2, color=_BRAND_GOLD))
    story.append(
        Paragraph("1. HAZARD REGISTER &amp; RISK ASSESSMENT", styles["section_heading"])
    )
    story.append(Spacer(1, 4))

    if active_hazards:
        hazard_header = [
            Paragraph("<b>Hazard</b>", styles["table_header"]),
            Paragraph("<b>Sev</b>", styles["table_header"]),
            Paragraph("<b>Like</b>", styles["table_header"]),
            Paragraph("<b>Score</b>", styles["table_header"]),
            Paragraph("<b>Rating</b>", styles["table_header"]),
            Paragraph("<b>Control Measures</b>", styles["table_header"]),
        ]
        hazard_rows = [hazard_header]

        for h in active_hazards:
            sev = h["severity"]
            like = h["likelihood"]
            score = sev * like
            rating = _RISK_MATRIX.get((sev, like), "Low")
            rcolour = _risk_colour(rating)
            controls_text = "<br/>".join(
                f"• {c}" for c in h.get("controls", [])
            )
            hazard_rows.append([
                Paragraph(h["description"], styles["table_cell"]),
                Paragraph(str(sev), styles["table_cell"]),
                Paragraph(str(like), styles["table_cell"]),
                Paragraph(str(score), styles["table_cell"]),
                Paragraph(
                    f'<font color="white"><b>{rating}</b></font>',
                    styles["table_header"],
                ),
                Paragraph(controls_text, styles["table_cell"]),
            ])

        hz_col_widths = [
            page_width * 0.20,
            page_width * 0.06,
            page_width * 0.06,
            page_width * 0.06,
            page_width * 0.10,
            page_width * 0.52,
        ]
        hazard_table = Table(hazard_rows, colWidths=hz_col_widths, repeatRows=1)
        hz_style = TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), _BRAND_DARK),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _GREY_LIGHT]),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ])
        # Colour each rating cell
        for i, h in enumerate(active_hazards, start=1):
            sev = h["severity"]
            like = h["likelihood"]
            rating = _RISK_MATRIX.get((sev, like), "Low")
            rcolour = _risk_colour(rating)
            hz_style.add("BACKGROUND", (4, i), (4, i), rcolour)

        hazard_table.setStyle(hz_style)
        story.append(hazard_table)
    else:
        story.append(
            Paragraph(
                "No specific hazards identified. Standard site safety rules apply.",
                styles["body"],
            )
        )

    story.append(Spacer(1, 12))

    # ------ Section 2: PPE Requirements ----------------------------------
    story.append(HRFlowable(width="100%", thickness=2, color=_BRAND_GOLD))
    story.append(
        Paragraph("2. PERSONAL PROTECTIVE EQUIPMENT (PPE)", styles["section_heading"])
    )
    story.append(Spacer(1, 4))

    ppe_cols = 3
    ppe_padded = all_ppe + [""] * (ppe_cols - len(all_ppe) % ppe_cols or 0)
    ppe_rows = [
        [Paragraph(f"&#10004; {item}" if item else "", styles["table_cell"])
         for item in ppe_padded[i: i + ppe_cols]]
        for i in range(0, len(ppe_padded), ppe_cols)
    ]
    if ppe_rows:
        ppe_table = Table(
            ppe_rows,
            colWidths=[page_width / ppe_cols] * ppe_cols,
        )
        ppe_table.setStyle(TableStyle([
            ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, _GREY_LIGHT]),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        story.append(ppe_table)

    story.append(Spacer(1, 12))

    # ------ Section 3: Method Statement ----------------------------------
    story.append(HRFlowable(width="100%", thickness=2, color=_BRAND_GOLD))
    story.append(
        Paragraph("3. METHOD STATEMENT", styles["section_heading"])
    )
    story.append(Spacer(1, 4))

    for line in method_statement.splitlines():
        if line.strip():
            story.append(Paragraph(line, styles["body"]))
        else:
            story.append(Spacer(1, 4))

    story.append(Spacer(1, 12))

    # ------ Section 4: Emergency Procedures ------------------------------
    story.append(HRFlowable(width="100%", thickness=2, color=_BRAND_GOLD))
    story.append(
        Paragraph("4. EMERGENCY PROCEDURES", styles["section_heading"])
    )
    emergency_items = [
        "Emergency assembly point to be confirmed by site supervisor at induction.",
        "First-aider contact: to be confirmed at site induction.",
        "Emergency services: 999 (UK) | Fire, Police, Ambulance.",
        "In case of chemical spill: evacuate area, contain if safe, call Environment Agency hotline: 0800 80 70 60.",
        "Serious injury: do not move casualty; call 999; preserve scene for investigation.",
        "All incidents, near-misses, and dangerous occurrences must be reported on the company incident form within 24 h.",
    ]
    for item in emergency_items:
        story.append(Paragraph(f"• {item}", styles["bullet"]))

    story.append(Spacer(1, 12))

    # ------ Section 5: Sign-off ------------------------------------------
    story.append(HRFlowable(width="100%", thickness=2, color=_BRAND_GOLD))
    story.append(
        Paragraph("5. OPERATIVE SIGN-OFF", styles["section_heading"])
    )
    story.append(Spacer(1, 4))
    story.append(
        Paragraph(
            "By signing below, the operative confirms they have read, understood, "
            "and will comply with all sections of this RAMS document.",
            styles["body"],
        )
    )
    story.append(Spacer(1, 8))

    signoff_rows = [
        [
            Paragraph("<b>Operative Name</b>", styles["table_header"]),
            Paragraph(worker_name, styles["table_cell"]),
            Paragraph("<b>Signature</b>", styles["table_header"]),
            Paragraph("", styles["table_cell"]),
        ],
        [
            Paragraph("<b>Date</b>", styles["table_header"]),
            Paragraph("", styles["table_cell"]),
            Paragraph("<b>RAMS Ref</b>", styles["table_header"]),
            Paragraph(rams_ref, styles["table_cell"]),
        ],
    ]
    cw2 = page_width / 4
    signoff_table = Table(signoff_rows, colWidths=[cw2, cw2, cw2, cw2])
    signoff_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), _BRAND_MID),
        ("BACKGROUND", (2, 0), (2, -1), _BRAND_MID),
        ("BACKGROUND", (1, 0), (1, -1), _GREY_LIGHT),
        ("BACKGROUND", (3, 0), (3, -1), _GREY_LIGHT),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.white),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        # Make the signature cell taller
        ("ROWHEIGHT", (0, 0), (-1, -1), 30),
    ]))
    story.append(signoff_table)

    story.append(Spacer(1, 16))

    # ------ Footer -------------------------------------------------------
    story.append(HRFlowable(width="100%", thickness=1, color=colors.lightgrey))
    story.append(Spacer(1, 4))
    story.append(
        Paragraph(
            f"Document ref: {rams_ref} | "
            f"Issued: {issue_date_str} | "
            f"Review: {review_date_str} | "
            f"Prepared by: {prepared_by} | "
            "Lilieth Sovereign Network -- Dual-Business Hub",
            styles["footer"],
        )
    )

    # ------ Build --------------------------------------------------------
    doc.build(story)

    return output_path.resolve()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _cli() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a RAMS PDF from a JSON payload."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "json_string",
        nargs="?",
        help="Inline JSON string.",
    )
    group.add_argument(
        "--input",
        "-i",
        metavar="FILE",
        help="Path to a JSON file.",
    )
    args = parser.parse_args()

    if args.input:
        with open(args.input, encoding="utf-8") as fh:
            data = json.load(fh)
    else:
        data = json.loads(args.json_string)

    output = generate_rams_pdf(data)
    print(f"RAMS PDF generated: {output}")


if __name__ == "__main__":
    _cli()

"""
Lilieth Guard — RAMS Generator
================================
Generates production-quality Risk Assessment & Method Statement (RAMS)
documents for the Dual-Business Hub.

Usage
-----
    python rams_generator.py "Roof Clean, Covent Garden, 3 stories"
    python rams_generator.py --job "High-pressure clean, M25 Junction 9, night shift"
    python rams_generator.py --job "Commercial fascia clean, Piccadilly, 2 stories" \\
                             --prepared-by "Paul Cassidy" \\
                             --reviewed-by "Dean Mitchell"

The script analyses job_details against Paul Cassidy's 30-year road-grit knowledge
base and writes a fully compliant RAMS markdown file to the /rams/ directory.
"""

from __future__ import annotations

import argparse
import re
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Output directory (created relative to this script's parent when run locally,
# or at /app/rams when running inside the Docker container)
# ---------------------------------------------------------------------------
RAMS_DIR = Path(__file__).resolve().parent.parent / "rams"

# ---------------------------------------------------------------------------
# Risk scoring helpers
# ---------------------------------------------------------------------------

# Severity × Likelihood matrix value → label
RISK_MATRIX: dict[int, str] = {
    1: "Low",
    2: "Low",
    3: "Low",
    4: "Medium",
    6: "Medium",
    8: "Medium",
    9: "High",
    12: "High",
    16: "Critical",
    25: "Critical",
}


def score_to_label(severity: int, likelihood: int) -> str:
    score = severity * likelihood
    for threshold, label in sorted(RISK_MATRIX.items(), reverse=True):
        if score >= threshold:
            return label
    return "Low"


# ---------------------------------------------------------------------------
# Hazard detection — keyword analysis on raw job_details text
# ---------------------------------------------------------------------------

_KEYWORD_MAP: list[tuple[list[str], str]] = [
    # High-pressure water
    (["pressure", "wash", "blast", "clean", "jet", "softwash", "roof clean"],
     "high_pressure_water"),
    # Working at height
    (["stor", "height", "roof", "scaffold", "ladder", "cherry picker", "mewp",
      "elevated", "aerial", "gutter"],
     "working_at_height"),
    # Traffic management / roads
    (["motorway", "m1", "m2", "m3", "m4", "m5", "m6", "m25", "a road",
      "a-road", "junction", "highway", "carriageway", "road", "street",
      "traffic", "lane closure", "tmc"],
     "traffic_management"),
    # Night shift
    (["night", "overnight", "nocturnal", "out-of-hours", "after hours"],
     "night_shift"),
    # High footfall public areas
    (["piccadilly", "covent garden", "oxford street", "trafalgar", "carnaby",
      "shoreditch", "brixton", "canary wharf", "city of london",
      "pedestrian", "public", "footfall", "high street"],
     "public_footfall"),
    # COSHH / chemicals
    (["chemical", "biocide", "softwash", "sodium hypochlorite", "bleach",
      "detergent", "solvent", "coshh", "hazardous"],
     "coshh"),
    # Confined spaces
    (["confined", "tank", "pit", "vault", "sewer", "drain", "basement"],
     "confined_space"),
    # Electrical
    (["electrical", "live wire", "substation", "overhead line", "pylon",
      "hv", "lv", "cable"],
     "electrical"),
]


def detect_hazards(text: str) -> dict[str, bool]:
    """Return a dict of hazard flags based on keyword matching."""
    lower = text.lower()
    flags: dict[str, bool] = {key: False for _, key in _KEYWORD_MAP}
    for keywords, flag_name in _KEYWORD_MAP:
        if any(kw in lower for kw in keywords):
            flags[flag_name] = True
    return flags


def parse_height(text: str) -> float | None:
    """Extract number of stories / metres from job_details text."""
    # e.g. "3 stories", "3 storey", "4m", "10 metres"
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:stor(?:y|eys?|ies?)|floor)", text, re.I)
    if m:
        return float(m.group(1)) * 3.0  # ~3 m per storey
    m = re.search(r"(\d+(?:\.\d+)?)\s*m(?:etre|eter)?s?\b", text, re.I)
    if m:
        return float(m.group(1))
    return None


def extract_location(text: str) -> str:
    """Best-effort location extraction (comma-separated token after first comma)."""
    parts = [p.strip() for p in text.split(",")]
    return parts[1] if len(parts) >= 2 else "Site location not specified"


# ---------------------------------------------------------------------------
# RAMS section builders
# ---------------------------------------------------------------------------

def build_hazard_table(hazards: dict[str, bool], height_m: float | None) -> str:
    """Build the Markdown risk register table."""
    rows: list[dict[str, Any]] = []

    if hazards.get("high_pressure_water"):
        rows.append({
            "hazard": "High-pressure water jetting / washing",
            "who": "Operatives, public, bystanders",
            "severity": 4, "likelihood": 3,
            "controls": (
                "Operatives trained and certificated (CSSA / NJC). "
                "Minimum 100 bar, max 350 bar unless risk assessed. "
                "Full PPE: waterproof suit, face visor, gloves (cut-resistant level B). "
                "Exclusion zone established with barriers and signage. "
                "Water run-off managed — no discharge to surface water without consent."
            ),
        })

    if hazards.get("working_at_height"):
        h_str = f"{height_m:.1f} m" if height_m else "TBC"
        rows.append({
            "hazard": f"Working at height ({h_str})",
            "who": "Operatives",
            "severity": 5, "likelihood": 2,
            "controls": (
                "Work-at-height plan produced before works commence (WAH Regs 2005). "
                "Competent persons only — PASMA/IPAF certificated where applicable. "
                "Scaffold erected/dismantled by CISRS card-holder. "
                "Ladders: 3-point contact, secured at top and foot, 75° angle, max 4 m. "
                "MEWP: daily pre-use inspection, harness and lanyard worn. "
                "Exclusion zone beneath all overhead works."
            ),
        })

    if hazards.get("traffic_management"):
        rows.append({
            "hazard": "Moving vehicles / live traffic",
            "who": "Operatives, road users",
            "severity": 5, "likelihood": 3,
            "controls": (
                "Traffic Management Plan (TMP) produced in accordance with Chapter 8 "
                "of the Traffic Signs Manual. "
                "All TM operatives hold NHSS 12AB or equivalent. "
                "Lane closure signed and lit per TSM Chapter 8. "
                "High-visibility clothing (Class 3 EN ISO 20471) worn at all times. "
                "Night works: additional lighting towers, advance signing extended by 50%."
            ),
        })

    if hazards.get("night_shift"):
        rows.append({
            "hazard": "Night-shift / low-visibility working",
            "who": "Operatives, road users",
            "severity": 4, "likelihood": 3,
            "controls": (
                "Works permitted only under approved TMP. "
                "All operatives briefed on night-working fatigue management. "
                "Lighting: minimum 50 lux on working surface. "
                "Buddy system: no operative works alone. "
                "Regular welfare breaks; warm drinks provided. "
                "Site supervisor contactable at all times."
            ),
        })

    if hazards.get("public_footfall"):
        rows.append({
            "hazard": "Public footfall management in high-footfall area",
            "who": "Members of public",
            "severity": 4, "likelihood": 4,
            "controls": (
                "Temporary pedestrian route established with appropriate signage. "
                "Barriers (Heras / water-filled) erected to full exclusion zone. "
                "Banksman/steward deployed during peak hours. "
                "Liaison with local authority / BID / police if required. "
                "Works planned for low-footfall windows where possible (early morning). "
                "Public-liability insurance: minimum £5 m confirmed on site."
            ),
        })

    if hazards.get("coshh"):
        rows.append({
            "hazard": "Hazardous substances (COSHH)",
            "who": "Operatives, environment",
            "severity": 3, "likelihood": 2,
            "controls": (
                "COSHH assessment completed for each substance. "
                "SDS sheets reviewed and available on site. "
                "PPE as per SDS: nitrile gloves, goggles, respirator (P2/P3). "
                "Dilution and application per manufacturer guidelines. "
                "Spill kit on site; no run-off to drains without authorisation. "
                "Emergency eye-wash available."
            ),
        })

    if hazards.get("confined_space"):
        rows.append({
            "hazard": "Confined space entry",
            "who": "Operatives",
            "severity": 5, "likelihood": 2,
            "controls": (
                "Confined Spaces Regs 1997 procedures strictly followed. "
                "Atmospheric testing (O₂, CO, H₂S, LEL) before and during entry. "
                "Entry permit system in place. "
                "Trained top-man / standby person always present. "
                "Emergency retrieval equipment rigged before entry. "
                "BA set available on site."
            ),
        })

    if hazards.get("electrical"):
        rows.append({
            "hazard": "Proximity to electrical hazards",
            "who": "Operatives, public",
            "severity": 5, "likelihood": 2,
            "controls": (
                "Dial-Before-You-Dig: cable search via LSBUD/Safe Dig before works. "
                "Exclusion zone: minimum 3 m from HV overhead lines. "
                "All 110 V site tools; RCDs fitted. "
                "Wet conditions: no electrical works until surface dry. "
                "Permit-to-work from Distribution Network Operator if required."
            ),
        })

    # Always include manual handling and slips/trips
    rows.append({
        "hazard": "Manual handling / slips, trips and falls at ground level",
        "who": "Operatives",
        "severity": 3, "likelihood": 3,
        "controls": (
            "Manual handling assessment completed (Manual Handling Ops Regs 1992). "
            "Team lifts for loads > 25 kg. "
            "Work area kept tidy and clear of trailing hoses/cables. "
            "Non-slip footwear (S3 safety boots) worn at all times."
        ),
    })

    lines = [
        "| # | Hazard | Persons at Risk | Severity | Likelihood | Risk Level | Controls |",
        "|---|--------|-----------------|----------|------------|------------|----------|",
    ]
    for i, row in enumerate(rows, 1):
        level = score_to_label(row["severity"], row["likelihood"])
        lines.append(
            f"| {i} | {row['hazard']} | {row['who']} "
            f"| {row['severity']} | {row['likelihood']} | **{level}** "
            f"| {row['controls']} |"
        )
    return "\n".join(lines)


def build_method_statement(job_details: str, hazards: dict[str, bool],
                           height_m: float | None) -> str:
    """Build the Method Statement narrative section."""
    sections: list[str] = []

    sections.append(
        "### 1. Pre-Works\n"
        "- Site induction for all operatives, including site-specific hazards.\n"
        "- Review and sign RAMS; ensure all operatives understand their responsibilities.\n"
        "- Confirm all plant, equipment and PPE is present, serviceable and inspected.\n"
        "- Erect all barriers, signage and exclusion zones before commencing works.\n"
        "- Confirm emergency arrangements and nearest A&E location."
    )

    mobilisation_steps = [
        "Mobilise vehicles to site; park in designated area and apply banksman where required.",
        "Unload equipment using correct manual handling techniques.",
    ]
    if hazards.get("traffic_management"):
        mobilisation_steps.append(
            "Implement Traffic Management Plan: place advance signing, "
            "cone taper, then establish working space per Chapter 8."
        )
    if hazards.get("public_footfall"):
        mobilisation_steps.append(
            "Establish pedestrian exclusion zone using water-filled barriers. "
            "Deploy steward at pedestrian diversion point."
        )
    sections.append(
        "### 2. Mobilisation & Site Set-Up\n"
        + "\n".join(f"- {s}" for s in mobilisation_steps)
    )

    works_steps: list[str] = []
    if hazards.get("working_at_height"):
        h_str = f"{height_m:.1f} m" if height_m else "as determined by survey"
        works_steps += [
            f"Erect access equipment to working height ({h_str}) per manufacturer's "
            "instructions and WAH Regs 2005.",
            "Inspect access equipment before use — record on pre-use checklist.",
            "Operatives to wear harness and attach lanyard before ascending above 2 m.",
        ]
    if hazards.get("high_pressure_water"):
        works_steps += [
            "Connect pressure washer to water supply / bowser. "
            "Confirm correct lance and nozzle for surface type.",
            "Test pressure at ground level before applying to surface.",
            "Apply cleaning agent top-down; dwell per manufacturer guidelines.",
            "Rinse thoroughly. Manage water run-off — pump to IBC / drain "
            "with appropriate consent.",
            "Inspect surface for damage on completion.",
        ]
    if hazards.get("coshh"):
        works_steps.append(
            "Apply biocide / chemical per dilution rate on COSHH assessment. "
            "Keep public away during application."
        )
    if not works_steps:
        works_steps.append("Carry out specified works per client brief and drawings.")
    sections.append(
        "### 3. Works Execution\n"
        + "\n".join(f"- {s}" for s in works_steps)
    )

    sections.append(
        "### 4. Demobilisation\n"
        "- Remove all tools, equipment and waste from site.\n"
        "- Remove traffic management / barriers in reverse order of placement.\n"
        "- Leave site clean and tidy; photograph on completion.\n"
        "- Complete site diary and obtain client sign-off where required."
    )

    if hazards.get("night_shift"):
        sections.append(
            "### 5. Night-Shift Specific Controls\n"
            "- Supervisor to carry out fatigue checks every 2 hours.\n"
            "- 'Stop and Rest' protocol: no operative to continue if alertness "
            "is compromised.\n"
            "- Emergency contact list distributed before shift starts.\n"
            "- Dawn check: ensure all TM removed before morning peak traffic."
        )

    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# Main document assembly
# ---------------------------------------------------------------------------

def generate_rams(
    job_details: str,
    prepared_by: str = "Lilieth Guard (AI)",
    reviewed_by: str = "Site Supervisor",
    approved_by: str = "Paul Cassidy — Operations Director",
) -> Path:
    """
    Generate a RAMS document for the given job_details string.

    Returns the Path to the written markdown file.
    """
    RAMS_DIR.mkdir(parents=True, exist_ok=True)

    today = date.today()
    review_date = today + timedelta(days=365)
    rams_ref = f"RAMS-{today.strftime('%Y%m%d')}-{str(uuid.uuid4())[:8].upper()}"

    hazards = detect_hazards(job_details)
    height_m = parse_height(job_details) if hazards.get("working_at_height") else None
    location = extract_location(job_details)

    # Determine overall risk level
    all_combos = []
    if hazards.get("high_pressure_water"):
        all_combos.append((4, 3))
    if hazards.get("working_at_height"):
        all_combos.append((5, 2))
    if hazards.get("traffic_management"):
        all_combos.append((5, 3))
    if hazards.get("public_footfall"):
        all_combos.append((4, 4))
    if hazards.get("night_shift"):
        all_combos.append((4, 3))
    if not all_combos:
        all_combos = [(3, 3)]
    worst_severity, worst_likelihood = max(all_combos, key=lambda sl: sl[0] * sl[1])
    overall_risk = score_to_label(worst_severity, worst_likelihood)

    hazard_table = build_hazard_table(hazards, height_m)
    method_statement = build_method_statement(job_details, hazards, height_m)

    # PPE schedule derived from detected hazards
    ppe_list = ["Safety boots (S3)", "Hi-vis jacket (Class 2+)", "Hard hat (where overhead risk)"]
    if hazards.get("high_pressure_water"):
        ppe_list += ["Waterproof over-suit", "Face visor", "Cut-resistant gloves (Level B)"]
    if hazards.get("working_at_height"):
        ppe_list += ["Full-body harness (EN 361)", "Energy-absorbing lanyard (EN 355)"]
    if hazards.get("coshh"):
        ppe_list += ["Chemical-resistant gloves (nitrile)", "Safety goggles (EN 166)", "Respirator (FFP2/P3)"]

    # Welfare & Emergency
    emergency_section = (
        "| Item | Details |\n"
        "|------|---------|\n"
        f"| Nearest A&E | TBC — confirm before works commence |\n"
        f"| Site First Aider | {reviewed_by} (certificated) |\n"
        "| First Aid Kit | Located in site vehicle / welfare unit |\n"
        "| Emergency Assembly Point | Confirmed during site induction |\n"
        "| NHS 111 | 111 |\n"
        "| Emergency Services | 999 |\n"
        "| Environment Agency (spills) | 0800 80 70 60 |"
    )

    active_flags = [k.replace("_", " ").title() for k, v in hazards.items() if v]
    flags_str = ", ".join(active_flags) if active_flags else "Standard operations"

    doc = f"""# Risk Assessment & Method Statement (RAMS)

---

## Document Control

| Field | Value |
|-------|-------|
| **RAMS Reference** | {rams_ref} |
| **Job Details** | {job_details} |
| **Location** | {location} |
| **Overall Risk Level** | **{overall_risk}** |
| **Prepared By** | {prepared_by} |
| **Reviewed By** | {reviewed_by} |
| **Approved By** | {approved_by} |
| **Issue Date** | {today.strftime('%d %B %Y')} |
| **Review Date** | {review_date.strftime('%d %B %Y')} |
| **Version** | 1.0 |
| **Hazard Flags** | {flags_str} |

---

## 1. Scope of Works

**Job Description:** {job_details}

**Location:** {location}

This RAMS applies to all operatives and subcontractors engaged on the above works.
All persons must read, understand and sign this document prior to commencing any activity on site.
Any variation to the scope of works must be risk-assessed and this document updated accordingly.

---

## 2. Legislation & Standards Reference

- Health and Safety at Work etc. Act 1974
- Management of Health and Safety at Work Regulations 1999
- Work at Height Regulations 2005
- Manual Handling Operations Regulations 1992
- Control of Substances Hazardous to Health Regulations 2002 (COSHH)
- Personal Protective Equipment at Work Regulations 1992 (as amended 2022)
- Traffic Signs Regulations and General Directions 2016
- Chapter 8 of the Traffic Signs Manual (Traffic Management)
- Confined Spaces Regulations 1997 (where applicable)
- Electricity at Work Regulations 1989 (where applicable)
- Environmental Protection Act 1990

---

## 3. Personnel & Competencies

| Role | Minimum Competency |
|------|--------------------|
| Site Supervisor | SSSTS / SMSTS |
| Operative (construction) | CSCS card (appropriate grade) |
| Streetworks operative | NRSWA Unit 1 + relevant units |
| Traffic Management | NHSS 12AB |
| Working at Height | PASMA / IPAF (as applicable) |
| Scaffold erection | CISRS card |
| Pressure washing | CSSA accredited training |

---

## 4. Risk Assessment Register

{hazard_table}

---

## 5. Personal Protective Equipment (PPE) Schedule

| Item | Standard | Mandatory |
|------|----------|-----------|
""" + "\n".join(
        f"| {item} | EN/BS applicable | ✅ |" for item in ppe_list
    ) + f"""

---

## 6. Method Statement

{method_statement}

---

## 7. Emergency Arrangements

{emergency_section}

---

## 8. Environmental Controls

- Identify any drainage / watercourses within 10 m of works.
- Spill containment kit deployed before works commence.
- No hazardous substances or wash water to enter surface water drainage.
- Waste segregated and disposed of by registered carrier (Waste Transfer Notes retained).
- Noise and dust minimised; works outside permitted hours only with prior consent.

---

## 9. Signatures

| Role | Name | Signature | Date |
|------|------|-----------|------|
| Prepared By | {prepared_by} | | {today.strftime('%d/%m/%Y')} |
| Reviewed By | {reviewed_by} | | |
| Approved By | {approved_by} | | |
| Site Operative | | | |
| Site Operative | | | |
| Site Operative | | | |

---

*This document is generated by the Lilieth Guard module and must be reviewed by a competent
person before use on site. It does not constitute legal advice. Always verify currency of
legislation and standards.*

*Document Reference: {rams_ref} | Generated: {today.isoformat()}*
"""

    safe_name = re.sub(r"[^\w\-]", "_", job_details[:60])
    filename = RAMS_DIR / f"{rams_ref}_{safe_name}.md"
    filename.write_text(doc, encoding="utf-8")
    return filename


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Lilieth Guard — RAMS Generator (Paul Cassidy special)"
    )
    parser.add_argument(
        "job_details",
        nargs="?",
        help="Job details string, e.g. 'Roof Clean, Covent Garden, 3 stories'",
    )
    parser.add_argument(
        "--job", "-j",
        dest="job_flag",
        help="Alternative way to pass job details (flag form)",
    )
    parser.add_argument(
        "--prepared-by",
        default="Lilieth Guard (AI)",
        help="Name of person preparing the RAMS (default: Lilieth Guard (AI))",
    )
    parser.add_argument(
        "--reviewed-by",
        default="Site Supervisor",
        help="Name of person reviewing the RAMS",
    )
    parser.add_argument(
        "--approved-by",
        default="Paul Cassidy — Operations Director",
        help="Name of approving officer",
    )
    args = parser.parse_args()

    job_details = args.job_flag or args.job_details
    if not job_details:
        parser.print_help()
        sys.exit(1)

    output_path = generate_rams(
        job_details=job_details,
        prepared_by=args.prepared_by,
        reviewed_by=args.reviewed_by,
        approved_by=args.approved_by,
    )
    print(f"✅  RAMS document written to: {output_path}")


if __name__ == "__main__":
    main()

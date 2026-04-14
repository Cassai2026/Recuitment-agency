"""
Module 2 — The Optical Shield
==============================
Compliance document verification utility.

Accepts an image URL (e.g. a Twilio MediaUrl for a WhatsApp photo), extracts
all text via Google Cloud Vision (primary) or Tesseract (fallback), then
searches for CSCS / NRSWA card identifiers and an expiry date.

Returns a structured compliance result:
    {
        "is_compliant": bool,
        "card_type":    "CSCS" | "NRSWA" | "UNKNOWN",
        "expiry_date":  "YYYY-MM-DD" | null,
        "is_expired":   bool,
        "raw_text":     str,
        "confidence":   "HIGH" | "MEDIUM" | "LOW"
    }

Environment variables
---------------------
GOOGLE_APPLICATION_CREDENTIALS – Path to GCP service-account JSON key file.
                                  When absent the function falls back to
                                  Tesseract via pytesseract.
USE_TESSERACT_FALLBACK          – Set to "1" to force Tesseract even when
                                  GCP credentials are present (useful in dev).
"""

from __future__ import annotations

import io
import logging
import os
import re
from datetime import date, datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration flags
# ---------------------------------------------------------------------------

_GCP_CREDS = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
_FORCE_TESSERACT = os.getenv("USE_TESSERACT_FALLBACK", "0") == "1"
_USE_GCP = bool(_GCP_CREDS) and not _FORCE_TESSERACT

# ---------------------------------------------------------------------------
# Regex patterns for field extraction
# ---------------------------------------------------------------------------

# Matches "CSCS" or variations like "C.S.C.S", "C S C S"
_RE_CSCS = re.compile(r"\bC\.?\s*S\.?\s*C\.?\s*S\.?\b", re.IGNORECASE)

# Matches "NRSWA" or "New Roads and Street Works"
_RE_NRSWA = re.compile(
    r"\bNRSWA\b|\bNew\s+Roads\s+and\s+Street\s+Works\b",
    re.IGNORECASE,
)

# Matches common UK date formats:
#   DD/MM/YYYY  DD-MM-YYYY  DD.MM.YYYY
#   DD MMM YYYY  DD MMMM YYYY
#   YYYY-MM-DD (ISO)
#   MM/YYYY (card-style short expiry)
_RE_DATE = re.compile(
    r"""
    (?:
        (?P<d1>\d{1,2})[/\-.](?P<m1>\d{1,2})[/\-.](?P<y1>\d{4})   # DD/MM/YYYY
      | (?P<d2>\d{1,2})\s+(?P<mon>[A-Za-z]{3,9})\s+(?P<y2>\d{4})  # DD Mon YYYY
      | (?P<y3>\d{4})[/\-.](?P<m3>\d{2})[/\-.](?P<d3>\d{2})       # ISO YYYY-MM-DD
      | (?P<m4>\d{1,2})[/\-](?P<y4>\d{4})                          # MM/YYYY
    )
    """,
    re.VERBOSE,
)

_MONTH_ABBREVS: dict[str, int] = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Words that frequently appear near an expiry date on a card
_EXPIRY_CONTEXT = re.compile(
    r"(?:expir[eyd]{0,2}|valid\s*(?:until|to|thru)|renewal|renew|by|end|date)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Date parsing helpers
# ---------------------------------------------------------------------------


def _parse_match(m: re.Match) -> date | None:
    """Convert a regex match from _RE_DATE into a date object, or None."""
    try:
        if m.group("y1"):
            return date(int(m.group("y1")), int(m.group("m1")), int(m.group("d1")))
        if m.group("y2"):
            month_str = m.group("mon").lower()[:3]
            month = _MONTH_ABBREVS.get(month_str)
            if month:
                return date(int(m.group("y2")), month, int(m.group("d2")))
        if m.group("y3"):
            return date(int(m.group("y3")), int(m.group("m3")), int(m.group("d3")))
        if m.group("y4"):
            # Month/Year only — treat as last day of that month
            year, month = int(m.group("y4")), int(m.group("m4"))
            # Clamp to valid months (1-12)
            if 1 <= month <= 12:
                import calendar
                last_day = calendar.monthrange(year, month)[1]
                return date(year, month, last_day)
    except (ValueError, TypeError):
        pass
    return None


def _extract_expiry_date(text: str) -> date | None:
    """
    Extract the most likely expiry date from OCR text.

    Strategy:
    1. Look for dates that appear within 80 chars of an expiry-context keyword.
    2. Fall back to the latest date found anywhere in the text.
    3. Ignore dates in the past by more than 20 years (noise) or
       implausibly far in the future (> 10 years).
    """
    today = date.today()
    earliest_plausible = date(today.year - 20, 1, 1)
    latest_plausible = date(today.year + 10, 12, 31)

    candidates: list[tuple[bool, date]] = []  # (near_expiry_keyword, date)

    for m in _RE_DATE.finditer(text):
        parsed = _parse_match(m)
        if parsed is None:
            continue
        if not (earliest_plausible <= parsed <= latest_plausible):
            continue

        # Check if within 80 chars of an expiry context keyword
        start = max(0, m.start() - 80)
        end = min(len(text), m.end() + 80)
        context_window = text[start:end]
        near_expiry = bool(_EXPIRY_CONTEXT.search(context_window))
        candidates.append((near_expiry, parsed))

    if not candidates:
        return None

    # Prefer dates near an expiry keyword; among those pick the latest
    expiry_candidates = [d for near, d in candidates if near]
    if expiry_candidates:
        return max(expiry_candidates)

    # No expiry-keyword context — return the latest date as best guess
    return max(d for _, d in candidates)


# ---------------------------------------------------------------------------
# OCR back-ends
# ---------------------------------------------------------------------------


async def _ocr_google_vision(image_bytes: bytes) -> str:
    """
    Extract text from image bytes using Google Cloud Vision TEXT_DETECTION.
    Requires GOOGLE_APPLICATION_CREDENTIALS to be set.
    """
    from google.cloud import vision  # type: ignore[import]

    client = vision.ImageAnnotatorClient()
    image = vision.Image(content=image_bytes)
    response = client.text_detection(image=image)

    if response.error.message:
        raise RuntimeError(
            f"Google Cloud Vision error: {response.error.message}"
        )

    texts = response.text_annotations
    return texts[0].description if texts else ""


def _ocr_tesseract(image_bytes: bytes) -> str:
    """
    Extract text from image bytes using Tesseract via pytesseract.
    Requires tesseract-ocr system package + pytesseract Python package.
    """
    import pytesseract  # type: ignore[import]
    from PIL import Image  # type: ignore[import]

    image = Image.open(io.BytesIO(image_bytes))
    # Page-segmentation mode 6: assume uniform block of text (works well for
    # structured cards that have clear text blocks)
    return pytesseract.image_to_string(image, config="--psm 6")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def check_compliance_card(image_url: str) -> dict[str, Any]:
    """
    Download an image from *image_url*, run OCR, and return a compliance
    result JSON object.

    Parameters
    ----------
    image_url : str
        Publicly accessible URL of the card image (e.g. Twilio MediaUrl).

    Returns
    -------
    dict with keys:
        is_compliant  bool   – True if a recognised card type is found and
                               not expired.
        card_type     str    – "CSCS", "NRSWA", or "UNKNOWN"
        expiry_date   str    – ISO-8601 date string or null
        is_expired    bool   – True if expiry_date < today
        raw_text      str    – Full OCR output (useful for debugging)
        confidence    str    – "HIGH", "MEDIUM", or "LOW"
    """
    # ------------------------------------------------------------------
    # Step 1: Fetch image bytes
    # ------------------------------------------------------------------
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(image_url)
            resp.raise_for_status()
            image_bytes = resp.content
    except httpx.HTTPError as exc:
        logger.error("Failed to download card image from %s: %s", image_url, exc)
        return _error_result(f"Image download failed: {exc}")

    # ------------------------------------------------------------------
    # Step 2: OCR
    # ------------------------------------------------------------------
    raw_text = ""
    ocr_engine = "unknown"
    try:
        if _USE_GCP:
            raw_text = await _ocr_google_vision(image_bytes)
            ocr_engine = "google_vision"
        else:
            raw_text = _ocr_tesseract(image_bytes)
            ocr_engine = "tesseract"
    except Exception as exc:  # noqa: BLE001
        logger.error("OCR failed (%s): %s", ocr_engine, exc)
        # Last-resort: if GCP failed, try Tesseract
        if _USE_GCP:
            try:
                raw_text = _ocr_tesseract(image_bytes)
                ocr_engine = "tesseract_fallback"
            except Exception as inner:  # noqa: BLE001
                logger.error("Tesseract fallback also failed: %s", inner)
                return _error_result(f"OCR failed: {exc}")
        else:
            return _error_result(f"OCR failed: {exc}")

    logger.debug("OCR engine used: %s | text length: %d", ocr_engine, len(raw_text))

    # ------------------------------------------------------------------
    # Step 3: Identify card type
    # ------------------------------------------------------------------
    found_cscs = bool(_RE_CSCS.search(raw_text))
    found_nrswa = bool(_RE_NRSWA.search(raw_text))

    if found_cscs and found_nrswa:
        # Prefer whichever keyword appears first (primary document type)
        cscs_pos = (_RE_CSCS.search(raw_text) or re.search("", "")).start()
        nrswa_pos = (_RE_NRSWA.search(raw_text) or re.search("", "")).start()
        card_type = "CSCS" if cscs_pos <= nrswa_pos else "NRSWA"
    elif found_cscs:
        card_type = "CSCS"
    elif found_nrswa:
        card_type = "NRSWA"
    else:
        card_type = "UNKNOWN"

    # ------------------------------------------------------------------
    # Step 4: Extract expiry date
    # ------------------------------------------------------------------
    expiry_date_obj = _extract_expiry_date(raw_text)
    expiry_date_str = expiry_date_obj.isoformat() if expiry_date_obj else None

    today = date.today()
    is_expired = (expiry_date_obj < today) if expiry_date_obj else True

    # ------------------------------------------------------------------
    # Step 5: Derive compliance and confidence
    # ------------------------------------------------------------------
    is_compliant = card_type != "UNKNOWN" and not is_expired

    if card_type != "UNKNOWN" and expiry_date_obj is not None:
        confidence = "HIGH"
    elif card_type != "UNKNOWN" or expiry_date_obj is not None:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    return {
        "is_compliant": is_compliant,
        "card_type": card_type,
        "expiry_date": expiry_date_str,
        "is_expired": is_expired,
        "raw_text": raw_text,
        "confidence": confidence,
        "ocr_engine": ocr_engine,
    }


def _error_result(detail: str) -> dict[str, Any]:
    return {
        "is_compliant": False,
        "card_type": "UNKNOWN",
        "expiry_date": None,
        "is_expired": True,
        "raw_text": "",
        "confidence": "LOW",
        "error": detail,
    }

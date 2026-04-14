"""
Module 1 — The Intake
======================
FastAPI router for the Twilio WhatsApp webhook.

Responsibilities
----------------
1. Validate every inbound Twilio request signature (HMAC-SHA1).
2. Parse the incoming WhatsApp message and any media attachments.
3. Route the message payload through the LLM orchestration layer
   (Gemini Bridge placeholder) to determine the next conversational step.
4. When the conversation flow requires it, prompt the candidate to upload
   their CSCS/NRSWA card photo by replying with a Twilio TwiML <Message>.

Environment variables required
--------------------------------
TWILIO_ACCOUNT_SID   – Twilio account SID
TWILIO_AUTH_TOKEN    – Used to verify the X-Twilio-Signature header
TWILIO_WHATSAPP_FROM – Your Twilio WhatsApp sender number, e.g. whatsapp:+14155238886
LLM_BRIDGE_URL       – Internal URL of the Gemini Bridge service
                       (default: http://gemini_bridge:8080)

Endpoints
---------
POST /api/whatsapp-webhook
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
from typing import Any

import httpx
from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import PlainTextResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["whatsapp"])

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TWILIO_AUTH_TOKEN: str = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_WHATSAPP_FROM: str = os.getenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")
LLM_BRIDGE_URL: str = os.getenv("LLM_BRIDGE_URL", "http://gemini_bridge:8080")

# Conversation state keys stored in Redis
_STATE_AWAITING_CSCS = "awaiting_cscs_photo"

# TwiML template — Twilio parses this XML to send a reply
_TWIML_RESPONSE = """\
<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Message from="{from_number}">{body}</Message>
</Response>"""

# ---------------------------------------------------------------------------
# Signature validation
# ---------------------------------------------------------------------------


def _verify_twilio_signature(
    auth_token: str,
    url: str,
    params: dict[str, str],
    signature: str,
) -> bool:
    """
    Validate the X-Twilio-Signature header per Twilio's HMAC-SHA1 scheme.

    https://www.twilio.com/docs/usage/webhooks/webhooks-security
    """
    # Build the validation string: URL + sorted POST params concatenated
    sorted_params = "".join(f"{k}{v}" for k, v in sorted(params.items()))
    validation_string = (url + sorted_params).encode("utf-8")

    computed = hmac.new(
        auth_token.encode("utf-8"),
        validation_string,
        hashlib.sha1,
    ).digest()

    expected = base64.b64encode(computed).decode("utf-8")
    return hmac.compare_digest(expected, signature)


# ---------------------------------------------------------------------------
# LLM orchestration (Gemini Bridge placeholder)
# ---------------------------------------------------------------------------


async def _orchestrate_with_llm(
    from_number: str,
    message_body: str,
    media_url: str | None,
    conversation_state: str | None,
) -> dict[str, Any]:
    """
    Send the inbound message to the Gemini Bridge and retrieve the next
    conversational action.

    Expected response schema from the LLM bridge:
    {
        "reply":          str,          # Text to send back to the candidate
        "next_state":     str | null,   # New conversation state key
        "request_photo":  bool,         # True → ask candidate for CSCS photo
        "compliance_data": {...} | null # Populated when bridge extracts card data
    }

    This function is intentionally thin — all business logic lives in the
    LLM bridge so prompts can be iterated without deploying new code.
    """
    payload = {
        "from": from_number,
        "message": message_body,
        "media_url": media_url,
        "conversation_state": conversation_state,
        "system_prompt": (
            "You are Lilieth, an AI assistant for a specialist infrastructure "
            "recruitment agency. You are speaking with a candidate via WhatsApp. "
            "Your goal is to verify their compliance documents (CSCS card, NRSWA "
            "certificate) and match them to available night-shift roles. "
            "Be professional, concise, and clear. Always address the candidate "
            "by first name when known."
        ),
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{LLM_BRIDGE_URL}/v1/chat",
                json=payload,
            )
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError as exc:
        logger.warning("LLM bridge unreachable (%s); using fallback flow.", exc)
        # Graceful degradation — return a safe default so Twilio always gets
        # a well-formed TwiML response even when the LLM is offline.
        return {
            "reply": (
                "Welcome to Lilieth Recruitment. "
                "To get started, please send a photo of your CSCS card."
            ),
            "next_state": _STATE_AWAITING_CSCS,
            "request_photo": True,
            "compliance_data": None,
        }


# ---------------------------------------------------------------------------
# Webhook endpoint
# ---------------------------------------------------------------------------


@router.post(
    "/whatsapp-webhook",
    response_class=PlainTextResponse,
    summary="Receive inbound WhatsApp messages from Twilio",
    status_code=status.HTTP_200_OK,
)
async def whatsapp_webhook(
    request: Request,
    x_twilio_signature: str | None = Header(None, alias="x-twilio-signature"),
) -> PlainTextResponse:
    """
    Entry point for all inbound WhatsApp messages delivered by Twilio.

    Flow
    ----
    1. Parse raw form data once (used for both signature verification and field extraction).
    2. Verify Twilio HMAC-SHA1 signature (reject 403 on failure).
    3. Read candidate's current conversation state from Redis.
    4. Forward message + state to the LLM orchestration layer.
    5. Persist updated state in Redis (TTL 24 h).
    6. Return TwiML reply — optionally asking for a CSCS card photo.
    """
    # ------------------------------------------------------------------
    # 1. Parse form data exactly once — used for both signature verification
    #    and field extraction, avoiding any double-read of the request body.
    # ------------------------------------------------------------------
    raw_form_data = await request.form()
    raw_form: dict[str, str] = {k: str(v) for k, v in raw_form_data.items()}

    From: str = raw_form.get("From", "")
    Body: str = raw_form.get("Body", "")
    num_media: int = int(raw_form.get("NumMedia", "0"))
    media_url_0: str | None = raw_form.get("MediaUrl0")

    # ------------------------------------------------------------------
    # 2. Signature verification
    # ------------------------------------------------------------------
    if TWILIO_AUTH_TOKEN:
        if not x_twilio_signature:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Missing X-Twilio-Signature header.",
            )

        # Reconstruct the full URL Twilio signed
        webhook_url = str(request.url)

        if not _verify_twilio_signature(
            TWILIO_AUTH_TOKEN,
            webhook_url,
            raw_form,
            x_twilio_signature,
        ):
            logger.warning(
                "Invalid Twilio signature from %s — rejecting request.", From
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid Twilio signature.",
            )
    else:
        logger.warning(
            "TWILIO_AUTH_TOKEN not configured — skipping signature verification. "
            "Set this variable in production."
        )

    # ------------------------------------------------------------------
    # 3. Retrieve conversation state from Redis
    # ------------------------------------------------------------------
    redis = request.app.state.redis
    state_key = f"whatsapp:state:{From}"
    conversation_state: str | None = await redis.get(state_key)

    # ------------------------------------------------------------------
    # 4. LLM orchestration
    # ------------------------------------------------------------------
    media_url = media_url_0 if num_media > 0 else None

    llm_result = await _orchestrate_with_llm(
        from_number=From,
        message_body=Body,
        media_url=media_url,
        conversation_state=conversation_state,
    )

    reply_text: str = llm_result.get("reply", "")
    next_state: str | None = llm_result.get("next_state")
    request_photo: bool = llm_result.get("request_photo", False)

    # ------------------------------------------------------------------
    # 5. Persist updated state in Redis (24 h TTL)
    # ------------------------------------------------------------------
    if next_state:
        await redis.setex(state_key, 86400, next_state)
    elif next_state is None and conversation_state:
        # LLM explicitly cleared the state
        await redis.delete(state_key)

    # ------------------------------------------------------------------
    # 6. Build TwiML response
    # ------------------------------------------------------------------
    if request_photo and _STATE_AWAITING_CSCS in (next_state or ""):
        # Append a clear photo-upload prompt to whatever the LLM replied
        if not reply_text.endswith((".", "!", "?")):
            reply_text += "."
        reply_text += (
            "\n\nPlease reply to this message with a *clear photo* of your "
            "CSCS card (both sides if possible). Make sure all text and the "
            "expiry date are visible. 📷"
        )

    twiml = _TWIML_RESPONSE.format(
        from_number=TWILIO_WHATSAPP_FROM,
        body=reply_text,
    )

    return PlainTextResponse(content=twiml, media_type="text/xml")

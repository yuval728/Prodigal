"""
extractor.py — LLM-powered structured field extraction.

Dedicated extraction layer, separate from response generation.
Only job: extract structured fields from messy natural language.
"""

import json
import logging
import os
import re

from openai import OpenAI

from .state import ExtractedFields, Stage, ConversationState

logger = logging.getLogger(__name__)

EXTRACTION_MODEL = os.getenv("EXTRACTION_MODEL", "gpt-4o-mini")

EXTRACTION_SYSTEM_PROMPT = """
You are a field extraction engine for a payment collection agent.
Your ONLY job is to extract structured data from user messages.

Output STRICTLY valid JSON with these possible fields (omit fields not found, do not include null values):
{
  "account_id": "ACC1001",
  "full_name": "Nithin Jain",
  "dob": "14 May 1990",
  "aadhaar_last4": "4321",
  "pincode": "400001",
  "amount": "500",
  "card_number": "4532015112830366",
  "cvv": "123",
  "expiry": "12/2027",
  "cardholder_name": "Nithin Jain"
}

RULES:
- Omit any field not clearly present in the message
- full_name: preserve EXACT casing as stated by the user
- card_number: strip all spaces/dashes, return digits only
- dob: return the RAW value as the user said it, do NOT normalize to ISO
- amount: return the raw phrase ("full amount", "500", "a thousand rupees")
- Do NOT hallucinate fields not present
- Do NOT infer — only extract what is explicitly stated
""".strip()


def extract_fields(user_input: str, stage: Stage, state: ConversationState) -> ExtractedFields:
    """
    Extract structured fields from free-form user input.
    Never raises — returns empty ExtractedFields on any failure.
    """
    prompt = _build_extraction_prompt(user_input, stage, state)

    try:
        client = OpenAI()
        response = client.chat.completions.create(
            model=EXTRACTION_MODEL,
            messages=[
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            response_format={"type": "json_object"},
            max_tokens=300,
        )
        raw = response.choices[0].message.content
        data = json.loads(raw)
        return _parse_extraction_response(data)

    except Exception as e:
        logger.warning("Field extraction failed", extra={"error": str(e), "stage": stage.name})
        return ExtractedFields()


def _parse_extraction_response(data: dict) -> ExtractedFields:
    """Map LLM JSON output to ExtractedFields. Handles None and type issues safely."""
    fields = ExtractedFields()

    if data.get("account_id"):
        fields.account_id = str(data["account_id"]).strip().upper()
    if data.get("full_name"):
        fields.full_name = str(data["full_name"]).strip()
    if data.get("dob"):
        fields.dob = str(data["dob"]).strip()
    if data.get("aadhaar_last4"):
        fields.aadhaar_last4 = str(data["aadhaar_last4"]).strip()
    if data.get("pincode"):
        fields.pincode = str(data["pincode"]).strip()
    if data.get("amount"):
        fields.amount = str(data["amount"]).strip()
    if data.get("card_number"):
        fields.card_number = re.sub(r"\D", "", str(data["card_number"]))
    if data.get("cvv"):
        fields.cvv = re.sub(r"\D", "", str(data["cvv"]))
    if data.get("expiry"):
        fields.expiry = str(data["expiry"]).strip()
    if data.get("cardholder_name"):
        fields.cardholder_name = str(data["cardholder_name"]).strip()

    return fields


def _build_extraction_prompt(user_input: str, stage: Stage, state: ConversationState) -> str:
    """Build stage-aware extraction prompt to reduce false positives."""
    balance_str = f"₹{state.account_data.balance:,.2f}" if state.account_data else "unknown"

    stage_hints = {
        Stage.GREETING: "Looking for an account ID (format: ACC + 4 digits).",
        Stage.ACCOUNT_LOOKUP: "Looking for an account ID.",
        Stage.IDENTITY_COLLECTION: (
            "Collecting: full name, date of birth, Aadhaar last 4 digits, or 6-digit pincode. "
            f"Already have — name:{'yes' if state.provided_name else 'no'}, "
            f"dob:{'yes' if state.provided_dob else 'no'}, "
            f"aadhaar:{'yes' if state.provided_aadhaar else 'no'}, "
            f"pincode:{'yes' if state.provided_pincode else 'no'}."
        ),
        Stage.BALANCE_DISCLOSURE: f"Collecting payment amount. Balance is {balance_str}.",
        Stage.CARD_COLLECTION: (
            "Collecting card details: number, CVV, expiry, cardholder name. "
            f"Already have — "
            f"card:{'yes' if state.card_details and state.card_details.card_number else 'no'}, "
            f"cvv:{'yes' if state.card_details and state.card_details.cvv else 'no'}, "
            f"expiry:{'yes' if state.card_details and state.card_details.expiry_month else 'no'}, "
            f"name:{'yes' if state.card_details and state.card_details.cardholder_name else 'no'}."
        ),
    }

    hint = stage_hints.get(stage, "Extract any relevant fields.")
    return f"Stage: {stage.name}\nContext: {hint}\nUser message: \"{user_input}\"\n\nExtract fields. JSON only."

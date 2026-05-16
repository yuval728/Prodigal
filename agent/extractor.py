"""
extractor.py — LLM-powered structured field extraction.

Dedicated extraction layer, separate from response generation.
Only job: extract structured fields from messy natural language.
"""

import logging
import os
import re

from .llm import chat_completion, get_message_content, parse_json_object

from .state import ExtractedFields, Stage, ConversationState

logger = logging.getLogger(__name__)

EXTRACTION_MODEL = os.getenv("EXTRACTION_MODEL", "groq/llama-3.1-8b-instant")

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

    extracted = ExtractedFields()

    try:
        response = chat_completion(
            model=EXTRACTION_MODEL,
            messages=[
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            response_format={"type": "json_object"},
            max_tokens=300,
        )
        raw = get_message_content(response)
        data = parse_json_object(raw)
        if not data:
            raise ValueError("JSON parse failed")
        extracted = _parse_extraction_response(data)
    except Exception as e:
        logger.warning("Field extraction failed", extra={"error": str(e), "stage": stage.name})
        try:
            response = chat_completion(
                model=EXTRACTION_MODEL,
                messages=[
                    {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
                max_tokens=300,
            )
            raw = get_message_content(response)
            data = parse_json_object(raw)
            if data:
                extracted = _parse_extraction_response(data)
        except Exception as retry_error:
            logger.warning(
                "Field extraction fallback failed",
                extra={"error": str(retry_error), "stage": stage.name},
            )

    fallback = _fallback_extract_fields(user_input, stage)
    return _merge_fields(extracted, fallback)


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


def _merge_fields(primary: ExtractedFields, fallback: ExtractedFields) -> ExtractedFields:
    """Fill empty fields from fallback extraction."""
    for field_name in primary.__dataclass_fields__.keys():
        if getattr(primary, field_name) is None and getattr(fallback, field_name) is not None:
            setattr(primary, field_name, getattr(fallback, field_name))
    return primary


def _fallback_extract_fields(user_input: str, stage: Stage) -> ExtractedFields:
    """Heuristic fallback extraction for common patterns."""
    fields = ExtractedFields()
    text = user_input.strip()
    lower = text.lower()

    account_match = re.search(r"acc\s*[-:]?\s*(\d{4})", text, re.IGNORECASE)
    if account_match:
        fields.account_id = f"ACC{account_match.group(1)}"

    if "aadhaar" in lower or "aadhar" in lower:
        aadhaar_match = re.search(r"\b\d{4}\b", lower)
        if aadhaar_match:
            fields.aadhaar_last4 = aadhaar_match.group(0)

    pincode_match = re.search(r"\b\d{6}\b", lower)
    if pincode_match:
        fields.pincode = pincode_match.group(0)
    else:
        spaced_pin = re.search(r"(\d\s){5}\d", lower)
        if spaced_pin:
            fields.pincode = spaced_pin.group(0).replace(" ", "")

    if "dob" in lower or "date of birth" in lower or "born" in lower:
        dob_match = re.search(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", lower)
        if dob_match:
            fields.dob = dob_match.group(0)
        else:
            month_match = re.search(
                r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+\d{1,2},?\s+\d{2,4}\b",
                lower,
            )
            if month_match:
                fields.dob = month_match.group(0)
            else:
                rev_month_match = re.search(
                    r"\b\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+\d{2,4}\b",
                    lower,
                )
                if rev_month_match:
                    fields.dob = rev_month_match.group(0)

    if any(phrase in lower for phrase in ["full amount", "clear the full", "pay all", "clear all", "total amount", "pay it all"]):
        fields.amount = "full amount"
    else:
        amount_match = re.search(r"\b\d+(?:\.\d+)?\b", lower)
        if amount_match and stage == Stage.BALANCE_DISCLOSURE:
            fields.amount = amount_match.group(0)

    card_digits = re.sub(r"\D", "", text)
    if len(card_digits) in (15, 16) and ("card" in lower or stage == Stage.CARD_COLLECTION):
        fields.card_number = card_digits

    if "cvv" in lower:
        cvv_match = re.search(r"\b\d{3,4}\b", lower)
        if cvv_match:
            fields.cvv = cvv_match.group(0)
        else:
            fields.cvv = _parse_spelled_digits(lower)

    if any(word in lower for word in ["exp", "expiry", "expires", "valid till", "valid through"]):
        exp_match = re.search(r"\b\d{1,2}[/-]\d{2,4}\b", lower)
        if exp_match:
            fields.expiry = exp_match.group(0)
        else:
            month_exp = re.search(
                r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+\d{2,4}\b",
                lower,
            )
            if month_exp:
                fields.expiry = month_exp.group(0)

    if stage == Stage.IDENTITY_COLLECTION:
        name_match = re.search(r"\bmy name is\s+(.+)$", text, re.IGNORECASE)
        if name_match:
            fields.full_name = name_match.group(1).strip()

    if "cardholder" in lower or "name on card" in lower:
        card_name_match = re.search(r"(?:cardholder(?: name)?|name on card)\s*(?:is)?\s*(.+)$", text, re.IGNORECASE)
        if card_name_match:
            fields.cardholder_name = card_name_match.group(1).strip()

    return fields


def _parse_spelled_digits(text: str) -> str | None:
    """Convert short digit words like 'one two three' into '123'."""
    mapping = {
        "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
        "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    }
    tokens = re.findall(r"zero|one|two|three|four|five|six|seven|eight|nine", text)
    if not tokens:
        return None
    digits = "".join(mapping[token] for token in tokens)
    if 3 <= len(digits) <= 4:
        return digits
    return None

"""LLM-powered response generation."""

import logging
import os

from .client import chat_completion, get_message_content
from .prompts import RESPONSE_SYSTEM_PROMPT
from ..domain.state import ConversationState

logger = logging.getLogger(__name__)

RESPONSE_MODEL = os.getenv("RESPONSE_MODEL", "groq/llama-3.1-70b-versatile")


def generate_response(state: ConversationState, situation: dict) -> str:
    state_summary = _build_state_summary(state)
    situation_str = _format_situation(situation)

    user_prompt = f"""
Current conversation state:
{state_summary}

Situation to handle:
{situation_str}

Recent conversation (last 6 turns):
{_format_history(state.history[-6:])}

Generate a single, clear agent response for this situation.
""".strip()

    try:
        response = chat_completion(
            model=RESPONSE_MODEL,
            messages=[
                {"role": "system", "content": RESPONSE_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=250,
        )
        return get_message_content(response).strip()

    except Exception as e:
        logger.error("Response generation failed", extra={"error": str(e)})
        return _fallback_response(situation)


def _build_state_summary(state: ConversationState) -> str:
    lines = [f"Stage: {state.stage.name}"]

    if state.account_id:
        lines.append(f"Account ID: {state.account_id}")
    if state.account_data:
        lines.append(f"Account holder: {state.account_data.full_name}")
        lines.append(f"Balance: ₹{state.account_data.balance:,.2f}")
    if state.verified:
        lines.append("Verification: PASSED")
    elif state.verification_attempts > 0:
        lines.append(f"Verification: FAILED ({state.verification_attempts} attempt(s))")

    collected = []
    if state.provided_name:
        collected.append("name")
    if state.provided_dob:
        collected.append("DOB")
    if state.provided_aadhaar:
        collected.append("Aadhaar last 4")
    if state.provided_pincode:
        collected.append("pincode")
    if collected:
        lines.append(f"Identity fields collected: {', '.join(collected)}")

    if state.payment_amount:
        lines.append(f"Payment amount: ₹{state.payment_amount:,.2f}")
    if state.card_details:
        lines.append("Card details: collected")
    if state.transaction_id:
        lines.append(f"Transaction ID: {state.transaction_id}")

    return "\n".join(lines)


def _format_situation(situation: dict) -> str:
    action = situation.get("action", "unknown")
    data = situation.get("data", {})
    context = situation.get("context", "")

    parts = [f"Action: {action}"]
    if data:
        parts.append(f"Data: {data}")
    if context:
        parts.append(f"Additional context: {context}")

    return "\n".join(parts)


def _format_history(history: list) -> str:
    if not history:
        return "(no history yet)"
    lines = []
    for turn in history:
        role = "User" if turn["role"] == "user" else "Agent"
        lines.append(f"{role}: {turn['content']}")
    return "\n".join(lines)


def _fallback_response(situation: dict) -> str:
    action = situation.get("action", "")
    fallbacks = {
        "greet": "Hello! I'm an automated payment assistant. Please share your account ID to get started.",
        "ask_account_id": "Could you please provide your account ID?",
        "ask_name": "Could you please confirm your full name?",
        "ask_secondary_factor": "Please provide your date of birth, Aadhaar last 4 digits, or pincode for verification.",
        "verify_success": "Your identity has been verified successfully.",
        "verify_fail": "I wasn't able to verify your identity with the details provided. Please try again.",
        "show_balance": f"Your outstanding balance is ₹{situation.get('data', {}).get('balance', 0):,.2f}. How much would you like to pay?",
        "ask_card_number": "Please provide your 16-digit card number.",
        "ask_cvv": "Please provide your card's CVV.",
        "ask_expiry": "Please provide your card's expiry date.",
        "ask_cardholder": "Please provide the cardholder name as it appears on your card.",
        "payment_success": f"Payment successful! Transaction ID: {situation.get('data', {}).get('transaction_id', 'N/A')}",
        "payment_fail": f"Payment failed: {situation.get('data', {}).get('error_message', 'Please try again.')}",
        "close": "Thank you. This session is now closed. Have a good day.",
        "error": "Something went wrong. Please try again.",
    }
    return fallbacks.get(action, "I'm sorry, something went wrong. Please try again.")

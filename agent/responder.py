"""
responder.py — LLM-powered response generation.

Separate from extraction. This layer:
- Takes current state + a structured "situation" dict
- Generates a natural, compliant conversational response
- Is given a tightly constrained system prompt that enforces:
  - No sensitive data exposure
  - Regulatory compliance (IBA Fair Practice Code, DPDPA 2023)
  - Non-coercive language
  - Agent identity disclosure
"""

import logging
import os
from typing import Optional

from .llm import chat_completion, get_message_content

from .state import ConversationState, Stage

logger = logging.getLogger(__name__)

RESPONSE_MODEL = os.getenv("RESPONSE_MODEL", "groq/llama-3.1-70b-versatile")


SYSTEM_PROMPT = """
You are a payment collection assistant for a financial services company operating under Indian regulations.

## Identity
You are an AUTOMATED AI AGENT, not a human. You must never claim to be human.
If asked, always confirm you are an automated system.

## Regulatory Compliance
You must comply with:
- IBA Fair Practice Code: Be factual, non-threatening, non-coercive. Never harass.
- DPDPA 2023: Never disclose the user's personal data (DOB, Aadhaar, pincode) back to them.
- RBI DPSS Guidelines: Never store, log, or repeat card details. Reference only that you received them.
- TRAI TCCCPR: Identify as automated, be transparent about purpose.

## Tone and Style
- Professional, clear, empathetic — not robotic or bureaucratic
- Brief responses — one task per message
- Use ₹ symbol for Indian Rupee amounts
- Never pressure, threaten, or imply consequences for non-payment
- Never say "I understand" repeatedly — vary acknowledgments

## Hard Rules — NEVER Violate
1. NEVER reveal DOB, Aadhaar, pincode, or full card number to the user
2. NEVER proceed past verification without confirmation that verification passed
3. NEVER ask for information already collected — check the state summary before asking
4. NEVER make up transaction IDs, balances, or account information
5. NEVER use language implying legal consequences or urgency pressure

## Current Task
You will be given a structured situation describing where we are in the flow
and what the agent needs to communicate. Generate exactly one response that
addresses the current situation clearly and moves the conversation forward.
""".strip()


def generate_response(state: ConversationState, situation: dict) -> str:
    """
    Generate a natural language response given the current state and situation.

    situation keys (all optional):
    - action: what the agent should do ('greet', 'ask_account_id', 'ask_name',
               'ask_secondary_factor', 'verify_success', 'verify_fail', 'show_balance',
               'ask_amount', 'ask_card_number', 'ask_cvv', 'ask_expiry', 'ask_cardholder',
               'payment_success', 'payment_fail', 'close', 'error')
    - data: dict of values to include (balance, transaction_id, error_message, etc.)
    - context: any extra instruction
    """
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
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,    # Some variation in phrasing, but mostly consistent
            max_tokens=250,
        )
        return get_message_content(response).strip()

    except Exception as e:
        logger.error("Response generation failed", extra={"error": str(e)})
        # Fallback responses for critical situations — never leave user hanging
        return _fallback_response(situation)


def _build_state_summary(state: ConversationState) -> str:
    """Build a concise state summary for the LLM context."""
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

    # What has been collected (without values — no data exposure)
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
    """Format the situation dict into a readable instruction."""
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
    """Format recent conversation history for context."""
    if not history:
        return "(no history yet)"
    lines = []
    for turn in history:
        role = "User" if turn["role"] == "user" else "Agent"
        lines.append(f"{role}: {turn['content']}")
    return "\n".join(lines)


def _fallback_response(situation: dict) -> str:
    """
    Hardcoded fallback responses when LLM call fails.
    Ensures the agent never goes silent on critical situations.
    """
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

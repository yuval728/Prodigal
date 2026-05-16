"""
agent.py — Payment Collection AI Agent

Architecture: LLM-orchestrated state machine
- Deterministic Python layer owns ALL state transitions and business logic
- LLM (extraction layer) parses natural language → structured fields
- LLM (response layer) generates natural language responses
- Zero business logic in LLM prompts — it can't bypass verification or skip stages

Flow:
  GREETING → ACCOUNT_LOOKUP → IDENTITY_COLLECTION →
  VERIFICATION → BALANCE_DISCLOSURE → CARD_COLLECTION →
  PAYMENT_PROCESSING → CLOSED

Regulatory compliance:
- DPDPA 2023: No PII echoed back to user, no session persistence
- RBI DPSS: Card data held in memory only, cleared post-API call
- IBA Fair Practice Code: Non-coercive language, agent identity disclosed
- Aadhaar Act: Last 4 digits used for verification only, not stored post-session
"""

import logging
import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

from .state import (
    ConversationState, Stage, AccountData, CardDetails,
    MAX_VERIFICATION_RETRIES, MAX_PAYMENT_RETRIES, MAX_CARD_RETRIES,
    ExtractedFields,
)
from .extractor import extract_fields
from .responder import generate_response
from .validators import (
    normalize_account_id,
    normalize_dob,
    normalize_aadhaar_last4,
    normalize_pincode,
    normalize_amount,
    normalize_card_number,
    normalize_cvv,
    normalize_expiry,
    verify_identity,
)
from .tools import (
    lookup_account, process_payment,
    AccountLookupSuccess, AccountLookupError,
    PaymentSuccess, PaymentError,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


class Agent:
    """
    Payment collection agent.

    Interface contract:
    - Instantiated once per conversation session
    - next(user_input: str) → {"message": str} called once per turn
    - All state maintained internally — no external setup between turns
    """

    def __init__(self):
        self._state = ConversationState()
        logger.info("Agent session started")

    def next(self, user_input: str) -> dict:
        """
        Process one turn of the conversation.

        Args:
            user_input: The user's raw message string.

        Returns:
            {"message": str} — the agent's response.
        """
        state = self._state

        # Terminal state — politely decline further input
        if state.is_closed():
            return {"message": "This session has already been closed. Please start a new session if you need further assistance."}

        # Record user turn
        state.add_turn("user", user_input)

        # --- Extract structured fields from the user message ---
        extracted = extract_fields(user_input, state.stage, state)
        state.last_extraction = extracted

        # Stash early card details for later use (do not act on them yet)
        self._stash_pending_card_fields(extracted)

        # --- Route to the appropriate stage handler ---
        response_message = self._route(user_input, extracted)

        # Record agent turn
        state.add_turn("assistant", response_message)

        logger.info("Turn complete", extra={"stage": state.stage.name, "message_preview": response_message[:80]})
        return {"message": response_message}

    # -------------------------------------------------------------------------
    # Stage router
    # -------------------------------------------------------------------------

    def _route(self, user_input: str, extracted: ExtractedFields) -> str:
        """Dispatch to the correct handler based on current stage."""
        stage = self._state.stage

        dispatch = {
            Stage.GREETING:            self._handle_greeting,
            Stage.ACCOUNT_LOOKUP:      self._handle_account_lookup,
            Stage.IDENTITY_COLLECTION: self._handle_identity_collection,
            Stage.VERIFICATION:        self._handle_verification,
            Stage.BALANCE_DISCLOSURE:  self._handle_balance_disclosure,
            Stage.CARD_COLLECTION:     self._handle_card_collection,
            Stage.PAYMENT_PROCESSING:  self._handle_payment_processing,
        }

        handler = dispatch.get(stage)
        if not handler:
            return self._close("internal_error")

        return handler(user_input, extracted)

    # -------------------------------------------------------------------------
    # Stage handlers
    # -------------------------------------------------------------------------

    def _handle_greeting(self, user_input: str, extracted: ExtractedFields) -> str:
        """
        GREETING → attempt to extract account ID.
        If found, jump straight to lookup. Otherwise ask for it.
        """
        state = self._state

        # User may volunteer account ID in first message
        if extracted.account_id:
            normalized = normalize_account_id(extracted.account_id)
            if normalized:
                state.account_id = normalized
                state.stage = Stage.ACCOUNT_LOOKUP
                return self._do_account_lookup()

        # No account ID found — greet and ask
        state.stage = Stage.ACCOUNT_LOOKUP
        return generate_response(state, {"action": "greet"})

    def _handle_account_lookup(self, user_input: str, extracted: ExtractedFields) -> str:
        """Expect account ID. Attempt lookup once we have it."""
        state = self._state

        # Try extracted first, then re-parse raw input
        account_id_raw = extracted.account_id or user_input
        normalized = normalize_account_id(account_id_raw)

        if not normalized:
            return generate_response(state, {
                "action": "ask_account_id",
                "context": "Could not find a valid account ID in the message. Account IDs follow the format ACC1001.",
            })

        state.account_id = normalized
        return self._do_account_lookup()

    def _do_account_lookup(self) -> str:
        """Execute the account lookup API call and handle the result."""
        state = self._state
        result = lookup_account(state.account_id)

        if isinstance(result, AccountLookupSuccess):
            # Store account data — used for verification (never exposed to user)
            state.account_data = AccountData(
                account_id=result.account_id,
                full_name=result.full_name,
                dob=result.dob,
                aadhaar_last4=result.aadhaar_last4,
                pincode=result.pincode,
                balance=result.balance,
            )
            state.stage = Stage.IDENTITY_COLLECTION
            return generate_response(state, {"action": "ask_name"})

        elif isinstance(result, AccountLookupError):
            if result.retryable:
                # Keep stage at ACCOUNT_LOOKUP — user can try a different ID
                return generate_response(state, {
                    "action": "error",
                    "data": {"error_message": result.message},
                    "context": "Ask the user to re-enter their account ID.",
                })
            else:
                # Terminal network failure
                return self._close("network_error")

    def _handle_identity_collection(self, user_input: str, extracted: ExtractedFields) -> str:
        """
        Collect name + at least one secondary factor.
        Accept out-of-order information — user may provide multiple fields at once.
        """
        state = self._state

        updated = False

        # --- Name ---
        if extracted.full_name and not state.provided_name:
            # Store exactly as extracted — verification is case-sensitive
            state.provided_name = extracted.full_name
            updated = True

        # --- Secondary factors ---
        if extracted.dob and not state.provided_dob:
            normalized_dob, dob_error = normalize_dob(extracted.dob)
            if normalized_dob:
                state.provided_dob = normalized_dob
                updated = True
            elif dob_error:
                return generate_response(state, {
                    "action": "error",
                    "data": {"error_message": dob_error},
                    "context": "The date of birth format was invalid. Ask user to provide again.",
                })

        if extracted.aadhaar_last4 and not state.provided_aadhaar:
            normalized_aadhaar = normalize_aadhaar_last4(extracted.aadhaar_last4)
            if normalized_aadhaar:
                state.provided_aadhaar = normalized_aadhaar
                updated = True

        if extracted.pincode and not state.provided_pincode:
            normalized_pincode = normalize_pincode(extracted.pincode)
            if normalized_pincode:
                state.provided_pincode = normalized_pincode
                updated = True

        # --- Decide next step ---
        has_name = bool(state.provided_name)
        has_secondary = state.secondary_factor_provided()

        if has_name and has_secondary:
            # All required fields collected — attempt verification
            state.stage = Stage.VERIFICATION
            return self._do_verification()

        elif has_name and not has_secondary:
            return generate_response(state, {"action": "ask_secondary_factor"})

        elif not has_name:
            return generate_response(state, {"action": "ask_name"})

        # Should not reach here
        return generate_response(state, {"action": "ask_name"})

    def _do_verification(self) -> str:
        """
        Run deterministic identity verification.
        Pure Python — no LLM involvement in the actual comparison.
        """
        state = self._state
        state.verification_attempts += 1

        is_verified, reason = verify_identity(
            account_data=state.account_data,
            provided_name=state.provided_name,
            provided_dob=state.provided_dob,
            provided_aadhaar=state.provided_aadhaar,
            provided_pincode=state.provided_pincode,
        )

        if is_verified:
            state.verified = True
            state.stage = Stage.BALANCE_DISCLOSURE
            return generate_response(state, {
                "action": "verify_success",
                "data": {"balance": state.account_data.balance},
            })

        # Verification failed
        attempts_remaining = MAX_VERIFICATION_RETRIES - state.verification_attempts

        if attempts_remaining <= 0:
            logger.warning("Verification failed — max retries exceeded", extra={"account_id": state.account_id})
            return self._close("verification_exhausted")

        # Reset collected fields and go back to collection
        # Keep name if it matched — only secondary factor failed
        name_matched = (reason != "name_mismatch")
        if not name_matched:
            state.provided_name = None

        state.provided_dob = None
        state.provided_aadhaar = None
        state.provided_pincode = None
        state.stage = Stage.IDENTITY_COLLECTION

        context_map = {
            "name_mismatch": f"The name did not match. Ask user to re-enter their full name exactly as registered. {attempts_remaining} attempt(s) remaining.",
            "secondary_factor_mismatch": f"The secondary verification factor did not match. Ask user to try a different factor (DOB, Aadhaar last 4, or pincode). {attempts_remaining} attempt(s) remaining.",
            "no_secondary_factor": "Name was accepted but no secondary factor was provided. Ask for DOB, Aadhaar last 4, or pincode.",
        }

        context = context_map.get(reason, f"Verification failed. {attempts_remaining} attempt(s) remaining.")

        return generate_response(state, {
            "action": "verify_fail",
            "data": {"reason": reason, "attempts_remaining": attempts_remaining},
            "context": context,
        })

    def _handle_verification(self, user_input: str, extracted: ExtractedFields) -> str:
        """Shouldn't normally receive input in VERIFICATION stage (it's internal)."""
        return self._handle_identity_collection(user_input, extracted)

    def _handle_balance_disclosure(self, user_input: str, extracted: ExtractedFields) -> str:
        """
        User is verified. Show balance and collect payment amount.
        First turn in this stage: show balance and ask for amount.
        Subsequent turns: parse the amount.
        """
        state = self._state

        # First visit — no amount yet — just show balance and ask
        if state.payment_amount is None and not extracted.amount:
            return generate_response(state, {
                "action": "show_balance",
                "data": {"balance": state.account_data.balance},
            })

        # Try to parse amount
        if extracted.amount:
            amount_raw = extracted.amount
        else:
            amount_raw = user_input

        # Handle zero balance edge case
        if state.account_data.balance == 0:
            state.stage = Stage.CLOSED
            return generate_response(state, {
                "action": "close",
                "context": "Account has zero balance. No payment needed. Close conversation gracefully.",
            })

        parsed_amount, amount_error = normalize_amount(amount_raw, state.account_data.balance)

        if amount_error:
            return generate_response(state, {
                "action": "error",
                "data": {"error_message": amount_error},
                "context": f"Ask user for a valid payment amount. Balance is ₹{state.account_data.balance:,.2f}.",
            })

        state.payment_amount = parsed_amount
        state.stage = Stage.CARD_COLLECTION
        return generate_response(state, {
            "action": "ask_card_number",
            "data": {"amount": parsed_amount},
        })

    def _handle_card_collection(self, user_input: str, extracted: ExtractedFields) -> str:
        """
        Collect card number, CVV, expiry, cardholder name.
        Accept fields in any order. Move to payment once all 4 are present.
        """
        state = self._state

        extracted = self._merge_pending_card_fields(extracted)

        # Initialize card details container if needed
        if state.card_details is None:
            state.card_details = CardDetails(
                cardholder_name="", card_number="", cvv="",
                expiry_month=0, expiry_year=0,
            )

        card = state.card_details
        errors = []

        # --- Card number ---
        if extracted.card_number and not card.card_number:
            card_num, card_error = normalize_card_number(extracted.card_number)
            if card_num:
                card.card_number = card_num
                state.card_attempts = 0
            elif card_error:
                errors.append(card_error)

        # --- CVV ---
        if extracted.cvv and not card.cvv:
            cvv, cvv_error = normalize_cvv(extracted.cvv, card.card_number or None)
            if cvv:
                card.cvv = cvv
            elif cvv_error:
                errors.append(cvv_error)

        # --- Expiry ---
        if extracted.expiry and not card.expiry_month:
            expiry_result, expiry_error = normalize_expiry(extracted.expiry)
            if expiry_result:
                card.expiry_month, card.expiry_year = expiry_result
            elif expiry_error:
                errors.append(expiry_error)

        # --- Cardholder name ---
        if extracted.cardholder_name and not card.cardholder_name:
            card.cardholder_name = extracted.cardholder_name
        elif extracted.full_name and not card.cardholder_name:
            # User may provide full name again when asked for cardholder name
            card.cardholder_name = extracted.full_name

        # Check card attempt limit
        if state.card_attempts >= MAX_CARD_RETRIES:
            return self._close("card_retry_exhausted")

        # If there were validation errors, surface the first one
        if errors:
            state.card_attempts += 1
            if state.card_attempts >= MAX_CARD_RETRIES:
                return self._close("card_retry_exhausted")
            return generate_response(state, {
                "action": "error",
                "data": {"error_message": errors[0]},
                "context": "Ask user to correct the indicated card field.",
            })

        # --- Check which fields are still missing ---
        missing = self._missing_card_fields(card)

        if not missing:
            # All card fields collected — proceed to payment
            state.stage = Stage.PAYMENT_PROCESSING
            return self._do_payment()

        # Ask for the next missing field
        action_map = {
            "card_number": "ask_card_number",
            "cvv": "ask_cvv",
            "expiry": "ask_expiry",
            "cardholder_name": "ask_cardholder",
        }
        next_field = missing[0]
        return generate_response(state, {"action": action_map[next_field]})

    def _missing_card_fields(self, card: CardDetails) -> list:
        """Return ordered list of missing card fields."""
        missing = []
        if not card.card_number:
            missing.append("card_number")
        if not card.cvv:
            missing.append("cvv")
        if not card.expiry_month:
            missing.append("expiry")
        if not card.cardholder_name:
            missing.append("cardholder_name")
        return missing

    def _do_payment(self) -> str:
        """Execute the payment API call and handle the result."""
        state = self._state
        card = state.card_details

        result = process_payment(
            account_id=state.account_id,
            amount=state.payment_amount,
            cardholder_name=card.cardholder_name,
            card_number=card.card_number,
            cvv=card.cvv,
            expiry_month=card.expiry_month,
            expiry_year=card.expiry_year,
        )

        # Clear card data immediately — regulatory requirement
        state.clear_all_card_data()

        if isinstance(result, PaymentSuccess):
            state.transaction_id = result.transaction_id
            state.stage = Stage.CLOSED
            return generate_response(state, {
                "action": "payment_success",
                "data": {
                    "transaction_id": result.transaction_id,
                    "amount": state.payment_amount,
                    "balance_after": state.account_data.balance - state.payment_amount,
                },
            })

        elif isinstance(result, PaymentError):
            state.payment_attempts += 1
            state.last_payment_error = result.error_code

            if not result.retryable or state.payment_attempts >= MAX_PAYMENT_RETRIES:
                return self._close("payment_failed", error_message=result.message)

            # Retryable error — go back to card collection
            state.stage = Stage.CARD_COLLECTION
            state.card_details = None

            return generate_response(state, {
                "action": "payment_fail",
                "data": {"error_message": result.message},
                "context": "Error is correctable. Ask user to re-enter their card details.",
            })

    def _handle_payment_processing(self, user_input: str, extracted: ExtractedFields) -> str:
        """Shouldn't normally receive input in PAYMENT_PROCESSING stage."""
        return self._do_payment()

    # -------------------------------------------------------------------------
    # Close / terminal state
    # -------------------------------------------------------------------------

    def _close(self, reason: str, error_message: Optional[str] = None) -> str:
        """
        Move to CLOSED stage and generate a closing message.
        Different reasons generate different closing messages.
        """
        state = self._state
        state.stage = Stage.CLOSED
        state.clear_all_card_data()

        close_contexts = {
            "verification_exhausted": (
                "Identity verification failed after maximum attempts. "
                "Close the session and inform user they may contact support if needed. "
                "Do NOT reveal what the correct values should be."
            ),
            "network_error": (
                "A network error prevented completion. "
                "Apologize and ask user to try again later or contact support."
            ),
            "card_retry_exhausted": (
                "Card details failed validation too many times. "
                "Suggest user verify their card details and try again in a new session."
            ),
            "payment_failed": (
                f"Payment could not be completed: {error_message or 'unknown error'}. "
                "Close gracefully and provide support contact guidance."
            ),
            "internal_error": (
                "An unexpected internal error occurred. "
                "Apologize briefly and ask user to try again."
            ),
        }

        context = close_contexts.get(reason, "Close the conversation.")

        return generate_response(state, {
            "action": "close",
            "data": {"reason": reason, "error_message": error_message},
            "context": context,
        })

    # ---------------------------------------------------------------------
    # Card pre-collection helpers
    # ---------------------------------------------------------------------

    def _stash_pending_card_fields(self, extracted: ExtractedFields) -> None:
        """Store card fields provided before CARD_COLLECTION stage."""
        state = self._state
        if state.stage in (Stage.CARD_COLLECTION, Stage.PAYMENT_PROCESSING, Stage.CLOSED):
            return

        if extracted.card_number and not state.pending_card_number:
            state.pending_card_number = extracted.card_number
        if extracted.cvv and not state.pending_cvv:
            state.pending_cvv = extracted.cvv
        if extracted.expiry and not state.pending_expiry:
            state.pending_expiry = extracted.expiry
        if extracted.cardholder_name and not state.pending_cardholder_name:
            state.pending_cardholder_name = extracted.cardholder_name

    def _merge_pending_card_fields(self, extracted: ExtractedFields) -> ExtractedFields:
        """Merge any pending card fields into the current extraction."""
        state = self._state

        if state.pending_card_number and not extracted.card_number:
            extracted.card_number = state.pending_card_number
        if state.pending_cvv and not extracted.cvv:
            extracted.cvv = state.pending_cvv
        if state.pending_expiry and not extracted.expiry:
            extracted.expiry = state.pending_expiry
        if state.pending_cardholder_name and not extracted.cardholder_name:
            extracted.cardholder_name = state.pending_cardholder_name

        state.clear_pending_card_details()
        return extracted

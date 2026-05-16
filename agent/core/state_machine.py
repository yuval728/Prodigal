"""State machine orchestration for the payment flow."""

import logging

from ..api.client import lookup_account, process_payment
from ..api.models import AccountLookupSuccess, AccountLookupError, PaymentSuccess, PaymentError
from ..domain.models import AccountData, CardDetails, ExtractedFields
from ..domain.stage import Stage
from ..domain.state import (
    ConversationState,
    MAX_VERIFICATION_RETRIES,
    MAX_PAYMENT_RETRIES,
    MAX_CARD_RETRIES,
)
from ..llm.extractor import extract_fields
from ..llm.responder import generate_response
from ..validation.normalizers import (
    normalize_account_id,
    normalize_dob,
    normalize_aadhaar_last4,
    normalize_pincode,
    normalize_amount,
    normalize_card_number,
    normalize_cvv,
    normalize_expiry,
)
from ..validation.validators import verify_identity

logger = logging.getLogger(__name__)


class PaymentStateMachine:
    def __init__(self, state: ConversationState) -> None:
        self._state = state

    def handle_turn(self, user_input: str) -> str:
        state = self._state

        if state.is_closed():
            return "This session has already been closed. Please start a new session if you need further assistance."

        state.add_turn("user", user_input)

        extracted = extract_fields(user_input, state.stage, state)
        state.last_extraction = extracted

        self._stash_pending_card_fields(extracted)

        response_message = self._route(user_input, extracted)

        state.add_turn("assistant", response_message)
        logger.info("Turn complete", extra={"stage": state.stage.name, "message_preview": response_message[:80]})
        return response_message

    def _route(self, user_input: str, extracted: ExtractedFields) -> str:
        stage = self._state.stage

        dispatch = {
            Stage.GREETING: self._handle_greeting,
            Stage.ACCOUNT_LOOKUP: self._handle_account_lookup,
            Stage.IDENTITY_COLLECTION: self._handle_identity_collection,
            Stage.VERIFICATION: self._handle_verification,
            Stage.BALANCE_DISCLOSURE: self._handle_balance_disclosure,
            Stage.CARD_COLLECTION: self._handle_card_collection,
            Stage.PAYMENT_PROCESSING: self._handle_payment_processing,
        }

        handler = dispatch.get(stage)
        if not handler:
            return self._close("internal_error")

        return handler(user_input, extracted)

    def _handle_greeting(self, user_input: str, extracted: ExtractedFields) -> str:
        state = self._state

        if extracted.account_id:
            normalized = normalize_account_id(extracted.account_id)
            if normalized:
                state.account_id = normalized
                state.stage = Stage.ACCOUNT_LOOKUP
                return self._do_account_lookup()

        state.stage = Stage.ACCOUNT_LOOKUP
        return generate_response(state, {"action": "greet"})

    def _handle_account_lookup(self, user_input: str, extracted: ExtractedFields) -> str:
        state = self._state

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
        state = self._state
        result = lookup_account(state.account_id)

        if isinstance(result, AccountLookupSuccess):
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

        if isinstance(result, AccountLookupError):
            if result.retryable:
                return generate_response(state, {
                    "action": "error",
                    "data": {"error_message": result.message},
                    "context": "Ask the user to re-enter their account ID.",
                })
            return self._close("network_error")

        return self._close("internal_error")

    def _handle_identity_collection(self, user_input: str, extracted: ExtractedFields) -> str:
        state = self._state

        if extracted.full_name and not state.provided_name:
            state.provided_name = extracted.full_name

        if extracted.dob and not state.provided_dob:
            normalized_dob, dob_error = normalize_dob(extracted.dob)
            if normalized_dob:
                state.provided_dob = normalized_dob
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

        if extracted.pincode and not state.provided_pincode:
            normalized_pincode = normalize_pincode(extracted.pincode)
            if normalized_pincode:
                state.provided_pincode = normalized_pincode

        has_name = bool(state.provided_name)
        has_secondary = state.secondary_factor_provided()

        if has_name and has_secondary:
            state.stage = Stage.VERIFICATION
            return self._do_verification()

        if has_name and not has_secondary:
            return generate_response(state, {"action": "ask_secondary_factor"})

        return generate_response(state, {"action": "ask_name"})

    def _do_verification(self) -> str:
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

        attempts_remaining = MAX_VERIFICATION_RETRIES - state.verification_attempts
        if attempts_remaining <= 0:
            logger.warning("Verification failed — max retries exceeded", extra={"account_id": state.account_id})
            return self._close("verification_exhausted")

        name_matched = (reason != "name_mismatch")
        if not name_matched:
            state.provided_name = None

        state.provided_dob = None
        state.provided_aadhaar = None
        state.provided_pincode = None
        state.stage = Stage.IDENTITY_COLLECTION

        context_map = {
            "name_mismatch": (
                "The name did not match. Ask user to re-enter their full name exactly as registered. "
                f"{attempts_remaining} attempt(s) remaining."
            ),
            "secondary_factor_mismatch": (
                "The secondary verification factor did not match. Ask user to try a different factor "
                f"(DOB, Aadhaar last 4, or pincode). {attempts_remaining} attempt(s) remaining."
            ),
            "no_secondary_factor": "Name was accepted but no secondary factor was provided. Ask for DOB, Aadhaar last 4, or pincode.",
        }

        context = context_map.get(reason, f"Verification failed. {attempts_remaining} attempt(s) remaining.")

        return generate_response(state, {
            "action": "verify_fail",
            "data": {"reason": reason, "attempts_remaining": attempts_remaining},
            "context": context,
        })

    def _handle_verification(self, user_input: str, extracted: ExtractedFields) -> str:
        return self._handle_identity_collection(user_input, extracted)

    def _handle_balance_disclosure(self, user_input: str, extracted: ExtractedFields) -> str:
        state = self._state

        if state.payment_amount is None and not extracted.amount:
            return generate_response(state, {
                "action": "show_balance",
                "data": {"balance": state.account_data.balance},
            })

        amount_raw = extracted.amount or user_input

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
        state = self._state

        extracted = self._merge_pending_card_fields(extracted)

        if state.card_details is None:
            state.card_details = CardDetails(
                cardholder_name="",
                card_number="",
                cvv="",
                expiry_month=0,
                expiry_year=0,
            )

        card = state.card_details
        errors = []

        if extracted.card_number and not card.card_number:
            card_num, card_error = normalize_card_number(extracted.card_number)
            if card_num:
                card.card_number = card_num
                state.card_attempts = 0
            elif card_error:
                errors.append(card_error)

        if extracted.cvv and not card.cvv:
            cvv, cvv_error = normalize_cvv(extracted.cvv, card.card_number or None)
            if cvv:
                card.cvv = cvv
            elif cvv_error:
                errors.append(cvv_error)

        if extracted.expiry and not card.expiry_month:
            expiry_result, expiry_error = normalize_expiry(extracted.expiry)
            if expiry_result:
                card.expiry_month, card.expiry_year = expiry_result
            elif expiry_error:
                errors.append(expiry_error)

        if extracted.cardholder_name and not card.cardholder_name:
            card.cardholder_name = extracted.cardholder_name
        elif extracted.full_name and not card.cardholder_name:
            card.cardholder_name = extracted.full_name

        if state.card_attempts >= MAX_CARD_RETRIES:
            return self._close("card_retry_exhausted")

        if errors:
            state.card_attempts += 1
            if state.card_attempts >= MAX_CARD_RETRIES:
                return self._close("card_retry_exhausted")
            return generate_response(state, {
                "action": "error",
                "data": {"error_message": errors[0]},
                "context": "Ask user to correct the indicated card field.",
            })

        missing = self._missing_card_fields(card)
        if not missing:
            state.stage = Stage.PAYMENT_PROCESSING
            return self._do_payment()

        action_map = {
            "card_number": "ask_card_number",
            "cvv": "ask_cvv",
            "expiry": "ask_expiry",
            "cardholder_name": "ask_cardholder",
        }
        next_field = missing[0]
        return generate_response(state, {"action": action_map[next_field]})

    def _missing_card_fields(self, card: CardDetails) -> list:
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

        if isinstance(result, PaymentError):
            state.payment_attempts += 1
            state.last_payment_error = result.error_code

            if not result.retryable or state.payment_attempts >= MAX_PAYMENT_RETRIES:
                return self._close("payment_failed", error_message=result.message)

            state.stage = Stage.CARD_COLLECTION
            state.card_details = None

            return generate_response(state, {
                "action": "payment_fail",
                "data": {"error_message": result.message},
                "context": "Error is correctable. Ask user to re-enter their card details.",
            })

        return self._close("payment_failed", error_message="unknown error")

    def _handle_payment_processing(self, user_input: str, extracted: ExtractedFields) -> str:
        return self._do_payment()

    def _close(self, reason: str, error_message: str | None = None) -> str:
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

    def _stash_pending_card_fields(self, extracted: ExtractedFields) -> None:
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

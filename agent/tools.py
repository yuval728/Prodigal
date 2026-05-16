"""
tools.py — API integration layer.

Wraps the external payment API with:
- Typed request/response models
- Retry logic with exponential backoff (network errors only, not 4xx)
- Explicit error classification (retryable vs terminal)
- Timeout on every request — no open-ended waits
- Zero raw card data in logs

Design: errors are returned as typed results, never raised.
The agent layer decides how to respond to each error type.
"""

import time
import logging
from dataclasses import dataclass
from typing import Optional
import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://se-payment-verification-api.service.external.usea2.aws.prodigaltech.com"
REQUEST_TIMEOUT = 10.0     # seconds — never let an API call hang indefinitely
MAX_RETRIES     = 3
BACKOFF_BASE    = 1.5      # seconds; wait = BACKOFF_BASE ^ attempt


# ---------------------------------------------------------------------------
# Result types — typed unions instead of exceptions
# ---------------------------------------------------------------------------

@dataclass
class AccountLookupSuccess:
    account_id:    str
    full_name:     str
    dob:           str
    aadhaar_last4: str
    pincode:       str
    balance:       float


@dataclass
class AccountLookupError:
    error_code: str      # 'account_not_found' | 'network_error' | 'unexpected_error'
    message:    str
    retryable:  bool


@dataclass
class PaymentSuccess:
    transaction_id: str


@dataclass
class PaymentError:
    error_code: str
    message:    str
    retryable:  bool     # True = user can fix and retry; False = terminal


# Error codes from the API and whether the user can retry
PAYMENT_ERROR_MAP = {
    "insufficient_balance": (
        "The payment amount exceeds your outstanding balance.",
        False  # Balance won't change; terminal
    ),
    "invalid_amount": (
        "The payment amount is invalid (must be positive with at most 2 decimal places).",
        True
    ),
    "invalid_card": (
        "The card number is invalid. Please check and re-enter your card details.",
        True
    ),
    "invalid_cvv": (
        "The CVV is incorrect. Please check the security code on the back of your card.",
        True
    ),
    "invalid_expiry": (
        "The card expiry date is invalid or the card has expired.",
        True
    ),
}


# ---------------------------------------------------------------------------
# HTTP client factory
# ---------------------------------------------------------------------------

def _get_client() -> httpx.Client:
    """Return a configured httpx client. Called per-request (stateless)."""
    return httpx.Client(
        base_url=BASE_URL,
        timeout=REQUEST_TIMEOUT,
        headers={"Content-Type": "application/json"},
    )


# ---------------------------------------------------------------------------
# Account lookup
# ---------------------------------------------------------------------------

def lookup_account(account_id: str) -> AccountLookupSuccess | AccountLookupError:
    """
    POST /api/lookup-account

    Retries on network errors (connection, timeout) with exponential backoff.
    Does NOT retry on 404 (account not found) — that's a definitive answer.
    """
    payload = {"account_id": account_id}

    for attempt in range(MAX_RETRIES):
        try:
            with _get_client() as client:
                response = client.post("/api/lookup-account", json=payload)

            if response.status_code == 200:
                data = response.json()
                return AccountLookupSuccess(
                    account_id=data["account_id"],
                    full_name=data["full_name"],
                    dob=data["dob"],
                    aadhaar_last4=data["aadhaar_last4"],
                    pincode=data["pincode"],
                    balance=float(data["balance"]),
                )

            elif response.status_code == 404:
                # Definitive: account does not exist, no point retrying
                return AccountLookupError(
                    error_code="account_not_found",
                    message="No account found with that ID. Please double-check your account number.",
                    retryable=True,  # User can provide a different account ID
                )

            else:
                # Unexpected HTTP status — log and retry
                logger.warning(
                    "lookup_account unexpected status",
                    extra={"status": response.status_code, "attempt": attempt}
                )
                if attempt < MAX_RETRIES - 1:
                    time.sleep(BACKOFF_BASE ** attempt)
                    continue

                return AccountLookupError(
                    error_code="unexpected_error",
                    message="We're having trouble reaching our systems. Please try again in a moment.",
                    retryable=True,
                )

        except (httpx.ConnectError, httpx.TimeoutException) as e:
            logger.warning("lookup_account network error", extra={"attempt": attempt, "error": str(e)})
            if attempt < MAX_RETRIES - 1:
                time.sleep(BACKOFF_BASE ** attempt)
                continue

            return AccountLookupError(
                error_code="network_error",
                message="We're unable to connect to our systems right now. Please try again in a moment.",
                retryable=True,
            )

        except Exception as e:
            logger.error("lookup_account unexpected exception", extra={"error": str(e)})
            return AccountLookupError(
                error_code="unexpected_error",
                message="An unexpected error occurred. Please try again.",
                retryable=True,
            )

    # Exhausted retries
    return AccountLookupError(
        error_code="network_error",
        message="We're having trouble connecting after multiple attempts. Please try again later.",
        retryable=False,
    )


# ---------------------------------------------------------------------------
# Payment processing
# ---------------------------------------------------------------------------

def process_payment(
    account_id: str,
    amount: float,
    cardholder_name: str,
    card_number: str,
    cvv: str,
    expiry_month: int,
    expiry_year: int,
) -> PaymentSuccess | PaymentError:
    """
    POST /api/process-payment

    Card data is passed as function arguments — never stored in a file,
    never written to logs. The payload dict is constructed inline and
    dereferenced after the call. (PCI-DSS adjacency, RBI DPSS guidelines.)

    Retries only on network errors. Payment API errors (4xx/422) are
    not retried automatically — user must correct the input.
    """
    payload = {
        "account_id": account_id,
        "amount": amount,
        "payment_method": {
            "type": "card",
            "card": {
                "cardholder_name": cardholder_name,
                "card_number": card_number,
                "cvv": cvv,
                "expiry_month": expiry_month,
                "expiry_year": expiry_year,
            },
        },
    }

    # NOTE: We intentionally do NOT log the payload — it contains card data.
    logger.info("process_payment initiated", extra={"account_id": account_id, "amount": amount})

    for attempt in range(MAX_RETRIES):
        try:
            with _get_client() as client:
                response = client.post("/api/process-payment", json=payload)

            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    logger.info(
                        "process_payment success",
                        extra={"account_id": account_id, "transaction_id": data["transaction_id"]}
                    )
                    return PaymentSuccess(transaction_id=data["transaction_id"])

            # 422 or success=False — map to typed error
            try:
                error_data = response.json()
                error_code = error_data.get("error_code", "unknown_error")
            except Exception:
                error_code = "unknown_error"

            user_message, retryable = PAYMENT_ERROR_MAP.get(
                error_code,
                ("Payment could not be processed. Please try again or contact support.", False)
            )

            logger.warning(
                "process_payment failed",
                extra={"account_id": account_id, "error_code": error_code}
            )
            return PaymentError(error_code=error_code, message=user_message, retryable=retryable)

        except (httpx.ConnectError, httpx.TimeoutException) as e:
            logger.warning("process_payment network error", extra={"attempt": attempt, "error": str(e)})
            if attempt < MAX_RETRIES - 1:
                time.sleep(BACKOFF_BASE ** attempt)
                continue

            return PaymentError(
                error_code="network_error",
                message="We couldn't reach the payment system. Please try again.",
                retryable=True,
            )

        except Exception as e:
            logger.error("process_payment unexpected exception", extra={"error": str(e)})
            return PaymentError(
                error_code="unexpected_error",
                message="An unexpected error occurred during payment. Please try again.",
                retryable=True,
            )

    return PaymentError(
        error_code="network_error",
        message="Payment system is unreachable after multiple attempts. Please try again later.",
        retryable=False,
    )

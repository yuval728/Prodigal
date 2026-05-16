"""HTTP client for the payment API."""

import logging
import os
import time

import httpx

from .models import AccountLookupSuccess, AccountLookupError, PaymentSuccess, PaymentError

logger = logging.getLogger(__name__)

BASE_URL = os.getenv("PAYMENT_API_BASE_URL", "https://se-payment-verification-api.service.external.usea2.aws.prodigaltech.com")
REQUEST_TIMEOUT = 10.0  # seconds
MAX_RETRIES = 3
BACKOFF_BASE = 1.5

PAYMENT_ERROR_MAP = {
    "insufficient_balance": (
        "The payment amount exceeds your outstanding balance.",
        False,
    ),
    "invalid_amount": (
        "The payment amount is invalid (must be positive with at most 2 decimal places).",
        True,
    ),
    "invalid_card": (
        "The card number is invalid. Please check and re-enter your card details.",
        True,
    ),
    "invalid_cvv": (
        "The CVV is incorrect. Please check the security code on the back of your card.",
        True,
    ),
    "invalid_expiry": (
        "The card expiry date is invalid or the card has expired.",
        True,
    ),
}


def _get_client() -> httpx.Client:
    return httpx.Client(
        base_url=BASE_URL,
        timeout=REQUEST_TIMEOUT,
        headers={"Content-Type": "application/json"},
    )


def lookup_account(account_id: str) -> AccountLookupSuccess | AccountLookupError:
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

            if response.status_code == 404:
                return AccountLookupError(
                    error_code="account_not_found",
                    message="No account found with that ID. Please double-check your account number.",
                    retryable=True,
                )

            logger.warning(
                "lookup_account unexpected status",
                extra={"status": response.status_code, "attempt": attempt},
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

    return AccountLookupError(
        error_code="network_error",
        message="We're having trouble connecting after multiple attempts. Please try again later.",
        retryable=False,
    )


def process_payment(
    account_id: str,
    amount: float,
    cardholder_name: str,
    card_number: str,
    cvv: str,
    expiry_month: int,
    expiry_year: int,
) -> PaymentSuccess | PaymentError:
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
                        extra={"account_id": account_id, "transaction_id": data["transaction_id"]},
                    )
                    return PaymentSuccess(transaction_id=data["transaction_id"])

            try:
                error_data = response.json()
                error_code = error_data.get("error_code", "unknown_error")
            except Exception:
                error_code = "unknown_error"

            user_message, retryable = PAYMENT_ERROR_MAP.get(
                error_code,
                ("Payment could not be processed. Please try again or contact support.", False),
            )

            logger.warning(
                "process_payment failed",
                extra={"account_id": account_id, "error_code": error_code},
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

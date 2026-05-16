"""API request/response models."""

from dataclasses import dataclass


@dataclass
class AccountLookupSuccess:
    account_id: str
    full_name: str
    dob: str
    aadhaar_last4: str
    pincode: str
    balance: float


@dataclass
class AccountLookupError:
    error_code: str
    message: str
    retryable: bool


@dataclass
class PaymentSuccess:
    transaction_id: str


@dataclass
class PaymentError:
    error_code: str
    message: str
    retryable: bool

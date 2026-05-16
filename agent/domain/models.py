"""Domain models."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class ExtractedFields:
    account_id: Optional[str] = None
    full_name: Optional[str] = None
    dob: Optional[str] = None
    aadhaar_last4: Optional[str] = None
    pincode: Optional[str] = None
    amount: Optional[float] = None
    card_number: Optional[str] = None
    cvv: Optional[str] = None
    expiry: Optional[str] = None
    expiry_month: Optional[int] = None
    expiry_year: Optional[int] = None
    cardholder_name: Optional[str] = None


@dataclass
class AccountData:
    account_id: str
    full_name: str
    dob: str
    aadhaar_last4: str
    pincode: str
    balance: float


@dataclass
class CardDetails:
    cardholder_name: str
    card_number: str
    cvv: str
    expiry_month: int
    expiry_year: int

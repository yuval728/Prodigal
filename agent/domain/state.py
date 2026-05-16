"""Conversation state container."""

from dataclasses import dataclass, field
from typing import Optional

from .models import AccountData, CardDetails, ExtractedFields
from .stage import Stage

MAX_VERIFICATION_RETRIES = 3
MAX_PAYMENT_RETRIES = 2
MAX_CARD_RETRIES = 3


@dataclass
class ConversationState:
    stage: Stage = Stage.GREETING

    account_id: Optional[str] = None
    account_data: Optional[AccountData] = None
    provided_name: Optional[str] = None
    provided_dob: Optional[str] = None
    provided_aadhaar: Optional[str] = None
    provided_pincode: Optional[str] = None
    payment_amount: Optional[float] = None
    card_details: Optional[CardDetails] = None
    pending_card_number: Optional[str] = None
    pending_cvv: Optional[str] = None
    pending_expiry: Optional[str] = None
    pending_cardholder_name: Optional[str] = None

    verified: bool = False
    verification_attempts: int = 0

    payment_attempts: int = 0
    card_attempts: int = 0
    last_payment_error: Optional[str] = None

    transaction_id: Optional[str] = None

    history: list = field(default_factory=list)
    last_extraction: Optional[ExtractedFields] = None

    def add_turn(self, role: str, content: str) -> None:
        self.history.append({"role": role, "content": content})

    def clear_card_details(self) -> None:
        self.card_details = None

    def clear_pending_card_details(self) -> None:
        self.pending_card_number = None
        self.pending_cvv = None
        self.pending_expiry = None
        self.pending_cardholder_name = None

    def clear_all_card_data(self) -> None:
        self.clear_card_details()
        self.clear_pending_card_details()

    def is_closed(self) -> bool:
        return self.stage == Stage.CLOSED

    def secondary_factor_provided(self) -> bool:
        return any([
            self.provided_dob,
            self.provided_aadhaar,
            self.provided_pincode,
        ])

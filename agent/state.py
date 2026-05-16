"""
state.py — Conversation state definitions and stage enum.

All mutable state lives here. The agent never stores state in local variables
across turns — everything goes through ConversationState.
"""

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional


# ---------------------------------------------------------------------------
# Stage enum — the single source of truth for where we are in the flow
# ---------------------------------------------------------------------------

class Stage(Enum):
    GREETING            = auto()   # Initial state, waiting for account ID
    ACCOUNT_LOOKUP      = auto()   # Account ID received, calling API
    IDENTITY_COLLECTION = auto()   # Collecting name + secondary factor
    VERIFICATION        = auto()   # Running verification logic
    BALANCE_DISCLOSURE  = auto()   # Verified — share balance, ask for amount
    CARD_COLLECTION     = auto()   # Collecting card details
    PAYMENT_PROCESSING  = auto()   # Calling process-payment API
    CLOSED              = auto()   # Terminal state — conversation over


# ---------------------------------------------------------------------------
# Extracted fields — what the LLM extraction layer produces per turn
# ---------------------------------------------------------------------------

@dataclass
class ExtractedFields:
    """
    Structured output from the extraction layer.
    All fields are Optional — the extractor only returns what it found.
    """
    account_id:     Optional[str] = None
    full_name:      Optional[str] = None
    dob:            Optional[str] = None    # normalized to YYYY-MM-DD
    aadhaar_last4:  Optional[str] = None    # 4 digits, string
    pincode:        Optional[str] = None    # 6 digits, string
    amount:         Optional[float] = None

    # Card fields
    card_number:    Optional[str] = None    # digits only, no spaces
    cvv:            Optional[str] = None
    expiry:         Optional[str] = None    # raw string from user, e.g. "12/2027"
    expiry_month:   Optional[int] = None    # normalized by validator layer
    expiry_year:    Optional[int] = None    # normalized by validator layer
    cardholder_name: Optional[str] = None


# ---------------------------------------------------------------------------
# Account data — returned from lookup API, held in memory only
# ---------------------------------------------------------------------------

@dataclass
class AccountData:
    account_id:     str
    full_name:      str
    dob:            str
    aadhaar_last4:  str
    pincode:        str
    balance:        float


# ---------------------------------------------------------------------------
# Card details — held in memory only for the duration of the API call
# Cleared immediately after process-payment returns
# ---------------------------------------------------------------------------

@dataclass
class CardDetails:
    cardholder_name: str
    card_number:     str
    cvv:             str
    expiry_month:    int
    expiry_year:     int


# ---------------------------------------------------------------------------
# Main conversation state
# ---------------------------------------------------------------------------

MAX_VERIFICATION_RETRIES = 3   # Hard limit before closing conversation
MAX_PAYMENT_RETRIES      = 2   # Retries for retryable payment errors
MAX_CARD_RETRIES         = 3   # Retries for invalid card details


@dataclass
class ConversationState:
    """
    All mutable state for one conversation session.
    Instantiated once per Agent instance, lives for the full session.
    """

    stage: Stage = Stage.GREETING

    # -- Collected inputs --
    account_id:     Optional[str]         = None
    account_data:   Optional[AccountData] = None
    provided_name:  Optional[str]         = None
    provided_dob:   Optional[str]         = None
    provided_aadhaar: Optional[str]       = None
    provided_pincode: Optional[str]       = None
    payment_amount: Optional[float]       = None
    card_details:   Optional[CardDetails] = None

    # -- Verification tracking --
    verified:               bool = False
    verification_attempts:  int  = 0

    # -- Payment tracking --
    payment_attempts:       int  = 0
    card_attempts:          int  = 0
    last_payment_error:     Optional[str] = None

    # -- Outcome --
    transaction_id: Optional[str] = None

    # -- Conversation history (for LLM context) --
    # List of {"role": "user"|"assistant", "content": str}
    history: list = field(default_factory=list)

    # -- Turn metadata --
    last_extraction: Optional[ExtractedFields] = None

    def add_turn(self, role: str, content: str) -> None:
        """Append a message to conversation history."""
        self.history.append({"role": role, "content": content})

    def clear_card_details(self) -> None:
        """
        Dereference card data immediately after payment API call.
        Regulatory requirement: card data must not persist beyond
        the transaction call (PCI-DSS adjacency, RBI DPSS guidelines).
        """
        self.card_details = None

    def is_closed(self) -> bool:
        return self.stage == Stage.CLOSED

    def secondary_factor_provided(self) -> bool:
        """Returns True if at least one secondary factor has been collected."""
        return any([
            self.provided_dob,
            self.provided_aadhaar,
            self.provided_pincode,
        ])

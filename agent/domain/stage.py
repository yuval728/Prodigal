"""Conversation stages."""

from enum import Enum, auto


class Stage(Enum):
    GREETING = auto()
    ACCOUNT_LOOKUP = auto()
    IDENTITY_COLLECTION = auto()
    VERIFICATION = auto()
    BALANCE_DISCLOSURE = auto()
    CARD_COLLECTION = auto()
    PAYMENT_PROCESSING = auto()
    CLOSED = auto()

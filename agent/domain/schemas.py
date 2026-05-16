"""Pydantic schemas for structured LLM outputs."""

from pydantic import BaseModel, ConfigDict, field_validator


class ExtractionPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    account_id: str | None = None
    full_name: str | None = None
    dob: str | None = None
    aadhaar_last4: str | None = None
    pincode: str | None = None
    amount: str | int | float | None = None
    card_number: str | int | None = None
    cvv: str | int | None = None
    expiry: str | None = None
    cardholder_name: str | None = None

    @field_validator(
        "account_id",
        "full_name",
        "dob",
        "aadhaar_last4",
        "pincode",
        "expiry",
        "cardholder_name",
        mode="before",
    )
    @classmethod
    def _coerce_str(cls, value: object) -> object:
        if value is None:
            return None
        return str(value)

    @field_validator("amount", "card_number", "cvv", mode="before")
    @classmethod
    def _coerce_numeric_str(cls, value: object) -> object:
        if value is None:
            return None
        return str(value)

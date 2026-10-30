from __future__ import annotations

from datetime import date as Date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

LOW_CONFIDENCE_THRESHOLD = 0.7
REQUIRED_RECEIPT_FIELDS = ("date", "merchant", "total_amount")


class ReceiptParseError(ValueError):
    """Raised when receipt analysis output is not valid receipt JSON."""


class VatBreakdown(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rate: Decimal | None = Field(default=None, ge=0, le=100)
    amount: Decimal | None = Field(default=None, ge=0)


class ReceiptLineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1)
    quantity: Decimal | None = Field(default=None, gt=0)
    price: Decimal | None = Field(default=None, ge=0)

    @field_validator("description")
    @classmethod
    def strip_description(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("description must not be empty")
        return value


class ReceiptData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: Date | None = None
    merchant: str | None = None
    total_amount: Decimal | None = Field(default=None, gt=0)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    vat: list[VatBreakdown] = Field(default_factory=list)
    payment_method: str | None = None
    line_items: list[ReceiptLineItem] = Field(default_factory=list)
    suggested_category: str | None = None
    confidence: float = Field(ge=0, le=1)
    notes: str | None = None

    @field_validator("merchant", "payment_method", "suggested_category", "notes")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        return value.strip().upper()


def parse_receipt_json(raw_json: str) -> ReceiptData:
    try:
        return ReceiptData.model_validate_json(raw_json)
    except ValidationError as error:
        raise ReceiptParseError("Receipt analysis output failed validation") from error


class ReceiptAnalysis(BaseModel):
    """Only the fields requested from the local vision model, to keep its answer short."""

    # Extra fields from a verbose model are ignored, not rejected.
    model_config = ConfigDict(extra="ignore")

    date: Date | None = None
    merchant: str | None = None
    total_amount: Decimal | None = Field(default=None, gt=0)
    confidence: float = Field(ge=0, le=1)


def parse_analysis_json(raw_json: str) -> ReceiptData:
    try:
        analysis = ReceiptAnalysis.model_validate_json(raw_json)
        return ReceiptData.model_validate(analysis.model_dump())
    except ValidationError as error:
        raise ReceiptParseError("Receipt analysis output failed validation") from error


def missing_required_fields(receipt: ReceiptData) -> tuple[str, ...]:
    return tuple(
        field_name
        for field_name in REQUIRED_RECEIPT_FIELDS
        if getattr(receipt, field_name) is None
    )


def requires_user_clarification(
    receipt: ReceiptData,
    threshold: float = LOW_CONFIDENCE_THRESHOLD,
) -> bool:
    if not 0 <= threshold <= 1:
        raise ValueError("threshold must be between 0 and 1")
    return bool(missing_required_fields(receipt)) or receipt.confidence < threshold

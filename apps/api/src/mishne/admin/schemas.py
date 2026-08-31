"""Request bodies for the back-office.

Every mutating one carries a `reason`, and it is required rather than optional.
"Who gave this organisation 500 credits, and why" is the question
`platform_actions` exists to answer, and a reason that may be blank is a
question that cannot be answered six months later — by which time the person
who did it has forgotten and the customer is disputing an invoice.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..schemas import TierId

def _reason() -> Any:
    """Long enough for a sentence and a ticket number, not a place for an essay.

    A function rather than one shared `Field(...)` instance: a FieldInfo carries
    per-model state once it is bound, and reusing one object across five models
    is the kind of sharing that works until the day it does not.
    """
    return Field(min_length=3, max_length=500)


class LoginRequest(BaseModel):
    email: str
    password: str


class CreateAdminRequest(BaseModel):
    email: str
    name: str = ""
    password: str


class GrantCreditsRequest(BaseModel):
    #: Negative is a correction, recorded as `adjustment`. Bounded in both
    #: directions: a slipped decimal point is the realistic mistake here, and
    #: the ledger is append-only, so an over-grant is corrected by a second
    #: entry rather than undone.
    credits: float = Field(ge=-100_000, le=100_000)
    reason: str = _reason()


class SetTierRequest(BaseModel):
    tier: TierId
    reason: str = _reason()


class SetRetentionRequest(BaseModel):
    #: A day at the short end and ten years at the long. The lifecycle rules
    #: read this column, so a zero would mean "delete tomorrow's uploads".
    retention_days: int = Field(ge=1, le=3650)
    reason: str = _reason()


class SuspendRequest(BaseModel):
    reason: str = _reason()


class DeleteOrgRequest(BaseModel):
    #: The organisation's name, typed by the operator. Not security — they are
    #: already authenticated and could type anything — but the difference
    #: between deleting the tenant you meant and the one above it in a list.
    confirm_name: str
    reason: str = _reason()

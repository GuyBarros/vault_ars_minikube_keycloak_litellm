from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserRecord(BaseModel):
    """A user record. Extra fields are preserved so callers can pass through
    arbitrary attributes that do not have a first-class field here.

    first_name/last_name are optional here (not on NewUserRecord below)
    because this same type also carries update_user_by_email's *partial*
    payload, where omitting a field means "leave it unchanged" - making them
    mandatory here would make every update require re-sending the user's
    whole name just to change one other field."""

    model_config = ConfigDict(extra="allow")

    email: EmailStr = Field(..., description="Primary unique identifier for the user.")
    first_name: str | None = None
    last_name: str | None = None
    ssn: str | None = None
    phone: str | None = None
    credit_card_number: str | None = None
    ip_address: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=False, mode="json")


class NewUserRecord(BaseModel):
    """create_user's input: email, first_name, and last_name are mandatory
    (ssn/phone/credit_card_number/ip_address stay optional - the seed data
    happens to always populate them, but nothing about the domain requires
    it). A separate type from UserRecord specifically so the mandatory-field
    contract create_user needs doesn't leak into update_user_by_email's
    partial-update semantics above."""

    model_config = ConfigDict(extra="allow")

    email: EmailStr = Field(..., description="Primary unique identifier for the user.")
    first_name: str = Field(..., description="User's first name.")
    last_name: str = Field(..., description="User's last name.")
    ssn: str | None = None
    phone: str | None = None
    credit_card_number: str | None = None
    ip_address: str | None = None


class UserListResult(BaseModel):
    users: list[UserRecord]
    count: int


def to_user_list_result(users: list[UserRecord]) -> UserListResult:
    return UserListResult(users=users, count=len(users))

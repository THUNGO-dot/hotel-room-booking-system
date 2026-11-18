"""Small helpers shared by the domain classes (no database, no Flask)."""
from __future__ import annotations

from datetime import date, datetime
from enum import Enum

DT = "%Y-%m-%d %H:%M:%S"


class StrEnum(str, Enum):
    """An enum whose members compare equal to the strings stored in the database."""

    def __str__(self) -> str:
        return self.value


def to_date(value) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def to_dt(value) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    text = str(value)
    try:
        return datetime.strptime(text, DT)
    except ValueError:
        return datetime.fromisoformat(text)


def dt_s(value) -> str | None:
    return value.strftime(DT) if isinstance(value, datetime) else value


def get(row, key, default=None):
    """Read `key` from a sqlite3.Row or a dict, with a default when it is missing."""
    try:
        value = row[key]
    except (KeyError, IndexError):
        return default
    return default if value is None else value


def status_text(status) -> str:
    """'CHECKED_IN' -> 'checked in', used inside the NOT_ALLOWED_STATE message."""
    return str(getattr(status, "value", status)).replace("_", " ").lower()

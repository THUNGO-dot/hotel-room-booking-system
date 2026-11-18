"""Enumerated types named in the class diagram (Chapter 5)."""
from __future__ import annotations

from ._util import StrEnum


class Role(StrEnum):
    CUSTOMER = "CUSTOMER"
    RECEPTIONIST = "RECEPTIONIST"
    ADMIN = "ADMIN"


class AccountStatus(StrEnum):
    ACTIVE = "ACTIVE"
    LOCKED = "LOCKED"


class BookingStatus(StrEnum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    CHECKED_IN = "CHECKED_IN"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


# statuses that occupy inventory (BR-07)
LIVE_STATUSES = (BookingStatus.PENDING, BookingStatus.CONFIRMED, BookingStatus.CHECKED_IN)


class RoomStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    OCCUPIED = "OCCUPIED"
    CLEANING = "CLEANING"
    MAINTENANCE = "MAINTENANCE"


class PaymentMethod(StrEnum):
    CASH = "CASH"
    CARD = "CARD"
    E_WALLET = "E_WALLET"


class PaymentPurpose(StrEnum):
    DEPOSIT = "DEPOSIT"
    BALANCE = "BALANCE"


class RefundStatus(StrEnum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"


class NotificationStatus(StrEnum):
    SENT = "SENT"
    FAILED = "FAILED"

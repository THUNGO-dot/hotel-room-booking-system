"""Account hierarchy, ResetToken and AuditLog (class diagram, top row).

`Account` is abstract. `Customer`, `Receptionist` and `Admin` inherit it, exactly
as in Chapter 5. The role classes carry the use-case operations of their actor;
they call the service layer, which owns the database work.
"""
from __future__ import annotations

import secrets
from abc import ABC
from dataclasses import dataclass
from datetime import datetime, timedelta

from werkzeug.security import check_password_hash, generate_password_hash

from ..errors import ServiceError
from ._util import dt_s, get, to_dt
from .enums import AccountStatus, Role


# --------------------------------------------------------------------- tokens
@dataclass
class ResetToken:
    """A single-use password reset code (BR-04). Only its hash is kept."""
    account_id: int
    code_hash: str
    expires_at: datetime
    used: bool = False
    token_id: int | None = None

    @classmethod
    def from_row(cls, row) -> "ResetToken":
        return cls(row["account_id"], row["code_hash"], to_dt(row["expires_at"]), bool(row["used"]),
                   row["token_id"])

    def is_valid(self, now: datetime) -> bool:
        return not self.used and self.expires_at >= now

    def matches(self, code: str) -> bool:
        return check_password_hash(self.code_hash, (code or "").strip())

    def mark_used(self) -> None:
        self.used = True


@dataclass
class AuditLog:
    """One line of the audit trail (CR-05)."""
    account_id: int
    action: str
    entity_type: str
    entity_id: int | None = None
    details: str = ""
    created_at: str = ""
    log_id: int | None = None

    def record(self, db) -> int:
        self.details = (self.details or "")[:500]
        self.log_id = db.insert(
            "INSERT INTO audit_log(account_id, action, entity_type, entity_id, details, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (self.account_id, self.action, self.entity_type, self.entity_id, self.details, self.created_at))
        return self.log_id


# -------------------------------------------------------------------- account
class Account(ABC):
    """Common state and behaviour of every signed-in user."""

    ROLE: Role

    def __init__(self, account_id, email, password_hash, full_name, phone,
                 status=AccountStatus.ACTIVE, failed_logins=0, created_at=None):
        if type(self) is Account:
            raise TypeError("Account is abstract: use Customer, Receptionist or Admin")
        self.account_id = account_id
        self.email = email
        self.password_hash = password_hash
        self.full_name = full_name
        self.phone = phone
        self.role: Role = self.ROLE
        self.status = AccountStatus(status)
        self.failed_logins = int(failed_logins or 0)
        self.created_at = dt_s(created_at)
        self.signed_in = False

    # -- construction ---------------------------------------------------
    @classmethod
    def from_row(cls, row) -> "Account":
        """Build the right subclass for the role stored in the row."""
        klass = {Role.CUSTOMER: Customer, Role.RECEPTIONIST: Receptionist, Role.ADMIN: Admin}[Role(row["role"])]
        return klass(row["account_id"], row["email"], row["password_hash"], row["full_name"], row["phone"],
                     row["status"], get(row, "failed_logins", 0), get(row, "created_at"))

    def __getitem__(self, key: str):
        """Lets an Account stand in for a database row wherever a service reads `actor["role"]`."""
        value = getattr(self, key)
        return getattr(value, "value", value)

    @property
    def is_active(self) -> bool:
        return self.status == AccountStatus.ACTIVE

    @staticmethod
    def hash_password(password: str) -> str:
        return generate_password_hash(password)  # BR-02 / NFR-03: only a salted hash is stored

    # -- diagram: login() / logout() / changePassword() / requestReset() ------
    def login(self, password: str, max_failed_logins: int = 5) -> None:
        """BR-03 / BR-05. Raises MSG-06 for a wrong password and MSG-07 once locked.
        The caller saves `failed_logins` and `status` afterwards (also after an error)."""
        if self.status == AccountStatus.LOCKED:
            raise ServiceError("ACCOUNT_LOCKED")
        if not check_password_hash(self.password_hash, password or ""):
            self.failed_logins += 1
            if self.failed_logins >= max_failed_logins:
                self.status = AccountStatus.LOCKED
                raise ServiceError("ACCOUNT_LOCKED")
            raise ServiceError("BAD_CREDENTIALS")
        self.failed_logins = 0
        self.signed_in = True

    def logout(self) -> None:
        self.signed_in = False

    def change_password(self, current_password: str, new_password: str, min_length: int = 8) -> None:
        if not check_password_hash(self.password_hash, current_password or ""):
            raise ServiceError("BAD_CURRENT_PASSWORD")
        self.set_password(new_password, min_length)

    def set_password(self, new_password: str, min_length: int = 8) -> None:
        if len(new_password or "") < min_length:
            raise ServiceError("INVALID_INPUT", detail=f"Use a password with at least {min_length} characters.")
        self.password_hash = self.hash_password(new_password)

    def request_reset(self, now: datetime, minutes: int = 30) -> tuple[ResetToken, str]:
        """UC-14: returns the token to store and the 6-digit code to e-mail."""
        code = f"{secrets.randbelow(10 ** 6):06d}"
        return ResetToken(self.account_id, self.hash_password(code), now + timedelta(minutes=minutes)), code


class Customer(Account):
    ROLE = Role.CUSTOMER

    @classmethod
    def register(cls, ctx, full_name: str, email: str, phone: str, password: str) -> "Customer":
        from ..services import accounts
        return cls.from_row(accounts.get(ctx, accounts.register(ctx, full_name, email, phone, password)))

    def update_profile(self, ctx, full_name: str, phone: str, current_password: str = "",
                       new_password: str = "") -> None:
        from ..services import accounts
        accounts.update_profile(ctx, self.account_id, full_name, phone, current_password, new_password)

    def search_rooms(self, ctx, check_in: str, check_out: str, adults: int = 2, children: int = 0, **filters):
        from ..services import availability
        return availability.search(ctx, check_in, check_out, adults, children, **filters)

    def create_booking(self, ctx, type_id: int, check_in: str, check_out: str, adults: int,
                       children: int = 0, special_request: str = ""):
        from ..services import bookings
        return bookings.create_booking(ctx, self, type_id, check_in, check_out, adults, children, special_request)

    def cancel_booking(self, ctx, code: str) -> dict:
        from ..services import bookings
        return bookings.cancel_booking(ctx, self, code)

    def view_history(self, ctx, tab: str = "all"):
        from ..services import bookings
        return bookings.list_for_customer(ctx, self.account_id, tab)


class Receptionist(Account):
    ROLE = Role.RECEPTIONIST

    def confirm_booking(self, ctx, code: str):
        from ..services import bookings
        return bookings.confirm_booking(ctx, self, code)

    def reject_booking(self, ctx, code: str, reason: str):
        from ..services import bookings
        return bookings.reject_booking(ctx, self, code, reason)

    def create_walk_in(self, ctx, guest_name: str, guest_phone: str, type_id: int, stay_nights, guests):
        from ..services import bookings
        return bookings.create_walk_in(ctx, self, guest_name, guest_phone, type_id, stay_nights, guests)

    def check_in(self, ctx, code: str, id_document: str, room_id):
        from ..services import frontdesk
        return frontdesk.check_in(ctx, self, code, id_document, room_id)

    def check_out(self, ctx, code: str, method: str | None, late: bool, decline: bool = False):
        from ..services import frontdesk
        return frontdesk.check_out(ctx, self, code, method, late, decline)

    def record_payment(self, ctx, booking, method: str, decline: bool = False) -> int:
        from ..services import payments
        return payments.pay_deposit(ctx, booking, method, self, decline)

    def cancel_booking(self, ctx, code: str) -> dict:
        from ..services import bookings
        return bookings.cancel_booking(ctx, self, code)


class Admin(Account):
    ROLE = Role.ADMIN

    def manage_staff(self, ctx):
        from ..services import accounts
        return accounts.list_staff(ctx, self)

    def create_staff(self, ctx, full_name: str, email: str, phone: str, role: str) -> str:
        from ..services import accounts
        return accounts.create_staff(ctx, self, full_name, email, phone, role)

    def set_staff_locked(self, ctx, account_id: int, locked: bool) -> None:
        from ..services import accounts
        accounts.set_locked(ctx, self, account_id, locked)

    def manage_rooms(self, ctx):
        from ..services import catalog
        return catalog.list_rooms(ctx, include_inactive=True)

    def save_room(self, ctx, room_id, room_number, type_id, floor, status) -> int:
        from ..services import catalog
        return catalog.save_room(ctx, self, room_id, room_number, type_id, floor, status)

    def manage_prices(self, ctx, type_id: int):
        from ..services import catalog
        return catalog.list_periods(ctx, type_id)

    def save_price_period(self, ctx, type_id: int, start: str, end: str, price, period_id=None) -> int:
        from ..services import catalog
        return catalog.save_period(ctx, self, type_id, start, end, price, period_id)

    def view_reports(self, ctx, kind: str, start: str, end: str, group: str = "day") -> dict:
        from ..services import reports
        fn = reports.occupancy_report if kind == "occupancy" else reports.revenue_report
        return fn(ctx, self, start, end, group)

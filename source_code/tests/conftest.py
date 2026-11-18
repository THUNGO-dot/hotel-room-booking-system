"""Shared fixtures. Every test gets a fresh in-memory database, a fixed clock
(Thursday 5 Nov 2026, 10:00) and a simulated mailer / payment gateway."""
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hotel import seed  # noqa: E402
from hotel.context import Clock, Context, SimulatedMailer  # noqa: E402
from hotel.db import Database  # noqa: E402
from hotel.services import accounts, bookings, frontdesk  # noqa: E402
from hotel.settings import Settings  # noqa: E402

NOW = datetime(2026, 11, 5, 10, 0)


@pytest.fixture
def ctx():
    db = Database(":memory:")
    db.init_schema()
    return Context(db, Settings(), Clock(NOW), SimulatedMailer())


@pytest.fixture
def ids(ctx):
    return seed.seed_base(ctx)


@pytest.fixture
def customer(ctx, ids):
    return accounts.get(ctx, ids["customer"])


@pytest.fixture
def reception(ctx, ids):
    return accounts.get(ctx, ids["reception"])


@pytest.fixture
def admin(ctx, ids):
    return accounts.get(ctx, ids["admin"])


STD, DLX, EXE = 0, 1, 2  # indexes into ids["types"]


@pytest.fixture
def make(ctx, ids, customer, reception):
    """make(type_idx, ci, co, pay=True, confirm=True) -> booking row"""
    def _make(type_idx=DLX, ci="2026-11-10", co="2026-11-12", pay=True, confirm=True, adults=2, children=0):
        b = bookings.create_booking(ctx, customer, ids["types"][type_idx], ci, co, adults, children)
        if pay:
            from hotel.services import payments
            payments.pay_deposit(ctx, b, "CARD", customer)
        if confirm:
            b, _ = bookings.confirm_booking(ctx, reception, b["booking_code"])
        return bookings.get_booking(ctx, b["booking_code"])
    return _make


@pytest.fixture
def checked_in(ctx, ids, reception, make):
    """A deluxe stay that arrived today (5-7 Nov) and is checked into room 201."""
    b = make(DLX, "2026-11-05", "2026-11-07")
    return frontdesk.check_in(ctx, reception, b["booking_code"], "079091234567", ids["rooms"]["201"])

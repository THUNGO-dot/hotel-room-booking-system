from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from ..context import Context
from ..domain import AuditLog, Booking, Refund
from ..errors import ServiceError

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def d(value: str | date) -> date:
    return value if isinstance(value, date) else date.fromisoformat(value)


def nights(ci: str | date, co: str | date) -> int:
    return (d(co) - d(ci)).days


def daterange(start: str | date, end: str | date):
    """Yield every date from start up to but NOT including end."""
    cur, stop = d(start), d(end)
    while cur < stop:
        yield cur
        cur += timedelta(days=1)


def digits(text: str | None) -> int:
    return len(re.sub(r"\D", "", text or ""))


def round_to(value: float, step: int) -> int:
    return int(round(value / step)) * step


def deposit_for(ctx: Context, room_charge: int) -> int:
    """BR-13: 30% of the room charge, rounded to the nearest 1,000 VND."""
    return Booking.deposit_for(room_charge, ctx.cfg)


def hours_until_checkin(ctx: Context, check_in_date: str | date) -> float:
    start = datetime.combine(d(check_in_date), datetime.min.time()).replace(hour=ctx.cfg.checkin_hour)
    return (start - ctx.now()).total_seconds() / 3600


def refund_rate_for(ctx: Context, hours: float) -> int:
    """BR-12: 100% at >= 48 h, 50% at >= 24 h, otherwise 0%."""
    return Refund.rate_for(hours, ctx.cfg)


def audit(ctx: Context, actor_id: int | None, action: str, entity_type: str,
          entity_id: int | None = None, details: str = "") -> None:
    """CR-05: every staff mutation is written to the audit log."""
    if actor_id is None:
        return
    AuditLog(actor_id, action, entity_type, entity_id, details, ctx.now_s()).record(ctx.db)


def require_role(actor, *roles: str) -> None:
    """Server-side authorization (CR-04 / NFR-02). `actor` is an account row."""
    from ..errors import PermissionDenied
    if actor is None or actor["role"] not in roles or actor["status"] != "ACTIVE":
        raise PermissionDenied()


def require_text(value: str | None, label: str, maxlen: int = 100) -> str:
    value = (value or "").strip()
    if not value:
        raise ServiceError("INVALID_INPUT", detail=f"Enter {label}.")
    if len(value) > maxlen:
        raise ServiceError("INVALID_INPUT", detail=f"{label[0].upper()}{label[1:]} is too long (max {maxlen} characters).")
    return value

"""Booking life cycle: Pending -> Confirmed -> Checked-in -> Completed (BR-09..17).

UC-04 book, UC-05 cancel, UC-06 confirm/reject, UC-16 walk-in, FR-11 change dates.
"""
from __future__ import annotations

from datetime import timedelta

from ..context import DT, Context
from ..domain import Booking, BookingStatus, Refund
from ..errors import PermissionDenied, ServiceError
from . import availability, catalog, notifications, payments
from .common import audit, deposit_for, digits, hours_until_checkin, require_role, require_text

BASE = (
    "SELECT b.*, t.name AS type_name, t.capacity AS capacity, r.room_number AS room_number,"
    " a.email AS account_email,"
    " COALESCE((SELECT SUM(p.amount) FROM payment p WHERE p.booking_id=b.booking_id),0) AS paid"
    " FROM booking b"
    " JOIN room_type t ON t.room_type_id=b.room_type_id"
    " LEFT JOIN room r ON r.room_id=b.room_id"
    " LEFT JOIN account a ON a.account_id=b.account_id")


def get_booking(ctx: Context, code: str):
    return ctx.db.one(BASE + " WHERE b.booking_code=?", (code,))


def get_booking_by_id(ctx: Context, booking_id: int):
    return ctx.db.one(BASE + " WHERE b.booking_id=?", (booking_id,))


def can_view(actor, booking) -> bool:
    """A customer sees only their own bookings (NFR-04); staff see all."""
    if actor is None:
        return False
    if actor["role"] == "CUSTOMER":
        return booking["account_id"] == actor["account_id"]
    return actor["role"] in ("RECEPTIONIST", "ADMIN")


def load_for(ctx: Context, actor, code: str):
    b = get_booking(ctx, code)
    if b is None:
        raise ServiceError("NOT_FOUND")
    if not can_view(actor, b):
        raise PermissionDenied()
    return b


# ---------------------------------------------------------------- hold expiry
def expire_holds(ctx: Context) -> int:
    """BR-10 / UC-04 exception 5a: a Pending booking whose deposit is not fully paid
    within 24 hours becomes Expired and its room goes back on sale. Any partial
    payment is queued for refund."""
    rows = ctx.db.all(BASE + " WHERE b.status='PENDING' AND b.hold_expires_at IS NOT NULL AND b.hold_expires_at<?",
                      (ctx.now_s(),))
    n = 0
    for r in rows:
        booking = Booking.from_row(r)
        if not booking.is_hold_overdue(ctx.now()):
            continue  # deposit is in: the booking waits for the front desk, not for the clock
        with ctx.db.transaction():
            booking.expire()
            ctx.db.execute("UPDATE booking SET status=? WHERE booking_id=?", (booking.status.value, booking.booking_id))
            payments.create_refund(ctx, booking.booking_id, 100, "Hold expired before the deposit was complete", False)
        n += 1
    return n


# ------------------------------------------------------------------- codes
def next_code(ctx: Context, prefix: str = "HB") -> str:
    stamp = ctx.today().strftime("%Y%m%d")
    table, col = ("booking", "booking_code") if prefix == "HB" else ("invoice", "invoice_no")
    count = ctx.db.scalar(f"SELECT COUNT(*) FROM {table} WHERE {col} LIKE ?", (f"{prefix}-{stamp}-%",))
    seq = count + 1
    while ctx.db.scalar(f"SELECT 1 FROM {table} WHERE {col}=?", (f"{prefix}-{stamp}-{seq:04d}",)):
        seq += 1
    return f"{prefix}-{stamp}-{seq:04d}"


# ----------------------------------------------------------------- create
def create_booking(ctx: Context, account, type_id: int, check_in: str, check_out: str,
                   adults: int, children: int = 0, special_request: str = ""):
    """UC-04. Holds the room for 24 hours and returns the new booking row."""
    require_role(account, "CUSTOMER")
    expire_holds(ctx)
    availability.validate_stay(ctx, check_in, check_out)
    t = catalog.get_room_type(ctx, type_id)
    if t is None or not t["active"]:
        raise ServiceError("NOT_FOUND")
    adults, children = int(adults), int(children or 0)
    guests = adults + children
    if adults < 1:
        raise ServiceError("INVALID_INPUT", detail="At least one adult is needed.")
    err = availability.capacity_error(ctx, t, guests)
    if err:
        raise err
    with ctx.db.transaction():  # re-check inside the write lock: UC-04 exception 4a
        if availability.rooms_left(ctx, type_id, check_in, check_out) <= 0:
            raise ServiceError("NO_ROOM")
        charge = catalog.quote(ctx, type_id, check_in, check_out)
        code = next_code(ctx)
        hold = ctx.now() + timedelta(hours=ctx.cfg.hold_hours)
        ctx.db.insert(
            "INSERT INTO booking(booking_code,account_id,room_type_id,guest_name,guest_phone,check_in_date,"
            "check_out_date,guests,special_request,status,room_charge,deposit_amount,hold_expires_at,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,'PENDING',?,?,?,?)",
            (code, account["account_id"], type_id, account["full_name"], account["phone"], check_in, check_out,
             guests, (special_request or "").strip()[:200], charge, deposit_for(ctx, charge),
             hold.strftime(DT), ctx.now_s()))
    return get_booking(ctx, code)


def create_walk_in(ctx: Context, actor, guest_name: str, guest_phone: str, type_id: int,
                   stay_nights, guests) -> "sqlite3.Row":  # noqa: F821
    """UC-16 / BR-15: no account, confirmed at once, arrives today."""
    require_role(actor, "RECEPTIONIST")
    expire_holds(ctx)
    name = require_text(guest_name, "the guest's name")
    if digits(guest_phone) < 9:
        raise ServiceError("INVALID_INPUT", detail="Enter a valid phone number (at least 9 digits).")
    try:
        n, g = int(stay_nights), int(guests)
    except (TypeError, ValueError):
        raise ServiceError("INVALID_INPUT", detail="Nights and guests must be whole numbers.")
    if not 1 <= n <= ctx.cfg.max_nights:
        raise ServiceError("INVALID_INPUT", detail=f"A stay is 1 to {ctx.cfg.max_nights} nights.")
    if g < 1:
        raise ServiceError("INVALID_INPUT", detail="Enter the number of guests.")
    t = catalog.get_room_type(ctx, type_id)
    if t is None or not t["active"]:
        raise ServiceError("NOT_FOUND")
    err = availability.capacity_error(ctx, t, g)
    if err:
        raise err
    ci = ctx.today().isoformat()
    co = (ctx.today() + timedelta(days=n)).isoformat()
    with ctx.db.transaction():
        free_now = ctx.db.scalar("SELECT COUNT(*) FROM room WHERE room_type_id=? AND active=1 AND status='AVAILABLE'",
                                 (type_id,))
        if availability.rooms_left(ctx, type_id, ci, co) <= 0 or free_now == 0:
            raise ServiceError("NO_ROOM")  # MSG-19
        charge = catalog.quote(ctx, type_id, ci, co)
        code = next_code(ctx)
        ctx.db.insert(
            "INSERT INTO booking(booking_code,account_id,room_type_id,guest_name,guest_phone,check_in_date,"
            "check_out_date,guests,status,room_charge,deposit_amount,hold_expires_at,created_at)"
            " VALUES (?,NULL,?,?,?,?,?,?,'CONFIRMED',?,0,NULL,?)",
            (code, type_id, name, guest_phone.strip(), ci, co, g, charge, ctx.now_s()))
        audit(ctx, actor["account_id"], "WALK_IN_BOOKING", "booking", None, code)
    return get_booking(ctx, code)


# --------------------------------------------------------- confirm / reject
def confirm_booking(ctx: Context, actor, code: str):
    """UC-06 main flow. Returns (booking, email_status)."""
    require_role(actor, "RECEPTIONIST")
    expire_holds(ctx)
    b = get_booking(ctx, code)
    if b is None:
        raise ServiceError("NOT_FOUND")
    booking = Booking.from_row(b).confirm()  # MSG-21 / not pending / deposit not paid
    with ctx.db.transaction():
        ctx.db.execute("UPDATE booking SET status=?, hold_expires_at=NULL WHERE booking_id=?",
                       (booking.status.value, b["booking_id"]))
        audit(ctx, actor["account_id"], "CONFIRM_BOOKING", "booking", b["booking_id"], code)
    b = get_booking(ctx, code)
    return b, notifications.send_booking_confirmation(ctx, b, b["account_email"])


def reject_booking(ctx: Context, actor, code: str, reason: str):
    require_role(actor, "RECEPTIONIST")
    reason = (reason or "").strip()
    if not reason:
        raise ServiceError("REASON_REQUIRED")  # MSG-29
    b = get_booking(ctx, code)
    if b is None:
        raise ServiceError("NOT_FOUND")
    booking = Booking.from_row(b).reject(reason)
    with ctx.db.transaction():
        ctx.db.execute("UPDATE booking SET status=?, hold_expires_at=NULL WHERE booking_id=?",
                       (booking.status.value, b["booking_id"]))
        payments.create_refund(ctx, b["booking_id"], 100, f"Rejected: {reason}", False)
        audit(ctx, actor["account_id"], "REJECT_BOOKING", "booking", b["booking_id"], f"{code}: {reason}")
    return get_booking(ctx, code)


# ------------------------------------------------------------------- cancel
def refund_preview(ctx: Context, booking) -> dict:
    hours = hours_until_checkin(ctx, booking["check_in_date"])
    paid = int(booking["paid"])
    refund = Refund(booking["booking_id"])
    amount = refund.calculate_refund(paid, hours, ctx.cfg)  # BR-12
    return {"hours": max(0, round(hours)), "rate": refund.refund_rate, "paid": paid, "amount": amount}


def cancel_booking(ctx: Context, actor, code: str) -> dict:
    """UC-05 / FR-12 / BR-12. Customers cancel their own; receptionists cancel any."""
    b = load_for(ctx, actor, code)
    if actor["role"] not in ("CUSTOMER", "RECEPTIONIST"):
        raise PermissionDenied()
    booking = Booking.from_row(b).cancel()  # MSG-26 when checked in or completed
    prev = refund_preview(ctx, b)
    with ctx.db.transaction():
        ctx.db.execute("UPDATE booking SET status=?, hold_expires_at=NULL WHERE booking_id=?",
                       (booking.status.value, b["booking_id"]))
        payments.create_refund(ctx, b["booking_id"], prev["rate"], "Cancelled by "
                               + ("the guest" if actor["role"] == "CUSTOMER" else "the front desk"),
                               completed=(actor["role"] == "RECEPTIONIST"))
        if actor["role"] == "RECEPTIONIST":
            audit(ctx, actor["account_id"], "CANCEL_BOOKING", "booking", b["booking_id"],
                  f"{code}, refund {prev['amount']}")
    prev["booking"] = get_booking(ctx, code)
    return prev


# ------------------------------------------------------------ change dates
def change_dates(ctx: Context, actor, code: str, new_check_in: str, new_check_out: str) -> dict:
    """FR-11. Allowed only until 48 h before check-in and only if a room is free."""
    require_role(actor, "CUSTOMER")
    b = load_for(ctx, actor, code)
    Booking.from_row(b).require_status(BookingStatus.PENDING, BookingStatus.CONFIRMED)
    availability.validate_stay(ctx, new_check_in, new_check_out)
    if new_check_in == b["check_in_date"] and new_check_out == b["check_out_date"]:
        raise ServiceError("CHANGE_SAME")
    minimum = ctx.cfg.change_min_hours
    if hours_until_checkin(ctx, b["check_in_date"]) < minimum or hours_until_checkin(ctx, new_check_in) < minimum:
        raise ServiceError("CHANGE_TOO_LATE", hours=minimum)
    with ctx.db.transaction():
        if availability.rooms_left(ctx, b["room_type_id"], new_check_in, new_check_out, b["booking_id"]) <= 0:
            raise ServiceError("NO_ROOM")
        charge = catalog.quote(ctx, b["room_type_id"], new_check_in, new_check_out)
        deposit = deposit_for(ctx, charge)
        paid = int(b["paid"])
        status, hold = b["status"], b["hold_expires_at"]
        if paid == 0:
            note = f"The deposit is now {deposit:,} VND."
        elif deposit > paid:
            status = "PENDING"  # the front desk confirms the new dates again
            hold = (ctx.now() + timedelta(hours=ctx.cfg.hold_hours)).strftime(DT)
            note = f"Pay the extra deposit of {deposit - paid:,} VND to keep the booking."
        elif deposit < paid:
            note = f"{paid - deposit:,} VND of your deposit is credited against your final bill."
        else:
            note = "Your deposit does not change."
        ctx.db.execute(
            "UPDATE booking SET check_in_date=?, check_out_date=?, room_charge=?, deposit_amount=?, status=?,"
            " hold_expires_at=? WHERE booking_id=?",
            (new_check_in, new_check_out, charge, deposit, status, hold, b["booking_id"]))
    return {"booking": get_booking(ctx, code), "note": note, "extra": max(0, deposit - paid)}


# ------------------------------------------------------------------ queries
TABS = {
    "all": None,
    "active": ("PENDING", "CONFIRMED", "CHECKED_IN"),
    "completed": ("COMPLETED",),
    "cancelled": ("CANCELLED", "REJECTED", "EXPIRED"),
}


def list_for_customer(ctx: Context, account_id: int, tab: str = "all"):
    expire_holds(ctx)
    sql, params = BASE + " WHERE b.account_id=?", [account_id]
    statuses = TABS.get(tab)
    if statuses:
        sql += f" AND b.status IN ({','.join('?' * len(statuses))})"
        params += list(statuses)
    return ctx.db.all(sql + " ORDER BY b.booking_id DESC", params)


def list_pending(ctx: Context):
    expire_holds(ctx)
    return ctx.db.all(BASE + " WHERE b.status='PENDING' ORDER BY b.created_at ASC, b.booking_id ASC")


def arrivals(ctx: Context, query: str = ""):
    """Confirmed bookings whose arrival date has been reached (UC-07 precondition)."""
    expire_holds(ctx)
    sql = BASE + " WHERE b.status='CONFIRMED' AND b.check_in_date<=?"
    params: list = [ctx.today().isoformat()]
    q = (query or "").strip().lower()
    if q:
        sql += " AND (lower(b.booking_code) LIKE ? OR lower(b.guest_name) LIKE ? OR replace(b.guest_phone,' ','') LIKE ?)"
        params += [f"%{q}%", f"%{q}%", f"%{q.replace(' ', '')}%"]
    return ctx.db.all(sql + " ORDER BY b.check_in_date, b.booking_code", params)


def inhouse(ctx: Context):
    return ctx.db.all(BASE + " WHERE b.status='CHECKED_IN' ORDER BY r.room_number")


def counters(ctx: Context) -> dict:
    expire_holds(ctx)
    today = ctx.today().isoformat()
    s = ctx.db.scalar
    return {
        "pending": s("SELECT COUNT(*) FROM booking WHERE status='PENDING'"),
        "arrivals": s("SELECT COUNT(*) FROM booking WHERE status='CONFIRMED' AND check_in_date<=?", (today,)),
        "inhouse": s("SELECT COUNT(*) FROM booking WHERE status='CHECKED_IN'"),
        "rooms_free": s("SELECT COUNT(*) FROM room WHERE active=1 AND status='AVAILABLE'"),
    }

"""Booking confirmation e-mail with retries (UC-15, FR-30, BR-35)."""
from __future__ import annotations

from ..context import Context
from ..domain import Notification


def confirmation_body(ctx: Context, booking) -> tuple[str, str]:
    from .common import nights
    n = nights(booking["check_in_date"], booking["check_out_date"])
    ci = booking["check_in_date"].split("-")[::-1]
    co = booking["check_out_date"].split("-")[::-1]
    subject = f"Booking {booking['booking_code']} confirmed - Grand Horizon Hotel"
    body = (
        f"Dear {booking['guest_name']},\n\n"
        f"Your booking {booking['booking_code']} is confirmed.\n\n"
        f"Room type : {booking['type_name']}\n"
        f"Stay      : {'/'.join(ci)} to {'/'.join(co)} ({n} night{'s' if n != 1 else ''})\n"
        f"Guests    : {booking['guests']}\n"
        f"Room total: {int(booking['room_charge']):,} VND\n"
        f"Deposit   : {int(booking['deposit_amount']):,} VND\n\n"
        f"Check-in is from {ctx.cfg.checkin_hour}:00 with your ID or passport. "
        f"Check-out is by {ctx.cfg.late_checkout_hour}:00.\n\nGrand Horizon Hotel")
    return subject, body


def send_booking_confirmation(ctx: Context, booking, email: str | None) -> str | None:
    """Try up to `email_max_attempts` times. The outcome is stored in `notification`.
    A failure never undoes the confirmation (UC-15 exception 3a); the caller shows MSG-55.
    Returns 'SENT', 'FAILED', or None when the guest has no email (walk-in)."""
    if not email:
        return None
    subject, body = confirmation_body(ctx, booking)
    note = Notification(booking["booking_id"], "BOOKING_CONFIRMATION", email)
    note.retry(ctx.mailer, subject, body, ctx.now(), ctx.cfg.email_max_attempts)  # BR-35
    ctx.db.insert(
        "INSERT INTO notification(booking_id,type,recipient_email,status,attempts,last_attempt_at)"
        " VALUES (?,?,?,?,?,?)",
        (note.booking_id, note.type, email, note.status.value, note.attempts, ctx.now_s()))
    return note.status.value

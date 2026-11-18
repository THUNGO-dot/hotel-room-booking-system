"""Payments and refunds (UC-09, FR-20, FR-22, BR-23)."""
from __future__ import annotations

from ..context import Context
from ..domain import Payment, Refund
from ..errors import ServiceError
from .common import audit, require_role

METHODS = ("CASH", "CARD", "E_WALLET")


def paid_total(ctx: Context, booking_id: int) -> int:
    return int(ctx.db.scalar("SELECT COALESCE(SUM(amount),0) FROM payment WHERE booking_id=?", (booking_id,)))


def record(ctx: Context, booking_id: int, amount: int, method: str, purpose: str, ref: str = "") -> int:
    return ctx.db.insert(
        "INSERT INTO payment(booking_id,amount,method,purpose,transaction_ref,paid_at) VALUES (?,?,?,?,?,?)",
        (booking_id, int(amount), method, purpose, ref, ctx.now_s()))


def pay_deposit(ctx: Context, booking, method: str, actor=None, decline: bool = False) -> int:
    """Customer pays online (card / e-wallet); the receptionist may also take cash (FR-20).
    The amount is whatever is still missing from the deposit (BR-13); BR-24 forbids
    paying less than the minimum deposit."""
    from .bookings import expire_holds, get_booking
    if method not in METHODS:
        raise ServiceError("INVALID_INPUT", detail="Choose a payment method.")
    if actor is not None and actor["role"] == "CUSTOMER" and method == "CASH":
        raise ServiceError("INVALID_INPUT", detail="Choose a bank card or an e-wallet.")
    expire_holds(ctx)
    b = get_booking(ctx, booking["booking_code"])
    if b["status"] == "EXPIRED":
        raise ServiceError("HOLD_EXPIRED")
    if b["status"] != "PENDING":
        raise ServiceError("NOT_ALLOWED_STATE", status=b["status"].replace("_", " ").lower())
    due = int(b["deposit_amount"]) - int(b["paid"])
    if due <= 0:
        raise ServiceError("INVALID_INPUT", detail="The deposit for this booking is already paid.")
    payment = Payment(b["booking_id"], due, method, "DEPOSIT")
    payment.process(ctx.gateway, decline)  # MSG-41 on a refusal: nothing is saved, the hold stays
    with ctx.db.transaction():
        pid = record(ctx, b["booking_id"], due, method, "DEPOSIT", payment.transaction_ref)
        if actor is not None and actor["role"] == "RECEPTIONIST":
            audit(ctx, actor["account_id"], "RECORD_DEPOSIT", "booking", b["booking_id"], f"{due} by {method}")
    return pid


def create_refund(ctx: Context, booking_id: int, rate: int, reason: str, completed: bool) -> int | None:
    """One refund per booking at most (BR-12). Returns None when nothing was paid."""
    paid = paid_total(ctx, booking_id)
    if paid <= 0:
        return None
    refund = Refund(booking_id, reason=reason[:200])
    refund.calculate_refund(paid, rate=rate)
    refund.settle(completed)
    return ctx.db.insert(
        "INSERT INTO refund(booking_id,amount,refund_rate,reason,status,refunded_at) VALUES (?,?,?,?,?,?)",
        (booking_id, refund.amount, refund.refund_rate, refund.reason, refund.status.value, ctx.now_s()))


def create_refund_amount(ctx: Context, booking_id: int, amount: int, reason: str) -> int:
    """A refund of an exact amount that is paid out on the spot (used at check-out)."""
    refund = Refund(booking_id, int(amount), 100, reason[:200], "COMPLETED")
    return ctx.db.insert(
        "INSERT INTO refund(booking_id,amount,refund_rate,reason,status,refunded_at) VALUES (?,?,?,?,?,?)",
        (booking_id, refund.amount, refund.refund_rate, refund.reason, refund.status.value, ctx.now_s()))


def pending_refunds(ctx: Context):
    return ctx.db.all(
        "SELECT f.*, b.booking_code, b.guest_name FROM refund f JOIN booking b USING(booking_id)"
        " WHERE f.status='PENDING' ORDER BY f.refunded_at")


def complete_refund(ctx: Context, actor, refund_id: int) -> None:
    """FR-22: the receptionist records that the money went back to the guest (MSG-44)."""
    require_role(actor, "RECEPTIONIST")
    row = ctx.db.one("SELECT * FROM refund WHERE refund_id=?", (refund_id,))
    if row is None:
        raise ServiceError("NOT_FOUND")
    refund = Refund(row["booking_id"], row["amount"], row["refund_rate"], row["reason"], row["status"],
                    row["refunded_at"], row["refund_id"])
    refund.mark_completed()
    with ctx.db.transaction():
        ctx.db.execute("UPDATE refund SET status=? WHERE refund_id=?", (refund.status.value, refund_id))
        audit(ctx, actor["account_id"], "COMPLETE_REFUND", "refund", refund_id, f"{row['amount']} VND")

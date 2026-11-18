"""Front desk: check-in, room assignment, extra services, check-out, invoice
(UC-07, UC-08, UC-09, FR-16..21, BR-18..27)."""
from __future__ import annotations

from ..context import Context
from ..domain import (AddOnService, Booking, BookingStatus, Invoice, Payment, Room, ServiceUsage)
from ..errors import ServiceError
from . import bookings, catalog, payments
from .common import audit, require_role


# ------------------------------------------------------------------ check-in
def available_rooms(ctx: Context, type_id: int):
    return ctx.db.all(
        "SELECT * FROM room WHERE room_type_id=? AND active=1 AND status='AVAILABLE' ORDER BY room_number",
        (type_id,))


def check_in(ctx: Context, actor, code: str, id_document: str, room_id) -> "sqlite3.Row":  # noqa: F821
    """UC-07: Confirmed booking, arrival date reached, ID recorded, room of the booked type."""
    require_role(actor, "RECEPTIONIST")
    bookings.expire_holds(ctx)
    b = bookings.get_booking(ctx, code)
    if b is None:
        raise ServiceError("NOT_FOUND")
    booking = Booking.from_row(b)
    with ctx.db.transaction():
        room = None
        try:
            row = ctx.db.one("SELECT * FROM room WHERE room_id=? AND active=1", (int(room_id),))
            room = Room.from_row(row) if row else None
        except (TypeError, ValueError):
            pass
        booking.check_in(room, id_document, ctx.today())  # MSG-31 / ID needed / MSG-32
        ctx.db.execute("UPDATE booking SET status=?, room_id=?, id_document=? WHERE booking_id=?",
                       (booking.status.value, booking.room_id, booking.id_document, b["booking_id"]))
        ctx.db.execute("UPDATE room SET status=? WHERE room_id=?", (room.status.value, room.room_id))
        audit(ctx, actor["account_id"], "CHECK_IN", "booking", b["booking_id"], f"{code} -> room {room.room_number}")
    return bookings.get_booking(ctx, code)


def change_room(ctx: Context, actor, code: str, new_room_id) -> "sqlite3.Row":  # noqa: F821
    """FR-17: move a checked-in guest. Old room -> Cleaning, new room -> Occupied (MSG-34)."""
    require_role(actor, "RECEPTIONIST")
    b = bookings.get_booking(ctx, code)
    if b is None:
        raise ServiceError("NOT_FOUND")
    Booking.from_row(b).require_status(BookingStatus.CHECKED_IN)
    with ctx.db.transaction():
        try:
            rid = int(new_room_id)
        except (TypeError, ValueError):
            raise ServiceError("NO_ROOM_CHECKIN")
        row = ctx.db.one("SELECT * FROM room WHERE room_id=? AND active=1", (rid,))
        room = Room.from_row(row) if row else None
        if room is None or not room.can_host(b["room_type_id"]):
            raise ServiceError("NO_ROOM_CHECKIN")
        old = Room.from_row(ctx.db.one("SELECT * FROM room WHERE room_id=?", (b["room_id"],)))
        old.vacate()
        room.occupy()
        ctx.db.execute("UPDATE room SET status=? WHERE room_id=?", (old.status.value, old.room_id))
        ctx.db.execute("UPDATE room SET status=? WHERE room_id=?", (room.status.value, room.room_id))
        ctx.db.execute("UPDATE booking SET room_id=? WHERE booking_id=?", (rid, b["booking_id"]))
        audit(ctx, actor["account_id"], "CHANGE_ROOM", "booking", b["booking_id"],
              f"{code}: {b['room_number']} -> {room.room_number}")
    return bookings.get_booking(ctx, code)


# ------------------------------------------------------------ extra services
def add_service(ctx: Context, actor, code: str, service_id, quantity) -> int:
    """FR-18. The unit price is frozen on the line, so later price changes do not touch this bill."""
    require_role(actor, "RECEPTIONIST")
    b = bookings.get_booking(ctx, code)
    if b is None:
        raise ServiceError("NOT_FOUND")
    Booking.from_row(b).require_status(BookingStatus.CHECKED_IN)
    try:
        qty, sid = int(quantity), int(service_id)
    except (TypeError, ValueError):
        raise ServiceError("INVALID_INPUT", detail="Choose a service and enter a quantity.")
    if not 1 <= qty <= 20:
        raise ServiceError("INVALID_INPUT", detail="Quantity is 1 to 20.")
    row = ctx.db.one("SELECT * FROM add_on_service WHERE service_id=? AND active=1", (sid,))
    if row is None:
        raise ServiceError("NOT_FOUND")
    svc = AddOnService.from_row(row)
    line = ServiceUsage(b["booking_id"], svc.service_id, qty, svc.unit_price)  # unit price frozen here
    with ctx.db.transaction():
        uid = ctx.db.insert(
            "INSERT INTO service_usage(booking_id,service_id,quantity,unit_price,used_at) VALUES (?,?,?,?,?)",
            (line.booking_id, line.service_id, line.quantity, line.unit_price, ctx.now_s()))
        audit(ctx, actor["account_id"], "ADD_SERVICE", "booking", b["booking_id"], f"{svc.name} x{qty}")
    return uid


def usages(ctx: Context, booking_id: int):
    return ctx.db.all(
        "SELECT u.*, s.name FROM service_usage u JOIN add_on_service s USING(service_id)"
        " WHERE booking_id=? ORDER BY usage_id", (booking_id,))


# -------------------------------------------------------------------- billing
def is_late(ctx: Context, booking) -> bool:
    """BR-20: leaving after 12:00 on (or after) the check-out date."""
    return Booking.from_row(booking).is_late(ctx.now(), ctx.cfg.late_checkout_hour)


def compute_bill(ctx: Context, booking, late: bool) -> dict:
    """Room + services + late fee, plus 10% VAT, minus what is already paid (UC-08 step 3)."""
    lines = usages(ctx, booking["booking_id"])
    bill = Booking.from_row(booking).calculate_bill(ctx.cfg, [ServiceUsage.from_row(u) for u in lines], late)
    bill["lines"] = lines  # the rows (with the service name) are what the screens and the PDF print
    return bill


def check_out(ctx: Context, actor, code: str, method: str | None, late: bool,
              decline: bool = False):
    """UC-08 + UC-09. One transaction: payment, invoice, booking Completed, room Cleaning (NFR-07).
    Returns the invoice row."""
    require_role(actor, "RECEPTIONIST")
    b = bookings.get_booking(ctx, code)
    if b is None:
        raise ServiceError("NOT_FOUND")
    booking = Booking.from_row(b)
    booking.require_status(BookingStatus.CHECKED_IN)
    bill = compute_bill(ctx, b, late)
    balance = bill["balance"]
    reference = ""
    if balance > 0:
        if method not in payments.METHODS:
            raise ServiceError("UNPAID_BALANCE")  # MSG-38: cannot leave with a balance
        reference = Payment(b["booking_id"], balance, method, "BALANCE").process(ctx.gateway, decline).transaction_ref
    with ctx.db.transaction():
        if balance > 0:
            payments.record(ctx, b["booking_id"], balance, method, "BALANCE", reference)
        elif balance < 0:
            # the deposit was larger than the final bill (e.g. the stay was shortened): give it back
            payments.create_refund_amount(ctx, b["booking_id"], -balance, "Deposit exceeded the final bill")
        inv_no = bookings.next_code(ctx, "INV")
        inv = Invoice(inv_no, b["booking_id"], ctx.now_s()).generate(bill)
        ctx.db.insert(
            "INSERT INTO invoice(invoice_no,booking_id,issued_at,room_charge,service_charge,late_fee,"
            "vat_amount,total_amount) VALUES (?,?,?,?,?,?,?,?)",
            (inv.invoice_no, inv.booking_id, inv.issued_at, inv.room_charge, inv.service_charge, inv.late_fee,
             inv.vat_amount, inv.total_amount))
        room = Room.from_row(ctx.db.one("SELECT * FROM room WHERE room_id=?", (b["room_id"],)))
        booking.check_out(room)  # Completed, and the room goes to Cleaning
        ctx.db.execute("UPDATE booking SET status=? WHERE booking_id=?", (booking.status.value, b["booking_id"]))
        ctx.db.execute("UPDATE room SET status=? WHERE room_id=?", (room.status.value, room.room_id))
        audit(ctx, actor["account_id"], "CHECK_OUT", "booking", b["booking_id"], f"{code} -> {inv_no}")
    return get_invoice(ctx, inv_no)


# -------------------------------------------------------------------- invoices
def get_invoice(ctx: Context, invoice_no: str):
    return ctx.db.one(
        "SELECT i.*, b.booking_code, b.guest_name, b.guest_phone, b.check_in_date, b.check_out_date,"
        " b.account_id, b.booking_id AS bid, t.name AS type_name, r.room_number"
        " FROM invoice i JOIN booking b ON b.booking_id=i.booking_id"
        " JOIN room_type t ON t.room_type_id=b.room_type_id LEFT JOIN room r ON r.room_id=b.room_id"
        " WHERE i.invoice_no=?", (invoice_no,))


def get_invoice_for_booking(ctx: Context, booking_id: int):
    row = ctx.db.one("SELECT invoice_no FROM invoice WHERE booking_id=?", (booking_id,))
    return get_invoice(ctx, row["invoice_no"]) if row else None


def rooms_board(ctx: Context):
    return catalog.list_rooms(ctx)

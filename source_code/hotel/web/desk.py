"""Receptionist screens: SCR-13..SCR-18 (+ room board for FR-24)."""
from __future__ import annotations

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from ..app import paginate
from ..errors import ServiceError
from ..services import bookings, catalog, frontdesk, payments
from .helpers import page_number, page_size
from .security import roles

bp = Blueprint("desk", __name__, url_prefix="/desk")


def _email_note(code: str, status: str | None) -> None:
    if status == "FAILED":
        flash(ServiceError("EMAIL_FAILED", n=g.ctx.cfg.email_max_attempts).message, "bad")   # MSG-55
    elif status == "SENT":
        flash(f"Booking {code} confirmed. A confirmation email was sent to the customer.", "ok")
    else:
        flash(f"Booking {code} confirmed.", "ok")


# ----------------------------------------------------------------------- SCR-13
@bp.route("/")
@roles("RECEPTIONIST")
def dashboard():
    return render_template("desk/dashboard.html", counts=bookings.counters(g.ctx),
                           pending=bookings.list_pending(g.ctx)[:5], arrivals=bookings.arrivals(g.ctx)[:5])


# ----------------------------------------------------------------------- SCR-14
@bp.route("/pending")
@roles("RECEPTIONIST")
def pending():
    rows = bookings.list_pending(g.ctx)
    return render_template("desk/pending.html", p=paginate(rows, page_number(), page_size()),
                           refunds=payments.pending_refunds(g.ctx))


@bp.post("/bookings/<code>/confirm")
@roles("RECEPTIONIST")
def confirm(code: str):
    try:
        b, status = bookings.confirm_booking(g.ctx, g.user, code)
        _email_note(code, status)
    except ServiceError as e:
        flash(e.message, "bad")
    return redirect(request.form.get("back") or url_for("desk.pending"))


@bp.post("/bookings/<code>/reject")
@roles("RECEPTIONIST")
def reject(code: str):
    try:
        bookings.reject_booking(g.ctx, g.user, code, request.form.get("reason", ""))
        flash(f"Booking {code} rejected. The customer is told and the deposit is queued for refund.", "info")
    except ServiceError as e:
        flash(e.message, "bad")
    return redirect(url_for("desk.pending"))


@bp.post("/bookings/<code>/deposit")
@roles("RECEPTIONIST")
def record_deposit(code: str):
    """FR-20: the receptionist records a deposit paid at the desk."""
    try:
        b = bookings.get_booking(g.ctx, code)
        if b is None:
            raise ServiceError("NOT_FOUND")
        payments.pay_deposit(g.ctx, b, request.form.get("method", "CASH"), g.user)
        flash(f"Deposit recorded for {code}.", "ok")
    except ServiceError as e:
        flash(e.message, "bad")
    return redirect(url_for("desk.pending"))


@bp.post("/bookings/<code>/cancel")
@roles("RECEPTIONIST")
def cancel(code: str):
    try:
        res = bookings.cancel_booking(g.ctx, g.user, code)
        flash(f"Booking {code} cancelled. Refund {res['rate']}%: {res['amount']:,} VND recorded.", "ok")
    except ServiceError as e:
        flash(e.message, "bad")
    return redirect(request.form.get("back") or url_for("desk.search"))


@bp.post("/refunds/<int:refund_id>/complete")
@roles("RECEPTIONIST")
def complete_refund(refund_id: int):
    try:
        payments.complete_refund(g.ctx, g.user, refund_id)
        flash("Refund recorded.", "ok")                         # MSG-44
    except ServiceError as e:
        flash(e.message, "bad")
    return redirect(url_for("desk.pending"))


@bp.route("/bookings")
@roles("RECEPTIONIST")
def search():
    """Find any booking by code, name or phone, for cancellations (FR-12)."""
    q = (request.args.get("q") or "").strip()
    rows = []
    if q:
        like = f"%{q.lower()}%"
        rows = g.ctx.db.all(
            bookings.BASE + " WHERE lower(b.booking_code) LIKE ? OR lower(b.guest_name) LIKE ?"
            " OR replace(b.guest_phone,' ','') LIKE ? ORDER BY b.booking_id DESC",
            (like, like, f"%{q.replace(' ', '')}%"))
    return render_template("desk/search.html", q=q, p=paginate(rows, page_number(), page_size()))


# ----------------------------------------------------------------------- SCR-15
@bp.route("/walkin", methods=["GET", "POST"])
@roles("RECEPTIONIST")
def walkin():
    error, f = None, request.form
    types = catalog.list_room_types(g.ctx)
    if request.method == "POST":
        try:
            b = bookings.create_walk_in(g.ctx, g.user, f.get("name", ""), f.get("phone", ""),
                                        int(f.get("type") or 0), f.get("nights"), f.get("guests"))
            flash(f"Walk-in booking {b['booking_code']} confirmed. It is ready for check-in.", "ok")
            return redirect(url_for("desk.checkin_form", code=b["booking_code"]))
        except ValueError:
            error = ServiceError("INVALID_INPUT", detail="Choose a room type.")
        except ServiceError as e:
            error = e
    return render_template("desk/walkin.html", error=error, f=f, types=types)


# ----------------------------------------------------------------------- SCR-16
@bp.route("/checkin")
@roles("RECEPTIONIST")
def checkin_list():
    q = request.args.get("q", "")
    rows = bookings.arrivals(g.ctx, q)
    return render_template("desk/checkin_list.html", q=q, p=paginate(rows, page_number(), page_size()))


@bp.route("/checkin/<code>", methods=["GET", "POST"])
@roles("RECEPTIONIST")
def checkin_form(code: str):
    b = bookings.get_booking(g.ctx, code)
    if b is None:
        raise ServiceError("NOT_FOUND")
    error = None
    if request.method == "POST":
        try:
            out = frontdesk.check_in(g.ctx, g.user, code, request.form.get("id_document", ""), request.form.get("room"))
            flash(f"{out['guest_name']} is in room {out['room_number']}.", "ok")
            return redirect(url_for("desk.inhouse"))
        except ServiceError as e:
            error = e
    return render_template("desk/checkin_form.html", b=b, error=error, rooms=frontdesk.available_rooms(g.ctx, b["room_type_id"]),
                           f=request.form)


@bp.route("/inhouse/<code>/room", methods=["GET", "POST"])
@roles("RECEPTIONIST")
def change_room(code: str):
    b = bookings.get_booking(g.ctx, code)
    if b is None:
        raise ServiceError("NOT_FOUND")
    error = None
    if request.method == "POST":
        try:
            out = frontdesk.change_room(g.ctx, g.user, code, request.form.get("room"))
            flash(f"Room changed to {out['room_number']}. The old room is now marked Cleaning.", "ok")   # MSG-34
            return redirect(url_for("desk.inhouse"))
        except ServiceError as e:
            error = e
    return render_template("desk/change_room.html", b=b, error=error,
                           rooms=frontdesk.available_rooms(g.ctx, b["room_type_id"]))


# ----------------------------------------------------------------------- SCR-17
@bp.route("/inhouse")
@roles("RECEPTIONIST")
def inhouse():
    rows = bookings.inhouse(g.ctx)
    bills = {r["booking_code"]: frontdesk.compute_bill(g.ctx, r, False) for r in rows}
    return render_template("desk/inhouse.html", rows=rows, bills=bills)


@bp.route("/services/<code>", methods=["GET", "POST"])
@roles("RECEPTIONIST")
def services(code: str):
    b = bookings.get_booking(g.ctx, code)
    if b is None:
        raise ServiceError("NOT_FOUND")
    error = None
    if request.method == "POST":
        try:
            frontdesk.add_service(g.ctx, g.user, code, request.form.get("service"), request.form.get("quantity", 1))
            flash("Service added to the guest's bill.", "ok")
            return redirect(url_for("desk.services", code=code))
        except ServiceError as e:
            error = e
    b = bookings.get_booking(g.ctx, code)
    return render_template("desk/services.html", b=b, error=error, lines=frontdesk.usages(g.ctx, b["booking_id"]),
                           catalog=catalog.list_services(g.ctx), bill=frontdesk.compute_bill(g.ctx, b, False))


# ----------------------------------------------------------------------- SCR-18
@bp.route("/checkout/<code>", methods=["GET", "POST"])
@roles("RECEPTIONIST")
def checkout(code: str):
    b = bookings.get_booking(g.ctx, code)
    if b is None:
        raise ServiceError("NOT_FOUND")
    if b["status"] != "CHECKED_IN":
        flash("This guest is not checked in.", "bad")
        return redirect(url_for("desk.inhouse"))
    if request.method == "POST":
        late = request.form.get("late") == "1"
    elif "recalc" in request.args:                 # the tick box on the page was toggled
        late = request.args.get("late") == "1"
    else:                                          # first visit: suggest from the clock (BR-20)
        late = frontdesk.is_late(g.ctx, b)
    error = None
    if request.method == "POST":
        try:
            decline = bool(request.form.get("decline")) and g.ctx.cfg.demo_mode
            inv = frontdesk.check_out(g.ctx, g.user, code, request.form.get("method"), late, decline)
            flash(f"Check-out complete. {inv['invoice_no']} issued. Room {b['room_number']} is now Cleaning.", "ok")
            return redirect(url_for("common.invoice", invoice_no=inv["invoice_no"]))
        except ServiceError as e:
            error = e
    return render_template("desk/checkout.html", b=b, bill=frontdesk.compute_bill(g.ctx, b, late), late=late, error=error,
                           method=request.form.get("method", "CASH"))


# ---------------------------------------------------------------- room board
@bp.route("/rooms")
@roles("RECEPTIONIST")
def rooms():
    return render_template("desk/rooms.html", rooms=catalog.list_rooms(g.ctx))


@bp.post("/rooms/<int:room_id>/status")
@roles("RECEPTIONIST")
def room_status(room_id: int):
    try:
        catalog.set_room_status(g.ctx, g.user, room_id, request.form.get("status", ""))
        flash("Room status updated.", "ok")
    except ServiceError as e:
        flash(e.message, "bad")
    return redirect(url_for("desk.rooms"))

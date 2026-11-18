"""Guest and Customer screens: SCR-01..SCR-12."""
from __future__ import annotations

from datetime import timedelta

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from ..app import paginate
from ..errors import ServiceError
from ..services import accounts, availability, bookings, catalog, payments
from ..services.common import deposit_for
from . import security
from .helpers import page_number, page_size, safe_next, to_int
from .security import HOME_FOR_ROLE, roles

bp = Blueprint("public", __name__)

POLICY = [
    ("Check-in", "From {ci}:00, with an ID or passport"),
    ("Check-out", "By {co}:00. Later departures pay half a night"),
    ("Deposit", "{dep}% of the room total, rounded to the nearest {round} VND, due within {hold} hours"),
    ("Cancellation", "100% refund at {full} hours or more before check-in, {half}% from {h2} to {full} hours, "
                     "none under {h2} hours"),
    ("Stay length", "Up to {nights} nights per booking"),
]


def policy():
    c = g.ctx.cfg
    vals = dict(ci=c.checkin_hour, co=c.late_checkout_hour, dep=round(c.deposit_rate * 100), round=f"{c.deposit_rounding:,}",
                hold=c.hold_hours, full=c.refund_full_hours, half=c.refund_half_rate, h2=c.refund_half_hours,
                nights=c.max_nights)
    return [(k, v.format(**vals)) for k, v in POLICY]


# ------------------------------------------------------------------ SCR-05/06/07
def search_params() -> dict:
    today = g.ctx.today()
    a = request.args
    return {
        "ci": a.get("ci") or (today + timedelta(days=1)).isoformat(),
        "co": a.get("co") or (today + timedelta(days=3)).isoformat(),
        "adults": to_int(a.get("adults"), 2, 1, 6), "children": to_int(a.get("children"), 0, 0, 4),
        "type": a.get("type") or "any", "price": a.get("price") or "any",
        "sort": "desc" if a.get("sort") == "desc" else "asc",
    }


@bp.route("/")
def home():
    q = search_params()
    results, error = [], None
    try:
        tid = int(q["type"]) if q["type"] != "any" else None
        results = availability.search(g.ctx, q["ci"], q["co"], q["adults"], q["children"], tid, q["price"], q["sort"])
    except ServiceError as e:
        error = e
    except ValueError:
        q["type"] = "any"
    nights = availability.validate_stay(g.ctx, q["ci"], q["co"]) if not error else 0
    types = catalog.list_room_types(g.ctx)
    max_cap = max((t["capacity"] for t in types), default=0)
    return render_template("customer/home.html", q=q, results=results, error=error, nights=nights, types=types,
                           party=q["adults"] + q["children"], max_cap=max_cap, min_date=g.ctx.today().isoformat())


@bp.route("/rooms/<int:type_id>")
def room_detail(type_id: int):
    t = catalog.get_room_type(g.ctx, type_id)
    if t is None or not t["active"]:
        raise ServiceError("NOT_FOUND")
    q = search_params()
    info, error = None, None
    try:
        rows = availability.search(g.ctx, q["ci"], q["co"], q["adults"], q["children"], type_id)
        info = rows[0] if rows else None
        if info is None:
            error = ServiceError("NO_PRICE", date=q["ci"])
    except ServiceError as e:
        error = e
    return render_template("customer/room_detail.html", t=t, amenities=catalog.amenity_list(t), q=q, info=info,
                           error=error, policy=policy())


# ----------------------------------------------------------------------- SCR-08
@bp.route("/book/<int:type_id>", methods=["GET", "POST"])
@roles("CUSTOMER")
def book(type_id: int):
    t = catalog.get_room_type(g.ctx, type_id)
    if t is None or not t["active"]:
        raise ServiceError("NOT_FOUND")
    src = request.form if request.method == "POST" else request.args
    q = {"ci": src.get("ci", ""), "co": src.get("co", ""), "adults": to_int(src.get("adults"), 2, 1, 6),
         "children": to_int(src.get("children"), 0, 0, 4), "note": src.get("note", "")}
    error, summary = None, None
    try:
        n = availability.validate_stay(g.ctx, q["ci"], q["co"])
        total = catalog.quote(g.ctx, type_id, q["ci"], q["co"])
        summary = {"nights": n, "total": total, "deposit": deposit_for(g.ctx, total),
                   "left": availability.rooms_left(g.ctx, type_id, q["ci"], q["co"]),
                   "capacity_error": availability.capacity_error(g.ctx, t, q["adults"] + q["children"])}
        if request.method == "POST":
            b = bookings.create_booking(g.ctx, g.user, type_id, q["ci"], q["co"], q["adults"], q["children"], q["note"])
            flash(f"Booking {b['booking_code']} created. The room is held until {b['hold_expires_at'][8:10]}/"
                  f"{b['hold_expires_at'][5:7]} {b['hold_expires_at'][11:16]}.", "ok")
            return redirect(url_for("public.pay", code=b["booking_code"]))
    except ServiceError as e:
        error = e
    return render_template("customer/book.html", t=t, q=q, summary=summary, error=error, back=request.args)


# ----------------------------------------------------------------------- SCR-09
@bp.route("/bookings/<code>/pay", methods=["GET", "POST"])
@roles("CUSTOMER")
def pay(code: str):
    b = bookings.load_for(g.ctx, g.user, code)
    due = int(b["deposit_amount"]) - int(b["paid"])
    error = None
    if b["status"] != "PENDING" or due <= 0:
        flash("There is no deposit to pay for this booking.", "info")
        return redirect(url_for("public.booking_detail", code=code))
    if request.method == "POST":
        decline = bool(request.form.get("decline")) and g.ctx.cfg.demo_mode
        try:
            payments.pay_deposit(g.ctx, b, request.form.get("method", ""), g.user, decline=decline)
            return redirect(url_for("public.confirmation", code=code))
        except ServiceError as e:
            error = e
    return render_template("customer/pay.html", b=b, due=due, error=error,
                           method=request.form.get("method", "CARD"))


@bp.route("/bookings/<code>/confirmation")
@roles("CUSTOMER")
def confirmation(code: str):
    b = bookings.load_for(g.ctx, g.user, code)
    return render_template("customer/confirmation.html", b=b)


# ------------------------------------------------------------------ SCR-11 / 12
@bp.route("/bookings")
@roles("CUSTOMER")
def booking_list():
    tab = request.args.get("tab", "all")
    if tab not in bookings.TABS:
        tab = "all"
    rows = bookings.list_for_customer(g.ctx, g.user["account_id"], tab)
    return render_template("customer/bookings.html", tab=tab, p=paginate(rows, page_number(), page_size()))


@bp.route("/bookings/<code>")
@roles("CUSTOMER")
def booking_detail(code: str):
    b = bookings.load_for(g.ctx, g.user, code)
    from ..services.common import hours_until_checkin, nights
    hours = hours_until_checkin(g.ctx, b["check_in_date"])
    live = b["status"] in ("PENDING", "CONFIRMED")
    invoice = None
    if b["status"] == "COMPLETED":
        from ..services import frontdesk
        invoice = frontdesk.get_invoice_for_booking(g.ctx, b["booking_id"])
    return render_template("customer/booking_detail.html", b=b, nights=nights(b["check_in_date"], b["check_out_date"]),
                           can_change=live and hours >= g.ctx.cfg.change_min_hours, live=live, invoice=invoice,
                           due=max(0, int(b["deposit_amount"]) - int(b["paid"])),
                           steps=["Booking created", "Deposit paid", "Confirmed by the hotel", "Stay completed"])


@bp.route("/bookings/<code>/change", methods=["GET", "POST"])
@roles("CUSTOMER")
def change(code: str):
    b = bookings.load_for(g.ctx, g.user, code)
    src = request.form if request.method == "POST" else request.args
    ci, co = src.get("ci") or b["check_in_date"], src.get("co") or b["check_out_date"]
    error, preview = None, None
    try:
        if request.method == "POST":
            res = bookings.change_dates(g.ctx, g.user, code, ci, co)
            flash(f"Dates changed. {res['note']}", "ok")
            if res["extra"]:
                return redirect(url_for("public.pay", code=code))
            return redirect(url_for("public.booking_detail", code=code))
        if (ci, co) != (b["check_in_date"], b["check_out_date"]):
            availability.validate_stay(g.ctx, ci, co)
            total = catalog.quote(g.ctx, b["room_type_id"], ci, co)
            preview = {"nights": availability.validate_stay(g.ctx, ci, co), "total": total,
                       "deposit": deposit_for(g.ctx, total),
                       "left": availability.rooms_left(g.ctx, b["room_type_id"], ci, co, b["booking_id"])}
    except ServiceError as e:
        error = e
    return render_template("customer/change.html", b=b, ci=ci, co=co, error=error, preview=preview)


@bp.route("/bookings/<code>/cancel", methods=["GET", "POST"])
@roles("CUSTOMER")
def cancel(code: str):
    b = bookings.load_for(g.ctx, g.user, code)
    if request.method == "POST":
        try:
            res = bookings.cancel_booking(g.ctx, g.user, code)
            note = (f"{res['amount']:,} VND will be refunded." if res["amount"]
                    else "The room was released." if not res["paid"] else "No refund applies this close to check-in.")
            flash(f"Booking {code} cancelled. {note}", "ok")
            return redirect(url_for("public.booking_list"))
        except ServiceError as e:
            flash(e.message, "bad")
            return redirect(url_for("public.booking_detail", code=code))
    if b["status"] not in ("PENDING", "CONFIRMED"):
        flash(ServiceError("CANNOT_CANCEL").message if b["status"] in ("CHECKED_IN", "COMPLETED")
              else "This booking is already closed.", "bad")
        return redirect(url_for("public.booking_detail", code=code))
    return render_template("customer/cancel.html", b=b, prev=bookings.refund_preview(g.ctx, b))


# ------------------------------------------------------------- SCR-01..04 accounts
def _after_login(acct):
    nxt = safe_next(request.values.get("next"))
    if acct["role"] != "CUSTOMER" and (not nxt or not nxt.startswith(("/desk", "/admin", "/invoices", "/profile"))):
        nxt = None
    return redirect(nxt or url_for(HOME_FOR_ROLE[acct["role"]]))


@bp.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if g.user:
        return redirect(url_for(HOME_FOR_ROLE[g.user["role"]]))
    if request.method == "POST":
        try:
            acct = accounts.login(g.ctx, request.form.get("email", ""), request.form.get("password", ""))
            security.sign_in(acct)
            flash(f"Welcome back, {acct['full_name']}.", "ok")
            return _after_login(acct)
        except ServiceError as e:
            error = e
    return render_template("auth/login.html", error=error, email=request.form.get("email", ""),
                           next=request.values.get("next", ""))


@bp.route("/register", methods=["GET", "POST"])
def register():
    error, f = None, request.form
    if request.method == "POST":
        try:
            aid = accounts.register(g.ctx, f.get("name", ""), f.get("email", ""), f.get("phone", ""), f.get("password", ""))
            security.sign_in(accounts.get(g.ctx, aid))
            flash("Account created. Welcome to Grand Horizon.", "ok")
            return _after_login(g.user or accounts.get(g.ctx, aid))
        except ServiceError as e:
            error = e
    return render_template("auth/register.html", error=error, f=f, next=request.values.get("next", ""))


@bp.route("/logout", methods=["POST"])
def logout():
    security.sign_out()
    flash("Signed out. See you again soon.", "info")
    return redirect(url_for("public.home"))


@bp.route("/forgot", methods=["GET", "POST"])
def forgot():
    error = None
    if request.method == "POST":
        try:
            email = request.form.get("email", "")
            accounts.request_reset(g.ctx, email)
            flash("If this email has an account, a reset code was sent. It works once and expires in "
                  f"{g.ctx.cfg.reset_code_minutes} minutes.", "info")
            return redirect(url_for("public.reset", email=email))
        except ServiceError as e:
            error = e
    return render_template("auth/forgot.html", error=error, email=request.form.get("email", ""))


@bp.route("/reset", methods=["GET", "POST"])
def reset():
    error, f = None, request.form
    email = f.get("email") or request.args.get("email", "")
    if request.method == "POST":
        try:
            accounts.reset_password(g.ctx, email, f.get("code", ""), f.get("password", ""))
            flash("Password updated. Sign in with your new password.", "ok")
            return redirect(url_for("public.login"))
        except ServiceError as e:
            error = e
    return render_template("auth/reset.html", error=error, email=email)


@bp.route("/profile", methods=["GET", "POST"])
@roles("CUSTOMER", "RECEPTIONIST", "ADMIN")
def profile():
    error, f = None, request.form
    if request.method == "POST":
        try:
            accounts.update_profile(g.ctx, g.user["account_id"], f.get("name", ""), f.get("phone", ""),
                                    f.get("current", ""), f.get("new", ""))
            flash("Profile updated.", "ok")
            return redirect(url_for("public.profile"))
        except ServiceError as e:
            error = e
    return render_template("customer/profile.html", error=error, f=f)

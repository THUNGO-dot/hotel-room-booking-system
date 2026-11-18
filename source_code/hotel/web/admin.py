"""Administrator screens: SCR-19..SCR-26 and the audit log."""
from __future__ import annotations

from datetime import date, timedelta

from flask import Blueprint, Response, flash, g, redirect, render_template, request, url_for

from ..app import paginate
from ..errors import ServiceError
from ..services import accounts, catalog, export, reports
from .helpers import page_number, page_size, to_int
from .security import roles

bp = Blueprint("admin", __name__, url_prefix="/admin")


def _month_arg() -> tuple[int, int]:
    today = g.ctx.today()
    raw = request.args.get("month", "")
    try:
        y, m = (int(x) for x in raw.split("-"))
        date(y, m, 1)
        return y, m
    except (ValueError, TypeError):
        return today.year, today.month


# ----------------------------------------------------------------------- SCR-19
@bp.route("/")
@roles("ADMIN")
def dashboard():
    y, m = _month_arg()
    today = g.ctx.today()
    this = date(today.year, today.month, 1)
    last = this - timedelta(days=1)
    months = [(f"{this:%Y-%m}", this.strftime("%B %Y")), (f"{last:%Y-%m}", last.strftime("%B %Y"))]
    data = reports.dashboard(g.ctx, g.user, y, m)
    peak = max([w["revenue"] for w in data["weeks"]] + [1])
    return render_template("admin/dashboard.html", d=data, months=months, current=f"{y}-{m:02d}",
                           metric="occ" if request.args.get("metric") == "occ" else "rev", peak=peak)


# ------------------------------------------------------------- SCR-25 / SCR-26
def _range_args() -> tuple[str, str, str]:
    today = g.ctx.today()
    start = request.args.get("start") or today.replace(day=1).isoformat()
    end = request.args.get("end") or today.isoformat()
    group = "month" if request.args.get("group") == "month" else "day"
    return start, end, group


def _report(kind: str):
    start, end, group = _range_args()
    fn = reports.revenue_report if kind == "revenue" else reports.occupancy_report
    error, rep = None, None
    try:
        rep = fn(g.ctx, g.user, start, end, group)
    except ServiceError as e:
        error = e
    fmt = request.args.get("format")
    if rep is not None and fmt in ("xlsx", "pdf"):
        from ..services.common import audit
        audit(g.ctx, g.user["account_id"], "EXPORT_REPORT", "report", None, f"{kind} {start}..{end} as {fmt}")
        try:
            if fmt == "xlsx":
                data, mime = export.to_xlsx(rep), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            else:
                data, mime = export.to_pdf(rep), "application/pdf"
            return Response(data, mimetype=mime, headers={
                "Content-Disposition": f'attachment; filename="{kind}-{start}-{end}.{fmt}"'})
        except ServiceError as e:                                  # MSG-58
            flash(e.message, "bad")
    peak = 1
    if rep:
        key = "net" if kind == "revenue" else "rate"
        peak = max([abs(r[key]) for r in rep["rows"]] + [1])
    return render_template(f"admin/report_{kind}.html", rep=rep, error=error, start=start, end=end, group=group, peak=peak)


@bp.route("/reports/revenue")
@roles("ADMIN")
def revenue():
    return _report("revenue")


@bp.route("/reports/occupancy")
@roles("ADMIN")
def occupancy():
    return _report("occupancy")


# ----------------------------------------------------------------------- SCR-22
ROOM_STATUS_OPTIONS = [("AVAILABLE", "Available"), ("CLEANING", "Cleaning"), ("MAINTENANCE", "Maintenance"),
                       ("OCCUPIED", "Occupied")]


@bp.route("/rooms")
@roles("ADMIN")
def rooms():
    rows = catalog.list_rooms(g.ctx)
    return render_template("admin/rooms.html", p=paginate(rows, page_number(), page_size()), total=len(rows))


@bp.route("/rooms/new", methods=["GET", "POST"])
@bp.route("/rooms/<int:room_id>", methods=["GET", "POST"])
@roles("ADMIN")
def room_form(room_id: int | None = None):
    room = catalog.get_room(g.ctx, room_id) if room_id else None
    if room_id and room is None:
        raise ServiceError("NOT_FOUND")
    error, f = None, request.form
    if request.method == "POST":
        try:
            catalog.save_room(g.ctx, g.user, room_id, f.get("number", ""), f.get("type"), f.get("floor"), f.get("status", ""))
            flash("Room saved." if room_id else "Room added.", "ok")
            return redirect(url_for("admin.rooms"))
        except ServiceError as e:
            error = e
    values = f if request.method == "POST" else ({
        "number": room["room_number"], "type": room["room_type_id"], "floor": room["floor"], "status": room["status"]
    } if room else {"floor": 1, "status": "AVAILABLE"})
    return render_template("admin/room_form.html", room=room, error=error, v=values, types=catalog.list_room_types(g.ctx),
                           statuses=ROOM_STATUS_OPTIONS)


@bp.post("/rooms/<int:room_id>/delete")
@roles("ADMIN")
def room_delete(room_id: int):
    try:
        catalog.delete_room(g.ctx, g.user, room_id)
        flash("Room removed.", "ok")
    except ServiceError as e:
        flash(e.message, "bad")
    return redirect(url_for("admin.rooms"))


# ------------------------------------------------------------- SCR-21 / SCR-23
@bp.route("/room-types")
@roles("ADMIN")
def room_types():
    types = catalog.list_room_types(g.ctx)
    rates = {t["room_type_id"]: catalog.current_rate(g.ctx, t["room_type_id"]) for t in types}
    return render_template("admin/room_types.html", types=types, rates=rates, amen=catalog.amenity_list)


@bp.route("/room-types/new", methods=["GET", "POST"])
@bp.route("/room-types/<int:type_id>", methods=["GET", "POST"])
@roles("ADMIN")
def room_type_form(type_id: int | None = None):
    t = catalog.get_room_type(g.ctx, type_id) if type_id else None
    if type_id and t is None:
        raise ServiceError("NOT_FOUND")
    error, f = None, request.form
    if request.method == "POST":
        try:
            catalog.save_room_type(g.ctx, g.user, type_id, f.get("name", ""), f.get("bed", ""), f.get("capacity"),
                                   f.get("area"), f.get("amenities", ""), f.get("description", ""))
            flash("Room type saved." if type_id else "Room type added. Now add its price.", "ok")
            return redirect(url_for("admin.room_types"))
        except ServiceError as e:
            error = e
    values = f if request.method == "POST" else ({
        "name": t["name"], "bed": t["bed_type"], "capacity": t["capacity"], "area": t["area_m2"],
        "amenities": t["amenities"], "description": t["description"]} if t else {"capacity": 2, "area": 30})
    return render_template("admin/room_type_form.html", t=t, error=error, v=values)


@bp.post("/room-types/<int:type_id>/delete")
@roles("ADMIN")
def room_type_delete(type_id: int):
    try:
        result = catalog.delete_room_type(g.ctx, g.user, type_id)
        flash(f"Room type {result}.", "ok")
    except ServiceError as e:
        flash(e.message, "bad")
    return redirect(url_for("admin.room_types"))


@bp.route("/room-types/<int:type_id>/prices", methods=["GET", "POST"])
@roles("ADMIN")
def prices(type_id: int):
    t = catalog.get_room_type(g.ctx, type_id)
    if t is None:
        raise ServiceError("NOT_FOUND")
    error, f = None, request.form
    edit_id = to_int(request.args.get("edit"), 0, 0, 10**9) or None
    if request.method == "POST":
        try:
            catalog.save_period(g.ctx, g.user, type_id, f.get("start", ""), f.get("end", ""), f.get("price"),
                                to_int(f.get("period_id"), 0, 0, 10**9) or None)
            flash("Price period saved. It applies to new bookings only.", "ok")
            return redirect(url_for("admin.prices", type_id=type_id))
        except ServiceError as e:
            error = e
    periods = catalog.list_periods(g.ctx, type_id)
    editing = next((p for p in periods if p["price_period_id"] == edit_id), None)
    return render_template("admin/prices.html", t=t, periods=periods, error=error, f=f, editing=editing)


@bp.post("/prices/<int:period_id>/delete")
@roles("ADMIN")
def price_delete(period_id: int):
    type_id = to_int(request.form.get("type_id"), 0, 0, 10**9)
    catalog.delete_period(g.ctx, g.user, period_id)
    flash("Price period removed.", "ok")
    return redirect(url_for("admin.prices", type_id=type_id))


# ----------------------------------------------------------------------- SCR-24
@bp.route("/services", methods=["GET", "POST"])
@roles("ADMIN")
def services():
    error, f = None, request.form
    edit_id = to_int(request.args.get("edit"), 0, 0, 10**9) or None
    if request.method == "POST":
        try:
            catalog.save_service(g.ctx, g.user, to_int(f.get("service_id"), 0, 0, 10**9) or None, f.get("name", ""),
                                 f.get("price"))
            flash("Service saved.", "ok")
            return redirect(url_for("admin.services"))
        except ServiceError as e:
            error = e
    rows = catalog.list_services(g.ctx)
    editing = next((s for s in rows if s["service_id"] == edit_id), None)
    return render_template("admin/services.html", rows=rows, error=error, f=f, editing=editing)


@bp.post("/services/<int:service_id>/remove")
@roles("ADMIN")
def service_remove(service_id: int):
    catalog.remove_service(g.ctx, g.user, service_id)
    flash("Service removed. Bills that already include it keep their price.", "ok")
    return redirect(url_for("admin.services"))


# ----------------------------------------------------------------------- SCR-20
@bp.route("/staff", methods=["GET", "POST"])
@roles("ADMIN")
def staff():
    error, f = None, request.form
    if request.method == "POST":
        try:
            temp = accounts.create_staff(g.ctx, g.user, f.get("name", ""), f.get("email", ""), f.get("phone", ""),
                                         f.get("role", ""))
            flash("Staff account created. A temporary password was emailed to " + f.get("email", "").strip().lower()
                  + (f" (demo: {temp})." if g.ctx.cfg.demo_mode else "."), "ok")
            return redirect(url_for("admin.staff"))
        except ServiceError as e:
            error = e
    rows = accounts.list_staff(g.ctx, g.user)
    return render_template("admin/staff.html", p=paginate(rows, page_number(), page_size()), error=error, f=f)


@bp.post("/staff/<int:account_id>/lock")
@roles("ADMIN")
def staff_lock(account_id: int):
    locked = request.form.get("locked") == "1"
    try:
        accounts.set_locked(g.ctx, g.user, account_id, locked)
        flash("Account locked." if locked else "Account unlocked.", "ok" if not locked else "info")
    except ServiceError as e:
        flash(e.message, "bad")
    return redirect(url_for("admin.staff"))


@bp.post("/staff/<int:account_id>/reset")
@roles("ADMIN")
def staff_reset(account_id: int):
    try:
        temp = accounts.reset_staff_password(g.ctx, g.user, account_id)
        flash("Password reset. A temporary password was emailed" + (f" (demo: {temp})." if g.ctx.cfg.demo_mode else "."), "ok")
    except ServiceError as e:
        flash(e.message, "bad")
    return redirect(url_for("admin.staff"))


# --------------------------------------------------------------------- audit log
@bp.route("/audit")
@roles("ADMIN")
def audit_log():
    rows = g.ctx.db.all(
        "SELECT l.*, a.full_name, a.role FROM audit_log l JOIN account a USING(account_id) ORDER BY l.log_id DESC")
    return render_template("admin/audit.html", p=paginate(rows, page_number(), page_size()))

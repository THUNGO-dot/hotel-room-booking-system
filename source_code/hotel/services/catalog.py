"""Catalog: room types, rooms, price periods, add-on services (UC-10, UC-11)."""
from __future__ import annotations

from ..context import Context
from ..domain import AddOnService, PricePeriod, Room, RoomType
from ..errors import ServiceError
from .common import audit, d, daterange, require_role, require_text

ROOM_STATUSES = ("AVAILABLE", "OCCUPIED", "CLEANING", "MAINTENANCE")
LIVE = ("PENDING", "CONFIRMED", "CHECKED_IN")  # statuses that occupy inventory (BR-07)


# ------------------------------------------------------------------ room types
def list_room_types(ctx: Context, active_only: bool = True):
    sql = ("SELECT t.*, (SELECT COUNT(*) FROM room r WHERE r.room_type_id=t.room_type_id AND r.active=1) AS rooms "
           "FROM room_type t")
    if active_only:
        sql += " WHERE t.active=1"
    return ctx.db.all(sql + " ORDER BY t.room_type_id")


def get_room_type(ctx: Context, type_id: int):
    return ctx.db.one("SELECT * FROM room_type WHERE room_type_id=?", (type_id,))


def amenity_list(row) -> list[str]:
    return [a.strip() for a in (row["amenities"] or "").split(",") if a.strip()]


def save_room_type(ctx: Context, actor, type_id: int | None, name: str, bed_type: str, capacity,
                   area_m2, amenities: str, description: str) -> int:
    require_role(actor, "ADMIN")
    name = require_text(name, "a name", 50)
    bed_type = require_text(bed_type, "the bed type", 30)
    try:
        capacity, area_m2 = int(capacity), int(area_m2)
    except (TypeError, ValueError):
        raise ServiceError("INVALID_INPUT", detail="Capacity and size must be whole numbers.")
    if not 1 <= capacity <= 8:
        raise ServiceError("INVALID_INPUT", detail="Capacity is 1 to 8 guests.")
    if area_m2 < 10:
        raise ServiceError("INVALID_INPUT", detail="Enter the size in square metres (at least 10).")
    clash = ctx.db.one("SELECT room_type_id FROM room_type WHERE lower(name)=lower(?)", (name,))
    if clash and clash["room_type_id"] != type_id:
        raise ServiceError("INVALID_INPUT", detail="A room type with this name already exists.")
    amen = ", ".join(a.strip() for a in (amenities or "").split(",") if a.strip())
    with ctx.db.transaction():
        if type_id:
            old = get_room_type(ctx, type_id)
            if old is None:
                raise ServiceError("NOT_FOUND")
            ctx.db.execute(
                "UPDATE room_type SET name=?, bed_type=?, capacity=?, area_m2=?, amenities=?, description=?"
                " WHERE room_type_id=?", (name, bed_type, capacity, area_m2, amen, description.strip(), type_id))
            audit(ctx, actor["account_id"], "UPDATE_ROOM_TYPE", "room_type", type_id, name)
            return type_id
        new_id = ctx.db.insert(
            "INSERT INTO room_type(name,bed_type,capacity,area_m2,amenities,description,active)"
            " VALUES (?,?,?,?,?,?,1)", (name, bed_type, capacity, area_m2, amen, description.strip()))
        audit(ctx, actor["account_id"], "CREATE_ROOM_TYPE", "room_type", new_id, name)
        return new_id


def delete_room_type(ctx: Context, actor, type_id: int) -> str:
    """BR-30: blocked while rooms or active/upcoming bookings use it. A type with only
    old bookings is deactivated (kept for the history) instead of erased."""
    require_role(actor, "ADMIN")
    t = get_room_type(ctx, type_id)
    if t is None:
        raise ServiceError("NOT_FOUND")
    rooms = ctx.db.scalar("SELECT COUNT(*) FROM room WHERE room_type_id=? AND active=1", (type_id,))
    live = ctx.db.scalar(
        "SELECT COUNT(*) FROM booking WHERE room_type_id=? AND status IN ('PENDING','CONFIRMED','CHECKED_IN')",
        (type_id,))
    if rooms or live:
        raise ServiceError("DELETE_BLOCKED")
    history = ctx.db.scalar("SELECT COUNT(*) FROM booking WHERE room_type_id=?", (type_id,))
    with ctx.db.transaction():
        if history:
            ctx.db.execute("UPDATE room_type SET active=0 WHERE room_type_id=?", (type_id,))
            result = "deactivated"
        else:
            ctx.db.execute("DELETE FROM price_period WHERE room_type_id=?", (type_id,))
            ctx.db.execute("DELETE FROM room WHERE room_type_id=?", (type_id,))
            ctx.db.execute("DELETE FROM room_type WHERE room_type_id=?", (type_id,))
            result = "deleted"
        audit(ctx, actor["account_id"], "DELETE_ROOM_TYPE", "room_type", type_id, f"{t['name']} ({result})")
    return result


# ------------------------------------------------------------------ price list
def list_periods(ctx: Context, type_id: int):
    return ctx.db.all("SELECT * FROM price_period WHERE room_type_id=? ORDER BY start_date", (type_id,))


def _check_period(ctx: Context, type_id: int, start: str, end: str, price, period_id: int | None):
    try:
        s, e = d(start), d(end)
    except (TypeError, ValueError):
        raise ServiceError("DATES_REQUIRED")
    try:
        price = int(price)
    except (TypeError, ValueError):
        raise ServiceError("BAD_PRICE")
    period = PricePeriod(type_id, s, e, price, period_id)
    period.validate()  # MSG-59 for a price <= 0, then the date order
    if any(period.overlaps(PricePeriod.from_row(r)) for r in list_periods(ctx, type_id)):
        raise ServiceError("PERIOD_OVERLAP")  # BR-31 / MSG-50
    return s.isoformat(), e.isoformat(), price


def save_period(ctx: Context, actor, type_id: int, start: str, end: str, price,
                period_id: int | None = None) -> int:
    require_role(actor, "ADMIN")
    if get_room_type(ctx, type_id) is None:
        raise ServiceError("NOT_FOUND")
    s, e, p = _check_period(ctx, type_id, start, end, price, period_id)
    with ctx.db.transaction():
        if period_id:
            ctx.db.execute("UPDATE price_period SET start_date=?, end_date=?, price_per_night=? WHERE price_period_id=?",
                           (s, e, p, period_id))
            audit(ctx, actor["account_id"], "UPDATE_PRICE", "price_period", period_id, f"{s}..{e} = {p}")
            return period_id
        new_id = ctx.db.insert(
            "INSERT INTO price_period(room_type_id,start_date,end_date,price_per_night) VALUES (?,?,?,?)",
            (type_id, s, e, p))
        audit(ctx, actor["account_id"], "CREATE_PRICE", "price_period", new_id, f"{s}..{e} = {p}")
        return new_id


def delete_period(ctx: Context, actor, period_id: int) -> None:
    require_role(actor, "ADMIN")
    with ctx.db.transaction():
        ctx.db.execute("DELETE FROM price_period WHERE price_period_id=?", (period_id,))
        audit(ctx, actor["account_id"], "DELETE_PRICE", "price_period", period_id)


def _priced_type(ctx: Context, type_id: int) -> RoomType:
    """A RoomType that carries its price periods, so it can answer get_price() and quote()."""
    return RoomType(type_id, "", 0, periods=[PricePeriod.from_row(r) for r in list_periods(ctx, type_id)])


def night_price(ctx: Context, type_id: int, day) -> int | None:
    return _priced_type(ctx, type_id).get_price(day)


def quote(ctx: Context, type_id: int, check_in, check_out) -> int:
    """Room charge for a stay = sum of the price that applies to each night."""
    return _priced_type(ctx, type_id).quote(check_in, check_out)


def current_rate(ctx: Context, type_id: int):
    """The rate shown on admin screens: the period covering today, else the next one."""
    today = ctx.today().isoformat()
    row = ctx.db.one(
        "SELECT price_per_night FROM price_period WHERE room_type_id=? AND end_date>=? ORDER BY start_date LIMIT 1",
        (type_id, today))
    return int(row[0]) if row else None


# ----------------------------------------------------------------------- rooms
def list_rooms(ctx: Context, include_inactive: bool = False):
    sql = ("SELECT r.*, t.name AS type_name FROM room r JOIN room_type t USING(room_type_id)")
    if not include_inactive:
        sql += " WHERE r.active=1"
    return ctx.db.all(sql + " ORDER BY r.room_number")


def get_room(ctx: Context, room_id: int):
    return ctx.db.one("SELECT r.*, t.name AS type_name FROM room r JOIN room_type t USING(room_type_id)"
                      " WHERE room_id=?", (room_id,))


def peak_future_demand(ctx: Context, type_id: int) -> int:
    """Most bookings of this type that overlap on any single future night (BR-30).
    Used to stop an admin taking so many rooms out of service that a booking we
    already promised could no longer be honoured."""
    today = ctx.today().isoformat()
    rows = ctx.db.all(
        "SELECT check_in_date, check_out_date FROM booking WHERE room_type_id=? AND check_out_date>?"
        " AND status IN ('PENDING','CONFIRMED','CHECKED_IN')", (type_id, today))
    if not rows:
        return 0
    counts: dict = {}
    for r in rows:
        for day in daterange(max(d(r["check_in_date"]), d(today)), r["check_out_date"]):
            counts[day] = counts.get(day, 0) + 1
    return max(counts.values(), default=0)


def rooms_in_service(ctx: Context, type_id: int | None = None) -> int:
    sql = "SELECT COUNT(*) FROM room WHERE active=1 AND status<>'MAINTENANCE'"
    params: tuple = ()
    if type_id is not None:
        sql += " AND room_type_id=?"
        params = (type_id,)
    return ctx.db.scalar(sql, params)


def _guard_capacity(ctx: Context, type_id: int, removing_one: bool = True) -> None:
    if removing_one and rooms_in_service(ctx, type_id) - 1 < peak_future_demand(ctx, type_id):
        raise ServiceError("DELETE_BLOCKED")


def save_room(ctx: Context, actor, room_id: int | None, room_number: str, type_id, floor, status: str) -> int:
    """UC-10: add or edit a room. Receptionists may only change the status (FR-24)."""
    require_role(actor, "ADMIN")
    number = require_text(room_number, "a room number", 10)
    try:
        type_id, floor = int(type_id), int(floor)
    except (TypeError, ValueError):
        raise ServiceError("INVALID_INPUT", detail="Choose a room type and enter the floor number.")
    if get_room_type(ctx, type_id) is None:
        raise ServiceError("NOT_FOUND")
    if status not in ROOM_STATUSES:
        raise ServiceError("INVALID_INPUT", detail="Choose a valid room status.")
    clash = ctx.db.one("SELECT room_id FROM room WHERE room_number=?", (number,))
    if clash and clash["room_id"] != room_id:
        raise ServiceError("INVALID_INPUT", detail="This room number already exists.")  # BR-28
    with ctx.db.transaction():
        if room_id:
            old = get_room(ctx, room_id)
            if old is None:
                raise ServiceError("NOT_FOUND")
            if old["status"] == "OCCUPIED" and status == "MAINTENANCE":
                raise ServiceError("OCCUPIED_MAINTENANCE")  # MSG-51
            if old["status"] == "OCCUPIED" and status != "OCCUPIED":
                raise ServiceError("INVALID_INPUT", detail="An occupied room changes status only at check-out.")
            if old["status"] != "OCCUPIED" and status == "OCCUPIED":
                raise ServiceError("INVALID_INPUT", detail="A room becomes occupied only through check-in.")
            leaving_service = (old["status"] != "MAINTENANCE" and status == "MAINTENANCE") or old["room_type_id"] != type_id
            if leaving_service:
                _guard_capacity(ctx, old["room_type_id"])
            ctx.db.execute("UPDATE room SET room_number=?, room_type_id=?, floor=?, status=? WHERE room_id=?",
                           (number, type_id, floor, status, room_id))
            audit(ctx, actor["account_id"], "UPDATE_ROOM", "room", room_id, f"{number} -> {status}")
            return room_id
        if status == "OCCUPIED":
            raise ServiceError("INVALID_INPUT", detail="A new room cannot start as occupied.")
        new_id = ctx.db.insert("INSERT INTO room(room_type_id,room_number,floor,status,active) VALUES (?,?,?,?,1)",
                               (type_id, number, floor, status))
        audit(ctx, actor["account_id"], "CREATE_ROOM", "room", new_id, number)
        return new_id


def set_room_status(ctx: Context, actor, room_id: int, status: str) -> None:
    """Receptionist (and admin) status changes such as Cleaning -> Available (FR-24)."""
    require_role(actor, "ADMIN", "RECEPTIONIST")
    room = get_room(ctx, room_id)
    if room is None:
        raise ServiceError("NOT_FOUND")
    domain_room = Room.from_row(room)
    domain_room.change_status(status)  # rejects a bad status and any change to an occupied room
    with ctx.db.transaction():
        if status == "MAINTENANCE" and room["status"] != "MAINTENANCE":
            _guard_capacity(ctx, room["room_type_id"])
        ctx.db.execute("UPDATE room SET status=? WHERE room_id=?", (domain_room.status.value, room_id))
        audit(ctx, actor["account_id"], "SET_ROOM_STATUS", "room", room_id, f"{room['room_number']} -> {status}")


def delete_room(ctx: Context, actor, room_id: int) -> None:
    """Rooms with history are deactivated, never erased (BR-30)."""
    require_role(actor, "ADMIN")
    room = get_room(ctx, room_id)
    if room is None:
        raise ServiceError("NOT_FOUND")
    if room["status"] == "OCCUPIED":
        raise ServiceError("DELETE_BLOCKED")
    with ctx.db.transaction():
        if room["status"] != "MAINTENANCE":
            _guard_capacity(ctx, room["room_type_id"])
        used = ctx.db.scalar("SELECT COUNT(*) FROM booking WHERE room_id=?", (room_id,))
        if used:
            ctx.db.execute("UPDATE room SET active=0 WHERE room_id=?", (room_id,))
        else:
            ctx.db.execute("DELETE FROM room WHERE room_id=?", (room_id,))
        audit(ctx, actor["account_id"], "DELETE_ROOM", "room", room_id, room["room_number"])


# --------------------------------------------------------------- add-on services
def list_services(ctx: Context, active_only: bool = True):
    sql = "SELECT * FROM add_on_service"
    if active_only:
        sql += " WHERE active=1"
    return ctx.db.all(sql + " ORDER BY service_id")


def save_service(ctx: Context, actor, service_id: int | None, name: str, price) -> int:
    require_role(actor, "ADMIN")
    name = require_text(name, "a service name", 50)
    try:
        price = int(price)
    except (TypeError, ValueError):
        raise ServiceError("BAD_PRICE")
    AddOnService(service_id or 0, name, price).validate()
    clash = ctx.db.one("SELECT service_id FROM add_on_service WHERE lower(name)=lower(?)", (name,))
    if clash and clash["service_id"] != service_id:
        raise ServiceError("INVALID_INPUT", detail="A service with this name already exists.")
    with ctx.db.transaction():
        if service_id:
            # Services already on a bill keep their frozen unit_price (service_usage.unit_price).
            ctx.db.execute("UPDATE add_on_service SET name=?, unit_price=?, active=1 WHERE service_id=?",
                           (name, price, service_id))
            audit(ctx, actor["account_id"], "UPDATE_SERVICE", "add_on_service", service_id, f"{name} = {price}")
            return service_id
        new_id = ctx.db.insert("INSERT INTO add_on_service(name,unit_price,active) VALUES (?,?,1)", (name, price))
        audit(ctx, actor["account_id"], "CREATE_SERVICE", "add_on_service", new_id, f"{name} = {price}")
        return new_id


def remove_service(ctx: Context, actor, service_id: int) -> None:
    require_role(actor, "ADMIN")
    row = ctx.db.one("SELECT * FROM add_on_service WHERE service_id=?", (service_id,))
    service = AddOnService.from_row(row) if row else None
    if service:
        service.deactivate()
    with ctx.db.transaction():
        ctx.db.execute("UPDATE add_on_service SET active=0 WHERE service_id=?", (service_id,))
        audit(ctx, actor["account_id"], "REMOVE_SERVICE", "add_on_service", service_id)

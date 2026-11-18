"""Room search and availability (UC-03, BR-06..08, FR-06/07)."""
from __future__ import annotations

from ..context import Context
from ..domain import RoomType
from ..errors import ServiceError
from . import catalog
from .common import d, deposit_for

LIVE = "('PENDING','CONFIRMED','CHECKED_IN')"


def validate_stay(ctx: Context, check_in: str | None, check_out: str | None) -> int:
    """Return the number of nights, or raise the matching MSG-14..17 error."""
    if not check_in or not check_out:
        raise ServiceError("DATES_REQUIRED")
    try:
        ci, co = d(check_in), d(check_out)
    except ValueError:
        raise ServiceError("DATES_REQUIRED")
    if ci < ctx.today():
        raise ServiceError("DATE_IN_PAST")
    n = (co - ci).days
    if n <= 0:
        raise ServiceError("DATE_ORDER")
    if n > ctx.cfg.max_nights:  # BR-06
        raise ServiceError("TOO_LONG", n=ctx.cfg.max_nights)
    return n


def overlapping(ctx: Context, type_id: int, check_in: str, check_out: str,
                exclude_booking_id: int | None = None) -> int:
    """Bookings of this type that hold a room on any night in [check_in, check_out)."""
    return ctx.db.scalar(
        f"SELECT COUNT(*) FROM booking WHERE room_type_id=? AND status IN {LIVE}"
        " AND check_in_date<? AND check_out_date>? AND booking_id<>COALESCE(?,-1)",
        (type_id, d(check_out).isoformat(), d(check_in).isoformat(), exclude_booking_id))


def rooms_left(ctx: Context, type_id: int, check_in: str, check_out: str,
               exclude_booking_id: int | None = None) -> int:
    """BR-07/08: rooms in service minus overlapping Pending/Confirmed/Checked-in bookings."""
    return max(0, catalog.rooms_in_service(ctx, type_id)
               - overlapping(ctx, type_id, check_in, check_out, exclude_booking_id))


def capacity_error(ctx: Context, room_type, guests: int) -> ServiceError | None:
    if not RoomType.from_row(room_type).can_host(guests):
        return ServiceError("TOO_MANY_GUESTS", name=room_type["name"], cap=room_type["capacity"], guests=guests)
    return None


def search(ctx: Context, check_in: str, check_out: str, adults: int = 2, children: int = 0,
           type_id: int | None = None, price: str = "any", sort: str = "asc") -> list[dict]:
    """Room types with their price for the whole stay. Raises MSG-14..17 for bad input.
    A type that cannot hold the party or is sold out is still listed but marked
    unavailable, exactly like the prototype."""
    n = validate_stay(ctx, check_in, check_out)
    guests = max(1, int(adults)) + max(0, int(children))
    out = []
    for t in catalog.list_room_types(ctx):
        if type_id and t["room_type_id"] != type_id:
            continue
        try:
            total = catalog.quote(ctx, t["room_type_id"], check_in, check_out)
        except ServiceError:
            continue  # no price for these dates: the type cannot be sold
        nightly = round(total / n)
        if price == "lo" and nightly >= 1_000_000:
            continue
        if price == "hi" and nightly < 1_000_000:
            continue
        left = rooms_left(ctx, t["room_type_id"], check_in, check_out)
        cap_err = capacity_error(ctx, t, guests)
        out.append({
            "type": t, "amenities": catalog.amenity_list(t), "nights": n, "total": total, "nightly": nightly,
            "deposit": deposit_for(ctx, total), "left": left,
            "capacity_error": cap_err.message if cap_err else "",
            "can_book": left > 0 and cap_err is None,
        })
    out.sort(key=lambda r: r["nightly"], reverse=(sort == "desc"))
    return out

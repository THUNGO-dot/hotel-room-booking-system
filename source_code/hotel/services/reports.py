"""Admin reports (UC-12, FR-27..29, BR-33, BR-34).

Revenue   = payments - refunds                        (BR-33)
Occupancy = occupied room-nights / (rooms in service x days) x 100   (BR-34)
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta

from ..context import Context
from ..errors import ServiceError
from . import catalog
from .common import d, require_role


def _parse_range(ctx: Context, start: str, end: str) -> tuple[date, date]:
    try:
        s, e = d(start), d(end)
    except (TypeError, ValueError):
        raise ServiceError("DATES_REQUIRED")
    if e < s:
        raise ServiceError("RANGE_INVALID")  # MSG-52
    return s, e


def buckets(start: date, end: date, group: str) -> list[tuple[str, date, date]]:
    """Split [start, end] (inclusive) into days or calendar months."""
    out = []
    if group == "month":
        cur = start
        while cur <= end:
            last = date(cur.year, cur.month, calendar.monthrange(cur.year, cur.month)[1])
            out.append((cur.strftime("%m/%Y"), cur, min(last, end)))
            cur = last + timedelta(days=1)
    else:
        cur = start
        while cur <= end:
            out.append((cur.strftime("%d/%m/%Y"), cur, cur))
            cur += timedelta(days=1)
    return out


def week_buckets(year: int, month: int) -> list[tuple[str, date, date]]:
    last = calendar.monthrange(year, month)[1]
    spans = [(1, 7), (8, 14), (15, 21), (22, last)]
    return [(f"Week {i + 1}", date(year, month, a), date(year, month, b)) for i, (a, b) in enumerate(spans)]


def _money(ctx: Context, table: str, col: str, s: date, e: date) -> int:
    return int(ctx.db.scalar(
        f"SELECT COALESCE(SUM(amount),0) FROM {table} WHERE date({col}) BETWEEN ? AND ?",
        (s.isoformat(), e.isoformat())))


def revenue_between(ctx: Context, s: date, e: date) -> dict:
    pay = _money(ctx, "payment", "paid_at", s, e)
    ref = _money(ctx, "refund", "refunded_at", s, e)
    return {"payments": pay, "refunds": ref, "net": pay - ref}


def revenue_by_type(ctx: Context, s: date, e: date) -> list[dict]:
    pay = {r["room_type_id"]: int(r["v"]) for r in ctx.db.all(
        "SELECT b.room_type_id, SUM(p.amount) v FROM payment p JOIN booking b USING(booking_id)"
        " WHERE date(p.paid_at) BETWEEN ? AND ? GROUP BY b.room_type_id", (s.isoformat(), e.isoformat()))}
    ref = {r["room_type_id"]: int(r["v"]) for r in ctx.db.all(
        "SELECT b.room_type_id, SUM(f.amount) v FROM refund f JOIN booking b USING(booking_id)"
        " WHERE date(f.refunded_at) BETWEEN ? AND ? GROUP BY b.room_type_id", (s.isoformat(), e.isoformat()))}
    rows = []
    for t in catalog.list_room_types(ctx, active_only=False):
        net = pay.get(t["room_type_id"], 0) - ref.get(t["room_type_id"], 0)
        if net or t["active"]:
            rows.append({"name": t["name"], "net": net})
    total = sum(r["net"] for r in rows) or 1
    for r in rows:
        r["share"] = round(r["net"] / total * 100, 1) if r["net"] else 0.0
    return sorted(rows, key=lambda r: -r["net"])


def occupied_nights(ctx: Context, s: date, e: date) -> int:
    """Room-nights actually used between s and e inclusive (checked-in or completed stays)."""
    rows = ctx.db.all(
        "SELECT check_in_date, check_out_date FROM booking WHERE status IN ('CHECKED_IN','COMPLETED')"
        " AND check_in_date<=? AND check_out_date>?", (e.isoformat(), s.isoformat()))
    total = 0
    for r in rows:
        lo, hi = max(d(r["check_in_date"]), s), min(d(r["check_out_date"]), e + timedelta(days=1))
        total += max(0, (hi - lo).days)
    return total


def occupancy_between(ctx: Context, s: date, e: date) -> dict:
    """Days are counted only up to today, so the running month is not diluted by future days."""
    end = min(e, ctx.today())
    days = max(0, (end - s).days + 1)
    rooms = catalog.rooms_in_service(ctx)
    nights = occupied_nights(ctx, s, end) if days else 0
    capacity = rooms * days
    return {"nights": nights, "rooms": rooms, "days": days, "capacity": capacity,
            "rate": round(nights / capacity * 100, 1) if capacity else 0.0}


# ------------------------------------------------------------------- reports
def revenue_report(ctx: Context, actor, start: str, end: str, group: str = "day") -> dict:
    require_role(actor, "ADMIN")
    s, e = _parse_range(ctx, start, end)
    group = "month" if group == "month" else "day"
    rows = []
    for label, a, b in buckets(s, e, group):
        r = revenue_between(ctx, a, b)
        rows.append({"label": label, **r})
    total = revenue_between(ctx, s, e)
    return {"kind": "revenue", "start": s, "end": e, "group": group, "rows": rows, "total": total,
            "by_type": revenue_by_type(ctx, s, e), "empty": not (total["payments"] or total["refunds"])}


def occupancy_report(ctx: Context, actor, start: str, end: str, group: str = "day") -> dict:
    require_role(actor, "ADMIN")
    s, e = _parse_range(ctx, start, end)
    group = "month" if group == "month" else "day"
    rows = []
    for label, a, b in buckets(s, e, group):
        rows.append({"label": label, **occupancy_between(ctx, a, b)})
    total = occupancy_between(ctx, s, e)
    return {"kind": "occupancy", "start": s, "end": e, "group": group, "rows": rows, "total": total,
            "empty": total["nights"] == 0}


def dashboard(ctx: Context, actor, year: int, month: int) -> dict:
    """SCR-19: KPIs for one calendar month, compared with the month before."""
    require_role(actor, "ADMIN")
    first = date(year, month, 1)
    last = date(year, month, calendar.monthrange(year, month)[1])
    prev_last = first - timedelta(days=1)
    prev_first = date(prev_last.year, prev_last.month, 1)
    cur, prev = revenue_between(ctx, first, last), revenue_between(ctx, prev_first, prev_last)
    change = None  # only a finished month is compared with the month before
    if last <= ctx.today() and prev["net"] > 0:
        change = round((cur["net"] - prev["net"]) / prev["net"] * 100, 1)
    weeks = []
    for label, a, b in week_buckets(year, month):
        weeks.append({"label": label, "revenue": revenue_between(ctx, a, b)["net"],
                      "occupancy": occupancy_between(ctx, a, b)["rate"]})
    return {"year": year, "month": month, "label": first.strftime("%B %Y"), "start": first, "end": last,
            "revenue": cur, "change": change, "occupancy": occupancy_between(ctx, first, last),
            "weeks": weeks, "by_type": revenue_by_type(ctx, first, last)}

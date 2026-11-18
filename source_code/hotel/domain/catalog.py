"""RoomType, PricePeriod, Room, AddOnService, ServiceUsage (class diagram, hotel inventory)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from ..errors import ServiceError
from ._util import get, to_date, to_dt
from .enums import LIVE_STATUSES, RoomStatus


# ----------------------------------------------------------------- PricePeriod
@dataclass
class PricePeriod:
    """A price per night that applies from `start_date` to `end_date` inclusive (UC-11)."""
    room_type_id: int
    start_date: date
    end_date: date
    price_per_night: int
    price_period_id: int | None = None

    @classmethod
    def from_row(cls, row) -> "PricePeriod":
        return cls(row["room_type_id"], to_date(row["start_date"]), to_date(row["end_date"]),
                   int(row["price_per_night"]), row["price_period_id"])

    def validate(self) -> None:
        if self.price_per_night <= 0:
            raise ServiceError("BAD_PRICE")  # MSG-59
        if self.end_date < self.start_date:
            raise ServiceError("RANGE_INVALID")

    def covers(self, day: date) -> bool:
        return self.start_date <= day <= self.end_date

    def overlaps(self, other: "PricePeriod") -> bool:
        """BR-31: two periods of the same room type may not share a night."""
        if self.room_type_id != other.room_type_id:
            return False
        if self.price_period_id is not None and self.price_period_id == other.price_period_id:
            return False
        return self.start_date <= other.end_date and self.end_date >= other.start_date


# -------------------------------------------------------------------- RoomType
@dataclass
class RoomType:
    room_type_id: int
    name: str
    capacity: int
    description: str = ""
    amenities: str = ""
    active: bool = True
    bed_type: str = ""
    area_m2: int = 0
    periods: list[PricePeriod] = field(default_factory=list)

    @classmethod
    def from_row(cls, row, periods=()) -> "RoomType":
        return cls(row["room_type_id"], row["name"], int(row["capacity"]), get(row, "description", ""),
                   get(row, "amenities", ""), bool(row["active"]), get(row, "bed_type", ""),
                   int(get(row, "area_m2", 0)), list(periods))

    def amenity_list(self) -> list[str]:
        return [a.strip() for a in (self.amenities or "").split(",") if a.strip()]

    def can_host(self, guests: int) -> bool:
        return guests <= self.capacity

    def get_price(self, day) -> int | None:
        """Price per night on `day`, or None when no price period covers it."""
        day = to_date(day)
        for p in self.periods:
            if p.covers(day):
                return p.price_per_night
        return None

    def quote(self, check_in, check_out) -> int:
        """Room charge for a stay = the price of each night added up."""
        total, cur, stop = 0, to_date(check_in), to_date(check_out)
        while cur < stop:
            price = self.get_price(cur)
            if price is None:
                raise ServiceError("NO_PRICE", date=cur.strftime("%d/%m/%Y"))
            total += int(price)
            cur += timedelta(days=1)
        return total


# ------------------------------------------------------------------------ Room
@dataclass
class Room:
    room_id: int
    room_type_id: int
    room_number: str
    floor: int = 1
    status: RoomStatus = RoomStatus.AVAILABLE
    active: bool = True

    def __post_init__(self):
        self.status = RoomStatus(self.status)

    @classmethod
    def from_row(cls, row) -> "Room":
        return cls(row["room_id"], row["room_type_id"], row["room_number"], int(get(row, "floor", 1)),
                   row["status"], bool(row["active"]))

    @property
    def in_service(self) -> bool:
        return self.active and self.status != RoomStatus.MAINTENANCE

    def can_host(self, room_type_id: int) -> bool:
        """Check-in rule (BR-18): an active, free room of the booked type."""
        return self.active and self.room_type_id == room_type_id and self.status == RoomStatus.AVAILABLE

    def is_available(self, from_date, to_date_, bookings=()) -> bool:
        """True when the room is in service and none of the given bookings that hold
        this room overlaps the stay [from_date, to_date_)."""
        if not self.in_service:
            return False
        start, end = to_date(from_date), to_date(to_date_)
        for b in bookings:
            if getattr(b, "room_id", None) != self.room_id or b.status not in LIVE_STATUSES:
                continue
            if b.check_in_date < end and b.check_out_date > start:
                return False
        return True

    def change_status(self, new_status) -> None:
        """FR-24: Available, Cleaning or Maintenance by hand. Occupied changes only through
        check-in and check-out."""
        if new_status not in ("AVAILABLE", "CLEANING", "MAINTENANCE"):
            raise ServiceError("INVALID_INPUT", detail="Choose Available, Cleaning or Maintenance.")
        if self.status == RoomStatus.OCCUPIED:
            raise ServiceError("OCCUPIED_MAINTENANCE" if new_status == "MAINTENANCE" else "INVALID_INPUT",
                               detail="An occupied room changes status only at check-out.")
        self.status = RoomStatus(new_status)

    def occupy(self) -> None:
        if self.status != RoomStatus.AVAILABLE:
            raise ServiceError("NO_ROOM_CHECKIN")
        self.status = RoomStatus.OCCUPIED

    def vacate(self) -> None:
        """Check-out or room change: the room goes to Cleaning."""
        self.status = RoomStatus.CLEANING


# ----------------------------------------------------------------- AddOnService
@dataclass
class AddOnService:
    service_id: int
    name: str
    unit_price: int
    active: bool = True

    @classmethod
    def from_row(cls, row) -> "AddOnService":
        return cls(row["service_id"], row["name"], int(row["unit_price"]), bool(row["active"]))

    def validate(self) -> None:
        if self.unit_price <= 0:
            raise ServiceError("BAD_PRICE")

    def deactivate(self) -> None:
        self.active = False


@dataclass
class ServiceUsage:
    """An extra service on a guest's bill. The unit price is frozen when it is added."""
    booking_id: int
    service_id: int
    quantity: int
    unit_price: int
    used_at: datetime | None = None
    usage_id: int | None = None
    name: str = ""

    @classmethod
    def from_row(cls, row) -> "ServiceUsage":
        return cls(row["booking_id"], row["service_id"], int(row["quantity"]), int(row["unit_price"]),
                   to_dt(row["used_at"]), get(row, "usage_id"), get(row, "name", ""))

    def __getitem__(self, key):  # so templates and the PDF export can read it like a row
        return getattr(self, key)

    def get_amount(self) -> int:
        return self.quantity * self.unit_price

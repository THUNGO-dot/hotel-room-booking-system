"""Booking: the life cycle Pending -> Confirmed -> Checked-in -> Completed (BR-09..22)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time

from ..errors import ServiceError
from ._util import get, status_text, to_date, to_dt
from .enums import BookingStatus


@dataclass
class Booking:
    booking_id: int
    booking_code: str
    room_type_id: int
    guest_name: str
    guest_phone: str
    check_in_date: date
    check_out_date: date
    guests: int
    status: BookingStatus
    room_charge: int
    deposit_amount: int
    hold_expires_at: datetime | None = None
    created_at: str = ""
    account_id: int | None = None
    room_id: int | None = None
    id_document: str | None = None
    special_request: str = ""
    paid: int = 0  # total of the payments made so far (loaded with the row)

    def __post_init__(self):
        self.status = BookingStatus(self.status)

    @classmethod
    def from_row(cls, row) -> "Booking":
        return cls(row["booking_id"], row["booking_code"], row["room_type_id"], row["guest_name"],
                   row["guest_phone"], to_date(row["check_in_date"]), to_date(row["check_out_date"]),
                   int(row["guests"]), row["status"], int(row["room_charge"]), int(row["deposit_amount"]),
                   to_dt(row["hold_expires_at"]), get(row, "created_at", ""), row["account_id"],
                   row["room_id"], get(row, "id_document"), get(row, "special_request", ""),
                   int(get(row, "paid", 0)))

    # -- helpers --------------------------------------------------------------
    @property
    def nights(self) -> int:
        return (self.check_out_date - self.check_in_date).days

    @property
    def deposit_paid(self) -> bool:
        return self.deposit_amount > 0 and self.paid >= self.deposit_amount

    def require_status(self, *allowed: BookingStatus) -> None:
        if self.status not in allowed:
            raise ServiceError("NOT_ALLOWED_STATE", status=status_text(self.status))

    def hours_until_checkin(self, now: datetime, checkin_hour: int = 14) -> float:
        start = datetime.combine(self.check_in_date, time(checkin_hour, 0))
        return (start - now).total_seconds() / 3600

    def is_late(self, now: datetime, late_checkout_hour: int = 12) -> bool:
        """BR-20: leaving after 12:00 on the check-out date."""
        return now > datetime.combine(self.check_out_date, time(late_checkout_hour, 0))

    # -- diagram: calculateDeposit() -------------------------------------------
    @staticmethod
    def deposit_for(room_charge: int, cfg) -> int:
        """BR-13: 30% of the room charge, rounded to the nearest 1,000 VND."""
        step = cfg.deposit_rounding
        return int(round(room_charge * cfg.deposit_rate / step)) * step

    def calculate_deposit(self, cfg) -> int:
        self.deposit_amount = self.deposit_for(self.room_charge, cfg)
        return self.deposit_amount

    # -- diagram: confirm() / reject() / cancel() / expire() --------------------
    def confirm(self) -> "Booking":
        """UC-06: only a Pending booking whose deposit has been paid."""
        if self.status == BookingStatus.EXPIRED:
            raise ServiceError("HOLD_EXPIRED")  # MSG-21
        self.require_status(BookingStatus.PENDING)
        if self.paid < self.deposit_amount:
            raise ServiceError("DEPOSIT_NOT_PAID")
        self.status = BookingStatus.CONFIRMED
        self.hold_expires_at = None
        return self

    def reject(self, reason: str) -> "Booking":
        if not (reason or "").strip():
            raise ServiceError("REASON_REQUIRED")  # MSG-29
        self.require_status(BookingStatus.PENDING)
        self.status = BookingStatus.REJECTED
        self.hold_expires_at = None
        return self

    def cancel(self) -> "Booking":
        """UC-05: a booking that is already checked in or completed cannot be cancelled (MSG-26)."""
        if self.status in (BookingStatus.CHECKED_IN, BookingStatus.COMPLETED):
            raise ServiceError("CANNOT_CANCEL")
        self.require_status(BookingStatus.PENDING, BookingStatus.CONFIRMED)
        self.status = BookingStatus.CANCELLED
        self.hold_expires_at = None
        return self

    def is_hold_overdue(self, now: datetime) -> bool:
        """BR-10: Pending, hold time passed, and the deposit is not fully in."""
        return (self.status == BookingStatus.PENDING and self.hold_expires_at is not None
                and self.hold_expires_at < now and not self.deposit_paid)

    def expire(self) -> "Booking":
        self.require_status(BookingStatus.PENDING)
        self.status = BookingStatus.EXPIRED
        self.hold_expires_at = None
        return self

    # -- diagram: checkIn(room) / checkOut() -------------------------------------
    def check_in(self, room, id_document: str, today: date) -> "Booking":
        """UC-07: Confirmed, arrival date reached, an ID number and a free room of the booked type."""
        if self.status != BookingStatus.CONFIRMED or self.check_in_date > today:
            raise ServiceError("NOT_CONFIRMED")  # MSG-31
        doc = (id_document or "").strip()
        if len(doc) < 6:
            raise ServiceError("ID_REQUIRED")
        if room is None or not room.can_host(self.room_type_id):
            raise ServiceError("NO_ROOM_CHECKIN")  # MSG-32
        room.occupy()
        self.status = BookingStatus.CHECKED_IN
        self.room_id = room.room_id
        self.id_document = doc
        return self

    def check_out(self, room=None) -> "Booking":
        """UC-08: the booking is Completed and its room goes to Cleaning."""
        self.require_status(BookingStatus.CHECKED_IN)
        self.status = BookingStatus.COMPLETED
        if room is not None:
            room.vacate()
        return self

    # -- diagram: calculateBill() -------------------------------------------------
    def calculate_bill(self, cfg, usages=(), late: bool = False) -> dict:
        """Room + services + late fee, plus VAT, minus what is already paid (UC-08 step 3)."""
        n = max(1, self.nights)
        services = sum(u.get_amount() for u in usages)
        late_fee = int(round(self.room_charge / n * cfg.late_fee_rate)) if late else 0  # half a night
        subtotal = self.room_charge + services + late_fee
        vat = int(round(subtotal * cfg.vat_rate))
        total = subtotal + vat
        return {"nights": n, "room_charge": self.room_charge, "lines": list(usages), "service_charge": services,
                "late_fee": late_fee, "subtotal": subtotal, "vat": vat, "total": total, "paid": self.paid,
                "balance": total - self.paid}

"""Payment, Refund, Invoice, Notification (class diagram, bottom row)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..errors import ServiceError
from .enums import (NotificationStatus, PaymentMethod, PaymentPurpose, RefundStatus)


@dataclass
class Payment:
    booking_id: int
    amount: int
    method: PaymentMethod
    purpose: PaymentPurpose
    transaction_ref: str = ""
    paid_at: str = ""
    payment_id: int | None = None

    def __post_init__(self):
        self.method = PaymentMethod(self.method)
        self.purpose = PaymentPurpose(self.purpose)

    def process(self, gateway, decline: bool = False) -> "Payment":
        """UC-09: ask the (simulated) gateway. A refusal raises MSG-41 and nothing is saved."""
        result = gateway.charge(int(self.amount), self.method.value, decline=decline)
        if not result.ok:
            raise ServiceError("GATEWAY_FAIL")
        self.transaction_ref = result.reference
        return self


@dataclass
class Refund:
    booking_id: int
    amount: int = 0
    refund_rate: int = 0
    reason: str = ""
    status: RefundStatus = RefundStatus.PENDING
    refunded_at: str = ""
    refund_id: int | None = None

    def __post_init__(self):
        self.status = RefundStatus(self.status)

    @staticmethod
    def rate_for(hours_before_checkin: float, cfg) -> int:
        """BR-12: 100% at >= 48 h, 50% at >= 24 h, otherwise 0%."""
        if hours_before_checkin >= cfg.refund_full_hours:
            return 100
        if hours_before_checkin >= cfg.refund_half_hours:
            return cfg.refund_half_rate
        return 0

    def calculate_refund(self, paid: int, hours_before_checkin: float | None = None, cfg=None,
                         rate: int | None = None) -> int:
        """Set the rate (from the hours left, or given) and the amount to give back."""
        if rate is None:
            rate = self.rate_for(hours_before_checkin, cfg)
        self.refund_rate = int(rate)
        self.amount = int(round(paid * self.refund_rate / 100))
        return self.amount

    def settle(self, completed: bool) -> None:
        """Completed when the money has gone back (or there is nothing to send)."""
        self.status = RefundStatus.COMPLETED if (completed or self.amount == 0) else RefundStatus.PENDING

    def mark_completed(self) -> None:
        self.status = RefundStatus.COMPLETED


@dataclass
class Invoice:
    invoice_no: str
    booking_id: int
    issued_at: str
    room_charge: int = 0
    service_charge: int = 0
    late_fee: int = 0
    vat_amount: int = 0
    total_amount: int = 0
    invoice_id: int | None = None

    def generate(self, bill: dict) -> "Invoice":
        """Fill the amounts from the bill computed at check-out. An issued invoice is final (BR-25)."""
        self.room_charge = bill["room_charge"]
        self.service_charge = bill["service_charge"]
        self.late_fee = bill["late_fee"]
        self.vat_amount = bill["vat"]
        self.total_amount = bill["total"]
        return self

    def _details(self, details) -> dict:
        return {**dict(details), "invoice_no": self.invoice_no, "issued_at": self.issued_at,
                "room_charge": self.room_charge, "late_fee": self.late_fee, "vat_amount": self.vat_amount,
                "total_amount": self.total_amount}

    def export_pdf(self, details, lines=()) -> bytes:
        """FR-21. `details` is the invoice joined with its booking (guest, dates, room)."""
        from ..services import export
        return export.invoice_pdf(self._details(details), lines)

    def print(self, details, lines=()) -> str:
        """Plain-text copy for a receipt printer."""
        d = self._details(details)
        out = [f"Grand Horizon Hotel - Invoice {self.invoice_no}", f"Issued {self.issued_at}",
               f"Guest: {d.get('guest_name', '')}   Booking: {d.get('booking_code', '')}",
               f"Room charge  {self.room_charge:>12,}"]
        for u in lines:
            out.append(f"{u['name']} x {u['quantity']}  {int(u['quantity']) * int(u['unit_price']):>12,}")
        if self.late_fee:
            out.append(f"Late fee     {self.late_fee:>12,}")
        out += [f"VAT          {self.vat_amount:>12,}", f"Total        {self.total_amount:>12,}"]
        return "\n".join(out)


@dataclass
class Notification:
    booking_id: int
    type: str
    recipient_email: str
    status: NotificationStatus = NotificationStatus.FAILED
    attempts: int = 0
    last_attempt_at: str | None = None
    notification_id: int | None = None

    def __post_init__(self):
        self.status = NotificationStatus(self.status)

    def send(self, mailer, subject: str, body: str, now: datetime) -> bool:
        """One attempt (UC-15). Records the outcome on the object."""
        self.attempts += 1
        self.last_attempt_at = now.strftime("%Y-%m-%d %H:%M:%S")
        try:
            mailer.send(self.recipient_email, subject, body)
        except Exception:
            self.status = NotificationStatus.FAILED
            return False
        self.status = NotificationStatus.SENT
        return True

    def retry(self, mailer, subject: str, body: str, now: datetime, max_attempts: int = 3) -> bool:
        """BR-35: keep trying until it is sent or `max_attempts` attempts have been made."""
        while self.status != NotificationStatus.SENT and self.attempts < max_attempts:
            self.send(mailer, subject, body, now)
        return self.status == NotificationStatus.SENT

"""Domain layer: the 16 classes of the class diagram (report Chapter 5).

Entities hold their own state and business rules. The service layer loads rows,
builds these objects, calls their methods and saves the result.
"""
from .account import Account, Admin, AuditLog, Customer, Receptionist, ResetToken
from .booking import Booking
from .catalog import AddOnService, PricePeriod, Room, RoomType, ServiceUsage
from .enums import (AccountStatus, BookingStatus, NotificationStatus, PaymentMethod, PaymentPurpose,
                    RefundStatus, Role, RoomStatus)
from .payment import Invoice, Notification, Payment, Refund

__all__ = [
    "Account", "Customer", "Receptionist", "Admin", "ResetToken", "AuditLog",
    "RoomType", "PricePeriod", "Room", "AddOnService", "ServiceUsage",
    "Booking", "Payment", "Refund", "Invoice", "Notification",
    "Role", "AccountStatus", "BookingStatus", "RoomStatus", "PaymentMethod", "PaymentPurpose",
    "RefundStatus", "NotificationStatus",
]

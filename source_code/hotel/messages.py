"""Standard system messages (report: MSG-01 to MSG-61).

Only the codes that appear in the report's use cases keep their number. The key
is what the code uses; the code is shown to the user next to the text.
"""
from __future__ import annotations

# key -> (code, text).  "{...}" placeholders are filled by ServiceError(**kw).
MESSAGES: dict[str, tuple[str, str]] = {
    # accounts
    "REGISTERED": ("MSG-01", "Account created. Welcome to Grand Horizon."),
    "INVALID_INPUT": ("MSG-02", "{detail}"),
    "BAD_EMAIL": ("MSG-04", "Enter a valid email address."),
    "EMAIL_TAKEN": ("MSG-05", "An account with this email already exists. Sign in instead."),
    "BAD_CREDENTIALS": ("MSG-06", "The email or password is not correct."),
    "ACCOUNT_LOCKED": ("MSG-07", "This account is locked after too many failed sign-ins. Ask an administrator to unlock it."),
    "RESET_INVALID": ("MSG-09", "This code is not correct or has expired."),
    "PASSWORD_UPDATED": ("MSG-10", "Password updated. Sign in with your new password."),
    "PROFILE_SAVED": ("MSG-11", "Profile updated."),
    "BAD_CURRENT_PASSWORD": ("MSG-12", "Your current password is not correct."),
    # search / booking
    "DATES_REQUIRED": ("MSG-14", "Choose both a check-in and a check-out date."),
    "DATE_IN_PAST": ("MSG-15", "Check-in cannot be in the past."),
    "DATE_ORDER": ("MSG-16", "Check-out must be after check-in."),
    "TOO_LONG": ("MSG-17", "A stay is limited to {n} nights. Shorten it or make a second booking."),
    "TOO_MANY_GUESTS": ("MSG-17", "{name} sleeps up to {cap} guests, but you selected {guests}."),
    "NO_MATCH": ("MSG-18", "No room matches these filters."),
    "NO_ROOM": ("MSG-19", "No rooms of this type are left for these dates."),
    "BOOKING_CREATED": ("", "Booking {code} created. The room is held until {until}."),
    "HOLD_EXPIRED": ("MSG-21", "This booking expired because the deposit was not paid in time."),
    "CONFIRM_CANCEL": ("MSG-24", "Cancel this booking? This cannot be undone."),
    "BOOKING_CANCELLED": ("", "Booking {code} cancelled. {refund_note}"),
    "CANNOT_CANCEL": ("MSG-26", "A booking that is checked in or completed cannot be cancelled."),
    "NOT_ALLOWED_STATE": ("", "This action is not available while the booking is {status}."),
    "REASON_REQUIRED": ("MSG-29", "Enter a reason so the customer knows why."),
    "DEPOSIT_NOT_PAID": ("", "Confirm after the deposit is received."),
    "BOOKING_CONFIRMED": ("", "Booking {code} confirmed."),
    "BOOKING_REJECTED": ("", "Booking {code} rejected. The deposit is queued for refund."),
    "CHANGE_TOO_LATE": ("", "The new check-in must be at least {hours} hours from now."),
    "CHANGE_SAME": ("", "These are your current dates."),
    "DATES_CHANGED": ("", "Dates changed. {note}"),
    # front desk
    "NOT_CONFIRMED": ("MSG-31", "Check-in needs a confirmed booking and an arrival date that has been reached."),
    "NO_ROOM_CHECKIN": ("MSG-32", "No room of the booked type is available right now."),
    "ID_REQUIRED": ("", "Enter the ID or passport number to check in."),
    "ROOM_SWAPPED": ("MSG-34", "Room changed. The old room is now marked Cleaning."),
    "CHECKED_IN": ("", "{guest} is in room {room}."),
    "UNPAID_BALANCE": ("MSG-38", "Check-out is blocked until the balance is paid."),
    "GATEWAY_FAIL": ("MSG-41", "The payment was declined. Nothing was charged."),
    "REFUND_RECORDED": ("MSG-44", "Refund recorded."),
    "CHECKED_OUT": ("", "Check-out complete. {invoice} issued. Room {room} is now Cleaning."),
    # admin
    "DELETE_BLOCKED": ("MSG-49", "This item is used by active or upcoming bookings, so it cannot be removed."),
    "PERIOD_OVERLAP": ("MSG-50", "This date period overlaps another price period of the same room type."),
    "OCCUPIED_MAINTENANCE": ("MSG-51", "An occupied room cannot go into maintenance. Check the guest out first."),
    "RANGE_INVALID": ("MSG-52", "The end date must not be before the start date."),
    "NO_DATA": ("MSG-53", "There is no data for this period."),
    "EMAIL_FAILED": ("MSG-55", "The confirmation email could not be sent after {n} attempts. The booking stays confirmed."),
    "FORBIDDEN": ("MSG-56", "You do not have permission to do this."),
    "PDF_FAILED": ("MSG-58", "The PDF could not be created. You can print it again."),
    "BAD_PRICE": ("MSG-59", "The price must be greater than 0."),
    "LAST_ADMIN": ("MSG-60", "The last active administrator cannot be locked."),
    "SELF_LOCK": ("", "You cannot lock your own account."),
    "NOT_FOUND": ("", "We could not find that record."),
    "NO_PRICE": ("", "No price is defined for {date}. Ask the administrator to add a price period."),
}


def render(key: str, **kw) -> tuple[str, str]:
    code, text = MESSAGES[key]
    try:
        text = text.format(**kw)
    except (KeyError, IndexError):
        pass
    return code, text

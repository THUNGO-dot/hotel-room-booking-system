"""UC-03..06, UC-16, FR-11 and BR-06..17."""
import re
from datetime import datetime

import pytest

from hotel.errors import PermissionDenied, ServiceError
from hotel.services import availability, bookings, catalog, payments
from hotel.services.common import deposit_for
from tests.conftest import DLX, EXE, STD


# ---------------------------------------------------------------- BR-13 deposit
@pytest.mark.parametrize("charge,expected", [
    (2_400_000, 720_000), (1_500_000, 450_000), (3_800_000, 1_140_000),
    (1_111_000, 333_000),      # 333,300 -> nearest 1,000
    (1_117_000, 335_000),      # 335,100 -> nearest 1,000
])
def test_deposit_is_30_percent_rounded_to_1000(ctx, charge, expected):
    assert deposit_for(ctx, charge) == expected


def test_deposit_rate_comes_from_configuration(ctx):
    """NFR-05: change the parameter, not the code."""
    ctx.cfg = ctx.cfg.with_overrides(deposit_rate=0.5)
    assert deposit_for(ctx, 2_400_000) == 1_200_000


# ------------------------------------------------------------ search validation
@pytest.mark.parametrize("ci,co,key", [
    (None, "2026-11-08", "DATES_REQUIRED"),
    ("2026-11-04", "2026-11-08", "DATE_IN_PAST"),      # today is 5 Nov
    ("2026-11-08", "2026-11-08", "DATE_ORDER"),
    ("2026-11-09", "2026-11-08", "DATE_ORDER"),
    ("2026-11-06", "2026-12-07", "TOO_LONG"),          # 31 nights
])
def test_stay_validation(ctx, ci, co, key):
    with pytest.raises(ServiceError) as e:
        availability.validate_stay(ctx, ci, co)
    assert e.value.key == key


def test_30_nights_is_allowed_and_today_is_allowed(ctx):
    assert availability.validate_stay(ctx, "2026-11-05", "2026-12-05") == 30


def test_search_lists_every_type_with_stay_total_and_deposit(ctx, ids):
    rows = availability.search(ctx, "2026-11-10", "2026-11-12", adults=2)
    by_name = {r["type"]["name"]: r for r in rows}
    dlx = by_name["Deluxe Ocean Suite"]
    assert (dlx["total"], dlx["nightly"], dlx["deposit"], dlx["nights"]) == (2_400_000, 1_200_000, 720_000, 2)
    assert dlx["left"] == 4                              # 305 is in maintenance (BR-08)
    assert [r["nightly"] for r in rows] == sorted(r["nightly"] for r in rows)


def test_search_filters_and_sort(ctx, ids):
    lo = availability.search(ctx, "2026-11-10", "2026-11-12", price="lo")
    assert {r["type"]["name"] for r in lo} == {"Standard King Room"}
    hi = availability.search(ctx, "2026-11-10", "2026-11-12", price="hi", sort="desc")
    assert [r["type"]["name"] for r in hi] == ["Executive Family Suite", "Deluxe Ocean Suite"]


def test_search_flags_a_party_that_does_not_fit(ctx, ids):
    rows = availability.search(ctx, "2026-11-10", "2026-11-12", adults=3, children=0)
    flags = {r["type"]["name"]: r["can_book"] for r in rows}
    assert flags == {"Standard King Room": False, "Deluxe Ocean Suite": False, "Executive Family Suite": True}


# ------------------------------------------------------------------- creating
def test_booking_is_pending_with_code_hold_and_deposit(ctx, ids, customer):
    b = bookings.create_booking(ctx, customer, ids["types"][DLX], "2026-11-10", "2026-11-12", 2, 0, "Quiet floor")
    assert re.fullmatch(r"HB-20261105-\d{4}", b["booking_code"])          # BR-16
    assert b["status"] == "PENDING"
    assert (b["room_charge"], b["deposit_amount"]) == (2_400_000, 720_000)
    assert b["hold_expires_at"] == "2026-11-06 10:00:00"                   # BR-10: 24 hours
    assert b["room_id"] is None                                            # room is assigned at check-in
    assert b["guest_name"] == customer["full_name"]


def test_codes_are_unique_and_sequential(ctx, ids, customer):
    a = bookings.create_booking(ctx, customer, ids["types"][STD], "2026-11-10", "2026-11-11", 1)
    b = bookings.create_booking(ctx, customer, ids["types"][STD], "2026-11-10", "2026-11-11", 1)
    assert a["booking_code"].endswith("0001") and b["booking_code"].endswith("0002")


def test_only_customers_book_online(ctx, ids, reception):
    with pytest.raises(PermissionDenied):
        bookings.create_booking(ctx, reception, ids["types"][STD], "2026-11-10", "2026-11-11", 1)
    with pytest.raises(PermissionDenied):
        bookings.create_booking(ctx, None, ids["types"][STD], "2026-11-10", "2026-11-11", 1)


def test_party_larger_than_capacity_is_refused(ctx, ids, customer):
    with pytest.raises(ServiceError) as e:
        bookings.create_booking(ctx, customer, ids["types"][STD], "2026-11-10", "2026-11-11", 2, 1)
    assert e.value.code == "MSG-17"


def test_cannot_book_more_rooms_than_exist(ctx, ids, customer):
    for _ in range(2):                                       # Executive has 2 rooms
        bookings.create_booking(ctx, customer, ids["types"][EXE], "2026-11-10", "2026-11-12", 2)
    with pytest.raises(ServiceError) as e:
        bookings.create_booking(ctx, customer, ids["types"][EXE], "2026-11-11", "2026-11-13", 2)
    assert e.value.code == "MSG-19"


def test_back_to_back_stays_do_not_overlap(ctx, ids, customer):
    for _ in range(2):
        bookings.create_booking(ctx, customer, ids["types"][EXE], "2026-11-10", "2026-11-12", 2)
    # the new guest arrives on the day the others leave: the room is free
    bookings.create_booking(ctx, customer, ids["types"][EXE], "2026-11-12", "2026-11-14", 2)


def test_expired_cancelled_and_rejected_bookings_free_the_room(ctx, ids, customer, reception):
    a = bookings.create_booking(ctx, customer, ids["types"][EXE], "2026-11-10", "2026-11-12", 2)
    b = bookings.create_booking(ctx, customer, ids["types"][EXE], "2026-11-10", "2026-11-12", 2)
    assert availability.rooms_left(ctx, ids["types"][EXE], "2026-11-10", "2026-11-12") == 0
    bookings.cancel_booking(ctx, customer, a["booking_code"])
    assert availability.rooms_left(ctx, ids["types"][EXE], "2026-11-10", "2026-11-12") == 1
    bookings.reject_booking(ctx, reception, b["booking_code"], "Room needed for maintenance")
    assert availability.rooms_left(ctx, ids["types"][EXE], "2026-11-10", "2026-11-12") == 2


def test_prices_follow_the_period_of_each_night(ctx, ids, admin):
    """BR-31: a stay that crosses two price periods is charged per night."""
    tid = ids["types"][STD]
    ctx.db.execute("DELETE FROM price_period WHERE room_type_id=?", (tid,))
    catalog.save_period(ctx, admin, tid, "2026-01-01", "2026-11-10", 700_000)
    catalog.save_period(ctx, admin, tid, "2026-11-11", "2026-12-31", 900_000)
    assert catalog.quote(ctx, tid, "2026-11-09", "2026-11-13") == 700_000 * 2 + 900_000 * 2


# ----------------------------------------------------------------- hold expiry
def test_unpaid_booking_expires_after_24_hours(ctx, ids, customer):
    b = bookings.create_booking(ctx, customer, ids["types"][EXE], "2026-11-10", "2026-11-12", 2)
    ctx.clock.advance(hours=23, minutes=59)
    assert bookings.expire_holds(ctx) == 0
    ctx.clock.advance(minutes=2)
    assert bookings.expire_holds(ctx) == 1
    assert bookings.get_booking(ctx, b["booking_code"])["status"] == "EXPIRED"


def test_paying_in_time_keeps_the_booking(ctx, ids, customer):
    b = bookings.create_booking(ctx, customer, ids["types"][EXE], "2026-11-10", "2026-11-12", 2)
    payments.pay_deposit(ctx, b, "CARD", customer)
    ctx.clock.advance(days=3)
    bookings.expire_holds(ctx)
    assert bookings.get_booking(ctx, b["booking_code"])["status"] == "PENDING"   # waits for the front desk


def test_paying_after_expiry_is_refused(ctx, ids, customer):
    b = bookings.create_booking(ctx, customer, ids["types"][EXE], "2026-11-10", "2026-11-12", 2)
    ctx.clock.advance(hours=25)
    with pytest.raises(ServiceError) as e:
        payments.pay_deposit(ctx, b, "CARD", customer)
    assert e.value.code == "MSG-21"


# --------------------------------------------------------------------- payment
def test_deposit_payment_records_amount_and_reference(ctx, ids, customer):
    b = bookings.create_booking(ctx, customer, ids["types"][DLX], "2026-11-10", "2026-11-12", 2)
    payments.pay_deposit(ctx, b, "E_WALLET", customer)
    p = ctx.db.one("SELECT * FROM payment")
    assert (p["amount"], p["method"], p["purpose"]) == (720_000, "E_WALLET", "DEPOSIT")
    assert p["transaction_ref"].startswith("EWL")


def test_declined_payment_saves_nothing_and_keeps_the_hold(ctx, ids, customer):
    b = bookings.create_booking(ctx, customer, ids["types"][DLX], "2026-11-10", "2026-11-12", 2)
    with pytest.raises(ServiceError) as e:
        payments.pay_deposit(ctx, b, "CARD", customer, decline=True)
    assert e.value.code == "MSG-41"
    assert ctx.db.scalar("SELECT COUNT(*) FROM payment") == 0
    assert bookings.get_booking(ctx, b["booking_code"])["status"] == "PENDING"
    payments.pay_deposit(ctx, b, "CARD", customer)            # the guest can simply try again
    assert ctx.db.scalar("SELECT COUNT(*) FROM payment") == 1


def test_deposit_cannot_be_paid_twice(ctx, ids, customer):
    b = bookings.create_booking(ctx, customer, ids["types"][DLX], "2026-11-10", "2026-11-12", 2)
    payments.pay_deposit(ctx, b, "CARD", customer)
    with pytest.raises(ServiceError):
        payments.pay_deposit(ctx, b, "CARD", customer)


def test_customers_cannot_pay_cash_but_the_desk_can(ctx, ids, customer, reception):
    b = bookings.create_booking(ctx, customer, ids["types"][DLX], "2026-11-10", "2026-11-12", 2)
    with pytest.raises(ServiceError):
        payments.pay_deposit(ctx, b, "CASH", customer)
    payments.pay_deposit(ctx, b, "CASH", reception)
    assert ctx.db.scalar("SELECT method FROM payment") == "CASH"


# ---------------------------------------------------------- confirm and reject
def test_confirm_needs_the_deposit(ctx, ids, customer, reception):
    b = bookings.create_booking(ctx, customer, ids["types"][DLX], "2026-11-10", "2026-11-12", 2)
    with pytest.raises(ServiceError) as e:
        bookings.confirm_booking(ctx, reception, b["booking_code"])
    assert e.value.key == "DEPOSIT_NOT_PAID"


def test_confirm_sends_the_email(ctx, make):
    b = make()
    assert b["status"] == "CONFIRMED"
    assert len(ctx.mailer.sent) == 1 and ctx.mailer.sent[0]["to"] == "an.nguyen@example.com"
    assert b["booking_code"] in ctx.mailer.sent[0]["body"]
    n = ctx.db.one("SELECT * FROM notification")
    assert (n["status"], n["attempts"]) == ("SENT", 1)


def test_email_is_retried_up_to_three_times(ctx, ids, customer, reception):
    b = bookings.create_booking(ctx, customer, ids["types"][DLX], "2026-11-10", "2026-11-12", 2)
    payments.pay_deposit(ctx, b, "CARD", customer)
    ctx.mailer.fail_next = 2                                   # fails twice, third attempt works
    _, status = bookings.confirm_booking(ctx, reception, b["booking_code"])
    assert status == "SENT" and ctx.db.scalar("SELECT attempts FROM notification") == 3


def test_email_failure_never_undoes_the_confirmation(ctx, ids, customer, reception):
    b = bookings.create_booking(ctx, customer, ids["types"][DLX], "2026-11-10", "2026-11-12", 2)
    payments.pay_deposit(ctx, b, "CARD", customer)
    ctx.mailer.fail_next = 99
    booking, status = bookings.confirm_booking(ctx, reception, b["booking_code"])
    assert status == "FAILED" and booking["status"] == "CONFIRMED"      # UC-15 exception 3a
    n = ctx.db.one("SELECT * FROM notification")
    assert (n["status"], n["attempts"]) == ("FAILED", 3)                # BR-35


def test_confirming_twice_is_refused(ctx, make, reception):
    b = make()
    with pytest.raises(ServiceError) as e:
        bookings.confirm_booking(ctx, reception, b["booking_code"])
    assert e.value.key == "NOT_ALLOWED_STATE"


def test_reject_needs_a_reason_and_queues_the_refund(ctx, ids, customer, reception):
    b = bookings.create_booking(ctx, customer, ids["types"][DLX], "2026-11-10", "2026-11-12", 2)
    payments.pay_deposit(ctx, b, "CARD", customer)
    with pytest.raises(ServiceError) as e:
        bookings.reject_booking(ctx, reception, b["booking_code"], "  ")
    assert e.value.code == "MSG-29"
    r = bookings.reject_booking(ctx, reception, b["booking_code"], "Overbooked")
    assert r["status"] == "REJECTED"
    f = ctx.db.one("SELECT * FROM refund")
    assert (f["amount"], f["refund_rate"], f["status"]) == (720_000, 100, "PENDING")
    payments.complete_refund(ctx, reception, f["refund_id"])
    assert ctx.db.scalar("SELECT status FROM refund") == "COMPLETED"


# ----------------------------------------------------------------- cancellation
@pytest.mark.parametrize("now,rate,amount", [
    (datetime(2026, 11, 8, 14, 0), 100, 720_000),      # exactly 48 h before check-in (10 Nov 14:00)
    (datetime(2026, 11, 8, 14, 1), 50, 360_000),       # 47 h 59 m
    (datetime(2026, 11, 9, 14, 0), 50, 360_000),       # exactly 24 h
    (datetime(2026, 11, 9, 14, 1), 0, 0),              # under 24 h
    (datetime(2026, 11, 5, 10, 0), 100, 720_000),
])
def test_refund_policy(ctx, make, customer, now, rate, amount):
    """BR-12: 100% at >= 48 h, 50% at 24-48 h, 0% under 24 h before check-in (14:00)."""
    b = make(confirm=False)
    ctx.clock._fixed = now
    result = bookings.cancel_booking(ctx, customer, b["booking_code"])
    assert (result["rate"], result["amount"]) == (rate, amount)
    assert result["booking"]["status"] == "CANCELLED"
    refund = ctx.db.one("SELECT * FROM refund")
    assert refund["amount"] == amount and refund["refund_rate"] == rate


def test_customer_cancellation_leaves_the_payout_for_the_desk(ctx, make, customer, reception):
    b = make(confirm=False)
    bookings.cancel_booking(ctx, customer, b["booking_code"])
    assert ctx.db.scalar("SELECT status FROM refund") == "PENDING"
    assert len(payments.pending_refunds(ctx)) == 1


def test_front_desk_cancellation_records_the_refund_and_the_audit(ctx, make, reception):
    b = make()
    bookings.cancel_booking(ctx, reception, b["booking_code"])
    assert ctx.db.scalar("SELECT status FROM refund") == "COMPLETED"
    assert ctx.db.scalar("SELECT COUNT(*) FROM audit_log WHERE action='CANCEL_BOOKING'") == 1


def test_cancelling_without_a_deposit_creates_no_refund(ctx, ids, customer):
    b = bookings.create_booking(ctx, customer, ids["types"][DLX], "2026-11-10", "2026-11-12", 2)
    bookings.cancel_booking(ctx, customer, b["booking_code"])
    assert ctx.db.scalar("SELECT COUNT(*) FROM refund") == 0


def test_checked_in_booking_cannot_be_cancelled(ctx, checked_in, reception):
    with pytest.raises(ServiceError) as e:
        bookings.cancel_booking(ctx, reception, checked_in["booking_code"])
    assert e.value.code == "MSG-26"


def test_cannot_cancel_twice(ctx, make, customer):
    b = make(confirm=False)
    bookings.cancel_booking(ctx, customer, b["booking_code"])
    with pytest.raises(ServiceError):
        bookings.cancel_booking(ctx, customer, b["booking_code"])


def test_a_customer_cannot_touch_someone_elses_booking(ctx, make):
    from hotel.services import accounts
    b = make(confirm=False)
    other = accounts.get(ctx, accounts.register(ctx, "Other Person", "other@example.com", "0988 777 666", "password123"))
    for action in (lambda: bookings.cancel_booking(ctx, other, b["booking_code"]),
                   lambda: bookings.load_for(ctx, other, b["booking_code"]),
                   lambda: bookings.change_dates(ctx, other, b["booking_code"], "2026-11-20", "2026-11-22")):
        with pytest.raises(PermissionDenied):
            action()                                           # NFR-04


# ----------------------------------------------------------------- change dates
def test_change_dates_recomputes_price_and_deposit(ctx, make, customer):
    b = make(confirm=False, pay=False)
    r = bookings.change_dates(ctx, customer, b["booking_code"], "2026-11-20", "2026-11-23")
    assert (r["booking"]["room_charge"], r["booking"]["deposit_amount"]) == (3_600_000, 1_080_000)


def test_change_needs_48_hours_notice(ctx, make, customer):
    b = make(confirm=False, pay=False, ci="2026-11-06", co="2026-11-08")        # check-in in 28 h
    with pytest.raises(ServiceError) as e:
        bookings.change_dates(ctx, customer, b["booking_code"], "2026-11-20", "2026-11-22")
    assert e.value.key == "CHANGE_TOO_LATE"
    far = make(confirm=False, pay=False)
    with pytest.raises(ServiceError) as e:                                         # new date too close
        bookings.change_dates(ctx, customer, far["booking_code"], "2026-11-06", "2026-11-08")
    assert e.value.key == "CHANGE_TOO_LATE"


def test_change_to_dates_with_no_room_is_refused(ctx, ids, customer):
    for _ in range(2):
        bookings.create_booking(ctx, customer, ids["types"][EXE], "2026-11-20", "2026-11-22", 2)
    mine = bookings.create_booking(ctx, customer, ids["types"][EXE], "2026-11-10", "2026-11-12", 2)
    with pytest.raises(ServiceError) as e:
        bookings.change_dates(ctx, customer, mine["booking_code"], "2026-11-20", "2026-11-22")
    assert e.value.code == "MSG-19"


def test_changing_to_the_same_dates_is_refused(ctx, make, customer):
    b = make(confirm=False)
    with pytest.raises(ServiceError) as e:
        bookings.change_dates(ctx, customer, b["booking_code"], b["check_in_date"], b["check_out_date"])
    assert e.value.key == "CHANGE_SAME"


def test_longer_stay_needs_an_extra_deposit_and_a_new_confirmation(ctx, make, customer):
    b = make()                                                  # confirmed, 720,000 paid
    r = bookings.change_dates(ctx, customer, b["booking_code"], "2026-11-10", "2026-11-14")   # 4 nights
    assert r["extra"] == 1_440_000 - 720_000
    assert r["booking"]["status"] == "PENDING"
    payments.pay_deposit(ctx, r["booking"], "CARD", customer)
    assert ctx.db.scalar("SELECT SUM(amount) FROM payment") == 1_440_000


def test_shorter_stay_keeps_the_booking_confirmed_and_credits_the_deposit(ctx, make, customer):
    b = make(ci="2026-11-10", co="2026-11-14")                  # deposit 1,440,000
    r = bookings.change_dates(ctx, customer, b["booking_code"], "2026-11-10", "2026-11-12")
    assert r["booking"]["status"] == "CONFIRMED" and r["extra"] == 0
    assert "credited" in r["note"]


# --------------------------------------------------------------------- walk-in
def test_walk_in_has_no_account_and_is_confirmed_at_once(ctx, ids, reception):
    b = bookings.create_walk_in(ctx, reception, "Walk In Guest", "0933 444 555", ids["types"][STD], 2, 1)
    assert b["account_id"] is None and b["status"] == "CONFIRMED"            # BR-15
    assert (b["check_in_date"], b["check_out_date"]) == ("2026-11-05", "2026-11-07")
    assert b["deposit_amount"] == 0 and b["room_charge"] == 1_500_000


def test_walk_in_needs_a_free_room_today(ctx, ids, reception):
    ctx.db.execute("UPDATE room SET status='OCCUPIED' WHERE room_type_id=?", (ids["types"][EXE],))
    with pytest.raises(ServiceError) as e:
        bookings.create_walk_in(ctx, reception, "Walk In Guest", "0933 444 555", ids["types"][EXE], 1, 2)
    assert e.value.code == "MSG-19"


@pytest.mark.parametrize("kw", [dict(guest_name=""), dict(guest_phone="12"), dict(stay_nights=0),
                                dict(stay_nights=31), dict(guests=0), dict(guests=9)])
def test_walk_in_validation(ctx, ids, reception, kw):
    data = dict(guest_name="Guest", guest_phone="0933444555", type_id=ids["types"][STD], stay_nights=1, guests=1)
    data.update(kw)
    with pytest.raises(ServiceError):
        bookings.create_walk_in(ctx, reception, **data)


def test_walk_in_is_receptionist_only(ctx, ids, customer, admin):
    for actor in (customer, admin, None):
        with pytest.raises(PermissionDenied):
            bookings.create_walk_in(ctx, actor, "Guest", "0933444555", ids["types"][STD], 1, 1)

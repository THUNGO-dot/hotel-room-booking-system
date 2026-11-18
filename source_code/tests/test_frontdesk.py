"""UC-07, UC-08, UC-09 and BR-18..27."""
import re
import sqlite3
from datetime import datetime

import pytest

from hotel.errors import PermissionDenied, ServiceError
from hotel.services import bookings, catalog, frontdesk
from tests.conftest import DLX, EXE, STD


def room_status(ctx, number):
    return ctx.db.scalar("SELECT status FROM room WHERE room_number=?", (number,))


# --------------------------------------------------------------------- check-in
def test_check_in_assigns_room_and_marks_it_occupied(ctx, ids, make, reception):
    b = make(DLX, "2026-11-05", "2026-11-07")
    out = frontdesk.check_in(ctx, reception, b["booking_code"], "079091234567", ids["rooms"]["201"])
    assert out["status"] == "CHECKED_IN" and out["room_number"] == "201" and out["id_document"] == "079091234567"
    assert room_status(ctx, "201") == "OCCUPIED"


def test_check_in_needs_a_confirmed_booking(ctx, ids, make, reception, customer):
    pending = make(DLX, "2026-11-05", "2026-11-07", confirm=False)
    with pytest.raises(ServiceError) as e:                                   # BR-18
        frontdesk.check_in(ctx, reception, pending["booking_code"], "079091234567", ids["rooms"]["201"])
    assert e.value.code == "MSG-31"


def test_check_in_before_the_arrival_date_is_refused(ctx, ids, make, reception):
    b = make(DLX, "2026-11-10", "2026-11-12")
    with pytest.raises(ServiceError) as e:
        frontdesk.check_in(ctx, reception, b["booking_code"], "079091234567", ids["rooms"]["201"])
    assert e.value.code == "MSG-31"


def test_check_in_needs_an_id_document(ctx, ids, make, reception):
    b = make(DLX, "2026-11-05", "2026-11-07")
    for doc in ("", "  ", "123"):
        with pytest.raises(ServiceError) as e:
            frontdesk.check_in(ctx, reception, b["booking_code"], doc, ids["rooms"]["201"])
        assert e.value.key == "ID_REQUIRED"


@pytest.mark.parametrize("number,why", [("101", "other type"), ("305", "maintenance"), ("202", "occupied")])
def test_check_in_only_accepts_an_available_room_of_the_booked_type(ctx, ids, make, reception, number, why):
    ctx.db.execute("UPDATE room SET status='OCCUPIED' WHERE room_number='202'")
    b = make(DLX, "2026-11-05", "2026-11-07")
    with pytest.raises(ServiceError) as e:
        frontdesk.check_in(ctx, reception, b["booking_code"], "079091234567", ids["rooms"][number])
    assert e.value.code == "MSG-32"
    assert bookings.get_booking(ctx, b["booking_code"])["status"] == "CONFIRMED"   # nothing half done


def test_available_rooms_lists_only_the_right_ones(ctx, ids):
    ctx.db.execute("UPDATE room SET status='CLEANING' WHERE room_number='202'")
    numbers = [r["room_number"] for r in frontdesk.available_rooms(ctx, ids["types"][DLX])]
    assert numbers == ["201", "205", "302"]


def test_check_in_is_receptionist_only(ctx, ids, make, customer, admin):
    b = make(DLX, "2026-11-05", "2026-11-07")
    for actor in (customer, admin):
        with pytest.raises(PermissionDenied):
            frontdesk.check_in(ctx, actor, b["booking_code"], "079091234567", ids["rooms"]["201"])


def test_walk_in_goes_straight_to_check_in(ctx, ids, reception):
    b = bookings.create_walk_in(ctx, reception, "Walk In", "0933 444 555", ids["types"][STD], 1, 1)
    out = frontdesk.check_in(ctx, reception, b["booking_code"], "B1234567", ids["rooms"]["101"])
    assert out["status"] == "CHECKED_IN"


def test_changing_room_cleans_the_old_one(ctx, ids, checked_in, reception):
    out = frontdesk.change_room(ctx, reception, checked_in["booking_code"], ids["rooms"]["202"])
    assert out["room_number"] == "202"
    assert (room_status(ctx, "201"), room_status(ctx, "202")) == ("CLEANING", "OCCUPIED")   # MSG-34
    assert ctx.db.scalar("SELECT COUNT(*) FROM audit_log WHERE action='CHANGE_ROOM'") == 1


# --------------------------------------------------------------- extra services
def test_service_price_is_frozen_on_the_line(ctx, ids, checked_in, reception, admin):
    laundry = ctx.db.scalar("SELECT service_id FROM add_on_service WHERE name LIKE 'Laundry%'")
    frontdesk.add_service(ctx, reception, checked_in["booking_code"], laundry, 2)
    catalog.save_service(ctx, admin, laundry, "Laundry (per item)", 90_000)         # admin raises the price
    b = bookings.get_booking(ctx, checked_in["booking_code"])
    assert frontdesk.compute_bill(ctx, b, False)["service_charge"] == 100_000        # old bill unchanged
    frontdesk.add_service(ctx, reception, checked_in["booking_code"], laundry, 1)
    assert frontdesk.compute_bill(ctx, b, False)["service_charge"] == 190_000        # new line at the new price


@pytest.mark.parametrize("qty", [0, 21, "x", -1])
def test_service_quantity_limits(ctx, checked_in, reception, qty):
    sid = ctx.db.scalar("SELECT service_id FROM add_on_service LIMIT 1")
    with pytest.raises(ServiceError):
        frontdesk.add_service(ctx, reception, checked_in["booking_code"], sid, qty)


def test_services_only_for_guests_in_house(ctx, make, reception):
    b = make(DLX, "2026-11-05", "2026-11-07")
    sid = ctx.db.scalar("SELECT service_id FROM add_on_service LIMIT 1")
    with pytest.raises(ServiceError) as e:
        frontdesk.add_service(ctx, reception, b["booking_code"], sid, 1)
    assert e.value.key == "NOT_ALLOWED_STATE"


# ------------------------------------------------------------------------ bill
def test_bill_matches_the_report_example(ctx, checked_in, reception):
    """Figure 7.19: room 2,400,000 + laundry 100,000, VAT 10% = 250,000, total 2,750,000,
    deposit 720,000 paid, balance 2,030,000."""
    laundry = ctx.db.scalar("SELECT service_id FROM add_on_service WHERE name LIKE 'Laundry%'")
    frontdesk.add_service(ctx, reception, checked_in["booking_code"], laundry, 2)
    bill = frontdesk.compute_bill(ctx, bookings.get_booking(ctx, checked_in["booking_code"]), late=False)
    assert (bill["room_charge"], bill["service_charge"], bill["vat"], bill["total"]) == \
           (2_400_000, 100_000, 250_000, 2_750_000)
    assert (bill["paid"], bill["balance"]) == (720_000, 2_030_000)


def test_late_checkout_adds_half_a_night(ctx, checked_in):
    b = bookings.get_booking(ctx, checked_in["booking_code"])
    bill = frontdesk.compute_bill(ctx, b, late=True)
    assert bill["late_fee"] == 600_000                                               # BR-20: 50% of 1,200,000
    assert bill["vat"] == round((2_400_000 + 600_000) * 0.10)


def test_late_flag_follows_the_clock(ctx, checked_in):
    b = bookings.get_booking(ctx, checked_in["booking_code"])        # check-out 7 Nov
    ctx.clock._fixed = datetime(2026, 11, 7, 11, 59)
    assert frontdesk.is_late(ctx, b) is False
    ctx.clock._fixed = datetime(2026, 11, 7, 12, 1)
    assert frontdesk.is_late(ctx, b) is True


def test_vat_rate_comes_from_configuration(ctx, checked_in):
    ctx.cfg = ctx.cfg.with_overrides(vat_rate=0.08)
    b = bookings.get_booking(ctx, checked_in["booking_code"])
    assert frontdesk.compute_bill(ctx, b, False)["vat"] == 192_000


# -------------------------------------------------------------------- check-out
def test_check_out_completes_everything_together(ctx, checked_in, reception):
    inv = frontdesk.check_out(ctx, reception, checked_in["booking_code"], "CASH", late=False)
    assert re.fullmatch(r"INV-20261105-0001", inv["invoice_no"])                      # BR-26
    assert inv["total_amount"] == 2_640_000                                           # 2,400,000 + 10% VAT
    b = bookings.get_booking(ctx, checked_in["booking_code"])
    assert b["status"] == "COMPLETED" and b["paid"] == 2_640_000                      # BR-27: balance 0
    assert room_status(ctx, "201") == "CLEANING"                                      # BR-22
    last = ctx.db.one("SELECT * FROM payment ORDER BY payment_id DESC LIMIT 1")
    assert (last["purpose"], last["amount"], last["method"]) == ("BALANCE", 1_920_000, "CASH")


def test_check_out_is_blocked_until_the_balance_is_paid(ctx, checked_in, reception):
    with pytest.raises(ServiceError) as e:
        frontdesk.check_out(ctx, reception, checked_in["booking_code"], None, late=False)
    assert e.value.code == "MSG-38"
    assert bookings.get_booking(ctx, checked_in["booking_code"])["status"] == "CHECKED_IN"


def test_failed_card_leaves_no_trace(ctx, checked_in, reception):
    """NFR-07: if the payment fails nothing else may change."""
    before = ctx.db.scalar("SELECT COUNT(*) FROM payment")
    with pytest.raises(ServiceError) as e:
        frontdesk.check_out(ctx, reception, checked_in["booking_code"], "CARD", late=False, decline=True)
    assert e.value.code == "MSG-41"
    assert ctx.db.scalar("SELECT COUNT(*) FROM payment") == before
    assert ctx.db.scalar("SELECT COUNT(*) FROM invoice") == 0
    assert bookings.get_booking(ctx, checked_in["booking_code"])["status"] == "CHECKED_IN"
    assert room_status(ctx, "201") == "OCCUPIED"


def test_a_crash_in_the_middle_of_check_out_rolls_back(ctx, checked_in, reception, monkeypatch):
    """Simulate the database failing after the payment was written."""
    real = bookings.next_code

    def boom(*a, **k):
        raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(bookings, "next_code", boom)
    with pytest.raises(sqlite3.OperationalError):
        frontdesk.check_out(ctx, reception, checked_in["booking_code"], "CASH", late=False)
    monkeypatch.setattr(bookings, "next_code", real)
    assert ctx.db.scalar("SELECT COUNT(*) FROM payment WHERE purpose='BALANCE'") == 0
    assert bookings.get_booking(ctx, checked_in["booking_code"])["status"] == "CHECKED_IN"
    frontdesk.check_out(ctx, reception, checked_in["booking_code"], "CASH", late=False)   # retry works


def test_invoice_is_immutable(ctx, checked_in, reception):
    inv = frontdesk.check_out(ctx, reception, checked_in["booking_code"], "CASH", late=False)
    with pytest.raises(sqlite3.IntegrityError):                                       # BR-25
        ctx.db.execute("UPDATE invoice SET total_amount=1 WHERE invoice_id=?", (inv["invoice_id"],))
    with pytest.raises(sqlite3.IntegrityError):
        ctx.db.execute("DELETE FROM invoice WHERE invoice_id=?", (inv["invoice_id"],))


def test_invoice_numbers_run_in_sequence_each_day(ctx, ids, reception):
    nos = []
    for room, t in (("101", STD), ("102", STD)):
        b = bookings.create_walk_in(ctx, reception, "Guest", "0933 444 555", ids["types"][t], 1, 1)
        frontdesk.check_in(ctx, reception, b["booking_code"], "B1234567", ids["rooms"][room])
        nos.append(frontdesk.check_out(ctx, reception, b["booking_code"], "CASH", False)["invoice_no"])
    assert nos == ["INV-20261105-0001", "INV-20261105-0002"]


def test_cannot_check_out_twice(ctx, checked_in, reception):
    frontdesk.check_out(ctx, reception, checked_in["booking_code"], "CASH", False)
    with pytest.raises(ServiceError):
        frontdesk.check_out(ctx, reception, checked_in["booking_code"], "CASH", False)


def test_shortened_stay_refunds_the_surplus_deposit(ctx, ids, customer, reception):
    from hotel.services import payments
    b = bookings.create_booking(ctx, customer, ids["types"][STD], "2026-11-05", "2026-11-15", 2)     # 7.5M, 2.25M deposit
    payments.pay_deposit(ctx, b, "CARD", customer)
    bookings.confirm_booking(ctx, reception, b["booking_code"])
    frontdesk.check_in(ctx, reception, b["booking_code"], "079091234567", ids["rooms"]["101"])
    ctx.db.execute("UPDATE booking SET room_charge=750000, check_out_date='2026-11-06' WHERE booking_code=?",
                   (b["booking_code"],))                                      # guest leaves after one night
    frontdesk.check_out(ctx, reception, b["booking_code"], None, False)       # total 825,000 < 2,250,000 paid
    refund = ctx.db.one("SELECT * FROM refund")
    assert refund["amount"] == 2_250_000 - 825_000 and refund["status"] == "COMPLETED"


def test_check_out_is_audited(ctx, checked_in, reception):
    frontdesk.check_out(ctx, reception, checked_in["booking_code"], "CASH", False)
    actions = [r["action"] for r in ctx.db.all("SELECT action FROM audit_log ORDER BY log_id")]
    assert actions[-1] == "CHECK_OUT" and "CHECK_IN" in actions and "CONFIRM_BOOKING" in actions   # CR-05

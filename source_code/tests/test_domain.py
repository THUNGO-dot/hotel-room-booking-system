"""Unit tests of the domain layer: the 16 classes of the class diagram (report Chapter 5).

These tests need no database and no browser: they call the classes directly, the way the
class diagram describes them. A last group checks the use-case operations of the actor classes.
"""
from datetime import date, datetime, timedelta

import pytest

from hotel.context import PaymentGateway, SimulatedMailer
from hotel.domain import (Account, AccountStatus, AddOnService, Admin, AuditLog, Booking, BookingStatus,
                          Customer, Invoice, Notification, Payment, PricePeriod, Receptionist, Refund,
                          ResetToken, Role, Room, RoomStatus, RoomType, ServiceUsage)
from hotel.errors import ServiceError
from hotel.settings import Settings

CFG = Settings()
NOW = datetime(2026, 11, 5, 10, 0)

# every class and operation named in Fig 5.1 (snake_case of the diagram's camelCase)
DIAGRAM = {
    "Account": ["login", "logout", "change_password", "request_reset"],
    "ResetToken": ["is_valid", "mark_used"],
    "AuditLog": ["record"],
    "Receptionist": ["confirm_booking", "reject_booking", "create_walk_in", "check_in", "check_out", "record_payment"],
    "Customer": ["register", "update_profile", "search_rooms", "create_booking", "cancel_booking", "view_history"],
    "Admin": ["manage_staff", "manage_rooms", "manage_prices", "view_reports"],
    "RoomType": ["get_price"],
    "PricePeriod": ["overlaps"],
    "Booking": ["calculate_deposit", "confirm", "reject", "cancel", "check_in", "check_out", "calculate_bill", "expire"],
    "Room": ["change_status", "is_available"],
    "ServiceUsage": ["get_amount"],
    "AddOnService": ["deactivate"],
    "Payment": ["process"],
    "Refund": ["calculate_refund"],
    "Invoice": ["generate", "export_pdf", "print"],
    "Notification": ["send", "retry"],
}


def test_every_class_and_operation_of_the_class_diagram_exists():
    import hotel.domain as domain
    assert len(DIAGRAM) == 16
    for cls_name, methods in DIAGRAM.items():
        cls = getattr(domain, cls_name)
        for m in methods:
            assert callable(getattr(cls, m)), f"{cls_name}.{m}"


def make_account(cls=Customer, **kw):
    base = dict(account_id=1, email="a@b.c", password_hash=Account.hash_password("secret123"),
                full_name="An", phone="0900000000")
    base.update(kw)
    return cls(**base)


def make_booking(**kw):
    base = dict(booking_id=1, booking_code="HB-20261105-0001", room_type_id=2, guest_name="An", guest_phone="0900",
                check_in_date=date(2026, 11, 10), check_out_date=date(2026, 11, 12), guests=2,
                status="PENDING", room_charge=2_400_000, deposit_amount=720_000,
                hold_expires_at=NOW + timedelta(hours=24), paid=0)
    base.update(kw)
    return Booking(**base)


# ------------------------------------------------------------------ Account
def test_account_is_abstract_and_roles_inherit_it():
    with pytest.raises(TypeError):
        Account(1, "a@b.c", "x", "An", "0900")
    for cls, role in ((Customer, Role.CUSTOMER), (Receptionist, Role.RECEPTIONIST), (Admin, Role.ADMIN)):
        assert issubclass(cls, Account) and make_account(cls).role == role


def test_account_from_row_picks_the_subclass(ctx, ids):
    from hotel.services import accounts
    assert isinstance(Account.from_row(accounts.get(ctx, ids["customer"])), Customer)
    assert isinstance(Account.from_row(accounts.get(ctx, ids["reception"])), Receptionist)
    assert isinstance(Account.from_row(accounts.get(ctx, ids["admin"])), Admin)


def test_login_counts_failures_and_locks_on_the_fifth():
    acct = make_account()
    for _ in range(4):
        with pytest.raises(ServiceError) as e:
            acct.login("wrong-pass", 5)
        assert e.value.code == "MSG-06"
    with pytest.raises(ServiceError) as e:
        acct.login("wrong-pass", 5)
    assert e.value.code == "MSG-07" and acct.status == AccountStatus.LOCKED
    with pytest.raises(ServiceError):
        acct.login("secret123", 5)  # a locked account stays locked even with the right password


def test_login_success_resets_the_counter_and_logout_ends_it():
    acct = make_account(failed_logins=3)
    acct.login("secret123", 5)
    assert acct.failed_logins == 0 and acct.signed_in
    acct.logout()
    assert not acct.signed_in


def test_change_password_needs_the_current_one_and_a_long_enough_new_one():
    acct = make_account()
    with pytest.raises(ServiceError) as e:
        acct.change_password("nope", "newpassword1")
    assert e.value.code == "MSG-12"
    with pytest.raises(ServiceError):
        acct.change_password("secret123", "short")
    acct.change_password("secret123", "newpassword1")
    acct.login("newpassword1", 5)


def test_request_reset_returns_a_hashed_single_use_token():
    acct = make_account()
    token, code = acct.request_reset(NOW, 30)
    assert len(code) == 6 and token.code_hash != code
    assert token.matches(code) and not token.matches("000000x")
    assert token.is_valid(NOW + timedelta(minutes=29))
    assert not token.is_valid(NOW + timedelta(minutes=31))
    token.mark_used()
    assert not token.is_valid(NOW)


def test_audit_log_record_writes_a_row(ctx, ids):
    log = AuditLog(ids["admin"], "TEST", "room", 7, "x" * 600, ctx.now_s())
    log_id = log.record(ctx.db)
    row = ctx.db.one("SELECT * FROM audit_log WHERE log_id=?", (log_id,))
    assert row["action"] == "TEST" and len(row["details"]) == 500


# ------------------------------------------------------------------ catalogue
def test_price_period_overlap_rule():
    a = PricePeriod(1, date(2026, 12, 1), date(2026, 12, 10), 900_000, 1)
    assert a.overlaps(PricePeriod(1, date(2026, 12, 10), date(2026, 12, 20), 1, 2))      # share the 10th
    assert not a.overlaps(PricePeriod(1, date(2026, 12, 11), date(2026, 12, 20), 1, 2))
    assert not a.overlaps(PricePeriod(2, date(2026, 12, 1), date(2026, 12, 10), 1, 2))   # another type
    assert not a.overlaps(PricePeriod(1, date(2026, 12, 1), date(2026, 12, 10), 1, 1))   # itself
    with pytest.raises(ServiceError):
        PricePeriod(1, date(2026, 12, 1), date(2026, 12, 2), 0).validate()
    with pytest.raises(ServiceError):
        PricePeriod(1, date(2026, 12, 5), date(2026, 12, 2), 10).validate()


def test_room_type_price_and_quote():
    t = RoomType(1, "Deluxe", 3, periods=[PricePeriod(1, date(2026, 11, 1), date(2026, 11, 30), 1_000_000),
                                          PricePeriod(1, date(2026, 12, 1), date(2026, 12, 31), 1_500_000)])
    assert t.get_price("2026-11-15") == 1_000_000 and t.get_price(date(2027, 1, 1)) is None
    assert t.quote("2026-11-29", "2026-12-02") == 1_000_000 + 1_000_000 + 1_500_000
    with pytest.raises(ServiceError) as e:
        t.quote("2026-12-30", "2027-01-02")
    assert e.value.key == "NO_PRICE"
    assert t.can_host(3) and not t.can_host(4)


def test_room_availability_and_status_changes():
    room = Room(5, 2, "201")
    stay = make_booking(room_id=5, status="CONFIRMED")
    assert room.is_available("2026-11-12", "2026-11-14", [stay])            # back to back: free
    assert not room.is_available("2026-11-11", "2026-11-13", [stay])
    room.change_status("MAINTENANCE")
    assert not room.is_available("2026-12-01", "2026-12-02")
    room.change_status("AVAILABLE")
    room.occupy()
    with pytest.raises(ServiceError) as e:
        room.change_status("MAINTENANCE")
    assert e.value.code == "MSG-51"
    room.vacate()
    assert room.status == RoomStatus.CLEANING
    with pytest.raises(ServiceError):
        room.change_status("OCCUPIED")


def test_service_usage_amount_and_add_on_service():
    assert ServiceUsage(1, 1, 3, 90_000).get_amount() == 270_000
    svc = AddOnService(1, "Laundry", 50_000)
    svc.deactivate()
    assert svc.active is False
    with pytest.raises(ServiceError):
        AddOnService(1, "Free", 0).validate()


# -------------------------------------------------------------------- Booking
@pytest.mark.parametrize("charge, expected", [(2_400_000, 720_000), (1_250_000, 375_000), (1_234_000, 370_000)])
def test_booking_calculate_deposit_rounds_to_1000(charge, expected):
    b = make_booking(room_charge=charge)
    assert b.calculate_deposit(CFG) == expected == b.deposit_amount


def test_booking_confirm_reject_cancel_expire_rules():
    assert make_booking(paid=720_000).confirm().status == BookingStatus.CONFIRMED
    with pytest.raises(ServiceError) as e:
        make_booking(paid=0).confirm()
    assert e.value.key == "DEPOSIT_NOT_PAID"
    with pytest.raises(ServiceError) as e:
        make_booking(status="EXPIRED").confirm()
    assert e.value.code == "MSG-21"
    with pytest.raises(ServiceError) as e:
        make_booking().reject("  ")
    assert e.value.code == "MSG-29"
    assert make_booking().reject("Full").status == BookingStatus.REJECTED
    assert make_booking(status="CONFIRMED").cancel().status == BookingStatus.CANCELLED
    with pytest.raises(ServiceError) as e:
        make_booking(status="CHECKED_IN").cancel()
    assert e.value.code == "MSG-26"
    late = make_booking(hold_expires_at=NOW - timedelta(minutes=1))
    assert late.is_hold_overdue(NOW) and late.expire().status == BookingStatus.EXPIRED
    assert not make_booking(hold_expires_at=NOW - timedelta(hours=1), paid=720_000).is_hold_overdue(NOW)


def test_booking_check_in_and_check_out():
    b = make_booking(status="CONFIRMED")
    room = Room(9, 2, "202")
    with pytest.raises(ServiceError) as e:
        b.check_in(room, "079123456789", date(2026, 11, 9))       # arrival date not reached
    assert e.value.code == "MSG-31"
    with pytest.raises(ServiceError) as e:
        b.check_in(room, "12", date(2026, 11, 10))
    assert e.value.key == "ID_REQUIRED"
    with pytest.raises(ServiceError) as e:
        b.check_in(Room(9, 1, "101"), "079123456789", date(2026, 11, 10))   # wrong room type
    assert e.value.code == "MSG-32"
    b.check_in(room, "079123456789", date(2026, 11, 10))
    assert b.status == BookingStatus.CHECKED_IN and b.room_id == 9 and room.status == RoomStatus.OCCUPIED
    b.check_out(room)
    assert b.status == BookingStatus.COMPLETED and room.status == RoomStatus.CLEANING


def test_booking_bill_late_fee_and_vat():
    b = make_booking(status="CHECKED_IN", paid=720_000)
    bill = b.calculate_bill(CFG, [ServiceUsage(1, 1, 2, 100_000)], late=True)
    assert bill["service_charge"] == 200_000
    assert bill["late_fee"] == 600_000                       # half of one night (2 nights, 2,400,000)
    assert bill["subtotal"] == 3_200_000 and bill["vat"] == 320_000 and bill["total"] == 3_520_000
    assert bill["balance"] == 3_520_000 - 720_000
    assert b.is_late(datetime(2026, 11, 12, 12, 1)) and not b.is_late(datetime(2026, 11, 12, 12, 0))


# -------------------------------------------------- Payment, Refund, Invoice, Notification
def test_payment_process_uses_the_gateway():
    pay = Payment(1, 720_000, "CARD", "DEPOSIT").process(PaymentGateway())
    assert pay.transaction_ref.startswith("CRD")
    with pytest.raises(ServiceError) as e:
        Payment(1, 720_000, "CARD", "DEPOSIT").process(PaymentGateway(), decline=True)
    assert e.value.code == "MSG-41"
    assert Payment(1, 100_000, "CASH", "BALANCE").process(PaymentGateway(), decline=True).transaction_ref.startswith("CSH")


@pytest.mark.parametrize("hours, rate", [(100, 100), (48, 100), (47.99, 50), (24, 50), (23.99, 0), (0, 0)])
def test_refund_rate_tiers(hours, rate):
    r = Refund(1)
    assert r.calculate_refund(1_000_000, hours, CFG) == rate * 10_000 and r.refund_rate == rate


def test_refund_settle():
    r = Refund(1)
    r.calculate_refund(500_000, rate=50)
    assert r.amount == 250_000
    r.settle(False)
    assert r.status.value == "PENDING"
    r.mark_completed()
    assert r.status.value == "COMPLETED"
    zero = Refund(1)
    zero.calculate_refund(500_000, rate=0)
    zero.settle(False)
    assert zero.status.value == "COMPLETED"


def test_invoice_generate_and_print():
    bill = make_booking(status="CHECKED_IN").calculate_bill(CFG, [], False)
    inv = Invoice("INV-20261112-0001", 1, "2026-11-12 11:00:00").generate(bill)
    assert inv.total_amount == 2_640_000 and inv.vat_amount == 240_000
    text = inv.print({"guest_name": "An", "booking_code": "HB-1"})
    assert "INV-20261112-0001" in text and "2,640,000" in text


def test_notification_retries_up_to_three_times():
    mailer = SimulatedMailer()
    mailer.fail_next = 2
    n = Notification(1, "BOOKING_CONFIRMATION", "a@b.c")
    assert n.retry(mailer, "s", "b", NOW, 3) and n.attempts == 3 and n.status.value == "SENT"
    mailer.fail_next = 5
    n = Notification(1, "BOOKING_CONFIRMATION", "a@b.c")
    assert not n.retry(mailer, "s", "b", NOW, 3) and n.attempts == 3 and n.status.value == "FAILED"


# --------------------------------------------------- actor classes drive the use cases
def test_actor_classes_run_a_whole_stay(ctx, ids):
    from hotel.services import accounts
    cust = Customer.register(ctx, "Lan Pham", "lan.pham@example.com", "0911222333", "password1")
    assert isinstance(cust, Customer) and cust.role == Role.CUSTOMER
    rec = Account.from_row(accounts.get(ctx, ids["reception"]))
    adm = Account.from_row(accounts.get(ctx, ids["admin"]))

    results = cust.search_rooms(ctx, "2026-11-10", "2026-11-12")
    assert results and results[0]["can_book"]
    b = cust.create_booking(ctx, ids["types"][1], "2026-11-10", "2026-11-12", 2)
    assert b["status"] == "PENDING"
    rec.record_payment(ctx, b, "CARD")
    confirmed, email = rec.confirm_booking(ctx, b["booking_code"])
    assert confirmed["status"] == "CONFIRMED" and email == "SENT"
    assert len(cust.view_history(ctx)) == 1

    ctx.clock.advance(days=5)  # 10 Nov
    from hotel.services import frontdesk
    room = frontdesk.available_rooms(ctx, ids["types"][1])[0]
    assert rec.check_in(ctx, b["booking_code"], "079123456789", room["room_id"])["status"] == "CHECKED_IN"
    invoice = rec.check_out(ctx, b["booking_code"], "CASH", False)
    assert invoice["invoice_no"].startswith("INV-")

    assert adm.manage_staff(ctx) and adm.manage_rooms(ctx) and adm.manage_prices(ctx, ids["types"][1])
    report = adm.view_reports(ctx, "revenue", "2026-11-01", "2026-11-30")
    assert report

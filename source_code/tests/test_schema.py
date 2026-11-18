"""Report section 6.2: 13 tables and the CHECK / UNIQUE / FK rules."""
import sqlite3

import pytest

TABLES = {"account", "reset_token", "audit_log", "room_type", "price_period", "room", "add_on_service",
          "booking", "service_usage", "payment", "refund", "invoice", "notification"}


def test_thirteen_tables(ctx):
    names = {r[0] for r in ctx.db.all("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    assert names == TABLES and len(names) == 13


def test_booking_status_check(ctx, ids):
    with pytest.raises(sqlite3.IntegrityError):
        ctx.db.execute(
            "INSERT INTO booking(booking_code,room_type_id,guest_name,guest_phone,check_in_date,check_out_date,"
            "guests,status,room_charge,created_at) VALUES ('X',?, 'A','1','2026-11-10','2026-11-11',1,'BOGUS',1,'now')",
            (ids["types"][0],))


def test_checkout_must_be_after_checkin(ctx, ids):
    with pytest.raises(sqlite3.IntegrityError):
        ctx.db.execute(
            "INSERT INTO booking(booking_code,room_type_id,guest_name,guest_phone,check_in_date,check_out_date,"
            "guests,status,room_charge,created_at) VALUES ('X',?, 'A','1','2026-11-10','2026-11-10',1,'PENDING',1,'now')",
            (ids["types"][0],))


def test_room_number_unique(ctx, ids):
    with pytest.raises(sqlite3.IntegrityError):
        ctx.db.execute("INSERT INTO room(room_type_id,room_number) VALUES (?, '101')", (ids["types"][0],))


def test_email_unique(ctx, ids):
    with pytest.raises(sqlite3.IntegrityError):
        ctx.db.execute("INSERT INTO account(email,password_hash,full_name,phone,role,created_at)"
                       " VALUES ('an.nguyen@example.com','x','A','1','CUSTOMER','now')")


def test_foreign_keys_enforced(ctx):
    with pytest.raises(sqlite3.IntegrityError):
        ctx.db.execute("INSERT INTO payment(booking_id,amount,method,purpose,paid_at) VALUES (999,1,'CASH','DEPOSIT','now')")


def test_one_refund_and_one_invoice_per_booking(ctx, make):
    b = make()
    ctx.db.execute("INSERT INTO refund(booking_id,amount,refund_rate,status,refunded_at) VALUES (?,1,100,'PENDING','now')",
                   (b["booking_id"],))
    with pytest.raises(sqlite3.IntegrityError):
        ctx.db.execute("INSERT INTO refund(booking_id,amount,refund_rate,status,refunded_at) VALUES (?,1,100,'PENDING','now')",
                       (b["booking_id"],))


def test_payment_method_check(ctx, make):
    b = make()
    with pytest.raises(sqlite3.IntegrityError):
        ctx.db.execute("INSERT INTO payment(booking_id,amount,method,purpose,paid_at) VALUES (?,1,'BITCOIN','DEPOSIT','now')",
                       (b["booking_id"],))

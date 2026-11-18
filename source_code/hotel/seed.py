"""Demo data. Dates are relative to "today", so the demo always has current arrivals,
in-house guests and two months of history for the reports."""
from __future__ import annotations

import random
from datetime import timedelta

from werkzeug.security import generate_password_hash

from .context import DT, Context
from .services import bookings, catalog, frontdesk

CUSTOMER = ("an.nguyen@example.com", "12345678")
RECEPTIONIST = ("chau.le@grandhorizon.example", "Staff@1234")
ADMIN = ("bao.tran@grandhorizon.example", "Admin@1234")

ROOM_TYPES = [
    # name, bed, cap, m2, amenities, rate, description
    ("Standard King Room", "1 double bed", 2, 30, "Free Wi-Fi, City view", 750_000,
     "A quiet room on a higher floor with a city skyline view, a double bed and a rain shower."),
    ("Deluxe Ocean Suite", "1 king bed", 2, 45, "Free Wi-Fi, Ocean view, Breakfast included", 1_200_000,
     "A corner suite with an arched window onto the sea, a soaking tub and a small work desk."),
    ("Executive Family Suite", "2 queen beds", 4, 65, "Private balcony, Jacuzzi bathtub, Free Wi-Fi", 1_900_000,
     "Two queen beds, a private balcony and a jacuzzi bathtub, planned for a family of up to four."),
]
ROOMS = [("101", 0, 1), ("102", 0, 1), ("103", 0, 1), ("104", 0, 1), ("105", 0, 1),
         ("201", 1, 2), ("202", 1, 2), ("205", 1, 2), ("302", 1, 3), ("305", 1, 3),
         ("401", 2, 4), ("501", 2, 5)]
SERVICES = [("Laundry (per item)", 50_000), ("Minibar", 80_000), ("Airport transfer", 300_000),
            ("Spa treatment", 450_000)]


def _account(ctx: Context, email, password, name, phone, role, status="ACTIVE") -> int:
    return ctx.db.insert(
        "INSERT INTO account(email,password_hash,full_name,phone,role,status,failed_logins,created_at)"
        " VALUES (?,?,?,?,?,?,0,?)",
        (email, generate_password_hash(password), name, phone, role, status, ctx.now_s()))


def seed_base(ctx: Context) -> dict:
    """Catalog and accounts only: enough to run the system."""
    ids: dict = {"types": [], "rooms": {}}
    today = ctx.today()
    with ctx.db.transaction():
        ids["customer"] = _account(ctx, *CUSTOMER, "Nguyen Van An", "0901 234 567", "CUSTOMER")
        ids["reception"] = _account(ctx, *RECEPTIONIST, "Le Minh Chau", "0902 111 222", "RECEPTIONIST")
        ids["admin"] = _account(ctx, *ADMIN, "Tran Quoc Bao", "0903 333 444", "ADMIN")
        _account(ctx, "han.pham@grandhorizon.example", "Staff@1234", "Pham Gia Han", "0904 555 666",
                 "RECEPTIONIST", status="LOCKED")
        for name, bed, cap, area, amen, rate, desc in ROOM_TYPES:
            tid = ctx.db.insert(
                "INSERT INTO room_type(name,description,capacity,amenities,bed_type,area_m2,active) VALUES (?,?,?,?,?,?,1)",
                (name, desc, cap, amen, bed, area))
            ids["types"].append(tid)
            # one long price period covering last year and the next two
            ctx.db.insert("INSERT INTO price_period(room_type_id,start_date,end_date,price_per_night) VALUES (?,?,?,?)",
                          (tid, f"{today.year - 1}-01-01", f"{today.year + 2}-12-31", rate))
        for number, ti, floor in ROOMS:
            status = "MAINTENANCE" if number == "305" else "AVAILABLE"
            ids["rooms"][number] = ctx.db.insert(
                "INSERT INTO room(room_type_id,room_number,floor,status,active) VALUES (?,?,?,?,1)",
                (ids["types"][ti], number, floor, status))
        for name, price in SERVICES:
            ctx.db.insert("INSERT INTO add_on_service(name,unit_price,active) VALUES (?,?,1)", (name, price))
    return ids


def seed_demo(ctx: Context) -> None:
    """Base data plus bookings in every state and two months of paid history."""
    ids = seed_base(ctx)
    rnd = random.Random(2026)
    today = ctx.today()
    rec = ctx.db.one("SELECT * FROM account WHERE account_id=?", (ids["reception"],))
    cust = ctx.db.one("SELECT * FROM account WHERE account_id=?", (ids["customer"],))
    types = ids["types"]
    guests = ["Tran Minh Khoa", "Le Thi Mai", "Do Quang Huy", "Bui Thanh Tam", "Pham Thu Ha", "Vo Hoang Nam",
              "Hoang Van Phuc", "Nguyen Thi Lan", "Dang Gia Bao", "Ly Khanh Vy", "Mai Duc Anh", "Ngo Thanh Truc"]
    phones = [f"09{rnd.randint(10, 99)} {rnd.randint(100, 999)} {rnd.randint(100, 999)}" for _ in guests]

    def insert(code_date, type_idx, ci, co, name, phone, status, account=None, created=None, guests_n=2):
        """Insert a booking with a faked creation time and price from the price list."""
        with ctx.db.transaction():
            tid = types[type_idx]
            charge = catalog.quote(ctx, tid, ci.isoformat(), co.isoformat())
            created = created or (ci - timedelta(days=rnd.randint(3, 20)))
            stamp = created.strftime("%Y%m%d")
            n = ctx.db.scalar("SELECT COUNT(*) FROM booking WHERE booking_code LIKE ?", (f"HB-{stamp}-%",)) + 1
            code = f"HB-{stamp}-{n:04d}"
            from .services.common import deposit_for
            dep = 0 if account is None else deposit_for(ctx, charge)
            bid = ctx.db.insert(
                "INSERT INTO booking(booking_code,account_id,room_type_id,guest_name,guest_phone,check_in_date,"
                "check_out_date,guests,status,room_charge,deposit_amount,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (code, account, tid, name, phone, ci.isoformat(), co.isoformat(), guests_n, status, charge, dep,
                 created.strftime(DT) if hasattr(created, "hour") else f"{created.isoformat()} 09:00:00"))
            return bid, code, charge, dep

    def pay(bid, amount, method, purpose, when):
        ctx.db.insert("INSERT INTO payment(booking_id,amount,method,purpose,transaction_ref,paid_at) VALUES (?,?,?,?,?,?)",
                      (bid, amount, method, purpose, "SEED", f"{when.isoformat()} 10:00:00"))

    def complete(bid, charge, dep, ci, co, room_number, extra=0):
        """History: a finished stay with deposit, balance, invoice."""
        sub = charge + extra
        vat = round(sub * ctx.cfg.vat_rate)
        total = sub + vat
        if dep:
            pay(bid, dep, rnd.choice(["CARD", "E_WALLET"]), "DEPOSIT", ci - timedelta(days=rnd.randint(2, 10)))
        pay(bid, total - dep, rnd.choice(["CASH", "CARD"]), "BALANCE", co)
        stamp = co.strftime("%Y%m%d")
        n = ctx.db.scalar("SELECT COUNT(*) FROM invoice WHERE invoice_no LIKE ?", (f"INV-{stamp}-%",)) + 1
        ctx.db.insert(
            "INSERT INTO invoice(invoice_no,booking_id,issued_at,room_charge,service_charge,late_fee,vat_amount,total_amount)"
            " VALUES (?,?,?,?,?,0,?,?)", (f"INV-{stamp}-{n:04d}", bid, f"{co.isoformat()} 11:30:00", charge, extra, vat, total))
        ctx.db.execute("UPDATE booking SET room_id=?, id_document=? WHERE booking_id=?",
                       (ids["rooms"][room_number], f"07909{rnd.randint(1000, 9999)}", bid))

    # --- history: ~two months of completed stays so the reports have real numbers.
    # Every stay gets a real room that is free for those nights, so the history never overbooks.
    pools = {0: ["101", "102", "103", "104", "105"], 1: ["201", "202", "205", "302"], 2: ["401", "501"]}
    free_from = {room: today - timedelta(days=90) for rooms in pools.values() for room in rooms}
    cursor = today - timedelta(days=62)
    k = 0
    while cursor < today - timedelta(days=2):
        for _ in range(rnd.choice([3, 4, 4, 5, 6])):
            ti = rnd.choices([0, 1, 2], weights=[5, 4, 2])[0]
            n = rnd.choice([1, 2, 2, 3, 3, 4])
            ci, co = cursor, cursor + timedelta(days=n)
            if co >= today - timedelta(days=1):
                continue
            room = next((r for r in pools[ti] if free_from[r] <= ci), None)
            if room is None:
                continue
            free_from[room] = co
            idx = k % len(guests)
            account = ids["customer"] if k % 7 == 0 else None
            bid, code, charge, dep = insert(None, ti, ci, co, guests[idx], phones[idx], "COMPLETED", account)
            complete(bid, charge, dep, ci, co, room, extra=rnd.choice([0, 0, 50_000, 160_000]))
            k += 1
        cursor += timedelta(days=1)

    # a cancelled booking with a refund in the history
    ci = today - timedelta(days=12)
    bid, code, charge, dep = insert(None, 1, ci, ci + timedelta(days=2), "Dang Gia Bao", phones[8], "CANCELLED",
                                    ids["customer"], created=ci - timedelta(days=9))
    pay(bid, dep, "CARD", "DEPOSIT", ci - timedelta(days=8))
    ctx.db.insert("INSERT INTO refund(booking_id,amount,refund_rate,reason,status,refunded_at) VALUES (?,?,100,?,'COMPLETED',?)",
                  (bid, dep, "Cancelled by the guest", f"{(ci - timedelta(days=5)).isoformat()} 09:00:00"))

    # --- today: arrivals waiting, guests in-house, bookings to review
    now = ctx.now()
    for i, (ti, name, phone) in enumerate([(1, guests[0], phones[0]), (0, guests[1], phones[1]),
                                           (2, guests[2], phones[2])]):
        bid, code, charge, dep = insert(None, ti, today, today + timedelta(days=2), name, phone, "CONFIRMED",
                                        ids["customer"] if i == 0 else None, created=now - timedelta(days=3))
        if dep:
            pay(bid, dep, "CARD", "DEPOSIT", today - timedelta(days=2))
    for room, ti, name, phone, nts in [("205", 1, guests[7], phones[7], 2), ("501", 2, guests[6], phones[6], 2)]:
        ci = today - timedelta(days=1)
        bid, code, charge, dep = insert(None, ti, ci, ci + timedelta(days=nts), name, phone, "CHECKED_IN",
                                        ids["customer"] if room == "205" else None, created=now - timedelta(days=5))
        if dep:
            pay(bid, dep, "CARD", "DEPOSIT", ci - timedelta(days=3))
        ctx.db.execute("UPDATE booking SET room_id=?, id_document='079091234567' WHERE booking_id=?",
                       (ids["rooms"][room], bid))
        ctx.db.execute("UPDATE room SET status='OCCUPIED' WHERE room_id=?", (ids["rooms"][room],))
    ctx.db.execute("UPDATE room SET status='CLEANING' WHERE room_number='103'")

    # upcoming bookings in several states
    up1 = today + timedelta(days=5)
    bid, code, charge, dep = insert(None, 1, up1, up1 + timedelta(days=2), "Nguyen Van An", "0901 234 567", "PENDING",
                                    ids["customer"], created=now - timedelta(hours=3))
    ctx.db.execute("UPDATE booking SET hold_expires_at=? WHERE booking_id=?",
                   ((now + timedelta(hours=21)).strftime(DT), bid))
    up2 = today + timedelta(days=9)
    bid, code, charge, dep = insert(None, 0, up2, up2 + timedelta(days=2), guests[4], phones[4], "PENDING", ids["customer"],
                                    created=now - timedelta(hours=5))
    pay(bid, dep, "E_WALLET", "DEPOSIT", today)
    ctx.db.execute("UPDATE booking SET hold_expires_at=? WHERE booking_id=?",
                   ((now + timedelta(hours=19)).strftime(DT), bid))
    up3 = today + timedelta(days=12)
    bid, code, charge, dep = insert(None, 2, up3, up3 + timedelta(days=3), guests[5], phones[5], "PENDING", ids["customer"],
                                    created=now - timedelta(hours=1))
    ctx.db.execute("UPDATE booking SET hold_expires_at=? WHERE booking_id=?",
                   ((now + timedelta(hours=23)).strftime(DT), bid))
    up4 = today + timedelta(days=20)
    bid, code, charge, dep = insert(None, 0, up4, up4 + timedelta(days=3), "Nguyen Van An", "0901 234 567", "CONFIRMED",
                                    ids["customer"], created=now - timedelta(days=2))
    pay(bid, dep, "CARD", "DEPOSIT", today - timedelta(days=1))
    # Laundry already on one in-house bill
    in_house = ctx.db.one("SELECT booking_id FROM booking WHERE status='CHECKED_IN' ORDER BY booking_id LIMIT 1")
    ctx.db.insert("INSERT INTO service_usage(booking_id,service_id,quantity,unit_price,used_at) VALUES (?,1,2,50000,?)",
                  (in_house["booking_id"], ctx.now_s()))


def init_demo(ctx: Context) -> None:
    ctx.db.init_schema()
    seed_demo(ctx)

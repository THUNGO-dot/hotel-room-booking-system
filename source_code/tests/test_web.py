"""Web layer: access control (NFR-02), CSRF, session timeout (CR-01), ownership (NFR-04),
the full booking journey through real HTTP requests, and a render check of every screen."""
import re
import sqlite3
from datetime import datetime

import pytest

from hotel import create_app, seed
from hotel.context import Clock, Context, SimulatedMailer
from hotel.db import Database
from hotel.seed import ADMIN, CUSTOMER, RECEPTIONIST
from hotel.settings import Settings

NOW = datetime(2026, 11, 5, 10, 0)


class Browser:
    """A test client that sends the CSRF token the way a real form would."""

    def __init__(self, app):
        self.app = app
        self.c = app.test_client()

    def token(self) -> str:
        with self.c.session_transaction() as s:
            if "csrf" in s:
                return s["csrf"]
        self.c.get("/login")                 # any page that renders a form sets the token
        with self.c.session_transaction() as s:
            return s["csrf"]

    def get(self, url, **kw):
        return self.c.get(url, **kw)

    def post(self, url, data=None, csrf=True, **kw):
        data = dict(data or {})
        if csrf:
            data["csrf_token"] = self.token()
        return self.c.post(url, data=data, **kw)

    def login(self, cred):
        r = self.post("/login", {"email": cred[0], "password": cred[1]})
        assert r.status_code == 302, r.data.decode()[:300]
        return self

    @staticmethod
    def text(r) -> str:
        return r.data.decode()


def build(tmp_path, demo_seed: bool):
    db_file = tmp_path / "web.db"
    cfg = Settings().with_overrides(database=str(db_file), demo_mode=True, secret_key="test-key")
    clock = Clock(NOW)
    if not demo_seed:                       # empty hotel: catalog and 4 accounts only
        db = Database(db_file)
        db.init_schema()
        seed.seed_base(Context(db, cfg, clock, SimulatedMailer()))
        db.close()
    app = create_app(cfg, clock=clock, csrf=True)
    app.config.update(TESTING=True)
    return app, clock, db_file


@pytest.fixture
def web(tmp_path):
    app, clock, db_file = build(tmp_path, demo_seed=False)

    class W:
        pass
    w = W()
    w.app, w.clock, w.db_file = app, clock, db_file
    w.guest = lambda: Browser(app)
    w.customer = lambda: Browser(app).login(CUSTOMER)
    w.reception = lambda: Browser(app).login(RECEPTIONIST)
    w.admin = lambda: Browser(app).login(ADMIN)
    w.mailer = app.extensions["hotel"].mailer

    def sql(query, params=()):
        con = sqlite3.connect(db_file)
        con.row_factory = sqlite3.Row
        try:
            return con.execute(query, params).fetchall()
        finally:
            con.close()
    w.sql = sql
    return w


def book(browser, type_id=2, ci="2026-11-10", co="2026-11-12", adults=2):
    r = browser.post(f"/book/{type_id}", {"ci": ci, "co": co, "adults": adults, "children": 0, "note": ""})
    assert r.status_code == 302, browser.text(r)[:500]
    return re.search(r"/bookings/(HB-\d{8}-\d{4})/pay", r.headers["Location"]).group(1)


# ------------------------------------------------------------------ access control
def test_anonymous_visitors_are_sent_to_login(web):
    g = web.guest()
    for url in ("/bookings", "/profile", "/desk/", "/desk/pending", "/admin/", "/admin/staff", "/book/1"):
        r = g.get(url)
        assert r.status_code == 302 and "/login" in r.headers["Location"], url


def test_public_pages_need_no_account(web):
    g = web.guest()
    for url in ("/", "/rooms/1", "/login", "/register", "/forgot"):
        assert g.get(url).status_code == 200, url


@pytest.mark.parametrize("url", ["/desk/", "/desk/pending", "/desk/walkin", "/admin/", "/admin/staff",
                                 "/admin/reports/revenue", "/admin/audit"])
def test_customer_is_refused_staff_screens_with_msg56(web, url):
    r = web.customer().get(url)
    assert r.status_code == 403
    assert "MSG-56" in Browser.text(r)


@pytest.mark.parametrize("url", ["/admin/", "/admin/rooms", "/admin/staff", "/admin/reports/occupancy"])
def test_receptionist_cannot_open_admin_screens(web, url):
    assert web.reception().get(url).status_code == 403


@pytest.mark.parametrize("url", ["/desk/", "/desk/pending", "/desk/inhouse", "/bookings", "/book/1"])
def test_admin_cannot_open_front_desk_or_customer_screens(web, url):
    assert web.admin().get(url).status_code == 403


def test_customer_cannot_post_to_staff_actions(web):
    c = web.customer()
    code = book(c)
    assert c.post(f"/desk/bookings/{code}/confirm").status_code == 403
    assert c.post("/admin/rooms/1/delete").status_code == 403
    assert web.sql("SELECT status FROM booking WHERE booking_code=?", (code,))[0]["status"] == "PENDING"


def test_a_customer_sees_only_their_own_bookings_and_invoices(web):
    owner = web.customer()
    code = book(owner)
    other = web.guest()
    other.post("/register", {"name": "Tran Thi Binh", "email": "binh@example.com", "phone": "0905 000 111",
                             "password": "binh-pass-1"})
    for url in (f"/bookings/{code}", f"/bookings/{code}/pay", f"/bookings/{code}/cancel", f"/bookings/{code}/change",
                f"/bookings/{code}/confirmation"):
        assert other.get(url).status_code == 403, url
    assert other.post(f"/bookings/{code}/cancel").status_code == 403
    assert code not in other.text(other.get("/bookings"))
    assert code in owner.text(owner.get("/bookings"))


# ------------------------------------------------------------------------- CSRF
def test_post_without_csrf_token_is_refused(web):
    g = web.guest()
    r = g.post("/login", {"email": CUSTOMER[0], "password": CUSTOMER[1]}, csrf=False)
    assert r.status_code == 400


def test_post_with_a_wrong_token_is_refused(web):
    g = web.guest()
    g.token()
    r = g.c.post("/login", data={"email": CUSTOMER[0], "password": CUSTOMER[1], "csrf_token": "forged"})
    assert r.status_code == 400


def test_logout_requires_post(web):
    c = web.customer()
    assert c.get("/logout").status_code == 405
    assert c.post("/logout").status_code == 302
    assert c.get("/bookings").status_code == 302


# -------------------------------------------------------------------- sessions
def test_session_expires_after_30_idle_minutes(web):
    c = web.customer()
    assert c.get("/bookings").status_code == 200
    web.clock.advance(minutes=29)
    assert c.get("/bookings").status_code == 200           # activity renews the timer
    web.clock.advance(minutes=29)
    assert c.get("/bookings").status_code == 200
    web.clock.advance(minutes=31)
    r = c.get("/bookings")
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    assert "signed out after 30 minutes" in c.text(c.get("/login"))


def test_login_page_redirects_signed_in_users_home(web):
    r = web.reception().get("/login")
    assert r.status_code == 302 and r.headers["Location"].endswith("/desk/")


def test_login_next_cannot_leave_the_site(web):
    g = web.guest()
    r = g.post("/login", {"email": CUSTOMER[0], "password": CUSTOMER[1], "next": "//evil.example/x"})
    assert r.status_code == 302 and "evil.example" not in r.headers["Location"]


def test_wrong_password_message_does_not_reveal_which_part_was_wrong(web):
    g = web.guest()
    a = g.text(g.post("/login", {"email": CUSTOMER[0], "password": "nope-nope"}))
    b = g.text(g.post("/login", {"email": "nobody@example.com", "password": "nope-nope"}))
    msg = re.search(r'role="alert"><svg.*?</svg><div>(.*?)</div>', a, re.S).group(1)
    assert msg in b


def test_five_wrong_passwords_lock_the_account(web):
    g = web.guest()
    for _ in range(5):
        g.post("/login", {"email": CUSTOMER[0], "password": "wrong-password"})
    r = g.post("/login", {"email": CUSTOMER[0], "password": CUSTOMER[1]})
    assert r.status_code == 200                              # still locked: the right password is refused


# --------------------------------------------------------------- full journey
def test_full_stay_from_search_to_invoice(web):
    # 1. the guest registers, searches and books a Deluxe room for 7-9 Nov (2 nights)
    g = web.guest()
    r = g.post("/register", {"name": "Le Thi Hoa", "email": "hoa@example.com", "phone": "0906 777 888",
                             "password": "hoa-pass-123"})
    assert r.status_code == 302
    page = g.text(g.get("/?ci=2026-11-07&co=2026-11-09&adults=2&children=0"))
    assert "Book now" in page and "VND" in page
    code = book(g, type_id=2, ci="2026-11-07", co="2026-11-09")

    # 2. the deposit page shows the amount due; paying it moves the booking to the front desk queue
    pay_page = g.text(g.get(f"/bookings/{code}/pay"))
    assert "Pay " in pay_page and "VND" in pay_page
    r = g.post(f"/bookings/{code}/pay", {"method": "CARD"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/confirmation")
    assert code in g.text(g.get(f"/bookings/{code}/confirmation"))

    # 3. the receptionist confirms; the customer gets an e-mail
    desk = web.reception()
    assert code in desk.text(desk.get("/desk/pending"))
    desk.post(f"/desk/bookings/{code}/confirm")
    assert web.sql("SELECT status FROM booking WHERE booking_code=?", (code,))[0]["status"] == "CONFIRMED"
    assert any(m["to"] == "hoa@example.com" and code in m["body"] for m in web.mailer.sent)

    # 4. arrival day: check in
    web.clock.advance(days=2)
    desk = web.reception()                                   # two days later the old session has expired
    assert code in desk.text(desk.get("/desk/checkin"))
    room = web.sql("SELECT room_id FROM room WHERE room_number='201'")[0]["room_id"]
    r = desk.post(f"/desk/checkin/{code}", {"id_document": "079091234567", "room": room})
    assert r.status_code == 302
    row = web.sql("SELECT status, room_id FROM booking WHERE booking_code=?", (code,))[0]
    assert row["status"] == "CHECKED_IN" and row["room_id"] == room

    # 5. a service is added during the stay
    laundry = web.sql("SELECT service_id FROM add_on_service ORDER BY service_id")[0]["service_id"]
    desk.post(f"/desk/services/{code}", {"service": laundry, "quantity": 2})
    assert web.sql("SELECT SUM(quantity) q FROM service_usage")[0]["q"] == 2

    # 6. check-out two days later, the balance is paid in cash and the invoice is issued
    web.clock.advance(days=2)
    desk = web.reception()
    co_page = desk.text(desk.get(f"/desk/checkout/{code}"))
    assert "Balance to collect" in co_page
    r = desk.post(f"/desk/checkout/{code}", {"method": "CASH", "late": "0"})
    assert r.status_code == 302 and "/invoices/INV-" in r.headers["Location"]
    inv_no = r.headers["Location"].rsplit("/", 1)[1]
    assert web.sql("SELECT status FROM booking WHERE booking_code=?", (code,))[0]["status"] == "COMPLETED"
    assert web.sql("SELECT status FROM room WHERE room_number='201'")[0]["status"] == "CLEANING"

    # 7. the guest can open and download the invoice; the totals add up with 10% VAT
    g = web.guest().login(("hoa@example.com", "hoa-pass-123"))
    page = g.text(g.get(f"/invoices/{inv_no}"))
    inv = web.sql("SELECT * FROM invoice WHERE invoice_no=?", (inv_no,))[0]
    assert inv["vat_amount"] == round((inv["room_charge"] + inv["service_charge"] + inv["late_fee"]) * 0.10)
    assert inv["total_amount"] == inv["room_charge"] + inv["service_charge"] + inv["late_fee"] + inv["vat_amount"]
    assert f"{inv['total_amount']:,} VND" in page
    pdf = g.get(f"/invoices/{inv_no}.pdf")
    assert pdf.status_code == 200 and pdf.mimetype == "application/pdf" and pdf.data.startswith(b"%PDF")
    paid = web.sql("SELECT SUM(amount) s FROM payment")[0]["s"]
    assert paid == inv["total_amount"]                       # deposit + balance = invoice total


def test_declined_card_leaves_the_booking_unpaid_and_held(web):
    c = web.customer()
    code = book(c)
    r = c.post(f"/bookings/{code}/pay", {"method": "CARD", "decline": "1"})
    assert r.status_code == 200 and "MSG-41" in c.text(r)
    assert web.sql("SELECT COUNT(*) n FROM payment")[0]["n"] == 0
    assert c.post(f"/bookings/{code}/pay", {"method": "CARD"}).status_code == 302   # retry works


def test_unpaid_hold_is_released_after_24_hours(web):
    c = web.customer()
    code = book(c)
    web.clock.advance(hours=25)
    c.get("/bookings")                                      # any request runs the expiry sweep
    assert web.sql("SELECT status FROM booking WHERE booking_code=?", (code,))[0]["status"] == "EXPIRED"


def test_last_room_cannot_be_booked_twice(web):
    # one Executive room only: make it the single room of its type
    exe = web.sql("SELECT room_type_id FROM room_type WHERE name LIKE 'Exec%'")[0]["room_type_id"]
    n = web.sql("SELECT COUNT(*) n FROM room WHERE room_type_id=?", (exe,))[0]["n"]
    c = web.customer()
    for _ in range(n):
        book(c, type_id=exe, ci="2026-12-01", co="2026-12-03", adults=2)
    r = c.post(f"/book/{exe}", {"ci": "2026-12-01", "co": "2026-12-03", "adults": 2, "children": 0, "note": ""})
    assert r.status_code == 200 and "MSG-19" in c.text(r)


def test_cancelling_early_refunds_in_full_and_the_desk_pays_it_out(web):
    c = web.customer()
    code = book(c, ci="2026-11-20", co="2026-11-22")
    c.post(f"/bookings/{code}/pay", {"method": "CARD"})
    page = c.text(c.get(f"/bookings/{code}/cancel"))
    assert "100% refund" in page
    r = c.post(f"/bookings/{code}/cancel")
    assert r.status_code == 302
    ref = web.sql("SELECT * FROM refund")[0]
    assert ref["refund_rate"] == 100 and ref["status"] == "PENDING"
    desk = web.reception()
    assert code in desk.text(desk.get("/desk/pending"))
    desk.post(f"/desk/refunds/{ref['refund_id']}/complete")
    assert web.sql("SELECT status FROM refund")[0]["status"] == "COMPLETED"


def test_changing_dates_inside_48_hours_is_refused(web):
    c = web.customer()
    code = book(c, ci="2026-11-06", co="2026-11-08")         # arrival in 14 hours
    page = c.text(c.get(f"/bookings/{code}"))
    assert "Change dates" not in page or "cannot" in page.lower() or "up to 48 hours" in page
    r = c.post(f"/bookings/{code}/change", {"ci": "2026-11-09", "co": "2026-11-11"})
    assert r.status_code == 200 and "at least 48 hours" in c.text(r)


def test_dates_and_money_use_vietnamese_conventions(web):
    c = web.customer()
    code = book(c, ci="2026-11-10", co="2026-11-12")
    page = c.text(c.get("/bookings"))
    assert "10/11/2026" in page and "12/11/2026" in page    # CR-02 dd/mm/yyyy
    assert re.search(r"\d{1,3}(,\d{3})+ VND", page)         # thousands separators


def test_lists_show_ten_rows_per_page(web):
    c = web.customer()
    for i in range(12):
        book(c, type_id=1, ci=f"2026-12-{i + 1:02d}", co=f"2026-12-{i + 2:02d}")
    p1, p2 = c.text(c.get("/bookings")), c.text(c.get("/bookings?page=2"))
    code_cell = r'class="code">(HB-\d{8}-\d{4})'        # the table rows, not the "booking created" notices
    assert len(set(re.findall(code_cell, p1))) == 10
    assert len(set(re.findall(code_cell, p2))) == 2
    assert "Page 1 of 2" in p1


def test_staff_cannot_book_online(web):
    assert web.reception().get("/book/1?ci=2026-11-10&co=2026-11-12").status_code == 403


# -------------------------------------------------------------------- admin
def test_admin_exports_are_real_files_and_are_audited(web):
    a = web.admin()
    q = "start=2026-10-01&end=2026-11-05"
    x = a.get(f"/admin/reports/occupancy?{q}&format=xlsx")
    assert x.status_code == 200 and x.data[:2] == b"PK" and "spreadsheetml" in x.mimetype
    p = a.get(f"/admin/reports/occupancy?{q}&format=pdf")
    assert p.status_code == 200 and p.data.startswith(b"%PDF")
    assert web.sql("SELECT COUNT(*) n FROM audit_log WHERE action='EXPORT_REPORT'")[0]["n"] == 2


def test_report_range_validation_and_empty_state(web):
    a = web.admin()
    assert "(MSG-" in a.text(a.get("/admin/reports/revenue?start=2026-11-05&end=2026-10-01"))
    page = a.text(a.get("/admin/reports/revenue?start=2026-10-01&end=2026-11-05"))
    assert "No revenue in this period" in page


def test_admin_creates_and_locks_a_receptionist(web):
    a = web.admin()
    r = a.post("/admin/staff", {"name": "Vo Thanh Dat", "email": "dat@grandhorizon.example", "phone": "0907 123 456",
                                "role": "RECEPTIONIST"})
    assert r.status_code == 302
    row = web.sql("SELECT account_id, role FROM account WHERE email='dat@grandhorizon.example'")[0]
    assert row["role"] == "RECEPTIONIST"
    a.post(f"/admin/staff/{row['account_id']}/lock", {"locked": "1"})
    assert web.sql("SELECT status FROM account WHERE account_id=?", (row["account_id"],))[0]["status"] == "LOCKED"
    log = a.text(a.get("/admin/audit"))
    assert "CREATE_STAFF" in log or "STAFF" in log


def test_admin_cannot_lock_the_only_admin_account(web):
    a = web.admin()
    me = web.sql("SELECT account_id FROM account WHERE email=?", (ADMIN[0],))[0]["account_id"]
    a.post(f"/admin/staff/{me}/lock", {"locked": "1"})
    assert web.sql("SELECT status FROM account WHERE account_id=?", (me,))[0]["status"] == "ACTIVE"


def test_price_change_applies_to_new_bookings_only(web):
    c = web.customer()
    code = book(c, type_id=1, ci="2026-11-10", co="2026-11-12")
    before = web.sql("SELECT room_charge FROM booking WHERE booking_code=?", (code,))[0]["room_charge"]
    a = web.admin()
    period = web.sql("SELECT * FROM price_period WHERE room_type_id=1 AND start_date<='2026-11-10' "
                     "AND end_date>='2026-11-12'")[0]
    a.post("/admin/room-types/1/prices", {"period_id": period["price_period_id"], "start": period["start_date"],
                                          "end": period["end_date"], "price": int(period["price_per_night"]) + 100000})
    after_old = web.sql("SELECT room_charge FROM booking WHERE booking_code=?", (code,))[0]["room_charge"]
    assert after_old == before                              # the existing booking keeps its price
    code2 = book(c, type_id=1, ci="2026-11-10", co="2026-11-12")
    new = web.sql("SELECT room_charge FROM booking WHERE booking_code=?", (code2,))[0]["room_charge"]
    assert new == before + 200000


# ----------------------------------------------------------- password reset
def test_password_reset_code_works_once(web):
    g = web.guest()
    g.post("/forgot", {"email": CUSTOMER[0]})
    mail = [m for m in web.mailer.sent if m["to"] == CUSTOMER[0]][-1]
    code = re.search(r"\b(\d{6})\b", mail["body"]).group(1)
    r = g.post("/reset", {"email": CUSTOMER[0], "code": code, "password": "brand-new-pass"})
    assert r.status_code == 302
    assert web.guest().post("/login", {"email": CUSTOMER[0], "password": "brand-new-pass"}).status_code == 302
    again = g.post("/reset", {"email": CUSTOMER[0], "code": code, "password": "another-pass-1"})
    assert again.status_code == 200                          # the code was single use


def test_forgot_password_does_not_reveal_unknown_emails(web):
    g = web.guest()
    r = g.post("/forgot", {"email": "ghost@example.com"})
    assert r.status_code == 302                              # same answer as for a real account
    assert not web.mailer.sent


# ------------------------------------------------- every screen renders for its role
@pytest.fixture(scope="module")
def demo_web(tmp_path_factory):
    app, clock, db_file = build(tmp_path_factory.mktemp("demo"), demo_seed=True)
    return app, db_file


def _rows(db_file, sql, params=()):
    con = sqlite3.connect(db_file)
    try:
        return [r[0] for r in con.execute(sql, params).fetchall()]
    finally:
        con.close()


def test_every_screen_renders_with_demo_data(demo_web):
    app, db = demo_web
    problems = []

    def hit(b, url, ok=(200,)):
        r = b.get(url)
        if r.status_code not in ok:
            problems.append((url, r.status_code))
        return r

    guest = Browser(app)
    for u in ("/", "/?ci=2026-11-10&co=2026-11-12&type=2&price=hi&sort=desc", "/?ci=2026-11-12&co=2026-11-10",
              "/?adults=6&children=4", "/rooms/1", "/rooms/2?ci=2026-11-10&co=2026-11-12", "/login", "/register",
              "/forgot", "/reset", "/outbox"):
        hit(guest, u)
    hit(guest, "/rooms/999", (404,))

    cust = Browser(app).login(CUSTOMER)
    for u in ("/bookings", "/bookings?tab=active", "/bookings?tab=completed", "/bookings?tab=cancelled", "/profile",
              "/book/1?ci=2026-11-20&co=2026-11-22"):
        hit(cust, u)
    mine = _rows(db, "SELECT booking_code FROM booking b JOIN account a USING(account_id) WHERE a.email=?", (CUSTOMER[0],))
    assert len(mine) > 10
    for code in mine[:3] + mine[-6:]:
        for suffix in ("", "/confirmation", "/change", "/change?ci=2026-12-10&co=2026-12-12"):
            hit(cust, f"/bookings/{code}{suffix}")
    for code in _rows(db, "SELECT booking_code FROM booking b JOIN account a USING(account_id) WHERE a.email=? "
                          "AND b.status IN ('PENDING','CONFIRMED')", (CUSTOMER[0],)):
        hit(cust, f"/bookings/{code}/cancel", (200, 302))
    for inv in _rows(db, "SELECT invoice_no FROM invoice i JOIN booking b USING(booking_id) "
                         "JOIN account a USING(account_id) WHERE a.email=? LIMIT 2", (CUSTOMER[0],)):
        hit(cust, f"/invoices/{inv}")
        hit(cust, f"/invoices/{inv}.pdf")

    desk = Browser(app).login(RECEPTIONIST)
    for u in ("/desk/", "/desk/pending", "/desk/bookings", "/desk/bookings?q=an", "/desk/walkin", "/desk/checkin",
              "/desk/inhouse", "/desk/rooms"):
        hit(desk, u)
    for code in _rows(db, "SELECT booking_code FROM booking WHERE status='CONFIRMED' AND check_in_date<='2026-11-05'"):
        hit(desk, f"/desk/checkin/{code}")
    for code in _rows(db, "SELECT booking_code FROM booking WHERE status='CHECKED_IN'"):
        for suffix in (f"/inhouse/{code}/room", f"/services/{code}", f"/checkout/{code}", f"/checkout/{code}?recalc=1&late=1"):
            hit(desk, "/desk" + suffix)

    adm = Browser(app).login(ADMIN)
    for u in ("/admin/", "/admin/?month=2026-10", "/admin/?metric=occ", "/admin/reports/revenue",
              "/admin/reports/revenue?start=2026-09-01&end=2026-11-05&group=month",
              "/admin/reports/revenue?start=2030-01-01&end=2030-01-05", "/admin/reports/occupancy",
              "/admin/reports/occupancy?start=2026-09-01&end=2026-11-05&group=month",
              "/admin/rooms", "/admin/rooms/new", "/admin/rooms/1", "/admin/room-types", "/admin/room-types/new",
              "/admin/room-types/1", "/admin/room-types/1/prices", "/admin/room-types/1/prices?edit=1",
              "/admin/services", "/admin/services?edit=1", "/admin/staff", "/admin/audit"):
        hit(adm, u)
    assert not problems, problems

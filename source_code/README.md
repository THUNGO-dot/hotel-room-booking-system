# Grand Horizon Hotel: Room Booking System

Python implementation of the *Requirement Specification & Design Document* of Group 07
(Software Engineering, UTH). It covers all 30 functional requirements (FR-01 to FR-30), the
business rules (BR), the common rules (CR) and the non-functional requirements (NFR) that the
code can enforce. The web screens reuse the look of the HTML prototype (SCR-01 to SCR-26).

| | |
|---|---|
| Language / framework | Python 3.10 or newer (developed on 3.13), Flask 3 |
| Database | SQLite, schema from report section 6.2 (`hotel/schema.sql`) |
| Other libraries | openpyxl (Excel export), reportlab (PDF export), pytest |
| Tests | 231 automated tests, all passing |

## 1. Run it

```bash
python -m venv .venv                 # optional
.venv\Scripts\activate               # Windows   (Linux/macOS: source .venv/bin/activate)
pip install -r requirements.txt
python run.py                        # then open http://127.0.0.1:5000
```

The first start creates `instance/hotel.db` and fills it with demo data: 3 room types, 12 rooms,
about two months of past stays (so the reports are not empty), guests arriving today, guests in the
house and some pending bookings. `python run.py --reset` throws the data away and rebuilds it.

Run the tests: `python -m pytest` (about 70 seconds).

### Demo accounts

| Role | Email | Password |
|---|---|---|
| Customer | an.nguyen@example.com | 12345678 |
| Receptionist | chau.le@grandhorizon.example | Staff@1234 |
| Admin | bao.tran@grandhorizon.example | Admin@1234 |

A second receptionist (`han.pham@grandhorizon.example`) is created locked, to show the lock rule.
You can also register your own customer account on the Register tab.

### Simulated outside services (report section 2.1)

Demo mode is on in `config.ini`. E-mails are not sent: they are written to the **E-mail outbox**
page (link in the dark bar at the top), which is where the password-reset code appears. Card and
e-wallet payments are simulated; the payment and check-out screens have a tick box
"simulate a declined payment" to show MSG-41. Set `demo_mode = false` to hide both.

### Walk-through for a demonstration

1. As a guest, search a room on the home page, open **Details**, then **Book now** and sign in.
2. Pay the 30% deposit (card). The booking shows as *Awaiting confirmation*.
3. Sign in as the receptionist: **Pending** → **Confirm** (an e-mail appears in the outbox).
4. **Check-in** (lists confirmed guests whose arrival date has come, or create a **walk-in**),
   enter an ID number and choose a room; add a service under **In-house**; then **Check out**.
5. The invoice opens (VAT 10%, print or PDF). The room turns to *Cleaning* on the Rooms board.
6. As the admin: **Reports** (dashboard, revenue and occupancy reports with Excel/PDF export),
   rooms, room types, price periods, services, staff accounts and the audit log.

## 2. Architecture

Four layers. Each layer only calls the one below it. The **domain layer** holds the 16 classes of the report's class diagram (Chapter 5) and the business rules that belong to each of them.

```
 Browser
    |  HTML forms (Jinja templates, CSS, a little JS)
    v
+----------------------------------------------------------+
|  WEB LAYER      hotel/web/*.py, hotel/templates/         |  routes, forms, RBAC, CSRF,
|  public / desk / admin / common blueprints, security.py  |  session timeout, formatting
+----------------------------------------------------------+
                          |  plain function calls with a Context
                          v
+----------------------------------------------------------+
|  SERVICE LAYER  hotel/services/*.py  (no Flask import)   |  every business rule and
|  accounts, availability, bookings, catalog, frontdesk,   |  use case; raises ServiceError
|  payments, notifications, reports, export                |  with a MSG-xx code
+----------------------------------------------------------+
                          |  SQL
                          v
+----------------------------------------------------------+
|  DOMAIN LAYER   hotel/domain/*.py  (16 classes)          |  state and rules of Account, Booking,
|  Account, Booking, Room, Payment, Refund, Invoice ...    |  Room, Refund ... (class diagram)
+----------------------------------------------------------+
                          |  SQL
                          v
+----------------------------------------------------------+
|  DATA LAYER     hotel/db.py + hotel/schema.sql (SQLite)  |  transactions, constraints,
+----------------------------------------------------------+  triggers
```

`Context` (`hotel/context.py`) carries the five things a service may need: the database, the
settings, a clock, the mailer and the payment gateway. The web layer builds one per request;
tests build one with an in-memory database, a fixed clock and fake gateways. That is why the
business rules can be tested without a browser.

```
hotel_booking/
├── run.py                  start the app (--reset, --port)
├── config.ini              business rules and security settings (NFR-05)
├── requirements.txt
├── hotel/
│   ├── app.py              application factory, template filters, error pages
│   ├── settings.py         reads config.ini into one immutable object
│   ├── context.py          Clock, SimulatedMailer, PaymentGateway, Context
│   ├── db.py               SQLite wrapper, nestable BEGIN IMMEDIATE transactions
│   ├── schema.sql          13 tables, CHECKs, UNIQUEs, invoice-immutable triggers
│   ├── messages.py         MSG-xx codes and texts
│   ├── errors.py           ServiceError, PermissionDenied
│   ├── seed.py             base data and demo history
│   ├── domain/             the 16 classes of the class diagram (section 2.1)
│   ├── services/           the use cases: load rows, call the domain classes, save (section 3)
│   ├── web/                routes: public.py, desk.py, admin.py, common.py, security.py
│   ├── templates/          customer/, desk/, admin/, auth/, common/
│   └── static/             css/style.css (from the prototype), js/app.js
└── tests/                  pytest: domain (29) + services (157) + web (45)
```

### 2.1 Class diagram to code (report Chapter 5)

Every class of Fig 5.1 exists in `hotel/domain/` with the attributes (snake_case) and the operations
of the diagram. `tests/test_domain.py` checks that each operation exists and does what the report says.

| Class (Fig 5.1) | File | Operations |
|---|---|---|
| `Account` (abstract) | `domain/account.py` | `login`, `logout`, `change_password`, `request_reset` |
| `Customer`, `Receptionist`, `Admin` (inherit `Account`) | `domain/account.py` | the use cases of each actor, such as `create_booking`, `confirm_booking`, `view_reports` |
| `ResetToken`, `AuditLog` | `domain/account.py` | `is_valid`, `mark_used`, `record` |
| `RoomType`, `PricePeriod` | `domain/catalog.py` | `get_price`, `overlaps` |
| `Room`, `AddOnService`, `ServiceUsage` | `domain/catalog.py` | `change_status`, `is_available`, `deactivate`, `get_amount` |
| `Booking` | `domain/booking.py` | `calculate_deposit`, `confirm`, `reject`, `cancel`, `check_in`, `check_out`, `calculate_bill`, `expire` |
| `Payment`, `Refund`, `Invoice`, `Notification` | `domain/payment.py` | `process`, `calculate_refund`, `generate`, `export_pdf`, `print`, `send`, `retry` |

The service layer keeps the SQL and the transactions. For each use case it loads the rows, builds
the domain objects, calls their methods (which raise `ServiceError` with the MSG code when a rule
is broken) and saves the result. Example: `bookings.cancel_booking` calls `Booking.cancel()` and
`Refund.calculate_refund()`.

### Design decisions worth knowing

* **Rules live in config, not in code (NFR-05).** Deposit rate, hold time, VAT, late fee, refund
  tiers, stay limit, lock-out count, session length and page size are all in `config.ini`.
  Change a value, restart, done. Tests prove the rules follow the configuration.
* **No double booking (NFR-07, FR-10).** Booking, change of dates and check-in run inside
  `BEGIN IMMEDIATE` transactions, so two customers racing for the last room cannot both win.
  Availability = rooms in service of that type minus overlapping Pending, Confirmed and
  Checked-in bookings (stays are half-open ranges, so a guest leaving on the 10th does not block
  one arriving on the 10th).
* **Check-out is atomic (UC-08/09).** Payment, invoice, booking = Completed and room = Cleaning
  are saved together or not at all. Triggers in `schema.sql` make an issued invoice read-only (BR-25).
* **Room number is assigned at check-in**, not at booking, as in the report.
* **Security (NFR-02, NFR-03, CR-01, CR-04).** Passwords are hashed (werkzeug), reset codes are
  stored hashed, single use and expire after 30 minutes. Roles are checked on the server for every
  request (`@roles(...)`; wrong role gives 403 with MSG-56). Every form carries a CSRF token. A
  session ends after 30 idle minutes. Five wrong passwords lock the account. The login error
  never says whether the email or the password was wrong. A customer can open only their own
  bookings and invoices (NFR-04); the ID number is collected at check-in and is not shown on any customer page.
* **Audit log (CR-05).** Every change a staff member makes is written to `audit_log` and shown
  on the admin Audit log page.
* **Formats (CR-02, CR-07).** Dates are dd/mm/yyyy, money is "1,234,000 VND", lists show 10 rows.
* **Time is injectable.** All "now" and "today" values come from `Context.clock`, which is what
  lets the tests move forward 25 hours to prove that an unpaid hold expires, or 31 minutes to
  prove the session timeout.

## 3. Requirement traceability

| FR | Function | Service code | Screen / route |
|---|---|---|---|
| FR-01 | Register customer | `accounts.register` | SCR-02 `/register` |
| FR-02 | Log in and out | `accounts.login`, `web/security.py` | SCR-01 `/login`, `/logout` |
| FR-03 | Reset forgotten password | `accounts.request_reset`, `reset_password` | SCR-03 `/forgot`, `/reset` |
| FR-04 | View and update profile | `accounts.update_profile` | SCR-04 `/profile` |
| FR-05 | Manage staff accounts | `accounts.create_staff`, `set_locked`, `reset_staff_password` | SCR-20 `/admin/staff` |
| FR-06 | Search available rooms | `availability.search`, `validate_stay` | SCR-05/06 `/` |
| FR-07 | Filter and sort results | `availability.search` (price, sort) | SCR-06 `/` |
| FR-08 | View room details | `catalog.get_room_type`, `availability.search` | SCR-07 `/rooms/<id>` |
| FR-09 | Create booking | `bookings.create_booking` | SCR-08 `/book/<id>` |
| FR-10 | Overlap check, 24 h hold | `availability.rooms_left`, `bookings.expire_holds` | SCR-08 |
| FR-11 | Modify booking | `bookings.change_dates` | SCR-12 `/bookings/<code>/change` |
| FR-12 | Cancel booking | `bookings.cancel_booking`, `refund_preview` | SCR-12/14 `/bookings/<code>/cancel`, `/desk/bookings` |
| FR-13 | Booking history and status | `bookings.list_for_customer` | SCR-11 `/bookings` |
| FR-14 | Confirm or reject booking | `bookings.confirm_booking`, `reject_booking` | SCR-14 `/desk/pending` |
| FR-15 | Walk-in booking | `bookings.create_walk_in` | SCR-15 `/desk/walkin` |
| FR-16 | Check in guest | `frontdesk.check_in` | SCR-16 `/desk/checkin` |
| FR-17 | Assign or change room | `frontdesk.check_in`, `change_room` | SCR-16 `/desk/inhouse/<code>/room` |
| FR-18 | Record extra services | `frontdesk.add_service` | SCR-17 `/desk/services/<code>` |
| FR-19 | Check out, compute bill | `frontdesk.compute_bill`, `check_out` | SCR-18 `/desk/checkout/<code>` |
| FR-20 | Pay deposit or full amount | `payments.pay_deposit`, `record`, `PaymentGateway` | SCR-09 `/bookings/<code>/pay`, desk |
| FR-21 | Create and print invoice | `frontdesk.get_invoice`, `export.invoice_pdf` | SCR-18 `/invoices/<no>`, `.pdf` |
| FR-22 | Refund on cancellation | `payments.create_refund`, `complete_refund` | SCR-14 `/desk/pending` |
| FR-23 | Manage room types | `catalog.save_room_type`, `delete_room_type` | SCR-21 `/admin/room-types` |
| FR-24 | Manage rooms and status | `catalog.save_room`, `set_room_status`, `delete_room` | SCR-22 `/admin/rooms`, `/desk/rooms` |
| FR-25 | Manage price list | `catalog.save_period`, `delete_period`, `quote` | SCR-23 `/admin/room-types/<id>/prices` |
| FR-26 | Manage add-on services | `catalog.save_service`, `remove_service` | SCR-24 `/admin/services` |
| FR-27 | Revenue report | `reports.revenue_report` | SCR-25 `/admin/reports/revenue` |
| FR-28 | Occupancy report | `reports.occupancy_report` | SCR-26 `/admin/reports/occupancy` |
| FR-29 | Export to PDF / Excel | `export.to_pdf`, `to_xlsx` | SCR-25/26 (Export buttons) |
| FR-30 | Booking confirmation e-mail | `notifications.send_booking_confirmation` (3 attempts, BR-35) | sent from FR-14 |

Also built: SCR-13 front-desk dashboard (`/desk/`), SCR-19 admin dashboard (`/admin/`),
room board, audit log, demo e-mail outbox.

### Business rules, where they are enforced

| Rule | Where |
|---|---|
| BR-02/03 password length and hashing; BR-05 lock after 5 failures | `accounts.register`, `accounts.login` |
| BR-06..08 stay of at most 30 nights, no past check-in, availability (maintenance rooms excluded) | `availability.validate_stay`, `rooms_left` |
| BR-10 hold 24 h then expire | `bookings.expire_holds` (runs on every request) |
| BR-12 refund 100% / 50% / 0% | `common.refund_rate_for`, `payments.create_refund` |
| BR-13 deposit 30%, rounded to 1,000 VND; BR-24 no smaller deposit | `common.deposit_for`, `payments.pay_deposit` |
| BR-16 booking code `HB-YYYYMMDD-NNNN`, invoice `INV-...` | `bookings.next_code` |
| BR-18..22 check-in needs Confirmed + ID + free room of the booked type; late check-out | `frontdesk.check_in` |
| BR-20 late check-out after 12:00 = +50% of one night | `frontdesk.compute_bill` |
| BR-25 issued invoice cannot change | triggers in `schema.sql` |
| BR-30 a room type cannot lose rooms that future bookings need | `catalog._guard_capacity` |
| BR-31 price periods of a type must not overlap | `catalog.save_period` |
| BR-35 e-mail retried up to 3 times, booking stays confirmed | `notifications.send_booking_confirmation` |

## 4. Tests

```
tests/test_domain.py                the 16 domain classes and the operations of Fig 5.1
tests/test_schema.py                constraints, uniqueness, immutable invoice
tests/test_accounts.py              register, login, lock, reset code, staff
tests/test_booking_rules.py         search, deposit, hold expiry, change, cancel and refund tiers
tests/test_frontdesk.py             check-in, services, late fee, check-out, invoice, refund
tests/test_catalog_and_reports.py   rooms, prices, capacity guard, reports, exports
tests/test_web.py                   HTTP level: access control (403 and MSG-56), CSRF, 30-minute
                                    session timeout, ownership, a complete stay from search to PDF
                                    invoice, declined card, cancellation with refund, pagination,
                                    and a check that every screen renders for its role
```

Beyond pass/fail, the web tests double as a script of the main use cases (UC-01 to UC-17).

## 5. Differences from the report, and limits

Things a reader comparing the code with the report should know.

* **Schema (section 6.2).** Same 13 tables. Differences are additive only: one surrogate primary
  key per table, `booking.special_request` (the booking form has that field), and
  `room_type.bed_type` and `area_m2` (the room cards show them). See the header of `schema.sql`.
* **Booking code date.** `HB-YYYYMMDD-NNNN` uses the date the booking was *created*, not the stay date.
* **Changing dates** re-prices the stay with the current price list. If the new deposit is higher
  than what was paid, the booking goes back to Pending until the difference is paid; if it is
  lower, the surplus is credited at check-out. If the deposit is larger than the final bill
  (stay shortened), the difference becomes a refund.
* **Price changes** apply to new bookings only; existing bookings and invoices keep their price.
* **Occupancy** counts days only up to today, so a month in progress is not diluted by future days.
  The dashboard shows "against previous month" only once the month is finished.
* **Late check-out** is a tick box on the check-out screen. It starts ticked when the clock is
  already past 12:00 on the check-out date and the receptionist can correct it.
* **Payment and e-mail are simulated**, as in the report. There is no real SMTP or card gateway;
  `PaymentGateway` and `EmailGateway` are the two places to plug one in.
* **Not production-ready:** change `secret_key` in `config.ini`, serve over HTTPS, and run behind a
  WSGI server (for example `waitress` on Windows) instead of `python run.py`. SQLite suits one hotel
  and one server. NFR-06 (3-second search) is met with a large margin on the demo data, but the
  50-user load test of the report was not run.
* **Language.** Screens are in English, like the prototype and the report.

## 6. Version control

The assignment asks for source under version control. A `.gitignore` is included:

```bash
git init
git add .
git commit -m "Hotel booking system: services, web layer, tests"
```

The `_draft_archive/` folder, if present, holds earlier throw-away drafts; it is ignored by git
and is not part of the project.

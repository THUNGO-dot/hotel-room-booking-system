-- Grand Horizon Hotel - logical schema (report section 6.2, 13 tables)
-- SQLite dialect. Keys are INTEGER AUTOINCREMENT (AUTO_INCREMENT in MySQL,
-- IDENTITY in SQL Server). Money is whole VND (DECIMAL(12,0) in the report).
--
-- Differences from the figure in 6.2 (all additive, see README):
--   * every table has ONE surrogate primary key; foreign keys are plain FK columns
--   * booking.special_request       (SCR-08 has a "special requests" field)
--   * room_type.bed_type / area_m2   (SCR-06/07 show bed type and room size)

PRAGMA foreign_keys = ON;

CREATE TABLE account (
    account_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    email         VARCHAR(100)  NOT NULL UNIQUE,
    password_hash VARCHAR(255)  NOT NULL,
    full_name     VARCHAR(100)  NOT NULL,
    phone         VARCHAR(20)   NOT NULL,
    role          VARCHAR(12)   NOT NULL CHECK (role IN ('CUSTOMER','RECEPTIONIST','ADMIN')),
    status        VARCHAR(8)    NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','LOCKED')),
    failed_logins TINYINT       NOT NULL DEFAULT 0,
    created_at    DATETIME      NOT NULL
);

CREATE TABLE reset_token (
    token_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER      NOT NULL REFERENCES account(account_id),
    code_hash  VARCHAR(255) NOT NULL,
    expires_at DATETIME     NOT NULL,
    used       TINYINT      NOT NULL DEFAULT 0 CHECK (used IN (0,1))
);

CREATE TABLE audit_log (
    log_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id  INTEGER      NOT NULL REFERENCES account(account_id),
    action      VARCHAR(50)  NOT NULL,
    entity_type VARCHAR(30)  NOT NULL,
    entity_id   INTEGER,
    details     VARCHAR(500),
    created_at  DATETIME     NOT NULL
);

CREATE TABLE room_type (
    room_type_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name         VARCHAR(50)  NOT NULL UNIQUE,
    description  VARCHAR(500) NOT NULL DEFAULT '',
    capacity     TINYINT      NOT NULL CHECK (capacity BETWEEN 1 AND 8),
    amenities    VARCHAR(255) NOT NULL DEFAULT '',
    bed_type     VARCHAR(30)  NOT NULL DEFAULT '',
    area_m2      TINYINT      NOT NULL DEFAULT 0,
    active       TINYINT      NOT NULL DEFAULT 1 CHECK (active IN (0,1))
);

CREATE TABLE price_period (
    price_period_id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_type_id    INTEGER       NOT NULL REFERENCES room_type(room_type_id),
    start_date      DATE          NOT NULL,
    end_date        DATE          NOT NULL,
    price_per_night DECIMAL(12,0) NOT NULL CHECK (price_per_night > 0),
    CHECK (end_date >= start_date)
);

CREATE TABLE room (
    room_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    room_type_id INTEGER     NOT NULL REFERENCES room_type(room_type_id),
    room_number  VARCHAR(10) NOT NULL UNIQUE,
    floor        TINYINT     NOT NULL DEFAULT 1,
    status       VARCHAR(12) NOT NULL DEFAULT 'AVAILABLE'
                 CHECK (status IN ('AVAILABLE','OCCUPIED','CLEANING','MAINTENANCE')),
    active       TINYINT     NOT NULL DEFAULT 1 CHECK (active IN (0,1))
);

CREATE TABLE add_on_service (
    service_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name       VARCHAR(50)   NOT NULL UNIQUE,
    unit_price DECIMAL(12,0) NOT NULL CHECK (unit_price > 0),
    active     TINYINT       NOT NULL DEFAULT 1 CHECK (active IN (0,1))
);

CREATE TABLE booking (
    booking_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_code    VARCHAR(20)   NOT NULL UNIQUE,              -- HB-YYYYMMDD-NNNN (BR-16)
    account_id      INTEGER       REFERENCES account(account_id),   -- NULL = walk-in (BR-15)
    room_type_id    INTEGER       NOT NULL REFERENCES room_type(room_type_id),
    room_id         INTEGER       REFERENCES room(room_id),         -- NULL until check-in
    guest_name      VARCHAR(100)  NOT NULL,
    guest_phone     VARCHAR(20)   NOT NULL,
    id_document     VARCHAR(30),                                -- filled at check-in only (NFR-04)
    check_in_date   DATE          NOT NULL,
    check_out_date  DATE          NOT NULL,
    guests          TINYINT       NOT NULL CHECK (guests >= 1),
    special_request VARCHAR(200)  NOT NULL DEFAULT '',
    status          VARCHAR(12)   NOT NULL DEFAULT 'PENDING'
                    CHECK (status IN ('PENDING','CONFIRMED','CHECKED_IN','COMPLETED',
                                      'REJECTED','CANCELLED','EXPIRED')),
    room_charge     DECIMAL(12,0) NOT NULL CHECK (room_charge >= 0),
    deposit_amount  DECIMAL(12,0) NOT NULL DEFAULT 0 CHECK (deposit_amount >= 0),
    hold_expires_at DATETIME,
    created_at      DATETIME      NOT NULL,
    CHECK (check_out_date > check_in_date)
);
CREATE INDEX ix_booking_overlap ON booking(room_type_id, check_in_date, check_out_date, status);
CREATE INDEX ix_booking_account ON booking(account_id);

CREATE TABLE service_usage (
    usage_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_id INTEGER       NOT NULL REFERENCES booking(booking_id),
    service_id INTEGER       NOT NULL REFERENCES add_on_service(service_id),
    quantity   INTEGER       NOT NULL CHECK (quantity >= 1),
    unit_price DECIMAL(12,0) NOT NULL,          -- price frozen at the time of use
    used_at    DATETIME      NOT NULL
);

CREATE TABLE payment (
    payment_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_id      INTEGER       NOT NULL REFERENCES booking(booking_id),
    amount          DECIMAL(12,0) NOT NULL CHECK (amount > 0),
    method          VARCHAR(10)   NOT NULL CHECK (method IN ('CASH','CARD','E_WALLET')),
    purpose         VARCHAR(10)   NOT NULL CHECK (purpose IN ('DEPOSIT','BALANCE')),
    transaction_ref VARCHAR(40),
    paid_at         DATETIME      NOT NULL
);

CREATE TABLE refund (
    refund_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_id  INTEGER       NOT NULL UNIQUE REFERENCES booking(booking_id),   -- max one (BR-12)
    amount      DECIMAL(12,0) NOT NULL CHECK (amount >= 0),
    refund_rate TINYINT       NOT NULL CHECK (refund_rate IN (0,50,100)),
    reason      VARCHAR(200)  NOT NULL DEFAULT '',
    status      VARCHAR(10)   NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','COMPLETED')),
    refunded_at DATETIME      NOT NULL
);

CREATE TABLE invoice (
    invoice_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_no     VARCHAR(20)   NOT NULL UNIQUE,                               -- INV-YYYYMMDD-NNNN
    booking_id     INTEGER       NOT NULL UNIQUE REFERENCES booking(booking_id),
    issued_at      DATETIME      NOT NULL,
    room_charge    DECIMAL(12,0) NOT NULL,
    service_charge DECIMAL(12,0) NOT NULL DEFAULT 0,
    late_fee       DECIMAL(12,0) NOT NULL DEFAULT 0,
    vat_amount     DECIMAL(12,0) NOT NULL,
    total_amount   DECIMAL(12,0) NOT NULL
);

-- BR-25: an issued invoice is immutable.
CREATE TRIGGER invoice_no_update BEFORE UPDATE ON invoice
BEGIN SELECT RAISE(ABORT, 'An issued invoice cannot be changed'); END;
CREATE TRIGGER invoice_no_delete BEFORE DELETE ON invoice
BEGIN SELECT RAISE(ABORT, 'An issued invoice cannot be deleted'); END;

CREATE TABLE notification (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_id      INTEGER      NOT NULL REFERENCES booking(booking_id),
    type            VARCHAR(30)  NOT NULL,
    recipient_email VARCHAR(100) NOT NULL,
    status          VARCHAR(10)  NOT NULL CHECK (status IN ('SENT','FAILED')),
    attempts        TINYINT      NOT NULL DEFAULT 0,
    last_attempt_at DATETIME
);

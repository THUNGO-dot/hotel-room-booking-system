"""UC-10, UC-11, UC-12 and BR-28..34, plus NFR-02 (server-side roles) and NFR-05 (configuration)."""
import io
import zipfile
from datetime import date

import pytest
from openpyxl import load_workbook

from hotel.errors import PermissionDenied, ServiceError
from hotel.services import bookings, catalog, export, frontdesk, reports
from hotel.settings import Settings
from tests.conftest import DLX, EXE, STD


# ------------------------------------------------------------------- price list
def test_overlapping_price_period_is_refused(ctx, ids, admin):
    tid = ids["types"][STD]
    ctx.db.execute("DELETE FROM price_period WHERE room_type_id=?", (tid,))
    catalog.save_period(ctx, admin, tid, "2026-12-20", "2027-01-05", 1_000_000)
    for s, e in [("2026-12-31", "2027-01-10"), ("2026-12-01", "2026-12-20"), ("2026-12-22", "2026-12-23"),
                 ("2026-12-01", "2027-02-01")]:
        with pytest.raises(ServiceError) as exc:
            catalog.save_period(ctx, admin, tid, s, e, 1_100_000)
        assert exc.value.code == "MSG-50"                    # BR-31
    catalog.save_period(ctx, admin, tid, "2027-01-06", "2027-01-31", 1_100_000)   # touching but not overlapping


def test_editing_a_period_does_not_clash_with_itself(ctx, ids, admin):
    tid = ids["types"][STD]
    pid = ctx.db.scalar("SELECT price_period_id FROM price_period WHERE room_type_id=?", (tid,))
    catalog.save_period(ctx, admin, tid, "2025-01-01", "2028-12-31", 800_000, period_id=pid)
    assert ctx.db.scalar("SELECT price_per_night FROM price_period WHERE price_period_id=?", (pid,)) == 800_000


@pytest.mark.parametrize("price,key", [(0, "BAD_PRICE"), (-5, "BAD_PRICE"), ("abc", "BAD_PRICE")])
def test_invalid_price(ctx, ids, admin, price, key):
    with pytest.raises(ServiceError) as e:
        catalog.save_period(ctx, admin, ids["types"][STD], "2030-01-01", "2030-02-01", price)
    assert e.value.key == key and e.value.code == "MSG-59"


def test_period_end_before_start(ctx, ids, admin):
    with pytest.raises(ServiceError) as e:
        catalog.save_period(ctx, admin, ids["types"][STD], "2030-02-01", "2030-01-01", 100_000)
    assert e.value.key == "RANGE_INVALID"


def test_new_price_applies_only_to_new_bookings(ctx, ids, admin, make):
    old = make(DLX, confirm=False, pay=False)
    tid = ids["types"][DLX]
    pid = ctx.db.scalar("SELECT price_period_id FROM price_period WHERE room_type_id=?", (tid,))
    catalog.save_period(ctx, admin, tid, "2025-01-01", "2028-12-31", 2_000_000, period_id=pid)
    assert bookings.get_booking(ctx, old["booking_code"])["room_charge"] == 2_400_000     # unchanged
    assert catalog.quote(ctx, tid, "2026-12-01", "2026-12-03") == 4_000_000


def test_a_night_without_a_price_cannot_be_sold(ctx, ids):
    with pytest.raises(ServiceError) as e:
        catalog.quote(ctx, ids["types"][STD], "2040-01-01", "2040-01-03")
    assert e.value.key == "NO_PRICE"


# ------------------------------------------------------------------------ rooms
def test_room_numbers_are_unique(ctx, ids, admin):
    with pytest.raises(ServiceError):
        catalog.save_room(ctx, admin, None, "101", ids["types"][STD], 1, "AVAILABLE")           # BR-28
    rid = catalog.save_room(ctx, admin, None, "106", ids["types"][STD], 1, "AVAILABLE")
    with pytest.raises(ServiceError):
        catalog.save_room(ctx, admin, rid, "101", ids["types"][STD], 1, "AVAILABLE")


def test_occupied_room_cannot_go_to_maintenance(ctx, ids, admin, checked_in):
    rid = ids["rooms"]["201"]
    with pytest.raises(ServiceError) as e:
        catalog.save_room(ctx, admin, rid, "201", ids["types"][DLX], 2, "MAINTENANCE")
    assert e.value.code == "MSG-51"
    with pytest.raises(ServiceError):
        catalog.set_room_status(ctx, admin, rid, "MAINTENANCE")


def test_housekeeping_cycle_after_check_out(ctx, ids, reception, checked_in):
    frontdesk.check_out(ctx, reception, checked_in["booking_code"], "CASH", False)
    rid = ids["rooms"]["201"]
    assert ctx.db.scalar("SELECT status FROM room WHERE room_id=?", (rid,)) == "CLEANING"
    catalog.set_room_status(ctx, reception, rid, "AVAILABLE")
    assert ctx.db.scalar("SELECT status FROM room WHERE room_id=?", (rid,)) == "AVAILABLE"


def test_cannot_take_rooms_out_of_service_that_promised_bookings_need(ctx, ids, admin, customer):
    for _ in range(2):                                          # both Executive rooms are booked
        bookings.create_booking(ctx, customer, ids["types"][EXE], "2026-11-10", "2026-11-12", 2)
    with pytest.raises(ServiceError) as e:
        catalog.set_room_status(ctx, admin, ids["rooms"]["401"], "MAINTENANCE")
    assert e.value.code == "MSG-49"                             # BR-30
    with pytest.raises(ServiceError):
        catalog.delete_room(ctx, admin, ids["rooms"]["401"])


def test_room_with_history_is_deactivated_not_deleted(ctx, ids, admin, reception, checked_in):
    frontdesk.check_out(ctx, reception, checked_in["booking_code"], "CASH", False)
    catalog.set_room_status(ctx, reception, ids["rooms"]["201"], "AVAILABLE")
    catalog.delete_room(ctx, admin, ids["rooms"]["201"])
    row = ctx.db.one("SELECT * FROM room WHERE room_number='201'")
    assert row["active"] == 0
    assert "201" not in [r["room_number"] for r in catalog.list_rooms(ctx)]


def test_room_type_in_use_cannot_be_deleted(ctx, ids, admin):
    with pytest.raises(ServiceError) as e:
        catalog.delete_room_type(ctx, admin, ids["types"][STD])
    assert e.value.code == "MSG-49"


def test_empty_room_type_can_be_deleted(ctx, ids, admin):
    tid = catalog.save_room_type(ctx, admin, None, "Garden Bungalow", "1 queen bed", 3, 40, "Garden view", "")
    catalog.save_period(ctx, admin, tid, "2026-01-01", "2026-12-31", 1_000_000)
    assert catalog.delete_room_type(ctx, admin, tid) == "deleted"
    assert catalog.get_room_type(ctx, tid) is None


def test_room_type_validation(ctx, admin):
    with pytest.raises(ServiceError):
        catalog.save_room_type(ctx, admin, None, "Standard King Room", "bed", 2, 30, "", "")   # duplicate name
    with pytest.raises(ServiceError):
        catalog.save_room_type(ctx, admin, None, "X", "bed", 9, 30, "", "")                      # capacity 1..8


def test_add_on_service_rules(ctx, admin):
    with pytest.raises(ServiceError):
        catalog.save_service(ctx, admin, None, "Minibar", 10_000)                 # duplicate name
    with pytest.raises(ServiceError) as e:
        catalog.save_service(ctx, admin, None, "Free stuff", 0)
    assert e.value.code == "MSG-59"
    sid = catalog.save_service(ctx, admin, None, "Bike hire", 60_000)
    catalog.remove_service(ctx, admin, sid)
    assert "Bike hire" not in [s["name"] for s in catalog.list_services(ctx)]


def test_catalog_changes_are_audited(ctx, ids, admin):
    catalog.save_service(ctx, admin, None, "Bike hire", 60_000)
    catalog.save_period(ctx, admin, ids["types"][STD], "2030-01-01", "2030-02-01", 900_000)
    actions = {r["action"] for r in ctx.db.all("SELECT action FROM audit_log")}
    assert {"CREATE_SERVICE", "CREATE_PRICE"} <= actions


# ----------------------------------------------------------- roles (NFR-02, CR-04)
@pytest.mark.parametrize("call", [
    lambda c, i, a: catalog.save_room_type(c, a, None, "T", "b", 2, 20, "", ""),
    lambda c, i, a: catalog.save_period(c, a, i["types"][0], "2030-01-01", "2030-02-01", 1),
    lambda c, i, a: catalog.save_room(c, a, None, "999", i["types"][0], 1, "AVAILABLE"),
    lambda c, i, a: catalog.save_service(c, a, None, "S", 1000),
    lambda c, i, a: reports.dashboard(c, a, 2026, 10),
    lambda c, i, a: reports.revenue_report(c, a, "2026-10-01", "2026-10-31"),
    lambda c, i, a: reports.occupancy_report(c, a, "2026-10-01", "2026-10-31"),
])
def test_admin_functions_reject_everyone_else(ctx, ids, customer, reception, call):
    for actor in (customer, reception, None):
        with pytest.raises(PermissionDenied) as e:
            call(ctx, ids, actor)
        assert e.value.code == "MSG-56"


# ------------------------------------------------------------------------ reports
def _history(ctx, ids, reception, customer):
    """Two finished stays on 5 and 6 Nov, one cancellation with a partial refund."""
    for room, nts in (("101", 1), ("102", 2)):
        b = bookings.create_walk_in(ctx, reception, "Guest", "0933 444 555", ids["types"][STD], nts, 1)
        frontdesk.check_in(ctx, reception, b["booking_code"], "B1234567", ids["rooms"][room])
        frontdesk.check_out(ctx, reception, b["booking_code"], "CASH", False)


def test_revenue_is_payments_minus_refunds(ctx, ids, admin, reception, customer, make):
    _history(ctx, ids, reception, customer)       # 825,000 + 1,650,000 = 2,475,000 taken in
    b = make(DLX, "2026-11-20", "2026-11-22", confirm=False)                # 720,000 deposit
    bookings.cancel_booking(ctx, customer, b["booking_code"])               # 100% refund (>48 h)
    rep = reports.revenue_report(ctx, admin, "2026-11-01", "2026-11-30")
    assert rep["total"] == {"payments": 3_195_000, "refunds": 720_000, "net": 2_475_000}      # BR-33
    assert rep["by_type"][0] == {"name": "Standard King Room", "net": 2_475_000, "share": 100.0} or \
        rep["by_type"][0]["net"] == 2_475_000


def test_occupancy_formula(ctx, ids, admin, reception, customer):
    _history(ctx, ids, reception, customer)                               # 1 + 2 = 3 room-nights
    ctx.clock._fixed = ctx.clock.now().replace(day=8)                     # 8 Nov: 8 days elapsed since 1 Nov
    rep = reports.occupancy_report(ctx, admin, "2026-11-01", "2026-11-08")
    # rooms in service = 11 (305 under maintenance); BR-34: 3 / (11 x 8) x 100
    assert rep["total"]["nights"] == 3 and rep["total"]["rooms"] == 11 and rep["total"]["days"] == 8
    assert rep["total"]["rate"] == round(3 / (11 * 8) * 100, 1)


def test_future_days_do_not_dilute_occupancy(ctx, ids, admin, reception, customer):
    _history(ctx, ids, reception, customer)
    short = reports.occupancy_between(ctx, date(2026, 11, 1), date(2026, 11, 5))
    long = reports.occupancy_between(ctx, date(2026, 11, 1), date(2026, 11, 30))
    assert short["days"] == long["days"] == 5


def test_report_groups_by_day_and_month(ctx, ids, admin, reception, customer):
    _history(ctx, ids, reception, customer)
    daily = reports.revenue_report(ctx, admin, "2026-11-05", "2026-11-07", "day")
    assert [r["label"] for r in daily["rows"]] == ["05/11/2026", "06/11/2026", "07/11/2026"]
    monthly = reports.revenue_report(ctx, admin, "2026-10-15", "2026-12-15", "month")
    assert [r["label"] for r in monthly["rows"]] == ["10/2026", "11/2026", "12/2026"]
    assert sum(r["net"] for r in monthly["rows"]) == monthly["total"]["net"]


def test_report_range_errors(ctx, admin):
    with pytest.raises(ServiceError) as e:
        reports.revenue_report(ctx, admin, "2026-11-10", "2026-11-01")
    assert e.value.code == "MSG-52"
    assert reports.revenue_report(ctx, admin, "2026-01-01", "2026-01-31")["empty"] is True        # MSG-53


def test_dashboard_compares_a_finished_month_with_the_one_before(ctx, ids, admin):
    for month, amount in ((9, 1_000_000), (10, 1_500_000)):
        bid = ctx.db.insert(
            "INSERT INTO booking(booking_code,room_type_id,guest_name,guest_phone,check_in_date,check_out_date,guests,"
            "status,room_charge,created_at) VALUES (?,?,?,?,?,?,1,'COMPLETED',?,?)",
            (f"HB-X{month}", ids["types"][STD], "G", "1", f"2026-{month:02d}-10", f"2026-{month:02d}-11", amount, "now"))
        ctx.db.insert("INSERT INTO payment(booking_id,amount,method,purpose,paid_at) VALUES (?,?,'CASH','BALANCE',?)",
                      (bid, amount, f"2026-{month:02d}-11 10:00:00"))
    dash = reports.dashboard(ctx, admin, 2026, 10)
    assert dash["revenue"]["net"] == 1_500_000 and dash["change"] == 50.0
    assert [w["label"] for w in dash["weeks"]] == ["Week 1", "Week 2", "Week 3", "Week 4"]
    assert dash["weeks"][1]["revenue"] == 1_500_000
    assert reports.dashboard(ctx, admin, 2026, 11)["change"] is None          # month still running


def test_excel_export_opens_and_holds_the_numbers(ctx, ids, admin, reception, customer):
    _history(ctx, ids, reception, customer)
    rep = reports.revenue_report(ctx, admin, "2026-11-05", "2026-11-06")
    data = export.to_xlsx(rep)
    assert zipfile.is_zipfile(io.BytesIO(data))
    ws = load_workbook(io.BytesIO(data)).active
    values = [c.value for row in ws.iter_rows() for c in row]
    assert "Total" in values and 2_475_000 in values


def test_pdf_exports_are_valid_pdfs(ctx, ids, admin, reception, customer):
    _history(ctx, ids, reception, customer)
    assert export.to_pdf(reports.revenue_report(ctx, admin, "2026-11-05", "2026-11-06")).startswith(b"%PDF")
    assert export.to_pdf(reports.occupancy_report(ctx, admin, "2026-11-05", "2026-11-06")).startswith(b"%PDF")
    inv = ctx.db.one("SELECT invoice_no FROM invoice")["invoice_no"]
    invoice = frontdesk.get_invoice(ctx, inv)
    assert export.invoice_pdf(invoice, frontdesk.usages(ctx, invoice["bid"])).startswith(b"%PDF")


def test_pdf_handles_vietnamese_names(ctx, ids, reception):
    b = bookings.create_walk_in(ctx, reception, "Nguyễn Thị Ánh Tuyết", "0933 444 555", ids["types"][STD], 1, 1)
    frontdesk.check_in(ctx, reception, b["booking_code"], "B1234567", ids["rooms"]["101"])
    inv = frontdesk.check_out(ctx, reception, b["booking_code"], "CASH", False)
    assert export.invoice_pdf(inv, []).startswith(b"%PDF")


# ------------------------------------------------------------ configuration (NFR-05)
def test_config_file_is_the_source_of_business_parameters(tmp_path):
    cfg = tmp_path / "c.ini"
    cfg.write_text("[business]\ndeposit_rate = 0.5\nhold_hours = 12\nvat_rate = 0.08\n[system]\npage_size = 5\n")
    s = Settings.load(cfg)
    assert (s.deposit_rate, s.hold_hours, s.vat_rate, s.page_size) == (0.5, 12, 0.08, 5)
    assert s.max_nights == 30                         # untouched values keep their defaults


def test_shipped_config_matches_the_report():
    s = Settings.load()
    assert (s.deposit_rate, s.deposit_rounding, s.hold_hours, s.vat_rate) == (0.30, 1000, 24, 0.10)
    assert (s.max_failed_logins, s.session_minutes, s.reset_code_minutes, s.page_size) == (5, 30, 30, 10)
    assert (s.email_max_attempts, s.max_nights, s.refund_full_hours, s.refund_half_hours) == (3, 30, 48, 24)


def test_hold_time_comes_from_configuration(ctx, ids, customer):
    ctx.cfg = ctx.cfg.with_overrides(hold_hours=2)
    b = bookings.create_booking(ctx, customer, ids["types"][DLX], "2026-11-10", "2026-11-12", 2)
    assert b["hold_expires_at"] == "2026-11-05 12:00:00"

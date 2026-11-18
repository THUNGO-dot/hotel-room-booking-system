"""UC-01, UC-02, UC-13, UC-14, UC-17 and BR-01..05."""
from datetime import timedelta

import pytest

from hotel.errors import PermissionDenied, ServiceError
from hotel.services import accounts


def test_register_hashes_the_password(ctx):
    aid = accounts.register(ctx, "Tran Thi B", "B@Example.com", "0912 345 678", "secret-pass")
    row = accounts.get(ctx, aid)
    assert row["email"] == "b@example.com"            # BR-01: case-insensitive unique email
    assert row["role"] == "CUSTOMER"
    assert "secret-pass" not in row["password_hash"]    # NFR-03 / BR-02


@pytest.mark.parametrize("kw,key", [
    (dict(email="not-an-email"), "BAD_EMAIL"),
    (dict(password="short"), "INVALID_INPUT"),           # BR-02: at least 8 characters
    (dict(phone="123"), "INVALID_INPUT"),
    (dict(full_name="  "), "INVALID_INPUT"),
    (dict(email="an.nguyen@example.com"), "EMAIL_TAKEN"),
])
def test_register_rejects_bad_input(ctx, ids, kw, key):
    data = dict(full_name="X Y", email="new@example.com", phone="0912345678", password="longenough")
    data.update(kw)
    with pytest.raises(ServiceError) as e:
        accounts.register(ctx, **data)
    assert e.value.key == key


def test_login_ok_and_counter_reset(ctx, ids):
    with pytest.raises(ServiceError):
        accounts.login(ctx, "an.nguyen@example.com", "wrong")
    assert accounts.get(ctx, ids["customer"])["failed_logins"] == 1
    acct = accounts.login(ctx, "AN.NGUYEN@example.com", "12345678")
    assert acct["failed_logins"] == 0


def test_login_message_does_not_reveal_which_part_was_wrong(ctx, ids):
    with pytest.raises(ServiceError) as a:
        accounts.login(ctx, "nobody@example.com", "whatever")
    with pytest.raises(ServiceError) as b:
        accounts.login(ctx, "an.nguyen@example.com", "whatever")
    assert a.value.message == b.value.message and a.value.code == "MSG-06"


def test_fifth_failed_login_locks_the_account(ctx, ids):
    for i in range(4):
        with pytest.raises(ServiceError) as e:
            accounts.login(ctx, "an.nguyen@example.com", "bad")
        assert e.value.key == "BAD_CREDENTIALS"
    with pytest.raises(ServiceError) as e:
        accounts.login(ctx, "an.nguyen@example.com", "bad")
    assert e.value.key == "ACCOUNT_LOCKED" and e.value.code == "MSG-07"
    # even the right password is refused once locked
    with pytest.raises(ServiceError) as e:
        accounts.login(ctx, "an.nguyen@example.com", "12345678")
    assert e.value.key == "ACCOUNT_LOCKED"


def test_admin_can_unlock(ctx, ids, admin):
    for _ in range(5):
        with pytest.raises(ServiceError):
            accounts.login(ctx, "chau.le@grandhorizon.example", "bad")
    accounts.set_locked(ctx, admin, ids["reception"], False)
    assert accounts.login(ctx, "chau.le@grandhorizon.example", "Staff@1234")["failed_logins"] == 0


def test_profile_password_change_needs_current_password(ctx, ids, customer):
    with pytest.raises(ServiceError) as e:
        accounts.update_profile(ctx, ids["customer"], "New Name", "0909 999 999", "wrong", "newpassword1")
    assert e.value.code == "MSG-12"
    accounts.update_profile(ctx, ids["customer"], "New Name", "0909 999 999", "12345678", "newpassword1")
    assert accounts.login(ctx, "an.nguyen@example.com", "newpassword1")["full_name"] == "New Name"


def _code(ctx):
    mail = ctx.mailer.sent[-1]["body"]
    return mail.split("reset code is ")[1][:6]


def test_reset_code_is_single_use(ctx, ids):
    accounts.request_reset(ctx, "an.nguyen@example.com")
    code = _code(ctx)
    accounts.reset_password(ctx, "an.nguyen@example.com", code, "brand-new-pass")
    assert accounts.login(ctx, "an.nguyen@example.com", "brand-new-pass")
    with pytest.raises(ServiceError) as e:                  # BR-04: used once
        accounts.reset_password(ctx, "an.nguyen@example.com", code, "another-pass1")
    assert e.value.code == "MSG-09"


def test_reset_code_expires_after_30_minutes(ctx, ids):
    accounts.request_reset(ctx, "an.nguyen@example.com")
    code = _code(ctx)
    ctx.clock.advance(minutes=31)
    with pytest.raises(ServiceError) as e:
        accounts.reset_password(ctx, "an.nguyen@example.com", code, "brand-new-pass")
    assert e.value.code == "MSG-09"


def test_reset_code_is_stored_hashed_and_unknown_email_is_silent(ctx, ids):
    accounts.request_reset(ctx, "an.nguyen@example.com")
    code = _code(ctx)
    stored = ctx.db.scalar("SELECT code_hash FROM reset_token")
    assert code not in stored
    n = len(ctx.mailer.sent)
    accounts.request_reset(ctx, "ghost@example.com")        # no error, no mail, nothing to learn
    assert len(ctx.mailer.sent) == n


def test_new_reset_code_replaces_the_old_one(ctx, ids):
    accounts.request_reset(ctx, "an.nguyen@example.com")
    first = _code(ctx)
    accounts.request_reset(ctx, "an.nguyen@example.com")
    with pytest.raises(ServiceError):
        accounts.reset_password(ctx, "an.nguyen@example.com", first, "brand-new-pass")


def test_last_active_admin_cannot_be_locked(ctx, ids, admin):
    with pytest.raises(ServiceError) as e:
        accounts.set_locked(ctx, admin, ids["admin"], True)
    assert e.value.code == "MSG-60"                        # UC-17 exception 2a


def test_admin_cannot_lock_own_account_when_another_admin_exists(ctx, ids, admin):
    accounts.create_staff(ctx, admin, "Second Admin", "second@grandhorizon.example", "0900", "ADMIN")
    with pytest.raises(ServiceError) as e:
        accounts.set_locked(ctx, admin, ids["admin"], True)
    assert e.value.key == "SELF_LOCK"


def test_locked_staff_account_loses_all_rights(ctx, ids, admin):
    accounts.set_locked(ctx, admin, ids["reception"], True)
    with pytest.raises(PermissionDenied):
        accounts.set_locked(ctx, accounts.get(ctx, ids["reception"]), ids["admin"], True)


def test_only_admin_manages_staff(ctx, ids, reception, customer):
    for actor in (reception, customer, None):
        with pytest.raises(PermissionDenied):
            accounts.create_staff(ctx, actor, "X", "x@example.com", "0900", "ADMIN")


def test_staff_creation_is_audited(ctx, admin):
    accounts.create_staff(ctx, admin, "New Desk", "desk@grandhorizon.example", "0900 000", "RECEPTIONIST")
    row = ctx.db.one("SELECT * FROM audit_log WHERE action='CREATE_STAFF'")
    assert row["account_id"] == admin["account_id"]

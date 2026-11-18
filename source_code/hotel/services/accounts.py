"""Accounts: register, login, lock-out, profile, password reset, staff (UC-01/02/13/14/17)."""
from __future__ import annotations

import secrets

from ..context import DT, Context
from ..domain import Account, ResetToken
from ..errors import ServiceError
from .common import EMAIL_RE, audit, digits, require_role, require_text


def _hash(password: str) -> str:
    # BR-02 / NFR-03: only a salted hash is ever stored
    return Account.hash_password(password)


def _check_password_rules(ctx: Context, password: str) -> None:
    if len(password or "") < ctx.cfg.min_password_length:
        raise ServiceError("INVALID_INPUT",
                           detail=f"Use a password with at least {ctx.cfg.min_password_length} characters.")


def normalise_email(email: str | None) -> str:
    return (email or "").strip().lower()


def get(ctx: Context, account_id: int):
    return ctx.db.one("SELECT * FROM account WHERE account_id=?", (account_id,))


def get_by_email(ctx: Context, email: str):
    return ctx.db.one("SELECT * FROM account WHERE email=?", (normalise_email(email),))


# ----------------------------------------------------------------- register
def register(ctx: Context, full_name: str, email: str, phone: str, password: str) -> int:
    name = require_text(full_name, "your full name")
    email = normalise_email(email)
    if not EMAIL_RE.match(email):
        raise ServiceError("BAD_EMAIL")
    if digits(phone) < 9:
        raise ServiceError("INVALID_INPUT", detail="Enter a valid phone number (at least 9 digits).")
    _check_password_rules(ctx, password)
    if get_by_email(ctx, email):
        raise ServiceError("EMAIL_TAKEN")
    return ctx.db.insert(
        "INSERT INTO account(email,password_hash,full_name,phone,role,status,failed_logins,created_at)"
        " VALUES (?,?,?,?,'CUSTOMER','ACTIVE',0,?)",
        (email, _hash(password), name, phone.strip(), ctx.now_s()))


# -------------------------------------------------------------------- login
def login(ctx: Context, email: str, password: str):
    """BR-03 / BR-05. The message never says whether the email or the password
    was wrong. The 5th wrong password locks the account."""
    acct = get_by_email(ctx, email)
    if acct is None:
        raise ServiceError("BAD_CREDENTIALS")
    account = Account.from_row(acct)  # the rules live in Account.login()
    try:
        account.login(password, ctx.cfg.max_failed_logins)
    except ServiceError:
        if account.failed_logins != acct["failed_logins"] or account.status.value != acct["status"]:
            ctx.db.execute("UPDATE account SET failed_logins=?, status=? WHERE account_id=?",
                           (account.failed_logins, account.status.value, acct["account_id"]))
        raise
    ctx.db.execute("UPDATE account SET failed_logins=0 WHERE account_id=?", (acct["account_id"],))
    return get(ctx, acct["account_id"])


# ------------------------------------------------------------------ profile
def update_profile(ctx: Context, account_id: int, full_name: str, phone: str,
                   current_password: str = "", new_password: str = "") -> None:
    acct = get(ctx, account_id)
    name = require_text(full_name, "your full name")
    if digits(phone) < 9:
        raise ServiceError("INVALID_INPUT", detail="Enter a valid phone number (at least 9 digits).")
    new_hash = None
    if new_password:
        account = Account.from_row(acct)
        account.change_password(current_password, new_password, ctx.cfg.min_password_length)
        new_hash = account.password_hash
    with ctx.db.transaction():
        ctx.db.execute("UPDATE account SET full_name=?, phone=? WHERE account_id=?",
                       (name, phone.strip(), account_id))
        if new_hash:
            ctx.db.execute("UPDATE account SET password_hash=? WHERE account_id=?", (new_hash, account_id))


# ----------------------------------------------------------- password reset
def request_reset(ctx: Context, email: str) -> None:
    """UC-14 step 1-2. Always behaves the same, so nobody can probe which
    emails have an account."""
    email = normalise_email(email)
    if not EMAIL_RE.match(email):
        raise ServiceError("BAD_EMAIL")
    acct = get_by_email(ctx, email)
    if acct is None:
        return
    token, code = Account.from_row(acct).request_reset(ctx.now(), ctx.cfg.reset_code_minutes)
    with ctx.db.transaction():
        # older unused codes stop working
        ctx.db.execute("UPDATE reset_token SET used=1 WHERE account_id=? AND used=0", (acct["account_id"],))
        ctx.db.insert("INSERT INTO reset_token(account_id, code_hash, expires_at, used) VALUES (?,?,?,0)",
                      (token.account_id, token.code_hash, token.expires_at.strftime(DT)))
    try:
        ctx.mailer.send(
            email, "Your Grand Horizon password reset code",
            f"Hello {acct['full_name']},\n\nYour reset code is {code}. It works once and expires in "
            f"{ctx.cfg.reset_code_minutes} minutes.\n\nIf you did not ask for it, ignore this email.")
    except Exception:  # the user can simply request another code
        pass


def reset_password(ctx: Context, email: str, code: str, new_password: str) -> None:
    acct = get_by_email(ctx, email)
    _check_password_rules(ctx, new_password)
    if acct is None:
        raise ServiceError("RESET_INVALID")
    rows = ctx.db.all("SELECT * FROM reset_token WHERE account_id=? AND used=0 ORDER BY token_id DESC",
                      (acct["account_id"],))
    for row in rows:
        token = ResetToken.from_row(row)
        if token.is_valid(ctx.now()) and token.matches(code):
            token.mark_used()
            with ctx.db.transaction():
                ctx.db.execute("UPDATE reset_token SET used=? WHERE token_id=?", (int(token.used), token.token_id))
                ctx.db.execute("UPDATE account SET password_hash=? WHERE account_id=?",
                               (_hash(new_password), acct["account_id"]))
            return
    raise ServiceError("RESET_INVALID")


# --------------------------------------------------------------------- staff
def list_staff(ctx: Context, actor):
    require_role(actor, "ADMIN")
    return ctx.db.all("SELECT * FROM account WHERE role IN ('RECEPTIONIST','ADMIN') ORDER BY full_name")


def create_staff(ctx: Context, actor, full_name: str, email: str, phone: str, role: str) -> str:
    """UC-17. Returns the temporary password (it is also e-mailed)."""
    require_role(actor, "ADMIN")
    name = require_text(full_name, "the full name")
    email = normalise_email(email)
    if not EMAIL_RE.match(email):
        raise ServiceError("BAD_EMAIL")
    if role not in ("RECEPTIONIST", "ADMIN"):
        raise ServiceError("INVALID_INPUT", detail="Choose a role: Receptionist or Admin.")
    if get_by_email(ctx, email):
        raise ServiceError("EMAIL_TAKEN")
    temp = secrets.token_urlsafe(9)
    with ctx.db.transaction():
        new_id = ctx.db.insert(
            "INSERT INTO account(email,password_hash,full_name,phone,role,status,failed_logins,created_at)"
            " VALUES (?,?,?,?,?,'ACTIVE',0,?)",
            (email, _hash(temp), name, (phone or "").strip() or "-", role, ctx.now_s()))
        audit(ctx, actor["account_id"], "CREATE_STAFF", "account", new_id, f"{email} as {role}")
    _mail_temp_password(ctx, email, name, temp)
    return temp


def _mail_temp_password(ctx: Context, email: str, name: str, temp: str) -> None:
    try:
        ctx.mailer.send(email, "Your Grand Horizon staff account",
                        f"Hello {name},\n\nYour temporary password is {temp}. Change it after signing in.")
    except Exception:
        pass


def set_locked(ctx: Context, actor, account_id: int, locked: bool) -> None:
    require_role(actor, "ADMIN")
    target = get(ctx, account_id)
    if target is None or target["role"] == "CUSTOMER":
        raise ServiceError("NOT_FOUND")
    if locked and target["account_id"] == actor["account_id"]:
        # An admin can only reach "the last active admin" by locking themselves (UC-17 exception 2a).
        others = ctx.db.scalar(
            "SELECT COUNT(*) FROM account WHERE role='ADMIN' AND status='ACTIVE' AND account_id<>?", (actor["account_id"],))
        raise ServiceError("LAST_ADMIN" if others == 0 else "SELF_LOCK")
    with ctx.db.transaction():
        ctx.db.execute("UPDATE account SET status=?, failed_logins=0 WHERE account_id=?",
                       ("LOCKED" if locked else "ACTIVE", account_id))
        audit(ctx, actor["account_id"], "LOCK_ACCOUNT" if locked else "UNLOCK_ACCOUNT", "account", account_id,
              target["email"])


def reset_staff_password(ctx: Context, actor, account_id: int) -> str:
    require_role(actor, "ADMIN")
    target = get(ctx, account_id)
    if target is None or target["role"] == "CUSTOMER":
        raise ServiceError("NOT_FOUND")
    temp = secrets.token_urlsafe(9)
    with ctx.db.transaction():
        ctx.db.execute("UPDATE account SET password_hash=?, failed_logins=0 WHERE account_id=?",
                       (_hash(temp), account_id))
        audit(ctx, actor["account_id"], "RESET_STAFF_PASSWORD", "account", account_id, target["email"])
    _mail_temp_password(ctx, target["email"], target["full_name"], temp)
    return temp

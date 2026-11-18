"""Server-side access control, session timeout and CSRF protection (CR-01, CR-04, NFR-02)."""
from __future__ import annotations

import secrets
from datetime import datetime
from functools import wraps

from flask import abort, current_app, g, redirect, request, session, url_for

from ..context import DT

HOME_FOR_ROLE = {"CUSTOMER": "public.home", "RECEPTIONIST": "desk.dashboard", "ADMIN": "admin.dashboard"}


def load_user() -> None:
    """Runs before every request: restore the signed-in account and enforce the 30-minute
    idle timeout (CR-01). A locked account is signed out at once."""
    from ..services import accounts
    g.user = None
    uid = session.get("uid")
    if not uid:
        return
    ctx = g.ctx
    seen = session.get("seen")
    limit = ctx.cfg.session_minutes * 60
    if seen:
        idle = (ctx.now() - datetime.strptime(seen, DT)).total_seconds()
        if idle > limit:
            session.clear()
            session["timed_out"] = True
            return
    acct = accounts.get(ctx, uid)
    if acct is None or acct["status"] != "ACTIVE":
        session.clear()
        return
    session["seen"] = ctx.now_s()
    session.permanent = True
    g.user = acct


def sign_in(acct) -> None:
    session.clear()
    session["uid"] = acct["account_id"]
    session["seen"] = g.ctx.now_s()
    session["csrf"] = secrets.token_urlsafe(24)
    session.permanent = True


def sign_out() -> None:
    session.clear()


def roles(*allowed: str):
    """Only the listed roles may call the view. Anyone else gets MSG-56 (NFR-02);
    a visitor who is not signed in is sent to the login page first."""
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            if g.user is None:
                return redirect(url_for("public.login", next=request.full_path.rstrip("?")))
            if g.user["role"] not in allowed:
                abort(403)
            return fn(*a, **kw)
        return wrapper
    return deco


def csrf_token() -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(24)
    return session["csrf"]


def check_csrf() -> None:
    if not current_app.config.get("CSRF_ENABLED", True):
        return
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
        if not sent or sent != session.get("csrf"):
            abort(400, "The form expired. Reload the page and try again.")

"""Application factory: wires settings, database, gateways, blueprints and filters together."""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from types import SimpleNamespace

from flask import Flask, flash, g, render_template, request, session, url_for

from .context import Clock, Context, PaymentGateway, SimulatedMailer
from .db import Database
from .errors import ServiceError
from .messages import MESSAGES
from .settings import ROOT, Settings


def create_app(settings: Settings | None = None, clock: Clock | None = None, csrf: bool = True) -> Flask:
    cfg = settings or Settings.load()
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=cfg.secret_key,
        PERMANENT_SESSION_LIFETIME=timedelta(minutes=cfg.session_minutes),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        CSRF_ENABLED=csrf,
    )
    db_path = cfg.database_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    shared = SimpleNamespace(
        cfg=cfg,
        clock=clock or Clock(cfg.fixed_datetime()),
        mailer=SimulatedMailer(db_path.parent / "outbox.jsonl"),
        gateway=PaymentGateway(),
        db_path=db_path,
    )
    app.extensions["hotel"] = shared
    _prepare_database(shared)

    from .web import admin, common, desk, public, security

    @app.before_request
    def open_context():
        g.ctx = Context(Database(shared.db_path), shared.cfg, shared.clock, shared.mailer, shared.gateway)
        from .services import bookings
        bookings.expire_holds(g.ctx)          # BR-10: release unpaid holds on every request
        security.load_user()
        security.check_csrf()

    @app.teardown_request
    def close_context(_exc):
        ctx = g.pop("ctx", None)
        if ctx is not None:
            ctx.db.close()

    for bp in (public.bp, desk.bp, admin.bp, common.bp):
        app.register_blueprint(bp)
    _register_template_helpers(app)
    _register_error_pages(app)
    return app


def _prepare_database(shared) -> None:
    """First run: create the schema and, in demo mode, fill it with sample data."""
    from . import seed
    db = Database(shared.db_path)
    try:
        if not db.has_schema():
            db.init_schema()
            if shared.cfg.demo_mode:
                seed.seed_demo(Context(db, shared.cfg, shared.clock, shared.mailer, shared.gateway))
    finally:
        db.close()


# --------------------------------------------------------------------- templates
def dmy(value) -> str:
    """CR-02: dates are shown as dd/mm/yyyy."""
    if not value:
        return ""
    if isinstance(value, (date, datetime)):
        return value.strftime("%d/%m/%Y")
    return "/".join(str(value)[:10].split("-")[::-1])


def dmyhm(value) -> str:
    if not value:
        return ""
    text = str(value)
    return f"{dmy(text[:10])} {text[11:16]}"


def vnd(value) -> str:
    """CR-02: amounts are shown in VND with thousands separators."""
    return f"{int(round(float(value or 0))):,} VND"


def num(value) -> str:
    return f"{int(round(float(value or 0))):,}"


STATUS = {
    "PENDING": ("info", "Awaiting confirmation"), "CONFIRMED": ("ok", "Confirmed"),
    "CHECKED_IN": ("info", "Checked in"), "COMPLETED": ("mute", "Completed"),
    "CANCELLED": ("bad", "Cancelled"), "REJECTED": ("bad", "Rejected by hotel"), "EXPIRED": ("mute", "Expired"),
}
ROOM_STATUS = {"AVAILABLE": "ok", "OCCUPIED": "info", "CLEANING": "warn", "MAINTENANCE": "bad"}


def status_pill(booking) -> tuple[str, str]:
    if booking["status"] == "PENDING" and int(booking["paid"]) < int(booking["deposit_amount"]):
        return "warn", "Deposit due"
    return STATUS[booking["status"]]


def paginate(rows, page: int, size: int):
    """CR-07: lists show 10 rows per page."""
    rows = list(rows)
    pages = max(1, math.ceil(len(rows) / size))
    page = min(max(1, page), pages)
    return SimpleNamespace(items=rows[(page - 1) * size: page * size], page=page, pages=pages, total=len(rows),
                           first=(page - 1) * size + 1 if rows else 0, last=min(page * size, len(rows)))


def _register_template_helpers(app: Flask) -> None:
    from .web.art import HERO, room_art
    from .web.security import csrf_token

    app.jinja_env.filters.update(dmy=dmy, dmyhm=dmyhm, vnd=vnd, num=num)
    def page_url(n: int) -> str:
        args = {**(request.view_args or {}), **request.args.to_dict(), "page": n}
        return url_for(request.endpoint, **args)

    app.jinja_env.globals.update(
        page_url=page_url, csrf_token=csrf_token, room_art=room_art, HERO=HERO, status_pill=status_pill,
        ROOM_STATUS=ROOM_STATUS, today_iso=lambda: g.ctx.today().isoformat(), initials=_initials,
        mcode=lambda key: MESSAGES[key][0])     # message codes always come from messages.py

    @app.context_processor
    def inject():
        return {"user": g.get("user"), "cfg": app.extensions["hotel"].cfg,
                "timed_out": session.pop("timed_out", False)}


def _initials(name: str) -> str:
    parts = (name or "?").split()
    return "".join(w[0] for w in parts[-2:]).upper()


def _register_error_pages(app: Flask) -> None:
    @app.errorhandler(403)
    def forbidden(_e):
        from .messages import render
        code, text = render("FORBIDDEN")
        return render_template("common/error.html", status=403, code=code, title="Access denied", message=text), 403

    @app.errorhandler(404)
    def not_found(_e):
        return render_template("common/error.html", status=404, code="", title="Page not found",
                               message="We could not find that page."), 404

    @app.errorhandler(400)
    def bad_request(e):
        return render_template("common/error.html", status=400, code="", title="Request refused",
                               message=getattr(e, "description", "Bad request.")), 400

    @app.errorhandler(ServiceError)
    def service_error(e):
        if e.key == "FORBIDDEN":
            return forbidden(e)
        if e.key == "NOT_FOUND":
            return not_found(e)
        flash(e.message, "bad")
        return render_template("common/error.html", status=422, code=e.code, title="Not possible",
                               message=e.message), 422

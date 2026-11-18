"""Pages shared by several roles: invoices (SCR-18 / SCR-12) and the demo e-mail outbox."""
from __future__ import annotations

from flask import Blueprint, Response, abort, g, render_template, request

from ..errors import PermissionDenied, ServiceError
from ..services import export, frontdesk
from .security import roles

bp = Blueprint("common", __name__)


def _load_invoice(invoice_no: str):
    inv = frontdesk.get_invoice(g.ctx, invoice_no)
    if inv is None:
        raise ServiceError("NOT_FOUND")
    # a customer may open only their own invoice (NFR-04); staff may open any
    if g.user["role"] == "CUSTOMER" and inv["account_id"] != g.user["account_id"]:
        raise PermissionDenied()
    return inv


@bp.route("/invoices/<invoice_no>")
@roles("CUSTOMER", "RECEPTIONIST", "ADMIN")
def invoice(invoice_no: str):
    inv = _load_invoice(invoice_no)
    return render_template("common/invoice.html", inv=inv, lines=frontdesk.usages(g.ctx, inv["bid"]))


@bp.route("/invoices/<invoice_no>.pdf")
@roles("CUSTOMER", "RECEPTIONIST", "ADMIN")
def invoice_pdf(invoice_no: str):
    inv = _load_invoice(invoice_no)
    data = export.invoice_pdf(inv, frontdesk.usages(g.ctx, inv["bid"]))      # raises MSG-58 on failure
    return Response(data, mimetype="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{inv["invoice_no"]}.pdf"'})


@bp.route("/outbox")
def outbox():
    """Demo only: the e-mails the system would have sent (confirmations, reset codes)."""
    if not g.ctx.cfg.demo_mode:
        abort(404)
    mailer = g.ctx.mailer
    return render_template("common/outbox.html", mails=mailer.read_outbox(40))

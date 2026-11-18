"""Shared dependencies handed to every service function.

Keeping the clock, the e-mail gateway and the payment gateway behind small
interfaces means the business rules can be unit-tested without a real mail
server, a real card processor or waiting for 24 real hours.
"""
from __future__ import annotations

import json
import random
import string
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from .db import Database
from .settings import Settings

DT = "%Y-%m-%d %H:%M:%S"


class Clock:
    def __init__(self, fixed: datetime | None = None):
        self._fixed = fixed
        self._offset = timedelta(0)

    def now(self) -> datetime:
        base = self._fixed or datetime.now()
        return (base + self._offset).replace(microsecond=0)

    def today(self) -> date:
        return self.now().date()

    def advance(self, **kw) -> None:  # used by tests and the demo
        self._offset += timedelta(**kw)


# ------------------------------------------------------------------ e-mail
class EmailGateway:
    def send(self, to: str, subject: str, body: str) -> None:  # pragma: no cover
        raise NotImplementedError


class SimulatedMailer(EmailGateway):
    """Writes e-mails to an outbox file instead of using SMTP (report 2.1: the
    e-mail service is external). `fail_next` makes the next N sends fail so the
    3-attempt retry rule (BR-35) can be demonstrated and tested."""

    def __init__(self, outbox: Path | None = None):
        self.outbox = outbox
        self.sent: list[dict] = []
        self.fail_next = 0
        self._lock = threading.Lock()

    def send(self, to: str, subject: str, body: str) -> None:
        with self._lock:
            if self.fail_next > 0:
                self.fail_next -= 1
                raise ConnectionError("simulated SMTP failure")
            item = {"at": datetime.now().strftime(DT), "to": to, "subject": subject, "body": body}
            self.sent.append(item)
            if self.outbox:
                self.outbox.parent.mkdir(parents=True, exist_ok=True)
                with self.outbox.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(item, ensure_ascii=False) + "\n")

    def read_outbox(self, limit: int = 50) -> list[dict]:
        if not self.outbox or not self.outbox.exists():
            return list(reversed(self.sent))[:limit]
        lines = self.outbox.read_text(encoding="utf-8").splitlines()[-limit:]
        return [json.loads(x) for x in reversed(lines)]


# ----------------------------------------------------------------- payment
@dataclass
class PaymentResult:
    ok: bool
    reference: str = ""
    reason: str = ""


class PaymentGateway:
    """Simulated gateway (BR-23). Cash is always accepted. Cards and e-wallets
    succeed unless `decline_next` is set (the demo switch on SCR-09)."""

    def __init__(self):
        self.decline_next = False

    def charge(self, amount: int, method: str, decline: bool = False) -> PaymentResult:
        if method != "CASH" and (decline or self.decline_next):
            self.decline_next = False
            return PaymentResult(False, reason="declined")
        prefix = {"CASH": "CSH", "CARD": "CRD", "E_WALLET": "EWL"}[method]
        ref = prefix + "".join(random.choices(string.ascii_uppercase + string.digits, k=9))
        return PaymentResult(True, reference=ref)


@dataclass
class Context:
    db: Database
    cfg: Settings
    clock: Clock = field(default_factory=Clock)
    mailer: EmailGateway = field(default_factory=SimulatedMailer)
    gateway: PaymentGateway = field(default_factory=PaymentGateway)

    def now(self) -> datetime:
        return self.clock.now()

    def today(self) -> date:
        return self.clock.today()

    def now_s(self) -> str:
        return self.now().strftime(DT)

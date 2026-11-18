"""Business and system parameters loaded from config.ini (NFR-05).

Nothing in the services hard-codes the deposit rate, hold time or VAT: they all
read these values, so a change in config.ini needs no source change.
"""
from __future__ import annotations

import configparser
from dataclasses import dataclass, fields, replace
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    # business
    deposit_rate: float = 0.30
    deposit_rounding: int = 1000
    hold_hours: int = 24
    vat_rate: float = 0.10
    late_checkout_hour: int = 12
    late_fee_rate: float = 0.5
    checkin_hour: int = 14
    max_nights: int = 30
    refund_full_hours: int = 48
    refund_half_hours: int = 24
    refund_half_rate: int = 50
    change_min_hours: int = 48
    # security
    min_password_length: int = 8
    max_failed_logins: int = 5
    session_minutes: int = 30
    reset_code_minutes: int = 30
    secret_key: str = "change-me-before-deploying"
    # system
    page_size: int = 10
    email_max_attempts: int = 3
    database: str = "instance/hotel.db"
    demo_mode: bool = True
    fixed_now: str = ""

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Settings":
        path = Path(path) if path else ROOT / "config.ini"
        parser = configparser.ConfigParser()
        if path.exists():
            parser.read(path, encoding="utf-8")
        values = {}
        for f in fields(cls):
            raw = None
            for section in parser.sections():
                if parser.has_option(section, f.name):
                    raw = parser.get(section, f.name).strip()
            if raw is None:
                continue
            if f.type in ("int", int):
                values[f.name] = int(raw)
            elif f.type in ("float", float):
                values[f.name] = float(raw)
            elif f.type in ("bool", bool):
                values[f.name] = raw.lower() in ("1", "true", "yes", "on")
            else:
                values[f.name] = raw
        return cls(**values)

    def with_overrides(self, **kw) -> "Settings":
        return replace(self, **kw)

    def database_path(self) -> Path:
        p = Path(self.database)
        return p if p.is_absolute() else ROOT / p

    def fixed_datetime(self) -> datetime | None:
        if not self.fixed_now:
            return None
        return datetime.fromisoformat(self.fixed_now)

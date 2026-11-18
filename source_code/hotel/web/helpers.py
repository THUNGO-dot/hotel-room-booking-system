from __future__ import annotations

from urllib.parse import urlparse

from flask import g, request


def to_int(value, default: int, lo: int = 0, hi: int = 99) -> int:
    try:
        return min(max(int(value), lo), hi)
    except (TypeError, ValueError):
        return default


def safe_next(target: str | None) -> str | None:
    """Only follow redirects that stay on this site."""
    if not target:
        return None
    parts = urlparse(target)
    if parts.scheme or parts.netloc or not target.startswith("/") or target.startswith("//"):
        return None
    return target


def page_number() -> int:
    return to_int(request.args.get("page"), 1, 1, 10_000)


def page_size() -> int:
    return g.ctx.cfg.page_size

from __future__ import annotations

from .messages import render


class ServiceError(Exception):
    """A business rule was broken. Carries a standard message (MSG-xx)."""

    def __init__(self, key: str, **kw):
        self.key = key
        self.code, self.message = render(key, **kw)
        super().__init__(self.message)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"[{self.code}] {self.message}" if self.code else self.message


class PermissionDenied(ServiceError):
    def __init__(self):
        super().__init__("FORBIDDEN")

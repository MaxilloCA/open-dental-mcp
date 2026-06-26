"""Open Dental exceptions — typed hierarchy with structured context."""
from __future__ import annotations

from typing import Any


class OpenDentalError(Exception):
    """Base exception for all Open Dental client errors."""

    http_status: int | None = None

    def __init__(
        self,
        message: str,
        *,
        http_status: int | None = None,
        error_code: str | None = None,
        request_id: str | None = None,
        body: Any = None,
    ) -> None:
        super().__init__(message)
        self.http_status = http_status if http_status is not None else self.http_status
        self.error_code = error_code
        self.request_id = request_id
        self.body = body

    def __repr__(self) -> str:
        parts = [f"http_status={self.http_status!r}"]
        if self.error_code:
            parts.append(f"error_code={self.error_code!r}")
        if self.request_id:
            parts.append(f"request_id={self.request_id!r}")
        return f"{type(self).__name__}({', '.join(parts)})"


class OpenDentalAuthError(OpenDentalError):
    """401 (bad keys) or 403 (insufficient permission)."""

    http_status = 401


class OpenDentalNotFoundError(OpenDentalError):
    """404 — patient/appointment/etc. doesn't exist or you don't have access."""

    http_status = 404


class OpenDentalRateLimitError(OpenDentalError):
    """429 — rate limit hit."""

    http_status = 429

    def __init__(
        self,
        message: str,
        *,
        retry_after: float | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


class OpenDentalAPIError(OpenDentalError):
    """5xx or other non-2xx response."""

    http_status = 500


class OpenDentalConnectionError(OpenDentalError):
    """Network-level failure."""

    http_status = None

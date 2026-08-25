"""Shared HTTP response helpers."""

from fastapi.responses import JSONResponse


def error(detail: str, status: int) -> JSONResponse:
    """A `{"detail": ...}` JSON error body with the given status code.

    Mirrors DRF's error shape so the frontend can read `.detail` uniformly.
    """
    return JSONResponse({"detail": detail}, status_code=status)

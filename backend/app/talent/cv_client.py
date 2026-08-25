"""Thin HTTP client for the standalone cv-generator service.

The backend builds the allow-listed snapshot from its own database models, then
sends it to cv-generator for rendering into HTML. This keeps access control and
data ownership in the backend while moving the CV template/rendering code out.
"""

import logging

import requests

from app.core.config import settings

logger = logging.getLogger(__name__)


class CVGeneratorError(Exception):
    """Network / unexpected-status error talking to cv-generator."""


def render(snapshot: dict, target_role: str) -> tuple[str, str]:
    """Render a snapshot via cv-generator.

    Returns ``(html, sha256)``. Raises ``CVGeneratorError`` on failure.
    """
    url = f"{settings.CV_GENERATOR_URL}/generate"
    payload = {
        "snapshot": snapshot,
        "target_role": target_role,
    }
    try:
        resp = requests.post(
            url,
            json=payload,
            timeout=settings.CV_GENERATOR_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise CVGeneratorError(f"cv-generator request failed: {exc}") from exc

    if resp.status_code >= 400:
        raise CVGeneratorError(
            f"cv-generator returned {resp.status_code}: {resp.text[:200]}"
        )

    try:
        data = resp.json()
    except ValueError as exc:
        raise CVGeneratorError(f"cv-generator returned invalid JSON: {exc}") from exc

    return data["html"], data["sha256"]

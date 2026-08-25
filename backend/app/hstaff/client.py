"""Thin HTTP client for the external hstaff backend.

Public endpoints (/auth/login, /auth/refresh) work with no privileged creds, so
we proxy user logins. Authenticated reads use the user's own hstaff token, so
hstaff enforces their RBAC.
"""

from __future__ import annotations

import logging
import threading

import requests

from app.core.config import settings

logger = logging.getLogger(__name__)


class HstaffError(Exception):
    """Network / unexpected-status error talking to hstaff."""


class HstaffAuthError(HstaffError):
    """hstaff rejected the credentials (401/403) — used to distinguish bad login."""


class HstaffClient:
    def __init__(self):
        self.base = f"{settings.HSTAFF_BASE_URL}{settings.HSTAFF_API_PREFIX}"
        self.timeout = settings.HSTAFF_TIMEOUT
        self.enabled = settings.HSTAFF_ENABLED
        # The privileged service account targets the same base as the rest of
        # the integration (HSTAFF_BASE_URL / HSTAFF_API_PREFIX).
        self.svc_base = self.base
        # Cached privileged service-account token (see service_login).
        self._svc_token: str | None = None
        self._svc_lock = threading.Lock()

    # ---- internal ----
    def _request(self, method: str, path: str, *, token: str | None = None, **kwargs):
        url = f"{self.base}{path}"
        headers = kwargs.pop("headers", {})
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            resp = requests.request(
                method, url, headers=headers, timeout=self.timeout, **kwargs
            )
        except requests.RequestException as exc:
            raise HstaffError(f"hstaff request failed: {exc}") from exc
        if resp.status_code in (401, 403):
            raise HstaffAuthError(f"hstaff auth rejected ({resp.status_code})")
        if resp.status_code >= 400:
            raise HstaffError(f"hstaff returned {resp.status_code}: {resp.text[:200]}")
        return resp

    # ---- public (no privileged creds required) ----
    def login(self, email: str, password: str) -> dict:
        resp = self._request(
            "POST", "/auth/login", json={"email": email, "password": password}
        )
        return resp.json()

    def get_my_permissions(self, access_token: str) -> dict:
        return self._request("GET", "/permissions/me", token=access_token).json()

    def get_profile(self, access_token: str) -> dict:
        return self._request("GET", "/profile/", token=access_token).json()

    def refresh(self, refresh_token: str) -> dict:
        return self._request(
            "POST", "/auth/refresh", json={"refresh_token": refresh_token}
        ).json()

    def get(self, access_token: str, path: str, params=None):
        """Generic authenticated GET passthrough. Returns (status_code, json).

        Unlike the typed methods this does NOT raise on 4xx — the caller inspects
        the status (e.g. 401 → refresh + retry)."""
        return self.call(access_token, "GET", path, params=params)

    def call(
        self,
        access_token: str,
        method: str,
        path: str,
        *,
        params=None,
        json=None,
        files=None,
        data=None,
    ):
        """Generic authenticated passthrough for any method. Returns (status, json).

        Like `get`, does NOT raise on 4xx so the caller can refresh-and-retry on
        401. Supports JSON bodies and multipart uploads (files/data) so profile
        edits and avatar uploads proxy through under the user's own hstaff token,
        which keeps hstaff's RBAC in force."""
        url = f"{self.base}/{path.lstrip('/')}"
        try:
            resp = requests.request(
                method,
                url,
                headers={"Authorization": f"Bearer {access_token}"},
                params=params,
                json=json,
                files=files,
                data=data,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise HstaffError(f"hstaff request failed: {exc}") from exc
        try:
            return resp.status_code, resp.json()
        except ValueError:
            return resp.status_code, (
                {"detail": resp.text[:200]} if resp.text else None
            )

    # ---- privileged service account (HR credentials) ----
    def _svc_request(
        self, method: str, path: str, *, token: str | None = None, **kwargs
    ):
        """Like `call`, but against `svc_base` (dev hstaff). Returns (status, json)."""
        url = f"{self.svc_base}/{path.lstrip('/')}"
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            resp = requests.request(
                method, url, headers=headers, timeout=self.timeout, **kwargs
            )
        except requests.RequestException as exc:
            raise HstaffError(f"hstaff request failed: {exc}") from exc
        try:
            return resp.status_code, resp.json()
        except ValueError:
            return resp.status_code, (
                {"detail": resp.text[:200]} if resp.text else None
            )

    def _service_login(self) -> str:
        """Log in with the configured HR service credentials and cache the token."""
        if not settings.HSTAFF_SERVICE_EMAIL or not settings.HSTAFF_SERVICE_PASSWORD:
            raise HstaffError(
                "hstaff service account not configured "
                "(set HSTAFF_SERVICE_EMAIL / HSTAFF_SERVICE_PASSWORD)."
            )
        status, data = self._svc_request(
            "POST",
            "/auth/login",
            json={
                "email": settings.HSTAFF_SERVICE_EMAIL,
                "password": settings.HSTAFF_SERVICE_PASSWORD,
            },
        )
        if status in (401, 403):
            raise HstaffAuthError(
                "hstaff service login rejected — check HSTAFF_SERVICE_* credentials."
            )
        if status >= 400 or not isinstance(data, dict):
            raise HstaffError(f"hstaff service login failed ({status}).")
        token = data.get("access_token")
        if not token:
            raise HstaffError("hstaff service login returned no access_token.")
        self._svc_token = token
        return token

    def _service_token(self, force: bool = False) -> str:
        with self._svc_lock:
            if force or not self._svc_token:
                return self._service_login()
            return self._svc_token

    def create_user(self, payload: dict) -> tuple[int, dict | None]:
        """Register a user on hstaff via the privileged service account.

        Returns (status_code, json). Re-authenticates once if the cached service
        token has expired (401/403). Does not raise on hstaff 4xx (e.g. duplicate
        email) so the caller can surface hstaff's validation message verbatim."""
        token = self._service_token()
        status, body = self._svc_request(
            "POST", "/admin/create", token=token, json=payload
        )
        if status in (401, 403):
            token = self._service_token(force=True)
            status, body = self._svc_request(
                "POST", "/admin/create", token=token, json=payload
            )
        return status, body

    def service_get(
        self, path: str, params: dict | None = None
    ) -> tuple[int, dict | list | None]:
        """Authenticated GET against hstaff using the service account.

        Returns (status, json); re-authenticates once on token expiry. Does not
        raise on hstaff 4xx so callers can skip unavailable sections (e.g. a
        heatmap the HR account isn't allowed to read for a given user)."""
        token = self._service_token()
        status, body = self._svc_request("GET", path, token=token, params=params)
        if status in (401, 403):
            token = self._service_token(force=True)
            status, body = self._svc_request("GET", path, token=token, params=params)
        return status, body

    def get_talent(self, user_id: int) -> tuple[int, dict | list | None]:
        """Read another user's hstaff talent profile via the service account.
        Uses hstaff's GET /talent/{user_id} (readable by any authenticated member)."""
        return self.service_get(f"/talent/{user_id}")

    def list_users(self) -> tuple[int, list | None]:
        """Fetch all users from hstaff via the service account.

        Uses hstaff's GET /talent/all (open to any authenticated member).
        Returns (status_code, list of user dicts)."""
        status, body = self.service_get("/talent/all")
        if status == 200 and isinstance(body, list):
            return status, body
        return status, (body if isinstance(body, list) else None)


client = HstaffClient()

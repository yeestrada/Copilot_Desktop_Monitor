from __future__ import annotations

from typing import Any

import requests

from browser_utils import open_url

GITHUB_CLIENT_ID = "Iv1.b507a08c87ecfe98"
GITHUB_DEVICE_CODE_URL = "https://github.com/login/device/code"
GITHUB_ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_SCOPE = "read:user"
COPILOT_INTERNAL_PATH = "/copilot_internal/user"
COPILOT_INTERNAL_API_VERSION = "2025-05-01"
GITHUB_API = "https://api.github.com"


class GitHubAuthError(Exception):
    pass


class GitHubAuthPending(Exception):
    """Raised while waiting for the user to approve device authorization."""


def open_github_device_login(user_code: str, verification_uri: str) -> None:
    normalized = user_code.strip().upper()
    base = verification_uri.rstrip("/")
    # GitHub accepts the code in the query string so the page opens pre-filled.
    open_url(f"{base}?user_code={normalized}")


def normalize_user_code(user_code: str) -> str:
    return user_code.strip().upper()


def request_device_code() -> dict[str, Any]:
    response = requests.post(
        GITHUB_DEVICE_CODE_URL,
        headers={"Accept": "application/json"},
        data={"client_id": GITHUB_CLIENT_ID, "scope": GITHUB_SCOPE},
        timeout=30,
    )
    if not response.ok:
        detail = response.text[:200]
        raise GitHubAuthError(f"GitHub device authorization failed (HTTP {response.status_code}): {detail}")

    payload = response.json()
    if not isinstance(payload, dict):
        raise GitHubAuthError("GitHub returned an invalid device authorization response.")

    for key in ("device_code", "user_code", "verification_uri"):
        if not str(payload.get(key, "")).strip():
            raise GitHubAuthError(f"GitHub device authorization response missing {key}.")

    return payload


def poll_access_token(device_code: str) -> str:
    response = requests.post(
        GITHUB_ACCESS_TOKEN_URL,
        headers={"Accept": "application/json"},
        data={
            "client_id": GITHUB_CLIENT_ID,
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        },
        timeout=30,
    )
    if not response.ok:
        detail = response.text[:200]
        raise GitHubAuthError(f"GitHub token request failed (HTTP {response.status_code}): {detail}")

    payload = response.json()
    if not isinstance(payload, dict):
        raise GitHubAuthError("GitHub returned an invalid token response.")

    access_token = str(payload.get("access_token", "")).strip()
    if access_token:
        return access_token

    error = str(payload.get("error", "")).strip()
    if error in {"authorization_pending", "slow_down"}:
        raise GitHubAuthPending(error)

    description = str(payload.get("error_description") or error or "Unknown error").strip()
    raise GitHubAuthError(description)


def validate_github_token(token: str) -> str:
    """Validate token against Copilot usage API and return GitHub login."""
    normalized = token.strip()
    if not normalized:
        raise GitHubAuthError("GitHub token is empty.")

    response = requests.get(
        f"{GITHUB_API}{COPILOT_INTERNAL_PATH}",
        headers={
            "Authorization": f"Bearer {normalized}",
            "Accept": "application/json",
            "X-GitHub-Api-Version": COPILOT_INTERNAL_API_VERSION,
            "User-Agent": "CopilotDesktopMonitor/2.0",
        },
        timeout=30,
    )
    if response.status_code == 401:
        raise GitHubAuthError("GitHub authorization was rejected (401). Try signing in again.")
    if response.status_code == 403:
        raise GitHubAuthError(
            "This GitHub account cannot read Copilot usage. Check your Copilot subscription."
        )
    if not response.ok:
        detail = response.text[:200]
        raise GitHubAuthError(f"GitHub rejected the token (HTTP {response.status_code}): {detail}")

    payload = response.json()
    if not isinstance(payload, dict):
        raise GitHubAuthError("GitHub returned an invalid Copilot profile response.")

    login = str(payload.get("login") or "").strip()
    if login:
        return login

    user_response = requests.get(
        f"{GITHUB_API}/user",
        headers={
            "Authorization": f"Bearer {normalized}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "CopilotDesktopMonitor/2.0",
        },
        timeout=30,
    )
    if user_response.ok:
        user_payload = user_response.json()
        if isinstance(user_payload, dict):
            login = str(user_payload.get("login") or "").strip()
            if login:
                return login

    return "GitHub account"

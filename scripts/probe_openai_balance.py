"""Probe OpenAI credit balance via Firefox cookies + dashboard HTML/API."""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import tempfile
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DIST_CONFIG = ROOT / "dist" / "config.json"
CREDIT_URLS = (
    "https://api.openai.com/v1/dashboard/billing/credit_grants",
    "https://api.openai.com/dashboard/billing/credit_grants",
)
BILLING_URL = "https://platform.openai.com/settings/organization/billing/overview"
CREDIT_GRANTS_PAGE = "https://platform.openai.com/settings/organization/billing/credit-grants"


def load_api_key() -> str:
    if not DIST_CONFIG.exists():
        return ""
    data = json.loads(DIST_CONFIG.read_text(encoding="utf-8"))
    for account in data.get("accounts", []):
        if account.get("provider") == "openai":
            return str(account.get("api_key") or "").strip()
    return ""


def firefox_openai_cookies() -> dict[str, str]:
    ff_root = Path(os.environ.get("APPDATA", "")) / "Mozilla" / "Firefox" / "Profiles"
    if not ff_root.exists():
        return {}

    merged: dict[str, str] = {}
    for profile in ff_root.iterdir():
        db = profile / "cookies.sqlite"
        if not db.exists():
            continue
        tmp = Path(tempfile.gettempdir()) / f"ff_openai_{profile.name}.sqlite"
        shutil.copy2(db, tmp)
        try:
            con = sqlite3.connect(str(tmp))
            cur = con.cursor()
            cur.execute(
                """
                SELECT host, name, value
                FROM moz_cookies
                WHERE host LIKE '%openai%'
                """
            )
            rows = cur.fetchall()
            con.close()
        except Exception as exc:  # noqa: BLE001
            print(f"cookie_error {profile.name}: {exc}")
            continue

        print(f"profile={profile.name} openai_cookies={len(rows)}")
        for host, name, value in rows:
            # Last write wins; prefer host-specific later if needed.
            merged[name] = value
            print(f"  cookie {host} {name} len={len(value)}")
    return merged


def try_credit_grants(session: requests.Session, label: str) -> bool:
    for url in CREDIT_URLS:
        response = session.get(url, timeout=30)
        print(f"{label} {url.split('.com', 1)[-1]} -> {response.status_code}")
        if response.status_code != 200:
            print(f"  body: {response.text[:160]}")
            continue
        data = response.json()
        print(
            "  SUCCESS",
            {
                "total_granted": data.get("total_granted"),
                "total_used": data.get("total_used"),
                "total_available": data.get("total_available"),
            },
        )
        return True
    return False


def extract_balances(html: str) -> list[str]:
    # Obfuscated class can change; look for money near billing wording too.
    patterns = [
        r'class="guPvt"[^>]*>\s*(\$\d+(?:\.\d+)?)',
        r"Credit balance[^$]{0,120}(\$\d+(?:\.\d+)?)",
        r"Pay as you go[^$]{0,120}(\$\d+(?:\.\d+)?)",
        r"total_available[^0-9]{0,40}(\d+(?:\.\d+)?)",
        r"(\$\d+\.\d{2})",
    ]
    found: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, html, flags=re.IGNORECASE | re.DOTALL):
            found.append(match.group(1))
    # unique preserve order
    out: list[str] = []
    for item in found:
        if item not in out:
            out.append(item)
    return out


def main() -> None:
    headers = {
        "Accept": "text/html,application/json",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:154.0) "
            "Gecko/20100101 Firefox/154.0"
        ),
    }
    api_key = load_api_key()
    session = requests.Session()
    session.headers.update(headers)

    if api_key:
        session.headers["Authorization"] = f"Bearer {api_key}"
        print("--- try Admin/API key as Bearer ---")
        try_credit_grants(session, "api_key")
        session.headers.pop("Authorization", None)

    cookies = firefox_openai_cookies()
    if cookies:
        session.cookies.update(cookies)
        print("--- try Firefox cookies as Cookie jar ---")
        try_credit_grants(session, "ff_cookies")

        # Also try auth-looking cookies as Bearer.
        for name, value in cookies.items():
            lowered = name.lower()
            if not any(token in lowered for token in ("session", "auth", "token", "auth0")):
                continue
            session.headers["Authorization"] = f"Bearer {value}"
            print(f"--- try cookie-as-bearer {name} ---")
            ok = try_credit_grants(session, f"bearer:{name}")
            session.headers.pop("Authorization", None)
            if ok:
                return

        print("--- fetch billing overview HTML ---")
        for page in (BILLING_URL, CREDIT_GRANTS_PAGE):
            response = session.get(page, timeout=30, allow_redirects=True)
            print(f"GET {page} -> {response.status_code} final={response.url} bytes={len(response.text)}")
            balances = extract_balances(response.text)
            print(f"  balances_found={balances[:10]}")
            if "guPvt" in response.text:
                print("  contains class guPvt: yes")
            else:
                print("  contains class guPvt: no")
            # Show a short snippet around first dollar amount if present.
            m = re.search(r".{0,40}\$\d+\.\d{2}.{0,40}", response.text)
            if m:
                print(f"  snippet: {m.group(0)!r}")
    else:
        print("No Firefox openai cookies found")


if __name__ == "__main__":
    main()

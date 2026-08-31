from __future__ import annotations

import re
from typing import Callable, Iterable

_BROWSER_LOADERS: list[tuple[str, str]] = [
    ("Microsoft Edge", "edge"),
    ("Google Chrome", "chrome"),
    ("Mozilla Firefox", "firefox"),
    ("Brave", "brave"),
    ("Chromium", "chromium"),
]

CHROMIUM_DECRYPT_ERROR = "Unable to get key for cookie decryption"


def assemble_chunked_cookie(cookies: dict[str, str], prefix: str) -> str | None:
    chunks: dict[int, str] = {}
    for name, value in cookies.items():
        if name == prefix:
            chunks[0] = value
            continue
        if not name.startswith(f"{prefix}."):
            continue
        suffix = name[len(prefix) + 1 :]
        if suffix.isdigit():
            chunks[int(suffix)] = value
    if not chunks:
        return None
    return "".join(chunks[index] for index in sorted(chunks))


def read_provider_cookies(
    *,
    domains: Iterable[str],
    cookie_prefixes: Iterable[str],
) -> tuple[dict[str, str], list[str]]:
    """Read and merge cookies from installed browsers."""
    try:
        import browser_cookie3
    except ImportError as exc:
        raise RuntimeError(
            "browser-cookie3 is not installed. Run: py -m pip install browser-cookie3 pycryptodomex"
        ) from exc

    notes: list[str] = []
    merged: dict[str, str] = {}
    prefixes = tuple(cookie_prefixes)

    for label, loader_name in _BROWSER_LOADERS:
        loader = getattr(browser_cookie3, loader_name, None)
        if loader is None:
            continue

        browser_found = False
        for domain in domains:
            try:
                cookie_jar = loader(domain_name=domain)
            except Exception as exc:  # noqa: BLE001
                message = str(exc)
                if CHROMIUM_DECRYPT_ERROR in message and loader_name in {"chrome", "edge", "brave", "chromium"}:
                    notes.append(
                        f"{label}: encrypted cookies (Windows). "
                        "Use Firefox or paste the sess- token manually."
                    )
                else:
                    notes.append(f"{label}@{domain}: {message}")
                continue

            domain_hits = 0
            for cookie in cookie_jar:
                name = str(getattr(cookie, "name", ""))
                if not any(name == prefix or name.startswith(f"{prefix}.") for prefix in prefixes):
                    continue
                value = str(getattr(cookie, "value", "")).strip()
                if not value:
                    continue
                merged[name] = value
                domain_hits += 1
                browser_found = True

            if domain_hits:
                notes.append(f"{label}@{domain}: {domain_hits} session cookie(s)")

        if browser_found and not any(label.startswith("Mozilla Firefox") for label in notes if "cookie(s)" in label):
            pass

    for prefix in prefixes:
        assembled = assemble_chunked_cookie(merged, prefix)
        if assembled:
            merged[prefix] = assembled
            for name in list(merged):
                if name.startswith(f"{prefix}."):
                    del merged[name]

    if merged:
        return merged, notes

    if not notes:
        notes.append("No session cookies found in any browser.")
    return {}, notes


def pick_cookie_subset(cookies: dict[str, str], names: Iterable[str]) -> dict[str, str]:
    return {name: cookies[name] for name in names if cookies.get(name)}


def extract_sess_key(text: str) -> str | None:
    cleaned = text.strip().strip('"').strip("'")
    if not cleaned:
        return None

    for line in cleaned.splitlines():
        line = line.strip().strip(",")
        if "authorization" in line.lower():
            _, _, value = line.partition(":")
            cleaned = value.strip()
            break

    if cleaned.lower().startswith("bearer "):
        cleaned = cleaned[7:].strip()

    if cleaned.startswith("sess-"):
        return cleaned.split()[0].rstrip(",;")

    match = re.search(r"sess-[A-Za-z0-9_-]{20,}", cleaned)
    return match.group(0) if match else None


def extract_bearer_jwt(text: str) -> str | None:
    cleaned = text.strip().strip('"').strip("'")
    for line in cleaned.splitlines():
        line = line.strip().strip(",")
        if "authorization" in line.lower():
            _, _, value = line.partition(":")
            cleaned = value.strip()
            break
    if cleaned.lower().startswith("bearer "):
        cleaned = cleaned[7:].strip()
    match = re.search(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", cleaned)
    return match.group(0) if match else None


def first_success(values: Iterable[str], predicate: Callable[[str], str | None]) -> str | None:
    for value in values:
        result = predicate(value)
        if result:
            return result
    return None

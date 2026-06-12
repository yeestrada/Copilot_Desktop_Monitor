import requests

from _config_loader import load_github_account

cfg = load_github_account()
token = cfg["token"]
user = cfg["github_username"]
organization = str(cfg.get("organization", "")).strip()
headers = {
    "Authorization": f"Bearer {token}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2026-03-10",
}
base = "https://api.github.com"

tests = [
    ("GET", f"/users/{user}/settings/billing/premium_request/usage", None),
    ("GET", f"/users/{user}/settings/billing/usage/summary", None),
    ("GET", f"/users/{user}/settings/billing/usage", None),
    ("GET", f"/user/copilot", None),
]

if organization:
    tests.extend(
        [
            ("GET", f"/organizations/{organization}/settings/billing/premium_request/usage", {"user": user}),
            ("GET", f"/organizations/{organization}/settings/billing/usage/summary", {"user": user}),
            ("GET", f"/orgs/{organization}/copilot/billing", None),
            ("GET", f"/orgs/{organization}/members/{user}/copilot", None),
        ]
    )

for method, path, params in tests:
    r = requests.request(method, base + path, headers=headers, params=params, timeout=20)
    snippet = ""
    try:
        data = r.json()
        if isinstance(data, dict):
            keys = list(data.keys())[:6]
            snippet = f"keys={keys}"
            if "usageItems" in data:
                snippet += f" items={len(data['usageItems'])}"
            if "message" in data:
                snippet += f" msg={data['message'][:80]}"
    except Exception:
        snippet = r.text[:80]
    print(f"{r.status_code} {path} :: {snippet}")

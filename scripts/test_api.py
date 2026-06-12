import json
from pathlib import Path

import requests

cfg = json.loads(Path("config.json").read_text())
token = cfg["token"]
user = cfg["github_username"]
headers = {
    "Authorization": f"Bearer {token}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}
base = "https://api.github.com"

# Token scopes (classic PAT exposes this)
r = requests.get(f"{base}/user", headers=headers, timeout=20)
print("login:", r.json().get("login"))
scopes = r.headers.get("X-OAuth-Scopes", "(no scopes header)")
print("scopes:", scopes)

# Orgs detail
r = requests.get(f"{base}/user/orgs?per_page=100", headers=headers, timeout=20)
for org in r.json():
    login = org.get("login")
    print(f"\nOrg: {login}")
    for path in [
        f"/orgs/{login}/copilot/billing",
        f"/orgs/{login}/members/{user}/copilot",
        f"/organizations/{login}/settings/billing/premium_request/usage",
    ]:
        params = {"user": user, "product": "Copilot"} if "billing" in path else None
        resp = requests.get(base + path, headers=headers, params=params, timeout=20)
        msg = ""
        try:
            msg = resp.json().get("message", "")
        except Exception:
            msg = resp.text[:60]
        print(f"  {resp.status_code} {path.split(login)[1]} :: {msg[:70]}")

# Try enterprise list (if accessible)
for ent in ["WEC", "wec", "WEC-Labs", "wec-labs"]:
    resp = requests.get(
        f"{base}/enterprises/{ent}/settings/billing/premium_request/usage",
        headers=headers,
        params={"user": user, "organization": "WEC-Labs"},
        timeout=20,
    )
    if resp.status_code != 404:
        print(f"enterprise {ent}: {resp.status_code} {resp.text[:100]}")

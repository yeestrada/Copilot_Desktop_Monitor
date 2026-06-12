import json

import requests

from _config_loader import load_github_account

cfg = load_github_account()
token = cfg["token"]
user = cfg["github_username"]
organization = str(cfg.get("organization", "")).strip()
enterprise = str(cfg.get("enterprise", "")).strip()
headers = {
    "Authorization": f"Bearer {token}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}
base = "https://api.github.com"

if organization:
    for path in [
        f"/orgs/{organization}",
        f"/orgs/{organization}/memberships/{user}",
    ]:
        r = requests.get(base + path, headers=headers, timeout=20)
        print(path, r.status_code)
        if r.ok:
            print(json.dumps(r.json(), indent=2)[:800])
else:
    print("organization: skipped (not set in config.json)")

r = requests.get(f"{base}/user/memberships/orgs", headers=headers, timeout=20)
print("/user/memberships/orgs", r.status_code)
if r.ok:
    print(json.dumps(r.json(), indent=2)[:800])

if enterprise:
    params: dict[str, str] = {"user": user}
    if organization:
        params["organization"] = organization
    r = requests.get(
        f"{base}/enterprises/{enterprise}/settings/billing/premium_request/usage",
        headers=headers,
        params=params,
        timeout=20,
    )
    print(f"enterprise {enterprise}:", r.status_code, r.text[:200])
else:
    print("enterprise: skipped (not set in config.json)")

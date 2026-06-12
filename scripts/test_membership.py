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
org = "WEC-Labs"

for path in [
    f"/orgs/{org}",
    f"/orgs/{org}/memberships/{user}",
    "/user/memberships/orgs",
]:
    r = requests.get(base + path, headers=headers, timeout=20)
    print(path, r.status_code)
    if r.ok:
        print(json.dumps(r.json(), indent=2)[:800])

for ent in ["WEC", "wec", "WEC-Labs", "wec-labs", "wec-labs-inc"]:
    r = requests.get(
        f"{base}/enterprises/{ent}/settings/billing/premium_request/usage",
        headers=headers,
        params={"user": user, "organization": org},
        timeout=20,
    )
    if r.status_code != 404:
        print("enterprise hit:", ent, r.status_code, r.text[:200])

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

r = requests.get(f"{base}/user", headers=headers, timeout=20)
print("login:", r.json().get("login"))
scopes = r.headers.get("X-OAuth-Scopes", "(no scopes header)")
print("scopes:", scopes)

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

if enterprise:
    params: dict[str, str] = {"user": user}
    if organization:
        params["organization"] = organization
    resp = requests.get(
        f"{base}/enterprises/{enterprise}/settings/billing/premium_request/usage",
        headers=headers,
        params=params,
        timeout=20,
    )
    print(f"enterprise {enterprise}: {resp.status_code} {resp.text[:100]}")
else:
    print("enterprise: skipped (not set in config.json)")

import json

import requests

from _config_loader import load_github_account

cfg = load_github_account()
token = cfg["token"]
headers = {
    "Authorization": f"Bearer {token}",
    "Accept": "application/json",
    "X-GitHub-Api-Version": "2025-05-01",
}
r = requests.get("https://api.github.com/copilot_internal/user", headers=headers, timeout=20)
print(json.dumps(r.json(), indent=2))

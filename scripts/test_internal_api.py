import json
from pathlib import Path

import requests

cfg = json.loads(Path("config.json").read_text())
token = cfg["token"]
headers = {
    "Authorization": f"Bearer {token}",
    "Accept": "application/json",
    "X-GitHub-Api-Version": "2025-05-01",
}
r = requests.get("https://api.github.com/copilot_internal/user", headers=headers, timeout=20)
print(json.dumps(r.json(), indent=2))

# Copilot Desktop Monitor

Cross-platform desktop widget (Windows, macOS, Linux) that displays **Usage** and **Monthly Limit** for your GitHub Copilot account.

## Requirements

- Python 3.10+
- GitHub token with billing permissions
- Tkinter (bundled with Python on Windows/macOS; on Linux install `python3-tk`)

## Quick setup

1. Copy `config.example.json` to `config.json` (or run the launcher, which creates it automatically).
2. Edit `config.json`:

```json
{
  "github_username": "your-username",
  "token": "ghp_xxxxxxxx",
  "account_type": "user",
  "plan": "pro",
  "billing_mode": "auto",
  "autostart": {
    "enabled": true
  }
}
```

3. Run the app:

| Platform | Command |
|---|---|
| Windows | `run.bat` |
| macOS / Linux | `chmod +x run.sh && ./run.sh` |

## Autostart

By default, `autostart.enabled` is `true`. When the app starts, it registers autostart according to your system:

| Platform | Mechanism |
|---|---|
| Windows | `HKCU\...\Run` registry |
| macOS | LaunchAgent in `~/Library/LaunchAgents/` |
| Linux | `.desktop` file in `~/.config/autostart/` |

Manual commands:

```bash
# Enable
python src/main.py --install-autostart

# Disable
python src/main.py --uninstall-autostart

# Check status
python src/main.py --autostart-status
```

You can also enable or disable autostart from the system tray menu (**Launch at startup**).

## GitHub token

The billing API **does not work with every token type**. Recommendations:

| Account type | Recommended token |
|---|---|
| Personal Copilot (Pro/Pro+) | Fine-grained PAT with **Account permissions → Plan → Read** |
| Copilot via organization | Classic PAT with billing/admin permissions for the org |

Generate a token at: https://github.com/settings/tokens

## Config fields

| Field | Description |
|---|---|
| `github_username` | Your GitHub username |
| `token` | Personal Access Token |
| `account_type` | `user` or `organization` |
| `organization` | Org slug (only when `account_type` is `organization`) |
| `plan` | `free`, `pro`, `pro+`, `max`, `business`, `enterprise` |
| `billing_mode` | `auto`, `premium_requests`, or `ai_credits` |
| `monthly_limit` | Manual override for the monthly limit (optional) |
| `refresh_interval_seconds` | Refresh interval in seconds (default: 300) |
| `autostart.enabled` | Enable autostart on login (default: true) |
| `thresholds.warning_percent` | Percentage for "Warning" status (default: 75) |
| `thresholds.critical_percent` | Percentage for "Critical" status (default: 90) |

## Detected statuses

| Status | Condition |
|---|---|
| **Normal** | Usage < 75% |
| **Warning** | Usage ≥ 75% |
| **Critical** | Usage ≥ 90% |
| **Limit reached** | Usage ≥ 100% |
| **Error** | Invalid token, missing permissions, or API unavailable |

## Widget usage

- **Drag**: click and drag to move
- **Refresh**: ↻ button or system tray menu
- **Close**: × button or Esc key
- Position is saved automatically in `config.json`

## Building a standalone executable

PyInstaller builds the binary on the platform where you compile (you cannot cross-compile Windows → Linux from a single build).

| Platform | Command | Output |
|---|---|---|
| Windows | `build.bat` | `dist\CopilotMonitor.exe` |
| Linux / WSL | `chmod +x build-linux.sh && ./build-linux.sh` | `dist/CopilotMonitor` |

**Linux build dependencies:**

- Debian/Ubuntu: `sudo apt install python3-tk binutils`
- Fedora: `sudo dnf install python3-tkinter binutils`

After building, copy `config.example.json` to `config.json` inside `dist/` and edit your credentials.

## Platform notes

- **Linux**: if the widget does not appear, install `python3-tk`. Some desktop environments do not support transparency (`opacity`).
- **macOS**: may request accessibility permission the first time you use the system tray.
- **Windows**: autostart uses the current user's registry (no admin required).

## API used

- `GET /users/{username}/settings/billing/premium_request/usage`
- `GET /users/{username}/settings/billing/ai_credit/usage`
- Organization equivalents with `?user=` when applicable

Documentation: https://docs.github.com/en/rest/billing/usage

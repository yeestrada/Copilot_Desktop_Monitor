# Copilot Desktop Monitor

Cross-platform desktop widget (Windows, macOS, Linux) that monitors monthly AI usage for **GitHub Copilot**, **Cursor**, **OpenAI API**, and **SiliconFlow**. Each configured account gets its own floating widget, provider icon, and independent refresh cycle.

## Features

- One widget per account (mix Copilot, Cursor, OpenAI, and SiliconFlow in the same app)
- Unified layout across providers: usage %, used amount, monthly limit, progress bar, and detail panel
- Provider icons in the widget header (GitHub Copilot / Cursor / OpenAI / SiliconFlow)
- System tray with per-account refresh, autostart toggle, and quit
- Draggable widgets with saved position per account
- Optional login autostart (Windows, macOS, Linux)

## Requirements

- Python 3.10+
- Tkinter (bundled with Python on Windows/macOS; on Linux install `python3-tk`)
- Python packages: `requests`, `pystray`, `Pillow` (see `requirements.txt`)

## Quick start

### 1. Install dependencies

```bash
python -m venv .venv

# Windows
.venv\Scripts\pip install -r requirements.txt

# macOS / Linux
.venv/bin/pip install -r requirements.txt
```

If the virtual environment is broken on Windows, you can run with the system interpreter:

```bash
py -3 -m pip install -r requirements.txt
py -3 src/main.py
```

### 2. Create configuration

Copy the example config and edit your credentials:

```bash
cp config.example.json config.json
```

> **Security:** `config.json` contains secrets (tokens). It is listed in `.gitignore` and must never be committed.

The launcher scripts (`run.bat` / `run.sh`) create `config.json` from `config.example.json` automatically if it does not exist.

### 3. Configure accounts

Edit `config.json` and add one or more entries under `accounts[]`:

```json
{
  "refresh_interval_seconds": 300,
  "autostart": { "enabled": true },
  "accounts": [
    {
      "id": "copilot-personal",
      "label": "Copilot Personal",
      "provider": "github_copilot",
      "enabled": true,
      "github_username": "your-username",
      "token": "ghp_xxxxxxxx",
      "account_type": "user",
      "plan": "enterprise",
      "widget": { "position": { "x": 50, "y": 50 } }
    },
    {
      "id": "cursor-main",
      "label": "Cursor",
      "provider": "cursor",
      "enabled": true,
      "session_token": "your_cursor_session_token",
      "cursor_auth_mode": "auto",
      "widget": { "position": { "x": 50, "y": 220 } }
    },
    {
      "id": "openai-main",
      "label": "OpenAI API",
      "provider": "openai",
      "enabled": true,
      "session_token": "sess-your_openai_dashboard_session_token",
      "widget": { "position": { "x": 50, "y": 350 } }
    },
    {
      "id": "siliconflow-main",
      "label": "SiliconFlow",
      "provider": "siliconflow",
      "enabled": true,
      "session_token": "paste_full_Cookie_header_here",
      "organization": "your_x_subject_id",
      "widget": { "position": { "x": 50, "y": 480 } }
    }
  ]
}
```

### 4. Run the app

| Platform | Command |
|---|---|
| Windows | `run.bat` or `py -3 src\main.py` |
| macOS / Linux | `chmod +x run.sh && ./run.sh` |

Only one app instance runs at a time. If nothing appears, check the system tray — a previous instance may still be running (tray → **Quit**, then start again).

> **Legacy config:** flat fields (`github_username`, `token`, etc. at the root) are migrated automatically to `accounts[]` on startup.

---

## Configuration reference

### Global fields

| Field | Default | Description |
|---|---|---|
| `refresh_interval_seconds` | `300` | How often all widgets refresh (minimum 30 seconds) |
| `thresholds.warning_percent` | `75` | Usage % for **Warning** status |
| `thresholds.critical_percent` | `90` | Usage % for **Critical** status |
| `widget.always_on_top` | `true` | Default always-on-top for new account widgets |
| `widget.opacity` | `0.92` | Default opacity (`1.0` = fully opaque) |
| `autostart.enabled` | `true` | Register the app to start at login |

### Per-account fields (all providers)

| Field | Required | Description |
|---|---|---|
| `id` | Yes | Unique account identifier (used to save widget position) |
| `label` | Yes | Widget title (e.g. `Copilot Personal`, `Cursor`) |
| `provider` | Yes | `github_copilot`, `cursor`, `openai`, or `siliconflow` |
| `enabled` | No | `false` keeps the account in config but hides its widget |
| `thresholds` | No | Per-account override of global warning/critical % |
| `widget.enabled` | No | `false` hides only this account's widget |
| `widget.always_on_top` | No | Override global always-on-top |
| `widget.opacity` | No | Override global opacity |
| `widget.position.x` / `y` | No | Initial screen position (updated when you drag the widget) |

---

## GitHub Copilot setup

### Token

Generate a Personal Access Token at https://github.com/settings/tokens

| Account type | Recommended token |
|---|---|
| Personal Copilot (Pro / Pro+) | Fine-grained PAT with **Account permissions → Plan → Read** |
| Copilot via organization | Classic PAT with billing/admin permissions for the org |

The app tries the internal Copilot API first (`data_source: auto`), then falls back to the GitHub billing API.

### Account fields

| Field | Description |
|---|---|
| `github_username` | Your GitHub login (shown as `@username` in the widget) |
| `token` | Personal Access Token |
| `account_type` | `user`, `organization`, or `enterprise` |
| `organization` | Organization slug when `account_type` is `organization` |
| `enterprise` | Enterprise slug when `account_type` is `enterprise` |
| `plan` | `free`, `pro`, `pro+`, `max`, `business`, `enterprise` — used as fallback monthly limit |
| `billing_mode` | `auto`, `premium_requests`, or `ai_credits` |
| `data_source` | `auto` (recommended), `copilot_internal`, or `billing_api` |
| `monthly_limit` | Manual override for the monthly limit when the API does not return one |

### What the widget shows (Copilot)

| Area | Content |
|---|---|
| Header | Copilot icon, account label, `@github_username` (from config), status badge |
| Left panel | **Usage** (%), **Used** (premium requests or credits), **Monthly Limit**, progress bar |
| Right panel | Remaining, plan, organization (from config `organization`), billing-cycle reset date |

---

## Cursor setup

### Personal accounts (Pro, etc.)

**User API Keys do not expose personal usage quota.** For personal accounts you need a **session token** from the browser.

1. Log in at [cursor.com](https://cursor.com)
2. Open DevTools → **Application** → **Cookies** → `cursor.com`
3. Copy the value of `WorkosCursorSessionToken`
4. Paste it into `session_token` in `config.json`

The token may be URL-encoded (`%3A%3A` instead of `::`). Paste it as copied — the app decodes it automatically.

Tokens expire. If the widget shows **Error** / `401`, copy a fresh cookie value.

### Team admin API (optional)

For team spend tracking via the official Admin API:

| Field | Description |
|---|---|
| `cursor_auth_mode` | Set to `admin_api` |
| `api_key` | Admin API key from Cursor Dashboard → API |
| `cursor_email` | Team member email to look up in spend data |

### Cursor account fields

| Field | Description |
|---|---|
| `session_token` | `WorkosCursorSessionToken` cookie — **required for personal usage** |
| `cursor_auth_mode` | `auto` (default), `session`, or `admin_api` |
| `api_key` | Admin API key (team mode only; not for personal quota) |
| `cursor_email` | Account email for display (`@` handle) and `admin_api`; recommended for personal accounts |
| `api_base_url` | Default `https://api2.cursor.sh` (legacy; personal usage uses `cursor.com`) |
| `admin_api_base_url` | Default `https://api.cursor.com` |

### Auth modes

| Mode | Behavior |
|---|---|
| `auto` | Uses `session_token` via `cursor.com` (recommended for personal accounts) |
| `session` | Same as `auto`, but fails if the session token is invalid (no admin fallback) |
| `admin_api` | Team spend API only; requires `api_key` + `cursor_email` |

### Username in the widget

For Cursor, set `cursor_email` in config to control the subtitle (`@username` uses the email local-part, e.g. `you@example.com` → `@you`). If `cursor_email` is empty, the app fetches the account from `GET /api/auth/me` using the session token.

### Cursor usage metrics (important)

Cursor exposes two different numbers in the same API response:

| Metric | Example | Meaning |
|---|---|---|
| `totalPercentUsed` | ~7% | **Dashboard percentage** — what Cursor shows as total usage |
| `used` / `limit` (raw) | 1,300 / 2,000 | Event counter — **not** the same as the dashboard % |

The widget aligns all displayed values with the dashboard percentage:

- **Usage** → `totalPercentUsed`
- **Used** → `limit × totalPercentUsed / 100`
- **Monthly Limit** → plan `limit` from the API
- **Remaining** → `limit − used` (derived)
- **Mix** (detail line) → Auto % / API % breakdown

The progress bar and status thresholds are based on the dashboard %, not the raw event ratio.

### What the widget shows (Cursor)

| Area | Content |
|---|---|
| Header | Cursor icon, account label, `@username`, status badge |
| Left panel | **Usage** (%), **Used**, **Monthly Limit**, progress bar |
| Right panel | Remaining, plan, Auto/API mix %, billing-cycle reset date |

---

## OpenAI API setup

This monitors **OpenAI Platform API credit balance** (prepaid credits), not ChatGPT Plus/Team message limits.

### Browser session token (required)

OpenAI does **not** expose remaining credits through normal `sk-...` / `sk-proj-...` API keys. You need the **dashboard session key** that the website uses for billing.

#### Where to get `session_token`

1. Log in at [platform.openai.com](https://platform.openai.com)
2. Open DevTools (`F12`) → **Network**
3. Enable **Fetch/XHR** (or clear the list and keep DevTools open)
4. Open Billing overview (or reload it):  
   https://platform.openai.com/settings/organization/billing/overview
5. In Network, find this request:

   `GET https://api.openai.com/v1/dashboard/billing/credit_grants`

   (Status should be **200**)
6. Open **Headers** → **Request Headers** → **Authorization**
7. Copy the value after `Bearer ` — it looks like:

   `sess-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`

   **Use this `sess-...` token.** Do **not** use:
   - a JWT starting with `eyJ...` (login/auth token — different)
   - an API key starting with `sk-` / `sk-proj-`
   - Cloudflare cookies (`cf_clearance`, `__cf_bm`, etc.)
8. Paste **only** the `sess-...` value into `session_token` in `config.json` (without the word `Bearer`)

Example:

```json
{
  "id": "openai-main",
  "label": "OpenAI API",
  "provider": "openai",
  "enabled": true,
  "session_token": "sess-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "widget": { "position": { "x": 50, "y": 350 } }
}
```

`sess-` tokens expire. If the widget shows **Error**, repeat the steps and paste a fresh value from `credit_grants`.

### Account fields

| Field | Description |
|---|---|
| `session_token` | Dashboard session key from `Authorization: Bearer sess-...` on `GET /v1/dashboard/billing/credit_grants` — **required** |

### What the widget shows (OpenAI)

| Area | Content |
|---|---|
| Left panel | Usage %, Used credits, **Credit Limit** (total granted) |
| Right panel | Remaining balance (`total_available`), plan, “Credit balance”, grant expiry |

If remaining is **$0**, usage shows **100%** / **Limit reached**.

---

## SiliconFlow setup

This monitors your **SiliconFlow wallet balance** (used / recharged / remaining credits in USD).

The old public endpoint `GET /v1/user/info` was retired (**HTTP 410**, Aug 2026). The monitor uses the **same wallet API as the billing UI**:

`GET https://cloud.siliconflow.com/walletd-server/api/v1/subject/profile/peek`

### Where to get the credentials (required)

You need two values from the browser Network tab — **not** an API key (`sk-...`).

| Config field | Copy from (Request Headers of `profile/peek`) |
|---|---|
| `session_token` | Full **`Cookie`** header |
| `organization` | **`x-subject-id`** header |

#### Step by step

1. Log in at [cloud.siliconflow.com](https://cloud.siliconflow.com)
2. Open billing: [Expense / billing](https://cloud.siliconflow.com/me/expensebill)  
   (or any page that loads the wallet; refresh if needed)
3. Open DevTools → **Network** → filter **Fetch/XHR** (Chrome) or **XHR** (Firefox)
4. Reload the page and find the request named **`peek`** (or filter by `profile/peek`):

   `GET https://cloud.siliconflow.com/walletd-server/api/v1/subject/profile/peek`

   - Status should be **200**
   - Response JSON includes `data.financialInfo` (`available`, `used`, `recharged`, …)
5. Open that request → **Headers** / **Request Headers**:
   - Copy the entire **`Cookie:`** value → `session_token` in `config.json`
   - Copy **`x-subject-id:`** → `organization` in `config.json`
6. Set `"enabled": true`, save `config.json`, and refresh the widget

#### Tips (avoid truncated Cookie)

- **Firefox:** the Cookie preview often ends with `…`. That truncated value is **invalid** (causes `latin-1` / encode errors). Click the Cookie row and copy the **full** value, or use **Raw** headers / **Storage → Cookies** and rebuild `name=value; name2=value2`.
- **Chrome:** use **Headers → Request Headers → Cookie** → right‑click → Copy value.
- Do **not** paste JSON with collapsed `{…}` placeholders from DevTools previews.
- Cookies expire. On **Error** / 401 / 403, copy a fresh `Cookie` + `x-subject-id` from `profile/peek` again.

Example:

```json
{
  "id": "siliconflow-main",
  "label": "SiliconFlow",
  "provider": "siliconflow",
  "enabled": true,
  "session_token": "paste_full_Cookie_header_here",
  "organization": "your_x_subject_id",
  "widget": { "position": { "x": 50, "y": 500 } }
}
```

**Note:** Amounts in the API JSON are scaled by `1e12` (e.g. `"5000000000000"` → `$5.00`). The app converts them automatically.

### Account fields

| Field | Description |
|---|---|
| `session_token` | Full browser **`Cookie`** header from Network → `profile/peek` — **required** |
| `organization` | **`x-subject-id`** request header from the same call — **required** |
| `plan` | Optional display label (defaults to `wallet`) |

### What the widget shows (SiliconFlow)

| Area | Content |
|---|---|
| Left panel | Usage % (`used / recharged`), Used, **Credit Limit** (`recharged`) |
| Right panel | Remaining (`available`), plan, recharge summary |

---

## Using the application

### Widget controls

| Action | How |
|---|---|
| Move widget | Click and drag anywhere on the widget |
| Refresh one account | Click **↻** on that widget |
| Close one widget | Click **×** or press **Esc** (app stays in tray if other widgets are open) |
| Refresh all | System tray → account name or **Refresh all** |
| Toggle autostart | System tray → **Launch at startup** |
| Quit completely | System tray → **Quit** |

Widget positions are saved per account in `config.json` when you release the mouse after dragging.

### System tray

The tray icon shows a summary tooltip with all open accounts. Right-click for:

- Per-account refresh
- **Refresh all**
- **Launch at startup** toggle
- **Quit**

### Status badges

| Status | Condition |
|---|---|
| **Normal** | Usage &lt; warning threshold (default 75%) |
| **Warning** | Usage ≥ warning threshold |
| **Critical** | Usage ≥ critical threshold (default 90%) |
| **Limit reached** | Usage ≥ 100% |
| **Error** | Invalid token, expired session, missing permissions, or API failure |

---

## Autostart

When `autostart.enabled` is `true`, the app registers itself at login:

| Platform | Mechanism |
|---|---|
| Windows | `HKCU\...\Run` registry entry |
| macOS | LaunchAgent in `~/Library/LaunchAgents/` |
| Linux | `.desktop` file in `~/.config/autostart/` |

CLI commands:

```bash
python src/main.py --install-autostart
python src/main.py --uninstall-autostart
python src/main.py --autostart-status
```

---

## Building a standalone executable

PyInstaller builds on the **target platform only** (no cross-compilation).

| Platform | Command | Output |
|---|---|---|
| Windows | `build.bat` | `dist\CopilotMonitor.exe` |
| Linux / WSL | `chmod +x build-linux.sh && ./build-linux.sh` | `dist/CopilotMonitor` |

**Linux build dependencies:**

- Debian/Ubuntu: `sudo apt install python3-tk binutils`
- Fedora: `sudo dnf install python3-tkinter binutils`

After building:

1. Copy `config.example.json` to `dist/config.json`
2. Edit credentials in `dist/config.json`
3. Run the executable from `dist/`

Icons in `assets/icons/` are bundled automatically for the header and PyInstaller build.

---

## Platform notes

- **Linux:** install `python3-tk` if the window does not appear. Some desktops do not support window transparency (`opacity`).
- **macOS:** accessibility permission may be requested the first time you use the system tray.
- **Windows:** autostart uses the current user's registry (no administrator rights required).
- **Single instance:** starting the app twice exits silently; use the tray to manage the running instance.

---

## APIs used

### GitHub Copilot

- `GET /copilot_internal/user` — personal premium-interaction quota (preferred)
- `GET /users/{username}/settings/billing/premium_request/usage`
- `GET /users/{username}/settings/billing/ai_credit/usage`
- Organization / enterprise billing endpoints when configured

Docs: https://docs.github.com/en/rest/billing/usage

### Cursor

- `GET https://cursor.com/api/usage-summary` — plan quota (session cookie)
- `GET https://cursor.com/api/auth/me` — account email / display name (session cookie)
- `POST https://api.cursor.com/teams/spend` — team admin spend (admin API key)

Docs: https://cursor.com/docs/account/teams/admin-api

### OpenAI

- `GET https://api.openai.com/v1/dashboard/billing/credit_grants` — credit balance (`Authorization: Bearer sess-...` from the platform dashboard)

### SiliconFlow

- `GET https://cloud.siliconflow.com/walletd-server/api/v1/subject/profile/peek` — wallet `financialInfo` (browser Cookie + `x-subject-id`)

---

## Troubleshooting

| Problem | Solution |
|---|---|
| Widget shows **Error** (Cursor) | Refresh `session_token` from browser cookies |
| Widget shows **Error** (Copilot) | Regenerate token; ensure `copilot` / Plan read scope |
| Widget shows **Error** (OpenAI) | Refresh `session_token` from Network → `credit_grants` → `Authorization: Bearer sess-...` |
| Widget shows **Error** (SiliconFlow) | Refresh `session_token` (Cookie) and `organization` (`x-subject-id`) from Network → `profile/peek`. If the message mentions `latin-1` or `…`, the Cookie was truncated — copy the full header. |
| App starts but no window | Another instance may be running — check system tray |
| `run.bat` fails on Windows | Use `py -3 src\main.py` or recreate `.venv` with `py -3 -m venv .venv` |
| Cursor % and counts look odd | The app uses dashboard % as source of truth; raw event counts from the API are not shown directly |

---

## Configuration-only data

All account-specific values (usernames, tokens, organizations, emails, widget titles, positions) must live in `config.json`. The repository ships only generic placeholders in `config.example.json`.

| Rule | Detail |
|---|---|
| `config.json` | Gitignored — never commit real credentials |
| Source code / scripts | No hardcoded usernames, orgs, tokens, or emails |
| Test scripts | Read credentials via `scripts/_config_loader.py` |
| Widget labels | `organization`, `github_username`, `cursor_email`, and `label` come from config |
| OpenAI credits | `session_token` = `sess-...` from `GET /v1/dashboard/billing/credit_grants` (not `eyJ...` / `sk-...`) |
| SiliconFlow wallet | `session_token` = full **Cookie** + `organization` = **`x-subject-id`** from Network → `profile/peek` on [cloud.siliconflow.com/me/expensebill](https://cloud.siliconflow.com/me/expensebill) |
| API responses | Used for usage metrics only; display identity prefers config when set |

---

## Project layout

```
Copilot_Desktop_Monitor/
├── assets/icons/          # Provider icons (Copilot, Cursor, OpenAI, SiliconFlow)
├── config.example.json    # Template configuration
├── config.json            # Your local config (gitignored)
├── src/
│   ├── main.py            # App entry, tray, multi-widget loop
│   ├── widget.py          # Floating account widget UI
│   ├── config.py          # Config loading and validation
│   ├── github_api.py      # GitHub Copilot usage client
│   ├── cursor_api.py      # Cursor usage client
│   ├── openai_api.py      # OpenAI credit balance client (sess- token)
│   ├── siliconflow_api.py # SiliconFlow wallet client (Cookie + x-subject-id)
│   └── provider_icons.py  # Header icon loader
├── run.bat / run.sh       # Launch scripts
└── requirements.txt
```

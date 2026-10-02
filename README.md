# Antigravity (AGY) Quota — Desktop Widget

A real-time desktop widget to monitor your **AGY CLI (Antigravity)** token quota on Ubuntu/GNOME.

Data comes from the same source as `agy --print /usage`, rendered as a compact always-on-top panel showing each quota group and bucket with a colored progress bar and a live reset countdown.

![icon](icon.png)

## Features

- **Live quota bars** per group (e.g. *Gemini Models*, *Claude and GPT models*) and per bucket (*Weekly Limit*, *5-Hour Limit*)
- **Color coding**: green ≥75%, yellow ≥50%, orange ≥25%, red <25% / `EXHAUSTED`; a 5h bucket at 0% while weekly is exhausted shows `NONAKTIF` (disabled, gray)
- **Reset countdown** computed locally from `reset_time` and updated every second
- **Account email** resolved from the real AGY OAuth token (via Google `tokeninfo`, read-only, cached 6h)
- **Resizable** — drag the bottom-right grip; content scrolls vertically
- **Minimize to tray** — hides the window; restore from the tray menu (AppIndicator3)
- **Desktop widget** — always on top, sticky across workspaces, skips the taskbar
- Native GTK3 (no Electron) — light on older GPUs

## Requirements

- Ubuntu (or any GNOME desktop) with GTK3 and XWayland
- Python 3 with `PyGObject` (`gi`)
- The `agy` CLI installed and logged in

Install dependencies on Ubuntu:

```bash
sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-appindicator3-0.1
```

> The tray icon uses `AppIndicator3`. On stock GNOME also enable the **Ubuntu AppIndicators** extension, otherwise minimize falls back to showing in the taskbar.

## Install

Clone the repo anywhere you like:

```bash
git clone https://github.com/titodoni/antigravity-widget.git
cd antigravity-widget
```

Make sure `agy` is logged in once:

```bash
agy
# follow the login flow
```

Verify everything works:

```bash
python3 selftest.py
```

## Usage

Run the widget:

```bash
./run.sh
```

Or launch it from the app grid. To start it automatically on login, copy the desktop entry and point it at this clone:

```bash
mkdir -p ~/.config/autostart
cp antigravity-usage-widget.desktop ~/.config/autostart/
# replace both /path/to/antigravity-widget occurrences with the real path
```

### Controls

| Action | How |
|---|---|
| Move | Drag any empty area of the widget |
| Resize | Drag the bottom-right grip |
| Refresh now | ⟳ button |
| Open web dashboard | 🌐 button (localhost:5111) |
| Minimize to tray | — button (restore via tray menu) |
| Quit | ✕ button |

## Data sources (in order)

1. Local dashboard `http://127.0.0.1:5111/api/quota` (from [agy-token-monitor]) — fast, cached 15s
2. Direct `agy --print /usage --output-format json` — fallback if the dashboard is down

Both read `command.data.groups`.

### Email comes from the token, not a stale file

The dashboard's `account` field is read from `~/.gemini/google_accounts.json`, which can go stale on re-login. The widget instead shows the email from the actual AGY OAuth token, verified against `https://oauth2.googleapis.com/tokeninfo` (read-only). The token itself is never printed or sent anywhere except that Google endpoint.

## Configuration (environment variables)

| Var | Default | Description |
|---|---|---|
| `AGW_REFRESH` | `60` | Auto-refresh interval (seconds, min 15) |
| `AGW_WIDTH` | `330` | Widget width in logical pixels |
| `AGW_DASH_URL` | `http://127.0.0.1:5111/api/quota` | Dashboard endpoint |

Example:

```bash
AGW_REFRESH=30 ./run.sh
```

Window size is persisted in `~/.local/share/antigravity-widget/geometry.json`, and the resolved account email is cached in `~/.local/share/antigravity-widget/account.json` (6h TTL).

## Why GTK3 on X11?

Wayland-native GTK does not support the `keep_above` / `skip_taskbar` / `sticky` window hints this widget needs. GTK3 on XWayland supports them and stays lightweight.

## Self-test

```bash
python3 selftest.py
```

Exercises parsing, normalization, UI rendering, and live data. Token/email checks are skipped automatically when `agy` is not logged in.

## Components

| File | Purpose |
|---|---|
| `run.sh` | Launcher (forces X11 backend) |
| `widget.py` | The GTK3 widget |
| `selftest.py` | 30+ self-tests (parsing, normalization, rendering) |
| `icon.png` | Widget / tray icon |

## Compatibility note

This widget reads quota data directly from the AGY CLI. It uses a separate Google login session and is not compatible with the `antigravity-usage` npm package's session.

## License

Provided as-is for personal use.

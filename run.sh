#!/usr/bin/env bash
# Launcher widget Antigravity (AGY) Quota.
# Backend X11 (XWayland) dipakai karena Wayland-native tidak dukung hint
# keep_above / skip_taskbar yang dibutuhkan untuk widget desktop.
export GDK_BACKEND=x11
exec python3 "$(dirname "$(readlink -f "$0")")/widget.py" "$@"

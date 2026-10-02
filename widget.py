#!/usr/bin/env python3
"""
Antigravity (AGY) Quota — desktop widget (GTK3, X11 via XWayland).

Menampilkan quota AGY CLI secara realtime di desktop Ubuntu.
Sumber data (berurutan):
  1. Dashboard lokal http://127.0.0.1:5111/api/quota  (jika menyala, sudah ter-cache 15s)
  2. Langsung: `agy --print /usage --output-format json`

Catatan: GTK3 + type_hint UTILITY + keep_above + skip_taskbar = widget desktop
yang selalu di atas tanpa memenuhi taskbar. Backend X11 dipakai karena Wayland
native tidak mendukung hint keep_above.

Jendela bisa di-resize (seret grip kanan-bawah; isi scroll vertikal) dan bisa
diminimize: tombol _ mem-iconify jendela, tray icon (StatusIcon) mengembalikan.
Ukuran disimpan di ~/.local/share/antigravity-widget/geometry.json
"""

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

try:  # tray native Ubuntu (Ayatana). Fallback Gtk.StatusIcon jika absen.
    gi.require_version("AppIndicator3", "0.1")
    from gi.repository import AppIndicator3  # noqa: E402
except Exception:  # noqa: BLE001
    AppIndicator3 = None

APP_ID = "id.tito.AntigravityQuotaWidget"
APP_TITLE = "Antigravity Quota"
AGY_BIN = os.path.expanduser("~/.local/bin/agy")
DASH_URL = os.environ.get("AGW_DASH_URL", "http://127.0.0.1:5111/api/quota")
TOKEN_FILE = os.path.expanduser(
    os.environ.get("AGW_TOKEN_FILE", "~/.gemini/antigravity-cli/antigravity-oauth-token")
)
TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"
ACCOUNT_CACHE = os.path.expanduser("~/.local/share/antigravity-widget/account.json")
ACCOUNT_TTL = 6 * 3600  # detik
HERE = os.path.dirname(os.path.abspath(__file__))
REFRESH_INTERVAL = int(os.environ.get("AGW_REFRESH", "60"))  # detik
WIDTH = int(os.environ.get("AGW_WIDTH", "330"))
MIN_WIDTH = 240
MIN_HEIGHT = 150
DATA_DIR = os.path.expanduser("~/.local/share/antigravity-widget")
GEOM_FILE = os.path.join(DATA_DIR, "geometry.json")
ICON_PATH = os.path.join(HERE, "icon.png")

CSS = b"""
window {
  background-color: rgba(24, 26, 30, 0.93);
  border-radius: 14px;
  border: 1px solid rgba(255,255,255,0.10);
}
.header { padding: 10px 12px 4px 12px; }
.title { font-size: 13px; font-weight: 700; color: #e8eaed; }
.subtitle { font-size: 11px; color: #9aa0a6; }
.body { padding: 2px 12px 8px 12px; }
.group-name { font-size: 11px; font-weight: 700; color: #a8c7fa; }
.group-desc { font-size: 10px; color: #7a8087; }
.bucket-label { font-size: 12px; font-weight: 600; color: #dfe3e8; }
.bucket-pct { font-size: 12px; font-weight: 700; }
.reset-text { font-size: 10px; color: #8b9097; }
.footer { font-size: 10px; color: #7a8087; padding: 6px 12px 10px 12px; }
button.flat-small {
  min-width: 24px; min-height: 24px; padding: 2px 4px;
  border-radius: 8px; color: #c8ccd2;
  background: transparent; border-color: transparent;
  box-shadow: none; -gtk-icon-shadow: none;
}
button.flat-small:hover { background: rgba(255,255,255,0.12); }
progressbar { min-height: 6px; }
progressbar trough {
  min-height: 6px; border-radius: 4px;
  background-color: rgba(255,255,255,0.12);
}
progressbar > trough > progress { border-radius: 4px; }
progressbar.lvl-good  > trough > progress { background-color: #2fbf71; }
progressbar.lvl-mid   > trough > progress { background-color: #e3b341; }
progressbar.lvl-low   > trough > progress { background-color: #e0862d; }
progressbar.lvl-crit  > trough > progress { background-color: #e5484d; }
progressbar.lvl-off   > trough > progress { background-color: #5f6368; }
.err-box {
  background: rgba(229,72,77,0.12);
  border-radius: 10px; padding: 10px; margin-top: 6px;
}
.err-text { color: #f0a4a7; font-size: 12px; }
.size-grip {
  min-width: 18px; min-height: 16px;
  border-radius: 0 0 12px 0;
  background-color: rgba(255,255,255,0.05);
  border-right: 1px solid rgba(255,255,255,0.12);
  border-bottom: 1px solid rgba(255,255,255,0.12);
}
.size-grip:hover { background-color: rgba(255,255,255,0.14); }
.hint-text { color: #9aa0a6; font-size: 11px; }
.badge {
  font-size: 10px; background: rgba(47,191,113,0.16); color: #7fe0ad;
  border-radius: 6px; padding: 1px 6px;
}
.badge.dash { background: rgba(138,180,248,0.18); color: #a8c7fa; }
.pct-good { color: #2fbf71; }
.pct-mid  { color: #e3b341; }
.pct-low  { color: #e0862d; }
.pct-crit { color: #e5484d; }
.pct-off  { color: #8b9097; }
spinner { color: #8ab4f8; }
"""


def _extract_access_token(path: str) -> str | None:
    """Ambil access_token dari blob JSON token agy (recursive, tak dicetak)."""
    try:
        with open(path) as f:
            d = json.load(f)
    except Exception:
        return None

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in ("access_token", "id_token", "token") and isinstance(v, str) and len(v) > 40:
                    return v
                r = walk(v)
                if r:
                    return r
        elif isinstance(o, list):
            for v in o:
                r = walk(v)
                if r:
                    return r
        return None

    return walk(d)


def resolve_account_email() -> str | None:
    """Email dari token OAuth agy sebenarnya via tokeninfo Google (cache 6 jam).

    Field 'account' dashboard bisa usang (google_accounts.json), jadi ini
    jadi sumber utama. Gagal -> None (pemanggil fallback ke field dashboard).
    """
    try:
        with open(ACCOUNT_CACHE) as f:
            c = json.load(f)
        if time.time() - c.get("ts", 0) < ACCOUNT_TTL and c.get("email"):
            return c["email"]
    except Exception:
        pass

    tok = _extract_access_token(TOKEN_FILE)
    if not tok:
        return None
    try:
        url = TOKENINFO_URL + "?" + urllib.parse.urlencode({"access_token": tok})
        with urllib.request.urlopen(url, timeout=8) as r:
            info = json.loads(r.read().decode("utf-8"))
        email = info.get("email")
        if email:
            os.makedirs(os.path.dirname(ACCOUNT_CACHE), exist_ok=True)
            try:
                with open(ACCOUNT_CACHE, "w") as f:
                    json.dump({"email": email, "ts": time.time()}, f)
            except Exception:
                pass
        return email or None
    except Exception:
        return None


class FetchError(Exception):
    def __init__(self, message: str, not_logged_in: bool = False):
        super().__init__(message)
        self.message = message
        self.not_logged_in = not_logged_in


def find_agy() -> str | None:
    if os.path.exists(AGY_BIN) and os.access(AGY_BIN, os.X_OK):
        return AGY_BIN
    for p in os.environ.get("PATH", "").split(os.pathsep):
        f = os.path.join(p, "agy")
        if os.path.exists(f) and os.access(f, os.X_OK):
            return f
    return None


def _norm_bucket(b: dict) -> dict:
    frac = b.get("remaining_fraction")
    if frac is None and b.get("remaining_pct") is not None:
        frac = float(b.get("remaining_pct", 0)) / 100.0
    if frac is None:
        frac = 0.0
    frac = max(0.0, min(float(frac), 1.0))
    return {
        "id": b.get("id", ""),
        "name": b.get("name", "Limit"),
        "window": b.get("window", ""),
        "remaining_fraction": frac,
        "remaining_pct": round(frac * 100, 2),
        "reset_time": b.get("reset_time", "") or "",
        "description": b.get("description", "") or "",
    }


def _norm(data: dict, source: str) -> dict:
    groups = []
    for g in data.get("groups", []):
        groups.append(
            {
                "name": g.get("name", ""),
                "description": g.get("description", ""),
                "buckets": [_norm_bucket(b) for b in g.get("buckets", [])],
            }
        )
    return {
        "status": "ok",
        "account": data.get("account"),
        "source": source,
        "fetched_at": data.get("fetched_at"),
        "groups": groups,
    }


def fetch_from_dashboard() -> dict | None:
    try:
        with urllib.request.urlopen(DASH_URL, timeout=6) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("status") != "ok" or not data.get("groups"):
        return None
    return _norm(data, source="dashboard")


def fetch_from_agy(agy_path: str) -> dict:
    try:
        proc = subprocess.run(
            [agy_path, "--print", "/usage", "--output-format", "json"],
            capture_output=True,
            text=True,
            timeout=25,
        )
    except subprocess.TimeoutExpired:
        raise FetchError("agy tidak merespons dalam 25 detik.")
    except FileNotFoundError:
        raise FetchError("Binary agy tidak ditemukan.")

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if not out:
        raise FetchError(
            err or "agy keluar tanpa output (kode %d)." % proc.returncode,
            not_logged_in=proc.returncode != 0,
        )
    try:
        parsed = json.loads(out)
    except json.JSONDecodeError:
        raise FetchError("Output agy bukan JSON valid.")

    if parsed.get("status") != "SUCCESS":
        msg = str(parsed.get("response") or parsed.get("status") or "gagal")
        raise FetchError(msg, not_logged_in=True)

    data = parsed.get("command", {}).get("data", {})
    groups = data.get("groups", [])
    if not groups:
        raise FetchError("Data groups kosong — kemungkinan belum login.")

    plain = parsed.get("response") or ""
    return _norm(
        {
            "account": parsed.get("account"),
            "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "groups": groups,
        },
        source="agy",
    ), plain


def fetch_quota(agy_path: str | None) -> dict:
    """Dashboard dulu (cepat + ter-cache), fallback panggil agy langsung."""
    dash = fetch_from_dashboard()
    if dash is not None:
        return dash
    if agy_path is None:
        raise FetchError(
            "Dashboard agy-token-monitor tidak menyala dan binary agy tidak ditemukan.",
            not_logged_in=True,
        )
    res = fetch_from_agy(agy_path)
    if isinstance(res, tuple):
        res = res[0]
    return res


def parse_reset(reset_time: str) -> float:
    """ISO8601 (Z) -> epoch detik lokal."""
    if not reset_time:
        return 0.0
    try:
        dt = datetime.fromisoformat(reset_time.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return 0.0


def fmt_reset(reset_at: float) -> str:
    left = reset_at - time.time()
    if left <= 0:
        return "sudah reset"
    d = int(left // 86400)
    h = int((left % 86400) // 3600)
    m = int((left % 3600) // 60)
    s = int(left % 60)
    if d > 0:
        return f"reset {d} hari {h} jam"
    if h > 0:
        return f"reset {h} jam {m} mnt"
    if m > 0:
        return f"reset {m} mnt {s} dtk"
    return f"reset {s} dtk"


def pct_class(pct: float) -> str:
    if pct >= 75:
        return "good"
    if pct >= 50:
        return "mid"
    if pct >= 25:
        return "low"
    return "crit"


class BucketRow(Gtk.Box):
    """Satu bucket quota: nama + persen + bar + countdown."""

    def __init__(self, bucket: dict):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        self.set_margin_top(7)

        self.disabled = bucket.get("window") == "5h" and bucket["remaining_pct"] == 0
        pct = bucket["remaining_pct"]

        self.reset_at = parse_reset(bucket.get("reset_time", ""))

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        top.set_hexpand(True)

        name = bucket.get("name", "Limit")
        win = bucket.get("window", "")
        lbl_text = name
        if win and win not in name.lower():
            lbl_text = f"{name} ({win})"
        lbl = Gtk.Label(label=lbl_text)
        lbl.get_style_context().add_class("bucket-label")
        lbl.set_halign(Gtk.Align.START)
        lbl.set_hexpand(True)
        top.add(lbl)

        if self.disabled:
            pct_txt, cls = "NONAKTIF", "off"
        elif pct <= 0:
            pct_txt, cls = "EXHAUSTED", "crit"
        else:
            pct_txt, cls = f"{pct:g}%", pct_class(pct)
        self.pct_label = Gtk.Label(label=pct_txt)
        self.pct_label.get_style_context().add_class("bucket-pct")
        self.pct_label.get_style_context().add_class("pct-" + cls)
        self.pct_label.set_halign(Gtk.Align.END)
        top.add(self.pct_label)
        self.add(top)

        self.bar = Gtk.ProgressBar()
        self.bar.set_hexpand(True)
        lvl = "off" if self.disabled else pct_class(pct)
        self.bar.set_fraction(pct / 100.0)
        self.bar.get_style_context().add_class("lvl-" + lvl)
        self.add(self.bar)

        self.reset_label = Gtk.Label(label="")
        self.reset_label.get_style_context().add_class("reset-text")
        self.reset_label.set_halign(Gtk.Align.START)
        self.add(self.reset_label)

        self.tick()

    def tick(self):
        self.reset_label.set_text(fmt_reset(self.reset_at) if self.reset_at else "")


class GroupLabel(Gtk.Box):
    def __init__(self, group: dict):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        self.set_margin_top(12)
        n = Gtk.Label(label=group.get("name", ""))
        n.get_style_context().add_class("group-name")
        n.set_halign(Gtk.Align.START)
        self.add(n)
        desc = group.get("description", "")
        if desc:
            d = Gtk.Label(label=desc)
            d.get_style_context().add_class("group-desc")
            d.set_halign(Gtk.Align.START)
            self.add(d)


class Widget(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)
        self.agy = find_agy()
        self.window = None
        self.fetching = False
        self.iconified = False
        self.tray = None
        self.indicator = None
        self.tray_ok = False
        self.tray_polls = 0
        self._geom_dirty = False
        self._geom_cache = None
        self._resizing = False
        self._resize_start = None
        self._user_sized = False

    # ---------------- UI ----------------

    def do_activate(self):
        if self.window is not None:
            self.window.present()
            return

        win = Gtk.ApplicationWindow(application=self)
        self.window = win
        win.set_title(APP_TITLE)
        win.set_decorated(False)
        win.set_resizable(True)
        win.set_keep_above(True)
        win.stick()
        win.set_skip_taskbar_hint(True)
        win.set_skip_pager_hint(True)
        try:
            win.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        except Exception:  # noqa: BLE001
            pass
        gw, gh = self._load_geometry()
        win.set_default_size(gw, gh)
        try:
            geom = Gdk.Geometry()
            geom.min_width = MIN_WIDTH
            geom.min_height = MIN_HEIGHT
            win.set_geometry_hints(geom, Gdk.WindowHints.MIN_SIZE)
        except Exception:  # noqa: BLE001
            pass

        try:
            screen = win.get_screen()
            rgba = screen.get_rgba_visual()
            if rgba is not None:
                win.set_visual(rgba)
        except Exception:  # noqa: BLE001
            pass

        win.connect("button-press-event", self._on_press)
        win.connect("window-state-event", self._on_win_state)
        win.connect("configure-event", self._on_configure)
        win.connect("motion-notify-event", self._on_motion)
        win.connect("button-release-event", self._on_release)
        win.add_events(
            Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK
        )

        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        win.add(main)

        # ---- header ----
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.get_style_context().add_class("header")
        header.set_hexpand(True)

        titlebox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        titlebox.set_hexpand(True)
        t = Gtk.Label(label=APP_TITLE)
        t.get_style_context().add_class("title")
        t.set_halign(Gtk.Align.START)
        self.status_label = Gtk.Label(label="memulai…")
        self.status_label.get_style_context().add_class("subtitle")
        self.status_label.set_halign(Gtk.Align.START)
        titlebox.add(t)
        titlebox.add(self.status_label)
        header.add(titlebox)

        self.method_badge = Gtk.Label(label="AGY")
        self.method_badge.get_style_context().add_class("badge")
        header.add(self.method_badge)

        dash = Gtk.Button()
        dash.set_image(Gtk.Image.new_from_icon_name("applications-internet", Gtk.IconSize.BUTTON))
        dash.get_style_context().add_class("flat-small")
        dash.set_tooltip_text("Buka dashboard token (localhost:5111)")
        dash.set_valign(Gtk.Align.CENTER)
        dash.connect("clicked", lambda *_: self._open_dash())
        header.add(dash)

        refresh = Gtk.Button()
        refresh.set_image(Gtk.Image.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.BUTTON))
        refresh.get_style_context().add_class("flat-small")
        refresh.set_tooltip_text("Refresh sekarang")
        refresh.set_valign(Gtk.Align.CENTER)
        refresh.connect("clicked", lambda *_: self._refresh())
        header.add(refresh)

        mini = Gtk.Button()
        mini.set_image(Gtk.Image.new_from_icon_name("window-minimize-symbolic", Gtk.IconSize.BUTTON))
        mini.get_style_context().add_class("flat-small")
        mini.set_tooltip_text("Minimalkan — sembunyi ke tray")
        mini.set_valign(Gtk.Align.CENTER)
        mini.connect("clicked", lambda *_: self._minimize())
        header.add(mini)

        close = Gtk.Button()
        close.set_image(Gtk.Image.new_from_icon_name("window-close-symbolic", Gtk.IconSize.BUTTON))
        close.get_style_context().add_class("flat-small")
        close.set_tooltip_text("Tutup widget")
        close.set_valign(Gtk.Align.CENTER)
        close.connect("clicked", lambda *_: win.close())
        header.add(close)

        main.add(header)

        # ---- body (dalam ScrolledWindow agar resize menggulung isi) ----
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.body.get_style_context().add_class("body")
        self.scroll = Gtk.ScrolledWindow()
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroll.set_shadow_type(Gtk.ShadowType.NONE)
        # propagasikan tinggi natural isi, jika tidak jendela hanya setinggi
        # ~46px (viewport kosong) dan hanya satu bucket yang terlihat.
        self.scroll.set_propagate_natural_height(True)
        self.scroll.add(self.body)
        main.pack_start(self.scroll, expand=True, fill=True, padding=0)

        self.spinner = Gtk.Spinner()
        self.spinner.set_margin_top(14)
        self.spinner.set_margin_bottom(14)
        self.spinner.start()
        main.add(self.spinner)

        self.footer = Gtk.Label(label="")
        self.footer.set_halign(Gtk.Align.START)
        self.footer.set_hexpand(True)

        bottombar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        bottombar.get_style_context().add_class("footer")
        bottombar.add(self.footer)

        grip = Gtk.EventBox()
        grip.set_size_request(18, 16)
        grip.set_above_child(True)
        grip.get_style_context().add_class("size-grip")
        grip.set_tooltip_text("Seret untuk ubah ukuran")
        grip.connect("button-press-event", self._on_grip_press)
        bottombar.pack_end(grip, expand=False, fill=False, padding=0)
        main.pack_start(bottombar, expand=False, fill=False, padding=0)

        win.show_all()

        GLib.timeout_add_seconds(1, self._tick)
        self._refresh()
        GLib.timeout_add_seconds(max(REFRESH_INTERVAL, 15), self._periodic)
        self._init_tray()

    def _periodic(self):
        self._refresh()
        return True

    def _on_press(self, _win, event):
        if event.button == 1 and event.type == Gdk.EventType.BUTTON_PRESS:
            self.window.begin_move_drag(
                event.button, int(event.x_root), int(event.y_root), event.time
            )
        return False

    # ---------------- resize + minimize + tray ----------------

    def _on_grip_press(self, _grip, event):
        """Grip kanan-bawah: mulai drag resize manual (andal di XWayland)."""
        if event.button != 1 or event.type != Gdk.EventType.BUTTON_PRESS or self.window is None:
            return False
        try:
            w, h = self.window.get_size()
        except Exception:  # noqa: BLE001
            return False
        self._resize_start = (int(event.x_root), int(event.y_root), w, h)
        self._resizing = True
        try:
            gdkwin = self.window.get_window()
            if gdkwin is not None:
                gdkwin.set_cursor(Gdk.Cursor.new(Gdk.CursorType.BOTTOM_RIGHT_CORNER))
        except Exception:  # noqa: BLE001
            pass
        return True

    def _on_motion(self, _win, event):
        if not self._resizing or self._resize_start is None or self.window is None:
            return False
        sx, sy, sw, sh = self._resize_start
        nw = max(MIN_WIDTH, sw + (int(event.x_root) - sx))
        nh = max(MIN_HEIGHT, sh + (int(event.y_root) - sy))
        if (nw, nh) != self.window.get_size():
            self._user_sized = True
        self.window.resize(nw, nh)
        return True

    def _on_release(self, _win, event):
        if not self._resizing:
            return False
        self._resizing = False
        self._resize_start = None
        try:
            gdkwin = self.window.get_window()
            if gdkwin is not None:
                gdkwin.set_cursor(None)
        except Exception:  # noqa: BLE001
            pass
        return False

    def _autofit_height(self):
        """Pasangkan tinggi jendela ke tinggi natural isi (hanya render pertama)."""
        w = self.window
        if w is None:
            return
        try:
            _bmin, bnat = self.body.get_preferred_size()
            need = bnat.height if bnat.height > 0 else 0
            cur_w, _ = w.get_size()
            total = max(need + 110, MIN_HEIGHT)
            w.resize(cur_w, total)
        except Exception:  # noqa: BLE001
            pass

    def _on_win_state(self, _win, event):
        self.iconified = bool(event.new_window_state & Gdk.WindowState.ICONIFIED)
        if not self.iconified and self.window is not None and self.tray_ok:
            # sudah dikembalikan (dari tray) → kembali mode widget desktop
            self.window.set_skip_taskbar_hint(True)
        return False
    def _on_configure(self, _win, event):
        self._geom_cache = (event.width, event.height)
        if not self._geom_dirty:
            self._geom_dirty = True
            GLib.timeout_add_seconds(1, self._save_geometry)
        return False

    def _save_geometry(self):
        self._geom_dirty = False
        if self._geom_cache is None:
            return False
        w, h = self._geom_cache
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            tmp = GEOM_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump({"width": max(w, MIN_WIDTH), "height": max(h, MIN_HEIGHT)}, f)
            os.replace(tmp, GEOM_FILE)
        except Exception:  # noqa: BLE001
            pass
        return False

    @staticmethod
    def _load_geometry():
        try:
            with open(GEOM_FILE) as f:
                d = json.load(f)
            w = max(int(d.get("width", WIDTH)), MIN_WIDTH)
            h = int(d.get("height", -1))
        except Exception:  # noqa: BLE001
            return WIDTH, -1
        return w, (h if h >= MIN_HEIGHT else -1)

    def _init_tray(self):
        """Tray: AppIndicator3 (native Ubuntu) dulu, fallback Gtk.StatusIcon."""
        if AppIndicator3 is not None:
            try:
                ind = AppIndicator3.Indicator.new(
                    APP_ID,
                    ICON_PATH,
                    AppIndicator3.IndicatorCategory.APPLICATION_STATUS,
                )
                ind.set_title(APP_TITLE)
                ind.set_label("", "")
                ind.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
                ind.set_icon_full(ICON_PATH, APP_TITLE)
                # AppIndicator3 binding ini tak punya sinyal "activate";
                # klik-kiri tak tertangkap → pakai menu (klik kanan/panel).
                try:
                    ind.connect("connection_changed", self._on_indicator_conn)
                except Exception:  # noqa: BLE001
                    pass
                self.indicator = ind
                self._tray_menu()
                # tray_ok dikonfirmasi oleh sinyal connection_changed; asumsi
                # True dulu agar minimize tidak menampilkan taskbar secara prematur.
                self.tray_ok = True
                return
            except Exception:  # noqa: BLE001
                self.indicator = None
        try:
            self.tray = Gtk.StatusIcon.new_from_file(ICON_PATH)
        except Exception:  # noqa: BLE001
            self.tray = None
            return
        self.tray.set_tooltip_text("Antigravity Quota — klik tampilkan/sembunyi")
        self.tray.connect("activate", self._tray_activate)
        self.tray.connect("popup-menu", self._statusicon_popup)
        GLib.timeout_add_seconds(1, self._poll_tray)

    def _tray_menu(self):
        """Menu klik-kanan untuk AppIndicator3 (juga dipakai StatusIcon)."""
        m = Gtk.Menu()
        i_show = Gtk.MenuItem(label="Tampilkan widget")
        i_show.connect("activate", lambda *_: self._restore())
        m.append(i_show)
        i_ref = Gtk.MenuItem(label="Refresh sekarang")
        i_ref.connect("activate", lambda *_: self._refresh())
        m.append(i_ref)
        m.append(Gtk.SeparatorMenuItem())
        i_quit = Gtk.MenuItem(label="Keluar")
        i_quit.connect("activate", lambda *_: self.quit())
        m.append(i_quit)
        m.show_all()
        if self.indicator is not None:
            self.indicator.set_menu(m)
        return m

    def _poll_tray(self):
        """Cek tray legacy benar-benar tertanam; jika tidak, minimize pakai taskbar."""
        try:
            self.tray_ok = self.tray is not None and self.tray.is_embedded()
        except Exception:  # noqa: BLE001
            self.tray_ok = False
        self.tray_polls += 1
        return (not self.tray_ok) and self.tray_polls < 20

    def _on_indicator_conn(self, _ind, connected):
        """AppIndicator3: connected=True berarti icon terlihat di panel."""
        self.tray_ok = bool(connected)

    def _minimize(self, *_):
        w = self.window
        if w is None:
            return
        # Mutter mengabaikan iconify() untuk jendela keep_above+skip_taskbar+UTILITY.
        # Sembunyikan total saja; pemulihan lewat tray icon (menu "Tampilkan").
        if not self.tray_ok:
            # tanpa tray terlihat → taskbar jalan pulang, biarkan iconify saja
            try:
                w.iconify()
            except Exception:  # noqa: BLE001
                pass
            w.set_skip_taskbar_hint(False)
            return
        w.set_visible(False)
        self.iconified = True

    def _restore(self, *_):
        w = self.window
        if w is None:
            return
        w.set_skip_taskbar_hint(True)
        if not w.get_visible():
            w.set_visible(True)
        w.present()
        w.deiconify()
        self.iconified = False

    def _tray_activate(self, *_):
        w = self.window
        if w is None:
            return
        if self.iconified or not w.get_visible():
            self._restore()
        else:
            w.iconify()

    def _statusicon_popup(self, _icon, button, etime):
        self._tray_menu().popup(None, None, None, None, button, etime)

    # ---------------- data ----------------

    def _refresh(self):
        if self.fetching:
            return
        self.fetching = True
        self.spinner.show()
        self.spinner.start()
        threading.Thread(target=self._fetch_thread, daemon=True).start()

    def _fetch_thread(self):
        try:
            data = fetch_quota(self.agy)
            # Email dari token sebenarnya (field dashboard bisa usang)
            email = resolve_account_email()
            if email:
                data["account"] = email
            GLib.idle_add(self._render_ok, data)
        except FetchError as e:
            GLib.idle_add(self._render_err, e)
        except Exception as e:  # noqa: BLE001
            GLib.idle_add(self._render_err, FetchError(f"Kesalahan tak terduga: {e}"))

    def _clear_body(self):
        for child in self.body.get_children():
            self.body.remove(child)

    def _render_ok(self, data: dict):
        self.fetching = False
        self.spinner.stop()
        self.spinner.hide()
        self._clear_body()

        account = data.get("account")
        self.status_label.set_text(account if account else "akun agy")
        src = data.get("source", "agy")
        self.method_badge.set_text("DASHBOARD" if src == "dashboard" else "AGY CLI")
        ctx = self.method_badge.get_style_context()
        ctx.remove_class("dash")
        if src == "dashboard":
            ctx.add_class("dash")

        total_rows = 0
        for g in data.get("groups", []):
            self.body.add(GroupLabel(g))
            for b in g.get("buckets", []):
                self.body.add(BucketRow(b))
                total_rows += 1
        if total_rows == 0:
            empty = Gtk.Label(label="Tidak ada data quota.")
            empty.get_style_context().add_class("hint-text")
            empty.set_margin_top(10)
            self.body.add(empty)
        self.body.show_all()

        fetched = data.get("fetched_at") or time.strftime("%H:%M:%S")
        self.footer.set_text(f"diperbarui {fetched} • refresh tiap {REFRESH_INTERVAL}s • sumber: {src}")

        # Auto-fit tinggi saat render pertama: jendela dibuat saat body kosong
        # (hanya spinner), jadi tinggi naturalnya hanya ~header. Setelah bucket
        # terisi, tinggi natural baru terlihat.
        if not self._user_sized and self.window is not None:
            self._autofit_height()

    def _render_err(self, err: FetchError):
        self.fetching = False
        self.spinner.stop()
        self.spinner.hide()
        self._clear_body()

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.get_style_context().add_class("err-box")
        t = Gtk.Label(label="⚠  Tidak dapat mengambil quota")
        t.get_style_context().add_class("bucket-label")
        t.set_halign(Gtk.Align.START)
        box.add(t)
        msg = Gtk.Label(label=err.message)
        msg.get_style_context().add_class("err-text")
        msg.set_halign(Gtk.Align.START)
        msg.set_line_wrap(True)
        box.add(msg)
        hint = (
            "Pastikan agy sudah login: `agy` lalu login, atau nyalakan dashboard "
            "agy-token-monitor di port 5111."
            if err.not_logged_in
            else "Cek koneksi / jalankan: agy --print /usage"
        )
        h = Gtk.Label(label=hint)
        h.get_style_context().add_class("hint-text")
        h.set_halign(Gtk.Align.START)
        box.add(h)
        self.body.add(box)

        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btns.set_margin_top(8)
        b_retry = Gtk.Button(label="Coba lagi")
        b_retry.connect("clicked", lambda *_: self._refresh())
        btns.add(b_retry)
        b_dash = Gtk.Button(label="Buka dashboard")
        b_dash.connect("clicked", lambda *_: self._open_dash())
        btns.add(b_dash)
        self.body.add(btns)
        self.body.show_all()
        self.footer.set_text(f"gagal • {time.strftime('%H:%M:%S')}")

    # ---------------- aksi ----------------

    def _open_dash(self):
        url = DASH_URL.rsplit("/", 1)[0] + "/"
        try:
            subprocess.Popen(["xdg-open", url], start_new_session=True)
        except Exception as e:  # noqa: BLE001
            print(f"buka dashboard gagal: {e}", file=sys.stderr)

    # ---------------- ticking countdown ----------------

    def _tick(self):
        for child in self.body.get_children():
            if isinstance(child, BucketRow):
                child.tick()
        return True


def main():
    app = Widget()
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    screen = Gdk.Screen.get_default()
    if screen is not None:
        Gtk.StyleContext.add_provider_for_screen(
            screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Self-test widget: parsing agy, normalisasi, dan rendering UI."""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk  # noqa: E402

import widget as W  # noqa: E402

failures = []


def check(name, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        failures.append(name)


# 1. fmt_reset — waktu dibekukan
FIXED = 1000000.0
W.time.time = lambda: FIXED
check("fmt_reset hari", W.fmt_reset(FIXED + 5 * 86400 + 20 * 3600 + 56 * 60) == "reset 5 hari 20 jam")
check("fmt_reset jam", W.fmt_reset(FIXED + 4 * 3600 + 42 * 60) == "reset 4 jam 42 mnt")
check("fmt_reset menit", W.fmt_reset(FIXED + 12 * 60 + 5) == "reset 12 mnt 5 dtk")
check("fmt_reset detik", W.fmt_reset(FIXED + 45) == "reset 45 dtk")
check("fmt_reset lewat", W.fmt_reset(FIXED - 10) == "sudah reset")

# 2. pct_class
check("pct_class good", W.pct_class(95) == "good")
check("pct_class mid", W.pct_class(60) == "mid")
check("pct_class low", W.pct_class(30) == "low")
check("pct_class crit", W.pct_class(5) == "crit")

# 3. parse_reset
from datetime import datetime, timezone

ep = W.parse_reset("2026-10-07T02:25:25Z")
expected = datetime.fromisoformat("2026-10-07T02:25:25+00:00").timestamp()
check("parse_reset ISO Z", abs(ep - expected) < 1.0, str(ep))
check("parse_reset kosong", W.parse_reset("") == 0.0)

# 4. _norm_bucket
b = W._norm_bucket({"name": "Weekly", "window": "weekly", "remaining_fraction": 0.4991})
check("norm: pct 49.91", abs(b["remaining_pct"] - 49.91) < 0.01)
b2 = W._norm_bucket({"name": "X", "remaining_pct": 100})
check("norm: dari remaining_pct", b2["remaining_fraction"] == 1.0)
b3 = W._norm_bucket({"name": "X"})
check("norm: default 0", b3["remaining_pct"] == 0)

# 5. find_agy
agy = W.find_agy()
check("find_agy ketemu", agy is not None and os.path.exists(agy), str(agy))

# 6. sumber data live
dash = W.fetch_from_dashboard()
if dash is not None:
    check("dashboard: status ok", dash["status"] == "ok")
    check("dashboard: ada groups", len(dash["groups"]) >= 1)
    check("dashboard: ada buckets", sum(len(g["buckets"]) for g in dash["groups"]) >= 1)
    print("       dashboard groups:", [g["name"] for g in dash["groups"]])
else:
    print("SKIP  dashboard (tidak menyala)")

if agy:
    try:
        direct, plain = W.fetch_from_agy(agy)
        check("agy langsung: groups ada", len(direct["groups"]) >= 1)
        print("       agy groups:", [g["name"] for g in direct["groups"]])
    except W.FetchError as e:
        check("agy langsung: ambil data", False, e.message)

# 6b. ekstraksi token + resolusi email dari tokeninfo
tok = W._extract_access_token(W.TOKEN_FILE)
if tok:
    check("token: ter-ekstrak", isinstance(tok, str) and len(tok) > 40)
    print("       token terbaca:", len(tok), "char (tidak dicetak)")
    email = W.resolve_account_email()
    check("resolusi email: dapat alamat", bool(email) and "@" in email, str(email))
    print("       email tokeninfo:", email)
    hit = W.resolve_account_email()
    check("resolusi email: cache hit sama", hit == email, str(hit))
else:
    print("SKIP  token (belum login agy) — lewati tes tokeninfo")


# 7. rendering UI pakai data mock
class TestApp(W.Widget):
    def do_activate(self):
        if self.window is None:
            W.Widget.do_activate(self)
        self._run_checks()
        self.quit()

    def _run_checks(self):
        mock = {
            "status": "ok",
            "account": "you@example.com",
            "source": "dashboard",
            "fetched_at": "2026-10-01 12:28:53",
            "groups": [
                {
                    "name": "Gemini Models",
                    "description": "Models within this group: Gemini Flash, Gemini Pro",
                    "buckets": [
                        {
                            "id": "gemini-weekly",
                            "name": "Weekly Limit Remaining",
                            "window": "weekly",
                            "remaining_fraction": 0.4991,
                            "reset_time": "2026-10-07T02:25:25Z",
                        },
                        {
                            "id": "gemini-5h",
                            "name": "Five Hour Limit Remaining",
                            "window": "5h",
                            "remaining_fraction": 0.9558,
                            "reset_time": "2026-10-01T10:10:57Z",
                        },
                    ],
                },
                {
                    "name": "Claude and GPT models",
                    "description": "",
                    "buckets": [
                        {
                            "id": "3p-weekly",
                            "name": "Weekly Limit Remaining",
                            "window": "weekly",
                            "remaining_fraction": 0.0,
                            "reset_time": "2026-10-06T03:01:20Z",
                        },
                        {
                            "id": "3p-5h",
                            "name": "Five Hour Limit Remaining",
                            "window": "5h",
                            "remaining_fraction": 0.0,
                            "reset_time": "",
                        },
                    ],
                },
            ],
        }
        for g in mock["groups"]:
            g["buckets"] = [W._norm_bucket(b) for b in g["buckets"]]
        self._render_ok(mock)

        buckets = [c for c in self.body.get_children() if isinstance(c, W.BucketRow)]
        groups = [c for c in self.body.get_children() if isinstance(c, W.GroupLabel)]
        check("render: 2 groups", len(groups) == 2)
        check("render: 4 buckets", len(buckets) == 4)
        check("render: akun", self.status_label.get_text() == "you@example.com")
        check("render: badge DASHBOARD", self.method_badge.get_text() == "DASHBOARD")
        check("render: footer", "diperbarui 2026-10-01 12:28:53" in self.footer.get_text())

        # gemini-weekly = 49.91% -> low (di bawah 50)
        check("render: bar0 low", "lvl-low" in buckets[0].bar.get_style_context().list_classes())
        check("render: pct0 ~49.9%", buckets[0].pct_label.get_text().startswith("49.9"))
        # gemini-5h = 95% -> good
        check("render: bar1 good", "lvl-good" in buckets[1].bar.get_style_context().list_classes())
        # 3p-weekly 0% -> EXHAUSTED
        check("render: exhausted", buckets[2].pct_label.get_text() == "EXHAUSTED")
        check("render: bar2 crit", "lvl-crit" in buckets[2].bar.get_style_context().list_classes())
        # 3p-5h 0% window 5h -> NONAKTIF
        check("render: 5h nonaktif", buckets[3].pct_label.get_text() == "NONAKTIF")
        check("render: bar3 off", "lvl-off" in buckets[3].bar.get_style_context().list_classes())

        buckets[0].tick()
        check("render: countdown bucket0", buckets[0].reset_label.get_text().startswith("reset "))

        # error render
        self._render_err(W.FetchError("agy belum login", not_logged_in=True))
        boxes = [c for c in self.body.get_children() if isinstance(c, Gtk.Box)]
        btns = [b for box in boxes for b in box.get_children() if isinstance(b, Gtk.Button)]
        check("render: err 2 tombol", len(btns) == 2, str([b.get_label() for b in btns]))


app = TestApp()
app.run([])
print()
if failures:
    print("GAGAL:", len(failures), failures)
sys.exit(1 if failures else 0)

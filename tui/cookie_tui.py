#!/usr/bin/env python3
"""Cookie Bot console — DASH-styled terminal panel for Steam Cookie Clicker.

The real Steam game keeps running hidden (Steam counts hours and grants
achievements). A background agent (cookie_agent.py) holds the link to the game,
keeps long-term history and relaunches the game if it dies. This panel is just
a window into it: open it, look, close it — the game and the bot keep going.

Usage:
  CookieClicker              open the panel (starts agent + game if needed)
  CookieClicker status       print a short status and exit
  CookieClicker show|hide    show or hide the game window
  CookieClicker save         save the game
  CookieClicker quit         save and fully close the game (watchdog stands down)
  CookieClicker agent stop   stop the background agent
"""
import argparse
import curses
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
AGENT = HERE / "cookie_agent.py"
PATCHER = HERE / "patch_game.py"
PORT = int(os.environ.get("COOKIEBOT_PORT", "17777"))
DATA = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "cookiebot"
PANEL_STATE = DATA / "panel.json"

TABS = ["overview", "market", "minigames", "history", "charts", "settings"]

# settings tab: (cfg name, label, hint) grouped
SETTINGS = [
    ("play", [
        ("click", "autoclick", "20 clicks/s, 50 during combos"),
        ("golden", "golden cookies", "pop golden cookies and reindeer"),
        ("wrath", "wrath cookies", "also pop wrath cookies (risky)"),
        ("fortune", "fortunes", "click fortune news"),
        ("achievements", "free achievements", "do cheap in-game actions that give milk"),
    ]),
    ("buying", [
        ("buy", "auto-buy", "master switch for purchases"),
        ("buyBuildings", "buildings", ""),
        ("buyUpgrades", "upgrades", ""),
        ("luckyBank", "lucky bank", "keep 6000 s of CpS for lucky payouts"),
        ("wrinklers", "farm wrinklers", "keep wrinklers, pop to pay for purchases"),
        ("fillers", "buy while saving", "buy things that repay before the target is affordable"),
    ]),
    ("minigames", [
        ("spells", "spells", "cast force the hand of fate"),
        ("spellPredict", "predict spells", "read the seeded outcome, cast only good ones"),
        ("lumps", "harvest lumps", ""),
        ("lumpSpend", "spend lumps", "unlock grimoire, pantheon, market, garden"),
        ("lumpLevels", "level buildings", "then spend lumps on building levels (+1% each)"),
        ("pantheon", "pantheon", "fill empty god slots"),
        ("garden", "garden", "plant, harvest mature plants"),
        ("dragon", "dragon", "train cookie-cost levels, pick an aura"),
        ("market", "stock market", "buy low, sell high"),
        ("marketBrokers", "brokers", "hire brokers to cut overhead"),
        ("marketCheat", "see trends", "peek at the hidden market trend"),
    ]),
    ("heaven", [
        ("heavenlyBuy", "heavenly shopping", "on the heaven screen, buy upgrades in a fixed order"),
        ("autoAscend", "auto ascend", "ascend by itself when ready (otherwise press A)"),
    ]),
    ("system", [
        ("consoleMode", "background mode", "keep the game window hidden"),
        ("@watchdog", "watchdog", "relaunch the game if it closes unexpectedly"),
        ("@clawd", "clawd", "a little helper walking above the footer"),
        ("@anim", "animations", "count-up numbers, running shimmer on progress bars"),
    ]),
]
SETTING_ROWS = [(g, k, l, h) for g, items in SETTINGS for (k, l, h) in items]

SPANS = [("1h", 3600), ("6h", 6 * 3600), ("24h", 86400), ("7d", 7 * 86400)]


# ------------------------------------------------------------------ agent client
def http(method, path, body=None, timeout=1.5):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def agent_alive():
    try:
        return bool(http("GET", "/ping", timeout=0.5).get("ok"))
    except (urllib.error.URLError, OSError, ValueError):
        return False


def ensure_agent():
    if agent_alive():
        return True
    subprocess.Popen([sys.executable, str(AGENT)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     stdin=subprocess.DEVNULL, start_new_session=True)
    for _ in range(30):
        time.sleep(0.1)
        if agent_alive():
            return True
    return False


class Client:
    """Polls the agent in the background so drawing never waits on the network."""

    def __init__(self):
        self.lock = threading.Lock()
        self.data = {}
        self.hist = {}
        self.ok = False
        self.span = 3600
        self.stop = False
        threading.Thread(target=self.loop, daemon=True).start()

    def loop(self):
        n = 0
        while not self.stop:
            try:
                d = http("GET", "/state")
                with self.lock:
                    self.data, self.ok = d, True
                if n % 20 == 0:  # history every ~8 s, the full week every ~60 s
                    for sp in {3600, self.span} | ({7 * 86400} if n % 150 == 0 else set()):
                        rows = http("GET", f"/history?span={sp}").get("rows", [])
                        with self.lock:
                            self.hist[sp] = rows
            except (urllib.error.URLError, OSError, ValueError):
                with self.lock:
                    self.ok = False
            n += 1
            time.sleep(0.4)

    def refresh_history(self):
        try:
            rows = http("GET", f"/history?span={self.span}").get("rows", [])
            with self.lock:
                self.hist[self.span] = rows
        except (urllib.error.URLError, OSError, ValueError):
            pass

    def snapshot(self):
        with self.lock:
            return self.data.get("state"), bool(self.data.get("live")), self.data.get("agent") or {}, self.ok

    def history(self, span):
        with self.lock:
            return list(self.hist.get(span, []))

    def push(self, cmd):
        try:
            return http("POST", "/cmd", cmd).get("result", "")
        except (urllib.error.URLError, OSError, ValueError):
            return "agent unreachable"


# ------------------------------------------------------------------ helpers
SUFFIX = ["", "K", "M", "B", "T", "Qa", "Qi", "Sx", "Sp", "Oc", "No", "Dc",
          "UDc", "DDc", "TDc", "QaDc", "QiDc", "SxDc", "SpDc", "OcDc", "NoDc", "Vg"]


def num(x, d=1):
    if x is None:
        return "—"
    x = float(x)
    if abs(x) < 0.05:
        x = 0.0
    if abs(x) < 1000:
        s = f"{x:.{d}f}" if d else f"{x:.0f}"
        return s.rstrip("0").rstrip(".") if "." in s else s
    i = 0
    while abs(x) >= 1000 and i < len(SUFFIX) - 1:
        x /= 1000
        i += 1
    return f"{x:.3g}{SUFFIX[i]}" if abs(x) < 1000 else f"{x:.2e}"


def dur(sec):
    if sec is None or sec != sec or sec == float("inf"):
        return "∞"
    sec = int(max(0, sec))
    if sec < 1:
        return "now"
    d, sec = divmod(sec, 86400)
    h, sec = divmod(sec, 3600)
    m, s = divmod(sec, 60)
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def gained(rows, col):
    """Sum of increases of a cumulative column; ascensions and game restarts
    reset the counters, so drops are skipped instead of subtracted."""
    return sum(max(0, b[col] - a[col]) for a, b in zip(rows, rows[1:]))


def since_reset(rows):
    """Rows after the last reset of 'cookies earned' (ascension)."""
    for i in range(len(rows) - 1, 0, -1):
        if rows[i][3] < rows[i - 1][3]:
            return rows[i:]
    return rows


def game_running():
    return subprocess.run(["pgrep", "-f", r"Cookie Clicker\.exe"], capture_output=True).returncode == 0


def patched():
    return subprocess.run([sys.executable, str(PATCHER), "--check"], capture_output=True).returncode == 0


SPARK = " ▁▂▃▄▅▆▇█"


def sparkline(vals, width):
    vals = [v for v in vals if v is not None][-width:]
    if not vals:
        return ""
    lo, hi = min(vals), max(vals)
    span = hi - lo or 1
    return "".join(SPARK[1 + int((v - lo) / span * 7)] for v in vals)


def load_panel_state():
    try:
        return json.loads(PANEL_STATE.read_text())
    except (OSError, ValueError):
        return {}


def save_panel_state(d):
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        PANEL_STATE.write_text(json.dumps(d))
    except OSError:
        pass


# ------------------------------------------------------------------ DASH palette
# Terminal-256 approximations of the DASH tokens (dash-theme design.md).
C_T1, C_T2, C_T3 = 255, 248, 244          # #ededed #a1a1a6 #85858b
C_AMBER = 214                              # #f5a524
C_OK, C_BAD = 72, 167                      # #4cc38a #e5484d
C_RAISED = 235                             # #232325
C_LINE = 237                               # hairline


class Pal:
    def __init__(self):
        curses.start_color()
        curses.use_default_colors()
        rich = curses.COLORS >= 256

        def pair(n, fg, bg=-1, fallback=curses.COLOR_WHITE):
            curses.init_pair(n, fg if rich else fallback, bg if rich else -1)
            return curses.color_pair(n)

        self.t1 = pair(1, C_T1)
        self.t2 = pair(2, C_T2)
        self.t3 = pair(3, C_T3)
        self.amber = pair(4, C_AMBER, fallback=curses.COLOR_YELLOW)
        self.ok = pair(5, C_OK, fallback=curses.COLOR_GREEN)
        self.bad = pair(6, C_BAD, fallback=curses.COLOR_RED)
        self.line = pair(7, C_LINE)
        self.r_t1 = pair(8, C_T1, C_RAISED)
        self.r_t2 = pair(9, C_T2, C_RAISED)
        self.r_amber = pair(10, C_AMBER, C_RAISED, curses.COLOR_YELLOW)
        self.r_t3 = pair(11, C_T3, C_RAISED)
        self.r_ok = pair(12, C_OK, C_RAISED, curses.COLOR_GREEN)
        self.r_bad = pair(13, C_BAD, C_RAISED, curses.COLOR_RED)
        self.fill = pair(14, 239)  # chart fill texture, between hairline and t3
        self.clawd = pair(15, 173, fallback=curses.COLOR_RED)  # Clawd's terracotta
        self._rich = rich
        self._pairs = {}
        self._next = 40

    def rgb(self, fg, bg):
        """Colour pair for any (fg, bg) 256-colour combo, created on first use."""
        key = (fg, bg)
        if key not in self._pairs:
            if not self._rich or self._next >= curses.COLOR_PAIRS:
                return self.clawd
            curses.init_pair(self._next, fg, bg)
            self._pairs[key] = curses.color_pair(self._next)
            self._next += 1
        return self._pairs[key]


# ------------------------------------------------------------------ panel
class Panel:
    def __init__(self, scr, client):
        self.scr = scr
        self.c = client
        self.p = Pal()
        self.tab = 0
        self.cursor = 0
        self.span_i = 0
        self.flash = ""
        self.flash_t = 0
        self.quit_armed = 0
        self.patched = patched()
        self.opened = time.time()
        ps = load_panel_state()
        self.last_seen = ps.get("closed")
        self.tab = ps.get("tab", 0) if 0 <= ps.get("tab", 0) < len(TABS) else 0
        self.welcome = None
        self.prefs = {"clawd": True, "anim": True}
        self.prefs.update(ps.get("prefs") or {})
        self.f = 0                 # frame counter
        self.shown = {}            # count-up display values
        self.cl = {"x": 4, "dir": 1, "jump": 0, "fx": [], "fast": 0, "prev": None}
        curses.curs_set(0)
        scr.timeout(100 if self.prefs.get("anim") or self.prefs.get("clawd") else 250)

    def close(self):
        save_panel_state({"closed": time.time(), "tab": self.tab, "prefs": self.prefs})

    def tween(self, key, target):
        """Count-up: the shown value eases toward the real one each frame."""
        if not self.prefs.get("anim") or target is None:
            return target
        v = self.shown.get(key)
        if v is None or v <= 0 or target <= 0 or abs(target - v) / target < 0.002 or abs(target / v) > 1e3:
            v = target
        else:
            v += (target - v) * 0.35
        self.shown[key] = v
        return v

    # -- primitives
    def put(self, y, x, text, attr=0, maxw=None):
        h, w = self.scr.getmaxyx()
        if not (0 <= y < h) or x >= w - 1 or x < 0:
            return 0
        room = w - 1 - x if maxw is None else min(maxw, w - 1 - x)
        if room <= 0:
            return 0
        try:
            self.scr.addnstr(y, x, text, room, attr)
        except curses.error:
            pass
        return min(len(text), room)

    def say(self, msg):
        self.flash, self.flash_t = msg, time.time()

    def hline(self, y, x, w):
        self.put(y, x, "─" * max(0, w), self.p.line)

    def section(self, y, x, title, w, right=""):
        p = self.p
        self.put(y, x, "●", p.amber)
        self.put(y, x + 2, title, p.t2)
        rx = x + w - len(right)
        self.hline(y, x + 3 + len(title), max(0, (rx - 1 if right else x + w) - (x + 3 + len(title))))
        if right:
            self.put(y, rx, right, p.t3)
        return y + 1

    def kv(self, y, x, w, k, v, attr=None):
        self.put(y, x, k, self.p.t3)
        v = str(v)
        self.put(y, x + w - len(v), v, attr if attr is not None else self.p.t1)
        return y + 1

    def bar(self, y, x, w, frac, attr):
        if w <= 0:
            return
        frac = max(0.0, min(1.0, frac or 0))
        full = int(round(frac * w))
        self.put(y, x, "━" * full, attr)
        self.put(y, x + full, "─" * (w - full), self.p.line)
        # DASH loading stripe: a bright glint runs along an amber (active) bar
        if self.prefs.get("anim") and attr == self.p.amber and full >= 6:
            pos = self.f % (full + 8) - 4
            for k, a in ((-1, self.p.amber | curses.A_BOLD), (0, self.p.t1 | curses.A_BOLD), (1, self.p.amber | curses.A_BOLD)):
                if 0 <= pos + k < full:
                    self.put(y, x + pos + k, "━", a)

    def row(self, y, x, w, idx, active, parts):
        """Numbered list row; active = raised surface + amber number + amber edge."""
        p = self.p
        if active:
            self.put(y, x, " " * w, p.r_t1)
            self.put(y, x, "▎", p.r_amber)
            self.put(y, x + 1, f"{idx:02d}", p.r_amber)
        else:
            self.put(y, x + 1, f"{idx:02d}", p.t3)
        cx = x + 4
        for text, kind, width, align in parts:
            attr = getattr(p, ("r_" if active else "") + kind)
            text = str(text)[:width]
            pad = text.rjust(width) if align == "r" else text.ljust(width)
            self.put(y, cx, pad, attr, maxw=max(0, x + w - cx))
            cx += width + 1

    def chart(self, y, x, w, h, ts, vals, log=False):
        """Braille area chart: hairline grid, dotted fill under a solid line,
        amber end point, value labels on the right, time axis below.
        Takes h rows for the plot plus one for the time axis."""
        p = self.p
        pts = [(t, v) for t, v in zip(ts, vals) if v is not None]
        lw = 8                      # label column
        cw = w - lw - 1             # plot width in cells
        if cw < 8 or h < 2:
            return
        if len(pts) < 2:
            self.put(y + h // 2, x, "collecting data…", p.t3)
            return
        n = cw * 2                  # braille gives 2 dots per cell horizontally
        if len(pts) > n:            # squeeze the span into the width (bucket means)
            step = len(pts) / n
            buckets = [pts[int(i * step):max(int(i * step) + 1, int((i + 1) * step))] for i in range(n)]
            pts = [(b[-1][0], sum(v for _, v in b) / len(b)) for b in buckets]
        import math
        f = (lambda v: math.log10(max(v, 1e-9))) if log else (lambda v: v)
        inv = (lambda u: 10 ** u) if log else (lambda u: u)
        fv = [f(v) for _, v in pts]
        lo, hi = min(fv), max(fv)
        if hi - lo <= abs(hi) * 0.002 + 1e-12:   # flat: give it some room
            pad = abs(hi) * 0.05 + 1e-9
            lo, hi = lo - pad, hi + pad
        else:
            pad = (hi - lo) * 0.08
            lo, hi = lo - pad, hi + pad
        rows = h * 4
        # stretch the samples over the full width (linear interpolation per dot column)
        m = len(fv)
        cols = []
        for px in range(n):
            pos = px * (m - 1) / (n - 1)
            i = min(int(pos), m - 2)
            u = fv[i] + (fv[i + 1] - fv[i]) * (pos - i)
            cols.append(int(round((hi - u) / (hi - lo) * (rows - 1))))
        line = [[0] * cw for _ in range(h)]
        fill = [[0] * cw for _ in range(h)]
        bits = ((0x01, 0x08), (0x02, 0x10), (0x04, 0x20), (0x40, 0x80))
        prev = None
        for px, py in enumerate(cols):
            span = range(min(prev, py), max(prev, py) + 1) if prev is not None else (py,)
            for yy in span:
                line[yy // 4][px // 2] |= bits[yy % 4][px % 2]
            for yy in range(py + 2, rows):          # sparse dot-matrix shading under the line
                if yy % 4 == 3 and px % 2 == 0:
                    fill[yy // 4][px // 2] |= bits[yy % 4][px % 2]
            prev = py
        end_r, end_c = cols[-1] // 4, (n - 1) // 2
        grid_rows = sorted({0, h // 3, (2 * h) // 3, h - 1})
        for r in range(h):
            for c in range(cw):
                if r == end_r and c == end_c:
                    self.put(y + r, x + c, chr(0x2800 + (line[r][c] | fill[r][c])), p.amber)
                elif line[r][c]:
                    self.put(y + r, x + c, chr(0x2800 + line[r][c]), p.t1)
                elif fill[r][c]:
                    self.put(y + r, x + c, chr(0x2800 + fill[r][c]), p.fill)
                elif r in grid_rows:
                    self.put(y + r, x + c, "┈", p.line)
        # value labels: grid levels in grey, the current value in amber at its row
        for r in grid_rows:
            if r == end_r:
                continue
            u = hi - (r * 4 + 1.5) / (rows - 1) * (hi - lo)
            self.put(y + r, x + cw + 1, num(inv(u)).rjust(lw), p.t3)
        self.put(y + end_r, x + cw + 1, num(pts[-1][1]).rjust(lw), p.amber)
        # time axis
        t0, t1 = pts[0][0], pts[-1][0]
        def stamp(t):
            return "now" if time.time() - t < 90 else time.strftime("%H:%M", time.localtime(t))
        ax = y + h
        self.put(ax, x, stamp(t0), p.t3)
        mid = stamp((t0 + t1) / 2)
        self.put(ax, x + (cw - len(mid)) // 2, mid, p.t3)
        end = stamp(t1)
        self.put(ax, x + cw - len(end), end, p.t3)

    def chart_box(self, y, x, w, h, ts, vals, title, right=""):
        """Section header + chart; picks a log scale when values span >= 10x."""
        good = [v for v in vals if v and v > 0]
        log = len(good) > 1 and max(good) / min(good) >= 10
        tag = ("log · " if log else "") + right
        y = self.section(y, x, title, w, tag.rstrip(" ·"))
        self.chart(y, x, w, h, ts, vals, log)
        return y + h + 1

    def bars(self, y, x, w, h, vals, unit=""):
        """Vertical bar chart (eighth-block resolution), newest bar in amber,
        hairline baseline, max label on the right."""
        p = self.p
        lw = 7
        cw = w - lw - 1
        if cw < 4 or h < 2:
            return
        vals = list(vals)[-cw:]
        if not vals or max(vals) <= 0:
            self.put(y + h // 2, x, "nothing yet", p.t3)
            self.hline(y + h - 1, x, cw)
            return
        top = max(vals)
        off = cw - len(vals)
        for i, v in enumerate(vals):
            lvl = v / top * h * 8
            for r in range(h):
                base = (h - 1 - r) * 8
                f = lvl - base
                ch = "█" if f >= 8 else SPARK[int(f)] if f >= 1 else ""
                if not ch:
                    if r == h - 1:
                        self.put(y + r, x + off + i, "▁", p.line)
                    continue
                self.put(y + r, x + off + i, ch, p.amber if i == len(vals) - 1 else p.t2)
        self.put(y, x + cw + 1, (num(top, 0) + unit).rjust(lw), p.t3)
        self.put(y + h - 1, x + cw + 1, (num(vals[-1], 0) + unit).rjust(lw), p.amber)

    def span_bar(self, y):
        """Span selector line shared by the history and charts tabs."""
        p = self.p
        name, span = SPANS[self.span_i]
        if self.c.span != span:
            self.c.span = span
            self.c.refresh_history()
        x = 2
        self.put(y, x, "span", p.t3)
        x += 6
        for i, (nm, _) in enumerate(SPANS):
            x += self.put(y, x, nm, p.amber if i == self.span_i else p.t3) + 3
        self.put(y, x + 2, "← → to change", p.t3)
        return self.c.history(span)

    # -- frame
    def draw(self):
        s, live, agent, ok = self.c.snapshot()
        scr, p = self.scr, self.p
        scr.erase()
        H, W = scr.getmaxyx()
        if W < 70 or H < 20:
            self.put(0, 0, f"terminal too small ({W}x{H}), need 70x20", p.bad)
            scr.refresh()
            return

        # header: dot, name, tabs, status
        self.put(0, 1, "●" if live else "○", p.amber if live else p.t3)
        x = 3 + self.put(0, 3, "cookieclicker-bot", p.t1 | curses.A_BOLD) + 3
        for i, name in enumerate(TABS):
            act = i == self.tab
            x += self.put(0, x, str(i + 1), p.amber if act else p.t3)
            x += self.put(0, x, " " + name, p.t1 if act else p.t3) + 3
        right = []
        if s:
            right.append(("bot on", p.t1) if s.get("enabled") else ("bot off", p.bad))
            right.append(("hidden", p.t2) if (s.get("window") or {}).get("hidden") else ("shown", p.t2))
        right.append((time.strftime("%H:%M"), p.t3))
        def need(items):
            return sum(len(t) for t, _ in items) + 3 * (len(items) - 1)
        while len(right) > 1 and W - 2 - need(right) <= x:   # narrow: drop the clock, then window state
            right.pop()
        rx = W - 2 - need(right)
        if rx > x:
            for i, (t, a) in enumerate(right):
                if i:
                    rx += self.put(0, rx, " · ", p.t3)
                rx += self.put(0, rx, t, a)
        self.hline(1, 0, W - 1)

        self.f += 1
        lane = self.prefs.get("clawd") and H >= 36
        Hc = H - 8 if lane else H
        if not ok:
            self.draw_offline(H, W, "agent is not running")
        elif not s:
            self.draw_offline(H, W, "game is running, waiting for the mod…" if game_running() else "game is not running")
        else:
            getattr(self, "tab_" + TABS[self.tab])(s, agent, Hc, W)
        if lane:
            self.clawd(s, live, H, W)
        self.footer(s, agent, H, W)
        scr.refresh()

    def draw_offline(self, H, W, why):
        p = self.p
        y = self.section(3, 2, "connection", W - 4)
        self.put(y + 1, 4, why, p.t2)
        self.put(y + 3, 4, "L", p.amber)
        self.put(y + 3, 6, "launch cookie clicker via steam", p.t2)
        self.put(y + 4, 4, "q", p.amber)
        self.put(y + 4, 6, "close panel", p.t2)
        if not self.patched:
            self.put(y + 6, 4, "start.js is not patched: the window can't be hidden.", p.bad)
            self.put(y + 7, 4, "P", p.amber)
            self.put(y + 7, 6, "patch it (game must be closed)", p.t2)

    def cols(self, W):
        lw = min(48, (W - 7) // 2)
        lx, rx = 2, 2 + lw + 3
        return lx, lw, rx, W - rx - 2

    # -- "since you left"
    def since_line(self):
        if self.welcome is None and self.last_seen:
            rows = self.c.history(7 * 86400)
            rows = [r for r in rows if r[0] >= self.last_seen]
            if len(rows) >= 2:
                self.welcome = (f"since you left {dur(time.time() - self.last_seen)} ago: +{num(gained(rows, 3))} cookies"
                                f" · {gained(rows, 5)} purchases · {gained(rows, 6)} golden")
            elif self.c.history(7 * 86400):
                self.welcome = ""
        return self.welcome if self.welcome and time.time() - self.opened < 90 else ""

    # -- tab 1
    def tab_overview(self, s, agent, H, W):
        p = self.p
        lx, lw, rx, rw = self.cols(W)
        st = s.get("stats") or {}
        y = 2
        wl = self.since_line()
        if wl:
            self.put(y, lx, "▎", p.amber)
            self.put(y, lx + 2, wl, p.t1, maxw=W - 6)
            y += 2

        top = y
        y = self.section(y, lx, "overview", lw)
        h1 = self.c.history(3600)
        def spark_in(yy, col, label_w, val_w):
            # last hour as a tiny sparkline between the label and the value
            room = lw - label_w - val_w - 2
            if room >= 6 and len(h1) > 2:
                line = sparkline([r[col] for r in h1], room)
                self.put(yy, lx + lw - val_w - 1 - len(line), line, p.t3)
        self.put(y, lx, "cookies", p.t3)
        big = num(self.tween("cookies", s["cookies"]))
        self.put(y, lx + lw - len(big), big, p.t1 | curses.A_BOLD)
        spark_in(y, 2, 8, len(big) + 1)
        y += 1
        y = self.kv(y, lx, lw, "per second", num(s["cps"]))
        inc = num(self.tween("income", s["income"])) + "/s"
        spark_in(y, 1, 19, len(inc) + 1)
        y = self.kv(y, lx, lw, "income with clicks", inc)
        y = self.kv(y, lx, lw, "made by bot", num(s.get("botEarned")))
        lumps = s.get("lumps")
        y = self.kv(y, lx, lw, "sugar lumps", num(lumps, 0) if lumps is not None and lumps >= 0 else "locked")
        a = s.get("achievements") or [0, 0]
        y = self.kv(y, lx, lw, "achievements", f"{a[0]} / {a[1]}")
        y = self.kv(y, lx, lw, "clicks · golden", f"{num(st.get('clicks'), 0)} · {st.get('golden', 0)}")
        y = self.kv(y, lx, lw, "purchases · spells", f"{st.get('bought', 0)} · {st.get('spells', 0)}")

        # strategy
        y += 1
        y = self.section(y, lx, "strategy", lw)
        if s.get("plan"):
            self.put(y, lx, s["plan"], p.t1, maxw=lw)
            y += 1
        asc = s.get("ascend")
        if asc:
            ready = asc["ready"]
            auto = (s.get("cfg") or {}).get("autoAscend")
            hv = s.get("heaven") or {}
            if hv.get("ascending"):
                label, attr = "ascending…", p.amber
            elif auto:
                label, attr = ("auto ascend: waiting for combo to end" if ready else "auto ascend at"), p.amber if ready else p.t1
            else:
                label, attr = ("ascend now (press A)" if ready else "ascend at"), p.amber if ready else p.t3
            if auto and not hv.get("ascending"):
                self.put(y, lx, "●", p.amber)
                self.put(y, lx + 2, label, attr, maxw=lw - 22)
            else:
                self.put(y, lx, label, attr, maxw=lw - 22)
            txt = f"+{num(asc['gain'], 0)} chips" + ("" if asc["gain"] >= asc["goal"] else f" / min {num(asc['goal'], 0)}")
            self.put(y, lx + lw - len(txt), txt, p.amber if ready else p.t2)
            y += 1
            self.bar(y, lx, lw, asc["gain"] / asc["goal"] if asc["goal"] else 0, p.amber if ready else p.t2)
            y += 1
            if asc.get("why"):
                self.put(y, lx, asc["why"], p.amber if ready else p.t3, maxw=lw)
                y += 1
            if asc.get("rate") is not None:
                y = self.kv(y, lx, lw, "chips/h now · run avg", f"{asc['rate']:.1f} · {asc['avg']:.1f}",
                            p.amber if asc["rate"] < asc["avg"] else p.t1)
            if not ready and asc["gain"] < asc["goal"]:
                eta = self.ascend_eta(asc)
                if eta:
                    y = self.kv(y, lx, lw, "min reached in", eta, p.t2)
        wr = s.get("wrinklers")
        if wr and wr.get("max"):
            y = self.kv(y, lx, lw, "wrinklers", f"{wr['n']}/{wr['max']} · {num(wr['payout'])}")
        g = s.get("grimoire") or {}
        if g.get("next"):
            nx = g["next"]
            y = self.kv(y, lx, lw, "next spell", nx["name"], p.ok if nx.get("good") else p.bad)

        # log
        log = s.get("log") or []
        room = H - 3 - y - 2
        if room > 1 and log:
            y += 1
            y = self.section(y, lx, "log", lw)
            for line in log[:room]:
                self.put(y, lx, line, p.t3, maxw=lw)
                y += 1

        # right: next purchase
        ry = self.section(top, rx, "next purchase", rw)
        t = s.get("target")
        if t:
            name = ("upgrade · " if t["kind"] == "u" else "") + t["name"]
            self.put(ry, rx, "▎", p.amber)
            self.put(ry, rx + 2, name, p.t1, maxw=rw - 12)
            eta = dur(t["eta"])
            self.put(ry, rx + rw - len(eta), eta, p.amber)
            ry += 1
            info = num(t["price"]) + (f" · {t['why']}" if t.get("why") else "")
            self.put(ry, rx + 2, info, p.t3)
            if t["gain"] > 0:
                g2 = f"+{num(t['gain'])}/s"
                self.put(ry, rx + rw - len(g2), g2, p.t2)
            ry += 1
            self.bar(ry, rx + 2, rw - 2, s["cookies"] / t["price"] if t["price"] else 1, p.amber)
            ry += 1
        else:
            self.put(ry, rx + 2, "nothing queued", p.t3)
            ry += 1
        if s.get("reserve"):
            self.put(ry, rx + 2, f"lucky bank {num(s['reserve'])}", p.t3)
            ry += 1

        buffs = s.get("buffs") or []
        if buffs or s.get("combo"):
            ry += 1
            ry = self.section(ry, rx, "buffs", rw, f"combo ×{num(s['combo'])}" if s.get("combo") else "")
            for b in buffs[:4]:
                mult = b["cps"] if b["cps"] != 1 else b["click"]
                self.put(ry, rx + 2, b["name"], p.t1, maxw=rw - 22)
                self.put(ry, rx + rw - 18, f"×{num(mult)}".rjust(8), p.amber)
                self.put(ry, rx + rw - 8, dur(b["left"]).rjust(8), p.t3)
                ry += 1

        objs = s.get("objects") or []
        hist = self.c.history(3600)
        chart_h = 8 if H > 34 else 5
        room = H - 3 - ry - 2 - (chart_h + 2)
        if objs and room > 1:
            ry += 1
            total = sum(o["cps"] for o in objs) or 1
            ry = self.section(ry, rx, "buildings", rw, f"buildings: {s.get('buildings', 0)}")
            tid = t["id"] if t and t["kind"] == "b" else None
            shown = sorted(objs, key=lambda o: -o["cps"])[:room]
            tobj = next((o for o in objs if o["id"] == tid), None)
            if tobj and shown and tobj not in shown:
                shown[-1] = tobj
            shown.sort(key=lambda o: o["id"])
            barw = max(0, min(30, rw - 38))
            for o in shown:
                share = o["cps"] / total
                act = o["id"] == tid
                self.row(ry, rx, rw, o["id"] + 1, act, [
                    (o["name"], "t1", 18, "l"), (o["amount"], "t2", 5, "r"), (f"{share * 100:4.1f}%", "t3", 6, "r")])
                n = int(share * barw)
                if barw > 3 and n:
                    self.put(ry, rx + rw - barw, "▬" * n, p.r_t2 if act else p.t3)
                ry += 1

        ch = min(chart_h, H - 4 - ry - 2)
        if ch >= 3:
            ry += 1
            src = hist if len(hist) > 1 else (s.get("hist") or [])
            span = (src[-1][0] - src[0][0]) if len(src) > 1 else 0
            self.chart_box(ry, rx, rw, ch - 1, [r[0] for r in src], [r[1] for r in src], "income", dur(span) if span else "")

    def ascend_eta(self, asc):
        """Time to the ascension goal from the last hours of prestige growth."""
        rows = self.c.history(7 * 86400) or self.c.history(3600)
        if len(rows) < 10:
            return ""
        cut = rows[-1][0] - 3 * 3600
        win = [r for r in rows if r[0] >= cut] or rows
        a, b = win[0], win[-1]
        if b[4] < a[4] or b[0] <= a[0]:  # ascended inside the window
            return ""
        rate = (b[4] - a[4]) / (b[0] - a[0])
        if rate <= 0:
            return "not growing yet"
        return "~" + dur((asc["goal"] - asc["gain"]) / rate) + " at the current pace"

    # -- tab 2
    def tab_market(self, s, agent, H, W):
        p = self.p
        mk = s.get("market")
        st = s.get("stats") or {}
        y = 2
        if not mk:
            self.section(y, 2, "market", W - 4)
            self.put(y + 2, 4, "the stock market unlocks with bank level 1 (one sugar lump).", p.t3)
            return
        y = self.section(y, 2, "market", W - 4,
                         f"profit {num(st.get('marketProfit', 0))} · brokers {mk['brokers']}/{mk['maxBrokers']}")
        hdr = f"{'':4}{'good':<6}{'price':>9} {'rest':>5}  {'vs rest':<14}{'stock':>11}  {'paid':>8} {'p/l':>8}"
        self.put(y, 2, hdr, p.t3)
        y += 1
        sw = max(0, W - 4 - len(hdr) - 4)
        cfg = s.get("cfg") or {}
        buy_at = 0.4
        sell_at = 1.5
        for i, g in enumerate(mk["goods"]):
            if y >= H - 3:
                break
            held = g["stock"] > 0
            ratio = g["val"] / g["rest"] if g["rest"] else 1
            kind = "ok" if ratio <= buy_at else "bad" if ratio >= sell_at else "t1"
            pl = (g["val"] - g["paid"]) * g["stock"] if held and g.get("paid") else None
            self.row(y, 2, W - 4, i + 1, held, [
                (g["sym"], "t2", 5, "l"),
                (f"${g['val']:.2f}", kind, 8, "r"),
                (f"{g['rest']}", "t3", 5, "r"),
                ("", "t3", 14, "l"),
                (f"{g['stock']}/{g['max']}" if held else f"0/{g['max']}", "amber" if held else "t3", 10, "r"),
                (f"${g['paid']:.2f}" if held and g.get("paid") else "", "t2", 8, "r"),
                ((("+" if pl >= 0 else "") + f"{pl:.0f}$") if pl is not None else "", "ok" if pl and pl >= 0 else "bad", 8, "r"),
            ])
            # position of the price on a 0..2x resting-value gauge
            gx = 27  # column right after good/price/rest
            pos = int(min(1.0, ratio / 2) * 12)
            gauge = "".join("●" if k == pos else ("┊" if k in (int(buy_at / 2 * 12), int(sell_at / 2 * 12)) else "·")
                            for k in range(13))
            self.put(y, gx, gauge, p.r_t3 if held else p.t3)
            self.put(y, gx + pos, "●", getattr(p, ("r_" if held else "") + kind))
            if sw > 6:
                line = sparkline(list(reversed(g.get("hist") or [])), sw)
                self.put(y, W - 2 - len(line), line, p.r_t3 if held else p.t3)
            y += 1
        y += 1
        trend = "on" if cfg.get("marketCheat") else "off"
        self.put(y, 2, f"buys under {int(buy_at * 100)}% of resting value · sells over {int(sell_at * 100)}% · see trends {trend}", p.t3)

    # -- tab 3
    def tab_minigames(self, s, agent, H, W):
        p = self.p
        lx, lw, rx, rw = self.cols(W)
        y = self.section(2, lx, "grimoire", lw)
        g = s.get("grimoire")
        if s.get("magic"):
            m, mm = s["magic"]
            self.put(y, lx, "magic", p.t3)
            self.bar(y, lx + 8, lw - 18, m / mm if mm else 0, p.t2)
            txt = f"{int(m)}/{mm}"
            self.put(y, lx + lw - len(txt), txt, p.amber if s.get("spellReady") else p.t1)
            y += 1
            st = s.get("stats") or {}
            y = self.kv(y, lx, lw, "spells cast by bot", str(st.get("spells", 0)))
            if g and g.get("next"):
                nx = g["next"]
                y = self.kv(y, lx, lw, "next hand of fate", nx["name"], p.ok if nx.get("good") else p.bad)
            if g and g.get("queue"):
                self.put(y, lx, "after that", p.t3)
                q = " · ".join(g["queue"][:4])
                self.put(y, lx + lw - len(q), q, p.t2, maxw=lw - 12)
                y += 1
        else:
            self.put(y, lx, "wizard tower level 1 unlocks it", p.t3)
            y += 1

        y += 1
        y = self.section(y, lx, "pantheon", lw)
        pan = s.get("pantheon")
        if pan:
            for i, name in enumerate(pan["slots"]):
                label = ["diamond", "ruby", "jade"][i]
                y = self.kv(y, lx, lw, label, name or "empty", p.t1 if name else p.t3)
            y = self.kv(y, lx, lw, "swaps left", str(pan.get("swaps", 0)))
        else:
            self.put(y, lx, "temple level 1 unlocks it", p.t3)
            y += 1

        y += 1
        y = self.section(y, lx, "sugar lumps", lw)
        lumps = s.get("lumps")
        if lumps is not None and lumps >= 0:
            y = self.kv(y, lx, lw, "lumps", num(lumps, 0))
            if s.get("lumpRipe") is not None:
                y = self.kv(y, lx, lw, "next ripe in", dur(s["lumpRipe"]))
        else:
            self.put(y, lx, "unlock at 1 billion cookies baked", p.t3)
            y += 1

        hv = s.get("heaven")
        if hv and y < H - 6:
            y += 1
            y = self.section(y, lx, "heaven", lw, f"upgrades: {hv['owned']}/{hv['total']}")
            y = self.kv(y, lx, lw, "prestige · chips", f"{num(hv['prestige'], 0)} · {num(hv['chips'], 0)}")
            y = self.kv(y, lx, lw, "ascending now gives", f"+{num(hv['gain'], 0)} chips", p.amber if hv["gain"] else None)
            if hv.get("after"):
                self.put(y, lx, "you would buy", p.t3)
                y += 1
                for name in hv["after"][:max(1, H - 4 - y)]:
                    self.put(y, lx + 2, "· " + name, p.t2, maxw=lw - 2)
                    y += 1

        # right column
        ry = self.section(2, rx, "dragon", rw)
        dr = s.get("dragon")
        if dr:
            ry = self.kv(ry, rx, rw, "stage", f"{dr['level']} · {dr['name']}")
            if dr.get("next"):
                ry = self.kv(ry, rx, rw, "next", dr["next"], p.t2)
            ry = self.kv(ry, rx, rw, "aura", dr.get("aura") or "none")
            if dr.get("aura2"):
                ry = self.kv(ry, rx, rw, "second aura", dr["aura2"])
        else:
            self.put(ry, rx, "unlocks with the heavenly upgrade “how to bake your dragon”", p.t3, maxw=rw)
            ry += 1

        ry += 1
        ry = self.section(ry, rx, "garden", rw)
        gd = s.get("garden")
        if gd:
            ry = self.kv(ry, rx, rw, "plots", f"{gd['planted']}/{gd['plots']} planted · {gd['mature']} mature")
            ry = self.kv(ry, rx, rw, "seeds known", f"{gd['seeds']} / {gd['seedsTotal']}")
            ry = self.kv(ry, rx, rw, "soil", gd.get("soil", "—"))
            if gd.get("planting"):
                ry = self.kv(ry, rx, rw, "planting", gd["planting"], p.t2)
            grid = gd.get("grid")
            if grid:
                ry += 1
                for line in grid:
                    if ry >= H - 3:
                        break
                    x = rx
                    for ch in line:
                        attr = p.amber if ch == "◆" else p.t1 if ch == "●" else p.t3
                        self.put(ry, x, ch + " ", attr)
                        x += 2
                    ry += 1
                self.put(ry, rx, "◆ mature  ● growing  · empty", p.t3)
                ry += 1
        else:
            self.put(ry, rx, "farm level 1 unlocks it", p.t3)

    # -- tab 4
    def tab_history(self, s, agent, H, W):
        p = self.p
        rows = self.span_bar(2)
        y = 4
        if len(rows) < 2:
            self.put(y, 2, "collecting data… the agent stores one sample every 30 s.", p.t3)
            return
        a, b = rows[0], rows[-1]
        took = b[0] - a[0]
        stats = [("cookies earned", num(gained(rows, 3))), ("purchases", str(gained(rows, 5))),
                 ("golden cookies", str(gained(rows, 6))), ("prestige gained", "+" + num(gained(rows, 4), 0)),
                 ("covered", dur(took))]
        cw = (W - 4) // len(stats)
        for i, (k, v) in enumerate(stats):
            self.put(y, 2 + i * cw, k, p.t3)
            self.put(y + 1, 2 + i * cw, v, p.t1 | curses.A_BOLD)
        y += 3
        ch = max(3, (H - y - 8) // 2)
        y = self.chart_box(y, 2, W - 4, ch, [r[0] for r in rows], [r[1] for r in rows], "income per second")
        y += 1
        self.chart_box(y, 2, W - 4, ch, [r[0] for r in rows], [r[2] for r in rows], "bank")

    # -- tab 5: charts
    def tab_charts(self, s, agent, H, W):
        p = self.p
        rows = self.span_bar(2)
        if len(rows) < 3:
            self.put(4, 2, "collecting data… the agent stores one sample every 30 s.", p.t3)
            return
        import math
        y = 4
        gap = 3
        cw = (W - 4 - gap) // 2
        lx, rx = 2, 2 + cw + gap
        ts = [r[0] for r in rows]
        avail = H - y - 3
        top_h = max(4, (avail - 12) // 2)
        bot_h = max(3, avail - top_h - 12)

        # income (log) | prestige chips
        self.chart_box(y, lx, cw, top_h, ts, [r[1] for r in rows], "income per second")
        hv = s.get("heaven") or {}
        goal = (s.get("ascend") or {}).get("goal")
        self.chart_box(y, rx, cw, top_h, ts, [r[4] for r in rows], "heavenly chips this run",
                       f"min {num(goal, 0)}" if goal else "")
        y += top_h + 2

        # activity bars: purchases | golden cookies, per bucket
        nb = max(4, cw - 8)
        t0, t1 = rows[0][0], rows[-1][0]
        step = max(30, (t1 - t0) / nb)
        def per_bucket(col):
            out = [0] * nb
            for a, b in zip(rows, rows[1:]):
                d = b[col] - a[col]
                if d > 0:
                    k = min(nb - 1, int((b[0] - t0) / step))
                    out[k] += d
            return out
        label = dur(step).replace(" 00s", "")
        y1 = self.section(y, lx, f"purchases per {label}", cw)
        self.bars(y1, lx, cw, bot_h, per_bucket(5))
        y2 = self.section(y, rx, f"golden cookies per {label}", cw)
        self.bars(y2, rx, cw, bot_h, per_bucket(6))
        y = y1 + bot_h + 1

        # where CpS comes from: one stacked bar + legend, plus growth stats
        objs = sorted(s.get("objects") or [], key=lambda o: -o["cps"])
        total = sum(o["cps"] for o in objs) or 1
        y = self.section(y, lx, "where cps comes from", W - 4)
        shades = [p.amber, p.t1, p.t2, p.t3, p.fill, p.line]
        glyphs = ["█", "▓", "▒", "░", "▒", "░"]
        bw = W - 4
        x = 2
        top6 = objs[:6]
        for i, o in enumerate(top6):
            n = int(round(o["cps"] / total * bw))
            if i == len(top6) - 1:
                n = max(0, 2 + bw - x)
            self.put(y, x, glyphs[i] * n, shades[i])
            x += n
        y += 1
        x = 2
        for i, o in enumerate(top6):
            item = f"{o['name']} {o['cps'] / total * 100:.0f}%"
            if x + len(item) + 4 > W - 2:
                break
            self.put(y, x, glyphs[i], shades[i])
            self.put(y, x + 2, item, p.t2)
            x += len(item) + 5
        y += 2

        # growth: doubling time of income over the last hour of the span
        run = since_reset(rows)
        recent = [r for r in run if r[0] >= run[-1][0] - 3600 and r[1] > 0]
        if len(recent) >= 3 and recent[-1][1] > recent[0][1]:
            k = math.log(recent[-1][1] / recent[0][1]) / max(1, recent[-1][0] - recent[0][0])
            stats = [("income doubles every", dur(math.log(2) / k)),
                     ("growth per hour", "×" + (f"{math.exp(k * 3600):.2f}" if k * 3600 < 4.6 else num(math.exp(min(k * 3600, 700)))))]
        else:
            stats = [("income doubles every", "—"), ("growth per hour", "—")]
        stats.append(("cookies earned in span", num(gained(rows, 3))))
        colw = (W - 4) // len(stats)
        for i, (k_, v) in enumerate(stats):
            self.put(y, 2 + i * colw, k_, p.t3)
            self.put(y + 1, 2 + i * colw, v, p.t1 | curses.A_BOLD)

    # -- tab 6
    def tab_settings(self, s, agent, H, W):
        p = self.p
        cfg = s.get("cfg") or {}
        y = 2
        group = None
        lw = min(W - 4, 72)
        for i, (g, key, label, hint) in enumerate(SETTING_ROWS):
            if g != group:
                if group is not None:
                    y += 1
                y = self.section(y, 2, g, lw)
                group = g
            if y >= H - 3:
                break
            on = agent.get("watchdog") if key == "@watchdog" else self.prefs.get(key[1:]) if key.startswith("@") else cfg.get(key)
            act = i == self.cursor
            base = "r_" if act else ""
            if act:
                self.put(y, 2, " " * lw, p.r_t1)
                self.put(y, 2, "▎", p.r_amber)
            self.put(y, 4, "●" if on else "○", getattr(p, base + ("t1" if on else "t3")))
            self.put(y, 6, label, getattr(p, base + ("t1" if on else "t2")))
            self.put(y, 26, hint, getattr(p, base + "t3"), maxw=lw - 26)
            y += 1

    # -- clawd (half-block pixel art: every cell shows two pixels, ▀ fg = top, bg = bottom)
    PX = {"O": 173, "H": 216, "S": 131, "E": 16, "A": 214, "C": 94, "G": 243, "K": 236,
          "W": 255, "R": 167, "Y": 228, "B": 250}

    def px(self, y, x, rows):
        """Draw a pixel sprite; '.' is transparent. rows must have an even count."""
        for r in range(0, len(rows), 2):
            top, bot = rows[r], rows[r + 1] if r + 1 < len(rows) else "." * len(rows[r])
            for i in range(max(len(top), len(bot))):
                a = top[i] if i < len(top) else "."
                b = bot[i] if i < len(bot) else "."
                if a == "." and b == ".":
                    continue
                fa, fb = self.PX.get(a, -1), self.PX.get(b, -1)
                if a == b:
                    self.put(y + r // 2, x + i, "█", self.p.rgb(fa, -1))
                elif a == ".":
                    self.put(y + r // 2, x + i, "▄", self.p.rgb(fb, -1))
                elif b == ".":
                    self.put(y + r // 2, x + i, "▀", self.p.rgb(fa, -1))
                else:
                    self.put(y + r // 2, x + i, "▀", self.p.rgb(fa, fb))

    # Clawd, 14 x 10 px (14 cols x 5 rows). Eyes look where he goes.
    def clawd_rows(self, look=0, blink=False, cheer=False, legs="a", glasses=False):
        L = 3 + look
        R = 9 + look
        def eyes(row_closed):
            s = list(".OOOOOOOOOOOO.")
            for k in (L, L + 1, R, R + 1):
                s[k] = "O" if row_closed else "E"
            if glasses:
                for k in (L - 1, L + 2, R - 1, R + 2):
                    if 0 < k < 13:
                        s[k] = "K"
            return "".join(s)
        r = ["..OOOOOOOOOO..",
             ".OHHOOOOOOOOO.",
             eyes(blink),
             eyes(False),
             "OOOOOOOOOOOOOO",
             "OOOOOOOOOOOOOO",
             ".OOOOOOOOOOOS.",
             ".SSSSSSSSSSSS."]
        r[2] = r[2][:2] + "H" + r[2][3:] if r[2][2] == "O" else r[2]
        if cheer:  # arms up instead of out
            r[0] = "O.OOOOOOOOOO.O"
            r[1] = "OOHHOOOOOOOOOO"
            r[4] = r[5] = ".OOOOOOOOOOOO."
        feet = {"a": ["..O.O....O.O..", "..O.O....O.O.."],
                "b": ["..O.O....O.O..", "...O.O..O.O..."],
                "c": ["...O.O..O.O...", "..O.O....O.O.."],
                "sit": ["..SSSS..SSSS..", ".............."]}[legs]
        return r + feet

    HATS = {
        "overview": ["...WWW..WWW...", "..WWWWWWWWWW..", "...WWWWWWWW...", "...BBBBBBBB..."],   # chef
        "market": ["....KKKKKK....", "....KKKKKK....", "....AAAAAA....", "..KKKKKKKKKK.."],     # top hat
        "minigames": ["......Y.......", ".....GGG......", "....GGGGG.....", "..GGGGGGGGG..."],  # wizard
        "charts": ["..............", ".......R......", "...RRRRRR.....", "..RRRRRRRR...."],     # beret
        "settings": ["..............", "....AAAAAA....", "...AAAAAAAA...", "..AAAAAAAAAA.."],   # hard hat
    }
    COOKIE = [[".AAAA.", "AACAAA", "AAAACA", ".AAAA."],
              [".AAAA.", "AAAACA", "ACAAAA", ".AAAA."],
              [".AAAA.", "ACAAAA", "AAACAA", ".AAAA."],
              [".AAAA.", "AAAAAA", "ACAACA", ".AAAA."]]
    COIN = [".AAAA.", "AAKAAA", "AAKAAA", ".AAAA."]
    GEM = ["..WW..", ".WAAW.", ".WAAW.", "..WW.."]

    def clawd(self, s, live, H, W):
        """Clawd lives on the footer line. He walks with his eyes leading, blinks,
        stops to look around, hops on purchases, cheers on golden cookies and
        spells, sprints in combos and naps when the bot is off. Each tab gives
        him a costume and a job: chef pushing a cookie, trader in a top hat under
        a price ticker, wizard with sparkles, analyst in glasses at a chart
        board, painter in a beret leaving a trail of bars, mechanic in a hard hat
        at a workbench."""
        p, c = self.p, self.cl
        f = self.f
        top, base = H - 10, H - 3          # lane rows; bottom sprite row on `base`
        for yy in range(top, H - 2):
            self.put(yy, 0, " " * (W - 1))
        scene = TABS[self.tab] if s else "offline"
        st = (s or {}).get("stats") or {}
        log0 = ((s or {}).get("log") or [""])[0]

        cur = (st.get("bought", 0), st.get("golden", 0), st.get("spells", 0), log0)
        if c["prev"] is not None and s:
            db, dg, ds = cur[0] - c["prev"][0], cur[1] - c["prev"][1], cur[2] - c["prev"][2]
            if db > 0:
                c["jump"] = 4
                c["fx"].append({"t": "+" + str(db), "x": c["x"] + 5, "age": 0, "a": p.amber})
            if dg > 0:
                c["fast"] = 30
                c["cheer"] = 10
                c["fx"].append({"t": "✦", "x": c["x"] + 6, "age": 0, "a": p.amber | curses.A_BOLD})
            if ds > 0:
                c["cheer"] = 12
                for k in range(6):
                    c["fx"].append({"t": "✧*·"[k % 3], "x": c["x"] + k * 2, "age": -k, "a": p.amber})
            if cur[3] != c["prev"][3] and "market:" in str(cur[3]):
                c["fx"].append({"t": "$", "x": c["x"] + 6, "age": 0, "a": p.ok})
        c["prev"] = cur

        asleep = not s or not s.get("enabled") or not live
        combo = bool(s and s.get("combo"))
        busy = scene in ("history", "settings")
        SW = 14
        lo, hi = 1, W - SW - 9
        prop_x = W - 24
        spot = prop_x - SW - 1

        if not c.get("pause") and not asleep and not combo and not busy and f % 140 == 70:
            c["pause"] = 24
        paused = c.get("pause", 0) > 0
        c["pause"] = max(0, c.get("pause", 0) - 1)

        c["x"] = max(lo, min(hi, c["x"]))          # terminal may have been resized
        if not asleep and not paused:
            if busy and abs(c["x"] - spot) <= 1:
                c["x"], c["dir"] = spot, 1
            else:
                if busy:
                    c["dir"] = 1 if spot > c["x"] else -1
                step = 1 if (combo or c["fast"] > 0 or busy or f % 2 == 0) else 0
                c["x"] += c["dir"] * step
                if c["x"] <= lo:
                    c["dir"] = 1           # turn only when pointing outward: no edge jitter
                elif c["x"] >= hi:
                    c["dir"] = -1
                c["x"] = max(lo, min(hi, c["x"]))
        c["fast"] = max(0, c["fast"] - 1)
        x = c["x"]
        at_spot = busy and x == spot

        lift = (0, 1, 1, 1, 0)[4 - c["jump"]] if c["jump"] > 0 else 0
        c["jump"] = max(0, c["jump"] - 1)
        cheer = c.get("cheer", 0) > 0 or (combo and f % 6 < 3)
        c["cheer"] = max(0, c.get("cheer", 0) - 1)
        blink = f % 45 in (0, 1, 2) or asleep
        look = c["dir"] if not paused else (-1, 0, 1, 0)[(f // 6) % 4]
        if at_spot:
            look = 1
        if asleep:
            legs = "sit"
        elif paused or at_spot or lift:
            legs = "a"
        else:
            legs = ("b", "c")[(f // (1 if combo else 2)) % 2]
        bob = 0
        if scene == "settings" and at_spot:   # hammering: arms swing up and down
            cheer = f % 6 < 3

        # --- scene props behind him
        if scene == "market" and s and s.get("market"):
            goods = s["market"]["goods"]
            tape = "   ".join(f"{g['sym']} ${g['val']:.2f} {'▲' if g['val'] < g['rest'] else '▼'}" for g in goods) + "   "
            off = (f // 2) % max(1, len(tape))
            line = (tape * 3)[off:off + W - 4]
            self.put(top, 2, line, p.t3)
            for i, ch in enumerate(line):
                if ch in "▲▼":
                    self.put(top, 2 + i, ch, p.ok if ch == "▲" else p.bad)
        elif scene == "history":
            vals = [r[1] for r in self.c.history(3600)][-18:]
            board = sparkline(vals, 18) if len(vals) > 2 else "▁▂▃▄▅▆▇█"
            self.put(base - 3, prop_x, "┌" + "─" * 18 + "┐", p.line)
            self.put(base - 2, prop_x, "│ income · 1h" + " " * 6 + "│", p.line)
            self.put(base - 1, prop_x, "│", p.line)
            self.put(base - 1, prop_x + 1, board.ljust(18), p.t2)
            self.put(base - 1, prop_x + 19, "│", p.line)
            self.put(base, prop_x, "└" + "─" * 18 + "┘", p.line)
            if at_spot and f % 10 < 5:
                self.put(base - 1, prop_x + len(board), board[-1:] or "█", p.amber)
        elif scene == "settings":
            self.px(base - 2, prop_x, ["GGGGGGGG", "GKKKKKKG", "GKAKKAKG", "GKKKKKKG", "GGGGGGGG", "G......G"])
            if at_spot and f % 6 == 0:
                c["fx"].append({"t": "·✦*"[(f // 6) % 3], "x": prop_x - 1 + (f % 3), "age": 5, "a": p.amber})
        elif scene == "charts":
            trail = c.setdefault("trail", [])
            if not asleep and not paused and f % 2 == 0:
                inc = [r[1] for r in self.c.history(3600)][-24:] or [1]
                lvl = SPARK[1 + int((inc[(f // 2) % len(inc)] - min(inc)) / ((max(inc) - min(inc)) or 1) * 7)]
                trail.append({"x": x - 1 if c["dir"] > 0 else x + SW, "ch": lvl, "age": 0})
            keep = []
            for b in trail:
                if b["age"] < 50:
                    self.put(base, b["x"], b["ch"], p.amber if b["age"] < 6 else p.t2 if b["age"] < 25 else p.fill)
                    b["age"] += 1
                    keep.append(b)
            c["trail"] = keep[-80:]

        # --- Clawd himself (5 rows) + costume (2 rows above)
        y_body = base - 4 - lift + bob
        self.px(y_body, x, self.clawd_rows(look, blink, cheer, legs, glasses=(scene == "history")))
        hat = self.HATS.get(scene)
        if hat:
            if scene == "minigames" and f % 8 >= 4:
                hat = [hat[0].replace("Y", "A")] + hat[1:]
            self.px(y_body - 2, x, hat)

        # what he carries (rolls / bounces next to him)
        if not asleep and not busy and scene not in ("charts",):
            item = self.COIN if scene == "market" else self.GEM if scene == "minigames" else self.COOKIE[(f // 2) % 4]
            ix = x + SW if c["dir"] > 0 else x - 6
            self.px(base - 1, ix, item)

        if scene == "minigames" and not asleep and f % 3 == 0:
            c["fx"].append({"t": "·*✧"[(f // 3) % 3], "x": x - 1 if c["dir"] > 0 else x + SW, "age": 3, "a": p.amber})
        if asleep:
            z = (f // 5) % 3
            self.put(max(top, y_body - 1 - z), x + SW - 1, "z" if z < 2 else "Z", p.t3)
        if combo and f % 4 < 2:
            self.put(top, x + 3, "combo!", p.amber | curses.A_BOLD)

        alive = []
        for e in c["fx"]:
            e["age"] += 1
            if e["age"] < 0:
                alive.append(e)
                continue
            yy = y_body - 2 - e["age"] // 3
            if yy >= top and e["age"] < 14:
                self.put(yy, e["x"], e["t"], e["a"] if e["age"] < 9 else p.t3)
                alive.append(e)
        c["fx"] = alive[-14:]

    # -- footer
    def footer(self, s, agent, H, W):
        p = self.p
        self.hline(H - 2, 0, W - 1)
        if not s:
            keys = [("L", "launch"), ("q", "close")] + ([("P", "patch")] if not self.patched else [])
        elif TABS[self.tab] == "settings":
            keys = [("↑↓", "move"), ("space", "toggle"), ("1-6", "tabs"), ("q", "close")]
        elif TABS[self.tab] in ("history", "charts"):
            keys = [("←→", "span"), ("1-6", "tabs"), ("q", "close")]
        else:
            keys = [("1-6", "tabs"), ("b", "bot"), ("w", "window"), ("s", "save"), ("A", "ascend"), ("QQ", "quit game + exit"), ("q", "close")]
        x = 1
        for k, label in keys:
            x += self.put(H - 1, x, k, p.amber)
            x += self.put(H - 1, x, " " + label + "   ", p.t3)
        if self.flash and time.time() - self.flash_t < 4:
            self.put(H - 1, max(x, W - len(self.flash) - 2), self.flash, p.amber)
        elif agent:
            info = f"agent up {dur(time.time() - agent.get('started', time.time()))}"
            if agent.get("relaunches"):
                info += f" · relaunches: {agent['relaunches']}"
            if W - len(info) - 2 > x:
                self.put(H - 1, W - len(info) - 2, info, p.t3)

    # -- input
    def key(self, ch):
        s, live, agent, ok = self.c.snapshot()
        if ch in (ord("q"), 27):
            return False
        if ch == ord("Q"):
            # Q Q: save + fully close the game, then leave the panel too.
            if time.time() - self.quit_armed < 3:
                if live:
                    self.c.push({"op": "quit"})
                return False
            self.quit_armed = time.time()
            self.say("press Q again: close the game and exit" if live else "press Q again to exit (game not running)")
            return True
        if ord("1") <= ch <= ord(str(len(TABS))):
            self.tab = ch - ord("1")
            return True
        if ch == 9:  # Tab
            self.tab = (self.tab + 1) % len(TABS)
            return True
        if ch == curses.KEY_BTAB:
            self.tab = (self.tab - 1) % len(TABS)
            return True
        c = chr(ch) if 0 <= ch < 256 else ""
        if c == "L":
            self.say(self.c.push({"op": "launch"}))
            return True
        if c == "P":
            if game_running():
                self.say("close the game before patching")
            else:
                r = subprocess.run([sys.executable, str(PATCHER)], capture_output=True, text=True)
                self.patched = patched()
                self.say("patched" if self.patched else (r.stdout.strip() or "patch failed")[:60])
            return True
        name = TABS[self.tab]
        if name in ("history", "charts") and ch in (curses.KEY_LEFT, curses.KEY_RIGHT, ord("h"), ord("l")):
            d = -1 if ch in (curses.KEY_LEFT, ord("h")) else 1
            self.span_i = (self.span_i + d) % len(SPANS)
            return True
        if name == "settings":
            if ch in (curses.KEY_UP, ord("k")):
                self.cursor = (self.cursor - 1) % len(SETTING_ROWS)
                return True
            if ch in (curses.KEY_DOWN, ord("j")):
                self.cursor = (self.cursor + 1) % len(SETTING_ROWS)
                return True
            if ch in (ord(" "), 10, 13, curses.KEY_ENTER):
                key = SETTING_ROWS[self.cursor][1]
                if key == "@watchdog":
                    self.say(self.c.push({"op": "watchdog"}))
                elif key.startswith("@"):
                    self.prefs[key[1:]] = not self.prefs.get(key[1:])
                    self.scr.timeout(100 if self.prefs.get("anim") or self.prefs.get("clawd") else 250)
                elif live:
                    self.c.push({"op": "toggle", "key": key})
                else:
                    self.say("game not connected")
                return True
        if not live:
            if c:
                self.say("game not connected")
            return True
        win = s.get("window") or {}
        if c == "b":
            self.c.push({"op": "bot"})
        elif c == "w":
            if not win.get("patched"):
                self.say("start.js not patched — press P with the game closed")
            else:
                self.c.push({"op": "window", "mode": "show" if win.get("hidden") else "hide"})
        elif c == "s":
            self.c.push({"op": "save"})
            self.say("saving")
        elif c == "A":
            if ((s.get("ascend") or {}).get("gain") or 0) < 1:
                self.say("ascending now would give 0 heavenly chips — refused")
            elif time.time() - getattr(self, "asc_armed", 0) < 3:
                self.c.push({"op": "ascend"})
                self.say("ascending: pop wrinklers, ascend, shop heaven, reincarnate")
                self.asc_armed = 0
            else:
                self.asc_armed = time.time()
                g = (s.get("ascend") or {}).get("gain", 0)
                self.say(f"press A again to ascend now (+{num(g, 0)} chips)")
        return True


# Russian ЙЦУКЕН layout → the same physical keys on QWERTY, so hotkeys work
# without switching the keyboard layout.
RU = dict(zip("йцукенгшщзхъфывапролджэячсмитьбюЙЦУКЕНГШЩЗХЪФЫВАПРОЛДЖЭЯЧСМИТЬБЮ",
              "qwertyuiop[]asdfghjkl;'zxcvbnm,.QWERTYUIOP{}ASDFGHJKL:\"ZXCVBNM<>"))


def read_key(scr):
    """One key as an int (curses key code or ord of a Latin char), -1 if none."""
    try:
        k = scr.get_wch()
    except curses.error:
        return -1
    if isinstance(k, str):
        k = RU.get(k, k)
        return ord(k) if len(k) == 1 else -1
    return k


# ------------------------------------------------------------------ one-shot commands
A = {"t1": "\x1b[38;5;255m", "t2": "\x1b[38;5;248m", "t3": "\x1b[38;5;244m",
     "amber": "\x1b[38;5;214m", "bad": "\x1b[38;5;167m", "0": "\x1b[0m"}


def print_status(s, agent):
    tty = sys.stdout.isatty()

    def c(k, t):
        return A[k] + str(t) + A["0"] if tty else str(t)

    win = s.get("window") or {}
    bits = [c("t1", "bot on") if s.get("enabled") else c("bad", "bot off"),
            c("t2", "window hidden" if win.get("hidden") else "window shown")]
    print(c("amber", "●") + " " + c("t1", "cookieclicker-bot") + "  " + c("t3", f"{s.get('name', '')} bakery") +
          "  " + c("t3", " · ").join(bits))
    rows = [("cookies", num(s["cookies"])), ("income", num(s["income"]) + "/s"),
            ("made by bot", num(s.get("botEarned")))]
    asc = s.get("ascend")
    if asc:
        rows.append(("ascension", f"+{num(asc['gain'], 0)} chips · " + ("ready: " if asc["ready"] else "") + asc.get("why", "")))
    if s.get("plan"):
        rows.append(("doing", s["plan"]))
    t = s.get("target")
    if t:
        rows.append(("next purchase", f"{t['name']} · {num(t['price'])} · in {dur(t['eta'])}"))
    mk = s.get("market")
    if mk:
        held = sum(1 for g in mk["goods"] if g["stock"] > 0)
        rows.append(("market", f"profit {num((s.get('stats') or {}).get('marketProfit', 0))} · positions: {held}"))
    if agent:
        rows.append(("agent", f"up {dur(time.time() - agent.get('started', time.time()))} · watchdog "
                              + ("on" if agent.get("watchdog") else "off")))
    for k, v in rows:
        print("  " + c("t3", f"{k:<16}") + c("t1", v))
    if s.get("log"):
        print("  " + c("t3", f"{'last':<16}") + c("t2", s["log"][0]))


def one_shot(cmd, extra):
    if cmd == "agent":
        if extra == "stop":
            try:
                http("POST", "/shutdown")
                print("agent stopped")
            except (urllib.error.URLError, OSError):
                print("agent is not running")
            return 0
        print("agent " + ("running" if agent_alive() else "not running"))
        return 0
    if not ensure_agent():
        print("could not start the agent")
        return 1
    for _ in range(15):  # give a freshly started agent time to hear from the mod
        d = http("GET", "/state")
        if d.get("live"):
            break
        time.sleep(0.25)
    if not d.get("live"):
        print("game not connected" + ("" if game_running() else " (not running)"))
        return 1
    s = d["state"]
    if cmd == "status":
        print_status(s, d.get("agent"))
        return 0
    if cmd in ("show", "hide") and not (s.get("window") or {}).get("patched"):
        print("start.js is not patched; open the panel and press P with the game closed")
        return 1
    op = {"show": {"op": "window", "mode": "show"}, "hide": {"op": "window", "mode": "hide"},
          "save": {"op": "save"}, "quit": {"op": "quit"}}[cmd]
    http("POST", "/cmd", op)
    print({"show": "window shown", "hide": "window hidden", "save": "game saved",
           "quit": "saving and closing the game"}[cmd])
    return 0


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", choices=["status", "show", "hide", "save", "quit", "agent"])
    ap.add_argument("extra", nargs="?")
    ap.add_argument("--launch", action="store_true", help="start the game via Steam if it is not running")
    args = ap.parse_args()

    if args.command:
        sys.exit(one_shot(args.command, args.extra))

    if not ensure_agent():
        sys.exit("could not start the agent (is an old panel still holding port %d?)" % PORT)
    if args.launch and not game_running():
        http("POST", "/cmd", {"op": "launch"})

    client = Client()

    def run(scr):
        panel = Panel(scr, client)
        try:
            while True:
                panel.draw()
                ch = read_key(scr)
                if ch == curses.KEY_RESIZE or ch == -1:
                    continue
                if not panel.key(ch):
                    break
        finally:
            panel.close()

    os.environ.setdefault("ESCDELAY", "25")  # make Esc close the panel instantly

    def bye(*_):  # closing the terminal window (SIGHUP) still records "last seen"
        raise KeyboardInterrupt
    signal.signal(signal.SIGHUP, bye)
    signal.signal(signal.SIGTERM, bye)
    try:
        curses.wrapper(run)
    except KeyboardInterrupt:
        pass
    finally:
        client.stop = True


if __name__ == "__main__":
    main()

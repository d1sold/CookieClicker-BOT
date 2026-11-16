#!/usr/bin/env python3
"""Cookie Bot agent — the always-on link between the game and the terminal.

Runs detached in the background (the panel starts it on demand) and owns
http://127.0.0.1:17777:

  POST /sync       the Cookie Bot mod posts a snapshot, gets queued commands
  GET  /state      latest snapshot + agent info          (panel, status)
  GET  /history    ?span=<seconds> long-term samples     (panel charts)
  POST /cmd        queue a command for the mod, or an agent command
  POST /shutdown   stop the agent

History is kept in ~/.local/share/cookiebot/history.jsonl (one sample per 30 s,
7 days). A watchdog relaunches the game through Steam if it disappears without
a requested quit, so the bot keeps farming overnight.
"""
import json
import os
import subprocess
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

APP_ID = "1454400"
PORT = int(os.environ.get("COOKIEBOT_PORT", "17777"))
DATA = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "cookiebot"
HISTORY = DATA / "history.jsonl"
LOG = DATA / "agent.log"
SAMPLE_EVERY = 30          # seconds between history samples
KEEP = 7 * 86400           # history retention
WATCH_EVERY = 30           # watchdog poll
DEAD_AFTER = 120           # game missing this long -> relaunch
RELAUNCH_GAP = 600         # at most one relaunch per 10 min


def log(msg):
    DATA.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")


def game_running():
    return subprocess.run(["pgrep", "-f", r"Cookie Clicker\.exe"], capture_output=True).returncode == 0


def notify(title, body):
    """Desktop notification (best effort)."""
    try:
        subprocess.Popen(["notify-send", "-a", "Cookie Bot", "-i", "applications-games", title, body],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def num(x):
    for unit in ["", "K", "M", "B", "T", "Qa", "Qi", "Sx", "Sp", "Oc", "No"]:
        if abs(x) < 1000:
            return f"{x:.3g}{unit}"
        x /= 1000
    return f"{x:.2e}"


def launch_game():
    subprocess.Popen(["steam", f"steam://rungameid/{APP_ID}"], stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


class Agent:
    def __init__(self):
        self.lock = threading.Lock()
        self.state = None
        self.last = 0.0
        self.cmds = []
        self.started = time.time()
        self.hist = deque()
        self.last_sample = 0.0
        self.quit_requested = False
        self.watchdog = True
        self.missing_since = None
        self.last_relaunch = 0.0
        self.relaunches = 0
        self.was_ready = None
        self.was_ascending = False
        self.load_history()

    # ---- history
    def load_history(self):
        if not HISTORY.exists():
            return
        cut = time.time() - KEEP
        keep = []
        with HISTORY.open() as f:
            for line in f:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row[0] >= cut:
                    keep.append(row)
        self.hist.extend(keep)
        # rewrite trimmed file so it never grows past the retention window
        with HISTORY.open("w") as f:
            for row in keep:
                f.write(json.dumps(row) + "\n")

    def sample(self, s):
        now = time.time()
        if now - self.last_sample < SAMPLE_EVERY:
            return
        self.last_sample = now
        st = s.get("stats") or {}
        row = [int(now), s.get("income", 0), s.get("cookies", 0), s.get("earned", 0),
               s.get("prestigeGain", 0), st.get("bought", 0), st.get("golden", 0)]
        self.hist.append(row)
        cut = now - KEEP
        while self.hist and self.hist[0][0] < cut:
            self.hist.popleft()
        DATA.mkdir(parents=True, exist_ok=True)
        with HISTORY.open("a") as f:
            f.write(json.dumps(row) + "\n")

    # ---- mod link
    def live(self):
        return self.state is not None and time.time() - self.last < 3

    def accepts(self, s):
        """Stick to one game instance; a Steam game beats a browser copy."""
        cur = self.state
        if cur is None or time.time() - self.last > 5:
            return True
        if s.get("instance") == cur.get("instance"):
            return True
        return bool(s.get("steam")) and not cur.get("steam")

    def sync(self, s):
        with self.lock:
            if not self.accepts(s):
                return None
            self.state = s
            self.last = time.time()
            out, self.cmds = self.cmds, []
        self.sample(s)
        self.watch_events(s)
        return out

    def watch_events(self, s):
        """Notify on ascension readiness and on ascensions the bot performs."""
        asc = s.get("ascend") or {}
        ready = bool(asc.get("ready"))
        if self.was_ready is False and ready:
            auto = (s.get("cfg") or {}).get("autoAscend")
            notify("Можно возноситься",
                   f"+{num(asc.get('gain', 0))} небесных фишек"
                   + (" — бот вознесётся сам" if auto else " — нажми A в CookieClicker"))
            log("ascension ready: +%s" % asc.get("gain"))
        self.was_ready = ready
        hv = s.get("heaven") or {}
        if hv.get("ascending") and not self.was_ascending:
            notify("Вознесение", f"престиж {num(hv.get('prestige', 0))}, покупаю небесные апгрейды")
            log("ascending")
        self.was_ascending = bool(hv.get("ascending"))

    def command(self, c):
        op = c.get("op")
        if op == "launch":
            if game_running():
                return "already running"
            self.quit_requested = False
            launch_game()
            log("launch requested")
            return "launching"
        if op == "watchdog":
            self.watchdog = bool(c.get("on", not self.watchdog))
            return "watchdog " + ("on" if self.watchdog else "off")
        if op == "ascend":
            # Third guard (after the panel and the mod): old panels can still send this.
            gain = ((self.state or {}).get("ascend") or {}).get("gain", 0) or 0
            if gain < 1:
                log("ascend refused: +0 chips")
                return "refused: ascending now gives 0 heavenly chips"
        if op == "quit":
            self.quit_requested = True  # don't let the watchdog bring it back
            log("quit requested")
        with self.lock:
            self.cmds.append(c)
        return "queued"

    def info(self):
        return {"started": self.started, "watchdog": self.watchdog, "relaunches": self.relaunches,
                "quitRequested": self.quit_requested, "pid": os.getpid()}

    # ---- watchdog
    def watch(self):
        while True:
            time.sleep(WATCH_EVERY)
            try:
                running = game_running()
                now = time.time()
                if running:
                    # The game came back after being gone (user started it):
                    # a previous quit request no longer applies.
                    if self.missing_since is not None:
                        self.quit_requested = False
                    self.missing_since = None
                    continue
                if self.missing_since is None:
                    self.missing_since = now
                if (self.watchdog and not self.quit_requested and now - self.missing_since > DEAD_AFTER
                        and now - self.last_relaunch > RELAUNCH_GAP):
                    self.last_relaunch = now
                    self.relaunches += 1
                    log("game missing for %ds, relaunching" % (now - self.missing_since))
                    notify("Cookie Bot", "игра закрылась — перезапускаю")
                    launch_game()
            except Exception as e:  # never let the watchdog die
                log("watchdog error: %r" % e)


def make_handler(agent, server_ref):
    class H(BaseHTTPRequestHandler):
        def _send(self, code, obj=None):
            body = json.dumps(obj if obj is not None else {}).encode()
            self.send_response(code)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}")

        def do_OPTIONS(self):
            self._send(204)

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/state":
                with agent.lock:
                    s, last = agent.state, agent.last
                return self._send(200, {"state": s, "age": time.time() - last if last else None,
                                        "live": agent.live(), "agent": agent.info()})
            if u.path == "/history":
                q = parse_qs(u.query)
                span = float(q.get("span", ["3600"])[0])
                cut = time.time() - span
                rows = [r for r in list(agent.hist) if r[0] >= cut]
                return self._send(200, {"rows": rows})
            if u.path == "/ping":
                return self._send(200, {"ok": True, "agent": agent.info()})
            self._send(404)

        def do_POST(self):
            try:
                body = self._body()
            except ValueError:
                return self._send(400)
            if self.path == "/sync":
                cmds = agent.sync(body)
                if cmds is None:
                    return self._send(409, {"error": "another game instance is connected"})
                return self._send(200, {"cmds": cmds})
            if self.path == "/cmd":
                return self._send(200, {"result": agent.command(body)})
            if self.path == "/shutdown":
                self._send(200, {"ok": True})
                log("shutdown requested")
                threading.Thread(target=server_ref[0].shutdown, daemon=True).start()
                return
            self._send(404)

        def log_message(self, *a):
            pass

    return H


def main():
    agent = Agent()
    ref = [None]
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), make_handler(agent, ref))
    except OSError:
        print("agent already running (port %d busy)" % PORT)
        return 1
    ref[0] = srv
    threading.Thread(target=agent.watch, daemon=True).start()
    log("agent started pid=%d port=%d" % (os.getpid(), PORT))
    try:
        srv.serve_forever()
    finally:
        log("agent stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())

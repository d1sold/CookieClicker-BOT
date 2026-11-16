#!/usr/bin/env python3
"""Patch Cookie Clicker's Electron main process (resources/app/start.js).

Adds two things the console mode needs:
  1. backgroundThrottling:false - a hidden window keeps full-speed timers,
     so the bot still clicks 20-50 times per second.
  2. IPC request 'cb window' - lets the mod hide/show/minimize the window.

Steam hours and achievements keep working: the game process still runs
under Steam and still talks to Steam through greenworks.

Usage:  patch_game.py            apply (idempotent, keeps start.js.cb-backup)
        patch_game.py --restore  put the original start.js back
        patch_game.py --check    exit 0 if patched, 1 if not

Steam updates and "Verify integrity" restore the original file; run this again.
"""
import shutil
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[4]  # .../resources/app
START = APP / "start.js"
BACKUP = APP / "start.js.cb-backup"
MARK = "/*cookieBot-patch*/"

THROTTLE_OLD = "affinity:'Cookie Clicker',"
THROTTLE_NEW = "affinity:'Cookie Clicker'," + MARK + "backgroundThrottling:false,"

HANDLER_OLD = "else if (req=='fullscreen')"
HANDLER_NEW = (
    MARK
    + "else if (req=='cb window'){if(args.mode=='hide')win.hide();"
    "else if(args.mode=='show'){win.show();win.focus();}"
    "else if(args.mode=='minimize')win.minimize();"
    "send('cb window',{visible:win.isVisible()},callback);}\n\t\t\t"
    + HANDLER_OLD
)


def is_patched(src: str) -> bool:
    return MARK in src


def apply() -> int:
    src = START.read_text(encoding="utf-8")
    if is_patched(src):
        print("already patched:", START)
        return 0
    for old in (THROTTLE_OLD, HANDLER_OLD):
        if src.count(old) != 1:
            print(f"start.js changed after a game update; anchor not found: {old!r}")
            return 2
    if not BACKUP.exists():
        shutil.copy2(START, BACKUP)
    src = src.replace(THROTTLE_OLD, THROTTLE_NEW).replace(HANDLER_OLD, HANDLER_NEW)
    START.write_text(src, encoding="utf-8")
    print("patched:", START, "(backup:", BACKUP.name + ")")
    return 0


def restore() -> int:
    if not BACKUP.exists():
        print("no backup found; nothing to restore")
        return 1
    shutil.copy2(BACKUP, START)
    BACKUP.unlink()
    print("restored original start.js")
    return 0


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--restore":
        sys.exit(restore())
    if arg == "--check":
        sys.exit(0 if is_patched(START.read_text(encoding="utf-8")) else 1)
    sys.exit(apply())

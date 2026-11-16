#!/bin/sh
# Cookie Bot installer — run again after a Steam update or "Verify integrity".
#   * re-applies the start.js patch (hidden window + full-speed timers)
#   * (re)creates the CookieClicker command in ~/.local/bin
# Safe to run any time; close the game first if the patch has to be re-applied.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)

python3 "$HERE/tui/patch_game.py"

mkdir -p "$HOME/.local/bin"
cat > "$HOME/.local/bin/CookieClicker" <<WRAP
#!/bin/sh
# Cookie Bot terminal UI: starts Cookie Clicker via Steam (if not running) and opens the console panel.
exec python3 "$HERE/tui/cookie_tui.py" --launch "\$@"
WRAP
chmod +x "$HOME/.local/bin/CookieClicker"
echo "CookieClicker command: $HOME/.local/bin/CookieClicker"
echo "done — restart the game if it was running"

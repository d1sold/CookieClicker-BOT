# Cookie Bot

A bot for Steam Cookie Clicker that plays in the background while Steam keeps
counting hours and achievements, with a DASH-styled terminal panel.

## Parts

| Path | What it is |
| --- | --- |
| `main.js`, `info.txt` | the game mod: strategy, golden cookies, spells, market, garden, dragon, ascension, bridge to the agent |
| `tui/cookie_agent.py` | background agent on `127.0.0.1:17777`: keeps the link to the game, 7-day history, watchdog, desktop notifications |
| `tui/cookie_tui.py` | the `CookieClicker` panel and one-shot commands |
| `tui/patch_game.py` | patches the game's `start.js` (hide window, full-speed timers) |
| `sim/sim.js` | simulator: real game logic + real bot on a virtual clock, for A/B tests |
| `install.sh` | re-applies the patch and the `CookieClicker` command |
| `PLAN.md` | work log, experiment results, ideas |

## Use

```sh
CookieClicker              # panel (starts agent + game if needed)
CookieClicker status       # short status
CookieClicker show|hide    # game window
CookieClicker save|quit    # quit = save and fully close (watchdog stands down)
CookieClicker agent stop
```

Panel keys: `1-5` tabs, `b` bot, `w` window, `s` save, `A A` ascend, `Q Q` quit
game, `q` close panel. Settings tab: arrows + space.

## After a Steam update

Steam may restore the original `start.js`. Close the game and run `./install.sh`.
To undo the patch: `python3 tui/patch_game.py --restore`.

## Simulator

Serve `resources/app` (`python3 -m http.server 8777`), open `/src/index.html`,
evaluate `sim/sim.js`, then `await CBSim.run({ save, hours, seed, cfg })`.
Do one short warm-up run first; after that runs with the same seed repeat
exactly. Golden-cookie luck dominates short runs, so compare over many seeds.

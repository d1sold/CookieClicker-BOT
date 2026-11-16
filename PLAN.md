# Cookie Bot — overnight plan (2026-11-15 → 16)

Goal: a bot that plays Steam Cookie Clicker on its own, hidden in the
background, with a DASH terminal panel to look in. Steam hours and achievements
keep counting because the real game process runs.

Status legend: [ ] todo · [~] in progress · [x] done

## 1. Agent daemon — the bot never loses its controller
Today the terminal panel *is* the server: close it and nobody stores history,
nobody can send commands, nobody notices a crash.
- [x] `cookie_agent.py`: background process (started by `CookieClicker`, detached
      with setsid, single instance via the port) that owns 127.0.0.1:17777.
      - `/sync` for the mod (as now, one-instance lock, Steam wins)
      - `/state`, `/history?span=`, `/cmd` for clients (panel, status, scripts)
      - history persisted to `~/.local/share/cookiebot/history.jsonl` every 30 s
        (income, bank, cookies earned, prestige gain) — survives restarts
      - session report: earned since the panel was last opened
      - watchdog (option, on): game process gone for > 2 min and it wasn't a
        requested quit → relaunch via `steam://rungameid/1454400`
- [x] `CookieClicker` = panel (client of the agent; starts the agent if needed)
      `CookieClicker status|show|hide|save|quit` talk to the agent
      `CookieClicker agent stop` stops the agent (and the watchdog)

## 2. Terminal panel v4
- [x] tabs: `1 overview` · `2 market` · `3 minigames` · `4 history`
- [x] history tab: income / bank charts for 1 h · 6 h · 24 h from the agent
- [x] "since you left" line: cookies earned, purchases, golden cookies
- [x] DASH rules kept: graphite, lowercase titles, amber only on active

## 3. Strategy v4 (inside the mod)
- [x] Force the Hand of Fate prediction: the game seeds the spell RNG with
      `Game.seed + '/' + spellsCastTotal`, so the next outcome is known.
      Cast when the next outcome is good (click frenzy, building special,
      frenzy, lucky…) and hold when it is a backfire. Option `spellPredict`.
- [x] combo awareness: during Click frenzy / Dragonflight / Building special
      click at max rate and cast if the predicted outcome stacks.
- [x] dragon: hatch and train levels that cost cookies only (never the
      building-sacrifice levels), then set the best aura available.
- [x] free achievements that raise milk (→ kittens → CpS): news ticker
      clicks (Tabloid addiction), the tiny cookie, bakery name, etc. — by doing
      the in-game action, never by calling Game.Win.
- [x] Elder Pledge policy stays "don't" (wrinklers + grandma upgrades win).

## 4. Garden (basic, safe)
- [x] when the garden is unlocked: keep soil on fertilizer, plant the best
      cheap seed you own in empty tiles (Baker's wheat early), harvest only
      mature plants, never use the sacrifice button.

## 5. Ship
- [x] test on a local copy of the game in the browser (and close that tab!)
- [x] restart the Steam game with the final build, background mode on,
      agent running, verify snapshots arrive and purchases happen.

## Log
- 18:5x plan written.
- 19:10 agent + panel v4 (5 tabs, settings list, history charts, since-you-left) done, tested in a pty with a fake mod.
- 19:13 strategy v4: hand-of-fate predictor (40/40 exact on a test copy), skip bad outcomes with Haggler's Charm, hold click frenzy for a Frenzy; dragon (cookie stages + first aura); garden (wheat in even columns, harvest new mature plants); lumps also unlock the garden; free achievements (ticker x50, tiny cookie).
- 19:15 shipped: old panel stopped, agent started, game saved+quit and relaunched through the agent; new mod connected, window hidden, purchases running.
- 20:49 live fix: lucky bank stalled buying right after Lucky day (36B reserve at 7M/s). Now only after Get lucky, and purchases repaying in < 10 min skip the reserve. Game restarted via agent.
- 22:22 income figure used the 50/s combo click rate during buffs (status showed 39.8M vs real ~21M); now uses the normal rate. Game restarted via agent.
- 00:27 income still swung 3-5x with Frenzy: click value is a % of buffed CpS. Divide mouse value by the CpS buff too, so purchase ranking isn't skewed toward click upgrades during buffs. Game restarted.
- 02:50 ascension & heaven: heavenly shopping list (fixed order, pulls missing parents, prestige pool only), opt-in auto ascend, A key in the panel (double press), wrinklers popped before ascending, desktop notifications from the agent (ready / ascending / relaunch), ETA from prestige growth. Full ascend→shop→reincarnate cycle tested twice on a local copy; deployed.
- 03:05 ascension timing = marginal-value rule (ascend when chips/h now < run average; min 30 chips first run, then 10% of prestige; or doubling). Fillers: while saving for a big target, buy items that repay before it is affordable (+1.9% in a 400-min A/B from scratch; the real case was a 7 h stall on Kitten engineers). Deployed.
- 05:40 simulator (sim/sim.js): real game logic + real bot on a virtual clock (~200-500x real time), seeded RNG, starts from a frozen copy of the real save (sim/base.cki). Run: serve resources/app, open /src/index.html, eval sim.js, await CBSim.run({save, hours, seed, cfg}). One run per page load; tiny first-second differences still grow, so compare by many seeds.
  Finding: on the current save golden-cookie luck (cookie storms) swings 1-2 h results by 10-50x, far more than lucky-bank size (0 / 1500 / 6000 s). Not enough evidence to change the lucky bank; kept 6000 s (now configurable: luckyBankSeconds).
  Combos: the grimoire is still locked on the real save (first sugar lump not ripe), so spell combos can't pay off yet; revisit double-cast / Godzamok once the grimoire and temple are open.
- 05:55 first real auto-ascension: +38 chips (growth fell below the run average); heaven bought Legacy, Heavenly cookies, How to bake your dragon, Box of brand biscuits. Fixed 'made by bot' going negative after a reincarnation.
- 06:50 simulator made repeatable inside one page (internal-timestamp reset + one warm-up run). 60-run study, 20 seeds x lucky bank 0 / 1500 / 6000 s, 1 game hour each from the frozen late-run save:
  bank off vs 6000 s: geo-mean ratio 0.56 (7 wins / 13 losses, t = -1.75); 1500 vs 6000: 1.07 (8/12, t = 0.17).
  Verdict: keeping a lucky bank clearly pays (~1.8x); 1500 and 6000 s are indistinguishable. Kept 6000 s, no deploy needed.
- 07:25 "до идеала" pass: milk-aware buying (count building-count achievements) A/B'd on early and late saves, 20 seeds each: early 19/20 ties, late geo 0.98 (t = -1.5) -> shipped OFF (option milkAware). Spare sugar lumps now level the building with the best CpS per lump after the minigames are open (tested on a local copy). install.sh (re-patch + CookieClicker command), README.md, .gitignore, git repo initialised (not committed).
- 07:30 incident: a manual ascension (A A in the panel at 07:04) reset the run for +0 chips. Guarded in both the mod and the panel: ascending with less than 1 chip is refused.
- 07:40 third guard in the agent: 'ascend' with +0 chips is refused even from panels started before the fix. Verified live (refused), game farming.
- 07:35 panel: new 5th tab 'charts' — income (log) and heavenly-chips line charts, purchases / golden cookies per bucket bar charts (newest bar amber), CpS-by-building stacked bar with legend, income doubling time & growth per hour; overview got inline last-hour sparklines; history stats survive ascension resets.
- 07:45 panel animations (user asked): Clawd walks on the footer line pushing a rolling cookie, hops with a floating '+N' on purchases, sparkles on golden cookies, sprints and shouts 'combo!' during combos, naps (z Z) when the bot is off; count-up numbers; running glint on amber progress bars. Both toggleable in settings (clawd, animations); 10 fps redraw only while they are on.
- 07:45 Clawd v2: eyes follow his direction, blinks, idle look-around pauses, jump arc, arms up when cheering. Costumes per tab: overview cookie pusher; market top hat + scrolling price ticker (▲/▼ coloured), carries $; minigames wizard hat with twinkling star and sparkle trail, carries a crystal; history analyst walking to a board with the last-hour income sparkline; charts painter leaving a fading trail of income bars; settings hard hat hammering at a workbench with sparks.
- 07:55 panel keys: Q Q now saves + closes the game AND exits the panel (also works when the game is not connected — it used to do nothing then). Hotkeys work in the Russian layout (й=q, Й=Q, и=b, ц=w…); unicode-safe key reading. Tested in a pty: Q Q, й and q all exit.
- 08:05 Clawd v3: real half-block pixel art (14x10 px, terracotta with highlight + shadow, 2x2 eyes that look where he walks, blink, arms-up cheer, 3 walk frames), pixel costumes per tab (chef hat + rolling cookie, top hat + coin under the price ticker, wizard hat + gem + sparkles, analyst glasses at a chart board, beret + bar trail, hard hat at a workbench). Fixed: he got stuck jittering at the right edge (turned on every frame at the boundary); position re-clamps on terminal resize. Verified with coloured renders of the pty screen.

## Ideas for later
- Godzamok combos (sell/buy buildings during click frenzy) — powerful but needs care.
- seasons cycling for seasonal upgrades; garden mutation planner (targeted seed breeding).

// Cookie Bot simulator — runs the real game logic + the real bot, fast.
//
// Load the game page from a local copy (python3 -m http.server in resources/app,
// open /src/index.html), then evaluate this file. It:
//   * stops the game's own loop and drives Game.Logic() frame by frame,
//   * replaces Date.now / setInterval / setTimeout with a virtual clock, so the
//     bot's timers (clicks, buying, spells…) run on game time,
//   * replaces Math.random with a seeded generator (also after Math.seedrandom()),
//     so two strategies can be compared on the same luck,
//   * never talks to the real agent (the bridge is stripped from the mod).
//
// API:  await CBSim.run({ save, hours, cfg, seed, onProgress })  →  metrics
//       save: export string (or null = fresh game), cfg: bot overrides
"use strict";
(function () {
	if (window.CBSim) return;

	// ---- seeded RNG --------------------------------------------------------
	function mulberry32(a) {
		return function () {
			a |= 0; a = a + 0x6D2B79F5 | 0;
			var t = Math.imul(a ^ a >>> 15, 1 | a);
			t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
			return ((t ^ t >>> 14) >>> 0) / 4294967296;
		};
	}
	var rng = mulberry32(1);
	var realSeedrandom = Math.seedrandom;
	Math.random = function () { return rng(); };
	Math.seedrandom = function (seed) {
		if (seed === undefined) { Math.random = function () { return rng(); }; return; }
		return realSeedrandom.apply(Math, arguments); // deterministic seeded calls (hand of fate)
	};

	// ---- virtual clock -----------------------------------------------------
	var vnow = Date.now();
	var baseNow = vnow; // every run starts at the same virtual instant
	var realDateNow = Date.now.bind(Date);
	Date.now = function () { return Math.floor(vnow); };
	var timers = [], nextId = 1;
	window.setInterval = function (fn, ms) { var t = { id: nextId++, fn: fn, ms: Math.max(1, ms || 1), at: vnow + Math.max(1, ms || 1), rep: true }; timers.push(t); return t.id; };
	window.setTimeout = function (fn, ms) { var t = { id: nextId++, fn: fn, ms: ms || 0, at: vnow + (ms || 0), rep: false }; timers.push(t); return t.id; };
	window.clearInterval = window.clearTimeout = function (id) { timers = timers.filter(function (t) { return t.id !== id; }); };
	function runTimers() {
		for (var i = 0; i < timers.length; i++) {
			var t = timers[i];
			if (t.at <= vnow) {
				try { typeof t.fn === 'function' ? t.fn() : 0; } catch (e) { console.log('sim timer', e); }
				if (t.rep) t.at += t.ms; else { timers.splice(i, 1); i--; }
			}
		}
	}

	// stop the game's own loop; we call Game.Logic ourselves
	Game.Loop = function () {};
	Game.Draw = function () {};
	Game.prefs.particles = 0; Game.prefs.numbers = 0; Game.prefs.fancy = 0; Game.volume = 0;
	window.PlaySound = function () {};
	Game.WriteSave = function () { return ''; }; // never touch the page's stored save: each reload starts identical

	var modLoaded = false;
	async function loadMod() {
		if (modLoaded) return;
		var src = await (await fetch('/mods/local/cookieBot/main.js?' + realDateNow())).text();
		src = src.replace('setInterval(bridgeStep, 500);', '/* bridge off in sim */')
			.replace('setInterval(render, 500);', '/* panel off in sim */');
		(0, eval)(src);
		modLoaded = true;
	}

	function metrics(t0, earned0, extra) {
		var B = window.CookieBot;
		return Object.assign({
			hours: (vnow - t0) / 3600000,
			earned: Game.cookiesEarned - earned0,
			cps: Game.cookiesPs,
			unbuffedCps: Game.unbuffedCps,
			prestigeGain: B.ascendAdvice().gain,
			buildings: Game.BuildingsOwned,
			upgrades: Game.UpgradesOwned,
			golden: B.stats.golden, spells: B.stats.spells, bought: B.stats.bought,
		}, extra || {});
	}

	async function run(opt) {
		opt = opt || {};
		rng = mulberry32(opt.seed || 1);
		Math.random = function () { return rng(); };
		if (Game.killBuffs) Game.killBuffs(); Game.killShimmers(); // leftovers from the previous run
		if (opt.save) {
			// Pin the virtual clock to the save's own timestamp (+1 s), independent of
			// the real time: offline gains, lumps and buff timers then match every run.
			Game.ImportSaveCode(opt.save);
			vnow = (Game.lastDate || baseNow) + 1000;
			if (Game.killBuffs) Game.killBuffs(); Game.killShimmers();
			Game.ImportSaveCode(opt.save);
		} else { vnow = baseNow; Game.HardReset(2); }
		await loadMod();
		var B = window.CookieBot;
		B.stats = { clicks: 0, golden: 0, bought: 0, spells: 0, lumps: 0, wrinklers: 0, earnedStart: Game.cookiesEarned, t0: vnow };
		B.ranked = []; B.target = null; B.boughtAt = {}; B.pHist = []; B.log = [];
		Object.assign(B.cfg, { consoleMode: false, panel: false }, opt.cfg || {});
		// Same starting line for every run: frame counter, timer phases, RNG.
		Game.T = 0; Game.drawT = 0;
		Game.lastClick = 0; Game.lastActivity = vnow; Game.time = vnow; // timestamps from a previous run would block clicks
		Game.killShimmers(); // also resets the golden/reindeer spawn timers
		for (var k in Game.shimmerTypes) if (Game.shimmerTypes[k].reset) Game.shimmerTypes[k].reset();
		timers.forEach(function (t) { t.at = vnow + t.ms; });
		if (B._resetInternal) B._resetInternal();
		B.start();
		Game.CalculateGains();
		rng = mulberry32(opt.seed || 1);
		Math.random = function () { return rng(); };

		var t0 = vnow, earned0 = Game.cookiesEarned;
		var frames = Math.round((opt.hours || 1) * 3600 * Game.fps);
		var step = 1000 / Game.fps, wallStart = realDateNow(), samples = [];
		var ours = function () { return rng(); };
		for (var f = 0; f < frames; f++) {
			vnow += step;
			Math.random = ours; // some game code seeds Math.random and never restores it
			Game.Logic();
			runTimers();
			if (f % (Game.fps * 600) === 0) { // every 10 game minutes
				samples.push([Math.round((vnow - t0) / 60000), Game.cookiesEarned - earned0, Game.unbuffedCps]);
				if (opt.onProgress) opt.onProgress(f / frames);
				await yieldNow(); // let the page breathe (not throttled in background tabs)
			}
		}
		return metrics(t0, earned0, { wallSeconds: (realDateNow() - wallStart) / 1000, samples: samples,
			log: B.log.slice(0, 12), target: B.target && B.target.obj.name, plan: B.plan });
	}
	// MessageChannel callbacks are not throttled like timers in hidden tabs.
	var chan = new MessageChannel(), waiting = [];
	chan.port1.onmessage = function () { var r = waiting.shift(); if (r) r(); };
	function yieldNow() { return new Promise(function (r) { waiting.push(r); chan.port2.postMessage(0); }); }

	window.CBSim = { run: run, now: function () { return vnow; } };
})();

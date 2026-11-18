// Cookie Bot v4 — Steam Cookie Clicker mod.
// Modules: autoclicker, golden cookies, smart buyer (simulated CpS gain),
// lucky bank, combo engine (grimoire), sugar lumps, wrinklers, control panel.
// Hotkeys: Shift+B — bot on/off, Shift+H — show/hide panel.

Game.registerMod("cookie bot", {
	init: function () {
		var mod = this;
		var CB = (window.CookieBot = mod);

		// ---------- config ----------
		var DEFAULTS = {
			enabled: true,
			click: true, clickRate: 20, comboClickRate: 50,
			golden: true, wrath: false,
			buy: true, buyBuildings: true, buyUpgrades: true, luckyBank: true,
			spells: true,
			lumps: true,
			fortune: true,
			wrinklers: false, // farm wrinklers: keep them, pop only to pay for purchases
			wrinklerPayback: 7200, // pop wrinklers only for purchases that repay within 2 h
			lumpSpend: true, pantheon: true,
			lumpLevels: true, // after minigames, level the building with the best CpS per lump
			spellPredict: true, // read the seeded hand-of-fate outcome
			garden: true, dragon: true, achievements: true,
			luckyBankSeconds: 6000, // 6000 s = full Lucky payout; smaller = cheaper insurance
			milkAware: false, // value building-count achievements (milk); off: no measurable gain in 40 sims
			fillers: true, // while saving, buy things that repay before the target is affordable
			autoAscend: false, // opt-in: ascend by itself when the advice says so
			heavenlyBuy: true, // on the heaven screen, buy heavenly upgrades in a fixed order
			market: true, marketCheat: false, marketBrokers: true,
			marketBuyAt: 0.4, marketSellAt: 1.5, marketBudget: 0.5, // tuned on 3000-tick sims
			panel: true, panelX: null, panelY: 60,
			consoleMode: false, // hide the window while the terminal UI is connected
		};
		CB.cfg = Object.assign({}, DEFAULTS, CB.cfg || {});
		CB.stats = Object.assign(
			{ clicks: 0, golden: 0, bought: 0, spells: 0, lumps: 0, wrinklers: 0, earnedStart: Game.cookiesEarned, t0: Date.now() },
			CB.stats || {}
		);
		CB.log = [];

		// Upgrades never bought automatically.
		var SKIP = {
			'Elder Pledge': 1, 'Elder Covenant': 1, 'Revoke Elder Covenant': 1,
			'Sugar frenzy': 1, 'Chocolate egg': 1, 'Golden switch [off]': 1, 'Golden switch [on]': 1,
			'Shimmering veil [off]': 1, 'Shimmering veil [on]': 1,
		};

		function note(msg) {
			CB.log.unshift(new Date().toLocaleTimeString().slice(0, 5) + ' ' + msg);
			if (CB.log.length > 30) CB.log.pop();
		}

		// ---------- helpers ----------
		function buffMult(key) {
			var m = 1;
			for (var i in Game.buffs) if (typeof Game.buffs[i][key] !== 'undefined') m *= Game.buffs[i][key];
			return m;
		}
		function inCombo() { return buffMult('multClick') > 1 || buffMult('multCpS') > 7; }
		function clickRate() { return CB.cfg.click ? Math.min(50, inCombo() ? CB.cfg.comboClickRate : CB.cfg.clickRate) : 0; }
		// Effective income: passive CpS + clicks, both without temporary buffs.
		function baseIncome() {
			// Normal click rate, not the combo burst, so the figure (and purchase
			// ranking) doesn't jump every time a buff starts.
			// Clicks earn a % of CpS (mouse upgrades), so CpS buffs inflate them too.
			var mouse = Game.computedMouseCps / buffMult('multClick') / buffMult('multCpS');
			return Game.unbuffedCps + mouse * (CB.cfg.click ? Math.min(50, CB.cfg.clickRate) : 0);
		}
		function grimoire() {
			var w = Game.Objects['Wizard tower'];
			return w && w.minigameLoaded ? w.minigame : null;
		}

		// ---------- strategy engine ----------
		// One decision loop:
		//   1. rank every purchase by its real income gain (simulated with the
		//      game's own CalculateGains) plus value the simulation can't see;
		//   2. decide how much bank to keep for golden cookie payouts;
		//   3. buy, or pop wrinklers to finance a purchase that is almost affordable;
		//   4. slowly: spend sugar lumps on minigames, fill the pantheon.
		var GOLDEN_UPS = {};
		(Game.goldenCookieUpgrades || []).forEach(function (n) { GOLDEN_UPS[n] = 1; });
		// Building counts that unlock a tiered upgrade (each doubles that building).
		var TIERS = [1, 5, 25, 50, 100, 150, 200, 250, 300, 350, 400, 450, 500, 550, 600, 650, 700, 750, 800];
		CB.plan = '';

		function measure(apply, revert) {
			var before = baseIncome();
			apply();
			Game.CalculateGains();
			var after = baseIncome();
			revert();
			Game.CalculateGains();
			return after - before;
		}
		// The game flips `locked` only while drawing, and a hidden window doesn't draw:
		// use the game's own unlock rule instead.
		function available(b) { return b.amount > 0 || Game.cookiesEarned >= b.basePrice; }

		function candidates() {
			var list = [];
			var income = Math.max(baseIncome(), 0.1);
			var cpsBuff = buffMult('multCpS');
			if (CB.cfg.buyBuildings) {
				var nextLocked = true; // also offer the next not-yet-unlocked building
				Game.ObjectsById.forEach(function (b) {
					if (!available(b)) { if (!nextLocked) return; nextLocked = false; }
					// The next copy may win "have N of this building" achievements: each
					// one adds milk, and milk feeds every kitten upgrade. Count them in.
					var ach = 0;
					for (var ti in b.tieredAchievs) {
						var A = b.tieredAchievs[ti];
						if (A.won || !CB.cfg.milkAware) continue;
						var need = Game.Tiers[A.tier] && Game.Tiers[A.tier].achievUnlock;
						if (need && b.amount < need && b.amount + 1 >= need) ach++;
					}
					var gain = measure(
						function () { b.amount++; Game.BuildingsOwned++; Game.AchievementsOwned += ach; },
						function () { b.amount--; Game.BuildingsOwned--; Game.AchievementsOwned -= ach; }
					);
					var why = ach ? 'achievement' : '';
					if (b.amount > 0 && TIERS.indexOf(b.amount + 1) >= 0) {
						// Reaching a tier unlocks an upgrade that doubles this building.
						gain += b.storedTotalCps * Game.globalCpsMult / cpsBuff * 0.25;
						why = (why ? why + ', ' : '') + 'tier ' + (b.amount + 1);
					}
					list.push({ kind: 'b', obj: b, price: b.price, gain: gain, why: why });
				});
			}
			if (CB.cfg.buyUpgrades) {
				Game.UpgradesInStore.forEach(function (u) {
					// The store list is rebuilt by game logic a frame later, so a just-bought
					// upgrade can still be listed: skip it, or the revert below would un-buy it.
					if (u.bought || SKIP[u.name] || u.pool === 'toggle' || u.choicesFunction) return;
					var gain = measure(function () { u.bought = 1; }, function () { u.bought = 0; });
					var why = '';
					if (GOLDEN_UPS[u.name]) { gain += income * 0.15; why = 'golden cookies'; }
					else if (gain <= 1e-9 && u.getPrice() <= income * 900) {
						// No direct effect (research, unlocks): fine if it costs < 15 min.
						gain = u.getPrice() / 1800; why = 'unlock';
					}
					list.push({ kind: 'u', obj: u, price: u.getPrice(), gain: gain, why: why });
				});
			}
			list.forEach(function (c) {
				c.score = c.gain > 1e-9 ? c.price / c.gain + c.price / income : Infinity;
			});
			list.sort(function (a, b) { return a.score - b.score; });
			return list;
		}

		// Bank to keep. Lucky pays min(15% of bank, 900 s of CpS), so a full
		// payout needs 6000 s of CpS in the bank — worth it once golden cookie
		// upgrades make golden cookies frequent and long.
		function luckyReserve() {
			if (!CB.cfg.luckyBank) return 0;
			// Early on, 6000 s of idle bank costs far more growth than lucky payouts
			// return: only bank once Get lucky doubles golden effect durations.
			if (!Game.Has('Get lucky')) return 0;
			return Game.unbuffedCps * CB.cfg.luckyBankSeconds;
		}
		function reserveFor(t) {
			var r = luckyReserve();
			// Fast payback beats the bank: buy anything that repays itself in 10 min.
			if (t && t.gain > 0 && t.price / t.gain < 600) r = 0;
			return r;
		}

		function wrinklerPayout(w) {
			var m = 1.1;
			if (Game.Has('Sacrilegious corruption')) m *= 1.05;
			if (Game.Has('Wrinklerspawn')) m *= 1.05;
			return w.sucked * m;
		}
		function aliveWrinklers() {
			return Game.wrinklers.filter(function (w) { return w.phase > 0 && w.hp > 0 && w.type !== 1; });
		}
		// Wrinklers return >110% of what they eat, so keep them and pop only to
		// pay for a purchase that is worth having now.
		var lastFinance = 0;
		function financeWithWrinklers(t, need) {
			// Popped wrinklers pay out over the next frames: don't pop more meanwhile.
			if (Date.now() - lastFinance < 3000) return true;
			if (!CB.cfg.wrinklers || !t || !(t.gain > 0) || t.price / t.gain > CB.cfg.wrinklerPayback) return false;
			var ws = aliveWrinklers().sort(function (a, b) { return b.sucked - a.sucked; });
			var total = 0;
			ws.forEach(function (w) { total += wrinklerPayout(w); });
			if (total < need) return false;
			for (var i = 0; i < ws.length && need > 0; i++) {
				need -= wrinklerPayout(ws[i]);
				ws[i].hp = 0;
				CB.stats.wrinklers++;
			}
			lastFinance = Date.now();
			note('popped wrinklers for ' + t.obj.name);
			return true;
		}

		CB.target = null;
		CB.ranked = [];
		var lastRank = 0;

		// Up to 5 purchases per tick so a big golden payout is spent quickly.
		// One ranking pass costs ~5 ms, so this stays cheap.
		function buyStep() {
			if (Game.OnAscend || Game.AscendTimer > 0 || Game.ReincarnateTimer > 0) return;
			for (var i = 0; i < 5; i++) if (!buyOne()) break;
		}

		function buyOne() {
			if (!CB.cfg.buy) { CB.target = null; CB.plan = 'auto-buy off'; return false; }
			var now = Date.now();
			if (now - lastRank > 1000 || !CB.target) {
				CB.ranked = candidates();
				lastRank = now;
			}
			var t = CB.ranked[0];
			CB.target = t || null;
			if (!t || !isFinite(t.score)) { CB.plan = 'nothing worth buying'; return false; }

			t.price = t.kind === 'b' ? t.obj.price : t.obj.getPrice(); // live price
			var reserve = reserveFor(t);
			var spare = Game.cookies - reserve;
			if (spare < t.price) {
				if (financeWithWrinklers(t, t.price - spare)) return false; // payout lands next frame
				// While saving, buy anything that pays for itself before the target
				// would be affordable: it cannot delay the target and adds income.
				var wait = (t.price - spare) / Math.max(baseIncome(), 1);
				var filler = null;
				for (var i = 1; CB.cfg.fillers && i < CB.ranked.length; i++) {
					var c = CB.ranked[i];
					if (!(c.gain > 0)) continue;
					var cp = c.kind === 'b' ? c.obj.price : c.obj.getPrice();
					if (cp <= Game.cookies - reserveFor(c) && cp / c.gain < wait * 0.8) { filler = c; break; }
				}
				if (filler) { filler.why = 'while saving for ' + t.obj.name; t = filler; }
				else {
					CB.plan = inCombo() ? 'combo running' : reserve > 0 && Game.cookies < reserve ? 'refilling lucky bank' : 'saving for ' + t.obj.name;
					return false;
				}
				t.price = t.kind === 'b' ? t.obj.price : t.obj.getPrice();
			}

			var ok;
			if (t.kind === 'b') {
				var mode = Game.buyMode, bulk = Game.buyBulk, had = t.obj.amount;
				Game.buyMode = 1; Game.buyBulk = 1;
				t.obj.buy(1);
				Game.buyMode = mode; Game.buyBulk = bulk;
				ok = t.obj.amount > had;
			} else {
				t.obj.buy(1); // bypass confirmation prompt
				ok = !!t.obj.bought;
			}
			lastRank = 0; // re-rank before the next purchase
			if (!ok) return false;
			CB.stats.bought++;
			CB.plan = 'buying';
			note('bought ' + t.obj.name + (t.why ? ' (' + t.why + ')' : ''));
			return true;
		}

		// ---------- golden cookies, ticker ----------
		function shimmerStep() {
			if (CB.cfg.golden) {
				Game.shimmers.slice().forEach(function (s) {
					if (s.type === 'golden' && s.wrath && !CB.cfg.wrath) return;
					s.pop();
					CB.stats.golden++;
				});
			}
			if (CB.cfg.fortune && Game.TickerEffect && Game.TickerEffect.type === 'fortune') {
				Game.tickerL.click();
				note('fortune ticker');
			}
		}

		// ---------- slow loop: lumps, minigame unlocks, pantheon ----------
		// Minigames unlock at building level 1 (1 lump each): grimoire for
		// combos, temple for the pantheon, bank for the stock market.
		var MINIGAMES = ['Wizard tower', 'Temple', 'Bank', 'Farm'];
		// Pantheon picks that suit a clicking, golden-cookie bot:
		// Mokalsium (milk), Muridal (clicks), Jeremy (building CpS).
		var GODS = ['mother', 'labor', 'industry'];

		function slowStep() {
			try { ascendStep(); } catch (e) { console.log('ascend', e); }
			if (Game.OnAscend || Game.AscendTimer > 0) return;
			try { dragonStep(); } catch (e) { console.log('dragon', e); }
			try { gardenStep(); } catch (e) { console.log('garden', e); }
			try { achievementStep(); } catch (e) { console.log('achiev', e); }
			if (CB.cfg.lumps && Game.canLumps() && Date.now() - Game.lumpT >= Game.lumpRipeAge) {
				Game.clickLump();
				CB.stats.lumps++;
				note('sugar lump harvested');
			}
			if (CB.cfg.lumpSpend && Game.canLumps()) {
				for (var i = 0; i < MINIGAMES.length; i++) {
					var o = Game.Objects[MINIGAMES[i]];
					if (o && o.amount > 0 && o.level === 0 && Game.lumps >= 1) {
						var ask = Game.prefs.askLumps;
						Game.prefs.askLumps = 0; // skip the confirmation prompt
						o.levelUp();
						Game.prefs.askLumps = ask;
						note('spent a lump: ' + o.name + ' level 1');
						break;
					}
				}
				// Minigames done: each building level is +1% of that building's CpS and
				// costs (level + 1) lumps. Spend on the best CpS per lump, keep 1 spare.
				var allOpen = MINIGAMES.every(function (n) { var b = Game.Objects[n]; return !b || b.amount === 0 || b.level > 0; });
				if (allOpen && CB.cfg.lumpLevels) {
					var best = null, bestV = 0;
					Game.ObjectsById.forEach(function (b) {
						if (!b.amount || Game.lumps < b.level + 2) return;
						var v = b.storedTotalCps * 0.01 / (b.level + 1);
						if (v > bestV) { bestV = v; best = b; }
					});
					if (best) {
						var ask2 = Game.prefs.askLumps;
						Game.prefs.askLumps = 0;
						best.levelUp();
						Game.prefs.askLumps = ask2;
						note('spent lumps: ' + best.name + ' level ' + best.level);
					}
				}
			}
			var T = Game.Objects['Temple'];
			var P = T && T.minigameLoaded ? T.minigame : null;
			if (CB.cfg.pantheon && P) {
				for (var s = 0; s < 3 && P.swaps > 0; s++) {
					if (P.slot[s] !== -1) continue;
					for (var g = 0; g < GODS.length; g++) {
						var god = P.gods[GODS[g]];
						if (god && god.slot === -1) {
							P.slotGod(god, s);
							P.useSwap(1);
							note('pantheon: ' + god.name + ' in slot ' + (s + 1));
							break;
						}
					}
				}
			}
		}

		// ---------- dragon ----------
		// Only the first five stages cost cookies (1M..16M); later ones sacrifice
		// buildings, so the bot leaves those to the player. The first aura is set
		// once (from "none"), paying the game's normal price of one building.
		var AURA_PREF = ['Radiant Appetite', 'Dragonflight', 'Breath of Milk', 'Elder Battalion', 'Dragon Cursor'];
		function dragonStep() {
			if (!CB.cfg.dragon || !Game.Has('A crumbly egg')) return;
			var lvl = Game.dragonLevel;
			if (lvl < 5) {
				var price = 1e6 * Math.pow(2, lvl);
				var income = Math.max(baseIncome(), 1);
				if (Game.dragonLevels[lvl].cost() && Game.cookies - price >= luckyReserve() && price <= income * 600) {
					Game.UpgradeDragon();
					note('dragon: ' + (Game.dragonLevel < 5 ? 'egg chipped' : 'hatched, trained Breath of Milk'));
				}
				return;
			}
			if (Game.dragonAura === 0) {
				for (var i = 0; i < AURA_PREF.length; i++) {
					var id = auraId(AURA_PREF[i]);
					if (id > 0 && Game.dragonLevel >= id + 4 && id !== Game.dragonAura2) {
						var best = null;
						for (var k in Game.Objects) if (Game.Objects[k].amount > 0) best = Game.Objects[k];
						if (best) best.sacrifice(1); // the game's price for switching auras
						Game.dragonAura = id;
						Game.recalculateGains = 1;
						note('dragon aura: ' + AURA_PREF[i]);
						break;
					}
				}
			}
		}
		function auraId(name) {
			for (var i in Game.dragonAuras) if (Game.dragonAuras[i].name === name) return parseInt(i, 10);
			return -1;
		}

		// ---------- garden ----------
		// Safe routine: Baker's wheat (+1% CpS each) in every other column, the
		// gaps stay empty so neighbours can mutate into new plants. Any plant whose
		// seed is still unknown is harvested once mature (that unlocks the seed);
		// weeds with a known seed are pulled. Never uses the sacrifice button.
		function garden() {
			var f = Game.Objects['Farm'];
			return f && f.minigameLoaded ? f.minigame : null;
		}
		function gardenStep() {
			var G = garden();
			if (!CB.cfg.garden || !G) return;
			var wheat = G.plants['bakerWheat'];
			var income = Math.max(baseIncome(), 1);
			for (var y = 0; y < 6; y++) for (var x = 0; x < 6; x++) {
				if (!G.isTileUnlocked(x, y)) continue;
				var tile = G.plot[y][x];
				if (tile[0] >= 1) {
					var pl = G.plantsById[tile[0] - 1];
					var mature = tile[1] >= pl.mature;
					if (mature && !pl.unlocked) { G.harvest(x, y); note('garden: unlocked ' + pl.name); }
					else if (pl.weed && pl.unlocked) G.harvest(x, y);
					continue;
				}
				if (x % 2 === 1 || !wheat || !wheat.unlocked) continue; // gaps for mutations
				var cost = G.getCost(wheat);
				if (cost <= income * 120 && Game.cookies - cost >= luckyReserve()) {
					G.useTool(wheat.id, x, y);
				}
			}
		}
		CB.gardenInfo = function () {
			var G = garden();
			if (!G) return null;
			var plots = 0, planted = 0, mature = 0, grid = [], known = 0;
			G.plantsById.forEach(function (p) { if (p.unlocked) known++; });
			for (var y = 0; y < 6; y++) {
				var line = '';
				for (var x = 0; x < 6; x++) {
					if (!G.isTileUnlocked(x, y)) continue;
					plots++;
					var t = G.plot[y][x];
					if (t[0] >= 1) {
						planted++;
						var m = t[1] >= G.plantsById[t[0] - 1].mature;
						if (m) mature++;
						line += m ? '◆' : '●';
					} else line += '·';
				}
				if (line) grid.push(line);
			}
			return { plots: plots, planted: planted, mature: mature, seeds: known, seedsTotal: G.plantsById.length,
				soil: G.soilsById[G.soil] ? G.soilsById[G.soil].name : '', planting: CB.cfg.garden ? "Baker's wheat" : '', grid: grid };
		};

		// ---------- free achievements ----------
		// Done by performing the in-game action, never by calling Game.Win.
		// More achievements = more milk = kittens multiply CpS.
		function achievementStep() {
			if (!CB.cfg.achievements) return;
			if (!Game.HasAchiev('Tabloid addiction') && Game.TickerClicks < 50) Game.tickerL.click();
			if (!Game.HasAchiev('Tiny cookie')) Game.ClickTinyCookie();
		}

		// ---------- ascension & heaven ----------
		// Prestige is the game's real multiplier: every heavenly chip of prestige
		// level is +1% CpS once the "Heavenly chip secret" family is unlocked, and
		// heavenly upgrades add far more. Spending chips never lowers prestige.
		var HEAVEN_ORDER = [
			'Legacy', 'Heavenly cookies', 'How to bake your dragon', 'Box of brand biscuits', 'Tin of british tea biscuits', 'Box of macarons',
			'Persistent memory', 'Heavenly luck', 'Starter kit', 'Twin Gates of Transcendence',
			'Permanent upgrade slot I', 'Season switcher', 'Lasting fortune', 'Decisive fate',
			'Divine discount', 'Divine sales', 'Divine bakeries', 'Starter kitchen', 'Kitten angels',
			'Synergies Vol. I', 'Lucky digit', 'Elder spice', 'Sacrilegious corruption',
			'Wrinkly cookies', 'Distilled essence of redoubled luck', 'Synergies Vol. II',
			'Lucky number', 'Lucky payout', 'Sugar baking', 'Fortune cookies',
		];

		// ("Heavenly chip secret" & co. are normal store upgrades bought with cookies;
		// the regular buyer picks them up because they raise the prestige effect.)
		function heavenAvailable(u, owned) {
			if (u.pool !== 'prestige' || u.bought || owned[u.name]) return false;
			if (u.showIf && !u.showIf()) return false;
			for (var i = 0; i < u.parents.length; i++) {
				var par = u.parents[i];
				if (par !== -1 && par && !par.bought && !owned[par.name]) return false;
			}
			return true;
		}
		// Greedy shopping list for a chip budget, in HEAVEN_ORDER; a wanted upgrade
		// whose parent is missing pulls that parent in first.
		function heavenPlan(chips) {
			var owned = {}, list = [], spent = 0;
			function take(u, depth) {
				if (!u || u.pool !== 'prestige' || u.bought || owned[u.name] || depth > 6) return;
				for (var i = 0; i < u.parents.length; i++) {
					var par = u.parents[i];
					if (par !== -1 && par && !par.bought && !owned[par.name]) take(par, depth + 1);
				}
				if (heavenAvailable(u, owned) && spent + u.getPrice() <= chips) {
					owned[u.name] = 1; spent += u.getPrice(); list.push(u);
				}
			}
			HEAVEN_ORDER.forEach(function (n) { take(Game.Upgrades[n], 0); });
			return { list: list, spent: spent };
		}
		function heavenShop() {
			var got = [];
			heavenPlan(Game.heavenlyChips).list.forEach(function (u) { u.buy(1); if (u.bought) got.push(u.name); });
			if (got.length) note('heaven: bought ' + got.join(', '));
			return got.length;
		}

		var ascending = 0; // bot-started ascension: 1 = popping wrinklers, 2 = ascended
		function ascendStep() {
			if (Game.OnAscend) {
				// On the heaven screen: shop, then return if the bot started this.
				if (CB.cfg.heavenlyBuy) heavenShop();
				if (ascending) {
					ascending = 0;
					note('reincarnating');
					Game.Reincarnate(1);
				}
				return;
			}
			if (Game.AscendTimer > 0 || Game.ReincarnateTimer > 0) return;
			var adv = ascendAdvice();
			// A reset that earns no heavenly chips only throws the run away.
			if (CB.ascendNow && adv.gain < 1) { CB.ascendNow = false; note('ascension refused: +0 heavenly chips'); }
			var wanted = CB.ascendNow || (CB.cfg.autoAscend && adv.ready);
			if (!wanted) { ascending = 0; return; } // the rule changed its mind: start over next time
			if (inCombo() || Game.shimmers.length) return; // never throw away a running combo
			if (ascending === 0) {
				// Popped wrinklers pay into "cookies baked", which is what prestige counts.
				Game.CollectWrinklers();
				ascending = 1;
				note('ascending: popped wrinklers');
				return;
			}
			ascending = 2;
			CB.ascendNow = false;
			note('ascending with +' + Beautify(adv.gain) + ' heavenly chips');
			Game.Ascend(1);
		}
		Game.registerHook('reincarnate', function () {
			CB.boughtAt = {}; CB.ranked = []; CB.target = null;
			CB.stats.earnedStart = Game.cookiesEarned; // new run: 'made by bot' counts from zero
		});
		CB.heavenInfo = function () {
			var adv = ascendAdvice();
			var now = heavenPlan(Game.heavenlyChips), after = heavenPlan(Game.heavenlyChips + adv.gain);
			return { chips: Game.heavenlyChips, prestige: Game.prestige, gain: adv.gain,
				now: now.list.map(function (u) { return u.name; }),
				after: after.list.map(function (u) { return u.name; }), afterCost: after.spent,
				owned: Game.PrestigeUpgrades.filter(function (u) { return u.bought; }).length,
				total: Game.PrestigeUpgrades.length, ascending: !!ascending || !!Game.OnAscend };
		};

		// Ascension timing (the bot ascends only with autoAscend on, or when asked).
		// Marginal value rule: a run is worth G chips after t seconds; resetting is
		// best when the chips you are still gaining per hour drop below the run's
		// average (G / t) — after that, starting over earns chips faster.
		// Guards: a minimum gain worth a reset (30 chips on the first run, then 10%
		// of prestige), at least 1 h of run, and two shortcuts: doubling prestige,
		// or 440 chips on the first run.
		CB.pHist = CB.pHist || []; // [unix s, chips gain] every 5 min
		function currentGain() {
			return Math.max(0, Math.floor(Game.HowMuchPrestige(Game.cookiesReset + Game.cookiesEarned) - Game.prestige));
		}
		setInterval(function () {
			if (Game.OnAscend || Game.AscendTimer > 0) return;
			CB.pHist.push([Math.round(Date.now() / 1000), currentGain()]);
			if (CB.pHist.length > 48) CB.pHist.shift();
		}, 300000);
		function ascendAdvice() {
			var gain = currentGain(), P = Game.prestige, now = Date.now() / 1000;
			var first = P < 1;
			var min = first ? 30 : Math.max(10, Math.ceil(P * 0.1));
			var runFor = Math.max(1, now - Game.startDate / 1000);
			var avg = gain / runFor * 3600;               // chips per hour over the whole run
			var rate = null;                               // chips per hour lately
			for (var i = 0; i < CB.pHist.length; i++) {
				var s = CB.pHist[i];
				if (now - s[0] <= 3900 && now - s[0] >= 1800) { rate = (gain - s[1]) / (now - s[0]) * 3600; break; }
			}
			var slowing = rate !== null && runFor >= 3600 && rate < avg;
			var doubled = !first && gain >= P;
			var ready = gain >= min && (slowing || doubled || (first && gain >= 440));
			var why = ready ? (doubled ? 'prestige doubles' : slowing ? 'growth slowed below the run average' : '440 chips')
				: gain < min ? 'needs ' + min + ' chips' : rate === null ? 'measuring growth' : 'still growing faster than average';
			return { gain: gain, goal: min, ready: ready, prestige: P, rate: rate, avg: avg, why: why };
		}
		CB.ascendAdvice = ascendAdvice;
		Game.registerHook('reincarnate', function () { CB.pHist = []; });

		// ---------- combo engine (grimoire) ----------
		// Force the Hand of Fate is deterministic: castSpell seeds the RNG with
		// Game.seed + '/' + spellsCastTotal, then rolls success, then the golden
		// cookie's constructor uses 2 randoms for its position (3 in valentines /
		// easter), then the effect list is rolled. predictFate mirrors that exactly.
		var GOOD_FATE = { 'click frenzy': 3, 'building special': 3, 'frenzy': 2, 'multiply cookies': 1,
			'cookie storm': 1, 'free sugar lump': 2 };
		function predictFate(offset, goldOnScreen) {
			var M = grimoire();
			if (!M) return null;
			var fc = 0.15;
			if (Game.hasBuff('Magic adept')) fc *= 0.1;
			if (Game.hasBuff('Magic inept')) fc *= 5;
			fc *= 1 + 0.1 * Game.auraMult('Supreme Intellect');
			fc += 0.15 * (goldOnScreen || 0);
			Math.seedrandom(Game.seed + '/' + (M.spellsCastTotal + (offset || 0)));
			var win = Math.random() < 1 - fc;
			var extra = Game.season === 'valentines' || Game.season === 'easter';
			if (extra) Math.random();
			Math.random(); Math.random(); // shimmer x, y
			var choices;
			if (win) {
				choices = ['frenzy', 'multiply cookies'];
				if (!Game.hasBuff('Dragonflight')) choices.push('click frenzy');
				if (Math.random() < 0.1) choices.push('cookie storm', 'cookie storm', 'blab');
				if (Game.BuildingsOwned >= 10 && Math.random() < 0.25) choices.push('building special');
				if (Math.random() < 0.15) choices = ['cookie storm drop'];
				if (Math.random() < 0.0001) choices.push('free sugar lump');
			} else {
				choices = ['clot', 'ruin cookies'];
				if (Math.random() < 0.1) choices.push('cursed finger', 'blood frenzy');
				if (Math.random() < 0.003) choices.push('free sugar lump');
				if (Math.random() < 0.1) choices = ['blab'];
			}
			var out = choices[Math.floor(Math.random() * choices.length)];
			Math.seedrandom(); // back to real randomness
			return { name: out, win: win, value: win ? (GOOD_FATE[out] || 0) : 0 };
		}
		CB.predictFate = predictFate;

		function cheapestSkipSpell(M) {
			var best = null;
			["haggler's charm", 'summon crafty pixies'].forEach(function (k) {
				var sp = M.spells[k];
				if (sp && (!best || M.getSpellCost(sp) < M.getSpellCost(best))) best = sp;
			});
			return best;
		}

		var holdSince = 0;
		function cast(M, spell, why) {
			if (M.castSpell(spell)) {
				CB.stats.spells++;
				note('cast ' + spell.name + (why ? ' (' + why + ')' : ''));
				holdSince = 0;
				return true;
			}
			return false;
		}

		function spellStep() {
			var M = grimoire();
			if (!CB.cfg.spells || !M || Game.OnAscend || Game.AscendTimer > 0) return;
			var spell = M.spells['hand of fate'];
			var cost = M.getSpellCost(spell);
			var goldOnScreen = Game.shimmers.filter(function (s) { return s.type === 'golden'; }).length;
			// Each golden cookie on screen raises backfire chance: wait until clear.
			if (goldOnScreen) return;
			var full = M.magic >= M.magicM - 0.01;
			var frenzy = !!Game.hasBuff('Frenzy');
			var bigBuff = buffMult('multCpS') >= 7 || buffMult('multClick') > 1;

			if (!CB.cfg.spellPredict) {
				// Plain mode: cast into an active Frenzy, or when magic would overflow.
				if (M.magic >= cost && (frenzy || full)) cast(M, spell, frenzy ? 'combo' : '');
				return;
			}

			var next = predictFate(0, 0);
			if (!next) return;
			if (next.value === 0) {
				// Bad or useless outcome ahead: burn it with the cheapest spell, but only
				// with magic near full so regeneration isn't wasted.
				var skip = cheapestSkipSpell(M);
				if (skip && M.magic >= M.magicM * 0.9 && M.magic >= M.getSpellCost(skip)) cast(M, skip, 'skipping ' + next.name);
				return;
			}
			if (M.magic < cost) return;
			if (!holdSince) holdSince = Date.now();
			var waited = Date.now() - holdSince;
			var go = false;
			if (next.value >= 3) {
				// Click frenzy / building special: worth most on top of a Frenzy.
				// Hold for one (they come every few minutes), give up after 10 min.
				go = frenzy || bigBuff || waited > 600000;
			} else if (next.name === 'frenzy') {
				go = bigBuff || full;
			} else {
				go = frenzy || full;
			}
			if (go) cast(M, spell, next.name + (frenzy || bigBuff ? ', combo' : ''));
		}
		CB.grimoireInfo = function () {
			var M = grimoire();
			if (!M) return null;
			var n = predictFate(0, 0), q = [];
			for (var i = 1; i <= 3; i++) { var f = predictFate(i, 0); if (f) q.push(f.name); }
			return { next: n ? { name: n.name, good: n.value > 0 } : null, queue: q };
		};

		// ---------- stock market (Bank minigame) ----------
		// Every good drifts around a resting value: 10 + 10*id + (bank level - 1).
		// Buy deep under it, sell above it and above our own buy price + overhead.
		function market() {
			var b = Game.Objects['Bank'];
			return b && b.minigameLoaded ? b.minigame : null;
		}
		// English tickers by good id (the game localizes its own).
		var SYMBOLS = ['CRL', 'CHC', 'BTR', 'SUG', 'NUT', 'SLT', 'VNL', 'EGG', 'CNM', 'CRM', 'JAM', 'WCH', 'HNY', 'CKI', 'RCP', 'SBD', 'PBL', 'YOU', 'CRP', 'CYC'];
		function marketOverhead(M) { return 1 + 0.01 * (20 * Math.pow(0.95, M.brokers)); }
		function marketStep() {
			var M = market();
			if (!CB.cfg.market || !M || Game.OnAscend || Game.AscendTimer > 0) return;
			var oh = marketOverhead(M);
			var unit = Game.cookiesPsRawHighest; // cookies per $1
			if (!unit) return;

			// Brokers cut the buy overhead by 5% each; one costs 20 min of raw CpS.
			if (CB.cfg.marketBrokers && M.brokers < M.getMaxBrokers()) {
				var bp = M.getBrokerPrice();
				if (Game.cookies - bp >= luckyReserve() + bp * 4) {
					Game.Spend(bp);
					M.brokers++;
					note('hired broker #' + M.brokers);
				}
			}

			var budget = Math.max(0, (Game.cookies - luckyReserve()) * CB.cfg.marketBudget);
			M.goodsById.forEach(function (g) {
				if (!g.active) return;
				var rest = M.getRestingVal(g.id);
				var name = SYMBOLS[g.id] || g.symbol;
				// Hidden trend modes: 1 slow rise, 3 fast rise, 2 slow fall, 4 fast fall.
				var rising = CB.cfg.marketCheat && (g.mode === 1 || g.mode === 3);
				var falling = CB.cfg.marketCheat && (g.mode === 2 || g.mode === 4);

				if (g.stock > 0) {
					var paid = (CB.boughtAt[g.id] || g.prev || g.val) * oh;
					var target = Math.max(rest * CB.cfg.marketSellAt, paid * 1.05);
					if (g.val >= target && !rising) {
						var n = g.stock, val = g.val;
						if (M.sellGood(g.id, n)) {
							var profit = (val - paid) * n;
							CB.stats.marketProfit = (CB.stats.marketProfit || 0) + profit * unit;
							note('market: sold ' + n + ' ' + name + ' @ $' + val.toFixed(2));
							delete CB.boughtAt[g.id];
						}
					}
					return;
				}

				if (g.val <= rest * CB.cfg.marketBuyAt && !falling) {
					var cost = g.val * oh * unit;
					var room = M.getGoodMaxStock(g) - g.stock;
					var n2 = Math.min(room, Math.floor(budget / cost));
					if (n2 > 0 && M.buyGood(g.id, n2)) {
						budget -= cost * n2;
						CB.boughtAt[g.id] = g.val;
						note('market: bought ' + n2 + ' ' + name + ' @ $' + g.val.toFixed(2));
					}
				}
			});
		}
		CB.boughtAt = CB.boughtAt || {};

		// ---------- loops ----------
		var timers = [];
		var clickAcc = 0, lastClickT = Date.now();
		// For the simulator: forget every internal timestamp so a new run starts clean.
		CB._resetInternal = function () {
			lastFinance = 0; lastRank = 0; ascending = 0; holdSince = 0;
			clickAcc = 0; lastClickT = Date.now();
			CB.hist = []; CB.pHist = []; CB.ascendNow = false;
		};
		function clickStep() {
			var now = Date.now();
			var rate = clickRate();
			clickAcc += (now - lastClickT) * rate / 1000;
			lastClickT = now;
			if (clickAcc >= 1) {
				clickAcc = Math.min(clickAcc - 1, 2);
				var before = Game.cookieClicks;
				Game.ClickCookie();
				if (Game.cookieClicks > before) CB.stats.clicks++;
			}
		}

		CB.start = function () {
			CB.stop();
			CB.cfg.enabled = true;
			lastClickT = Date.now();
			timers.push(setInterval(clickStep, 10));
			timers.push(setInterval(shimmerStep, 200));
			timers.push(setInterval(spellStep, 500));
			timers.push(setInterval(slowStep, 2000));
			timers.push(setInterval(marketStep, 1000));
			timers.push(setInterval(buyStep, 100));
			Game.Notify('Cookie Bot', 'Запущен. Shift+B — стоп, Shift+H — панель.', [16, 5], 3);
			render();
		};
		CB.stop = function () {
			while (timers.length) clearInterval(timers.pop());
		};
		CB.toggle = function () {
			if (CB.cfg.enabled) { CB.stop(); CB.cfg.enabled = false; Game.Notify('Cookie Bot', 'Остановлен', [16, 5], 3); }
			else CB.start();
			render();
		};

		// ---------- panel ----------
		var css = document.createElement('style');
		css.textContent = [
			'#cbPanel{position:fixed;z-index:100000000;width:280px;background:rgba(17,12,8,.94);color:#f2e6d0;',
			'border:1px solid #6b4a2b;border-radius:8px;font:12px/1.4 Merriweather,Georgia,serif;box-shadow:0 6px 24px rgba(0,0,0,.6);user-select:none}',
			'#cbPanel .hd{display:flex;align-items:center;gap:8px;padding:8px 10px;cursor:move;border-bottom:1px solid #3d2a18}',
			'#cbPanel .hd b{flex:1;font-size:13px}',
			'#cbPanel .dot{width:8px;height:8px;border-radius:50%;background:#7a7a7a}',
			'#cbPanel .dot.on{background:#7fd65b;box-shadow:0 0 6px #7fd65b}',
			'#cbPanel button{background:#3d2a18;color:#f2e6d0;border:1px solid #6b4a2b;border-radius:4px;padding:2px 8px;cursor:pointer;font:inherit}',
			'#cbPanel button:hover{background:#5a3d22}',
			'#cbPanel .sec{padding:8px 10px;border-bottom:1px solid #3d2a18}',
			'#cbPanel .sec:last-child{border-bottom:0}',
			'#cbPanel .k{color:#b9a385}',
			'#cbPanel .row{display:flex;justify-content:space-between;gap:8px}',
			'#cbPanel .row span:last-child{text-align:right;font-variant-numeric:tabular-nums}',
			'#cbPanel .grid{display:grid;grid-template-columns:1fr 1fr;gap:2px 8px}',
			'#cbPanel label{display:flex;align-items:center;gap:5px;cursor:pointer}',
			'#cbPanel input[type=checkbox]{margin:0;accent-color:#d9a441}',
			'#cbPanel .bar{height:4px;background:#3d2a18;border-radius:2px;margin-top:4px;overflow:hidden}',
			'#cbPanel .bar i{display:block;height:100%;background:#d9a441;width:0}',
			'#cbPanel .log{color:#b9a385;font-size:11px;max-height:110px;overflow:hidden}',
			'#cbPanel .combo{color:#ffcf5a;font-weight:bold}',
		].join('');
		document.head.appendChild(css);

		var panel = document.createElement('div');
		panel.id = 'cbPanel';
		var TOGGLES = [
			['click', 'Автоклик'], ['golden', 'Золотые'], ['buy', 'Покупки'], ['luckyBank', 'Lucky-банк'],
			['buyBuildings', 'Здания'], ['buyUpgrades', 'Апгрейды'], ['spells', 'Заклинания'], ['lumps', 'Кубики'],
			['fortune', 'Предсказания'], ['wrath', 'Гневные'], ['wrinklers', 'Морщинники'], ['market', 'Биржа'], ['marketBrokers', 'Брокеры'], ['marketCheat', 'Видеть тренд'], ['lumpSpend', 'Тратить кубики'], ['pantheon', 'Пантеон'],
		];
		panel.innerHTML =
			'<div class="hd"><span class="dot" id="cbDot"></span><b>Cookie Bot</b>' +
			'<button id="cbToggle"></button><button id="cbHide" title="Shift+H">–</button></div>' +
			'<div class="sec" id="cbTarget"></div>' +
			'<div class="sec grid">' +
			TOGGLES.map(function (t) { return '<label><input type="checkbox" data-k="' + t[0] + '">' + t[1] + '</label>'; }).join('') +
			'</div>' +
			'<div class="sec" id="cbStats"></div>' +
			'<div class="sec log" id="cbLog"></div>';
		document.body.appendChild(panel);

		function placePanel() {
			var x = CB.cfg.panelX == null ? window.innerWidth - 300 : CB.cfg.panelX;
			panel.style.left = Math.max(0, Math.min(x, window.innerWidth - 60)) + 'px';
			panel.style.top = Math.max(0, Math.min(CB.cfg.panelY, window.innerHeight - 40)) + 'px';
			panel.style.display = CB.cfg.panel ? 'block' : 'none';
		}

		panel.querySelectorAll('input[data-k]').forEach(function (el) {
			el.addEventListener('change', function () {
				CB.cfg[el.dataset.k] = el.checked;
				lastRank = 0;
			});
		});
		panel.querySelector('#cbToggle').onclick = function () { CB.toggle(); };
		panel.querySelector('#cbHide').onclick = function () { CB.cfg.panel = false; placePanel(); };

		// Drag by header.
		(function () {
			var hd = panel.querySelector('.hd'), sx, sy, ox, oy, drag = false;
			hd.addEventListener('mousedown', function (e) {
				if (e.target.tagName === 'BUTTON') return;
				drag = true; sx = e.clientX; sy = e.clientY; ox = panel.offsetLeft; oy = panel.offsetTop;
				e.preventDefault();
			});
			window.addEventListener('mousemove', function (e) {
				if (!drag) return;
				// Button already released (mouseup lost outside the window, etc.).
				if (e.buttons === 0) { drag = false; return; }
				CB.cfg.panelX = ox + e.clientX - sx;
				CB.cfg.panelY = oy + e.clientY - sy;
				placePanel();
			});
			// Capture phase: the panel stops mouseup from bubbling to the game,
			// so a bubbling listener here never fired and the drag stuck.
			window.addEventListener('mouseup', function () { drag = false; }, true);
			window.addEventListener('blur', function () { drag = false; });
		})();
		// Keep clicks on the panel from reaching the game underneath.
		['mousedown', 'mouseup', 'click'].forEach(function (ev) {
			panel.addEventListener(ev, function (e) { e.stopPropagation(); });
		});
		window.addEventListener('resize', placePanel);

		function fmtTime(sec) {
			if (!isFinite(sec)) return '∞';
			if (sec < 1) return 'сейчас';
			return Game.sayTime(sec * Game.fps, -1);
		}
		function row(k, v) { return '<div class="row"><span class="k">' + k + '</span><span>' + v + '</span></div>'; }

		function render() {
			if (!CB.cfg.panel) return;
			var on = CB.cfg.enabled;
			panel.querySelector('#cbDot').className = 'dot' + (on ? ' on' : '');
			panel.querySelector('#cbToggle').textContent = on ? 'Стоп' : 'Старт';
			panel.querySelectorAll('input[data-k]').forEach(function (el) { el.checked = !!CB.cfg[el.dataset.k]; });

			// Target block.
			var t = CB.target, html = '';
			var income = baseIncome();
			if (inCombo()) html += '<div class="combo">КОМБО ×' + Beautify(buffMult('multClick') * buffMult('multCpS'), 1) + '</div>';
			if (t && isFinite(t.score)) {
				var price = t.kind === 'b' ? t.obj.price : t.obj.getPrice();
				var have = Math.max(0, Game.cookies - (t.gain > 0 && price / t.gain < 60 ? 0 : luckyReserve()));
				var pct = Math.min(100, have / price * 100);
				html += row('Цель', (t.kind === 'u' ? '⬆ ' : '') + (t.obj.dname || t.obj.name));
				html += row('Цена', Beautify(price));
				html += row('Прирост', t.gain > 0 ? '+' + Beautify(t.gain, 1) + '/с' : '—');
				html += row('Через', fmtTime((price - have) / Game.cookiesPs));
				html += '<div class="bar"><i style="width:' + pct.toFixed(1) + '%"></i></div>';
			} else html += row('Цель', CB.cfg.buy ? 'нет' : 'покупки выкл');
			var res = luckyReserve();
			if (res > 0) html += row('Lucky-банк', Beautify(res));
			panel.querySelector('#cbTarget').innerHTML = html;

			// Stats block.
			var s = CB.stats;
			var up = (Date.now() - s.t0) / 1000;
			var hc = Math.floor(Game.HowMuchPrestige(Game.cookiesReset + Game.cookiesEarned) - Game.prestige);
			var M = grimoire();
			panel.querySelector('#cbStats').innerHTML =
				row('Доход', Beautify(income, 1) + '/с') +
				row('Заработано ботом', Beautify(Game.cookiesEarned - s.earnedStart)) +
				row('Клики / золотые', Beautify(s.clicks) + ' / ' + s.golden) +
				row('Покупок / заклинаний', s.bought + ' / ' + s.spells) +
				(M ? row('Магия', Math.floor(M.magic) + ' / ' + M.magicM) : '') +
				(market() ? row('Биржа: прибыль', Beautify(CB.stats.marketProfit || 0)) +
					row('Биржа: позиций / брокеров', market().goodsById.filter(function (g) { return g.stock > 0; }).length + ' / ' + market().brokers) : '') +
				row('Престиж при вознесении', hc > 0 ? '+' + Beautify(hc) : '0') +
				row('Работает', fmtTime(up)) +
				row('Терминал', CB.bridge.connected ? 'подключён' : 'нет') +
				(CB.bridge.err ? '<div class="k" style="color:#e07a5f">' + CB.bridge.err + '</div>' : '');
			panel.querySelector('#cbLog').innerHTML = CB.log.length ? CB.log.join('<br>') : 'лог пуст';
		}
		setInterval(render, 500);

		// ---------- console bridge ----------
		// The terminal UI (tui/cookie_tui.py) runs a tiny HTTP server on Linux.
		// Every 500 ms the mod posts a state snapshot and gets queued commands back.
		// text/plain body keeps the request "simple" (no CORS preflight).
		var BRIDGE = 'http://127.0.0.1:17777/sync';
		// Lets the terminal tell game instances apart and stick to one.
		var INSTANCE = Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
		CB.bridge = { connected: false, hidden: false, patched: false, lastOk: 0 };
		var bridgeBusy = false;

		if (window.api && window.api.receive) {
			window.api.receive('fromMain', function (msg) {
				if (msg && msg.id === 'cb window') {
					CB.bridge.patched = true;
					CB.bridge.hidden = !(msg.data && msg.data.visible);
				}
			});
		}
		function setWindow(mode) {
			if (!window.api) return;
			window.api.send('toMain', { id: 'cb window', mode: mode });
			var hide = mode === 'hide';
			CB.bridge.hidden = hide;
			// Mute while hidden without touching saved volume settings.
			if (typeof Music !== 'undefined' && Music) Music.setVolume(hide ? 0 : Game.volumeMusic / 100);
		}
		CB.setWindow = setWindow;
		var origPlaySound = window.PlaySound;
		window.PlaySound = function () {
			if (CB.bridge.hidden) return;
			return origPlaySound.apply(this, arguments);
		};
		// Ask the patched main process for window state; no reply = start.js not patched.
		if (window.api) window.api.send('toMain', { id: 'cb window', mode: 'state' });

		// Income/bank history for terminal graphs: one sample per 5 s, last 30 min.
		CB.hist = [];
		setInterval(function () {
			CB.hist.push([Math.round(Date.now() / 1000), baseIncome(), Game.cookies]);
			if (CB.hist.length > 360) CB.hist.shift();
		}, 5000);

		// Game.AchievementsById is an object keyed by id, not an array.
		var achievTotalCache = 0;
		function achievTotal() {
			if (!achievTotalCache) for (var i in Game.AchievementsById) if (Game.AchievementsById[i].pool !== 'shadow') achievTotalCache++;
			return achievTotalCache;
		}
		function snapshot() {
			var t = CB.target, M = grimoire(), K = market();
			var tgt = null;
			if (t && isFinite(t.score)) {
				var price = t.kind === 'b' ? t.obj.price : t.obj.getPrice();
				tgt = { name: t.obj.name, kind: t.kind, id: t.obj.id, why: t.why || '', price: price, gain: t.gain,
					eta: Math.max(0, (price - Game.cookies) / Math.max(Game.cookiesPs, 1e-9)) };
			}
			var buffs = [];
			for (var i in Game.buffs) {
				var b = Game.buffs[i];
				buffs.push({ name: b.name, left: b.time / Game.fps, cps: b.multCpS || 1, click: b.multClick || 1 });
			}
			var cfg = {};
			for (var k in DEFAULTS) if (typeof DEFAULTS[k] === 'boolean') cfg[k] = !!CB.cfg[k];
			return {
				v: 1, now: Date.now(), name: Game.bakeryName,
				instance: INSTANCE, steam: typeof App !== 'undefined' && !!App,
				enabled: !!CB.cfg.enabled, cfg: cfg,
				cookies: Game.cookies, cps: Game.cookiesPs, income: baseIncome(),
				earned: Game.cookiesEarned, botEarned: Game.cookiesEarned - CB.stats.earnedStart,
				prestigeGain: Math.floor(Game.HowMuchPrestige(Game.cookiesReset + Game.cookiesEarned) - Game.prestige),
				prestige: Game.prestige, lumps: Game.lumps,
				achievements: [Game.AchievementsOwned, achievTotal()],
				buildings: Game.BuildingsOwned, upgrades: Game.UpgradesOwned,
				target: tgt, reserve: luckyReserve(),
				combo: inCombo() ? buffMult('multClick') * buffMult('multCpS') : 0,
				buffs: buffs,
				shimmers: Game.shimmers.length,
				stats: CB.stats,
				magic: M ? [M.magic, M.magicM] : null,
				market: K ? {
					brokers: K.brokers, maxBrokers: K.getMaxBrokers(),
					goods: K.goodsById.filter(function (g) { return g.active; }).map(function (g) {
						return { sym: SYMBOLS[g.id] || g.symbol, val: g.val, hist: g.vals.slice(0, 48), mode: CB.cfg.marketCheat ? g.mode : null, rest: K.getRestingVal(g.id),
							stock: g.stock, max: K.getGoodMaxStock(g), paid: CB.boughtAt[g.id] || null };
					}),
				} : null,
				log: CB.log,
				plan: CB.plan,
				grimoire: CB.grimoireInfo(),
				heaven: CB.heavenInfo(),
				garden: CB.gardenInfo(),
				pantheon: (function () {
					var T = Game.Objects['Temple'], P = T && T.minigameLoaded ? T.minigame : null;
					if (!P) return null;
					return { swaps: P.swaps, slots: P.slot.map(function (id) { return id === -1 ? '' : P.godsById[id].name.split(',')[0]; }) };
				})(),
				dragon: Game.Has('A crumbly egg') ? {
					level: Game.dragonLevel, name: Game.dragonLevels[Game.dragonLevel].name,
					next: Game.dragonLevel < 5 ? 'next stage ' + Beautify(1e6 * Math.pow(2, Game.dragonLevel)) : (Game.dragonLevel < Game.dragonLevels.length - 1 ? 'needs building sacrifices (manual)' : 'fully trained'),
					aura: Game.dragonAuras[Game.dragonAura].name, aura2: Game.dragonAura2 ? Game.dragonAuras[Game.dragonAura2].name : '',
				} : null,
				lumpRipe: Game.canLumps() ? Math.max(0, (Game.lumpRipeAge - (Date.now() - Game.lumpT)) / 1000) : null,
				ascend: ascendAdvice(),
				wrinklers: (function () { var ws = aliveWrinklers(), t = 0; ws.forEach(function (w) { t += wrinklerPayout(w); }); return { n: ws.length, max: Game.getWrinklersMax(), payout: t }; })(),
				hist: CB.hist,
				objects: Game.ObjectsById.filter(function (o) { return available(o) || (CB.target && CB.target.obj === o); }).map(function (o) {
					return { id: o.id, name: o.name, amount: o.amount, cps: o.storedTotalCps * Game.globalCpsMult, price: o.price };
				}),
				spellReady: (function () { var G = grimoire(); return G ? G.magic >= G.getSpellCost(G.spells['hand of fate']) : null; })(),
				ascendMode: Game.ascensionMode, startDate: Game.startDate,
				window: { hidden: CB.bridge.hidden, patched: CB.bridge.patched },
			};
		}

		function runCmd(c) {
			if (!c || !c.op) return;
			if (c.op === 'toggle' && c.key in CB.cfg) {
				CB.cfg[c.key] = !CB.cfg[c.key];
				lastRank = 0;
				// Console mode applies right away, not only on the next connect.
				if (c.key === 'consoleMode' && CB.bridge.patched) setWindow(CB.cfg.consoleMode ? 'hide' : 'show');
			}
			else if (c.op === 'set' && c.key in CB.cfg) { CB.cfg[c.key] = c.value; lastRank = 0; }
			else if (c.op === 'bot') CB.toggle();
			else if (c.op === 'window') setWindow(c.mode);
			else if (c.op === 'ascend') { CB.ascendNow = true; note('ascension requested from terminal'); }
			else if (c.op === 'save') { Game.WriteSave(); note('game saved'); }
			else if (c.op === 'quit') {
				Game.WriteSave();
				setTimeout(function () { window.api && window.api.send('toMain', { id: 'quit' }); }, 1500);
			}
			render();
		}

		function bridgeStep() {
			if (bridgeBusy) return;
			var body;
			try { body = JSON.stringify(snapshot()); }
			catch (e) { CB.bridge.err = 'snapshot: ' + e.message; return; }
			bridgeBusy = true;
			fetch(BRIDGE, { method: 'POST', headers: { 'Content-Type': 'text/plain' }, body: body })
				.then(function (r) { if (!r.ok) throw new Error('rejected (' + r.status + ')'); return r.json(); })
				.then(function (d) {
					var was = CB.bridge.connected;
					CB.bridge.connected = true;
					CB.bridge.lastOk = Date.now();
					CB.bridge.err = '';
					if (!was) note('terminal connected');
					if (!was && CB.cfg.consoleMode && CB.bridge.patched) setWindow('hide');
					(d.cmds || []).forEach(runCmd);
				})
				.catch(function (e) {
					CB.bridge.err = 'fetch: ' + (e && e.message);
					if (CB.bridge.connected) note('terminal disconnected');
					CB.bridge.connected = false;
				})
				.then(function () {
					bridgeBusy = false;
					// Console mode = background run: stay hidden after the terminal closes.
					// Otherwise never stay invisible with nobody controlling the game.
					if (CB.bridge.hidden && !CB.cfg.consoleMode && Date.now() - CB.bridge.lastOk > 15000) {
						setWindow('show');
						note('terminal gone, window shown');
					}
				});
		}
		setInterval(bridgeStep, 500);

		// ---------- hotkeys ----------
		document.addEventListener('keydown', function (e) {
			if (!e.shiftKey || e.ctrlKey || e.altKey) return;
			if (e.code === 'KeyB') CB.toggle();
			else if (e.code === 'KeyH') { CB.cfg.panel = !CB.cfg.panel; placePanel(); render(); }
		});

		placePanel();
		if (CB.cfg.enabled) CB.start(); else render();
	},

	// Persist settings and lifetime counters inside the game save.
	save: function () {
		return JSON.stringify({ cfg: this.cfg, stats: this.stats, boughtAt: this.boughtAt, pHist: this.pHist });
	},
	load: function (str) {
		try {
			var d = JSON.parse(str);
			if (d.cfg) Object.assign(this.cfg, d.cfg);
			if (d.boughtAt) this.boughtAt = Object.assign(this.boughtAt || {}, d.boughtAt);
			if (d.pHist && d.pHist.length) this.pHist = d.pHist.slice(-48);
			if (d.stats) Object.assign(this.stats, d.stats, { earnedStart: Game.cookiesEarned, t0: Date.now() });
			var p = document.getElementById('cbPanel');
			if (p) {
				p.style.display = this.cfg.panel ? 'block' : 'none';
				if (this.cfg.panelX != null) { p.style.left = this.cfg.panelX + 'px'; p.style.top = this.cfg.panelY + 'px'; }
			}
			if (this.cfg.enabled) this.start(); else this.stop();
		} catch (e) { console.log('Cookie Bot: bad save data', e); }
	},
});

// meter_hook.js — persistent hook feeding the Farever Book party meter.
// Resolves the HL functions_ptrs table, finds the local hero, and streams every
// player's damage (ent.Unit.onInflictDamage) to Python as {kind:'hit', ...}.
// DATA (resolver_data.json) and OFF (meter_offsets.json) are prepended by the
// Python host.

function log(m) { send({ kind: "log", msg: String(m) }); }

// ---- leaving the game cleanly ----
// Unloading while a game thread is inside a hook crashes the game (measured
// 2026-09-28): an onLeave hook's thread returns into freed trampoline code.
// So the host calls shutdown() first, waits for threads to leave, then unloads.
// Repeating timers go through every() so shutdown() can stop them all
// (setInterval is read-only in Frida's runtime).
const TIMERS = [];
function every(fn, ms) {
    const t = setInterval(fn, ms);
    TIMERS.push(t);
    return t;
}
let STOPPING = false;
rpc.exports = {
    shutdown: function () {
        STOPPING = true;
        TIMERS.forEach(function (t) { clearInterval(t); });
        Interceptor.detachAll();
        Interceptor.flush();
        return true;
    }
};

function ptrPattern(addr) {
    const b = []; let v = uint64(addr.toString());
    for (let i = 0; i < 8; i++) { b.push(("0" + v.and(0xff).toNumber().toString(16)).slice(-2)); v = v.shr(8); }
    return b.join(" ");
}
function resolveAnchors() {
    const out = [], cache = {};
    for (const a of DATA.anchors) {
        try {
            if (!(a.module in cache)) cache[a.module] = Process.findModuleByName(a.module);
            const m = cache[a.module]; if (!m) continue;
            const ad = m.findExportByName(a.symbol);
            if (ad && !ad.isNull()) out.push({ findex: a.findex, addr: ad });
        } catch (e) {}
    }
    return out;
}
function slotIsCode(base, fi) {
    if (fi === undefined) return true;      // nothing to check against
    try {
        const r = Process.findRangeByAddress(base.add(fi * 8).readPointer());
        return r !== null && r.protection.indexOf("x") >= 0;
    } catch (e) { return false; }
}
function isTableAt(base, resolved) {
    // three exact anchor matches is conclusive; any mismatch rejects
    let n = 0;
    for (const o of resolved) {
        let v; try { v = base.add(o.findex * 8).readPointer(); } catch (e) { return false; }
        if (!v.equals(o.addr)) return false;
        if (++n >= 3) return true;
    }
    return n > 0;
}

// Fast path: functions_ptrs is reachable from the exe's or libhl.dll's statics
// within two pointer hops (static -> hl_module -> functions_ptrs). No struct
// layout is assumed: every private-rw pointer reached is tested via isTableAt.
// Each module is walked separately, exe first, with its own node cap: measured
// 2026-10-02, the table is at Farever.exe+178888 -> +16 -> +32 (92 nodes),
// while libhl's seeds alone reach ~32k nodes by the second hop.
function findTableFast(resolved) {
    if (!resolved.length) return null;
    const t0 = Date.now(), BUDGET_MS = 8000, MAX_NODES = 60000, EXPAND = 64;
    const heap = Process.enumerateRanges("rw-").filter(r => !r.file)
        .sort((a, b) => a.base.compare(b.base));
    if (!heap.length) return null;
    const lo = heap[0].base;
    const hi = heap[heap.length - 1].base.add(heap[heap.length - 1].size);
    function inHeap(p) {
        if (p.compare(lo) < 0 || p.compare(hi) >= 0) return false;
        let a = 0, b = heap.length - 1;
        while (a <= b) {
            const mid = (a + b) >> 1, r = heap[mid];
            if (p.compare(r.base) < 0) b = mid - 1;
            else if (p.compare(r.base.add(r.size)) >= 0) a = mid + 1;
            else return true;
        }
        return false;
    }
    // seeds: every 8-aligned qword in a module's statics pointing into private rw-
    function seedsOf(m) {
        const out = [];
        let secs; try { secs = m.enumerateRanges("rw-"); } catch (e) { return out; }
        for (const sec of secs) {
            const CH = 65536;
            for (let off = 0; off < sec.size && out.length < 50000; off += CH) {
                const n = Math.min(CH, sec.size - off);
                let buf; try { buf = sec.base.add(off).readByteArray(n); } catch (e) { continue; }
                const dv = new DataView(buf);
                for (let i = 0; i + 8 <= n; i += 8) {
                    const l = dv.getUint32(i, true), h = dv.getUint32(i + 4, true);
                    if ((l === 0 && h === 0) || (l & 7)) continue;
                    const v = ptr(h).shl(32).or(l);
                    if (inHeap(v)) out.push(v);
                }
            }
        }
        return out;
    }
    const mods = [Process.enumerateModules()[0], Process.findModuleByName("libhl.dll")];
    const tried = [];
    for (const m of mods) {
        if (!m) continue;
        let frontier = seedsOf(m);
        const seen = new Set();
        const parentOf = new Map();         // node -> [the pointer it was read from, its slot]
        let gaveUp = false;
        for (let depth = 0; depth <= 2 && frontier.length && !gaveUp; depth++) {
            const next = [];
            for (const p of frontier) {
                const k = p.toString();
                if (seen.has(k)) continue;
                seen.add(k);
                if (seen.size > MAX_NODES || Date.now() - t0 > BUDGET_MS) { gaveUp = true; break; }
                if (isTableAt(p, resolved)) {
                    // read from hl_module.functions_ptrs (+32): that is the
                    // module, its globals at +16 (indexes) and +24 (data)
                    const par = parentOf.get(k);
                    if (par && par[1] === 4) hlModule = par[0];
                    return p;
                }
                if (depth === 2) continue;
                for (let i = 0; i < EXPAND; i++) {
                    let v; try { v = p.add(i * 8).readPointer(); } catch (e) { break; }
                    if (v.and(7).toInt32() === 0 && inHeap(v)) {
                        const vk = v.toString();
                        if (!parentOf.has(vk)) parentOf.set(vk, [p, i]);
                        next.push(v);
                    }
                }
            }
            frontier = next;
        }
        tried.push(m.name + ": " + seen.size + " nodes" + (gaveUp ? " (cap reached)" : ""));
    }
    log("statics walk: " + tried.join(", ") + ", " + (Date.now() - t0) + " ms");
    return null;
}

function findTableBase(resolved) {
    // functions_ptrs lives in anonymous rw- memory: scan anonymous ranges first,
    // smallest first. Keep the size cap generous: HL heap segments can exceed
    // 128 MiB, and a cap below the table's segment makes it unfindable.
    const ranges = Process.enumerateRanges("rw-")
        .filter(r => r.size < 0x40000000)
        .sort((a, b) => ((a.file ? 1 : 0) - (b.file ? 1 : 0)) || (a.size - b.size));
    // a matching build hits on the first seed; cap low to fail fast on a
    // mismatched one (the meter then regenerates the data)
    const seeds = Math.min(resolved.length, 3);
    const total = ranges.length * seeds;
    let done = 0, lastProg = Date.now();
    for (let s = 0; s < seeds; s++) {
        const seed = resolved[s], pat = ptrPattern(seed.addr);
        for (const r of ranges) {
            // heartbeat: tells the host the scan is alive, not hung
            done++;
            const now = Date.now();
            if (now - lastProg > 1500) {
                lastProg = now;
                send({ kind: "progress", done: done, total: total });
            }
            let mm; try { mm = Memory.scanSync(r.base, r.size, pat); } catch (e) { continue; }
            for (const m of mm) {
                const base = m.address.sub(seed.findex * 8);
                if (base.compare(r.base) < 0) continue;
                let agree = 0, checked = 0;
                for (const o of resolved) {
                    if (o === seed) continue;
                    const slot = base.add(o.findex * 8);
                    if (slot.compare(r.base) < 0 || slot.add(8).compare(r.base.add(r.size)) > 0) continue;
                    checked++;
                    let v; try { v = slot.readPointer(); } catch (e) { continue; }
                    if (v.equals(o.addr)) agree++;
                    if (checked >= 8) break;
                }
                if (agree >= 3) return base;
            }
        }
    }
    return null;
}
function hlStr(p) {
    try {
        if (!p || p.isNull()) return null;
        const b = p.add(OFF.String.bytes).readPointer();
        if (b.isNull()) return null;
        return b.readUtf16String();
    } catch (e) { return null; }
}
// Cached by type pointer (HL type descriptors live for the whole process):
// hundreds of entities per sweep share a few dozen classes.
const typeNameCache = {};
function typeName(p) {
    try {
        if (!p || p.isNull()) return null;
        const t = p.readPointer();
        const key = t.toString();
        const hit = typeNameCache[key];
        if (hit !== undefined) return hit;
        const k = t.readU32();
        const nm = (k === 11 || k === 21)
            ? t.add(8).readPointer().add(16).readPointer().readUtf16String()
            : "kind" + k;
        typeNameCache[key] = nm;
        return nm;
    } catch (e) { return null; }
}

let base = null;
let getHeroFns = [];      // [{addr}]
let localHero = null;
let localName = null;
let pickedHero = null;      // {id, name}: the character picked on the select screen
let hlModule = null;        // the game's hl_module (its globals), when the walk found it

// ---- the bars' values (an option of the app's, off by default) ----
// The game's own bars write "value / max" in themselves when its developer
// preference DevPrefs.showRessources is on (AttributeBar.sync): set in its
// memory, never saved (the game's onChange is not called). Off again: the
// value it had. Re-set every few seconds (the game may read its prefs anew).
let barValuesWanted = false, barValuesWas = null;
let escButtonWanted = true;     // the Farever Book button in the game's menu (an option, on)
// the targeting options (the app's): the indicator's size (%) and halo
// (0xRRGGBB or null), the health bars' frame (px more than the game's),
// their colours (0xRRGGBB, null: the game's) on a foe targeting the hero,
// an aggressive one, a neutral one; the outline of the targeted foe's bar
// (0xRRGGBB or null, its width in px)
let targetScale = 100, frameExtra = 0, targetGlow = null;
let targetLine = null, targetLineWidth = 2;
// the colour of the life being lost (by the same kinds), the bars' background
let lagColors = { mine: null, aggro: null, neutral: null }, barBg = null;
let levelScale = 100;           // the level beside the bar (%), on top of the bar's growth
// the halo's and the outline's blur (px), the level shown, its digits'
// colour and its picture's tint (0xRRGGBB or null), its offset (px)
let glowBlur = 5, lineBlur = 0, levelShow = true, levelText = null, levelTint = null;
let levelDx = 0, levelDy = 0;
// the text in the bars, the monsters' and the bosses': "none", "pct",
// "value", "max" (value / max); the bosses' bars: their colours (fill,
// life being lost, background: 0xRRGGBB or null), thickness, level
let foeText = "none", bossText = "none";
let bossColor = null, bossLag = null, bossBg = null, bossExtra = 0;
let bossLevelShow = true, bossLevelScale = 100, bossLevelDx = 0, bossLevelDy = 0;
let foeWidth = 0;               // the monsters' bars wider (px, may be less)
// the text in the bars: its size (%), its outline (0xRRGGBB or null, px)
let foeTextScale = 100, bossTextScale = 100;
// the allies' (players') bars: thicker, wider (px), their level (shown, %),
// their name in the bar (its own place hidden)
let allyExtra = 0, allyWidth = 0, allyLevelShow = true, allyLevelScale = 100;
// the allies' text (as the monsters'), their life's colours (0xRRGGBB or
// null), and every life's gradient end (null: the game's, in its ratio)
let allyText = "none", allyColor = null, allyColorEnd = null, allyLag = null, allyBg = null;
let barColorsEnd = { mine: null, aggro: null, neutral: null }, bossColorEnd = null;
// their level's place (px from the game's), their class's icon ("none",
// "replace": in the level's square, "beside": apart), its size (%), its place
let allyLevelDx = 0, allyLevelDy = 0, allyClass = "none", allyClassScale = 100;
let allyClassDx = 0, allyClassDy = 0;
let targetGlowA = 100, targetLineA = 100;       // the halo's, the target's frame's (%)
let barColors = { mine: null, aggro: null, neutral: null };
function hlGlobal(g) {
    // a global's value (an object), by its index: hl_module's globals
    const idx = hlModule.add(16).readPointer().add(g * 4).readS32();
    return hlModule.add(24).readPointer().add(idx).readPointer();
}
function devPrefs() {
    if (!hlModule || !OFF.Config || OFF.Config.global == null || !OFF.DevPrefs) return null;
    try {
        const cfg = hlGlobal(OFF.Config.global);
        if (typeName(cfg) !== "$Config") return null;
        const prefs = cfg.add(OFF.Config.prefs).readPointer();
        return typeName(prefs) === "DevPrefs" ? prefs : null;
    } catch (e) { return null; }
}
function applyBarValues() {
    const prefs = devPrefs();
    if (!prefs) return false;
    try {
        const at = prefs.add(OFF.DevPrefs.showRessources);
        const cur = at.readU8();
        if (barValuesWanted) {
            if (barValuesWas === null) barValuesWas = cur;
            if (cur !== 1) at.writeU8(1);
        } else if (barValuesWas !== null) {
            if (cur !== barValuesWas) at.writeU8(barValuesWas);
            barValuesWas = null;
        }
        return true;
    } catch (e) { return false; }
}
function listenConfig() {
    recv("config", function (msg) {
        escButtonWanted = msg.escButton !== false;
        targetScale = Math.max(50, Math.min(300, msg.targetScale | 0 || 100));
        frameExtra = Math.max(0, Math.min(16, msg.frameExtra | 0));
        const rgb = v => (typeof v === "number" && v >= 0 && v <= 0xFFFFFF) ? v : null;
        targetGlow = rgb(msg.targetGlow);
        targetLine = rgb(msg.targetLine);
        targetLineWidth = Math.max(1, Math.min(6, msg.targetLineWidth | 0 || 2));
        const bc = msg.barColors || {};
        barColors = { mine: rgb(bc.mine), aggro: rgb(bc.aggro), neutral: rgb(bc.neutral) };
        const lc = msg.lagColors || {};
        lagColors = { mine: rgb(lc.mine), aggro: rgb(lc.aggro), neutral: rgb(lc.neutral) };
        barBg = rgb(msg.barBg);
        levelScale = Math.max(50, Math.min(250, msg.levelScale | 0 || 100));
        glowBlur = Math.max(1, Math.min(16, msg.glowBlur | 0 || 5));
        lineBlur = Math.max(0, Math.min(16, msg.lineBlur | 0));
        levelShow = msg.levelShow !== false;
        levelText = rgb(msg.levelText);
        levelTint = rgb(msg.levelTint);
        levelDx = Math.max(-60, Math.min(60, msg.levelDx | 0));
        levelDy = Math.max(-60, Math.min(60, msg.levelDy | 0));
        const mode = v => ["none", "pct", "value", "max", "max_pct"].indexOf(v) >= 0 ? v : "none";
        foeText = mode(msg.foeText);
        bossText = mode(msg.bossText);
        bossColor = rgb(msg.bossColor);
        bossLag = rgb(msg.bossLag);
        bossBg = rgb(msg.bossBg);
        bossExtra = Math.max(0, Math.min(16, msg.bossExtra | 0));
        bossLevelShow = msg.bossLevelShow !== false;
        bossLevelScale = Math.max(50, Math.min(250, msg.bossLevelScale | 0 || 100));
        bossLevelDx = Math.max(-80, Math.min(80, msg.bossLevelDx | 0));
        bossLevelDy = Math.max(-80, Math.min(80, msg.bossLevelDy | 0));
        foeWidth = Math.max(-60, Math.min(200, msg.foeWidth | 0));
        foeTextScale = Math.max(50, Math.min(250, msg.foeTextScale | 0 || 100));
        allyExtra = Math.max(0, Math.min(16, msg.allyExtra | 0));
        allyWidth = Math.max(-60, Math.min(200, msg.allyWidth | 0));
        allyLevelShow = msg.allyLevelShow !== false;
        allyText = ["none", "pct", "value", "max", "max_pct"].indexOf(msg.allyText) >= 0
            ? msg.allyText : "none";
        allyColor = rgb(msg.allyColor);
        allyColorEnd = rgb(msg.allyColorEnd);
        allyLag = rgb(msg.allyLag);
        allyBg = rgb(msg.allyBg);
        bossColorEnd = rgb(msg.bossColorEnd);
        const be = msg.barColorsEnd || {};
        barColorsEnd = { mine: rgb(be.mine), aggro: rgb(be.aggro), neutral: rgb(be.neutral) };
        allyLevelScale = Math.max(20, Math.min(250, msg.allyLevelScale | 0 || 100));
        allyLevelDx = Math.max(-120, Math.min(120, msg.allyLevelDx | 0));
        allyLevelDy = Math.max(-120, Math.min(120, msg.allyLevelDy | 0));
        allyClass = msg.allyClass === "beside" ? "beside" : "none";
        allyClassScale = Math.max(20, Math.min(300, msg.allyClassScale | 0 || 100));
        allyClassDx = Math.max(-300, Math.min(300, msg.allyClassDx | 0));
        allyClassDy = Math.max(-300, Math.min(300, msg.allyClassDy | 0));
        bossTextScale = Math.max(50, Math.min(250, msg.bossTextScale | 0 || 100));
        targetGlowA = Math.max(0, Math.min(100, msg.targetGlowA == null ? 100 : msg.targetGlowA | 0));
        targetLineA = Math.max(0, Math.min(100, msg.targetLineA == null ? 100 : msg.targetLineA | 0));
        const want = !!msg.barValues;
        if (want !== barValuesWanted) {
            barValuesWanted = want;
            const ok = applyBarValues();
            log("bar values " + (want ? "on" : "off")
                + (ok ? "" : " (the game's preferences not reached)"));
        }
        listenConfig();                 // recv is one-shot: listen again
    });
}
let partyNames = {};      // set of names in the local player's group (incl. self)
const heroByName = {};    // name -> {ptr: ent.Hero*, t: last-seen ms} (from hits)

function inCombat(hero) {
    try {
        if (!hero || hero.isNull()) return 0;
        return hero.add(OFF.Hero.isInCombat).readU8() ? 1 : 0;
    } catch (e) { return 0; }
}

// ---- rift + zone + shard detection ----
// hero -> st.State.layer -> st.GameLayer: isRift, .world.level (zone),
// .serverName (shard). Plain reads, no HL calls, so timer-safe. Reported on
// change only.
let lastRift = null;
let lastLevel = null;
let lastServer = null;
// ---- dungeons ----
// hero -> layer -> mainActivity is an st.activity.Dungeon while in one; its
// DungeonContext holds state (Explo / BossStart / BossPhase / BossWin /
// BossLoose), times and deaths; difficulty is on the group's instance lobby.
// Timer-safe. Sent on change, and periodically in a dungeon for the clock.
let dungeonSig = null;
let dungeonBeat = 0;
let sweepBeat = 0;

function readLobbies(hero) {
    const out = [];
    try {
        if (!OFF.InstanceLobby || !OFF.Group || OFF.Group.instanceLobbies == null)
            return out;
        const player = hero.add(OFF.Hero.player).readPointer();
        if (!player || player.isNull()) return out;
        const group = player.add(OFF.Player.group).readPointer();
        if (!group || group.isNull()) return out;
        const proxy = group.add(OFF.Group.instanceLobbies).readPointer();
        if (!proxy || proxy.isNull()) return out;
        const dyn = proxy.add(OFF.ArrayProxyData.array).readPointer();
        if (!dyn || dyn.isNull()) return out;
        const arr = dyn.add(OFF.ArrayDyn.array).readPointer();
        if (!arr || arr.isNull()) return out;
        const n = arr.add(OFF.ArrayObj.length).readS32();
        const data = arr.add(OFF.ArrayObj.array).readPointer();
        for (let i = 0; i < n && i < 16; i++) {
            const lb = data.add(OFF.ArrayObj.data + i * 8).readPointer();
            if (!lb || lb.isNull()) continue;
            out.push({ a: hlStr(lb.add(OFF.InstanceLobby.activityId).readPointer()),
                       d: lb.add(OFF.InstanceLobby.difficulty).readS32() });
        }
    } catch (e) {}
    return out;
}

function dungeonCtxInfo(ctx) {
    const out = { type: typeName(ctx) };
    try {
        if (out.type && out.type.indexOf("Dungeon") >= 0) {
            const C = OFF.DungeonCtx;
            out.state = hlStr(ctx.add(C.dungeonState).readPointer());
            if (out.type === "st.activity.DungeonContext") {
                out.end = ctx.add(C.endActivity).readDouble();
                out.deaths = ctx.add(C.nbPlayerDeaths).readS32();
            }
        }
    } catch (e) {}
    return out;
}

// Elements of an hl ArrayObj (pointer array), bounded.
function arrayObjItems(arr, max) {
    const out = [];
    if (!arr || arr.isNull()) return out;
    const n = arr.add(OFF.ArrayObj.length).readS32();
    if (n < 0 || n > 256) return out;
    const data = arr.add(OFF.ArrayObj.array).readPointer();
    for (let i = 0; i < n && i < max; i++) {
        const p = data.add(OFF.ArrayObj.data + i * 8).readPointer();
        if (p && !p.isNull()) out.push(p);
    }
    return out;
}

function proxyItems(proxy, max) {
    if (!proxy || proxy.isNull()) return [];
    const dyn = proxy.add(OFF.ArrayProxyData.array).readPointer();
    if (!dyn || dyn.isNull()) return [];
    return arrayObjItems(dyn.add(OFF.ArrayDyn.array).readPointer(), max);
}

function checkDungeon() {
    try {
        if (!localHero || localHero.isNull() || !OFF.DungeonCtx) return;
        const layer = localHero.add(OFF.Hero.layer).readPointer();
        if (!layer || layer.isNull()) return;
        const d = {};
        const player = localHero.add(OFF.Hero.player).readPointer();
        if (player && !player.isNull()) {
            d.lobbies = readLobbies(localHero);
            if (OFF.Player.activityCtx != null) {
                d.playerCtx = proxyItems(
                    player.add(OFF.Player.activityCtx).readPointer(), 8)
                    .map(dungeonCtxInfo);
            }
        }
        const act = layer.add(OFF.GameLayer.mainActivity).readPointer();
        if (act && !act.isNull()) {
            d.type = typeName(act);
            d.kind = hlStr(act.add(OFF.Activity.kind).readPointer());
            if (d.type === "st.activity.Dungeon")
                d.bossId = hlStr(act.add(OFF.Dungeon.bossId).readPointer());
        }
        d.now = serverNowOf(layer);
        const sig = JSON.stringify(Object.assign({}, d, { now: 0 }));
        dungeonBeat++;
        const inDungeon = d.type === "st.activity.Dungeon";
        if (sig !== dungeonSig || (inDungeon && dungeonBeat % 12 === 0)) {
            dungeonSig = sig;
            send({ kind: "dungeon", d: d });
        }
    } catch (e) {}
}

function serverNowOf(layer) {
    try {
        if (!OFF.TimeState || OFF.GameLayer.time == null) return null;
        const ts = layer.add(OFF.GameLayer.time).readPointer();
        if (!ts || ts.isNull()) return null;
        return ts.add(OFF.TimeState.serverNow).readDouble();
    } catch (e) { return null; }
}

function checkRift() {
    try {
        if (!localHero || localHero.isNull() || !OFF.GameLayer) return;
        const layer = localHero.add(OFF.Hero.layer).readPointer();
        if (!layer || layer.isNull()) return;
        // zone identity: layer.world.level names the loaded level (not
        // Main.getMapId(), which returns the machine hostname, measured 2026-08-01)
        if (OFF.GameLayer.world != null && OFF.World
                && OFF.World.level != null) {
            const w = layer.add(OFF.GameLayer.world).readPointer();
            if (w && !w.isNull()) {
                const level = hlStr(w.add(OFF.World.level).readPointer());
                if (level && level !== lastLevel) {
                    const initial = lastLevel === null;
                    lastLevel = level;
                    const out = { kind: "zone", sig: level,
                                  initial: initial ? 1 : 0 };
                    if (!initial) {
                        resetBossBars();
                        // flush pickups, then rebaseline: the re-replicated
                        // loadout must not read as loot
                        sweepInventory();
                        invReady = false;
                    }
                    send(out);
                }
            }
        }
        // shard, sent separately from zone: the two change independently
        if (OFF.GameLayer.serverName != null) {
            const srv = hlStr(layer.add(OFF.GameLayer.serverName).readPointer());
            if (srv !== lastServer) {
                const initial = lastServer === null;
                lastServer = srv;
                send({ kind: "server", name: srv, initial: initial ? 1 : 0 });
            }
        }
        const state = layer.add(OFF.GameLayer.isRift).readU8() !== 0;
        if (state !== lastRift) {
            lastRift = state;
            send({ kind: "rift", state: state ? 1 : 0 });
        }
    } catch (e) {}
}

// ---- the game's cursor: a game window open, or the FreeCursor key ----
// GameApp.shouldFreeCursor() says whether a game window (inventory, map...)
// frees the mouse; GameApp.playerRequestedFreeCursor is the FreeCursor key's
// toggle (Alt), which a window resets. Read every 100 ms, sent when it
// changes and each second (the overlays trust a fresh state only).
let cursorGetApp = null, cursorShould = null, cursorAt = 0, cursorSent = 0;
let cursorLast = "";

function setupCursor(base) {
    const fi = DATA.cursor_fns && DATA.cursor_fns["GameApp.shouldFreeCursor"];
    const gi = DATA.funcs && DATA.funcs["GameApp.get"];
    if (fi == null || gi == null || !OFF.GameApp) {
        log("game cursor state unavailable; the overlays go by the cursor alone");
        return;
    }
    try {
        cursorGetApp = new NativeFunction(base.add(gi * 8).readPointer(), "pointer", []);
        cursorShould = new NativeFunction(base.add(fi * 8).readPointer(), "uint8", ["pointer"]);
    } catch (e) {
        cursorGetApp = cursorShould = null;
        log("game cursor state failed: " + e);
    }
}

// GAME THREAD ONLY.
function cursorTick() {
    if (!cursorShould) return;
    const now = Date.now();
    if (now - cursorAt < 100) return;
    cursorAt = now;
    try {
        const app = cursorGetApp();
        if (!app || app.isNull()) return;
        const win = cursorShould(app) ? 1 : 0;
        const alt = app.add(OFF.GameApp.playerRequestedFreeCursor).readU8() ? 1 : 0;
        // a loading screen: GameApp.get_isLoading (its state not 10)
        let load = 0;
        if (OFF.GameApp.loadingState != null) {
            try { load = app.add(OFF.GameApp.loadingState).readS32() !== 10 ? 1 : 0; } catch (e) {}
        }
        // the game's menu (Échap) open: where it is, in the game's pixels
        let menu = 0;
        if (OFF.GameApp.baseUI != null && OFF.BaseUI && OFF.UIFlow) {
            try {
                const ui = app.add(OFF.GameApp.baseUI).readPointer();
                const wins = (ui && !ui.isNull())
                    ? arrayObjItems(ui.add(OFF.BaseUI.windows).readPointer(), 32) : [];
                for (let i = 0; i < wins.length; i++) {
                    if (typeName(wins[i]) !== "ui.win.EscapeMenu") continue;
                    const F = OFF.UIFlow, w = wins[i];
                    // the scene's pixels to the window's (h2d.Scene: scaled
                    // to the window, offset when its ratio differs)
                    let sx = 1, sy = 1, ox = 0, oy = 0;
                    if (OFF.App && OFF.Scene) {
                        const s2d = app.add(OFF.App.s2d).readPointer();
                        if (s2d && !s2d.isNull()) {
                            sx = s2d.add(OFF.Scene.viewportScaleX).readDouble() || 1;
                            sy = s2d.add(OFF.Scene.viewportScaleY).readDouble() || 1;
                            ox = s2d.add(OFF.Scene.offsetX).readDouble() || 0;
                            oy = s2d.add(OFF.Scene.offsetY).readDouble() || 0;
                        }
                    }
                    menu = [w.add(F.absX).readDouble() * sx + ox,
                            w.add(F.absY).readDouble() * sy + oy,
                            w.add(F.calculatedWidth).readDouble() * w.add(F.matA).readDouble() * sx,
                            w.add(F.calculatedHeight).readDouble() * w.add(F.matD).readDouble() * sy]
                        .map(Math.round);
                    break;
                }
            } catch (e) {}
        }
        const s = win + ":" + alt + ":" + load + ":" + menu;
        if (s !== cursorLast || now - cursorSent > 1000) {
            cursorLast = s;
            cursorSent = now;
            send({ kind: "cursor", win: win, alt: alt, load: load, menu: menu });
        }
    } catch (e) {}
}

// ---- the game-thread tick ----
// HL calls must run on the game thread: from a timer they kill the game ("Can't
// lock GC in unregistered thread"). client.BaseCamera.postUpdate runs every
// frame, so it is hooked as the tick; timers only raise flags it consumes.
function hookGameTick(base) {
    const fi = DATA.cam_targets && DATA.cam_targets["client.BaseCamera.postUpdate"];
    if (fi === undefined) { log("!! camera target missing; local hero will not be found"); return; }
    try {
        Interceptor.attach(base.add(fi * 8).readPointer(), {
            onEnter: function () {
                cursorTick();
                if (heroRefreshDue) { heroRefreshDue = false; refreshLocalHero(); }
                if (rosterDue || analyzeWanted !== null || selfDue) characterTick();
                if (codexDue && hbKeys) {
                    codexDue = false;
                    refreshCodex();
                    refreshElements();
                }
            }
        });
    } catch (e) {
        log("!! game-tick hook failed (" + e + "); local hero will not be found");
    }
}

// ---- boss / elite healthbar ----
// ui.hud.BossesInfo.bossInfos is the game's list of on-screen boss bars.
// Measured: fetchBosses runs at a steady 2/s; the array holds only live bars;
// elites get a bar too (so boss-only rules use isBoss); the bar tracks
// engagement, not existence (it drops when the boss resets).
// Kill vs disengage: the last health seen while the bar was up is 0 on a kill.
const bossClass = {};            // unit kind -> {boss, elite}, classified once
let bossBars = {};               // unit ptr string -> {kind, boss, elite, hp}
let bossLast = "";               // last state signature, to send only on change
let bossFnIsBoss = null;

// ---- the fight ending without a kill (boss reset / team wipe) ----
// A dropped bar alone is not the end of a fight: the Nightqueen swaps herself
// for copies, leaving at least one poll with no bar. So "no boss bar" must
// persist for BOSS_GONE_POLLS polls. Only the observation is reported; the
// host decides whether it was a reset or a victory.
const BOSS_GONE_POLLS = 10;      // 10 polls at 2/s = ~5s of no boss bar
let bossGonePolls = 0;
let bossWasUp = false;           // a boss bar has been up since the last report

function bossUnitHealth(u) {
    try {
        if (!OFF.Unit || OFF.Unit.attr == null || !OFF.UnitAttributes) return null;
        const attr = u.add(OFF.Unit.attr).readPointer();
        if (!attr || attr.isNull() || attr.compare(ptr("0x10000")) <= 0) return null;
        return attr.add(OFF.UnitAttributes.health).readDouble();
    } catch (e) { return null; }
}

// Runs on the game thread (inside the fetchBosses hook), so HL calls are safe.
// Cached per unit kind.
function classifyBossUnit(u, kind) {
    let hit = bossClass[kind];
    if (hit) return hit;
    hit = { boss: false };
    try { if (bossFnIsBoss) hit.boss = !!bossFnIsBoss(u); } catch (e) {}
    bossClass[kind] = hit;
    return hit;
}

// ---- the boss bars' values (the bars' values option) ----
// A boss's bar (top of the screen) is a HealthBar too, its text hidden by
// the game: shown, its update writes "value / max" in it (with the
// showRessources preference, a percentage without). Found under the
// BossInfo among its children; put back hidden when the option goes off.
let bossTextShown = {};         // HealthBar.txt -> true, the texts shown by us
let bossTextHidden = {};        // HealthBar.txt -> true, the texts hidden by us
// the bars' texts (a HealthBar's txt, a gauge's own) -> "foe" or "boss": their
// text written as the player chose when the game gives it
const barTexts = new Map();
// a boss's bar styled (its slot): set by the targeting options
let bossStyler = null;
let setVisibleFn = null;
function findChild(o, type, depth) {
    if (!o || o.isNull() || depth > 4 || OFF.Object.children == null) return null;
    try {
        const arr = o.add(OFF.Object.children).readPointer();
        if (!arr || arr.isNull()) return null;
        const n = arr.add(OFF.ArrayObj.length).readS32();
        const data = arr.add(OFF.ArrayObj.array).readPointer();
        for (let i = 0; i < n && i < 64; i++) {
            const c = data.add(OFF.ArrayObj.data + i * 8).readPointer();
            if (!c || c.isNull()) continue;
            if (typeName(c) === type) return c;
            const deeper = findChild(c, type, depth + 1);
            if (deeper) return deeper;
        }
    } catch (e) {}
    return null;
}
function bossBarValues(slot) {
    if (!setVisibleFn || !OFF.HealthBar || OFF.HealthBar.txt == null) return;
    try {
        const hb = findChild(slot, "ui.comp.HealthBar", 0);
        if (!hb) return;
        const txt = hb.add(OFF.HealthBar.txt).readPointer();
        if (!txt || txt.isNull()) return;
        const key = txt.toString();
        const vis = txt.add(OFF.Object.visible).readU8();
        if (barTexts.size > 800) barTexts.clear();
        barTexts.set(key, "boss");
        const gauge = OFF.HealthBar.healthGauge != null
            ? hb.add(OFF.HealthBar.healthGauge).readPointer() : null;
        if (gauge && !gauge.isNull()) barTexts.set(gauge.toString(), "boss");
        // shown or hidden by its own choice (the game's own given back)
        const want = bossText !== "none";
        if (want && !vis) {
            setVisibleFn(txt, 1);
            if (bossTextHidden[key]) delete bossTextHidden[key];
            else bossTextShown[key] = true;
        } else if (!want && vis) {
            setVisibleFn(txt, 0);
            if (bossTextShown[key]) delete bossTextShown[key];
            else bossTextHidden[key] = true;
        }
        if (bossStyler) bossStyler(slot);
    } catch (e) {}
}

function pollBossBars(bi) {
    if (!bi || bi.isNull() || !OFF.BossesInfo || !OFF.BossInfo || !OFF.ArrayObj)
        return;
    const A = OFF.ArrayObj;
    const now = {};
    try {
        const arr = bi.add(OFF.BossesInfo.bossInfos).readPointer();
        if (arr && !arr.isNull()) {
            const n = arr.add(A.length).readS32();
            if (n > 0 && n < 64) {
                const data = arr.add(A.array).readPointer();
                if (data && !data.isNull()) {
                    for (let i = 0; i < n; i++) {
                        let slot;
                        try { slot = data.add(A.data + i * 8).readPointer(); }
                        catch (x) { continue; }
                        if (!slot || slot.isNull() || slot.compare(ptr("0x10000")) <= 0)
                            continue;
                        if (!slot.add(OFF.BossInfo.active).readU8()) continue;
                        bossBarValues(slot);
                        const u = slot.add(OFF.BossInfo.unit).readPointer();
                        if (!u || u.isNull() || u.compare(ptr("0x10000")) <= 0) continue;
                        const kind = hlStr(u.add(OFF.Unit.kind).readPointer());
                        if (!kind) continue;
                        const cls = classifyBossUnit(u, kind);
                        const hp = bossUnitHealth(u);
                        const key = u.toString();
                        const prev = bossBars[key];
                        now[key] = { kind: kind, boss: cls.boss,
                                     // keep the last reading: the unit may be gone at teardown
                                     hp: hp === null ? (prev ? prev.hp : null) : hp };
                    }
                }
            }
        }
    } catch (e) { return; }

    const up = [], down = [];
    for (const k in now) if (!(k in bossBars)) up.push(now[k]);
    for (const k in bossBars) {
        if (k in now) continue;
        const b = bossBars[k];
        // hp === null means we never got a reading; don't claim a kill.
        down.push({ kind: b.kind, boss: b.boss,
                    killed: b.hp !== null && b.hp <= 0 });
    }
    bossBars = now;

    let anyBoss = false;
    for (const k in now) if (now[k].boss) anyBoss = true;
    // must run before the change gate: it fires precisely when nothing changes
    if (anyBoss) {
        if (bossGonePolls)
            log("boss bar returned after " + bossGonePolls
                + " empty poll(s) — the fight had not ended");
        bossWasUp = true;
        bossGonePolls = 0;
    } else if (bossWasUp) {
        if (++bossGonePolls >= BOSS_GONE_POLLS) {
            bossWasUp = false;
            bossGonePolls = 0;
            send({ kind: "bossgone" });
        }
    }

    // send on change only
    const sig = String(anyBoss);
    if (!up.length && !down.length && sig === bossLast) return;
    bossLast = sig;
    send({ kind: "bossbar", boss: anyBoss, up: up, down: down });
}

function hookBossBar(base) {
    const fi = DATA.boss_targets && DATA.boss_targets["ui.hud.BossesInfo.fetchBosses"];
    if (fi == null) {
        log("!! boss target missing (ui.hud.BossesInfo.fetchBosses); boss-bar "
            + "detection disabled — re-run hltools/build_targets.py");
        return;
    }
    if (!OFF.BossesInfo || !OFF.BossInfo) {
        log("!! boss offsets missing (BossesInfo/BossInfo); boss-bar detection "
            + "disabled. The offsets file predates this build — delete "
            + "analysis_out and restart to regenerate it.");
        return;
    }
    const fns = DATA.boss_fns || {};
    function nf(nm) {
        const f = fns[nm];
        if (f == null) return null;
        try {
            return new NativeFunction(base.add(f * 8).readPointer(),
                                      "uint8", ["pointer"]);
        } catch (e) { return null; }
    }
    bossFnIsBoss = nf("ent.Unit.isBoss");
    const visFi = (DATA.bar_fns || {})["h2d.Object.set_visible"];
    if (visFi != null && OFF.Object && OFF.Object.visible != null) {
        try {
            setVisibleFn = new NativeFunction(base.add(visFi * 8).readPointer(),
                                              "uint8", ["pointer", "uint8"]);
        } catch (e) { setVisibleFn = null; }
    }
    if (!bossFnIsBoss)
        log("!! ent.Unit.isBoss unavailable; every bar will count as an elite "
            + "and boss-only rules will never fire");
    try {
        Interceptor.attach(base.add(fi * 8).readPointer(), {
            onEnter: function () { pollBossBars(this.context.rcx); }
        });
        log("boss bar tracking active");
    } catch (e) {
        log("!! boss bar hook failed (" + e + ")");
    }
}

// ---- loot (the inventory sweep) ----
// Feeds the dungeon loot list. Plain pointer reads only.
// st.Inventory.content entries are not items but standalone hl virtuals
// (kind 15) with fields {count:Int, item:st.Item}, found by name:
//
//   hl_type         { kind@0, union@8, vobj_proto@16 }
//   hl_type_virtual { fields@0, nfields@8, dataSize@12, indexes@16 }
//   hl_obj_field    { name@0, type@8, hashed@16 }   — 24 bytes each
//
// and for a standalone virtual the pointer array at v+24 holds each field's
// storage ADDRESS, so a field value is a double deref.
const HVIRTUAL = 15;
const virtFieldIdx = {};        // virtual type ptr -> { field name -> index }

function virtualFieldIndex(t, want) {
    const key = t.toString();
    let map = virtFieldIdx[key];
    if (map === undefined) {
        map = {};
        try {
            const vt = t.add(8).readPointer();
            const fields = vt.readPointer();
            const n = vt.add(8).readS32();
            for (let i = 0; i < n && i < 64; i++) {
                let nm = null;
                try { nm = fields.add(i * 24).readPointer().readUtf16String(); }
                catch (e) {}
                if (nm) map[nm] = i;
            }
        } catch (e) {}
        virtFieldIdx[key] = map;
    }
    const idx = map[want];
    return idx === undefined ? -1 : idx;
}

// A virtual's field storage (its address), by name: the pointer array at
// v+24, else null (a field the virtual reaches dynamically).
function virtualSlot(v, name) {
    try {
        if (!v || v.isNull()) return null;
        const t = v.readPointer();
        if (t.readU32() !== HVIRTUAL) return null;
        const i = virtualFieldIndex(t, name);
        if (i < 0) return null;
        const st = v.add(24 + i * 8).readPointer();
        return (st && !st.isNull()) ? st : null;
    } catch (e) { return null; }
}

// An object field of a virtual (a string, another virtual), or null.
function virtualPtr(v, name) {
    const st = virtualSlot(v, name);
    if (st) {
        try { return st.readPointer(); } catch (e) { return null; }
    }
    return getField(v, name);
}

// An i64 field of a virtual, as a decimal string, or null. Reached
// dynamically, the value comes boxed (vdynamic: its value at +8).
function virtualI64(v, name) {
    try {
        const st = virtualSlot(v, name);
        if (st) return st.readS64().toString();
        const d = getField(v, name);
        return (d && !d.isNull()) ? d.add(8).readS64().toString() : null;
    } catch (e) { return null; }
}

// { item, count } out of one container slot, or null.
function slotItem(p) {
    try {
        if (!p || p.isNull()) return null;
        const t = p.readPointer();
        if (t.readU32() !== HVIRTUAL) return null;
        const ii = virtualFieldIndex(t, "item");
        if (ii < 0) return null;
        const st = p.add(24 + ii * 8).readPointer();
        if (!st || st.isNull()) return null;
        const it = st.readPointer();
        if (!it || it.isNull() || it.compare(ptr("0x10000")) <= 0) return null;
        let count = 1;
        const ci = virtualFieldIndex(t, "count");
        if (ci >= 0) {
            try {
                const cs = p.add(24 + ci * 8).readPointer();
                const c = (cs && !cs.isNull()) ? cs.readS32() : 1;
                if (c > 0 && c < 1000000) count = c;
            } catch (e) {}
        }
        return { item: it, count: count };
    } catch (e) { return null; }
}

// uid churns on every container move, so it is never an identity: items are
// counted by kind (+ rarity, a property of the copy that doesn't churn).
function itemInfo(it) {
    const cls = typeName(it);
    if (!cls || (cls.lastIndexOf("st.Item", 0) !== 0
                 && cls.lastIndexOf("st.item.", 0) !== 0)) return null;
    const out = { cls: cls, kind: null, rarity: null, level: null };
    try { out.kind = hlStr(it.add(OFF.Item.kind).readPointer()); } catch (e) {}
    // level is on st.item.Gear (the Weapon offset serves armour too); rarity
    // only on st.item.Weapon (past the object's end elsewhere). Other items'
    // rarity comes from the app's sheet data.
    const gear = cls === "st.item.Weapon" || cls === "st.item.Armor";
    if (gear && OFF.Weapon) {
        try { out.level = it.add(OFF.Weapon.level).readS32(); } catch (e) {}
    }
    if (cls === "st.item.Weapon" && OFF.Weapon) {
        try { out.rarity = hlStr(it.add(OFF.Weapon.rarity).readPointer()); } catch (e) {}
    }
    return out;
}

const invKey = function (inf) { return inf.kind + "|" + (inf.rarity || ""); };

let invSeen = null;             // kind|rarity -> count, inventory + equipment
let invReady = false;           // first sweep only baselines, never fires

// False if unreadable: mistaking a failed read for an empty bag would report
// the whole bag as loot on the next good sweep.
// `detail`, when given, also counts the gear piece by piece:
// "kind|rarity|infusion bonus stat" (a weapon's own rarity, an armour's its
// sheet's: ""; the bonus an infusable piece rolled as it dropped,
// ent.Hero.makeLootItem), for the goals.
function readContainerKinds(invPtr, into, byKey, detail) {
    if (!invPtr || invPtr.isNull()) return false;
    let arr;
    try { arr = invPtr.add(OFF.Inventory.content).readPointer(); }
    catch (e) { return false; }
    if (!arr || arr.isNull()) return false;
    const A = OFF.ArrayObj;
    let n, data;
    try {
        n = arr.add(A.length).readS32();
        data = arr.add(A.array).readPointer();
    } catch (e) { return false; }
    if (n < 0 || n > 4096 || data.isNull()) return false;
    for (let i = 0; i < n; i++) {
        let raw;
        try { raw = data.add(A.data + i * 8).readPointer(); } catch (e) { continue; }
        if (!raw || raw.isNull() || raw.compare(ptr("0x10000")) <= 0) continue;
        const slot = slotItem(raw);
        if (!slot) continue;
        const inf = itemInfo(slot.item);
        if (!inf || !inf.kind) continue;
        const key = invKey(inf);
        into[key] = (into[key] || 0) + slot.count;
        if (!(key in byKey)) byKey[key] = inf;
        if (detail && (inf.cls === "st.item.Armor" || inf.cls === "st.item.Weapon")) {
            let bonus = "";
            if (OFF.Gear && OFF.Gear.infusionBonusStat != null) {
                try { bonus = hlStr(slot.item.add(OFF.Gear.infusionBonusStat).readPointer()) || ""; }
                catch (e) {}
            }
            const dk = inf.kind + "|" + (inf.rarity || "") + "|" + bonus;
            detail[dk] = (detail[dk] || 0) + slot.count;
        }
    }
    return true;
}

// ---- the stock: what the hero owns, by item kind ----
// Bag + equipment + every bank tab + the currencies, for the goals. Sent on
// change; `banks` is
// the number of tabs read (-1: no offset); the bank may only be known once
// opened.
let stockSig = null;

function sendStock(loadout, now, info, gear) {
    try {
        const all = {}, binfo = {};
        gear = gear || {};
        for (const key in now) {
            const k = info[key] && info[key].kind;
            if (k) all[k] = (all[k] || 0) + now[key];
        }
        let banks = -1;
        if (OFF.Loadout.banks != null) {
            banks = 0;
            const tabs = proxyItems(loadout.add(OFF.Loadout.banks).readPointer(), 64);
            for (let i = 0; i < tabs.length; i++) {
                const nm = typeName(tabs[i]);
                if (!nm || nm.indexOf("st.Inventory") !== 0) continue;
                const got = {};
                if (!readContainerKinds(tabs[i], got, binfo, gear)) continue;
                banks++;
                for (const key in got) {
                    const k = binfo[key] && binfo[key].kind;
                    if (k) all[k] = (all[k] || 0) + got[key];
                }
            }
        }
        // the currencies: held apart from the bag, an amount for each kind
        // (st.Loadout.currencies, read by st.Loadout.addCurrency)
        if (OFF.Loadout.currencies != null && OFF.CurrencyProxy) {
            const cur = proxyItems(loadout.add(OFF.Loadout.currencies).readPointer(), 64);
            for (let i = 0; i < cur.length; i++) {
                try {
                    const k = hlStr(cur[i].add(OFF.CurrencyProxy.kind).readPointer());
                    const n = cur[i].add(OFF.CurrencyProxy.amount).readS32();
                    if (k && n > 0) all[k] = (all[k] || 0) + n;
                } catch (e) {}
            }
        }
        const sig = banks + "|" + Object.keys(all).sort()
            .map(function (k) { return k + ":" + all[k]; }).join(",")
            + "|" + Object.keys(gear).sort()
            .map(function (k) { return k + ":" + gear[k]; }).join(",");
        if (sig === stockSig) return;
        stockSig = sig;
        send({ kind: "stock", items: all, banks: banks, gear: gear });
    } catch (e) {}
}

// Counting by kind across bag + equipment makes (un)equipping a no-op.
function sweepInventory() {
    try {
        if (!localHero || localHero.isNull()
            || !OFF.Loadout || !OFF.Inventory || !OFF.Item || !OFF.ArrayObj)
            return;
        const loadout = localHero.add(OFF.Hero.loadout).readPointer();
        if (!loadout || loadout.isNull()) return;
        const now = {}, info = {}, gear = {};
        const okInv = readContainerKinds(
            loadout.add(OFF.Loadout.inventory).readPointer(), now, info, gear);
        const okEq = readContainerKinds(
            loadout.add(OFF.Loadout.equipment).readPointer(), now, info, gear);
        if (!okInv || !okEq) return;
        sendStock(loadout, now, info, gear);
        if (!invReady) {
            invSeen = now; invReady = true; return;
        }
        for (const key in now) {
            const gained = now[key] - (invSeen[key] || 0);
            if (gained <= 0) continue;
            const inf = info[key];
            if (!inf) continue;
            send({ kind: "pickup", item: inf.kind, rarity: inf.rarity,
                   level: inf.level, count: gained });
        }
        invSeen = now;
    } catch (e) {}
}

// ---- the collection (mounts, gliders, companions) ----
// AccountProgress.collection: hxbit proxy arrays of Strings (pets: unit kinds,
// measured 2026-08-07) or items (read by kind).
let collSig = null;

function collElem(p) {
    const t = typeName(p);
    if (t && (t.lastIndexOf("st.Item", 0) === 0
              || t.lastIndexOf("st.item.", 0) === 0))
        return hlStr(p.add(OFF.Item.kind).readPointer());
    return hlStr(p);
}

function readCollList(coll, off) {
    if (off == null) return null;
    const proxy = coll.add(off).readPointer();
    if (!proxy || proxy.isNull()) return [];
    const dyn = proxy.add(OFF.ArrayProxyData.array).readPointer();
    if (!dyn || dyn.isNull()) return [];
    const arr = dyn.add(OFF.ArrayDyn.array).readPointer();
    if (!arr || arr.isNull()) return [];
    const n = arr.add(OFF.ArrayObj.length).readS32();
    if (n < 0 || n > 4096) return null;
    const data = arr.add(OFF.ArrayObj.array).readPointer();
    const out = [];
    for (let i = 0; i < n; i++) {
        const p = data.add(OFF.ArrayObj.data + i * 8).readPointer();
        if (!p || p.isNull()) continue;
        const s = collElem(p);
        if (s) out.push(s);
    }
    return out;
}

// The hero's jobs (HeroSpecialization.jobs: a replicated array of job
// proxies), each with its level and the crafts its recipes taught
// (learnedCrafts: the made items' ids). {job: {lvl, learnt: [...]}}, or null.
function readJobs() {
    if (!OFF.JobProxy || OFF.JobProxy.learnedCrafts == null || !OFF.HeroDetail
        || !OFF.Specialization || OFF.Specialization.jobs == null) return null;
    const sp = localHero.add(OFF.HeroDetail.specialization).readPointer();
    if (!sp || sp.isNull()) return null;
    const proxy = sp.add(OFF.Specialization.jobs).readPointer();
    if (!proxy || proxy.isNull()) return {};
    const dyn = proxy.add(OFF.ArrayProxyData.array).readPointer();
    if (!dyn || dyn.isNull()) return {};
    const arr = dyn.add(OFF.ArrayDyn.array).readPointer();
    if (!arr || arr.isNull()) return {};
    const n = arr.add(OFF.ArrayObj.length).readS32();
    if (n < 0 || n > 64) return null;
    const data = arr.add(OFF.ArrayObj.array).readPointer();
    const out = {};
    for (let i = 0; i < n; i++) {
        const j = data.add(OFF.ArrayObj.data + i * 8).readPointer();
        if (!j || j.isNull()) continue;
        const id = hlStr(j.add(OFF.JobProxy.job).readPointer());
        if (!id) continue;
        out[id] = { lvl: j.add(OFF.JobProxy.level).readS32(),
                    learnt: readCollList(j, OFF.JobProxy.learnedCrafts) || [] };
    }
    return out;
}

function checkCollection() {
    try {
        if (!localHero || localHero.isNull() || !OFF.Collection
            || !OFF.AccountProgress || OFF.Player.accountProgress == null)
            return;
        const player = localHero.add(OFF.Hero.player).readPointer();
        if (!player || player.isNull()) return;
        const acct = player.add(OFF.Player.accountProgress).readPointer();
        if (!acct || acct.isNull()) return;
        const coll = acct.add(OFF.AccountProgress.collection).readPointer();
        if (!coll || coll.isNull()) return;
        const C = OFF.Collection;
        const msg = { kind: "collection",
                      mounts: readCollList(coll, C.mounts),
                      gliders: readCollList(coll, C.gliders),
                      pets: readCollList(coll, C.pets),
                      // the armour appearances (null on older offsets)
                      gears: readCollList(coll, C.gears) };
        // the hero's jobs and the recipes they learnt (per character)
        try { msg.jobs = readJobs(); } catch (e) { msg.jobs = null; }
        if (msg.mounts === null || msg.gliders === null || msg.pets === null)
            return;
        const sig = JSON.stringify(msg);
        if (sig === collSig) return;
        collSig = sig;
        send(msg);
    } catch (e) {}
}

// ---- the codex: the game's own kill count per monster ----
// Measured 2026-08-05; the per-character store is replicated:
//   Hero.player -> Player.progress -> Progress.unitsProgress (hxbit.MapData)
//     -> MapData.map (a virtual; hl_vvirtual.value @8 is the real StringMap)
//     -> StringMap.h -> $std.hbkeys / $std.hbget(h, utf16(unitKind))
//     -> { killCount, rank }
//
// killCount is a lifetime total per monster kind. hbkeys/hbget allocate, so
// game thread only, on a slow clock.
let hbGet = null, hbKeys = null;
let codexDue = true;
let codexSig = null;

function setupCodexApi(base) {
    const N = DATA.map_natives || {};
    try {
        if (N.hbget != null)
            hbGet = new NativeFunction(base.add(N.hbget * 8).readPointer(),
                                       "pointer", ["pointer", "pointer"]);
        if (N.hbkeys != null)
            hbKeys = new NativeFunction(base.add(N.hbkeys * 8).readPointer(),
                                        "pointer", ["pointer"]);
        return hbGet !== null && hbKeys !== null;
    } catch (e) { return false; }
}

function unitsProgressMap() {
    try {
        if (!localHero || localHero.isNull() || !OFF.Progress
            || OFF.Player.progress == null) return null;
        const player = localHero.add(OFF.Hero.player).readPointer();
        if (!player || player.isNull()) return null;
        const prog = player.add(OFF.Player.progress).readPointer();
        if (!prog || prog.isNull()) return null;
        const md = prog.add(OFF.Progress.unitsProgress).readPointer();
        if (!md || md.isNull()) return null;
        const v = md.add(OFF.MapData.map).readPointer();
        if (!v || v.isNull()) return null;
        const inner = v.readPointer().readU32() === 15
            ? v.add(OFF.MapData.value).readPointer() : v;
        if (!inner || inner.isNull()) return null;
        const h = inner.add(OFF.StringMap.h).readPointer();
        return (h && !h.isNull()) ? h : null;
    } catch (e) { return null; }
}

// The item codex: Progress.itemProgress, item id -> {itemCount, rank}, same
// route as the monsters'.
let itemCodexSig = null;

// Achievements: the character's (Progress.achievements, id -> true), the
// account's (id -> completion time in ms) and the counters objectives use
// (measured 2026-10-01). GAME THREAD ONLY.
let achSig = null;

function boxedMap(md, read) {
    const out = {};
    mapEntries(mapHandle(md), 2000).forEach(function (kv) {
        const v = kv[1];
        if (v && !v.isNull()) out[kv[0]] = read(v);
    });
    return out;
}

function refreshAchievements() {
    try {
        if (!localHero || localHero.isNull() || !OFF.Progress
            || OFF.Progress.achievements == null || !hbKeys || !hbGet) return;
        const player = localHero.add(OFF.Hero.player).readPointer();
        if (!player || player.isNull()) return;
        const prog = player.add(OFF.Player.progress).readPointer();
        const acct = player.add(OFF.Player.accountProgress).readPointer();
        if (!prog || prog.isNull()) return;
        const mine = Object.keys(boxedMap(
            prog.add(OFF.Progress.achievements).readPointer(),
            function (v) { return v.add(8).readU8(); }));
        const account = (acct && !acct.isNull()
                         && OFF.AccountProgress.achievements != null)
            ? boxedMap(acct.add(OFF.AccountProgress.achievements).readPointer(),
                       function (v) { return v.add(8).readDouble(); })
            : {};
        const counters = countersOf(localHero);
        // the Soulwell's luck statuses (offerings), timed by the server clock
        const luck = statusesOf(localHero, ["Luck_"]);
        let now = null;
        try { now = serverNowOf(localHero.add(OFF.Hero.layer).readPointer()); } catch (e) {}
        const sig = localName + JSON.stringify([mine, account, counters,
            luck.map(function (l) { return [l[0], l[1]]; })]);
        if (sig === achSig) return;
        achSig = sig;
        send({ kind: "achievements", hero: localName, done: mine,
               account: account,
               counters: typeof counters === "object" ? counters : null,
               luck: luck, now: now });
    } catch (e) {}
}

// GAME THREAD ONLY.
function refreshItemCodex() {
    if (!OFF.ItemProxy || OFF.Progress.itemProgress == null) return;
    const h = progressMap("itemProgress");
    if (!h || !hbKeys || !hbGet) return;
    const out = {};
    const keys = hbKeys(h);
    if (!keys || keys.isNull()) return;
    const n = keys.add(16).readS32();
    for (let i = 0; i < n && i < 5000; i++) {
        const kb = keys.add(24 + i * 8).readPointer();
        if (!kb || kb.isNull()) continue;
        const id = kb.readUtf16String();
        const v = hbGet(h, kb);
        if (!id || !v || v.isNull()) continue;
        const t = typeName(v);
        if (t === "hxbit.ObjProxy_OitemCount_Int_rank_Int")
            out[id] = [v.add(OFF.ItemProxy.itemCount).readS32(),
                       v.add(OFF.ItemProxy.rank).readS32()];
    }
    const sig = localName + JSON.stringify(out);
    if (sig === itemCodexSig) return;
    itemCodexSig = sig;
    send({ kind: "itemcodex", hero: localName, items: out });
}

// GAME THREAD ONLY.
function refreshCodex() {
    const h = unitsProgressMap();
    if (!h || !hbKeys || !hbGet) return;
    try {
        const keys = hbKeys(h);
        if (!keys || keys.isNull()) return;
        // hl_varray: { t@0, at@8, size@16, pad@20 }, elements from +24.
        const n = keys.add(16).readS32();
        if (n < 0 || n > 20000) return;
        const out = {};
        for (let i = 0; i < n; i++) {
            const kb = keys.add(24 + i * 8).readPointer();
            if (!kb || kb.isNull()) continue;
            const id = kb.readUtf16String();
            if (!id) continue;
            const v = hbGet(h, kb);
            if (!v || v.isNull()) continue;
            out[id] = [v.add(OFF.CodexProxy.count).readS32(),
                       v.add(OFF.CodexProxy.rank).readS32()];
        }
        const sig = localName + JSON.stringify(out);
        if (sig !== codexSig) {
            codexSig = sig;
            send({ kind: "codex", hero: localName, ranks: out });
        }
        refreshItemCodex();
        refreshAchievements();
    } catch (e) {}
}

// ---- the world's elements (map completion) ----
// Progress.elements: element id (chest, orb, obelisk) -> state, per character,
// same route as the codex. Measured 2026-09-28: values are
// ObjProxy_Ocompleted_Float (`completed` = completion time); an element never
// completed has no entry.
let elementsSig = null;

function progressMap(field) {
    try {
        if (!localHero || localHero.isNull() || !OFF.Progress
            || OFF.Progress[field] == null || OFF.Player.progress == null)
            return null;
        const player = localHero.add(OFF.Hero.player).readPointer();
        if (!player || player.isNull()) return null;
        const prog = player.add(OFF.Player.progress).readPointer();
        if (!prog || prog.isNull()) return null;
        const md = prog.add(OFF.Progress[field]).readPointer();
        if (!md || md.isNull()) return null;
        const v = md.add(OFF.MapData.map).readPointer();
        if (!v || v.isNull()) return null;
        const inner = v.readPointer().readU32() === 15
            ? v.add(OFF.MapData.value).readPointer() : v;
        if (!inner || inner.isNull()) return null;
        const h = inner.add(OFF.StringMap.h).readPointer();
        return (h && !h.isNull()) ? h : null;
    } catch (e) { return null; }
}

// GAME THREAD ONLY (hbkeys/hbget allocate).
function refreshElements() {
    const h = progressMap("elements");
    if (!h || !hbKeys || !hbGet) return;
    try {
        const keys = hbKeys(h);
        if (!keys || keys.isNull()) return;
        const n = keys.add(16).readS32();
        if (n < 0 || n > 50000) return;
        const out = {};
        const at = OFF.ElementProxy ? OFF.ElementProxy.completed : 24;
        for (let i = 0; i < n; i++) {
            const kb = keys.add(24 + i * 8).readPointer();
            if (!kb || kb.isNull()) continue;
            const id = kb.readUtf16String();
            if (!id) continue;
            const v = hbGet(h, kb);
            if (!v || v.isNull()) { out[id] = null; continue; }
            let val = null;
            try { val = v.add(at).readDouble(); } catch (e) {}
            out[id] = val;
        }
        const sig = localName + JSON.stringify(out);
        if (sig === elementsSig) return;
        elementsSig = sig;
        send({ kind: "elements", hero: localName, states: out });
    } catch (e) {}
}

// ---- the players around, and their profiles (the Character tab) ----
// Measured 2026-09-28: for every hero on the layer the client holds class,
// level, equipment, talents, slotted skills and masteries, but NOT attribute
// values (they read 0; the game computes them on demand).
// Roster every ~10 s; a full profile on request (recv "analyze"). Both on the
// game thread (the talent map read allocates).
let rosterDue = false;
let analyzeWanted = null;

function mapHandle(md) {
    // hxbit.MapData -> the StringMap behind it (null for any other type)
    if (!md || md.isNull()) return null;
    const v = md.add(OFF.MapData.map).readPointer();
    if (!v || v.isNull()) return null;
    const inner = v.readPointer().readU32() === 15
        ? v.add(OFF.MapData.value).readPointer() : v;
    if (!inner || inner.isNull() || typeName(inner) !== "haxe.ds.StringMap")
        return null;
    return inner.add(OFF.StringMap.h).readPointer();
}

// A StringMap's entries: [[key, value pointer], ...].
function mapEntries(h, max) {
    const out = [];
    if (!h || h.isNull() || !hbKeys || !hbGet) return out;
    const keys = hbKeys(h);
    if (!keys || keys.isNull()) return out;
    const n = keys.add(16).readS32();
    for (let i = 0; i < n && i < max; i++) {
        const kb = keys.add(24 + i * 8).readPointer();
        if (!kb || kb.isNull()) continue;
        out.push([kb.readUtf16String(), hbGet(h, kb)]);
    }
    return out;
}

// An ArrayObj of skills (or nulls), as their kinds.
function skillKinds(arr, max) {
    const out = [];
    if (!arr || arr.isNull()) return out;
    const n = arr.add(OFF.ArrayObj.length).readS32();
    const data = arr.add(OFF.ArrayObj.array).readPointer();
    for (let i = 0; i < n && i < max; i++) {
        const s = data.add(OFF.ArrayObj.data + i * 8).readPointer();
        out.push((s && !s.isNull()) ? hlStr(s.add(OFF.Skill.kind).readPointer()) : null);
    }
    return out;
}

// An hxbit ArrayProxyData's elements as ids: a String's text, an item's
// kind, a skill's kind — or, for anything else, its class name in brackets.
function proxyThings(proxy, max) {
    const out = [];
    if (!proxy || proxy.isNull()) return out;
    const dyn = proxy.add(OFF.ArrayProxyData.array).readPointer();
    if (!dyn || dyn.isNull()) return out;
    const arr = dyn.add(OFF.ArrayDyn.array).readPointer();
    if (!arr || arr.isNull()) return out;
    const n = arr.add(OFF.ArrayObj.length).readS32();
    const data = arr.add(OFF.ArrayObj.array).readPointer();
    for (let i = 0; i < n && i < max; i++) {
        const q = data.add(OFF.ArrayObj.data + i * 8).readPointer();
        if (!q || q.isNull()) { out.push(null); continue; }
        const t = typeName(q);
        if (t === "String") out.push(hlStr(q));
        else if (t && (t.lastIndexOf("st.Item", 0) === 0 || t.lastIndexOf("st.item.", 0) === 0))
            out.push(hlStr(q.add(OFF.Item.kind).readPointer()));
        else if (t === "st.skill.Skill" || t === "st.skill.Status")
            out.push(hlStr(q.add(OFF.Skill.kind).readPointer()));
        else out.push("[" + t + "]");
    }
    return out;
}

function proxyStrings(proxy, max) {
    const out = [];
    if (!proxy || proxy.isNull()) return out;
    const dyn = proxy.add(OFF.ArrayProxyData.array).readPointer();
    if (!dyn || dyn.isNull()) return out;
    const arr = dyn.add(OFF.ArrayDyn.array).readPointer();
    if (!arr || arr.isNull()) return out;
    const n = arr.add(OFF.ArrayObj.length).readS32();
    const data = arr.add(OFF.ArrayObj.array).readPointer();
    for (let i = 0; i < n && i < max; i++) {
        const q = data.add(OFF.ArrayObj.data + i * 8).readPointer();
        if (q && !q.isNull() && typeName(q) === "String") out.push(hlStr(q));
    }
    return out;
}

// The equipment container, slot by slot (a null for an empty slot).
function equipSlots(loadout) {
    const out = [];
    const inv = loadout.add(OFF.Loadout.equipment).readPointer();
    if (!inv || inv.isNull()) return out;
    const arr = inv.add(OFF.Inventory.content).readPointer();
    if (!arr || arr.isNull()) return out;
    const n = arr.add(OFF.ArrayObj.length).readS32();
    const data = arr.add(OFF.ArrayObj.array).readPointer();
    for (let i = 0; i < n && i < 64; i++) {
        const raw = data.add(OFF.ArrayObj.data + i * 8).readPointer();
        const slot = (raw && !raw.isNull()) ? slotItem(raw) : null;
        const inf = slot ? itemInfo(slot.item) : null;
        if (!inf) { out.push(null); continue; }
        // [kind, rarity, level, upgradeLevel, slots, effects, infusion,
        //  infusion bonus stat, item flags (st.ItemFlag bits)]
        const row = [inf.kind, inf.rarity, inf.level, null, [], [], null, null, 0];
        if (OFF.Item.flags != null && OFF.EnumFlagsData) {
            try {
                const fl = slot.item.add(OFF.Item.flags).readPointer();
                if (fl && !fl.isNull()) row[8] = fl.add(OFF.EnumFlagsData.value).readS32();
            } catch (e) {}
        }
        const G = OFF.Gear;
        if (G && inf.cls !== "st.Item") {
            const it = slot.item;
            try { row[2] = it.add(G.level).readS32(); } catch (e) {}
            try { row[3] = it.add(G.upgradeLevel).readS32(); } catch (e) {}
            try { row[4] = proxyThings(it.add(G.slots).readPointer(), 8); } catch (e) {}
            if (inf.cls === "st.item.Weapon") {
                try { row[5] = proxyThings(it.add(G.effects).readPointer(), 8); } catch (e) {}
            }
            if (G.infusion != null) {
                try { row[6] = hlStr(it.add(G.infusion).readPointer()); } catch (e) {}
                try { row[7] = hlStr(it.add(G.infusionBonusStat).readPointer()); } catch (e) {}
            }
        }
        out.push(row);
    }
    return out;
}

// A hero's HeroStats (Progress.stats): {total: time played in seconds at
// the last save, first: its first connection, conn: this one} (f64s), or
// null. Only the local hero's progress is replicated. GAME THREAD ONLY.
function statsOf(h) {
    try {
        if (!OFF.HeroStats || OFF.Progress.stats == null) return null;
        const player = h.add(OFF.Hero.player).readPointer();
        if (!player || player.isNull()) return null;
        const prog = player.add(OFF.Player.progress).readPointer();
        if (!prog || prog.isNull()) return null;
        const st = prog.add(OFF.Progress.stats).readPointer();
        if (!st || st.isNull()) return null;
        return { total: st.add(OFF.HeroStats.totalPlayed).readDouble(),
                 first: st.add(OFF.HeroStats.firstConnectionTime).readDouble(),
                 conn: st.add(OFF.HeroStats.connectionTime).readDouble() };
    } catch (e) { return null; }
}

// A hero's Progress.counters (StringMap: luck counters, rift and gold
// totals...) as numbers, or why it couldn't be read. Only the local hero's is
// replicated (measured 2026-10-01). GAME THREAD ONLY.
function countersOf(h) {
    try {
        if (OFF.Progress.counters == null) return "no offset";
        const player = h.add(OFF.Hero.player).readPointer();
        if (!player || player.isNull()) return "no player";
        const prog = player.add(OFF.Player.progress).readPointer();
        if (!prog || prog.isNull()) return "no progress";
        const m = prog.add(OFF.Progress.counters).readPointer();
        if (!m || m.isNull()) return "no counters map";
        const out = {};
        mapEntries(m.add(OFF.StringMap.h).readPointer(), 200).forEach(function (kv) {
            const v = kv[1];
            if (!v || v.isNull()) { out[kv[0]] = null; return; }
            const kind = v.readPointer().readU32();
            out[kv[0]] = kind === 3 ? v.add(8).readS32()
                : kind === 6 ? v.add(8).readDouble()
                : kind === 5 ? v.add(8).readFloat()
                : "<" + kind + " " + (typeName(v) || "?") + ">";
        });
        return out;
    } catch (e) { return "error " + e; }
}

// A unit's statuses whose kind starts with one of `prefixes`:
// [[kind, startTime, duration, stopTime], ...] (the game's clock).
function statusesOf(u, prefixes) {
    const out = [];
    try {
        const proxy = u.add(OFF.Unit.statuses).readPointer();
        if (!proxy || proxy.isNull()) return out;
        const dyn = proxy.add(OFF.ArrayProxyData.array).readPointer();
        const arr = dyn.add(OFF.ArrayDyn.array).readPointer();
        const n = arr.add(OFF.ArrayObj.length).readS32();
        const data = arr.add(OFF.ArrayObj.array).readPointer();
        const S = OFF.Status;
        for (let i = 0; i < n && i < 200; i++) {
            const p = data.add(OFF.ArrayObj.data + i * 8).readPointer();
            if (!p || p.isNull() || p.add(S.removed).readU8()) continue;
            const k = hlStr(p.add(S.kind).readPointer());
            if (!k || !prefixes.some(function (x) { return k.indexOf(x) === 0; }))
                continue;
            out.push([k, p.add(S.startTime).readDouble(),
                      p.add(S.duration).readDouble(),
                      S.stopTime != null ? p.add(S.stopTime).readDouble() : null]);
        }
    } catch (e) {}
    return out;
}

function profileOf(h) {
    const H = OFF.Hero, D = OFF.HeroDetail, S = OFF.Specialization;
    const r = {};
    // every status on the hero: the sheet applies their attribute effects
    r.statuses = statusesOf(h, [""]).map(function (s) { return s[0]; });
    try { r.k = hlStr(h.add(H.kind).readPointer()); } catch (e) {}
    try { r.lvl = h.add(H.level).readS32(); } catch (e) {}
    try {
        const lo = h.add(H.loadout).readPointer();
        r.equip = (lo && !lo.isNull()) ? equipSlots(lo) : [];
        // what it wears over that gear, and its looks: its portrait
        r.look = (lo && !lo.isNull()) ? appearanceSlots(lo) : [];
    } catch (e) { r.equip = []; }
    r.skin = skinOf(h);
    try {
        const sp = h.add(D.specialization).readPointer();
        if (sp && !sp.isNull()) {
            // talent -> rank points (value: hxbit.ObjProxy_Orank_Int)
            r.talents = {};
            mapEntries(mapHandle(sp.add(S.talents).readPointer()), 80)
                .forEach(function (kv) {
                    let rank = 1;
                    try {
                        const v = kv[1];
                        if (v && !v.isNull()) {
                            const kind = v.readPointer().readU32();
                            const tn = kind === 11 ? typeName(v) : null;
                            if (tn === "hxbit.ObjProxy_Orank_Int")
                                rank = v.add(OFF.RankProxy.rank).readS32();
                            else if (kind === 3)       // a boxed Int
                                rank = v.add(8).readS32();
                        }
                    } catch (e) {}
                    r.talents[kv[0]] = rank;
                });
            r.slots = proxyStrings(sp.add(S.skillSlots).readPointer(), 20);
            r.masteries = proxyStrings(sp.add(S.skillMasteries).readPointer(), 60);
            // weapon -> its chosen skills (value: ObjProxy_Oskills_...)
            r.arsenals = {};
            if (S.arsenals != null) {
                mapEntries(mapHandle(sp.add(S.arsenals).readPointer()), 20)
                    .forEach(function (kv) {
                        let list = [];
                        try {
                            if (kv[1] && !kv[1].isNull())
                                list = proxyStrings(
                                    kv[1].add(OFF.SkillsProxy.skills).readPointer(), 10);
                        } catch (e) {}
                        r.arsenals[kv[0]] = list;
                    });
            }
            if (S.prayerSequence != null)
                r.prayers = proxyStrings(sp.add(S.prayerSequence).readPointer(), 10);
            // the mage's conduits, slot by slot
            if (S.conduits != null)
                r.conduits = proxyStrings(sp.add(S.conduits).readPointer(), 10);
        }
    } catch (e) {}
    // the action bar as the game holds it
    try {
        if (D.weaponSkills != null) r.weaponSkills = skillKinds(h.add(D.weaponSkills).readPointer(), 20);
        r.skills = skillKinds(h.add(D.skills).readPointer(), 80);
    } catch (e) {}
    return r;
}

// The players on the layer: [{n, k, lvl, me, h}] (h: the hero pointer).
function layerPlayers() {
    const out = [];
    const P = OFF.Player, G = OFF.GameLayer;
    const layer = localHero.add(OFF.Hero.layer).readPointer();
    const proxy = layer.add(G.players).readPointer();
    const dyn = proxy.add(OFF.ArrayProxyData.array).readPointer();
    const arr = dyn.add(OFF.ArrayDyn.array).readPointer();
    const n = arr.add(OFF.ArrayObj.length).readS32();
    const data = arr.add(OFF.ArrayObj.array).readPointer();
    for (let i = 0; i < n && i < SHARD_MAX; i++) {
        try {
            const p = data.add(24 + i * 8).readPointer();
            if (!p || p.isNull()) continue;
            const nm = hlStr(p.add(P.name).readPointer());
            const h = p.add(P.hero).readPointer();
            if (!nm || !h || h.isNull()) continue;
            out.push({ n: nm, h: h, me: h.equals(localHero),
                       k: hlStr(h.add(OFF.Hero.kind).readPointer()),
                       lvl: h.add(OFF.Hero.level).readS32() });
        } catch (e) {}
    }
    return out;
}

// The local hero's luck and statistics, read every 5 s: an offering made
// shows at once (a few memory reads).
let selfDue = true;

// GAME THREAD ONLY.
function characterTick() {
    if (!localHero || localHero.isNull() || !OFF.HeroDetail) return;
    if (selfDue) {
        selfDue = false;
        try {
            const me = { counters: countersOf(localHero),
                         luckStatuses: statusesOf(localHero, ["Luck_"]),
                         stats: statsOf(localHero) };
            // the heroic dungeon with a bonus, the hero's own
            // (HeroSpecialization.bonusHeroicDungeon: {activity, ...})
            if (OFF.HeroDetail && OFF.Specialization
                && OFF.Specialization.bonusHeroicDungeon != null && OFF.BonusProxy) {
                try {
                    const sp = localHero.add(OFF.HeroDetail.specialization).readPointer();
                    const b = (sp && !sp.isNull())
                        ? sp.add(OFF.Specialization.bonusHeroicDungeon).readPointer() : null;
                    me.bonus = (b && !b.isNull())
                        ? hlStr(b.add(OFF.BonusProxy.activity).readPointer()) : null;
                } catch (e) {}
            }
            try { me.now = serverNowOf(localHero.add(OFF.Hero.layer).readPointer()); } catch (e) {}
            send({ kind: "selfprofile", profile: me });
        } catch (e) { log("self profile failed: " + e); }
    }
    try {
        const players = layerPlayers();
        if (rosterDue) {
            rosterDue = false;
            send({ kind: "roster", players: players.map(function (p) {
                return { n: p.n, k: p.k, lvl: p.lvl, me: p.me };
            }), party: Object.keys(partyNames) });
        }
        if (analyzeWanted !== null) {
            const want = analyzeWanted;
            analyzeWanted = null;
            const p = players.find(function (x) { return x.n === want; });
            if (!p) send({ kind: "profile", n: want, missing: true });
            else {
                const prof = profileOf(p.h);
                prof.n = p.n;
                prof.me = p.me;
                send({ kind: "profile", profile: prof });
            }
        }
    } catch (e) { log("character tick failed: " + e); }
}

function listenAnalyze() {
    recv("analyze", function (msg) {
        analyzeWanted = msg.name;
        listenAnalyze();                // recv is one-shot: listen again
    });
}

function resetBossBars() {
    // A loading screen tears the HUD down. No up/down events: the pull isn't
    // ending, and a phantom kill would be worse than silence.
    if (Object.keys(bossBars).length) send({ kind: "bossbar", n: 0, boss: false,
                                             elite: false, up: [], down: [] });
    bossBars = {};
    bossLast = "";
}

// ---- skill display-name resolution (CDB, via libhl dynamic field access) ----
// baseSkill.inf is a vvirtual over the CDB skill row; its `texts.name` is the
// localized display name (Warrior_Rage_Strike -> "Rage Strike"), cached per id.
let hl_getField = null, hl_hashUtf8 = null;
const fieldHash = {};     // field name -> interned HL hash
const nameCache = {};     // skill id -> display name ("" if none)

function setupNameApi() {
    try {
        const m = Process.findModuleByName("libhl.dll");
        hl_getField = new NativeFunction(m.findExportByName("hl_obj_get_field"),
                                         "pointer", ["pointer", "int"]);
        hl_hashUtf8 = new NativeFunction(m.findExportByName("hl_hash_utf8"),
                                         "int", ["pointer"]);
        for (const n of ["texts", "name", "type", "id"]) fieldHash[n] = hl_hashUtf8(Memory.allocUtf8String(n));
        return true;
    } catch (e) { return false; }
}

function getField(obj, name) {
    try {
        if (!obj || obj.isNull()) return null;
        if (!(name in fieldHash)) fieldHash[name] = hl_hashUtf8(Memory.allocUtf8String(name));
        return hl_getField(obj, fieldHash[name]);
    } catch (e) { return null; }
}

// A hero's looks, as the game dresses it (ent.Unit.skinData, a virtual of
// strings: the body parts and their colours), or null.
const SKIN_FIELDS = ["skinColor", "hairColor", "hairColorSecondary", "eyeColor",
                     "eyes", "hair", "facialHair", "eyebrows", "face", "beard"];
function skinOf(h) {
    if (OFF.Unit.skinData == null || !hl_getField) return null;
    try {
        const sk = h.add(OFF.Unit.skinData).readPointer();
        if (!sk || sk.isNull()) return null;
        const out = {};
        SKIN_FIELDS.forEach(function (f) {
            const v = getField(sk, f);
            const s = (v && !v.isNull()) ? hlStr(v) : null;
            if (s) out[f] = s;
        });
        return out;
    } catch (e) { return null; }
}

// The looks worn over the gear (Loadout.appearance), slot by slot as the
// equipment's: an item kind, or null where the gear itself shows.
function appearanceSlots(lo) {
    const out = [];
    if (OFF.Loadout.appearance == null) return out;
    try {
        const inv = lo.add(OFF.Loadout.appearance).readPointer();
        if (!inv || inv.isNull()) return out;
        const arr = inv.add(OFF.Inventory.content).readPointer();
        if (!arr || arr.isNull()) return out;
        const n = arr.add(OFF.ArrayObj.length).readS32();
        const data = arr.add(OFF.ArrayObj.array).readPointer();
        for (let i = 0; i < n && i < 64; i++) {
            const raw = data.add(OFF.ArrayObj.data + i * 8).readPointer();
            const slot = (raw && !raw.isNull()) ? slotItem(raw) : null;
            const inf = slot ? itemInfo(slot.item) : null;
            out.push(inf ? inf.kind : null);
        }
    } catch (e) {}
    return out;
}

function skillDisplayName(baseSkill, id) {
    if (id in nameCache) return nameCache[id];
    let nm = "";
    if (hl_getField) {
        try {
            const inf = baseSkill.add(OFF.BaseSkill.inf).readPointer();
            const texts = getField(inf, "texts");
            nm = hlStr(getField(texts, "name")) || "";
        } catch (e) {}
    }
    nameCache[id] = nm;
    return nm;
}

// The local player's group -> {name: 1} (groupId reads 0; group.players is
// reliable):
//   Player.group -> st.Group
//   Group.players -> hxbit.ArrayProxyData (.array @40 -> hl.types.ArrayDyn)
//   ArrayDyn.array(@8) -> ArrayObj; .length(@8), native varray(@16)
//   varray elements start at +24 (hl_varray header), each an st.Player*
function readParty(hero) {
    const names = {};
    try {
        const player = hero.add(OFF.Hero.player).readPointer();
        if (!player || player.isNull()) return names;
        const group = player.add(OFF.Player.group).readPointer();
        if (!group || group.isNull()) return names;
        const proxy = group.add(OFF.Group.players).readPointer();
        const arrDyn = proxy.add(40).readPointer();
        const arrObj = arrDyn.add(8).readPointer();
        const length = arrObj.add(8).readS32();
        const varr = arrObj.add(16).readPointer();
        if (length < 0 || length > 64) return names;
        for (let i = 0; i < length; i++) {
            const p = varr.add(24 + i * 8).readPointer();
            const nm = hlStr(p.add(OFF.Player.name).readPointer());
            if (nm) names[nm] = 1;
        }
    } catch (e) {}
    return names;
}

// ---- the shard roster (every player's class) ----
// st.GameLayer.players is every player the client holds, not just those nearby.
// The class is on ent.Hero (st.player.HeroData is null client-side).
// Plain reads, timer-safe.
const SHARD_MAX = 256;          // a sane ceiling on a corrupt length read
let shardSig = "";              // last payload signature, to skip idle resends

function readShard(hero) {
    const out = [];
    const P = OFF.Player, H = OFF.Hero, G = OFF.GameLayer;
    if (!P || !H || !G || P.hero == null || G.players == null) return out;
    const layer = hero.add(H.layer).readPointer();
    if (!layer || layer.isNull()) return out;
    const proxy = layer.add(G.players).readPointer();
    if (!proxy || proxy.isNull()) return out;
    const arrDyn = proxy.add(OFF.ArrayProxyData.array).readPointer();
    if (!arrDyn || arrDyn.isNull()) return out;
    const arrObj = arrDyn.add(OFF.ArrayDyn.array).readPointer();
    if (!arrObj || arrObj.isNull()) return out;
    const length = arrObj.add(OFF.ArrayObj.length).readS32();
    const varr = arrObj.add(OFF.ArrayObj.array).readPointer();
    if (length < 0 || length > SHARD_MAX || !varr || varr.isNull()) return out;
    for (let i = 0; i < length; i++) {
        try {
            const p = varr.add(24 + i * 8).readPointer();
            if (!p || p.isNull()) continue;
            const nm = hlStr(p.add(P.name).readPointer());
            if (!nm) continue;                  // a slot mid-population
            const row = { n: nm };
            // no hero entity yet: the row ships without a class
            const h = p.add(P.hero).readPointer();
            if (h && !h.isNull() && H.kind != null)
                row.k = hlStr(h.add(H.kind).readPointer());
            out.push(row);
        } catch (e) {}
    }
    return out;
}

function sweepShard() {
    try {
        if (!localHero || localHero.isNull()) return;
        const list = readShard(localHero);
        if (!list.length) return;
        // sent on change only
        const sig = list.map(function (r) {
            return r.n + "|" + (r.k || "");
        }).sort().join(";");
        if (sig === shardSig) return;
        shardSig = sig;
        send({ kind: "shard", list: list });
    } catch (e) {}
}

// Set by a timer, consumed by the camera hook: getHero is an HL call and must
// never run from a timer (see the game-thread tick).
let heroRefreshDue = false;

// The local hero's lasting identity: its id in the game's database
// (HeroData.databaseID; two characters may share a name) and the game
// account it belongs to (HeroData.accountID). {} where unread.
let identityLogged = false;
function heroIdentity(h) {
    const out = {};
    if (!OFF.HeroData || OFF.Player.heroData == null) return out;
    try {
        const pl = h.add(OFF.Hero.player).readPointer();
        if (!pl || pl.isNull()) return out;
        const hd = pl.add(OFF.Player.heroData).readPointer();
        if (!hd || hd.isNull()) return out;
        const id = hd.add(OFF.HeroData.databaseID).readS64().toString();
        if (id !== "0") out.id = id;
        const ac = hlStr(hd.add(OFF.HeroData.accountID).readPointer());
        if (ac) out.acct = ac;
    } catch (e) {}
    if (!identityLogged) {
        identityLogged = true;
        log("hero identity: database id " + (out.id ? "read" : "not sent")
            + ", account " + (out.acct ? "read" : "not sent"));
    }
    return out;
}

function refreshLocalHero() {
    for (const f of getHeroFns) {
        try {
            const h = new NativeFunction(f.addr, "pointer", [])();
            if (h && !h.isNull() && typeName(h) === "ent.Hero") {
                // another hero is another bag: rebaseline, not loot
                if (!localHero || !localHero.equals(h)) invReady = false;
                localHero = h;
                partyNames = readParty(h);
                const nm = hlStr(h.add(OFF.Hero.name).readPointer());
                if (nm) partyNames[nm] = 1;   // always include self
                localName = nm;
                const ident = heroIdentity(h);
                // HeroData is null client-side: the id seen on the select
                // screen, for the character of that name
                if (!ident.id && pickedHero && pickedHero.name === nm) {
                    ident.id = pickedHero.id;
                }
                send({ kind: "hero", name: localName, id: ident.id || null,
                       acct: ident.acct || null });
                return;
            }
        } catch (e) {}
    }
}

function main() {
    const resolved = resolveAnchors();
    const t0 = Date.now();
    base = findTableFast(resolved);
    if (base) {
        log("functions_ptrs via statics walk (" + (Date.now() - t0) + " ms)");
    } else {
        log("statics walk missed; falling back to memory scan ...");
        base = findTableBase(resolved);
        if (base) log("functions_ptrs via memory scan (" + (Date.now() - t0) + " ms)");
    }
    if (!base) { log("!! HL functions_ptrs table not found"); send({ kind: "ready", ok: false }); return; }
    // The table exists before the game's code is compiled into it; hooking
    // then crashes (measured 2026-10-01). Wait until the camera slot is code.
    if (!slotIsCode(base, DATA.cam_targets && DATA.cam_targets["client.BaseCamera.postUpdate"])) {
        log("game still booting (its code isn't in the table yet)");
        send({ kind: "ready", ok: false, early: true });
        return;
    }

    if (!setupNameApi()) log("skill-name API unavailable; showing raw ids");
    setupCursor(base);
    if (!setupCodexApi(base)) log("!! map natives missing; no kill counts");
    every(function () { codexDue = true; }, 8000);
    every(function () { rosterDue = true; }, 5000);
    every(function () { selfDue = true; }, 5000);
    listenAnalyze();
    listenConfig();
    every(function () { if (barValuesWanted) applyBarValues(); }, 2000);

    for (const nm in DATA.funcs) {
        try { getHeroFns.push({ addr: base.add(DATA.funcs[nm] * 8).readPointer() }); } catch (e) {}
    }
    // first lookup and the 3 s refresh (respawn / zone change) run on the game thread
    heroRefreshDue = true;
    every(function () { heroRefreshDue = true; }, 3000);

    // heartbeat: isInCombat for us and every recent dealer (drives the capture timer)
    every(function () {
        const now = Date.now();
        const state = {};
        if (localHero && localName) state[localName] = inCombat(localHero);
        for (const nm in heroByName) {
            if (now - heroByName[nm].t > 60000) { delete heroByName[nm]; continue; }
            state[nm] = inCombat(heroByName[nm].ptr);
        }
        send({ kind: "combat", state: state });
        checkRift();
        checkDungeon();
        if (++sweepBeat % 3 === 0) sweepInventory();
        if (sweepBeat % 12 === 0) checkCollection();
    }, 400);

    // the minimap: where the hero stands and faces (ent.Entity posx, posy,
    // rotationZ: radians, atan2(dy, dx) as Entity.lookAt sets it), on change
    // or once a second; plain reads, off the game thread like checkRift's
    if (OFF.Hero.posx != null && OFF.Hero.rotationZ != null) {
        let lastPos = null;
        every(function () {
            if (!localHero || localHero.isNull()) return;
            try {
                const x = localHero.add(OFF.Hero.posx).readDouble();
                const y = localHero.add(OFF.Hero.posy).readDouble();
                const r = localHero.add(OFF.Hero.rotationZ).readDouble();
                if (!isFinite(x) || !isFinite(y) || !isFinite(r)) return;
                const now = Date.now();
                if (lastPos && Math.abs(x - lastPos.x) < 0.3 && Math.abs(y - lastPos.y) < 0.3
                        && Math.abs(r - lastPos.r) < 0.02 && now - lastPos.t < 1000) return;
                lastPos = { x: x, y: y, r: r, t: now };
                send({ kind: "pos", x: Math.round(x * 10) / 10, y: Math.round(y * 10) / 10,
                       r: Math.round(r * 1000) / 1000, lv: lastLevel });
            } catch (e) {}
        }, 100);
    } else {
        log("position offsets missing (Hero.posx/rotationZ): no minimap");
    }

    // the minimap: the units the client holds around (GameLayer.units)
    // within MM_REACH of the hero, each second on a change: the wild
    // companions (a Critter unit not a hero's: a player's own has the hero
    // for its summonOwner) and the monsters alive (an ent.Foe or a boss,
    // not a hero's, not a companion)
    const CRITTERS = {};
    (DATA.critters || []).forEach(function (k) { CRITTERS[k] = 1; });
    const MM_REACH = 450;
    const UA0 = OFF.UnitAttributes && OFF.UnitAttributes.health != null ? OFF.UnitAttributes : null;
    if (OFF.GameLayer && OFF.GameLayer.units != null && OFF.Hero.posx != null
            && OFF.Unit && OFF.Unit.kind != null && OFF.Foe && OFF.Foe.summonOwner != null) {
        let lastUnits = "", lastUnitsT = 0, ownedLogged = false;
        const seenCrit = {};            // what the log said of each kind
        every(function () {
            if (!localHero || localHero.isNull()) return;
            try {
                const layer = localHero.add(OFF.Hero.layer).readPointer();
                if (!layer || layer.isNull()) return;
                const arr = layer.add(OFF.GameLayer.units).readPointer();
                if (!arr || arr.isNull()) return;
                const hx = localHero.add(OFF.Hero.posx).readDouble();
                const hy = localHero.add(OFF.Hero.posy).readDouble();
                const n = arr.add(OFF.ArrayObj.length).readS32();
                const data = arr.add(OFF.ArrayObj.array).readPointer();
                const pets = [], foes = [];
                let owned = 0;
                for (let i = 0; i < n && i < 4000; i++) {
                    const u = data.add(OFF.ArrayObj.data + i * 8).readPointer();
                    if (!u || u.isNull()) continue;
                    const tn = typeName(u);
                    if (tn !== "ent.Foe" && !(tn && tn.startsWith("ent.boss."))) continue;
                    const x = u.add(OFF.Hero.posx).readDouble(), y = u.add(OFF.Hero.posy).readDouble();
                    if (Math.abs(x - hx) > MM_REACH || Math.abs(y - hy) > MM_REACH) continue;
                    const k = hlStr(u.add(OFF.Unit.kind).readPointer());
                    if (!k) continue;
                    // a player's: its owner a hero (a wild one's may be its
                    // spawner, any other GameObject)
                    const ow = u.add(OFF.Foe.summonOwner).readPointer();
                    const owner = (ow && !ow.isNull()) ? typeName(ow) : null;
                    if (CRITTERS[k] && !seenCrit[k + "|" + owner]) {
                        seenCrit[k + "|" + owner] = 1;
                        const keep = OFF.Foe.persistantSummon != null
                            && u.add(OFF.Foe.persistantSummon).readU8();
                        log("minimap: companion " + k + ", owner " + (owner || "none")
                            + ", persistant " + (keep ? 1 : 0));
                    }
                    if (owner === "ent.Hero") {
                        if (CRITTERS[k]) owned++;
                        continue;
                    }
                    if (CRITTERS[k]) {
                        // a wild companion: its health is not sent to the
                        // client (reads 0), it is there while it is held
                        pets.push([k, Math.round(x), Math.round(y)]);
                        continue;
                    }
                    if (UA0 && OFF.Unit.attr != null) {
                        const at = u.add(OFF.Unit.attr).readPointer();
                        if (at && !at.isNull() && !(at.add(UA0.health).readDouble() > 0)) continue;
                    }
                    // a monster, with its level (Unit._level, the hero's offset)
                    const lv = OFF.Hero.level != null ? u.add(OFF.Hero.level).readS32() : 0;
                    foes.push([k, Math.round(x), Math.round(y), lv > 0 && lv < 1000 ? lv : 0]);
                }
                if (owned && !ownedLogged) {
                    ownedLogged = true;
                    log("minimap: " + owned + " player companion(s) around, left out");
                }
                const key = JSON.stringify([pets, foes]), now = Date.now();
                if (key === lastUnits && now - lastUnitsT < 5000) return;
                lastUnits = key;
                lastUnitsT = now;
                send({ kind: "units", pets: pets, foes: foes, lv: lastLevel });
            } catch (e) {}
        }, 1000);
    }
    // the shard roster, on its own slow clock
    if (OFF.Player && OFF.Player.hero != null
        && OFF.GameLayer && OFF.GameLayer.players != null) {
        every(sweepShard, 2000);
    } else {
        // stale analysis_out: no class tags would ever appear
        log("!! Player.hero / GameLayer.players missing from analysis_out — "
            + "class tags will stay empty. Regenerate offsets.");
    }

    hookGameTick(base);
    hookBossBar(base);

    const fi = DATA.combat_targets["ent.Unit.onInflictDamage"];
    const daddr = base.add(fi * 8).readPointer();
    const DR = OFF.DamageResult, BS = OFF.BaseSkill;
    // the unit a hit landed on (st.skill.DamageResult.target): the dungeon
    // journal's "on whom"; absent from older offsets
    const DR_TARGET = OFF.DamageResult.target;

    // the common hit fields of a st.skill.DamageResult*
    function readResult(dr) {
        const amount = dr.add(DR._amount).readDouble();
        let skill = null, sname = "";
        const bs = dr.add(DR.baseSkill).readPointer();
        if (bs && !bs.isNull()) {
            skill = hlStr(bs.add(BS.kind).readPointer());
            if (skill) sname = skillDisplayName(bs, skill);
        }
        const out = {
            amount: amount,
            skill: skill || "?",
            name: sname,
            element: hlStr(dr.add(DR.affinity).readPointer()) || "?",
            crit: dr.add(DR._critical).readU8() ? 1 : 0,
            kill: dr.add(DR._kill).readU8() ? 1 : 0,
        };
        // Nullified-hit diagnostic (`amount` counts even against an immune
        // boss): sent only when one of these fields is set.
        try {
            if (DR.blocker != null && DR.effect != null) {
                const blk = dr.add(DR._block).readDouble();
                const who = hlStr(dr.add(DR.blocker).readPointer());
                const eff = dr.add(DR.effect).readS32();
                if (blk > 0 || who || eff !== 0) {
                    out.block = blk;
                    out.blocker = who || "";
                    out.effect = eff;
                }
            }
        } catch (e) {}
        return out;
    }

    function heroIdent(hero) {
        const name = hlStr(hero.add(OFF.Hero.name).readPointer());
        const is_me = (localHero && hero.equals(localHero)) ? 1 : 0;
        const in_party = (is_me || partyNames[name] === 1) ? 1 : 0;
        if (name) heroByName[name] = { ptr: hero, t: Date.now() };
        return { player: name || "?", is_me: is_me, in_party: in_party };
    }

    // ---- summons and pets ----
    // A summon's hit arrives with an ent.Foe dealer (~13% of a bee build's
    // damage, measured 2026-07-30). Attribution is ent.Foe.summonOwner,
    // type-checked to ent.Hero: isSummon() is also true for a mob's pet, and
    // the skill's owner is the summon itself. The type check also guards the
    // raw pointer: a freed owner no longer reads back as ent.Hero.
    const FOE_CLASS = {};
    (OFF.foeClasses || []).forEach(function (c) { FOE_CLASS[c] = 1; });
    const canAttributeSummons =
        Object.keys(FOE_CLASS).length > 0 && OFF.Foe
        && OFF.Foe.summonOwner != null;
    if (!canAttributeSummons)
        log("!! summon damage will not be attributed (offsets file predates "
            + "foeClasses) — pet and totem damage is missing from the parse. "
            + "Delete analysis_out and restart to regenerate it.");

    // Resolved at damage time, never cached at summon birth: the owner's
    // name still reads null then (measured).
    function summonOwnerOf(dealer) {
        if (!canAttributeSummons) return null;
        try {
            if (!FOE_CLASS[typeName(dealer)]) return null;
            const owner = dealer.add(OFF.Foe.summonOwner).readPointer();
            if (!owner || owner.isNull()) return null;   // an ordinary mob
            if (typeName(owner) !== "ent.Hero") return null;
            return owner;
        } catch (e) { return null; }
    }

    // Which summon dealt it, as its raw Unit.kind ("Summon_Imp"); the host
    // resolves the display name ("Nightling Terror") and labels it.
    function petKind(dealer) {
        try {
            return hlStr(dealer.add(OFF.Unit.kind).readPointer()) || "";
        } catch (e) { return ""; }
    }

    // ---- damage from a status somebody else applied ----
    // Swarmstrike Accord (DS_Bladeleaf_Skill2) blesses allies, but the game
    // credits the bonus damage to the caster (rcx). Measured 2026-08-04: the
    // status is instantiated per ally and DamageResult.baseSkill.owner is the
    // ally who swung. General rule: status damage belongs to whoever carries
    // the status. Only that skill was measured, so each distinct
    // re-attribution is logged once.
    const reattrSeen = {};
    function statusHolderOf(dr) {
        try {
            const bs = dr.add(DR.baseSkill).readPointer();
            if (!bs || bs.isNull() || BS.owner == null) return null;
            const owner = bs.add(BS.owner).readPointer();
            if (!owner || owner.isNull()) return null;
            // raw pointer: type check guards against a freed hero
            if (typeName(owner) !== "ent.Hero") return null;
            return owner;
        } catch (e) { return null; }
    }

    // the unit a hit landed on, or null
    function hitTarget(dr) {
        if (DR_TARGET == null) return null;
        try {
            const t = dr.add(DR_TARGET).readPointer();
            return (t && !t.isNull()) ? t : null;
        } catch (e) { return null; }
    }

    Interceptor.attach(daddr, {
        onEnter() {
            try {
                const dealer = this.context.rcx;
                const dr = this.context.rdx;
                // players and their summons; a mob's hit only when it lands
                // on a player (sent apart: "taken", the dungeon journal's)
                let attributeTo = dealer, pet = "";
                if (typeName(dealer) !== "ent.Hero") {
                    const owner = summonOwnerOf(dealer);
                    if (!owner) {
                        const victim = hitTarget(dr);
                        if (!victim || typeName(victim) !== "ent.Hero") return;
                        const t = readResult(dr);
                        if (!(t.amount > 0)) return;
                        t.src = petKind(dealer);
                        send(Object.assign({ kind: "taken" }, heroIdent(victim), t));
                        return;
                    }
                    attributeTo = owner;
                    pet = petKind(dealer);
                }
                const r = readResult(dr);
                if (!(r.amount > 0)) return;
                if (pet) r.pet = pet;
                // on whom: a player's name, else the unit's kind
                const tg = hitTarget(dr);
                if (tg) {
                    if (typeName(tg) === "ent.Hero") r.to = heroIdent(tg).player;
                    else r.tk = petKind(tg);
                }
                // hero-dealt hits only; summons are already resolved
                if (!pet) {
                    const holder = statusHolderOf(dr);
                    if (holder && !holder.equals(attributeTo)) {
                        const sig = r.skill + "|" + (hlStr(attributeTo.add(
                            OFF.Hero.name).readPointer()) || "?");
                        if (!reattrSeen[sig]) {
                            reattrSeen[sig] = 1;
                            log("re-attributing " + r.skill + " off "
                                + sig.split("|")[1] + " to the status holder "
                                + "(measured: Swarmstrike Accord)");
                        }
                        attributeTo = holder;
                    }
                }
                const who = heroIdent(attributeTo);
                send(Object.assign({ kind: "hit" }, who, r));
            } catch (e) {}
        }
    });

    // ---- crowd controls ----
    // A status coming to life on a unit (st.skill.Status.init, the client's
    // copy of the server's): the crowd controls only (data.cdb's
    // CrowdControl flag, listed by build_targets), between a player (or
    // their summon) and anything, for the journals.
    const stFi = (DATA.status_fns || {})["st.skill.Status.init"];
    const CC_STATUSES = {};
    Object.keys(DATA.cc_statuses || {}).forEach(function (k) {
        CC_STATUSES[k] = DATA.cc_statuses[k];
    });
    const STO = OFF.Status;
    // a unit as the journal names it: a player (or the player behind a
    // summon), else the unit's kind
    function unitIdent(u) {
        if (!u || u.isNull()) return null;
        if (typeName(u) === "ent.Hero") return { hero: heroIdent(u) };
        const owner = summonOwnerOf(u);
        if (owner) return { hero: heroIdent(owner), pet: petKind(u) };
        return { kind: petKind(u) };
    }
    if (stFi != null && STO && OFF.Status.instigator != null) {
        Interceptor.attach(base.add(stFi * 8).readPointer(), {
            onEnter() {
                try {
                    const s = this.context.rcx;
                    const kind = hlStr(s.add(BS.kind).readPointer());
                    if (!kind || !CC_STATUSES[kind]) return;
                    const on = unitIdent(s.add(OFF.BaseSkill.owner).readPointer());
                    const by = unitIdent(s.add(STO.instigator).readPointer());
                    if (!on || !by) return;
                    const ev = { kind: "status", st: kind, cc: CC_STATUSES[kind] };
                    const sk = s.add(STO.instigatorSkill).readPointer();
                    if (sk && !sk.isNull()) {
                        const sid = hlStr(sk.add(BS.kind).readPointer());
                        if (sid) { ev.skill = sid; ev.name = skillDisplayName(sk, sid); }
                    }
                    if (by.hero) {
                        // a player's: on a unit, or on another player
                        if (by.pet) ev.pet = by.pet;
                        if (on.hero) ev.to = on.hero.player; else ev.tk = on.kind;
                        send(Object.assign(ev, by.hero));
                    } else if (on.hero) {
                        // a mob's, on a player
                        ev.src = by.kind;
                        send(Object.assign(ev, on.hero));
                    }
                } catch (e) {}
            }
        });
    } else {
        log("status data missing (st.skill.Status.init / Status offsets): "
            + "the journals show no crowd control");
    }

    // ---- the targeting options (the monsters' nameplates) ----
    // Right after the game updates a nameplate (FoeWidget, each frame: it
    // has just placed its target indicator), on the game thread:
    //  * the indicator scaled (the game's layout keeps it centred, its foot
    //    lifted to stay where it was), a halo around it (the game's own
    //    h2d.filter.Glow at the screen's resolution, set as its filter,
    //    taken off when none);
    //  * the health bar's fill coloured by the foe's kind: one that targets
    //    the hero (Foe.target is the hero's network uid), an aggressive one,
    //    a neutral one (data.cdb unit flags NeutralAggro / NoAggro). A foe's
    //    fill is a gradient (its background's shader, seen 2026-10-14: its
    //    plain colour unused): both its colours set, the second kept in the
    //    same ratio to the first as the game's; the game's given back when
    //    no colour is chosen. The life being lost (lagBar) painted by the
    //    game each time it moves (BaseGauge's lag update -> applyColorTo(
    //    lagBar, its lagColor)): our colour given in place of its own (a
    //    null<i32> of ours, the game's i32 type). The bar's background
    //    (emptyBar, a plain colour) set, its own given back after;
    //  * the level beside the bar (LevelDisplay) scaled as the bar's height;
    //  * the bars bigger: the gauge's width and height (its fill follows,
    //    its inset, the frame, kept);
    //  * the bar of the foe the hero targets (its indicator shown) outlined:
    //    the outline of the bar's background (a DynamicBackground's shader,
    //    as its set_outline sets it), outside it (a negative outline offset:
    //    the background drawn that much larger, DynamicBackground.sync), its
    //    own given back after.
    const FF = DATA.foe_fns || {};
    const fuFi = DATA.foe_update;
    const D = OFF.Draw;
    if (fuFi != null && OFF.FoeWidget && OFF.FoeTarget && OFF.Gauge && D && OFF.HealthBar
            && OFF.HealthBar.healthGauge != null) {
        try {
            const fnOf = (fi, ret, args) => fi != null
                ? new NativeFunction(base.add(fi * 8).readPointer(), ret, args) : null;
            const setBarW = fnOf(FF["ui.comp.BaseGauge.set_barWidth"], "int", ["pointer", "int"]);
            const setBarH = fnOf(FF["ui.comp.BaseGauge.set_barHeight"], "int", ["pointer", "int"]);
            const setFilter = fnOf(FF["h2d.Object.set_filter"], "pointer", ["pointer", "pointer"]);
            const newGlow = fnOf(FF["h2d.filter.$Glow.__constructor__"], "void",
                                 ["pointer", "pointer", "pointer", "pointer", "pointer", "pointer", "pointer"]);
            const libhl = Process.findModuleByName("libhl.dll");
            const NEUTRAL = {};
            (DATA.neutral_units || []).forEach(function (k) { NEUTRAL[k] = 1; });
            let plates = new Map();     // widget -> its state (below)
            let plateFailed = false;

            // a background's colour vector: a DynamicBackground's shader's,
            // else the Drawable's own
            const dynShader = function (bg) {
                if (typeName(bg) !== "shiro.ui.DynamicBackground") return null;
                const sh = bg.add(D.dynShader).readPointer();
                return sh && !sh.isNull() ? sh : null;
            };
            const bgVec = function (bg) {
                const sh = dynShader(bg);
                const v = sh ? sh.add(D.dynColor).readPointer() : bg.add(D.color).readPointer();
                return v && !v.isNull() ? v : null;
            };
            const vecInt = function (v) {
                const ch = o => Math.max(0, Math.min(255, Math.round(v.add(o).readDouble() * 255)));
                return ((ch(D.vw) << 24) | (ch(D.vx) << 16) | (ch(D.vy) << 8) | ch(D.vz)) | 0;
            };
            // a fill's colours: its gradient's two, or its plain one
            const fillVecs = function (fill) {
                const bg = fill.add(D.background).readPointer();
                const sh = bg && !bg.isNull() ? dynShader(bg) : null;
                if (!sh) return null;
                const vs = sh.add(D.dynUseGradient).readU8()
                    ? [sh.add(D.dynGrad1).readPointer(), sh.add(D.dynGrad2).readPointer()]
                    : [sh.add(D.dynColor).readPointer()];
                return vs.every(v => v && !v.isNull()) ? vs : null;
            };
            const RGB = [D.vx, D.vy, D.vz];
            const readVecs = vs => vs.map(v => RGB.map(o => v.add(o).readDouble()));
            const writeVecs = function (vs, cols) {
                vs.forEach(function (v, i) {
                    RGB.forEach(function (o, k) { v.add(o).writeDouble(cols[i][k]); });
                });
            };
            // our colour as the game's: its first colour ours, the others in
            // the ratio the game's have to its first
            const colorAs = function (c, own, end) {
                const ours = [(c >> 16 & 255) / 255, (c >> 8 & 255) / 255, (c & 255) / 255];
                if (end !== null && end !== undefined && own.length === 2) {
                    return [ours, [(end >> 16 & 255) / 255, (end >> 8 & 255) / 255, (end & 255) / 255]];
                }
                return own.map(rgb => rgb.map((x, k) =>
                    Math.max(0, Math.min(1, ours[k] * (own[0][k] > 0.004 ? x / own[0][k] : 1)))));
            };
            let colorLogged = false;

            // the life being lost: our colour given to the game as it paints
            // it (a null<i32> of ours, the game's i32 type, kept for good)
            const hltI32 = libhl ? libhl.findExportByName("hlt_i32") : null;
            const lags = new Map();         // gauge -> {bar, c}
            const boxes = new Map();
            const boxFor = function (c) {
                let b = boxes.get(c);
                if (!b) {
                    b = Memory.alloc(16);
                    b.writePointer(hltI32);
                    b.add(8).writeS32(c | 0);
                    boxes.set(c, b);
                }
                return b;
            };
            const colorFi = FF["ui.comp.BaseGauge.applyColorTo"];
            const gaugeBoss = new Map();    // gauge -> its BossInfo, or null
            if (colorFi != null && hltI32) {
                Interceptor.attach(base.add(colorFi * 8).readPointer(), {
                    onEnter() {
                        const g = this.context.rcx;
                        const gk = g.toString();
                        // a boss's (its gauge in a BossInfo, looked for once)
                        let slot = gaugeBoss.get(gk);
                        if (slot === undefined) {
                            if (gaugeBoss.size > 600) gaugeBoss.clear();
                            slot = null;
                            try {
                                let o = g;
                                for (let i = 0; i < 10 && o && !o.isNull(); i++) {
                                    if (typeName(o) === "ui.hud.BossInfo") { slot = o; break; }
                                    o = o.add(OFF.Object.parent).readPointer();
                                }
                            } catch (e) { slot = null; }
                            // not yet in its place: looked for again
                            if (slot !== null || g.add(OFF.Object.parent).readPointer().isNull() === false) {
                                gaugeBoss.set(gk, slot);
                            }
                        }
                        if (slot && bossStyler) bossStyler(slot);
                        if (!lags.size) return;
                        const e = lags.get(gk);
                        if (e && this.context.rdx.equals(e.bar)) this.context.r8 = boxFor(e.c);
                    }
                });
            } else {
                log("gauge colour data missing (BaseGauge.applyColorTo / hlt_i32): "
                    + "no colour for the life being lost");
            }
            // the layouts a level badge sits in (h2d.Flow and the game's own)
            const FLOWS = { "h2d.Flow": 1, "ui.BaseElement": 1, "ui.UIElement": 1 };
            // a plain background's colour (the bar's own, under it)
            const plainVec = function (bar) {
                const bg = bar && !bar.isNull() ? bar.add(D.background).readPointer() : null;
                const sh = bg && !bg.isNull() ? dynShader(bg) : null;
                const v = sh ? sh.add(D.dynColor).readPointer() : null;
                return v && !v.isNull() ? v : null;
            };

            // the outline of the targeted foe's bar: its background's shader
            // as DynamicBackground.set_outline sets it; its own kept to give back
            const outlineOf = function (gauge) {
                const eb = gauge.add(OFF.Gauge.emptyBar).readPointer();
                if (!eb || eb.isNull()) return null;
                const bg = eb.add(D.background).readPointer();
                return bg && !bg.isNull() ? dynShader(bg) : null;
            };
            const readOutline = function (sh) {
                const col = sh.add(D.dynOutlineColor).readPointer();
                const th = sh.add(D.dynOutlineThickness).readPointer();
                if (!col || col.isNull() || !th || th.isNull()) return null;
                const scol = sh.add(D.dynShadowColor).readPointer();
                const soff = sh.add(D.dynShadowOffset).readPointer();
                const sspr = sh.add(D.dynShadowSpread).readPointer();
                if (!scol || scol.isNull() || !soff || soff.isNull() || !sspr || sspr.isNull()) return null;
                const o = { use: sh.add(D.dynUseOutline).readU8(), sep: sh.add(D.dynSeparate).readU8(),
                            off: sh.add(D.dynOutlineOffset).readDouble(), col: [], th: [],
                            shUse: sh.add(D.dynUseShadow).readU8(), shIn: sh.add(D.dynShadowInset).readU8(),
                            shBlur: sh.add(D.dynShadowBlur).readDouble(), shCol: [],
                            shOff: [soff.add(D.vx).readDouble(), soff.add(D.vy).readDouble()],
                            shSpr: [sspr.add(D.vx).readDouble(), sspr.add(D.vy).readDouble()] };
                for (const f of [D.vx, D.vy, D.vz, D.vw]) {
                    o.col.push(col.add(f).readDouble());
                    o.th.push(th.add(f).readDouble());
                    o.shCol.push(scol.add(f).readDouble());
                }
                return o;
            };
            const writeOutline = function (sh, o) {
                const col = sh.add(D.dynOutlineColor).readPointer();
                const th = sh.add(D.dynOutlineThickness).readPointer();
                [D.vx, D.vy, D.vz, D.vw].forEach(function (f, i) {
                    col.add(f).writeDouble(o.col[i]);
                    th.add(f).writeDouble(o.th[i]);
                });
                sh.add(D.dynOutlineOffset).writeDouble(o.off);
                const scol = sh.add(D.dynShadowColor).readPointer();
                const soff = sh.add(D.dynShadowOffset).readPointer();
                const sspr = sh.add(D.dynShadowSpread).readPointer();
                [D.vx, D.vy, D.vz, D.vw].forEach(function (f, i) { scol.add(f).writeDouble(o.shCol[i]); });
                soff.add(D.vx).writeDouble(o.shOff[0]);
                soff.add(D.vy).writeDouble(o.shOff[1]);
                sspr.add(D.vx).writeDouble(o.shSpr[0]);
                sspr.add(D.vy).writeDouble(o.shSpr[1]);
                sh.add(D.dynShadowBlur).writeDouble(o.shBlur);
                // its switches are the shader's constants: told changed only if so
                if (sh.add(D.dynUseOutline).readU8() !== o.use || sh.add(D.dynSeparate).readU8() !== o.sep
                        || sh.add(D.dynUseShadow).readU8() !== o.shUse
                        || sh.add(D.dynShadowInset).readU8() !== o.shIn) {
                    sh.add(D.dynUseOutline).writeU8(o.use);
                    sh.add(D.dynSeparate).writeU8(o.sep);
                    sh.add(D.dynUseShadow).writeU8(o.shUse);
                    sh.add(D.dynShadowInset).writeU8(o.shIn);
                    sh.add(D.dynConstModified).writeU8(1);
                }
            };

            // The level beside the bar (LevelDisplay: its picture, the dark
            // square and its frame in one, and its digits, a FmtText): as tall
            // as the bar times its own size, shown or not, moved by its
            // place in its row (its FlowProperties' offsets), its digits
            // coloured, its picture tinted (multiplied); the game's own kept
            // and given back. Its row laid out anew when it changes.
            const vecOf = function (o) {
                const v = o && !o.isNull() ? o.add(D.color).readPointer() : null;
                return v && !v.isNull() ? v : null;
            };
            const rgbOf = c => [(c >> 16 & 255) / 255, (c >> 8 & 255) / 255, (c & 255) / 255];
            const tintObj = function (st, key, o, c) {
                const v = vecOf(o);
                if (!v) return;
                if (c !== null) {
                    if (!st[key]) st[key] = readVecs([v]);
                    writeVecs([v], [rgbOf(c)]);
                } else if (st[key]) {
                    writeVecs([v], st[key]);
                    st[key] = null;
                }
            };
            const flowPropsOf = function (o, up) {
                const arr = up.add(D.flowProps).readPointer();
                if (!arr || arr.isNull()) return null;
                const n = arr.add(OFF.ArrayObj.length).readS32();
                const data = arr.add(OFF.ArrayObj.array).readPointer();
                for (let i = 0; i < n && i < 64; i++) {
                    const fp = data.add(OFF.ArrayObj.data + i * 8).readPointer();
                    if (fp && !fp.isNull() && fp.add(D.fpElt).readPointer().equals(o)) return fp;
                }
                return null;
            };
            // a picture's tile half a texel in on each side (once a tile:
            // tiles are shared): scaled, its neighbours in the texture
            // no more drawn on its edge (the white line over the level)
            const insetDone = new Set();
            const insetTile = function (bmp) {
                if (!bmp || bmp.isNull()) return;
                const t = bmp.add(D.bitmapTile).readPointer();
                if (!t || t.isNull() || insetDone.has(t.toString())) return;
                const tex = t.add(D.tileTex).readPointer();
                if (!tex || tex.isNull()) return;
                const tw = tex.add(D.texW).readS32(), th = tex.add(D.texH).readS32();
                if (tw <= 0 || th <= 0) return;
                insetDone.add(t.toString());
                const du = 0.5 / tw, dv = 0.5 / th;
                t.add(D.tileU).writeDouble(t.add(D.tileU).readDouble() + du);
                t.add(D.tileV).writeDouble(t.add(D.tileV).readDouble() + dv);
                t.add(D.tileU2).writeDouble(t.add(D.tileU2).readDouble() - du);
                t.add(D.tileV2).writeDouble(t.add(D.tileV2).readDouble() - dv);
            };
            const levelBadge = function (st, o) {
                const b = st.badge;
                const up = b.add(OFF.Object.parent).readPointer();
                const flow = up && !up.isNull() && FLOWS[typeName(up)] ? up : null;
                let moved = false;
                // its size
                const s = (st.h0 > 0 ? (st.h0 + 2 * o.extra) / st.h0 : 1) * o.scale / 100;
                if (Math.abs(b.add(D.scaleX).readDouble() - s) > 0.001) {
                    b.add(D.scaleX).writeDouble(s);
                    b.add(D.scaleY).writeDouble(s);
                    b.add(D.posChanged).writeU8(1);
                    moved = true;
                    if (Math.abs(s - 1) > 0.001) insetTile(findChild(b, "h2d.Bitmap", 3));
                }
                // shown or not
                const vis = b.add(OFF.Object.visible).readU8() !== 0;
                if (!o.show && vis) {
                    if (setVisibleFn) setVisibleFn(b, 0);
                    st.badgeHidden = true;
                    moved = true;
                } else if (o.show && st.badgeHidden) {
                    if (setVisibleFn && !vis) setVisibleFn(b, 1);
                    st.badgeHidden = false;
                    moved = true;
                }
                // its place in its row: the game's offsets plus ours (the
                // game's taken anew if it sets them again)
                if (flow) {
                    const fp = flowPropsOf(b, flow);
                    if (fp) {
                        const cx = fp.add(D.fpOffsetX).readS32(), cy = fp.add(D.fpOffsetY).readS32();
                        if (!st.off || cx !== st.off.x || cy !== st.off.y) {
                            st.off = { bx: cx, by: cy, x: cx, y: cy };      // the game's (anew)
                        }
                        const tx = st.off.bx + o.dx, ty = st.off.by + o.dy;
                        if (cx !== tx || cy !== ty) {
                            fp.add(D.fpOffsetX).writeS32(tx);
                            fp.add(D.fpOffsetY).writeS32(ty);
                            st.off.x = tx;
                            st.off.y = ty;
                            moved = true;
                        }
                    }
                }
                if (moved && flow) flow.add(D.needReflow).writeU8(1);
                // its digits, its picture
                if (o.text !== null || st.lvTextOwn) {
                    tintObj(st, "lvTextOwn", b.add(D.levelText).readPointer(), o.text);
                }
                if (o.tint !== null || st.lvPicOwn) {
                    tintObj(st, "lvPicOwn", findChild(b, "h2d.Bitmap", 3), o.tint);
                }
            };

            // A bar styled (a monster's, a boss's): the life being lost
            // coloured (c: its fill, lc: the life being lost, bg: under it,
            // null: the game's), bigger by extra (its width and height: the
            // game may set its own again, taken then as its new base).
            const styleBar = function (st, c, lc, bg, extra, wider, cEnd) {
                wider = wider || 0;
                const gk = st.gauge.toString();
                if (lc !== null && st.lag && !st.lag.isNull()) lags.set(gk, { bar: st.lag, c: lc });
                else if (lags.size) lags.delete(gk);
                if (bg !== null || st.bgOwn) {
                    const v = plainVec(st.gauge.add(OFF.Gauge.emptyBar).readPointer());
                    if (v) {
                        if (bg !== null) {
                            if (!st.bgOwn) st.bgOwn = readVecs([v]);
                            writeVecs([v], [rgbOf(bg)]);
                        } else {
                            writeVecs([v], st.bgOwn);
                            st.bgOwn = null;
                        }
                    }
                }
                if ((c !== null || st.own) && st.fill && !st.fill.isNull()) {
                    const vs = fillVecs(st.fill);
                    if (vs) {
                        if (c !== null) {
                            if (!st.own || st.own.length !== vs.length) st.own = readVecs(vs);
                            writeVecs(vs, colorAs(c, st.own, cEnd));
                            st.ownEnd = cEnd;
                            if (!colorLogged) {
                                colorLogged = true;
                                log("bar colour: the fill's " + (vs.length === 2 ? "gradient" : "colour")
                                    + " set");
                            }
                        } else {
                            if (st.own.length === vs.length) writeVecs(vs, st.own);
                            st.own = null;
                        }
                    }
                }
                if (setBarW && setBarH && st.w0 > 0 && st.h0 > 0) {
                    sizeGauge(st.gauge, st, extra, wider);
                    if (st.shield && !st.shield.isNull()) {
                        if (!st.sh) st.sh = { w0: st.shield.add(OFF.Gauge.barWidth).readS32(),
                                              h0: st.shield.add(OFF.Gauge.barHeight).readS32(), set: null };
                        if (st.sh.w0 > 0 && st.sh.h0 > 0) sizeGauge(st.shield, st.sh, extra, wider);
                    }
                }
            };
            // a gauge bigger by extra (and wider): the game may set its own
            // size again, taken then as its new base
            const sizeGauge = function (g, rec, extra, wider) {
                const G = OFF.Gauge;
                const cw = g.add(G.barWidth).readS32();
                const ch = g.add(G.barHeight).readS32();
                let again = rec.set === null ? extra !== 0 || wider !== 0
                    : rec.set.extra !== extra || rec.set.wider !== wider;
                if (rec.set !== null) {
                    if (cw !== rec.set.w) { rec.w0 = cw; again = true; }
                    if (ch !== rec.set.h) { rec.h0 = ch; again = true; }
                }
                if (again) {
                    const nw = Math.max(20, rec.w0 + 2 * extra + wider), nh = rec.h0 + 2 * extra;
                    if (cw !== nw) setBarW(g, nw);
                    if (ch !== nh) setBarH(g, nh);
                    rec.set = { extra: extra, wider: wider, w: nw, h: nh };
                }
            };
            // a health bar's shield gauge (drawn over its life's)
            const shieldOf = function (hb) {
                if (!hb || hb.isNull() || OFF.HealthBar.shieldGauge == null) return null;
                const s = hb.add(OFF.HealthBar.shieldGauge).readPointer();
                return s && !s.isNull() ? s : null;
            };
            // a bar's state (its parts, the game's own values kept)
            // A bar's state's signature: what each part it keeps is and holds
            // it. Kept parts are looked at again each frame: a nameplate gone,
            // its memory may be another object's (a network one) and a new
            // nameplate at the same place; written to, that other object would
            // be broken. A signature changed: the state made anew.
            const sigOf = function (st) {
                let out = "";
                for (const o of [st.gauge, st.fill, st.lag, st.shield, st.badge]) {
                    if (!o || o.isNull()) { out += "-|"; continue; }
                    out += typeName(o) + "@" + o.add(OFF.Object.parent).readPointer() + "|";
                }
                return out;
            };
            const stale = function (st) {
                try { return sigOf(st) !== st.sig; } catch (e) { return true; }
            };
            const barState = function (gauge, badge, hb) {
                const st = { gauge: gauge && !gauge.isNull() ? gauge : null, fill: null, lag: null,
                             inset0: null, w0: 0, h0: 0, set: null, own: null, bgOwn: null,
                             badge: badge, shield: shieldOf(hb) };
                if (st.gauge) {
                    st.fill = st.gauge.add(OFF.Gauge.fillBar).readPointer();
                    st.lag = st.gauge.add(OFF.Gauge.lagBar).readPointer();
                    st.inset0 = st.gauge.add(OFF.Gauge.inset).readS32();
                    st.w0 = st.gauge.add(OFF.Gauge.barWidth).readS32();
                    st.h0 = st.gauge.add(OFF.Gauge.barHeight).readS32();
                }
                return st;
            };

            // ---- the text in the bars ----
            // As the game gives a bar its text (a HealthBar's txt, a gauge's
            // own: "value / max", fresh from its making), it is written as
            // chosen: the value, a percentage, value / max (percentage). A
            // new string's bytes (libhl's), the string itself kept (only
            // one allocation, made before it is linked).
            const allocBytes = libhl
                ? new NativeFunction(libhl.findExportByName("hl_alloc_bytes"), "pointer", ["int"]) : null;
            let textLogged = false;
            const writeText = function (str, mode, name) {
                const len = str.add(OFF.String.length).readS32();
                if (len <= 0 || len > 80) return;
                const t = str.add(OFF.String.bytes).readPointer().readUtf16String(len);
                const m = /^(\d+) \/ (\d+)(.*)$/.exec(t);
                if (!m) return;
                const v = +m[1], mx = +m[2];
                const pct = mx > 0 ? Math.round(v / mx * 100) : 0;
                const out = name !== undefined ? name
                    : mode === "value" ? m[1]
                    : mode === "pct" ? pct + "%"
                    : mode === "max_pct" ? m[1] + " / " + m[2] + m[3] + " (" + pct + "%)"
                    : t;
                if (out === t) return;
                if (out.length <= len) {
                    // shorter: its own bytes, cut
                    str.add(OFF.String.bytes).readPointer().writeUtf16String(out);
                } else {
                    if (!allocBytes) return;
                    const b = allocBytes((out.length + 1) * 2);
                    if (!b || b.isNull()) return;
                    b.writeUtf16String(out);
                    str.add(OFF.String.bytes).writePointer(b);
                }
                str.add(OFF.String.length).writeS32(out.length);
                if (!textLogged) {
                    textLogged = true;
                    log("bar text: written as chosen (" + mode + ")");
                }
            };
            const onText = function (ctx) {
                if (!barTexts.size) return;
                const who = barTexts.get(ctx.rcx.toString());
                if (!who) return;
                if (typeof who === "object") {
                    // an ally's: as its own choice
                    if (allyText !== "none" && allyText !== "max") {
                        try { writeText(ctx.rdx, allyText); } catch (e) {}
                    }
                    return;
                }
                const mode = who === "boss" ? bossText : foeText;
                if (mode === "none" || mode === "max") return;
                try { writeText(ctx.rdx, mode); } catch (e) {}
            };
            for (const nm of ["ui.comp.BaseGauge.set_text", "ui.comp.FmtText.set_text"]) {
                const fi = FF[nm];
                if (fi == null || OFF.String.length == null) {
                    log("text data missing (" + nm + "): the bars' text as the game writes it");
                    continue;
                }
                Interceptor.attach(base.add(fi * 8).readPointer(), {
                    onEnter() { onText(this.context); }
                });
            }

            // ---- a boss's bar (its slot in BossesInfo, followed by the
            // bosses' tracking): its colours, size, level ----
            let bossBars = new Map();
            bossStyler = function (slot) {
                if (plateFailed) return;
                try {
                    const key = slot.toString();
                    if (typeName(slot) !== "ui.hud.BossInfo") return;
                    let st = bossBars.get(key);
                    if (st && stale(st)) { bossBars.delete(key); st = null; }
                    if (!st) {
                        if (bossBars.size > 64) bossBars = new Map();
                        const hb = findChild(slot, "ui.comp.HealthBar", 0);
                        st = barState(hb ? hb.add(OFF.HealthBar.healthGauge).readPointer() : null,
                                      findChild(slot, "ui.hud.LevelDisplay", 0), hb);
                        st.txts = barTextsOf(hb, st.gauge);
                        st.sig = sigOf(st);
                        bossBars.set(key, st);
                    }
                    if (!st.gauge) return;
                    styleBar(st, bossColor, bossLag, bossBg, bossExtra, 0, bossColorEnd);
                    textStyle(st.txts, bossTextScale);
                    if (st.badge && !st.badge.isNull()) {
                        levelBadge(st, { extra: bossExtra, scale: bossLevelScale, show: bossLevelShow,
                                         dx: bossLevelDx, dy: bossLevelDy, text: null, tint: null });
                    }
                } catch (err) {
                    plateFailed = true;
                    log("boss bar options failed: " + err);
                }
            };

            // The text in a bar (a HealthBar's txt, its gauge's: whichever
            // the game shows): its size (scaled, its row laid out anew).
            const textStyle = function (txts, scale) {
                const k = scale / 100;
                for (const t of txts) {
                    if (!t || t.isNull()) continue;
                    if (Math.abs(t.add(D.scaleX).readDouble() - k) > 0.001) {
                        t.add(D.scaleX).writeDouble(k);
                        t.add(D.scaleY).writeDouble(k);
                        t.add(D.posChanged).writeU8(1);
                        const up = t.add(OFF.Object.parent).readPointer();
                        if (up && !up.isNull() && FLOWS[typeName(up)]) up.add(D.needReflow).writeU8(1);
                    }
                }
            };
            const barTextsOf = function (hb, gauge) {
                const out = [];
                if (hb && !hb.isNull()) out.push(hb.add(OFF.HealthBar.txt).readPointer());
                if (gauge && !gauge.isNull()) out.push(gauge.add(OFF.Gauge.txt).readPointer());
                return out;
            };

            // the halo: a Glow of the game's (its type from its class global),
            // drawn at the screen's resolution and smoothed (not the
            // indicator's own small size scaled up)
            const glowType = function () {
                if (!hlModule || !OFF.Glow) return null;
                const cls = hlGlobal(OFF.Glow.global);
                if (typeName(cls) !== "h2d.filter.$Glow") return null;
                const t = cls.add(OFF.Glow.type).readPointer();
                return t && !t.isNull() ? t : null;
            };
            const glowAlloc = libhl
                ? new NativeFunction(libhl.findExportByName("hl_alloc_obj"), "pointer", ["pointer"]) : null;
            const makeGlow = function (c, blur, strength, opacity) {
                const t = glowType();
                if (!t || !newGlow || !glowAlloc) return null;
                const g = glowAlloc(t);
                if (!g || g.isNull()) return null;
                const col = Memory.alloc(8), alpha = Memory.alloc(8), radius = Memory.alloc(8),
                      gain = Memory.alloc(8), quality = Memory.alloc(8), smoothCol = Memory.alloc(8);
                col.writeS32(c & 0xFFFFFF);
                alpha.writeDouble(opacity === undefined ? 1 : opacity);
                radius.writeDouble(blur);
                gain.writeDouble(strength || 2);
                quality.writeDouble(2);
                smoothCol.writeU8(1);
                newGlow(g, col, alpha, radius, gain, quality, smoothCol);
                g.add(OFF.Glow.smooth).writeU8(1);
                g.add(OFF.Glow.screenRes).writeU8(1);
                return g;
            };

            // ---- the allies' (players') nameplates (HeroWidget), each
            // frame after the game's update: their bar's size, their level,
            // their name in the bar (its own place hidden, the bar's text
            // shown with it; given back after) ----
            const heFi = DATA.hero_update;
            // A String of ours (the game's type, borrowed from one of its
            // strings): its object and its bytes held in a root of libhl's
            // while they are made (the collector never sees them half-made).
            // (hl_add_root locks the collector: called on the game's thread
            // only, the first time a string is made there, never at load)
            const addRootAt = libhl ? libhl.findExportByName("hl_add_root") : null;
            const hlRoot = addRootAt ? Memory.alloc(16) : null;
            let rooted = false;
            const makeStr = function (text, like) {
                if (!hlRoot || !allocBytes || !glowAlloc || !like || like.isNull()) return null;
                if (!rooted) {
                    const addRoot = new NativeFunction(addRootAt, "void", ["pointer"]);
                    addRoot(hlRoot);
                    addRoot(hlRoot.add(8));
                    rooted = true;
                }
                const b = allocBytes((text.length + 1) * 2);
                if (!b || b.isNull()) return null;
                hlRoot.writePointer(b);
                b.writeUtf16String(text);
                const s = glowAlloc(like.readPointer());
                if (!s || s.isNull()) { hlRoot.writePointer(NULL); return null; }
                hlRoot.add(8).writePointer(s);
                s.add(OFF.String.bytes).writePointer(b);
                s.add(OFF.String.length).writeS32(text.length);
                return s;
            };
            const strOf = function (s) {
                if (!s || s.isNull()) return "";
                const n = s.add(OFF.String.length).readS32();
                return n > 0 && n < 400 ? s.add(OFF.String.bytes).readPointer().readUtf16String(n) : "";
            };
            // a text's own given back / ours set (the game's kept to give back)
            const setOwnText = function (st, key, t, text, like) {
                const cur = strOf(t.add(HW.fmtText).readPointer());
                if (cur === text) return;
                if (st[key] === undefined) st[key] = cur;
                const s = makeStr(text, like);
                if (s && setTextFn) setTextFn(t, s);
                if (hlRoot) hlRoot.writePointer(NULL);
                if (hlRoot) hlRoot.add(8).writePointer(NULL);
            };
            const backOwnText = function (st, key, t, like) {
                if (st[key] === undefined) return;
                const was = st[key];
                delete st[key];
                if (strOf(t.add(HW.fmtText).readPointer()) === was) return;
                const s = makeStr(was, like);
                if (s && setTextFn) setTextFn(t, s);
                if (hlRoot) hlRoot.writePointer(NULL);
                if (hlRoot) hlRoot.add(8).writePointer(NULL);
            };
            // ---- an ally's class icon, beside its level ----
            // Two Bitmaps of the game's (h2d.Bitmap's constructor, its type
            // the level's picture's): the level's square's tile (its rounded
            // dark ground), the class's icon (the game's own tile, from
            // BaseUI.loadTextIcon "icon/Class_<class>") as its child. Held in
            // libhl's root while made; children of the level's picture (not a
            // layout: placed by their x, y), so they go with the level.
            const newBmp = fnOf(FF["h2d.$Bitmap.__constructor__"], "void", ["pointer", "pointer", "pointer"]);
            const iconTile = fnOf(FF["ui.$BaseUI.loadTextIcon"], "pointer", ["pointer"]);
            const newGrid = fnOf(FF["h2d.$ScaleGrid.__constructor__"], "void",
                                 ["pointer", "pointer", "int", "int", "pointer", "pointer", "pointer"]);
            const gridW = fnOf(FF["h2d.ScaleGrid.set_width"], "double", ["pointer", "double"]);
            const gridH = fnOf(FF["h2d.ScaleGrid.set_height"], "double", ["pointer", "double"]);
            // the level's square as the game shows it: its layout's
            // background (a ScaleGrid) when it has one, else its picture
            const levelSquare = function (badge, lvBmp) {
                const sg = badge.add(D.background).readPointer();
                if (sg && !sg.isNull() && typeName(sg) === "h2d.ScaleGrid"
                        && sg.add(OFF.Object.visible).readU8()) return sg;
                return lvBmp && lvBmp.add(OFF.Object.visible).readU8() ? lvBmp : null;
            };
            const makeClassIcon = function (st, lvBmp, cls, like, w) {
                if (!newBmp || !iconTile || !glowAlloc || !hlRoot) return;
                const src = levelSquare(st.badge, lvBmp);
                if (!src) { st.clsTried = false; return; }      // looked for again
                const grid = typeName(src) === "h2d.ScaleGrid";
                if (grid && (!newGrid || !gridW || !gridH)) return;
                const srcTile = src.add(grid ? D.gridTile : D.bitmapTile).readPointer();
                if (!srcTile || srcTile.isNull()) return;
                const name = makeStr("icon/Class_" + cls, like);
                if (!name) return;
                const tile = iconTile(name);
                hlRoot.writePointer(NULL);
                hlRoot.add(8).writePointer(NULL);
                if (!tile || tile.isNull()) return;
                // its copy, a child of the square (drawn with it: under the
                // bar, drawn after, where they overlap). A child of the
                // nameplate, out of its layout, was never drawn (2026-10-14)
                const bg = glowAlloc(src.readPointer());
                if (!bg || bg.isNull()) return;
                hlRoot.writePointer(bg);
                if (grid) {
                    newGrid(bg, srcTile, src.add(D.gridBL).readS32(), src.add(D.gridBT).readS32(),
                            NULL, NULL, src);
                    for (const f of [D.gridBR, D.gridBB]) bg.add(f).writeS32(src.add(f).readS32());
                    for (const f of [D.gridTB, D.gridTC, D.gridIS]) bg.add(f).writeU8(src.add(f).readU8());
                    bg.add(D.gridBS).writeDouble(src.add(D.gridBS).readDouble());
                    st.clsW = src.add(D.gridW).readDouble();
                    st.clsH = src.add(D.gridH).readDouble();
                    gridW(bg, st.clsW);
                    gridH(bg, st.clsH);
                } else {
                    newBmp(bg, srcTile, src);
                    [st.clsW, st.clsH] = tileWH(src);
                }
                const ic = glowAlloc(lvBmp.readPointer());
                if (ic && !ic.isNull()) {
                    hlRoot.add(8).writePointer(ic);
                    newBmp(ic, tile, bg);
                    st.clsIcon = ic;
                }
                hlRoot.writePointer(NULL);
                hlRoot.add(8).writePointer(NULL);
                st.clsBg = bg;
                st.clsSrc = src;
                if (!classLogged) {
                    classLogged = true;
                    log("class icon: made beside the level (" + cls + ")");
                }
            };
            let classLogged = false;
            let remadeLogged = false;
            const tileWH = function (b) {
                const t = b.add(D.bitmapTile).readPointer();
                return t && !t.isNull() ? [t.add(D.tileW).readDouble(), t.add(D.tileH).readDouble()] : [0, 0];
            };
            const placeClassIcon = function (st, w) {
                const bg = st.clsBg, sq = st.clsSrc, g = st.gauge;
                if (!bg.add(OFF.Object.visible).readU8() && setVisibleFn) setVisibleFn(bg, 1);
                const bw = st.clsW || 0, bh = st.clsH || 0;
                // The level's mirror, by the bar (where the game drew them:
                // absolute places and scales), in the square's own space (it is
                // a child of the level's square): as big as the level's square
                // (times its own size), as far right of the bar's end as the
                // level is left of its start, level with it.
                const sa = sq.add(D.matA).readDouble(), sd = sq.add(D.matD).readDouble();
                const ga = g.add(D.matA).readDouble(), gd = g.add(D.matD).readDouble();
                if (!(sa > 0) || !(sd > 0) || !(ga > 0) || !(gd > 0)) return;     // not drawn yet
                const barW = g.add(OFF.Gauge.barWidth).readS32();
                // its size its own: the level's own size (the scale we give
                // the level) undone, so 100 is a level at 100 %
                const lvS = st.badge && !st.badge.isNull() ? st.badge.add(D.scaleX).readDouble() || 1 : 1;
                const k = allyClassScale / 100 / lvS;
                const sqL = sq.add(D.absX).readDouble(), sqR = sqL + bw * sa;
                const gL = g.add(D.absX).readDouble(), gR = gL + barW * ga;
                const gap = gL - sqR;
                const x = (gR + gap - sqL) / sa + allyClassDx * ga / sa;
                const y = (bh - bh * k) / 2 + allyClassDy * gd / sd;     // centred on the level
                if (Math.abs(bg.add(D.x).readDouble() - x) > 0.05 || Math.abs(bg.add(D.y).readDouble() - y) > 0.05
                        || Math.abs(bg.add(D.scaleX).readDouble() - k) > 0.001) {
                    bg.add(D.x).writeDouble(x);
                    bg.add(D.y).writeDouble(y);
                    bg.add(D.scaleX).writeDouble(k);
                    bg.add(D.scaleY).writeDouble(k);
                    bg.add(D.posChanged).writeU8(1);
                }
                const ic = st.clsIcon;
                if (ic && !ic.isNull() && !st.clsPlaced) {
                    // the icon in the square: 70 % of it, centred
                    const [iw, ih] = tileWH(ic);
                    if (iw > 0 && ih > 0 && bw > 0 && bh > 0) {
                        const f = Math.min(bw * 0.7 / iw, bh * 0.7 / ih);
                        ic.add(D.scaleX).writeDouble(f);
                        ic.add(D.scaleY).writeDouble(f);
                        // (its tile drawn from its own offset: dx, dy)
                        const t = ic.add(D.bitmapTile).readPointer();
                        const tdx = t.add(D.tileDx).readDouble(), tdy = t.add(D.tileDy).readDouble();
                        ic.add(D.x).writeDouble((bw - iw * f) / 2 - tdx * f);
                        ic.add(D.y).writeDouble((bh - ih * f) / 2 - tdy * f);
                        ic.add(D.posChanged).writeU8(1);
                        st.clsPlaced = true;
                    }
                }
            };

            // a text scaled (k: 1 its own), its row laid out anew
            const scaleText = function (t, k) {
                if (Math.abs(t.add(D.scaleX).readDouble() - k) <= 0.001) return;
                t.add(D.scaleX).writeDouble(k);
                t.add(D.scaleY).writeDouble(k);
                t.add(D.posChanged).writeU8(1);
                const up = t.add(OFF.Object.parent).readPointer();
                if (up && !up.isNull() && FLOWS[typeName(up)]) up.add(D.needReflow).writeU8(1);
            };
            // an element moved in its flow (its FlowProperties' offsets: the
            // game's plus ours, the game's taken anew if it sets them again)
            const moveInFlow = function (o, st, key, dx, dy) {
                const up = o.add(OFF.Object.parent).readPointer();
                if (!up || up.isNull() || !FLOWS[typeName(up)]) return;
                const fp = flowPropsOf(o, up);
                if (!fp) return;
                const cx = fp.add(D.fpOffsetX).readS32(), cy = fp.add(D.fpOffsetY).readS32();
                let off = st[key];
                if (!off || cx !== off.x || cy !== off.y) off = st[key] = { bx: cx, by: cy, x: cx, y: cy };
                const tx = off.bx + dx, ty = off.by + dy;
                if (cx !== tx || cy !== ty) {
                    fp.add(D.fpOffsetX).writeS32(tx);
                    fp.add(D.fpOffsetY).writeS32(ty);
                    off.x = tx;
                    off.y = ty;
                    up.add(D.needReflow).writeU8(1);
                }
            };
            const setTextFn = fnOf(FF["ui.comp.FmtText.set_text"], "pointer", ["pointer", "pointer"]);
            let heroes = new Map();
            const HW = OFF.HeroWidget;
            if (heFi != null && HW) {
                Interceptor.attach(base.add(heFi * 8).readPointer(), {
                    onEnter() { this.w = this.context.rcx; },
                    onLeave() {
                        if (plateFailed) return;
                        try {
                            const w = this.w;
                            const key = w.toString();
                            let st = heroes.get(key);
                            if (st && (stale(st) || !w.add(HW.healthBar).readPointer().equals(st.hb))) {
                                heroes.delete(key);
                                st = null;
                            }
                            if (!st) {
                                if (typeName(w) !== "ui.hud.HeroWidget") return;
                                if (heroes.size > 300) heroes = new Map();
                                const hb = w.add(HW.healthBar).readPointer();
                                const gauge = hb && !hb.isNull() ? hb.add(OFF.HealthBar.healthGauge).readPointer() : null;
                                st = barState(gauge, findChild(w, "ui.hud.LevelDisplay", 0), hb);
                                st.hb = hb;
                                st.txt = hb && !hb.isNull() ? hb.add(OFF.HealthBar.txt).readPointer() : null;
                                st.nameCont = w.add(HW.nameCont).readPointer();
                                st.heroName = w.add(HW.heroName).readPointer();
                                st.who = { name: null };
                                st.hero = w.add(HW.hero).readPointer();
                                if (st.txt && !st.txt.isNull()) {
                                    if (barTexts.size > 800) barTexts.clear();
                                    barTexts.set(st.txt.toString(), st.who);
                                    if (st.gauge) barTexts.set(st.gauge.toString(), st.who);
                                }
                                st.sig = sigOf(st);
                                heroes.set(key, st);
                            }
                            if (!st.gauge) return;
                            styleBar(st, allyColor, allyLag, allyBg, allyExtra, allyWidth, allyColorEnd);
                            // its text: shown or not by its own choice (the game's
                            // own visibility given back), written as chosen
                            if (st.txt && !st.txt.isNull() && setVisibleFn) {
                                const vis = st.txt.add(OFF.Object.visible).readU8() !== 0;
                                if (allyText !== "none" && !vis) {
                                    setVisibleFn(st.txt, 1);
                                    if (st.txtHidden) st.txtHidden = false; else st.txtShown = true;
                                } else if (allyText === "none" && vis && st.txtShown) {
                                    setVisibleFn(st.txt, 0);
                                    st.txtShown = false;
                                }
                            }
                            if (st.badge && !st.badge.isNull()) {
                                levelBadge(st, { extra: allyExtra, scale: allyLevelScale, show: allyLevelShow,
                                                 dx: allyLevelDx, dy: allyLevelDy, text: null, tint: null });
                            }
                            // its class's icon (the game's own, by the hero's class:
                            // its unit kind), written as the game writes its icons
                            const like = st.heroName && !st.heroName.isNull()
                                ? st.heroName.add(HW.fmtText).readPointer() : null;
                            const cls = st.hero && !st.hero.isNull() ? petKind(st.hero) : "";
                            const icon = /^[A-Za-z]+$/.test(cls) ? '<img src="/icon/Class_' + cls + '"/>' : null;
                            const lvTxt = st.badge && !st.badge.isNull()
                                ? st.badge.add(D.levelText).readPointer() : null;
                            // (the icon in the level's square, a choice gone: given back)
                            if (lvTxt && !lvTxt.isNull() && (st.lvWas !== undefined || st.lvOff)) {
                                backOwnText(st, "lvWas", lvTxt, like);
                                scaleText(lvTxt, 1);
                                moveInFlow(lvTxt, st, "lvOff", 0, 0);
                            }
                            // beside: pictures of ours, made once, under the level's
                            // square (its own picture, so they follow it): a copy of
                            // the square (its rounded dark ground), the class's icon
                            // centred on it
                            const lvBmp = st.badge && !st.badge.isNull()
                                ? findChild(st.badge, "h2d.Bitmap", 3) : null;
                            if (allyClass === "beside" && icon && lvBmp) {
                                // the square shown now: ours made again if it is not
                                // under it any more (the game may build the level anew,
                                // ours then left on a square no more shown)
                                if (st.clsBg) {
                                    const now = levelSquare(st.badge, lvBmp);
                                    const under = st.clsBg.add(OFF.Object.parent).readPointer();
                                    if (now && (!under.equals(now) || !now.equals(st.clsSrc))) {
                                        if (!remadeLogged) {
                                            remadeLogged = true;
                                            log("class icon: the level's square was made anew, the icon made again on it");
                                        }
                                        st.clsBg = st.clsIcon = st.clsSrc = null;
                                        st.clsTried = st.clsPlaced = false;
                                    }
                                }
                                if (!st.clsBg && !st.clsTried) {
                                    st.clsTried = true;
                                    makeClassIcon(st, lvBmp, cls, like, w);
                                }
                                if (st.clsBg) placeClassIcon(st, w);
                            } else if (st.clsBg && st.clsBg.add(OFF.Object.visible).readU8() && setVisibleFn) {
                                setVisibleFn(st.clsBg, 0);
                            }
                        } catch (err) {
                            plateFailed = true;
                            log("ally options failed: " + err);
                        }
                    }
                });
                log("ally options ready (players' nameplates followed)");
            } else {
                log("players' nameplate data missing (HeroWidget update): no ally options");
            }

            Interceptor.attach(base.add(fuFi * 8).readPointer(), {
                onEnter() { this.w = this.context.rcx; },
                onLeave() {
                    if (plateFailed) return;
                    try {
                        const w = this.w;
                        if (typeName(w) !== "ui.hud.FoeWidget") return;
                        const key = w.toString();
                        let st = plates.get(key);
                        if (st && stale(st)) { plates.delete(key); st = null; }
                        if (!st) {
                            if (plates.size > 400) plates = new Map();
                            const hb = findChild(w, "ui.comp.HealthBar", 0);
                            const gauge = hb ? hb.add(OFF.HealthBar.healthGauge).readPointer() : null;
                            if (hb) {
                                if (barTexts.size > 800) barTexts.clear();
                                barTexts.set(hb.add(OFF.HealthBar.txt).readPointer().toString(), "foe");
                                if (gauge && !gauge.isNull()) barTexts.set(gauge.toString(), "foe");
                            }
                            st = { gauge: gauge && !gauge.isNull() ? gauge : null, fill: null,
                                   inset0: null, w0: 0, h0: 0, set: null, scaled: false,
                                   glowSet: null, outline: null, own: null, bgOwn: null,
                                   badge: findChild(w, "ui.hud.LevelDisplay", 0), lag: null,
                                   txts: barTextsOf(hb, gauge && !gauge.isNull() ? gauge : null),
                                   shield: shieldOf(hb) };
                            if (st.gauge) {
                                st.fill = st.gauge.add(OFF.Gauge.fillBar).readPointer();
                                st.lag = st.gauge.add(OFF.Gauge.lagBar).readPointer();
                                st.inset0 = st.gauge.add(OFF.Gauge.inset).readS32();
                                st.w0 = st.gauge.add(OFF.Gauge.barWidth).readS32();
                                st.h0 = st.gauge.add(OFF.Gauge.barHeight).readS32();
                            }
                            st.sig = sigOf(st);
                            plates.set(key, st);
                        }
                        const ind = w.add(OFF.FoeWidget.targetIndicator).readPointer();
                        let targeted = false;
                        if (ind && !ind.isNull()) {
                            targeted = ind.add(OFF.Object.visible).readU8() !== 0;
                            // its size
                            if (targetScale !== 100 || st.scaled) {
                                const s = targetScale / 100;
                                const tile = ind.add(D.bitmapTile).readPointer();
                                const th = tile && !tile.isNull() ? tile.add(D.tileH).readDouble() : 0;
                                ind.add(D.scaleX).writeDouble(s);
                                ind.add(D.scaleY).writeDouble(s);
                                // the game set its y this frame for its own size
                                ind.add(D.y).writeDouble(ind.add(D.y).readDouble() - th * (s - 1));
                                ind.add(D.posChanged).writeU8(1);
                                st.scaled = targetScale !== 100;
                            }
                            // its halo
                            const glowKey = targetGlow === null ? null
                                : targetGlow + ":" + glowBlur + ":" + targetGlowA;
                            if (setFilter && st.glowSet !== glowKey) {
                                if (targetGlow === null) {
                                    setFilter(ind, NULL);
                                } else {
                                    const g = makeGlow(targetGlow, glowBlur, 2, targetGlowA / 100);
                                    if (g) setFilter(ind, g);
                                }
                                st.glowSet = glowKey;
                            }
                        }
                        if (!st.gauge) return;
                        // the fill's colour, by the foe's kind
                        const any = t => t.mine !== null || t.aggro !== null || t.neutral !== null;
                        let c = null, lc = null, cEnd = null;
                        if (any(barColors) || any(lagColors)) {
                            const foe = w.add(OFF.FoeWidget.gameObject).readPointer();
                            const hero = localHero;
                            if (foe && !foe.isNull()) {
                                const mine = hero && !hero.isNull()
                                    && foe.add(OFF.FoeTarget.target).readS64()
                                        .equals(hero.add(OFF.FoeTarget.heroUid).readS64());
                                const neutral = !!NEUTRAL[petKind(foe)];
                                const pick = t => mine && t.mine !== null ? t.mine
                                    : neutral ? t.neutral : t.aggro;
                                c = pick(barColors);
                                lc = pick(lagColors);
                                cEnd = c === null ? null
                                    : mine && barColors.mine !== null ? barColorsEnd.mine
                                    : neutral ? barColorsEnd.neutral : barColorsEnd.aggro;
                            }
                        }
                        styleBar(st, c, lc, barBg, frameExtra, foeWidth, cEnd);
                        textStyle(st.txts, foeTextScale);
                        if (st.badge && !st.badge.isNull()) {
                            levelBadge(st, { extra: frameExtra, scale: levelScale, show: levelShow,
                                             dx: levelDx, dy: levelDy, text: levelText, tint: levelTint });
                        }
                        // the targeted foe's bar outlined
                        const wantLine = targeted && targetLine !== null;
                        if (wantLine || st.outline) {
                            const sh = outlineOf(st.gauge);
                            if (sh) {
                                if (wantLine) {
                                    if (!st.outline) st.outline = readOutline(sh);
                                    const lc = targetLine, t = targetLineWidth;
                                    if (st.outline) {
                                        const rgba = [(lc >> 16 & 255) / 255, (lc >> 8 & 255) / 255,
                                                      (lc & 255) / 255, targetLineA / 100];
                                        // blurred: a glow of its colour round it (the
                                        // background's shadow, outside, not moved)
                                        const o = st.outline;
                                        writeOutline(sh, { use: 1, sep: 0, off: -t, col: rgba,
                                            th: [t, t, t, t],
                                            shUse: lineBlur > 0 ? 1 : o.shUse,
                                            shIn: lineBlur > 0 ? 0 : o.shIn,
                                            shBlur: lineBlur > 0 ? lineBlur : o.shBlur,
                                            shCol: lineBlur > 0 ? rgba : o.shCol,
                                            shOff: lineBlur > 0 ? [0, 0] : o.shOff,
                                            shSpr: lineBlur > 0 ? [t, t] : o.shSpr });
                                    }
                                } else {
                                    writeOutline(sh, st.outline);
                                    st.outline = null;
                                }
                            } else {
                                st.outline = null;
                            }
                        }
                    } catch (err) {
                        plateFailed = true;
                        log("targeting options failed: " + err);
                    }
                }
            });
            log("targeting options ready (nameplates followed)");
        } catch (e) {
            log("targeting options unavailable: " + e);
        }
    } else {
        log("nameplate data missing (FoeWidget update): no targeting options");
    }

    // ---- the Farever Book button in the game's menu (Échap) ----
    // Made as the game makes its own when the menu opens (EscapeMenu.init,
    // onLeave): a Button of Quit's type, its label a String of ours, its
    // click a closure of Quit's click's type calling back here, put after
    // Options. Allocated by libhl's own exports, on the game thread; any
    // doubt (a type, a pointer) and nothing is made.
    const MF = DATA.menu_fns || {};
    const escFi = MF["ui.win.EscapeMenu.init"], btnFi = MF["ui.comp.$Button.__constructor__"];
    const clickFi = MF["ui.UIElement.set_onClick"], atFi = MF["h2d.Object.addChildAt"];
    const idxFi = MF["h2d.Object.getChildIndex"];
    if (escFi != null && btnFi != null && clickFi != null && atFi != null && idxFi != null
            && OFF.EscapeMenu && OFF.UIElement && OFF.Object && OFF.String.length != null) {
        try {
            const hl = Process.findModuleByName("libhl.dll");
            const allocObj = new NativeFunction(hl.findExportByName("hl_alloc_obj"), "pointer", ["pointer"]);
            const allocBytes = new NativeFunction(hl.findExportByName("hl_alloc_bytes"), "pointer", ["int"]);
            const allocClosure = new NativeFunction(hl.findExportByName("hl_alloc_closure_void"),
                                                    "pointer", ["pointer", "pointer"]);
            const fn = (fi, ret, args) => new NativeFunction(base.add(fi * 8).readPointer(), ret, args);
            const newButton = fn(btnFi, "void", ["pointer", "pointer", "pointer", "pointer", "pointer"]);
            const setOnClick = fn(clickFi, "pointer", ["pointer", "pointer"]);
            const onClick = new NativeCallback(function () {
                try { send({ kind: "open_panel" }); } catch (e) {}
            }, "void", []);
            let menuFailed = false;
            const hlText = function (text, like) {
                // a String of the game's type (borrowed from one it made)
                const s = allocObj(like.readPointer());
                const b = allocBytes((text.length + 1) * 2);
                b.writeUtf16String(text);
                s.add(OFF.String.bytes).writePointer(b);
                s.add(OFF.String.length).writeS32(text.length);
                return s;
            };
            Interceptor.attach(base.add(escFi * 8).readPointer(), {
                onEnter() { this.menu = this.context.rcx; },
                onLeave() {
                    if (menuFailed || !escButtonWanted) return;
                    try {
                        const menu = this.menu;
                        if (typeName(menu) !== "ui.win.EscapeMenu") return;
                        const quit = menu.add(OFF.EscapeMenu.quitButton).readPointer();
                        const opt = menu.add(OFF.EscapeMenu.optionsBtn).readPointer();
                        if (typeName(quit) !== "ui.comp.Button" || typeName(opt) !== "ui.comp.Button") return;
                        const parent = opt.add(OFF.Object.parent).readPointer();
                        const quitClick = quit.add(OFF.UIElement.onClick).readPointer();
                        const name = localHero && !localHero.isNull()
                            ? localHero.add(OFF.Hero.name).readPointer() : null;
                        if (!parent || parent.isNull() || !quitClick || quitClick.isNull()
                                || !name || name.isNull()) return;
                        const btn = allocObj(quit.readPointer());
                        newButton(btn, hlText("Farever Book", name), NULL, NULL, parent);
                        setOnClick(btn, allocClosure(quitClick.readPointer(), onClick));
                        // left where the constructor put it, last in the
                        // menu: moving it with h2d.Object.addChildAt passed
                        // over the menu's own layout (an h2d.Flow, its own
                        // addChildAt) and blacked the screen (2026-10-14)
                    } catch (e) {
                        menuFailed = true;
                        log("menu button failed: " + e);
                    }
                }
            });
        } catch (e) {
            log("menu button unavailable: " + e);
        }
    } else {
        log("menu data missing (EscapeMenu / Button): no Farever Book button in the game's menu");
    }

    // ---- the character picked ----
    // HeroData is null on the client: a character's id in the game's
    // database is only seen on the character select screen, each slot a
    // virtual {header: {name, ...}, heroID: i64}. The last one picked is the
    // one that enters the world (refreshLocalHero matches it by name).
    const selFi = (DATA.select_fns || {})["ui.win.CharacterSelectScreen.setCurrentCharacter"];
    if (selFi != null) {
        Interceptor.attach(base.add(selFi * 8).readPointer(), {
            onEnter() {
                try {
                    const v = this.context.rdx;
                    if (!v || v.isNull()) return;
                    const id = virtualI64(v, "heroID");
                    const name = hlStr(virtualPtr(virtualPtr(v, "header"), "name"));
                    if (id && id !== "0" && name) {
                        if (!pickedHero || pickedHero.id !== id) {
                            log("character picked: database id read");
                        }
                        pickedHero = { id: id, name: name };
                    }
                } catch (e) {}
            }
        });
    } else {
        log("select data missing (CharacterSelectScreen.setCurrentCharacter): "
            + "two characters of the same name are told apart by name only");
    }

    // ---- healing ----
    // A client never sees heal amounts: measured 2026-08-03, only
    // ent.Unit.playHitHealFX fires client-side and its HitData.amount reads 0.
    // So healing comes from what is replicated:
    //   * playHitHealFX(target=rcx, hitData=rdx): the heal event (skill, healer)
    //   * UnitAttributes.set_health(attrs=rcx, v): a health rise = what landed
    // Every FX is emitted once, `landed` = its rise or 0 (a heal on a full
    // target still counts); the host estimates amount and overheal.
    // FX-less rises in combat are regen; out-of-combat rises and spawns (old
    // health 0) are dropped.
    function skillOwnerIdent(bs) {
        const owner = bs.add(OFF.BaseSkill.owner).readPointer();
        if (typeName(owner) === "ent.Hero") return heroIdent(owner);
        const pl = bs.add(OFF.BaseSkill.ownerPlayer).readPointer();
        if (typeName(pl) === "st.Player") {
            const nm = hlStr(pl.add(OFF.Player.name).readPointer());
            if (nm) {
                const is_me = pl.add(OFF.Player.isMe).readU8() ? 1 : 0;
                return { player: nm, is_me: is_me,
                         in_party: (is_me || partyNames[nm] === 1) ? 1 : 0 };
            }
        }
        return null;
    }

    const fxFi = DATA.combat_targets["ent.Unit.playHitHealFX"];
    // the heal's own number, shown over the target (measured in the code:
    // the server's rpcDisplayHeal carries the heal's DamageResult, its
    // _amount the heal's size, overheal included)
    const dhFi = DATA.combat_targets["ent.Unit.rpcDisplayHeal__impl"];
    const shFi = DATA.combat_targets["ent.UnitAttributes.set_health"];
    const UA = OFF.UnitAttributes, HD = OFF.HitData;
    if (fxFi == null || shFi == null || !UA || !HD || OFF.BaseSkill.owner == null) {
        log("heal data missing (playHitHealFX/set_health findex or offsets); "
            + "healing capture disabled — re-run hltools/build_targets.py "
            + "and hltools/emit_offsets.py");
    } else {
        // target unit ptr -> queue of heal FX awaiting a health rise (a queue:
        // two healers can hit the same target within the window)
        const pendingHealFx = {};
        const HEAL_MATCH_MS = 1500;   // how long an FX waits for its rise
        const HEAL_QUEUE_MAX = 32;    // a HoT storm must not grow without bound

        // `est`: the host may estimate the size (FX heals, where `landed` may
        // be capped). Regen is not estimable: it is only ever the rise itself.
        function emitHeal(fx, landed, estimable) {
            const ev = { kind: "heal", skill: fx.skill, name: fx.name,
                         amount: landed, landed: landed,
                         est: estimable ? 1 : 0, self: fx.slf ? 1 : 0,
                         to: fx.to || "",
                         step: fx.step, dyn: fx.dyn, atb: fx.atb };
            if (fx.size != null) ev.size = fx.size;
            if (fx.crit) ev.crit = 1;
            send(Object.assign(ev, fx.who));
        }

        // The heal formula's inputs (replicated dynVal1-3, caster attributes);
        // the host computes against analysis_out/heal_specs.json.
        function healInputs(bs, hitData, fx) {
            try {
                const B2 = OFF.BaseSkill;
                if (B2.dynVal1 != null) {
                    fx.dyn = [bs.add(B2.dynVal1).readDouble(),
                              bs.add(B2.dynVal2).readDouble(),
                              bs.add(B2.dynVal3).readDouble()];
                }
                // the spec is per step (Sword_Swarm_Combo heals from several)
                if (HD.step != null && OFF.SkillStep) {
                    const st = hitData.add(HD.step).readPointer();
                    if (st && !st.isNull())
                        fx.step = st.add(OFF.SkillStep.index).readS32();
                }
                // for a totem/summon these are its stats, not the summoner's
                // (accepted: the landed-heal fallback covers them)
                const owner = bs.add(OFF.BaseSkill.owner).readPointer();
                if (owner && !owner.isNull() && OFF.Unit.attr != null) {
                    const at = owner.add(OFF.Unit.attr).readPointer();
                    if (at && !at.isNull() && at.compare(ptr("0x10000")) > 0) {
                        fx.atb = {
                            Faith: at.add(UA.faith).readDouble(),
                            Intellect: at.add(UA.intellect).readDouble(),
                            Strength: at.add(UA.strength).readDouble(),
                            Dexterity: at.add(UA.dexterity).readDouble(),
                        };
                    }
                }
            } catch (e) {}
        }

        // Self-heal: owner pointer is the target, or (totem/summon) the
        // healer's name is the target's.
        function isSelfHeal(bs, target, who) {
            try {
                const owner = bs.add(OFF.BaseSkill.owner).readPointer();
                if (owner && !owner.isNull() && owner.equals(target)) return 1;
                const tn = hlStr(target.add(OFF.Hero.name).readPointer());
                return (tn && who.player === tn) ? 1 : 0;
            } catch (e) { return 0; }
        }

        // The heals' shown numbers (rpcDisplayHeal), per target, waiting for
        // their FX; and the heals matched to their rise, held a moment for
        // their number (the RPC and the FX come in either order).
        const shownHeals = {};
        const readyHeals = [];
        const SHOWN_MS = 2000, READY_MS = 200;

        function takeShown(key, skill) {
            const q = shownHeals[key];
            if (!q) return null;
            const now = Date.now();
            for (let i = 0; i < q.length; i++) {
                if (now - q[i].t > SHOWN_MS) continue;
                if (skill == null || q[i].skill === skill) return q.splice(i, 1)[0];
            }
            return null;
        }

        function claimShown(fx) {
            if (fx.size != null) return;
            const sh = takeShown(fx.key, fx.skill);
            if (sh) { fx.size = sh.amount; fx.crit = sh.crit; }
        }

        function finish(fx, landed) {
            claimShown(fx);
            readyHeals.push({ fx: fx, landed: landed, at: Date.now() });
        }

        function flushExpired(q, now) {
            // no rise within the window: landed 0
            while (q.length && now - q[0].t > HEAL_MATCH_MS) finish(q.shift(), 0);
        }

        every(function () {
            const now = Date.now();
            for (const k in pendingHealFx) {
                flushExpired(pendingHealFx[k], now);
                if (!pendingHealFx[k].length) delete pendingHealFx[k];
            }
            while (readyHeals.length && now - readyHeals[0].at > READY_MS) {
                const r = readyHeals.shift();
                claimShown(r.fx);
                emitHeal(r.fx, r.landed, true);
            }
            // a number no FX claimed: a heal of its own (on a full target)
            for (const k in shownHeals) {
                const q = shownHeals[k];
                while (q.length && now - q[0].t > SHOWN_MS) {
                    const sh = q.shift();
                    emitHeal({ who: sh.who, skill: sh.skill, name: sh.name,
                               slf: sh.slf, to: sh.to, size: sh.amount,
                               crit: sh.crit }, 0, true);
                }
                if (!q.length) delete shownHeals[k];
            }
        }, 100);

        if (dhFi != null) {
            Interceptor.attach(base.add(dhFi * 8).readPointer(), {
                onEnter() {
                    try {
                        const dr = this.context.rdx;
                        const target = hitTarget(dr) || this.context.rcx;
                        if (typeName(target) !== "ent.Hero") return;
                        const r = readResult(dr);
                        if (!(r.amount > 0)) return;
                        const bs = dr.add(DR.baseSkill).readPointer();
                        if (!bs || bs.isNull()) return;
                        const who = skillOwnerIdent(bs);
                        if (!who) return;
                        const key = target.toString();
                        // its FX first: waiting for its rise, or held
                        const q = pendingHealFx[key] || [];
                        for (let i = 0; i < q.length; i++) {
                            if (q[i].skill === r.skill && q[i].size == null) {
                                q[i].size = r.amount; q[i].crit = r.crit;
                                return;
                            }
                        }
                        for (let i = 0; i < readyHeals.length; i++) {
                            const f = readyHeals[i].fx;
                            if (f.key === key && f.skill === r.skill && f.size == null) {
                                f.size = r.amount; f.crit = r.crit;
                                return;
                            }
                        }
                        (shownHeals[key] || (shownHeals[key] = [])).push({
                            t: Date.now(), skill: r.skill, name: r.name,
                            amount: r.amount, crit: r.crit, who: who,
                            slf: isSelfHeal(bs, target, who),
                            to: heroIdent(target).player });
                    } catch (e) {}
                }
            });
        } else {
            log("rpcDisplayHeal missing from the targets: heal sizes stay "
                + "estimated (re-run hltools/build_targets.py)");
        }

        Interceptor.attach(base.add(fxFi * 8).readPointer(), {
            onEnter() {
                try {
                    const c = this.context;
                    const target = c.rcx;
                    if (typeName(target) !== "ent.Hero") return;
                    if (typeName(c.rdx) !== "st.skill.HitData") return;
                    const bs = c.rdx.add(HD.baseSkill).readPointer();
                    if (!bs || bs.isNull()) return;
                    const who = skillOwnerIdent(bs);
                    if (!who) return;
                    const skill = hlStr(bs.add(BS.kind).readPointer()) || "?";
                    const key = target.toString();
                    const q = pendingHealFx[key] || (pendingHealFx[key] = []);
                    if (q.length >= HEAL_QUEUE_MAX) emitHeal(q.shift(), 0, true);
                    const fx = { t: Date.now(), who: who, skill: skill, key: key,
                                 slf: isSelfHeal(bs, target, who),
                                 to: heroIdent(target).player,
                                 name: skill !== "?" ? skillDisplayName(bs, skill) : "" };
                    // read now: by flush time the skill may be re-cast or freed
                    healInputs(bs, c.rdx, fx);
                    q.push(fx);
                } catch (e) {}
            }
        });

        Interceptor.attach(base.add(shFi * 8).readPointer(), {
            onEnter() {
                this.attrs = this.context.rcx;
                try { this.oldHp = this.attrs.add(UA.health).readDouble(); }
                catch (e) { this.oldHp = null; }
            },
            onLeave() {
                try {
                    // old <= 0 => spawn/initial replication, not a heal
                    if (this.oldHp === null || !(this.oldHp > 0)) return;
                    const nv = this.attrs.add(UA.health).readDouble();
                    const delta = nv - this.oldHp;
                    if (!(delta > 0)) return;
                    const unit = this.attrs.add(UA.unit).readPointer();
                    if (typeName(unit) !== "ent.Hero") return;
                    const key = unit.toString();
                    const q = pendingHealFx[key];
                    let fx = null;
                    if (q) {
                        // expired FX can't own this rise; take the oldest survivor
                        flushExpired(q, Date.now());
                        if (q.length) fx = q.shift();
                        if (!q.length) delete pendingHealFx[key];
                    }
                    if (fx) { finish(fx, delta); return; }
                    // FX-less rise with a heal's number shown: that heal
                    const sh = takeShown(key, null);
                    if (sh) {
                        finish({ who: sh.who, skill: sh.skill, name: sh.name,
                                 slf: sh.slf, to: sh.to, size: sh.amount,
                                 crit: sh.crit, key: key }, delta);
                        return;
                    }
                    // FX-less rise: regen, counted in combat only, as a self-heal
                    if (!unit.add(OFF.Hero.isInCombat).readU8()) return;
                    const me = heroIdent(unit);
                    emitHeal({ who: me, skill: "Regen", name: "Regen", slf: 1,
                               to: me.player }, delta, false);
                } catch (e) {}
            }
        });
    }
    log("meter hook active (local hero " + (localName ? "identified" : "pending")
        + ")");
    send({ kind: "ready", ok: true });
}
// Deferred so script.load() returns at once: scanning inside load() blocks the
// injection handshake and can stall the game.
setTimeout(main, 150);

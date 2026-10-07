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
        let gaveUp = false;
        for (let depth = 0; depth <= 2 && frontier.length && !gaveUp; depth++) {
            const next = [];
            for (const p of frontier) {
                const k = p.toString();
                if (seen.has(k)) continue;
                seen.add(k);
                if (seen.size > MAX_NODES || Date.now() - t0 > BUDGET_MS) { gaveUp = true; break; }
                if (isTableAt(p, resolved)) return p;
                if (depth === 2) continue;
                for (let i = 0; i < EXPAND; i++) {
                    let v; try { v = p.add(i * 8).readPointer(); } catch (e) { break; }
                    if (v.and(7).toInt32() === 0 && inHeap(v)) next.push(v);
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
function readContainerKinds(invPtr, into, byKey) {
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
    }
    return true;
}

// ---- the stock: what the hero owns, by item kind ----
// Bag + equipment + every bank tab, for the goals. Sent on change; `banks` is
// the number of tabs read (-1: no offset); the bank may only be known once
// opened.
let stockSig = null;

function sendStock(loadout, now, info) {
    try {
        const all = {}, binfo = {};
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
                if (!readContainerKinds(tabs[i], got, binfo)) continue;
                banks++;
                for (const key in got) {
                    const k = binfo[key] && binfo[key].kind;
                    if (k) all[k] = (all[k] || 0) + got[key];
                }
            }
        }
        const sig = banks + "|" + Object.keys(all).sort()
            .map(function (k) { return k + ":" + all[k]; }).join(",");
        if (sig === stockSig) return;
        stockSig = sig;
        send({ kind: "stock", items: all, banks: banks });
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
        const now = {}, info = {};
        const okInv = readContainerKinds(
            loadout.add(OFF.Loadout.inventory).readPointer(), now, info);
        const okEq = readContainerKinds(
            loadout.add(OFF.Loadout.equipment).readPointer(), now, info);
        if (!okInv || !okEq) return;
        sendStock(loadout, now, info);
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
    } catch (e) { r.equip = []; }
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
                         luckStatuses: statusesOf(localHero, ["Luck_"]) };
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
            }) });
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
        return hl_getField(obj, fieldHash[name]);
    } catch (e) { return null; }
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
                send({ kind: "hero", name: localName });
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
    if (!setupCodexApi(base)) log("!! map natives missing; no kill counts");
    every(function () { codexDue = true; }, 8000);
    every(function () { rosterDue = true; }, 5000);
    every(function () { selfDue = true; }, 5000);
    listenAnalyze();

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

    Interceptor.attach(daddr, {
        onEnter() {
            try {
                const dealer = this.context.rcx;
                // players and their summons only (not mobs or a mob's pet)
                let attributeTo = dealer, pet = "";
                if (typeName(dealer) !== "ent.Hero") {
                    const owner = summonOwnerOf(dealer);
                    if (!owner) return;
                    attributeTo = owner;
                    pet = petKind(dealer);
                }
                const dr = this.context.rdx;
                const r = readResult(dr);
                if (!(r.amount > 0)) return;
                if (pet) r.pet = pet;
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
            send(Object.assign({ kind: "heal", skill: fx.skill, name: fx.name,
                                 amount: landed, landed: landed,
                                 est: estimable ? 1 : 0, self: fx.slf ? 1 : 0,
                                 step: fx.step, dyn: fx.dyn, atb: fx.atb },
                               fx.who));
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

        function flushExpired(q, now) {
            // no rise within the window: emit with landed 0
            while (q.length && now - q[0].t > HEAL_MATCH_MS) emitHeal(q.shift(), 0, true);
        }

        every(function () {
            const now = Date.now();
            for (const k in pendingHealFx) {
                flushExpired(pendingHealFx[k], now);
                if (!pendingHealFx[k].length) delete pendingHealFx[k];
            }
        }, 250);

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
                    const fx = { t: Date.now(), who: who, skill: skill,
                                 slf: isSelfHeal(bs, target, who),
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
                    if (fx) { emitHeal(fx, delta, true); return; }
                    // FX-less rise: regen, counted in combat only, as a self-heal
                    if (!unit.add(OFF.Hero.isInCombat).readU8()) return;
                    emitHeal({ who: heroIdent(unit), skill: "Regen",
                               name: "Regen", slf: 1 }, delta, false);
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

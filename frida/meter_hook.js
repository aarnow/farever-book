// meter_hook.js — persistent hook feeding the Farever France party meter.
// Resolves the HL functions_ptrs table, identifies the local hero via
// ui.Console.getMyHero(), hooks ent.Unit.onInflictDamage, and streams EVERY
// player's (ent.Hero dealer) damage instance to Python as {kind:'hit', ...},
// tagged with the dealer's name and whether it's the local player.
//
// DATA (resolver_data.json) and OFF (meter_offsets.json) are prepended by the
// Python host.

function log(m) { send({ kind: "log", msg: String(m) }); }

// ---- leaving the game cleanly ----
// Unloading the agent while a game thread is inside one of our hooks crashes
// the game (measured 2026-09-28, three crash dumps: an execute violation at
// an address that was the agent's, the agent already gone). The health hook's
// onLeave is the sharp edge: Frida swaps that function's return address for
// its own trampoline, so a thread still inside it returns into freed code.
// So the host first calls shutdown(): every hook comes off and every timer
// stops, the host waits for the game's threads to leave the trampolines, and
// only then unloads.
// Every repeating timer goes through every(), so shutdown() can stop them
// all (setInterval itself is read-only in Frida's runtime).
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
    // The real table holds EVERY anchor's live address at findex*8, so three
    // exact pointer matches is conclusive; any mismatch rejects immediately.
    let n = 0;
    for (const o of resolved) {
        let v; try { v = base.add(o.findex * 8).readPointer(); } catch (e) { return false; }
        if (!v.equals(o.addr)) return false;
        if (++n >= 3) return true;
    }
    return n > 0;
}

// Fast path: the loaded module (hl_module) holding functions_ptrs is reached
// from the statics of the host exe or libhl.dll in a couple of hops, so a
// pointer walk seeded from their writable sections finds the table with no
// heap scanning:
//     static -> ... -> hl_module -> functions_ptrs
// No struct layout is assumed: every private-rw pointer reachable within two
// hops is simply *tested* against the anchors via isTableAt.
//
// Each module is walked on its own, the exe first, with its own node cap.
// Measured 2026-10-02 (after the 10-01 maintenance): the table sits at
// Farever.exe+178888 -> +16 -> +32, found after 92 nodes from the exe's 12
// seeds — but walking both modules' seeds together, libhl's 1,571 seeds
// reached 31,868 nodes by the second hop, past the old shared cap of 30,000,
// and the walk gave up just short of it: a 70 s memory scan every launch.
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
    // Seeds: every 8-aligned qword in a module's statics that points into
    // private rw- memory.
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
    // functions_ptrs is a libhl heap allocation → anonymous rw- memory. Scan
    // anonymous ranges FIRST, smallest first (we return as soon as the table is
    // found, so the common case never touches the big heap segments or the
    // file-backed image/asset ranges). The size cap is a sanity bound only —
    // HL heap segments can exceed 128 MiB on some machines, and a cap below the
    // segment holding the table makes it unfindable, so keep this generous.
    const ranges = Process.enumerateRanges("rw-")
        .filter(r => r.size < 0x40000000)
        .sort((a, b) => ((a.file ? 1 : 0) - (b.file ? 1 : 0)) || (a.size - b.size));
    // On a matching build the table is found on the first seed; extra seeds
    // only ever run when the shipped data doesn't match this build, so cap low
    // to fail fast (the meter then auto-regenerates the data).
    const seeds = Math.min(resolved.length, 3);
    const total = ranges.length * seeds;
    let done = 0, lastProg = Date.now();
    for (let s = 0; s < seeds; s++) {
        const seed = resolved[s], pat = ptrPattern(seed.addr);
        for (const r of ranges) {
            // Heartbeat so the host can tell "scan in progress" from "hook
            // dead" and keep waiting instead of killing a live scan.
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
// Cached by type pointer. HL type descriptors are static for the life of the
// process, so this is safe — and it's what makes the world sweep affordable:
// a zone holds hundreds of entities but only a couple of dozen distinct
// classes, so after warmup naming one is a map hit instead of four memory
// reads and a UTF-16 decode, several hundred times a tick.
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
let getHeroFns = [];      // [{name, addr}]
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

// RETIRED: the zone signature used to come from Main.getMapId(), called from
// the game thread. Measured 2026-08-01: whatever that findex resolves to now
// returns the MACHINE HOSTNAME ('CAM-PC' — the user's PC name), which never
// changes — so the zone-change reset had gone silently dead. The zone signal
// now comes from layer.world.level in checkRift() below: the loaded level's
// own name, read with plain pointer walks (timer-safe, unlike an HL call).

// ---- rift + zone + shard detection ----
// hero -> st.State.layer -> st.GameLayer: isRift for rifts, .world.level for
// where you are, .serverName for WHICH SHARD you are on. Pure pointer + byte
// reads, no HL calls, so it's safe from the heartbeat timer rather than having
// to ride along inside a game-thread hook — which matters, because you can
// enter a rift (or start the meter mid-session) long before you hit anything.
// Reported on change only.
let lastRift = null;
let lastLevel = null;
let lastServer = null;
// ---- dungeons ----
// hero -> layer -> mainActivity: an st.activity.Dungeon while in one, whose
// globalCtx is the DungeonContext (state Explo / BossStart / BossPhase /
// BossWin / BossLoose, start and end times, death count). The difficulty is on
// the group's instance lobby. All plain reads, timer-safe. Sent whenever any of
// it changes, and every few seconds while in a dungeon so the clock is seen.
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
            out.step = hlStr(ctx.add(C.step).readPointer());
            out.changed = ctx.add(C.lastStateChanged).readDouble();
            if (out.type === "st.activity.DungeonContext") {
                out.start = ctx.add(C.startActivity).readDouble();
                out.end = ctx.add(C.endActivity).readDouble();
                out.deaths = ctx.add(C.nbPlayerDeaths).readS32();
            }
        }
    } catch (e) { out.err = String(e); }
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
            d.lobbyId = hlStr(player.add(OFF.Player.lobbyId).readPointer());
            const group = player.add(OFF.Player.group).readPointer();
            d.group = !!(group && !group.isNull());
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
            const g = act.add(OFF.Activity.globalCtx).readPointer();
            if (g && !g.isNull()) d.globalCtx = dungeonCtxInfo(g);
            if (OFF.Activity.contexts != null)
                d.actCtx = arrayObjItems(
                    act.add(OFF.Activity.contexts).readPointer(), 8)
                    .map(dungeonCtxInfo);
        }
        d.now = serverNowOf(layer);
        const sig = JSON.stringify(Object.assign({}, d, { now: 0 }));
        dungeonBeat++;
        const inDungeon = d.type === "st.activity.Dungeon";
        if (sig !== dungeonSig || (inDungeon && dungeonBeat % 12 === 0)) {
            dungeonSig = sig;
            send({ kind: "dungeon", d: d });
        }
    } catch (e) {
        const sig = "err:" + e;
        if (sig !== dungeonSig) {
            dungeonSig = sig;
            send({ kind: "dungeon", d: { err: String(e) } });
        }
    }
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
        // Zone identity AND the zone-change signal: layer.world.level names
        // the loaded level. This replaced Main.getMapId(), which turned out
        // to return the machine hostname. Everything here is plain pointer /
        // string-bytes reads, so it stays timer-safe.
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
                    // What the neighbouring fields actually hold, reported so
                    // their meaning gets measured from normal play — names
                    // lie in this game until they've been read live.
                    try {
                        out.name = hlStr(w.add(OFF.World.name).readPointer());
                        out.branch = hlStr(
                            w.add(OFF.World.branchName).readPointer());
                        out.world_map = w.add(OFF.World._isWorldMap).readU8();
                    } catch (e2) {}
                    if (!initial) {
                        resetBossBars();
                        // Whatever was picked up just before the loading
                        // screen, then a new baseline: the loadout is
                        // re-replicated and must not read as loot.
                        sweepInventory();
                        invReady = false;
                    }
                    send(out);
                }
            }
        }
        // Which shard. Deliberately NOT folded into the zone message above:
        // the two move independently — a relog can drop you on a different
        // shard in the same zone (no zone message), and walking into a dungeon
        // changes the zone while the shard string may not follow. Sending it
        // separately means neither can mask the other going stale.
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
// HL calls (getHero) must run on the game thread: calling one from a timer
// kills the game with "Can't lock GC in unregistered thread". Nothing hands us a
// per-frame game-thread callback, so client.BaseCamera.postUpdate is hooked for
// exactly that — it runs every frame — and timers only raise flags it consumes.
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
// ui.hud.BossesInfo.bossInfos is the game's own list of on-screen boss bars.
// Measured against a live King Ratsar pull and an elite:
//
//   * fetchBosses runs at a steady 2/s — a timer, not a per-frame call — so
//     this hook body can afford to walk the array rather than defer it.
//   * the array is not a fixed pool: it is empty with no bar and holds one
//     entry with a bar up, so its length alone answers "is a bar on screen".
//   * a bar comes up for ELITES too (a plain ent.Foe raised one), which is why
//     boss-only rules go through isBoss rather than through the bar.
//   * the bar tracks ENGAGEMENT, not existence — it dropped with the boss
//     alive at 22824 HP when the player walked off and the boss reset. That is
//     exactly the pull-start/pull-end boundary the meter wants.
//
// Kill vs disengage is decided from the last health seen while the bar was up:
// on the real kill the final sample read 0, on the walk-away it did not.
const bossClass = {};            // unit kind -> {boss, elite}, classified once
let bossBars = {};               // unit ptr string -> {kind, boss, elite, hp}
let bossLast = "";               // last state signature, to send only on change
let bossFnIsBoss = null, bossFnIsElite = null;

// ---- the fight ending without a kill (boss reset / team wipe) ----
// A dropped bar is NOT on its own the end of a fight: the Nightqueen replaces
// herself with copies, and between her bar going down and theirs coming up
// there is at least one poll seeing zero boss bars. Treating that as the end
// is the bug that used to wipe the meter repeatedly through one fight.
//
// So "no boss bar" has to PERSIST before it means anything. fetchBosses is a
// steady 2/s timer (measured), so this counts polls rather than needing a
// timer of its own — and because it is the poll that decides, a frame hitch
// cannot make a short gap look long.
//
// The observation is reported; the host decides what it means. It already
// knows whether a kill ended the fight, so it can tell a reset from a victory
// without the agent having to model either.
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

// Runs on the GAME thread (inside the fetchBosses hook), the only safe place
// for HL calls. Cached per unit KIND: a zone has few boss/elite types and the
// answer can't change for a given one.
function classifyBossUnit(u, kind) {
    let hit = bossClass[kind];
    if (hit) return hit;
    hit = { boss: false, elite: false };
    try { if (bossFnIsBoss) hit.boss = !!bossFnIsBoss(u); } catch (e) {}
    try { if (bossFnIsElite) hit.elite = !!bossFnIsElite(u); } catch (e) {}
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
                        now[key] = { kind: kind, boss: cls.boss, elite: cls.elite,
                                     // Keep the last non-null reading: at
                                     // teardown the unit may already be gone.
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
        down.push({ kind: b.kind, boss: b.boss, elite: b.elite,
                    killed: b.hp !== null && b.hp <= 0 });
    }
    bossBars = now;

    let anyBoss = false, anyElite = false, count = 0;
    for (const k in now) {
        count++;
        if (now[k].boss) anyBoss = true;
        if (now[k].elite) anyElite = true;
    }
    // The fight-ended-without-a-kill watch. Runs on every poll, before the
    // change gate below — the whole point is that it fires when NOTHING is
    // changing, which is exactly when that gate is sending nothing.
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
            send({ kind: "bossgone", polls: BOSS_GONE_POLLS });
        }
    }

    // Only talk when something changed. At 2/s an unconditional send would be
    // 2 messages a second forever, for a state that changes twice a pull.
    const sig = count + "|" + anyBoss + "|" + anyElite;
    if (!up.length && !down.length && sig === bossLast) return;
    bossLast = sig;
    send({ kind: "bossbar", n: count, boss: anyBoss, elite: anyElite,
           up: up, down: down });
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
    bossFnIsElite = nf("ent.Unit.isElite");
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
// The legendary-pickup cue, feeding the dungeon
// loot list. Plain pointer reads only.
//
// st.Inventory.content is an ArrayObj whose entries are NOT items: each is a
// standalone hl vvirtual (kind 15) carrying inline fields {count:Int,
// item:st.Item}. Its fields are found by NAME in the virtual's own field
// table:
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
    // level is declared on st.item.Gear (armour and weapons alike, so the
    // Weapon offset serves both); rarity only on st.item.Weapon — at any
    // other class that offset is past the end of the object. Everything
    // else's rarity is its sheet row's, looked up by the app.
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

// False if the container could not be read: a failed read looks exactly like
// an empty bag, and taking one for the other would report the whole bag as
// loot on the next good sweep.
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
// Bag + equipment (already counted for the pickups) + every bank tab, for the
// goals overlay ("5 copper ores": owned, wherever they are). Loadout.banks is
// an hxbit.ArrayProxyData; an entry is counted only when it reads as an
// st.Inventory. Sent when it changes; `banks` says how many tabs were read
// (-1: no offset), as the bank may only be known once it has been opened.
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

// Counting by kind across BOTH containers survives equipping and unequipping
// (the item moves, the count doesn't): only a real gain moves a count up.
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
            send({ kind: "pickup", item: inf.kind, cls: inf.cls,
                   rarity: inf.rarity, level: inf.level, count: gained });
        }
        invSeen = now;
    } catch (e) {}
}

// ---- the collection (mounts, gliders, companions) ----
// AccountProgress.collection: three hxbit proxy arrays. `pets` holds unit
// kinds as plain strings (measured 2026-08-07). `mounts` and `gliders` are
// read the same way, an element being taken as a String or, failing that,
// as an item whose kind is read — and its class is reported once, so the
// log says which it was.
let collSig = null;
let collTypesSent = false;

function collElem(p) {
    const t = typeName(p);
    if (t && (t.lastIndexOf("st.Item", 0) === 0
              || t.lastIndexOf("st.item.", 0) === 0))
        return hlStr(p.add(OFF.Item.kind).readPointer());
    return hlStr(p);
}

function readCollList(coll, off, types, key) {
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
        if (i === 0) types[key] = typeName(p);
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
        const C = OFF.Collection, types = {};
        const msg = { kind: "collection",
                      mounts: readCollList(coll, C.mounts, types, "mounts"),
                      gliders: readCollList(coll, C.gliders, types, "gliders"),
                      pets: readCollList(coll, C.pets, types, "pets"),
                      // the armour appearances (null on older offsets)
                      gears: readCollList(coll, C.gears, types, "gears") };
        if (msg.mounts === null || msg.gliders === null || msg.pets === null)
            return;
        const sig = JSON.stringify(msg);
        if (sig === collSig) return;
        collSig = sig;
        if (!collTypesSent) { msg.types = types; collTypesSent = true; }
        send(msg);
    } catch (e) {}
}

// ---- the codex: the game's own kill count per monster ----
// Measured 2026-08-05. The per-character store
// is replicated, so it is pointer reads plus the game's native map calls:
//
//   Hero.player -> Player.progress -> Progress.unitsProgress (hxbit.MapData)
//     -> MapData.map (a virtual; hl_vvirtual.value @8 is the real StringMap)
//     -> StringMap.h -> $std.hbkeys / $std.hbget(h, utf16(unitKind))
//     -> { killCount, rank }
//
// killCount is a LIFETIME total per monster kind, still climbing after the
// codex entry is mastered. hbkeys/hbget ALLOCATE, so this runs on the game
// thread only (the camera hook), on a slow clock.
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

// The item codex: Progress.itemProgress, item id -> {itemCount, rank} —
// the same route as the monsters' (a StringMap of hxbit proxies). A value of
// another type is reported by its class name, once, for the log.
let itemCodexSig = null;

// The achievements: the character's (Progress.achievements, id -> true) and
// the account's (AccountProgress.achievements, id -> completion time in ms),
// with the character's counters the objectives are measured on (measured
// 2026-10-01). Sent when anything changed. GAME THREAD ONLY.
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
    let other = null;
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
        else if (other === null) other = t;
    }
    const sig = localName + JSON.stringify(out);
    if (sig === itemCodexSig) return;
    itemCodexSig = sig;
    send({ kind: "itemcodex", hero: localName, items: out, other: other });
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
// Progress.elements: element id (a chest's, an orb's, an obelisk's) ->
// its ProgressState, per character. The game asks it through
// Progress.hasElementDiscovered / hasElementCompleted / getElementState.
// Same MapData -> StringMap route as the codex. Measured 2026-09-28: each
// value is an hxbit.ObjProxy_Ocompleted_Float whose `completed` is the time
// the element was completed; an element never completed has no entry (a
// player with every chest and orb had all of them, and none of the 18
// points of a region they never visited).
let elementsDue = true;
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
// Measured 2026-09-28 (a probe over ~25 players): for EVERY hero on the
// layer the client holds its class, level, equipment (Hero.loadout's
// equipment container, in slot order), its talents
// (HeroSpecialization.talents, a StringMap of talent ids), the skills in its
// slots and its skill masteries. Its attributes are NOT held as values: the
// UnitAttributes fields read 0 (ours included) and its `attributes` IntMap
// only has defaults — the game computes them on demand.
//
// The roster is sent every ~10 s; a full profile only when the app asks for
// one (recv "analyze"), both on the game thread (the talent map read
// allocates).
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

function mapKeys(h, max) {
    const out = [];
    if (!h || h.isNull() || !hbKeys) return out;
    const keys = hbKeys(h);
    if (!keys || keys.isNull()) return out;
    const n = keys.add(16).readS32();
    for (let i = 0; i < n && i < max; i++) {
        const kb = keys.add(24 + i * 8).readPointer();
        if (kb && !kb.isNull()) out.push(kb.readUtf16String());
    }
    return out;
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

// A hero's Progress.counters (a plain StringMap: loot luck counters, rift
// and gold totals...), each value as a number when it is a boxed Int /
// Float, else its type — or why the map could not be read. Only the local
// hero's is replicated (measured 2026-10-01). GAME THREAD ONLY.
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
    r.counters = countersOf(h);
    // the Soulwell's luck statuses, and the clock to time them by
    r.luckStatuses = statusesOf(h, ["Luck_", "Riftstalkers"]);
    // every status on the hero: the sheet applies their attribute effects
    r.statuses = statusesOf(h, [""]).map(function (s) { return s[0]; });
    try { r.now = serverNowOf(h.add(OFF.Hero.layer).readPointer()); } catch (e) {}
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
                            if (r.talentVal === undefined)
                                r.talentVal = [kind, tn, v.add(8).readS32(), v.add(20).readS32()];
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
    // The hero's own skill arrays: the action bar as the game holds it.
    try {
        if (D.weaponSkills != null) r.weaponSkills = skillKinds(h.add(D.weaponSkills).readPointer(), 20);
        if (D.secondarySkill != null) {
            const s2 = h.add(D.secondarySkill).readPointer();
            r.secondary = (s2 && !s2.isNull()) ? hlStr(s2.add(OFF.Skill.kind).readPointer()) : null;
        }
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

// The local hero's loot luck and statistics, for the live page: read on
// their own every minute, no analysis needed (only the local hero's
// counters are replicated anyway).
let selfDue = true;

// GAME THREAD ONLY.
function characterTick() {
    if (!localHero || localHero.isNull() || !OFF.HeroDetail) return;
    if (selfDue) {
        selfDue = false;
        try {
            const me = { counters: countersOf(localHero),
                         luckStatuses: statusesOf(localHero, ["Luck_", "Riftstalkers"]) };
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
    // A loading screen tears the HUD down: a bar that was up on the way out
    // must not stay "up" in the new zone. No up/down events — the pull isn't
    // ending, we just can't see it any more, and a phantom kill would be worse
    // than saying nothing.
    if (Object.keys(bossBars).length) send({ kind: "bossbar", n: 0, boss: false,
                                             elite: false, up: [], down: [] });
    bossBars = {};
    bossLast = "";
}

// ---- skill display-name resolution (CDB, via libhl dynamic field access) ----
// baseSkill.inf is a vvirtual over the CDB skill row; its `texts.name` is the
// localized display name (e.g. Warrior_Rage_Strike -> "Rage Strike"). We read
// it with hl_obj_get_field + hl_hash_utf8, cached per skill id.
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

// Walk the local player's group roster -> {name: 1}. groupId is unreliable (0),
// but group.players lists the actual party members. Traversal:
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

// ---- the shard roster (Social tab) ----
// st.GameLayer.players is EVERY player the client holds state for, not just
// the ones streamed in around you — which is the whole point, since `units`
// (what the minimap sweeps) only ever contains your neighbours.
//
// Each entry carries the player's Steam account id in `uid`, as
// "S" + the id's bytes in LITTLE-ENDIAN hex with trailing zero bytes trimmed.
// It is sent on as-is and converted host-side; doing the arithmetic here would
// put a second implementation of a fiddly byte-order rule in a second language.
//
// The class lives on the player's ent.Hero, not on st.player.HeroData — that
// object is null client-side for everyone, including you.
//
// Plain pointer reads throughout, so this is safe on a timer thread: no HL
// call, no allocation, nothing that needs the GC lock.
const SHARD_MAX = 256;          // a sane ceiling on a corrupt length read
let shardTimer = null;
let shardSig = "";              // last payload signature, to skip idle resends

function readShard(hero) {
    const out = [];
    const P = OFF.Player, H = OFF.Hero, G = OFF.GameLayer;
    if (!P || !H || !G || P.uid == null || G.players == null) return out;
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
            const row = { n: nm, uid: hlStr(p.add(P.uid).readPointer()) };
            try { row.me = p.add(P.isMe).readU8() !== 0; } catch (e) {}
            // The hero entity is absent for a player who is on the layer but
            // not yet built — a real state, so the row still ships, just
            // without a class. The tab shows "-" rather than dropping them.
            const h = p.add(P.hero).readPointer();
            if (h && !h.isNull()) {
                if (H.kind != null) row.k = hlStr(h.add(H.kind).readPointer());
                if (H.level != null) row.lvl = h.add(H.level).readS32();
            }
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
        // A hub roster is re-read every couple of seconds but changes rarely;
        // resending an identical list would repaint the tab under the cursor
        // for nothing. Level is in the signature so a ding still lands.
        const sig = list.map(function (r) {
            return r.n + "|" + (r.uid || "") + "|" + (r.k || "") + "|" + (r.lvl || "");
        }).sort().join(";");
        if (sig === shardSig) return;
        shardSig = sig;
        send({ kind: "shard", list: list });
    } catch (e) {}
}

// Set by a timer, consumed on the game thread by the camera hook. The lookup
// itself must NOT run on the timer: getHero is an HL call, and an HL call off
// the game thread kills the game with "Can't lock GC in unregistered thread".
// This shipped calling refreshLocalHero() straight from a setInterval, which
// is the same pattern that killed a probe on its ~10th tick — it survived only
// because the window is narrow, not because it was safe.
let heroRefreshDue = false;

function refreshLocalHero() {
    for (const f of getHeroFns) {
        try {
            const h = new NativeFunction(f.addr, "pointer", [])();
            if (h && !h.isNull() && typeName(h) === "ent.Hero") {
                // Another hero object is another bag: re-baseline rather
                // than report all of it as loot.
                if (!localHero || !localHero.equals(h)) invReady = false;
                localHero = h;
                partyNames = readParty(h);
                const nm = hlStr(h.add(OFF.Hero.name).readPointer());
                if (nm) partyNames[nm] = 1;   // always include self
                localName = nm;
                send({ kind: "hero", name: localName,
                       party: Object.keys(partyNames) });
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
    // The table exists (and holds the natives) before the game's own code is
    // compiled into it: hooked that early, every target is garbage (measured
    // 2026-10-01 on a relaunch: "access violation accessing 0x1556f0"). The
    // camera's slot must point at code; until it does, it's too early.
    if (!slotIsCode(base, DATA.cam_targets && DATA.cam_targets["client.BaseCamera.postUpdate"])) {
        log("game still booting (its code isn't in the table yet)");
        send({ kind: "ready", ok: false, early: true });
        return;
    }

    if (!setupNameApi()) log("skill-name API unavailable; showing raw ids");
    if (!setupCodexApi(base)) log("!! map natives missing; no kill counts");
    every(function () { codexDue = true; }, 8000);
    every(function () { rosterDue = true; }, 5000);
    every(function () { selfDue = true; }, 60000);
    listenAnalyze();

    // DATA.map_fn (Main.getMapId) is no longer resolved or called — measured
    // returning the machine hostname; the zone signal reads layer.world.level.

    for (const nm in DATA.funcs) {
        try { getHeroFns.push({ name: nm, addr: base.add(DATA.funcs[nm] * 8).readPointer() }); } catch (e) {}
    }
    // Both the first lookup and the 3s refresh (survive respawn / zone
    // changes) are deferred to the camera hook's game thread — see
    // heroRefreshDue.
    heroRefreshDue = true;
    every(function () { heroRefreshDue = true; }, 3000);

    // Combat-state heartbeat: report isInCombat for the local hero and every
    // player we've seen deal damage, so Python can drive the capture timer.
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

    // The shard roster, on its own slow clock. A hub list of 30 people is not
    // worth rebuilding at the minimap's 150ms, and sweepShard() suppresses
    // resends of an unchanged list anyway — so this costs one array walk every
    // two seconds and usually sends nothing.
    if (OFF.Player && OFF.Player.uid != null
        && OFF.GameLayer && OFF.GameLayer.players != null) {
        shardTimer = every(sweepShard, 2000);
    } else {
        // A stale analysis_out silently has no `uid`: readShard() returns []
        // on its first line forever and no class tags ever appear. Say so once.
        log("!! Player.uid / GameLayer.players missing from analysis_out — "
            + "class tags will stay empty. Regenerate offsets.");
    }

    hookGameTick(base);
    hookBossBar(base);

    const fi = DATA.count_targets["ent.Unit.onInflictDamage"];
    const daddr = base.add(fi * 8).readPointer();
    const DR = OFF.DamageResult, BS = OFF.BaseSkill;

    // Who the hit landed ON. `DamageResult.target` is typed ent.GameObject,
    // which is two levels above ent.Unit — so `Unit.kind`@600 is only a field
    // at all when the object really is a unit, and reading it off anything
    // else is a read past the end of the object. The type check against the
    // shipped unitClasses set is what makes it safe, exactly as FOE_CLASS
    // gates `Foe.summonOwner` for summon attribution.
    //
    // The raw kind goes to the host, not a display name: naming it would mean
    // an HL call (inf -> texts -> name) inside the damage hook, and the host
    // already maps kinds through the cdb's own unit sheet — which is where
    // "Cleodora" becomes "Queen Honeyzabeth".
    const UNIT_CLASS = {};
    (OFF.unitClasses || []).forEach(function (c) { UNIT_CLASS[c] = 1; });
    const canNameTargets = Object.keys(UNIT_CLASS).length > 0
        && OFF.Unit && OFF.Unit.kind != null && DR.target != null;
    if (!canNameTargets)
        log("!! hit targets will not be named (offsets file predates "
            + "unitClasses) — combat history datasets fall back to the zone "
            + "name alone. Delete analysis_out and restart to regenerate it.");

    function targetKindOf(dr) {
        if (!canNameTargets) return "";
        try {
            const t = dr.add(DR.target).readPointer();
            if (!t || t.isNull() || t.compare(ptr("0x10000")) <= 0) return "";
            if (!UNIT_CLASS[typeName(t)]) return "";
            return hlStr(t.add(OFF.Unit.kind).readPointer()) || "";
        } catch (e) { return ""; }
    }

    // Read the common hit fields off a st.skill.DamageResult* (heals reuse the
    // same struct — evalHeal/onInflictHealEval mirror the damage pipeline).
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
        // Only when there is one to give: a hit whose target reads back as
        // something other than a unit sends no field at all, so the host can
        // tell "not a unit" from "a unit named empty string".
        const tk = targetKindOf(dr);
        if (tk) out.target = tk;
        // Nullified-hit diagnostic. The meter counts `amount` whether or not
        // the target took it, so a boss in an immunity phase inflates the
        // parse. These three fields are the candidates for marking that, and
        // they ride along ONLY when one of them is actually set — on an
        // ordinary hit this adds nothing to the message.
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
    // A summon's damage is a player's damage. It arrives on this same hook
    // with an ent.Foe dealer, and until 3.3.4 it was dropped on the floor —
    // worth ~13% of a bee build's total (measured 2026-07-30, 10,204 of
    // 80,299 damage in one session).
    //
    // `ent.Foe.summonOwner`, type-checked to ent.Hero, is the attribution.
    // Two things that look like they'd work and don't:
    //   * `isSummon()` / `get_summonHero()` — YES for a MOB's pet too
    //     (RobinHoofDog01, owned by the RobinHoof mob), so a rule built on it
    //     credits a player with a monster's wolf.
    //   * the hit skill's `.owner` — that's the summon itself. A summon owns
    //     its own skill, which is precisely why nothing attributed before.
    // The type check on the OWNER is what covers both: a mob's pet has a
    // summonOwner, it just isn't an ent.Hero. It also double-duties as the
    // dangling-pointer guard — summonOwner is a raw pointer, so a summon that
    // outlives its owner would otherwise read a name out of recycled memory.
    // A freed hero stops reading back as ent.Hero and the hit is dropped.
    const FOE_CLASS = {};
    (OFF.foeClasses || []).forEach(function (c) { FOE_CLASS[c] = 1; });
    const canAttributeSummons =
        Object.keys(FOE_CLASS).length > 0 && OFF.Foe
        && OFF.Foe.summonOwner != null;
    if (!canAttributeSummons)
        log("!! summon damage will not be attributed (offsets file predates "
            + "foeClasses) — pet and totem damage is missing from the parse. "
            + "Delete analysis_out and restart to regenerate it.");

    // The owner's name is resolved HERE, at damage time, never cached at
    // summon-birth. Measured twice independently: at set_summonOwner the
    // owner is a valid ent.Hero whose `name` still reads null, and fills in
    // later — caching there yields a nameless row.
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

    // Which summon dealt it, as its RAW `Unit.kind` ("Summon_Imp"). The damage
    // merges into the owner's row, so the skill breakdown is the only place
    // that can say a chunk of that row came from a pet, and the host both
    // resolves the kind to a display name and does the prefixing — see
    // `_skill_of` / `_summon_label`.
    //
    // The kind is sent raw rather than prettied up here, for three reasons:
    // the boosted-damage rule matches on the raw display name and a baked-in
    // prefix would defeat it; the presentation can then change without a
    // re-inject; and the kind is NOT the name the game shows — `Summon_Imp`
    // displays as "Nightling Terror". Only the cdb unit sheet knows that, and
    // it lives host-side.
    //
    // The summon's own skill is what gets recorded, not the skill that spawned
    // it: it's what actually hit the target.
    function petKind(dealer) {
        try {
            return hlStr(dealer.add(OFF.Unit.kind).readPointer()) || "";
        } catch (e) { return ""; }
    }

    // ---- damage from a status somebody else applied ----
    // Swarmstrike Accord (`DS_Bladeleaf_Skill2`, off the Wingsabers dual blades
    // `DS_Z1RBee_AssWiz`) blesses every ally in range, and the game credits the
    // bonus damage to the CASTER no matter whose swing set it off. That put
    // other people's damage on the wielder's row — a rift wielder topped the
    // meter for work the group did.
    //
    // Measured 2026-08-04 (frida/boost_probe.js, 235 procs, 7 status
    // instances, run from a BUFFED ALLY's client so caster and swinger were
    // different objects): the blessing is a status skill instantiated PER
    // ALLY, and `DamageResult.baseSkill.owner` is the ally carrying it — the
    // one who actually swung. The dealer (rcx) was the caster on all 235.
    // Cross-checked two ways that agree exactly: the instances owned by the
    // local hero held 31 hits, and 31 procs were preceded by a local-hero
    // swing. `ctx` and `serverSource` are null and `weakSource` is a constant
    // across every instance, so none of those can carry it.
    //
    // A summon's hits proc it too (73 of the 235), and those still resolve to
    // a hero owner, so pets land on their owner's row for free.
    //
    // The rule is general rather than a name match on that one skill: damage
    // dealt by a status belongs to whoever is CARRYING the status. For an
    // ordinary skill the owner IS the dealer and nothing moves. Only
    // Swarmstrike Accord has been measured, so every distinct re-attribution
    // is logged once — anything unexpected shows up in the log rather than
    // quietly moving damage between players.
    const reattrSeen = {};
    function statusHolderOf(dr) {
        try {
            const bs = dr.add(DR.baseSkill).readPointer();
            if (!bs || bs.isNull() || BS.owner == null) return null;
            const owner = bs.add(BS.owner).readPointer();
            if (!owner || owner.isNull()) return null;
            // Type-checked for the same reason summonOwner is: this is a raw
            // pointer, and a freed hero stops reading back as an ent.Hero.
            if (typeName(owner) !== "ent.Hero") return null;
            return owner;
        } catch (e) { return null; }
    }

    Interceptor.attach(daddr, {
        onEnter() {
            try {
                const dealer = this.context.rcx;
                // Players (ent.Hero) and their summons. Everything else — mobs,
                // bosses, and a MOB's pet — is somebody else's damage.
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
                // Only for hero-dealt hits: a summon's skill is owned by the
                // summon, which summonOwnerOf has already resolved properly.
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
    // A client is never told how much a heal healed for. Measured 2026-08-03
    // (frida/run_heal.py, 40 heal events across 6 healers and 4 skills): of the
    // fifteen heal entry points in this build, ONLY ent.Unit.playHitHealFX runs
    // on a client, and its HitData.amount reads 0.000. receiveHeal, computeHeal,
    // evalHeal, the four *HealEval callbacks, applyHeal, rpcDisplayHeal(__impl)
    // and ui.hud.EffectsFeed.displayHeal never fire here at all.
    //
    // So healing is captured from the two things that ARE replicated:
    //   * ent.Unit.playHitHealFX(target=rcx, hitData=rdx): fires on every
    //     healed unit; HitData.baseSkill names the healing skill and its owner
    //     (the healer). This is the heal EVENT — it happens whether or not the
    //     target had any health to restore.
    //   * ent.UnitAttributes.set_health(attrs=rcx, v): the replicated health
    //     value. A RISE in a hero's health is how much of that heal LANDED.
    //
    // Every FX is emitted exactly once, with `landed` = the health rise it
    // produced or 0 if it produced none — a heal on a full-health target is a
    // real heal and the meter has to see it. (Before this, only rises were
    // sent, so a healer topping people off scored nothing and the parse
    // measured who healed FASTEST rather than who healed HARDEST.) The host
    // turns `landed` into a raw amount and an overheal share; the agent stays
    // out of that so the estimator can change without a re-inject.
    //
    // FX-less rises while in combat are natural regen (self-heal), and
    // out-of-combat regen / spawn replication (old health 0) is dropped.
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

    const fxFi = DATA.count_targets["ent.Unit.playHitHealFX"]
        || (DATA.candidates && DATA.candidates["ent.Unit.playHitHealFX"]);
    const shFi = DATA.count_targets["ent.UnitAttributes.set_health"]
        || (DATA.candidates && DATA.candidates["ent.UnitAttributes.set_health"]);
    const UA = OFF.UnitAttributes, HD = OFF.HitData;
    if (fxFi == null || shFi == null || !UA || !HD || OFF.BaseSkill.owner == null) {
        log("heal data missing (playHitHealFX/set_health findex or offsets); "
            + "healing capture disabled — re-run hltools/build_targets.py "
            + "and hltools/emit_offsets.py");
    } else {
        // target unit ptr -> queue of heal FX awaiting a health rise. A QUEUE,
        // not a slot: two healers can land on the same target inside the match
        // window, and the old single-slot map dropped the first one outright.
        const pendingHealFx = {};
        const HEAL_MATCH_MS = 1500;   // how long an FX waits for its rise
        const HEAL_QUEUE_MAX = 32;    // a HoT storm must not grow without bound

        // `est` marks an event whose size the host is allowed to estimate —
        // i.e. one that came from a heal FX, where `landed` may be a capped
        // view of a bigger heal. Natural regen is NOT estimable: it has no FX,
        // it is only ever observed AS the health rise, and running it through
        // the estimator would credit every small tick at the largest tick's
        // size.
        function emitHeal(fx, landed, estimable) {
            send(Object.assign({ kind: "heal", skill: fx.skill, name: fx.name,
                                 element: "Heal", amount: landed,
                                 landed: landed, est: estimable ? 1 : 0,
                                 self: fx.slf ? 1 : 0,
                                 step: fx.step, dyn: fx.dyn, atb: fx.atb,
                                 crit: 0, kill: 0 }, fx.who));
        }

        // What a heal was WORTH, as far as a client can see it. The amount is
        // never sent (measured — see the note above), but the cdb says how the
        // game computes each skill's heal, and both of its ingredients ARE
        // here: BaseSkill.dynVal1-3 are replicated, and a scaling heal is a
        // ratio on one of the caster's attributes. So the raw numbers ride
        // along with every heal event and the host does the arithmetic against
        // analysis_out/heal_specs.json.
        function healInputs(bs, hitData, fx) {
            try {
                const B2 = OFF.BaseSkill;
                if (B2.dynVal1 != null) {
                    fx.dyn = [bs.add(B2.dynVal1).readDouble(),
                              bs.add(B2.dynVal2).readDouble(),
                              bs.add(B2.dynVal3).readDouble()];
                }
                // Which step fired: a skill can heal from more than one step
                // at different rates (Sword_Swarm_Combo does), and the spec is
                // per step index.
                if (HD.step != null && OFF.SkillStep) {
                    const st = hitData.add(HD.step).readPointer();
                    if (st && !st.isNull())
                        fx.step = st.add(OFF.SkillStep.index).readS32();
                }
                // The caster's attributes. `owner` is the healing unit, which
                // for a totem or a summon is the totem — its stats, not the
                // summoner's. Accepted: those skills are a small minority and
                // the landed-heal fallback still covers them.
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

        // Did the healer heal THEMSELVES? Pointer identity settles it when the
        // skill's owner is the healed hero. The name fallback exists for the
        // skills a player owns but does not personally cast — a totem or a
        // summon healing the player who put it down is still that player
        // healing themselves, and there the owner pointer is the totem.
        function isSelfHeal(bs, target, who) {
            try {
                const owner = bs.add(OFF.BaseSkill.owner).readPointer();
                if (owner && !owner.isNull() && owner.equals(target)) return 1;
                const tn = hlStr(target.add(OFF.Hero.name).readPointer());
                return (tn && who.player === tn) ? 1 : 0;
            } catch (e) { return 0; }
        }

        function flushExpired(q, now) {
            // Nothing rose within the window, so this heal restored nothing.
            // It still happened — emit it with landed 0 rather than drop it.
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
                    // Read NOW, not when the event is emitted: an FX that never
                    // lands is flushed up to 1.5 s later, by which time the
                    // skill may have been re-cast (dynVal1 rewritten) or freed.
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
                        // Anything past the window can't own this rise; emit
                        // those as the zero-landing heals they are, then take
                        // the oldest survivor.
                        flushExpired(q, Date.now());
                        if (q.length) fx = q.shift();
                        if (!q.length) delete pendingHealFx[key];
                    }
                    if (fx) { emitHeal(fx, delta, true); return; }
                    // FX-less rise: natural regen. Only meaningful in
                    // combat — out-of-combat regen is constant noise.
                    if (!unit.add(OFF.Hero.isInCombat).readU8()) return;
                    // Regen is self-healing by definition: the unit whose
                    // health rose is both the healer and the healed.
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
// Defer setup off the load() call so script.load() returns immediately. Running
// the memory scan synchronously inside load() blocks the injection handshake and
// can stall the game's thread mid-inject; deferring lets injection settle first.
setTimeout(main, 150);

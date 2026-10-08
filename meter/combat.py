"""Combat accounting: the damage/heal session, rift and dungeon recording, and
what the hook says about the world."""
from __future__ import annotations

import sys
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

from common import (
    BOSS_PULL_BACKLAG_SECS, COMBAT_TIMEOUT_SECS, RECENT_EVENT_MAX, _APP,
    _class_tag)
from gamedata import _summon_label, dungeon_name


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------
def _skill_ident(ev):
    """(breakdown key, display name) for one hit or heal event. A summon's
    hit carries `pet` (its Unit.kind): its damage is the owner's, its skill
    keyed by the pet so it stays apart from the owner's own of that name, and
    named after it ("Nightling Terror: Attack")."""
    sid = ev.get("skill", "?")
    nm = ev.get("name")
    pet = ev.get("pet")
    if pet:
        sid = f"{pet}:{sid}"
        label = _summon_label(pet)
        nm = f"{label}: {nm}" if nm else label
    return sid, nm


def _stamp_report_classes(report, world):
    """Each player's class tag frozen into a finished report: it is saved
    and reopened later, when the shard no longer holds those players."""
    for ph in report.get("phases", ()):
        for p in ph.get("players", ()):
            p["cls"] = _class_tag(world.class_of(p.get("name")))


def _overheal_pct(total, landed):
    """Share of `total` healing that restored no health, as a percentage.

    Clamped at 0: a rise can exceed a heal's estimated size (a regen tick in
    its match window, say)."""
    if not total or total <= 0.0:
        return 0.0
    return max(0.0, (total - landed) / total * 100.0)


class HealSizeEstimator:
    """How big was that heal? The client is never told, so this estimates it.

    Measured 2026-08-03: only `ent.Unit.playHitHealFX` runs client-side and
    its amount reads 0; the only observable is the RISE in the target's
    health (zero on a full target). A heal is sized from the game's own data
    (size_from_spec) when possible, else as the HIGH-WATER MARK of what that
    player's casts of that skill were seen to restore: capping only biases
    observations down, and maxHealth reads 0 for heroes, so a mean would
    under-rate healers of healthy parties. Cost: a skill once seen to crit is
    credited its crit value. The window bounds it as gear and levels change.

    Hook message thread only, so no lock.
    """

    WINDOW = 64          # observations kept per (player, skill)
    UNDER_MIN_HP = 3     # a smaller shortfall than this is not reported

    def __init__(self, specs=None):
        self._obs: dict[tuple, deque] = defaultdict(
            lambda: deque(maxlen=self.WINDOW))
        # skill id -> {step index: [effect spec, ...]} from data.cdb
        # (analysis_out/heal_specs.json); the only way to size a heal on a
        # full-health target.
        self._specs = specs or {}
        self._computed = 0      # heals sized from the game's own numbers
        self._guessed = 0       # ...and heals that fell back to observation
        self._unsized = 0       # ...and heals nothing could size
        # skill -> (landed/computed, landed, computed) for the worst case seen
        self._audit: dict[str, tuple] = {}

    def size_from_spec(self, ev):
        """The heal's real size, computed the way the game computes it.

        `dyn` heals read BaseSkill.dynVal1-3 (server-replicated); `scale`
        heals are a ratio on a caster attribute. None when the skill isn't in
        the table or an input is missing (a summon's attributes, a step index
        that didn't match)."""
        steps = self._specs.get(ev.get("skill"))
        if not steps:
            return None
        specs = steps.get(str(ev.get("step")))
        if specs is None:
            # One heal step is the common case (39 of 44 skills). When there is
            # exactly one, a step index that didn't line up doesn't matter.
            if len(steps) != 1:
                return None
            specs = next(iter(steps.values()))
        dyn, atb = ev.get("dyn") or [], ev.get("atb") or {}
        total = 0.0
        for spec in specs:
            if spec.get("stack"):
                return None      # times the status's stacks: never told
            amount = 0.0
            # A spec carrying both is a dyn with a floor: the dyn is the real
            # value and the base is what it falls back to.
            n = spec.get("dyn")
            if n and len(dyn) >= n and dyn[n - 1]:
                amount = float(dyn[n - 1])
            elif spec.get("scale"):
                for ratio, name in spec["scale"]:
                    if name not in atb:
                        return None      # MaxHealth/FoePower — not readable
                    amount += float(ratio) * float(atb[name])
            if not amount:
                amount = float(spec.get("base") or 0.0)
            # a heal over time: each tick a share of it (the game divides)
            if spec.get("ticks"):
                amount /= spec["ticks"]
            total += amount
        return total if total > 0 else None

    def is_over_time(self, ev):
        """The heal is a tick of a heal over time (heal_specs' "ticks")."""
        steps = self._specs.get(ev.get("skill")) or {}
        specs = steps.get(str(ev.get("step")))
        if specs is None and len(steps) == 1:
            specs = next(iter(steps.values()))
        return any(s.get("ticks") for s in specs or ())

    def stamp(self, ev: dict) -> None:
        """Fold one heal event in and fill in its raw size.

        `landed` (what the health bar moved) comes from the hook; `amount`
        leaves as the estimated size of the heal, which consumers read."""
        landed = float(ev.get("landed", ev.get("amount", 0.0)) or 0.0)
        ev["landed"] = landed
        if not ev.get("est"):
            ev["amount"] = landed          # regen: observed AS the rise
            return
        obs = self._obs[(ev.get("player") or "?", ev.get("skill") or "?")]
        if landed > 0:
            obs.append(landed)
        # The game's own numbers first; observation only for skills the table
        # can't size.
        spec = self.size_from_spec(ev)
        if spec is not None:
            self._computed += 1
            ev["sized"] = "spec"
        elif self.is_over_time(ev):
            # a tick of a heal over time the formula can't size (a share of
            # the max health, the stacks unknown): what it was seen to
            # restore, never another tick's — a big heal landing with one
            # would otherwise become every tick's size
            spec = landed
            self._guessed += 1
            ev["sized"] = "tick"
        else:
            spec = max(obs) if obs else 0.0
            if spec > 0:
                self._guessed += 1
                ev["sized"] = "seen"
            else:
                self._unsized += 1
                ev["sized"] = "none"
        # A size can never make a measured heal smaller than it actually was.
        ev["amount"] = max(landed, spec)
        # Audit: a landed heal is a lower bound on its size, so a computed size
        # below it means the formula is wrong (above it is just overheal).
        if ev.get("sized") == "spec" and landed > 0:
            key = ev.get("skill") or "?"
            worst = self._audit.get(key)
            ratio = landed / spec if spec > 0 else 0.0
            if worst is None or ratio > worst[0]:
                self._audit[key] = (ratio, landed, spec)

    def drain_report(self):
        """A one-line summary for the log, or None when nothing has healed.
        UNDER-COMPUTED entries flag heal formulas that need fixing."""
        computed, guessed, unsized = (self._computed, self._guessed,
                                      self._unsized)
        if not (computed or guessed or unsized):
            return None
        # Both a ratio and a few HP: on an 8 HP heal, 2% is rounding.
        under = sorted((k, v) for k, v in self._audit.items()
                       if v[0] > 1.02 and v[1] - v[2] >= self.UNDER_MIN_HP)
        self._computed = self._guessed = self._unsized = 0
        self._audit = {}
        line = (f"[meter] heal sizing: {computed} computed, "
                f"{guessed} from observation, {unsized} unsized")
        if under:
            line += "   UNDER-COMPUTED: " + ", ".join(
                f"{k} landed={v[1]:.0f} vs computed={v[2]:.0f}"
                for k, v in under[:4])
        return line


@dataclass
class PlayerAgg:
    name: str
    is_me: bool = False
    in_party: bool = False
    total: float = 0.0
    hits: int = 0
    crits: int = 0
    kills: int = 0
    heal_total: float = 0.0     # raw healing (see HealSizeEstimator)
    heal_landed: float = 0.0    # ...of which actually restored health
    heal_self: float = 0.0      # ...and of which the healer was the target
    # skill -> [hits, total, crits]  (damage)
    skills: dict[str, list] = field(default_factory=lambda: defaultdict(lambda: [0, 0.0, 0]))
    # skill -> [hits, total, crits, self_total]  (healing; self_total splits
    # the bar into self/others)
    heals: dict[str, list] = field(default_factory=lambda: defaultdict(lambda: [0, 0.0, 0, 0.0]))
    # element -> [hits, total]
    elements: dict[str, list] = field(default_factory=lambda: defaultdict(lambda: [0, 0.0]))

    def record(self, skill, element, amount, crit, kill):
        self.total += amount
        self.hits += 1
        if crit:
            self.crits += 1
        if kill:
            self.kills += 1
        s = self.skills[skill]
        s[0] += 1; s[1] += amount; s[2] += crit
        e = self.elements[element]
        e[0] += 1; e[1] += amount

    def record_heal(self, skill, amount, crit, landed=0.0, is_self=False):
        self.heal_total += amount
        self.heal_landed += landed
        if is_self:
            self.heal_self += amount
        s = self.heals[skill]
        s[0] += 1; s[1] += amount; s[2] += crit
        s[3] += amount if is_self else 0.0

    @property
    def overheal_pct(self):
        """Share of this player's healing that restored no health (0 if none)."""
        return _overheal_pct(self.heal_total, self.heal_landed)


class PartySession:
    """Tracks the current encounter across all players.

    Encounter boundaries are hit-driven (a hit after > timeout of no damage
    starts a fresh encounter). The *duration* clock, however, only advances
    while at least one captured player is in combat — set_active() is driven by
    the caller with the game's isInCombat state, so DPS averages over active
    combat time rather than wall-clock.
    """

    def __init__(self, combat_timeout=COMBAT_TIMEOUT_SECS):
        self.lock = threading.Lock()
        self.timeout = combat_timeout
        self.players: dict[str, PlayerAgg] = {}
        self.skill_names: dict[str, str] = {}   # skill id -> display name
        self.combat: dict[str, int] = {}        # name -> isInCombat (from hook)
        self.enc_start = 0.0
        self.last_hit = 0.0
        self.active_accum = 0.0                  # accumulated in-combat seconds
        self.active_since = None                 # ts when current active run began
        self.in_combat = False
        self.epoch = 0          # bumped on explicit/zone reset (UI watches it)
        self.capture_until = None   # parse mode's hard cutoff (None = no limit)
        self.capture_start = None   # ...and when that window opened
        # (timestamp, "hit"|"heal", event) recorded lately, replayed by a
        # boss-pull reset
        self._recent: deque = deque(maxlen=RECENT_EVENT_MAX)

    def set_capture_window(self, seconds):
        """Parse mode: take data for exactly `seconds` from now, then stop.
        Enforced on the data path, not the UI tick, so the sample is exact.
        None clears the limit."""
        with self.lock:
            now = time.time()
            self.capture_start = None if seconds is None else now
            self.capture_until = None if seconds is None else now + seconds

    def _capturing(self, now):
        return self.capture_until is None or now <= self.capture_until

    def _effective_duration(self, now):
        """Seconds to divide by for DPS.

        Normally in-combat time, so the walk to a pull doesn't dilute it.
        Inside a parse window it's wall-clock elapsed: downtime must count or
        two parses aren't comparable (isInCombat drops between pulls)."""
        if self.capture_start is not None:
            return max(0.001, min(now, self.capture_until) - self.capture_start)
        return max(0.001, self._duration(now)) if self.enc_start else 0.0

    def _reset(self):
        self.players.clear()
        self.enc_start = 0.0
        self.active_accum = 0.0
        self.active_since = None

    def _player_for(self, ev):
        name = ev.get("player") or "?"
        p = self.players.get(name)
        if p is None:
            p = PlayerAgg(name=name, is_me=bool(ev.get("is_me")))
            self.players[name] = p
        if ev.get("is_me"):
            p.is_me = True
        if ev.get("in_party"):
            p.in_party = True
        return p

    def _skill_of(self, ev):
        sid, nm = _skill_ident(ev)
        if nm and sid not in self.skill_names:
            self.skill_names[sid] = nm
        return sid

    def record(self, ev: dict):
        with self.lock:
            now = time.time()
            if not self._capturing(now):
                return
            # No lull-reset inside a parse window: a quiet stretch is part of
            # the sample.
            if (self.capture_until is None and self.last_hit
                    and (now - self.last_hit) > self.timeout):
                self._reset()             # new encounter after a long lull
            if self.enc_start == 0.0:
                self.enc_start = now
            self.last_hit = now
            self._recent.append((now, "hit", ev))
            self._apply_hit(self._player_for(ev), ev, now)

    def _apply_hit(self, p, ev, ts):
        """Record one hit (shared with the boss-pull rewind)."""
        p.record(self._skill_of(ev), ev.get("element", "?"),
                 float(ev.get("amount", 0.0)), int(ev.get("crit", 0)),
                 int(ev.get("kill", 0)))

    def record_heal(self, ev: dict):
        # Heals never drive encounter boundaries (an out-of-combat regen must
        # not start a fresh encounter), so last_hit stays untouched.
        with self.lock:
            now = time.time()
            if not self._capturing(now):
                return
            self._recent.append((now, "heal", ev))
            p = self._player_for(ev)
            p.record_heal(self._skill_of(ev),
                          float(ev.get("amount", 0.0)), int(ev.get("crit", 0)),
                          float(ev.get("landed", 0.0)),
                          bool(ev.get("self")))

    def set_combat(self, state: dict):
        with self.lock:
            self.combat = state

    def set_active(self, active: bool, now: float):
        """Advance/pause the duration clock based on whether a captured player
        is currently in combat. Called each UI tick with a mode-aware value."""
        with self.lock:
            # Past a parse window's cutoff the clock stops even mid-fight.
            if not self._capturing(now):
                active = False
            if active and self.active_since is None:
                self.active_since = now
            elif not active and self.active_since is not None:
                self.active_accum += now - self.active_since
                self.active_since = None
            self.in_combat = active

    def reset(self):
        with self.lock:
            self._reset()
            self.last_hit = 0.0
            self.in_combat = False
            # back to live capture, and so to in-combat DPS
            self.capture_until = self.capture_start = None
            self._recent.clear()
            self.epoch += 1

    def reset_keeping_recent(self, backlag=BOSS_PULL_BACKLAG_SECS):
        """Reset the encounter but carry the last `backlag` seconds forward.

        For the boss-pull reset: the boss healthbar lags the pull, so a plain
        reset would delete the opening burst. Events are replayed with their
        ORIGINAL timestamps so the encounter start and duration stay honest.
        Returns how many events were carried over."""
        with self.lock:
            cutoff = time.time() - backlag
            keep = [e for e in self._recent if e[0] >= cutoff]
            self._reset()
            self.last_hit = 0.0
            self.in_combat = False
            self.capture_until = self.capture_start = None
            self._recent.clear()
            self.epoch += 1
            for ts, kind, ev in keep:
                self._recent.append((ts, kind, ev))
                p = self._player_for(ev)
                if kind == "hit":
                    if self.enc_start == 0.0:
                        self.enc_start = ts
                    self.last_hit = ts
                    self._apply_hit(p, ev, ts)
                else:
                    p.record_heal(self._skill_of(ev),
                                  float(ev.get("amount", 0.0)),
                                  int(ev.get("crit", 0)),
                                  float(ev.get("landed", 0.0)),
                                  bool(ev.get("self")))
            # In combat for the whole replayed stretch, else those seconds
            # would miss the DPS divisor.
            if self.enc_start:
                self.active_since = self.enc_start
                self.in_combat = True
        return len(keep)

    def _duration(self, now):
        d = self.active_accum
        if self.active_since is not None:
            d += now - self.active_since
        return d

    def combat_of(self, name):
        return bool(self.combat.get(name))

    def current(self):
        """(duration, in_combat) — cheap, reflects the latest clock state."""
        with self.lock:
            return self._effective_duration(time.time()), self.in_combat

    def snapshot(self):
        """Return (duration, in_combat, [PlayerAgg sorted by total desc])."""
        with self.lock:
            duration = self._effective_duration(time.time())
            rows = sorted(self.players.values(), key=lambda p: -p.total)
            import copy
            return duration, self.in_combat, [copy.copy(p) for p in rows]


class RiftRecorder:
    """Captures one rift run for the end-of-rift report.

    Fed the same hit/heal stream as PartySession but bounded by the rift
    itself: entering starts phase 1 (trash), the boss pull starts phase 2,
    the kill freezes both into a report. A run without a kill produces
    nothing. Counts every player the hook sends, ignoring the meter's
    party/all mode, which can change mid-rift."""

    PHASE_LABELS = ("Phase de faille", "Phase du boss")
    # The rift flag (read every 0.4 s) can drop before the boss bar (2/s,
    # up through the death). Once the boss is pulled, the rift's end waits
    # this long for the kill, then reports anyway as unconfirmed.
    CLOSE_GRACE = 20.0

    def __init__(self):
        self.lock = threading.Lock()
        self.active = False
        self.phase = 0
        self._closing_at = None     # the rift flag dropped mid-boss, at
        self._phases = [self._new_phase(), self._new_phase()]
        # (timestamp, phase, "hit"|"heal", event) — lets the boss pull move
        # the opening burst across the phase boundary
        self._recent: deque = deque(maxlen=RECENT_EVENT_MAX)
        # skill key -> display name, across the whole rift
        self.skill_names: dict[str, str] = {}

    @staticmethod
    def _new_phase():
        # skills / heals per player (below), elements per phase
        return {"players": {}, "elements": defaultdict(float),
                "start": 0.0, "end": 0.0}

    @staticmethod
    def _player_of(ph, name):
        p = ph["players"].get(name)
        if p is None:
            # the per-skill tables: [hits, total, crits] (+ the healing's
            # self-share), PlayerAgg's shape
            p = {"name": name, "total": 0.0, "hits": 0, "crits": 0,
                 "kills": 0, "heal": 0.0, "heal_landed": 0.0,
                 "skills": defaultdict(lambda: [0, 0.0, 0]),
                 "heals": defaultdict(lambda: [0, 0.0, 0, 0.0]),
                 "elements": defaultdict(lambda: [0, 0.0]),
                 # each skill's damage by element: its colour in the report
                 "skillEl": defaultdict(lambda: defaultdict(float))}
            ph["players"][name] = p
        return p

    def _apply(self, ph, kind, ev, sign):
        """Add (or, for the phase-boundary rewind, subtract) one event. Every
        stat is a plain sum so the rewind is exact."""
        p = self._player_of(ph, ev.get("player") or "?")
        amount = sign * float(ev.get("amount", 0.0))
        sid, nm = _skill_ident(ev)
        if nm and sid not in self.skill_names:
            self.skill_names[sid] = nm
        if kind == "hit":
            p["total"] += amount
            p["hits"] += sign
            p["crits"] += sign * int(ev.get("crit", 0))
            p["kills"] += sign * int(ev.get("kill", 0))
            el = ev.get("element") or "?"
            ph["elements"][el] += amount
            s = p["skills"][sid]
            s[0] += sign; s[1] += amount; s[2] += sign * int(ev.get("crit", 0))
            e = p["elements"][el]
            e[0] += sign; e[1] += amount
            p["skillEl"][sid][el] += amount
        else:
            p["heal"] += amount
            p["heal_landed"] += sign * float(ev.get("landed", 0.0))
            h = p["heals"][sid]
            h[0] += sign; h[1] += amount; h[2] += sign * int(ev.get("crit", 0))
            h[3] += amount if ev.get("self") else 0.0

    def set_rift(self, state: bool):
        """What became of the recording: "start", "closing" (the boss was
        pulled: waiting for the kill), "abandoned" (left before the boss),
        or None (nothing was recording)."""
        with self.lock:
            if state:
                self.active = True
                self.phase = 0
                self._closing_at = None
                self._phases = [self._new_phase(), self._new_phase()]
                self._phases[0]["start"] = time.time()
                self._recent.clear()
                return "start"
            if not self.active:
                return None
            if self.phase == 1 and self.CLOSE_GRACE > 0:
                if self._closing_at is None:
                    self._closing_at = time.time()
                return "closing"
            # Leaving mid-run (the report wasn't taken) abandons it.
            self.active = False
            return "abandoned"

    def on_zone(self):
        """A loading screen (wipe or walk-out) drops the recording unless
        only its kill is awaited. True when a recording was dropped."""
        with self.lock:
            if self.active and self._closing_at is not None:
                return False
            dropped = self.active
            self.active = False
            return dropped

    def tick(self):
        """Called on the hook's heartbeat: a rift whose flag dropped during
        the boss and whose kill never came is reported once the grace is
        over, marked unconfirmed. None otherwise."""
        with self.lock:
            due = (self.active and self._closing_at is not None
                   and time.time() - self._closing_at > self.CLOSE_GRACE)
        if not due:
            return None
        report = self.on_boss_kill()
        if report is not None:
            report["unconfirmed"] = True
        return report

    def record(self, kind, ev: dict):
        """kind is "hit" or "heal" (nullified hits already filtered out)."""
        with self.lock:
            if not self.active or self._closing_at is not None:
                return              # nothing recording, or the rift is over
            now = time.time()
            self._recent.append((now, self.phase, kind, ev))
            self._apply(self._phases[self.phase], kind, ev, 1)

    def on_boss_pull(self, backlag=BOSS_PULL_BACKLAG_SECS):
        """The boss healthbar lags the pull (fetchBosses is a 2/s timer), so
        the opening burst was recorded as trash: move the last few seconds
        into the boss phase."""
        with self.lock:
            if not self.active or self.phase != 0:
                return
            self.phase = 1
            now = time.time()
            boundary = now
            cutoff = now - backlag
            for ts, ph, kind, ev in self._recent:
                if ph == 0 and ts >= cutoff:
                    self._apply(self._phases[0], kind, ev, -1)
                    self._apply(self._phases[1], kind, ev, 1)
                    boundary = min(boundary, ts)
            # Boundary at the earliest moved event, so durations match totals.
            self._phases[0]["end"] = boundary
            self._phases[1]["start"] = boundary

    def on_boss_kill(self):
        """The kill that ended the fight. Returns the finished report as plain
        data (safe across threads), or None if nothing was recording. Taking
        it stops the recording."""
        with self.lock:
            if not self.active:
                return None
            self.active = False
            # the rift's end, if the flag came first, not the kill's signal
            now = self._closing_at or time.time()
            self._closing_at = None
            self._phases[self.phase]["end"] = now
            phases = []
            used_skills = set()
            for label, ph in zip(self.PHASE_LABELS, self._phases):
                players = sorted((dict(p) for p in ph["players"].values()),
                                 key=lambda p: -p["total"])
                # The rewind leaves float dust and all-zero rows: drop them.
                players = [p for p in players
                           if p["total"] > 0.5 or p["heal"] > 0.5]
                for p in players:
                    for key in ("skills", "heals", "elements"):
                        p[key] = {k: list(v) for k, v in p[key].items()
                                  if abs(v[1]) > 0.5}
                    # a skill's element: the one it did the most damage in
                    p["skillEl"] = {k: max(v, key=v.get)
                                    for k, v in p["skillEl"].items()
                                    if k in p["skills"] and v}
                    used_skills.update(p["skills"])
                    used_skills.update(p["heals"])
                total = sum(p["total"] for p in players)
                heal = sum(p["heal"] for p in players)
                heal_landed = sum(p["heal_landed"] for p in players)
                elements = sorted(((el, amt) for el, amt
                                   in ph["elements"].items() if amt > 0.5),
                                  key=lambda kv: -kv[1])
                start, end = ph["start"] or now, ph["end"] or now
                phases.append({"label": label,
                               "duration": max(0.0, end - start),
                               "players": players, "total": total,
                               "heal": heal, "heal_landed": heal_landed,
                               "elements": elements})
            # `skill_names` outlives one rift: only this report's names
            return {"at": now, "phases": phases,
                    "skill_names": {sid: nm for sid, nm
                                    in self.skill_names.items()
                                    if sid in used_skills}}


class DungeonRecorder(RiftRecorder):
    """The rift recorder for a dungeon (exploration, then boss), bounded by
    the game's dungeon state — see DungeonTracker."""

    PHASE_LABELS = ("Exploration", "Phase du boss")
    CLOSE_GRACE = 0             # its end comes from the dungeon's own state


# The dungeon difficulty as the instance lobby stores it (measured: the value
# followed the Normal/Difficile toggle in the lobby).
DUNGEON_DIFFICULTIES = {0: "Normal", 1: "Vétéran", 2: "Héroïque"}   # the game's names


# How long a difficulty seen in a lobby is trusted for the run that follows.
DUNGEON_LOBBY_TTL = 30 * 60


# A run left this soon without reaching the boss is not worth keeping.
DUNGEON_MIN_SECS = 30


class DungeonTracker:
    """Follows one dungeon run from the hook's `dungeon` messages.

    Measured 2026-09-28: the player's DungeonContext state goes Explo ->
    BossStart -> BossPhase -> BossWin; its `end` is the instance clock at the
    kill, i.e. the run's time (`start` stays -1 on a client). The difficulty
    only exists on the lobby, which vanishes at launch, so the last one seen
    is remembered.

    Runs on the hook's thread; a finished run is handed to the app."""

    def __init__(self, world):
        self.world = world
        self.rec = DungeonRecorder()
        self.lobbies = {}           # activity id -> (difficulty, seen at)
        self.run = None

    def record(self, kind, ev):
        self.rec.record(kind, ev)

    def update(self, d):
        now = time.time()
        for lb in d.get("lobbies") or ():
            if lb.get("a") is not None:
                self.lobbies[lb["a"]] = (lb.get("d"), now)
        in_dungeon = d.get("type") == "st.activity.Dungeon"
        kind = d.get("kind")
        if self.run and (not in_dungeon or kind != self.run["kind"]):
            self._leave()
        if not in_dungeon:
            return
        if self.run is None:
            diff, seen = self.lobbies.get(kind, (None, 0))
            if now - seen > DUNGEON_LOBBY_TTL:
                diff = None
            self.run = {"kind": kind, "boss": d.get("bossId") or "",
                        "difficulty": diff, "state": None, "wipes": 0,
                        "deaths": 0, "clock": 0.0, "done": False,
                        "boss_seen": False, "loot": [], "file": None}
            self.rec.set_rift(True)
            print(f"[dungeon] entered {kind} (difficulty "
                  f"{DUNGEON_DIFFICULTIES.get(diff, '?')})", file=sys.stderr)
        run = self.run
        if isinstance(d.get("now"), (int, float)):
            run["clock"] = float(d["now"])
        ctx = next((c for c in d.get("playerCtx") or ()
                    if c.get("type") == "st.activity.DungeonContext"), None)
        if not ctx:
            return
        if isinstance(ctx.get("deaths"), int):
            run["deaths"] = ctx["deaths"]
        state = ctx.get("state")
        if state == run["state"]:
            return
        print(f"[dungeon] state {run['state']} -> {state} at "
              f"{run['clock']:.1f}s", file=sys.stderr)
        run["state"] = state
        if state in ("BossStart", "BossPhase") and not run["boss_seen"]:
            run["boss_seen"] = True
            self.rec.on_boss_pull(backlag=1.0)
        elif state == "BossLoose":
            run["wipes"] += 1
        elif state == "BossWin" and not run["done"]:
            end = ctx.get("end")
            self._finish("victoire",
                         end if isinstance(end, (int, float)) and end > 0
                         else run["clock"])

    def pickup(self, p):
        """An item that entered the local player's bags (the hook's inventory
        sweep). Kept on the run, tagged with the phase it came in: after the
        boss's death it is the reward chest's."""
        run = self.run
        if run is None or not p.get("item"):
            return
        phase = ("coffre" if run["done"] else
                 "boss" if run["boss_seen"] else "exploration")
        run["loot"].append({"item": p["item"], "rarity": p.get("rarity"),
                            "level": p.get("level"),
                            "count": int(p.get("count") or 1),
                            "phase": phase})
        print(f"[dungeon] loot ({phase}): {p.get('count')}x {p['item']} "
              f"{p.get('rarity') or ''}", file=sys.stderr)
        # A finished run is already saved: rewrite it with the chest's loot.
        app = _APP["ref"]
        if run["done"] and run["file"] and app is not None:
            app.on_dungeon_loot(run["file"], list(run["loot"]))

    def _leave(self):
        run, self.run = self.run, None
        if run["done"]:
            return
        if run["clock"] < DUNGEON_MIN_SECS and not run["boss_seen"]:
            self.rec.on_zone()
            return
        self.run = run                  # _finish reads it
        self._finish("échec" if run["wipes"] or run["boss_seen"] else "abandon",
                     run["clock"])
        self.run = None

    def disconnect(self):
        if self.run is not None:
            self._leave()

    def _finish(self, result, duration):
        run = self.run
        run["done"] = True
        report = self.rec.on_boss_kill()
        if report is None:
            return
        _stamp_report_classes(report, self.world)
        run["file"] = f"run-{time.strftime('%Y%m%d-%H%M%S')}.json"
        report.update({
            "file": run["file"], "loot": list(run["loot"]),
            "type": "dungeon", "kind": run["kind"],
            "name": dungeon_name(run["kind"]), "boss": run["boss"],
            "difficulty": run["difficulty"], "result": result,
            "duration": float(duration), "deaths": run["deaths"],
            "wipes": run["wipes"]})
        print(f"[dungeon] {run['kind']} {result} in {duration:.1f}s "
              f"({run['deaths']} deaths, {run['wipes']} wipes)", file=sys.stderr)
        app = _APP["ref"]
        if app is not None:
            app.on_dungeon_run(report)


class WorldSnapshot:
    """Which class every player is (damage events carry none), from the
    hook's `shard` message. Tags are kept after a player leaves."""

    def __init__(self):
        self._lock = threading.Lock()
        self.classes = {}           # name -> "Warrior", "Mage"...

    def set_shard(self, rows):
        with self._lock:
            for r in rows or ():
                if r.get("n") and r.get("k"):
                    self.classes[r["n"]] = r["k"]

    def class_of(self, name):
        with self._lock:
            return self.classes.get(name)


class GameUIState:
    """What the hook says about the game: whether a rift is on, and which
    shard (st.GameLayer.serverName, e.g. "Sfojuxa3386_6601_na": a server,
    an instance, the region; stored raw, it is compared, not shown)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._rift = False
        self._dungeon = False       # inside a dungeon (DungeonTracker)
        self._server = None         # None until the hook reports it

    def set_server(self, name):
        with self._lock:
            self._server = name or None

    def server(self):
        with self._lock:
            return self._server

    def set_rift(self, state: bool):
        with self._lock:
            self._rift = bool(state)

    def in_rift(self) -> bool:
        with self._lock:
            return self._rift

    def set_dungeon(self, state: bool):
        with self._lock:
            self._dungeon = bool(state)

    def in_dungeon(self) -> bool:
        with self._lock:
            return self._dungeon

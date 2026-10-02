"""Combat accounting: the damage/heal session, rift and dungeon recording, and
what the hook says about the world."""
from __future__ import annotations

import sys
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

from common import (
    BOSS_PULL_BACKLAG_SECS, COMBAT_TIMEOUT_SECS, HISTORY_MIN_EVENTS,
    HISTORY_MIN_SECS, RECENT_EVENT_MAX, _OVERLAY, _class_tag, _n)
from gamedata import _summon_label, dungeon_name


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------
def _skill_ident(ev):
    """(breakdown key, display name) for one hit or heal event.

    A summon's hit carries `pet` — its raw Unit.kind. The damage already
    merged into the owner's row upstream, so the breakdown is the only place
    left that can show a pet was responsible for part of it.

    The KEY is prefixed with the raw kind (stable, and the same on a
    non-English client), which also keeps a pet's ability separate from an
    identically named one of the player's own. The NAME gets the sheet's
    display name — "Nightling Terror: Attack".

    Module-level because the rift recorder now keeps its own per-skill tables
    for the history dataset, and two copies of this rule is two places for a
    pet's damage to end up filed under the player's own ability.
    """
    sid = ev.get("skill", "?")
    nm = ev.get("name")
    pet = ev.get("pet")
    if pet:
        sid = f"{pet}:{sid}"
        label = _summon_label(pet)
        nm = f"{label}: {nm}" if nm else label
    return sid, nm


def _stamp_report_classes(report, world):
    """Freeze each player's class acronym into a finished rift report.

    Done once, at the kill, because the report is saved to disk and re-opened
    later — by then the world sweep has long forgotten a stranger who was in
    the rift, and the card would show a row of blanks. A player the sweep never
    saw gets "" and simply has no acronym."""
    for ph in report.get("phases", ()):
        for p in ph.get("players", ()):
            p["cls"] = _class_tag(world.class_of(p.get("name")))


def _report_name(p):
    """`Brudr (War)` for the report — the acronym only when one is known."""
    name = p.get("name") or "?"
    return f"{name} ({p['cls']})" if p.get("cls") else name


def _overheal_note(d, fmt=" ({:.0f}% en excès)"):
    """The overheal clause for a report dict, or "" when there is none to give.

    Rift reports are reloaded from JSON on disk, and a file written before
    healing meant RAW healing carries `heal` but no `heal_landed`. Treating
    that absence as zero would stamp every archived report 100% overheal, so
    an old report simply says nothing about overhealing — which is the truth
    about what it recorded."""
    landed = d.get("heal_landed")
    heal = d.get("heal") or 0.0
    if landed is None or heal <= 0.5:
        return ""
    return fmt.format(_overheal_pct(heal, landed))


# A window shorter than this has no rate worth showing. The boss-phase
# rewind can leave a phase a few milliseconds long, and dividing by that
# produces a seven-figure DPS that is not a number about anything.
RATE_MIN_SECS = 0.5


def _rate(amount, duration):
    """`amount` per second, or None when the window is too short to divide by.

    None rather than 0.0, because "no rate" and "a rate of zero" are different
    facts and the card renders them differently — one shows the total instead,
    the other shows a real zero."""
    if not duration or duration < RATE_MIN_SECS:
        return None
    return (amount or 0.0) / duration


def _rate_text(amount, duration, unit):
    """"12 345 DPS", or None when there is no rate to state."""
    r = _rate(amount, duration)
    return None if r is None else f"{_n(r)} {unit}"


def _overheal_pct(total, landed):
    """Share of `total` healing that restored no health, as a percentage.

    Clamped at 0 because the two figures come from different observations —
    a health rise can be attributed to a heal whose estimated size is smaller
    than the rise itself (a regen tick landing inside a heal's match window,
    say), and "-3% overheal" is not a thing to show anyone."""
    if not total or total <= 0.0:
        return 0.0
    return max(0.0, (total - landed) / total * 100.0)


class HealSizeEstimator:
    """How big was that heal? The client is never told, so this estimates it.

    Measured 2026-08-03 (`frida/run_heal.py`, 40 heal events across 6 healers
    and 4 skills): of the fifteen heal entry points in the build, ONLY
    `ent.Unit.playHitHealFX` runs client-side, and its `HitData.amount` reads
    0.000. `receiveHeal`, `computeHeal`, `evalHeal`, the four `*HealEval`
    callbacks, `applyHeal`, `rpcDisplayHeal(__impl)` and
    `ui.hud.EffectsFeed.displayHeal` never fire on a client at all. The only
    heal quantity observable here is the RISE in the target's replicated
    health — which is zero when the target is already full.

    A heal's size is therefore estimated as the HIGH-WATER MARK of what that
    player's casts of that skill have been seen to restore. A cast on a target
    missing more health than the heal restores lands in full, so the largest
    observation converges on the true per-cast value from below; every smaller
    one is a cast that was capped by the target's missing health, and every
    zero is a cast that was capped completely.

    Deliberately the maximum, not a mean or a quantile. Capping biases
    observations DOWN and there is no way to tell a capped observation from an
    uncapped one — `ent.UnitAttributes.maxHealth` reads 0 for heroes (measured
    in the same session), so "how hurt was the target" isn't available either.
    Averaging would report a healer as weaker the healthier their party was,
    which is the exact bug this replaces. The known cost is crits: once a skill
    has been seen to crit it is credited its crit value on every cast, so a
    crit-heavy healing build reads somewhat high.

    The window bounds that across a session — levels, gear and talent changes
    all move a skill's real value, and a lifetime maximum would pin the
    estimate to the best it ever was.

    Called only from the hook's message thread (the same thread that feeds
    PartySession), so it needs no lock of its own.
    """

    WINDOW = 64          # observations kept per (player, skill)
    UNDER_MIN_HP = 3     # a smaller shortfall than this is not reported

    def __init__(self, specs=None):
        self._obs: dict[tuple, deque] = defaultdict(
            lambda: deque(maxlen=self.WINDOW))
        # skill id -> {step index: [effect spec, ...]} out of the game's own
        # data.cdb (analysis_out/heal_specs.json). This is what makes a heal
        # on a full-health target countable at all, so its absence is worth
        # saying out loud rather than quietly falling back.
        self._specs = specs or {}
        self._computed = 0      # heals sized from the game's own numbers
        self._guessed = 0       # ...and heals that fell back to observation
        self._unsized = 0       # ...and heals nothing could size
        # skill -> (landed/computed, landed, computed) for the worst case seen
        self._audit: dict[str, tuple] = {}

    def size_from_spec(self, ev):
        """The heal's real size, computed the way the game computes it.

        `dyn` heals carry their amount in BaseSkill.dynVal1-3, which the server
        replicates; `scale` heals are a ratio on one of the caster's
        attributes. Both arrive on the event from the hook. Returns None when
        this skill isn't in the table, or when the inputs it needs are missing
        — a summon's attributes, say, or a step index that didn't match."""
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
            total += amount
        return total if total > 0 else None

    def stamp(self, ev: dict) -> None:
        """Fold one heal event in and fill in its raw size.

        `landed` (what the health bar actually moved) arrives from the hook;
        `amount` leaves as the estimated size of the heal itself. Every
        downstream consumer already reads `amount`, so healing totals become
        raw healing without any of them knowing about the estimate."""
        landed = float(ev.get("landed", ev.get("amount", 0.0)) or 0.0)
        ev["landed"] = landed
        if not ev.get("est"):
            ev["amount"] = landed          # regen: observed AS the rise
            return
        obs = self._obs[(ev.get("player") or "?", ev.get("skill") or "?")]
        if landed > 0:
            obs.append(landed)
        # The game's own numbers first — they are right on the very first cast
        # and don't care whether the target had room for the heal. Observation
        # is only the fallback for the skills the table can't size.
        spec = self.size_from_spec(ev)
        if spec is not None:
            self._computed += 1
            ev["sized"] = "spec"
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
        # Ground truth, collected from ordinary play rather than a probe: a
        # heal that LANDED in full is a direct measurement of what that heal
        # was worth, so a computed size below it means the formula is wrong.
        # (Above it is expected and means nothing — the target was topped off.)
        if ev.get("sized") == "spec" and landed > 0:
            key = ev.get("skill") or "?"
            worst = self._audit.get(key)
            ratio = landed / spec if spec > 0 else 0.0
            if worst is None or ratio > worst[0]:
                self._audit[key] = (ratio, landed, spec)

    def drain_report(self):
        """A one-line summary for the log, or None when nothing has healed.

        The `under` entries are the ones worth reading: a skill whose computed
        size came out BELOW what it was measured to restore is a formula that
        needs fixing, not a rounding artifact."""
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
    heal_hits: int = 0
    # skill -> [hits, total, crits]  (damage)
    skills: dict[str, list] = field(default_factory=lambda: defaultdict(lambda: [0, 0.0, 0]))
    # skill -> [hits, total, crits, self_total]  (healing). The fourth column
    # is what makes a healing bar splittable: how much of that skill's healing
    # the caster put on themselves.
    heals: dict[str, list] = field(default_factory=lambda: defaultdict(lambda: [0, 0.0, 0, 0.0]))
    # element -> [hits, total]
    elements: dict[str, list] = field(default_factory=lambda: defaultdict(lambda: [0, 0.0]))
    # unit kind -> damage THIS player dealt to it. Per player rather than per
    # session because a history dataset can be filtered down to your party,
    # and a session-wide tally would then claim the whole shard's damage on a
    # boss above rows that only add up to your group's share.
    targets: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    first_time: float = 0.0
    last_time: float = 0.0

    def record(self, skill, element, amount, crit, kill, now, target=None):
        if self.first_time == 0.0:
            self.first_time = now
        self.last_time = now
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
        # Absent on a hit whose target wasn't a unit the hook could name; the
        # dataset then falls back to its zone name, which is still true.
        if target:
            self.targets[target] += amount

    def record_heal(self, skill, amount, crit, landed=0.0, is_self=False):
        self.heal_total += amount
        self.heal_landed += landed
        self.heal_hits += 1
        if is_self:
            self.heal_self += amount
        s = self.heals[skill]
        s[0] += 1; s[1] += amount; s[2] += crit
        s[3] += amount if is_self else 0.0

    @property
    def overheal_pct(self):
        """Share of this player's healing that restored no health.

        Zero — not "unknown" — when they have not healed at all: a row with no
        healing has nothing to have wasted."""
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
        # (timestamp, "hit"|"heal", event) for the last few seconds, so a
        # boss-pull reset can rewind instead of wiping. Only what was actually
        # recorded goes in here — anything the capture window rejected was never
        # part of the encounter and must not reappear.
        self._recent: deque = deque(maxlen=RECENT_EVENT_MAX)
        # Called with the finished encounter, as plain data, every time one is
        # thrown away. Set by the overlay when combat history is on; None means
        # a reset is exactly what it always was. Never called under the lock —
        # the receiver writes a file, and holding the data path open across
        # disk IO would stall the damage hook.
        self.archive_hook = None
        # Which players belong in an archived dataset — the meter's party/all
        # mode, supplied by the overlay. Applied inside _freeze, BEFORE any
        # total is summed, so a party dataset's totals, event count, target
        # tally and minimum-size floor all describe the same set of people.
        # None keeps everyone (which is what a rift report does deliberately).
        self.archive_filter = None

    def set_capture_window(self, seconds):
        """Parse mode: take data for exactly `seconds` from now, then stop.
        Enforced here on the data path rather than by the UI tick, so the sample
        is the length asked for however the 250 ms refresh happens to land.
        None clears the limit."""
        with self.lock:
            now = time.time()
            self.capture_start = None if seconds is None else now
            self.capture_until = None if seconds is None else now + seconds

    def _capturing(self, now):
        return self.capture_until is None or now <= self.capture_until

    def _effective_duration(self, now):
        """Seconds to divide by for DPS.

        Normally that's in-combat time, so a pull's DPS isn't diluted by the
        walk to it. Inside a parse window it's wall-clock elapsed instead: the
        window *is* the measurement, so downtime has to count against you or two
        runs aren't comparable — and the game's isInCombat flag drops between
        pulls, which would otherwise inflate a 60 s parse by however much of it
        the flag happened to miss."""
        if self.capture_start is not None:
            return max(0.001, min(now, self.capture_until) - self.capture_start)
        return max(0.001, self._duration(now)) if self.enc_start else 0.0

    def _reset(self, now):
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
            # The lull-reset is suppressed inside a parse window: a quiet
            # stretch mid-parse is part of the sample, not the start of a new
            # encounter, and wiping 40 seconds in would ruin the run.
            if (self.capture_until is None and self.last_hit
                    and (now - self.last_hit) > self.timeout):
                self._reset(now)          # new encounter after a long lull
            if self.enc_start == 0.0:
                self.enc_start = now
            self.last_hit = now
            self._recent.append((now, "hit", ev))
            self._apply_hit(self._player_for(ev), ev, now)

    def _apply_hit(self, p, ev, ts):
        """Record one hit. Shared with the boss-pull rewind, so a replayed hit
        lands exactly where the live one did."""
        p.record(self._skill_of(ev), ev.get("element", "?"),
                 float(ev.get("amount", 0.0)), int(ev.get("crit", 0)),
                 int(ev.get("kill", 0)), ts, ev.get("target"))

    def record_heal(self, ev: dict):
        # Heals are recorded but never drive encounter boundaries: an
        # out-of-combat potion/regen must not roll the meter into a fresh
        # encounter (damage does that), so last_hit stays untouched.
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
            # Past a parse window's cutoff the clock stops even mid-fight, so
            # the duration the meter shows is the sample's, not the pull's.
            if not self._capturing(now):
                active = False
            if active and self.active_since is None:
                self.active_since = now
            elif not active and self.active_since is not None:
                self.active_accum += now - self.active_since
                self.active_since = None
            self.in_combat = active

    def _freeze(self, now):
        """The live encounter as plain, JSON-safe data — or None when there
        isn't enough of one to be worth keeping.

        Called with the lock held and returning nothing but built-ins, so the
        caller can hand it to a disk writer on another thread without the
        aggregates moving underneath it.
        """
        if not self.players:
            return None
        # Filter FIRST. Every number below is summed over `rows`, so a party
        # dataset is internally consistent — including the floor, which then
        # asks "did YOUR GROUP fight for long enough", not "did anything
        # happen on this shard".
        rows = sorted(self.players.values(), key=lambda p: -p.total)
        if self.archive_filter is not None:
            try:
                rows = list(self.archive_filter(rows))
            except Exception as e:
                print(f"[meter] archive filter failed ({e}); keeping every "
                      "player", file=sys.stderr)
        if not rows:
            return None
        duration = self._effective_duration(now)
        events = sum(p.hits + p.heal_hits for p in rows)
        if duration < HISTORY_MIN_SECS or events < HISTORY_MIN_EVENTS:
            return None
        targets: dict[str, float] = defaultdict(float)
        for p in rows:
            for kind, amt in p.targets.items():
                targets[kind] += amt
        return {
            "at": now,
            "start": self.enc_start or now,
            "duration": duration,
            "events": events,
            "total": sum(p.total for p in rows),
            "heal": sum(p.heal_total for p in rows),
            "heal_landed": sum(p.heal_landed for p in rows),
            "targets": {k: v for k, v in targets.items() if v > 0.5},
            # Only the names this encounter actually used: skill_names is
            # never cleared (a display name is a fact about the game, not
            # about the fight), and shipping the whole session's table with
            # every dataset would grow every file for nothing.
            "skill_names": {
                sid: nm for sid, nm in self.skill_names.items()
                if any(sid in p.skills or sid in p.heals for p in rows)},
            "players": [{
                "name": p.name,
                "is_me": bool(p.is_me),
                "in_party": bool(p.in_party),
                "total": p.total, "hits": p.hits, "crits": p.crits,
                "kills": p.kills,
                "heal": p.heal_total, "heal_landed": p.heal_landed,
                "heal_self": p.heal_self, "heal_hits": p.heal_hits,
                "first": p.first_time, "last": p.last_time,
                "skills": {k: list(v) for k, v in p.skills.items()},
                "heals": {k: list(v) for k, v in p.heals.items()},
                "elements": {k: list(v) for k, v in p.elements.items()},
                # Per player as well as summed above: it answers who was on
                # the boss and who was on the adds, and it makes the file
                # self-checking — the dataset's `targets` must be the sum of
                # these, whatever filter produced it.
                "targets": dict(p.targets),
            } for p in rows],
        }

    def _emit_archive(self, frozen):
        """Hand a finished encounter to the archive, outside the lock. A
        broken hook costs the dataset, never the reset that was asked for."""
        if frozen is None or self.archive_hook is None:
            return
        try:
            self.archive_hook(frozen)
        except Exception as e:
            print(f"[meter] couldn't archive the encounter: {e}",
                  file=sys.stderr)

    def reset(self):
        frozen = None
        with self.lock:
            now = time.time()
            frozen = self._freeze(now)
            self._reset(now)
            self.last_hit = 0.0
            self.in_combat = False
            # A reset always returns to live capture — and so to in-combat DPS.
            self.capture_until = self.capture_start = None
            self._recent.clear()
            self.epoch += 1
        self._emit_archive(frozen)

    def reset_keeping_recent(self, backlag=BOSS_PULL_BACKLAG_SECS):
        """Reset the encounter but carry the last `backlag` seconds forward.

        For the boss-pull reset. The healthbar the pull is detected from lags
        the pull itself, so a plain reset lands *after* the opening burst and
        deletes it — the single most interesting part of the parse. Replaying
        the buffered events with their ORIGINAL timestamps keeps the numbers,
        the per-player first/last times and the encounter start honest, rather
        than restamping everything to the moment the bar appeared and reporting
        a burst that took four seconds as instantaneous.

        Returns how many events were carried over, for the log."""
        frozen = None
        with self.lock:
            now = time.time()
            cutoff = now - backlag
            keep = [e for e in self._recent if e[0] >= cutoff]
            # The trash phase, archived before the pull takes the meter. The
            # carried events are counted in BOTH this dataset and the next
            # one: they are already in these aggregates and there is no exact
            # way to subtract them back out of a PlayerAgg. `carried` says so
            # in the file rather than leaving a few seconds of overlap for
            # someone to discover by adding two datasets up.
            frozen = self._freeze(now)
            if frozen is not None:
                frozen["carried"] = len(keep)
                frozen["carried_secs"] = backlag
            self._reset(now)
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
            # Damage was landing, so the player was in combat for the whole
            # replayed stretch. Without this the duration clock would only start
            # at the next UI tick and those seconds would be missing from the
            # divisor — inflating the DPS of the very burst we just rescued.
            if self.enc_start:
                self.active_since = self.enc_start
                self.in_combat = True
        self._emit_archive(frozen)
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

    Fed the same hit/heal stream as PartySession but never reset by the
    player — its boundaries are the rift's own. Entering the rift starts
    phase 1 (the trash), the boss-pull edge starts phase 2 (the boss), and
    the kill that ends the fight freezes both into a report. Two phases and
    not a running meter, because that's the question the report answers:
    who carries the AoE clear and who carries the single-target, which are
    different players on purpose.

    A run that doesn't end in a kill — walking out, a wipe's loading screen —
    produces nothing. Half a rift isn't a rift report.

    Aggregates everything the hook sends rather than the meter's party/all
    mode: the mode can change mid-rift (the rift prompt exists to change it),
    and a report whose phase 1 and phase 2 counted different sets of players
    would be comparing nothing with nothing."""

    PHASE_LABELS = ("Phase de faille", "Phase du boss")

    def __init__(self):
        self.lock = threading.Lock()
        self.active = False
        self.phase = 0
        self._phases = [self._new_phase(), self._new_phase()]
        # (timestamp, phase, "hit"|"heal", event) — kept so the boss-pull
        # edge can move the opening burst across the phase boundary, same
        # trick (and same measured bar lag) as reset_keeping_recent().
        self._recent: deque = deque(maxlen=RECENT_EVENT_MAX)
        # skill key -> display name, for the per-skill tables below. Kept
        # across the whole rift rather than per phase: a name is a fact about
        # the game, and the boss phase should not have to re-learn one the
        # trash phase already saw.
        self.skill_names: dict[str, str] = {}

    @staticmethod
    def _new_phase():
        # `skills`/`heals` are per player (below); `elements` and `targets`
        # are per phase, because "what did this phase consist of" is a
        # question about the phase and not about any one player.
        return {"players": {}, "elements": defaultdict(float),
                "targets": defaultdict(float), "start": 0.0, "end": 0.0}

    @staticmethod
    def _player_of(ph, name):
        p = ph["players"].get(name)
        if p is None:
            # The per-skill tables carry the same [hits, total, crits] (plus
            # the healing self-share) shape PlayerAgg uses, so a rift dataset
            # and an ordinary encounter dataset render through one code path.
            p = {"name": name, "total": 0.0, "hits": 0, "crits": 0,
                 "kills": 0, "heal": 0.0, "heal_landed": 0.0,
                 "heal_hits": 0,
                 "skills": defaultdict(lambda: [0, 0.0, 0]),
                 "heals": defaultdict(lambda: [0, 0.0, 0, 0.0]),
                 "elements": defaultdict(lambda: [0, 0.0])}
            ph["players"][name] = p
        return p

    def _apply(self, ph, kind, ev, sign):
        """Add (or, for the phase-boundary rewind, subtract) one event. Every
        stat is a plain sum, which is what makes the rewind exact — including
        the per-skill tables, which is why they are sums and not counters."""
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
            tk = ev.get("target")
            if tk:
                ph["targets"][tk] += amount
        else:
            p["heal"] += amount
            p["heal_landed"] += sign * float(ev.get("landed", 0.0))
            p["heal_hits"] += sign
            h = p["heals"][sid]
            h[0] += sign; h[1] += amount; h[2] += sign * int(ev.get("crit", 0))
            h[3] += amount if ev.get("self") else 0.0

    def set_rift(self, state: bool):
        with self.lock:
            if state:
                self.active = True
                self.phase = 0
                self._phases = [self._new_phase(), self._new_phase()]
                self._phases[0]["start"] = time.time()
                self._recent.clear()
            else:
                # Leaving normally happens after the kill, when the report has
                # already been taken; leaving mid-run abandons the recording.
                self.active = False

    def on_zone(self):
        """A loading screen means the player left the instance — a wipe or a
        walk-out. Whatever was building is not a finished rift."""
        with self.lock:
            self.active = False

    def record(self, kind, ev: dict):
        """kind is "hit" or "heal". Hits arrive already filtered of nullified
        damage — the caller drops those before the meter sees them too."""
        with self.lock:
            if not self.active:
                return
            now = time.time()
            self._recent.append((now, self.phase, kind, ev))
            self._apply(self._phases[self.phase], kind, ev, 1)

    def on_boss_pull(self, backlag=BOSS_PULL_BACKLAG_SECS):
        """The healthbar the pull is detected from lags the pull itself
        (fetchBosses is a 2/s timer), so the opening burst on the boss has
        already been recorded as trash. Move the last few seconds across the
        boundary — measured damage on the boss, miscounted only in which
        column it landed."""
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
            # The boundary is where the earliest moved event landed, not where
            # the bar rose — the durations should agree with the totals.
            self._phases[0]["end"] = boundary
            self._phases[1]["start"] = boundary

    def on_boss_kill(self):
        """The kill that ended the fight. Returns the finished report as plain
        data (safe to hand to the Tk thread), or None if nothing was recording.
        One report per rift: taking it stops the recording, so the walk to the
        exit portal can't dribble into the boss column."""
        with self.lock:
            if not self.active:
                return None
            self.active = False
            now = time.time()
            self._phases[self.phase]["end"] = now
            phases = []
            used_skills = set()
            for label, ph in zip(self.PHASE_LABELS, self._phases):
                players = sorted((dict(p) for p in ph["players"].values()),
                                 key=lambda p: -p["total"])
                # The rewind leaves float dust (and a player who only acted in
                # the moved window ends up all-zero) — drop empty rows rather
                # than showing "0" lines.
                players = [p for p in players
                           if p["total"] > 0.5 or p["heal"] > 0.5]
                # The rewind subtracts, so a skill moved wholesale to the next
                # phase is left behind as a zero row. Same dust, same rule.
                for p in players:
                    for key in ("skills", "heals", "elements"):
                        p[key] = {k: list(v) for k, v in p[key].items()
                                  if abs(v[1]) > 0.5}
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
                               "elements": elements,
                               "targets": {k: v for k, v
                                           in ph["targets"].items()
                                           if v > 0.5}})
            # `skill_names` outlives one rift, so only the names this report
            # can actually use are written into it — see PartySession._freeze.
            return {"at": now, "phases": phases,
                    "skill_names": {sid: nm for sid, nm
                                    in self.skill_names.items()
                                    if sid in used_skills}}


class DungeonRecorder(RiftRecorder):
    """The rift recorder's two-phase capture, for a dungeon: the exploration,
    then the boss. Its boundaries come from the game's dungeon state rather
    than from the boss bar — see DungeonTracker."""

    PHASE_LABELS = ("Exploration", "Phase du boss")


# The dungeon difficulty as the instance lobby stores it (measured: the value
# followed the Normal/Difficile toggle in the lobby).
DUNGEON_DIFFICULTIES = {0: "Normal", 1: "Difficile", 2: "Héroïque"}


# How long a difficulty seen in a lobby is trusted for the run that follows.
DUNGEON_LOBBY_TTL = 30 * 60


# A run left this soon without reaching the boss is not worth keeping.
DUNGEON_MIN_SECS = 30


class DungeonTracker:
    """Follows one dungeon run from the hook's `dungeon` messages.

    Measured (a Manfish Ruins run, 2026-09-28): the active activity is an
    st.activity.Dungeon; its state lives on the player's DungeonContext and
    goes Explo -> BossStart -> BossPhase -> BossWin (then back to Explo); the
    context's `end` is the instance clock at the kill, which is the run's
    time (the clock starts at the instance's creation); `start` stays -1 on a
    client. The difficulty exists only on the instance lobby, which vanishes
    at launch — so the last one seen is remembered for the run that follows.

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
        ov = _OVERLAY["ref"]
        if run["done"] and run["file"] and ov is not None:
            ov.on_dungeon_loot(run["file"], list(run["loot"]))

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
        ov = _OVERLAY["ref"]
        if ov is not None:
            ov.on_dungeon_run(report)


class WorldSnapshot:
    """Who you are, who is in your group, and which class every player is.

    Fed by two hook messages: `hero` (the local hero plus the group roster the
    meter's party filter reads) and `shard` (every player the client holds
    state for, each with its class). The meter's own rows come from damage
    events, which carry no class, so this is where the class tag comes from."""

    def __init__(self):
        self._lock = threading.Lock()
        self.party = frozenset()
        self.local = None
        # name -> class ("Warrior", "Mage", ...). Kept rather than replaced
        # wholesale: a player who leaves the layer shouldn't lose their tag on
        # the meter while their damage is still on it.
        self.classes = {}

    def set_hero(self, name, party):
        with self._lock:
            if name:
                self.local = name
            self.party = frozenset(party or ())

    def set_shard(self, rows):
        with self._lock:
            for r in rows or ():
                if r.get("n") and r.get("k"):
                    self.classes[r["n"]] = r["k"]

    def who(self):
        with self._lock:
            return self.local, self.party

    def class_of(self, name):
        with self._lock:
            return self.classes.get(name)


class GameUIState:
    """Which of the game's own UI windows are open, streamed by the hook.

    The hook watches ui.BaseUI.displayWindow/removeWindow — the game's window
    manager — and reports each window class as it opens and closes. That lets
    the overlay react to the game's UI (escape menu open => unlock) instead of
    making the player remember a key."""

    def __init__(self):
        self._lock = threading.Lock()
        self._open: set[str] = set()
        self._rift = False
        self._unlock_at = None         # when the game's escape menu opened
        self._boss_bar = 0
        self._zone_sig = None
        # The game's own `World._isWorldMap`. None until a zone message has
        # carried it — which is not the same as False, and a history dataset
        # would otherwise call an unknown zone a dungeon.
        self._zone_world_map = None
        # Which shard the character is on — st.GameLayer.serverName, sent by
        # the hook once at attach and then on every change. None until it
        # arrives, which is a real state worth distinguishing from "no shard":
        # the settings panel says "..." rather than claiming to know.
        self._server = None

    def set_zone(self, sig, world_map=None):
        """layer.world.level from the hook — the loaded level's name, sent
        once at attach and then on every change. (Its predecessor,
        Main.getMapId(), turned out to return the machine hostname.)

        `world_map` is the same message's `_isWorldMap`: 1 in an overworld
        region, 0 in a dungeon or a rift. It is what lets a history dataset
        say "Siagarta Overworld" without pattern-matching the path."""
        with self._lock:
            self._zone_sig = sig or None
            self._zone_world_map = (None if world_map is None
                                    else bool(world_map))

    def set_server(self, name):
        """st.GameLayer.serverName from the hook — which shard you are on.

        Measured 2026-08-05: reads "Sfojuxa3386_6601_na", and a relog to
        character select and back moved it to "Snitura2642_6306_na" while the
        zone stayed "World/W1_Siagarta" throughout. So it names the shard, not
        the zone, and the trailing "_na" is the server region.

        Stored raw. Prettifying it would risk two genuinely different shards
        rendering the same, and the whole use of this string is comparing it
        with somebody else's."""
        with self._lock:
            self._server = name or None

    def server(self):
        """The shard name, or None if the hook hasn't reported one yet."""
        with self._lock:
            return self._server


    def zone(self):
        """(sig, world_map) together — read as a pair because a history
        dataset names itself from both, and reading them one lock at a time
        could straddle a loading screen."""
        with self._lock:
            return self._zone_sig, self._zone_world_map

    def set_rift(self, state: bool):
        with self._lock:
            self._rift = bool(state)

    def in_rift(self) -> bool:
        with self._lock:
            return self._rift

    def set_boss_bar(self, count: int):
        """How many of the game's own boss/elite healthbars are on screen.

        The hook reads ui.hud.BossesInfo, so this counts bars the player can
        actually see — it goes to zero when they walk away and the boss resets,
        not just when something dies."""
        with self._lock:
            self._boss_bar = max(0, int(count))


    def clear(self):
        with self._lock:
            self._open.clear()
            # A loading screen tears the HUD down with it, so a bar that was up
            # on the way out must not leave the compass hidden in the new zone.
            self._boss_bar = 0


"""A dungeon boss fight's journal, as the run's page shows it: each hit,
heal and hit taken, a line each, the time since the pull first; and, for a
player picked, what those lines add up to (healing on self or on others,
what the bars moved against what the meter counted).

The lines come from the hook (combat.RiftRecorder._journal_rows): names are
the game's, a unit's kind is named here."""
from __future__ import annotations

import gzip
import json
import sys
from collections import defaultdict
from pathlib import Path

from common import _pretty_id
from gamedata import _fr_names, _summon_label
from i18n import tr

# the lines shown at once, then this many more per click
JOURNAL_PAGE = 500

# the rifts' journals kept (their reports follow the player's own setting)
RIFT_JOURNALS_KEPT = 50


# ---- the files: journal-<report's stem>.json.gz next to the report (its
# lines and its players' classes), gzip as they repeat a lot (~10x)
def journal_path(folder, report_name):
    stem = Path(str(report_name)).name
    if stem.endswith(".json"):
        stem = stem[:-len(".json")]
    return Path(folder) / f"journal-{stem}.json.gz"


def save_journal(folder, report_name, rows, cls):
    try:
        Path(folder).mkdir(parents=True, exist_ok=True)
        journal_path(folder, report_name).write_bytes(gzip.compress(
            json.dumps({"rows": rows, "cls": cls},
                       separators=(",", ":")).encode("utf-8")))
        return True
    except OSError as e:
        print(f"[meter] couldn't save the journal of {report_name}: {e}",
              file=sys.stderr)
        return False


def load_journal(folder, report_name):
    """(lines, classes) of a report's journal; the first ones were plain
    JSON (journal-run-….json), and lines only."""
    gz = journal_path(folder, report_name)
    try:
        data = json.loads(gzip.decompress(gz.read_bytes()).decode("utf-8"))
    except (OSError, ValueError):
        try:
            data = json.loads((Path(folder) / ("journal-" + Path(
                str(report_name)).name)).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
    if isinstance(data, list):
        data = {"rows": data}
    return data.get("rows") or [], data.get("cls") or {}


def remove_journal(folder, report_name):
    for p in (journal_path(folder, report_name),
              Path(folder) / ("journal-" + Path(str(report_name)).name)):
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass


def prune_journals(folder, prefix, keep):
    """At most `keep` journals whose report's name starts with `prefix`,
    the oldest out first."""
    try:
        files = sorted(Path(folder).glob(f"journal-{prefix}*.json.gz"),
                       reverse=True)
    except OSError:
        return
    for p in files[keep:]:
        try:
            p.unlink()
        except OSError:
            pass

KINDS = (("hit", "Dégâts infligés"), ("taken", "Dégâts reçus"),
         ("heal", "Soins"), ("status", "Contrôles"))

# how a heal's counted size was had (combat.HealSizeEstimator.stamp)
SIZED = {"game": "valeur du jeu",
         "spec": "formule du jeu",
         "seen": "plus gros soin vu de ce sort",
         "tick": "soin sur la durée, compté à ce qu'il a rendu",
         "none": "taille inconnue",
         "regen": "remontée de vie sans soin repéré"}


def _clock(ms):
    """00:01:135: minutes, seconds, milliseconds since the pull."""
    ms = max(0, int(ms))
    return f"{ms // 60000:02d}:{ms // 1000 % 60:02d}:{ms % 1000:03d}"


def _pts(v):
    return f"{round(float(v or 0)):,}".replace(",", " ")


def _skill(r):
    name = r.get("n") or _pretty_id(r.get("s") or "?")
    if r.get("pet"):
        name = f"{_summon_label(r['pet'])} : {name}"
    return name


def _status(r):
    """A crowd control's name: its own, else its type's (Slow, Root...)."""
    names = _fr_names("skill")
    return (names.get(r.get("st")) or names.get(r.get("cc"))
            or _pretty_id(r.get("cc") or r.get("st") or "?"))


# A line is a list of pieces: text, and the names drawn with their icon
# (a player's class, a monster's picture, a skill's icon, the skull). Its
# translated sentence carries each named piece as REF<n>REF.
REF = "\x01"


class _Line:
    def __init__(self, cls):
        self.cls = cls             # player name -> class key
        self.refs = []

    def _ref(self, piece):
        self.refs.append(piece)
        return f"{REF}{len(self.refs) - 1}{REF}"

    def player(self, name):
        return self._ref({"pl": name, "ck": self.cls.get(name) or ""})

    def unit(self, kind):
        return self._ref({"mob": kind, "t": _summon_label(kind)})

    def skill(self, r):
        return self._ref({"sk": r.get("s") or "", "t": _skill(r)})

    def status(self, r):
        """A crowd control: its own icon, else its type's (Slow, Stun...)."""
        return self._ref({"sk": r.get("st") or "", "alt": r.get("cc") or "",
                          "t": _status(r)})

    def skull(self):
        return self._ref({"skull": 1})

    def target(self, r):
        """A hit's or a status's target: a player, else a unit, or ""."""
        if r.get("to"):
            return self.player(r["to"])
        return self.unit(r["tk"]) if r.get("tk") else ""

    def pieces(self, text):
        out = []
        for i, part in enumerate(text.split(REF)):
            if i % 2:
                out.append(self.refs[int(part)])
            elif part:
                out.append({"t": part})
        return out


def _line(r, cls):
    """(pieces, tone) of one line."""
    k = r.get("k")
    L = _Line(cls)
    if k == "mark":
        return L.pieces({"pull": tr("— Le boss est engagé —"),
                         "wipe": tr("— Le groupe est vaincu —"),
                         "kill": tr("— Le boss est vaincu —")}.get(
                             r.get("m"), "—")), "event"
    p = L.player(r.get("p") or "?")
    a = _pts(r.get("a"))
    crit = tr(" (critique)") if r.get("c") else ""
    if k == "hit":
        on = L.target(r)
        txt = (tr("{p} : inflige {a} points de dégâts avec {skill} sur {on}",
                  p=p, a=a, skill=L.skill(r), on=on) if on else
               tr("{p} : inflige {a} points de dégâts avec {skill}",
                  p=p, a=a, skill=L.skill(r)))
        return L.pieces(txt + crit), "dealt"
    if k == "taken":
        txt = (tr("{p} : perd {a} points de vie, infligés par {skill} de {mob}",
                  p=p, a=a, skill=L.skill(r), mob=L.unit(r["src"]))
               if r.get("src") else
               tr("{p} : perd {a} points de vie, infligés par {skill}",
                  p=p, a=a, skill=L.skill(r)))
        return L.pieces(txt + crit), "taken"
    if k == "status":
        st = L.status(r)
        has_skill = r.get("s") and r.get("s") != "?"
        if r.get("src"):
            mob = L.unit(r["src"])
            txt = (tr("{p} : subit {status}, infligé par {skill} de {mob}",
                      p=p, status=st, skill=L.skill(r), mob=mob)
                   if has_skill else
                   tr("{p} : subit {status} de {mob}", p=p, status=st,
                      mob=mob))
        else:
            on = L.target(r) or "?"
            txt = (tr("{p} : applique {status} sur {on} avec {skill}",
                      p=p, status=st, on=on, skill=L.skill(r))
                   if has_skill else
                   tr("{p} : applique {status} sur {on}", p=p, status=st,
                      on=on))
        return L.pieces(txt), "event"
    # a heal: what it restored, the rest of it in excess, and where its
    # size comes from unless the game sent it
    if r.get("z") == "regen":
        return L.pieces(tr("{p} : regagne {l} points de vie sans soin repéré "
                           "(compté comme Regen)", p=p,
                           l=_pts(r.get("l")))), "heal"
    tone = "heal"           # on self in green, from another in blue
    over = float(r.get("a") or 0) - float(r.get("l") or 0)
    got = _pts(r.get("l")) if over >= 0.5 else a
    if r.get("self") or not r.get("to") or r.get("to") == r.get("p"):
        txt = tr("{p} : se rend {a} points de vie avec {skill}",
                 p=p, a=got, skill=L.skill(r))
    else:
        txt = tr("{to} : reçoit {a} points de vie par {skill} de {p}",
                 to=L.player(r["to"]), a=got, skill=L.skill(r), p=p)
        tone = "healin"
    if over >= 0.5:
        txt += tr(" et {o} en excès", o=_pts(over))
    txt += crit
    if r.get("z") != "game":
        txt += tr(" (taille : {how})",
                  how=tr(SIZED.get(r.get("z"), "taille inconnue")))
    return L.pieces(txt), tone


# hits of one skill, by one player, this close together: one line (an
# area attack on a pack)
AOE_MS = 100


def _group_aoe(rows):
    """The rows, an area attack's hits merged: {"k": "aoe", "hits": [...]}
    in their place (the first hit's time)."""
    out = []
    for r in rows:
        last = out[-1] if out else None
        if (r.get("k") == "hit" and last is not None
                and last.get("k") in ("hit", "aoe")):
            first = last if last["k"] == "hit" else last["hits"][0]
            if (first.get("p") == r.get("p") and first.get("s") == r.get("s")
                    and first.get("pet") == r.get("pet")
                    and r.get("t", 0) - first.get("t", 0) <= AOE_MS):
                if last["k"] == "hit":
                    out[-1] = {"k": "aoe", "t": first.get("t"),
                               "hits": [first, r]}
                else:
                    last["hits"].append(r)
                continue
        out.append(r)
    return out


def _aoe_line(g, cls):
    """One line for an area attack: its damage, in how many hits, on whom."""
    hits = g["hits"]
    first = hits[0]
    L = _Line(cls)
    total = sum(float(h.get("a") or 0) for h in hits)
    crits = sum(1 for h in hits if h.get("c"))
    # its targets, by name: a unit's kind does not tell two coyotes from
    # one hit twice, so "in n hits", not "on n targets"
    names = []
    for h in hits:
        key = h.get("to") or h.get("tk")
        if key and key not in [k for k, _r in names]:
            names.append((key, h))
    on = ", ".join(L.target(h) for _k, h in names[:3])
    if len(names) > 3:
        on += "…"
    txt = (tr("{p} : inflige {a} points de dégâts avec {skill} en {n} coups "
              "sur {on}", p=L.player(first.get("p") or "?"), a=_pts(total),
              skill=L.skill(first), n=len(hits), on=on) if on else
           tr("{p} : inflige {a} points de dégâts avec {skill} en {n} coups",
              p=L.player(first.get("p") or "?"), a=_pts(total),
              skill=L.skill(first), n=len(hits)))
    if crits:
        txt += tr(" ({n} critiques)", n=crits) if crits > 1 else \
            tr(" (1 critique)")
    return L.pieces(txt), "dealt"


def _death(r, cls):
    """The line a killing blow adds: who fell (the unit hit, or the player
    a mob's hit killed)."""
    L = _Line(cls)
    who = L.player(r["p"]) if r.get("k") == "taken" else (L.target(r) or "?")
    return L.pieces(tr("{who} est {skull} mort.", who=who,
                       skull=L.skull())), "event"


def _concerns(r, who):
    return r.get("k") == "mark" or r.get("p") == who or r.get("to") == who


def _summary(rows, who):
    """What a player's lines add up to: damage dealt and taken, healing
    given (on self, on others, a line per target: what the bars moved and
    what the meter counted, how its sizes were had) and received."""
    dealt = taken = 0.0
    on = defaultdict(lambda: [0.0, 0.0, 0])      # target -> landed, counted, n
    how = defaultdict(lambda: [0.0, 0.0, 0])     # sized -> landed, counted, n
    got = defaultdict(lambda: [0.0, 0])          # healer -> landed, n
    for r in rows:
        k = r.get("k")
        if k == "hit" and r.get("p") == who:
            dealt += float(r.get("a") or 0)
        elif k == "taken" and r.get("p") == who:
            taken += float(r.get("a") or 0)
        elif k == "heal":
            to = r.get("to") or r.get("p")
            if r.get("p") == who:
                t = on[who if (r.get("self") or to == who) else to]
                t[0] += float(r.get("l") or 0)
                t[1] += float(r.get("a") or 0)
                t[2] += 1
                h = how[r.get("z") or "none"]
                h[0] += float(r.get("l") or 0)
                h[1] += float(r.get("a") or 0)
                h[2] += 1
            if to == who and r.get("p") != who:
                g = got[r.get("p") or "?"]
                g[0] += float(r.get("l") or 0)
                g[1] += 1
    mine = on.pop(who, [0.0, 0.0, 0])
    others = [0.0, 0.0, 0]
    for v in on.values():
        for i in range(3):
            others[i] += v[i]
    # total (the heals' size), restored, in excess, how many
    cols = [tr("Total"), tr("Rendus"), tr("En excès"), tr("Soins")]

    def row(label, v):
        return [label, _pts(v[1]), _pts(v[0]), _pts(max(0.0, v[1] - v[0])),
                str(v[2])]
    tables = [{"t": tr("Soins donnés"), "cols": cols, "rows": [
        row(tr("Sur soi"), mine), row(tr("Sur les autres"), others)]}]
    if on:
        tables.append({"t": tr("Soins donnés, par cible"), "cols": cols,
                       "rows": [row(n, v) for n, v in
                                sorted(on.items(), key=lambda kv: -kv[1][1])]})
    if how:
        tables.append({"t": tr("Taille des soins donnés"), "cols": cols,
                       "rows": [row(tr(SIZED.get(z, "taille inconnue")), v)
                                for z, v in sorted(how.items(),
                                                   key=lambda kv: -kv[1][1])]})
    if got:
        tables.append({"t": tr("Soins reçus des autres"),
                       "cols": [tr("Rendus"), tr("Soins")],
                       "rows": [[n, _pts(v[0]), str(v[1])]
                                for n, v in sorted(got.items(),
                                                   key=lambda kv: -kv[1][0])]})
    total = mine[1] + others[1]
    landed = mine[0] + others[0]
    cards = [{"title": tr("Dégâts infligés"), "value": _pts(dealt)},
             {"title": tr("Dégâts reçus"), "value": _pts(taken)},
             {"title": tr("Soins donnés"), "value": _pts(total),
              "sub": tr("{l} rendus, {o} en excès", l=_pts(landed),
                        o=_pts(max(0.0, total - landed)))}]
    return {"cards": cards, "tables": tables}


def journal_view(rows, who, kinds, limit, node_id="dungeon_journal",
                 cls=None):
    """The journal node: the players to pick from, the kinds shown, the
    lines (at most `limit`), a picked player's summary. `cls`: each
    player's class key, for their icon."""
    cls = cls or {}
    players = sorted({r.get("p") for r in rows if r.get("p")}
                     | {r.get("to") for r in rows
                        if r.get("k") == "heal" and r.get("to")},
                     key=str.lower)
    if who not in players:
        who = ""
    shown = [r for r in rows
             if (r.get("k") == "mark" or r.get("k") in kinds)
             and (not who or _concerns(r, who))]
    shown = _group_aoe(shown)
    lines = []
    for r in shown[:limit]:
        if r.get("k") == "aoe":
            out = [_aoe_line(r, cls)] + [_death(h, cls) for h in r["hits"]
                                         if h.get("x")]
        else:
            out = [_line(r, cls)] + ([_death(r, cls)] if r.get("x") else [])
        for pieces, tone in out:
            lines.append({"t": _clock(r.get("t")), "tone": tone,
                          "pieces": pieces})
    return {"k": "journal", "id": node_id,
            "players": [{"v": "", "t": tr("Tous les joueurs")}]
            + [{"v": p, "t": p} for p in players],
            "who": who,
            "kinds": [{"id": k, "t": tr(t), "on": k in kinds}
                      for k, t in KINDS],
            "lines": lines, "count": len(shown),
            "more": len(shown) > limit,
            "summary": _summary(rows, who) if who else None,
            "note": tr("La taille d'un soin est celle que le jeu affiche "
                       "au-dessus de la cible quand il l'envoie, sinon elle "
                       "est estimée (la ligne le dit). « En excès » est la "
                       "part qui n'a rendu aucun point de vie, la cible étant "
                       "déjà pleine.")}

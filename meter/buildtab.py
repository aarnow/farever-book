"""The Build tab: the builds saved in builds/, the one being edited, and
the page it shows. The app only forwards the tab's actions here and asks
for its page (BuildTab.actions(), BuildTab.page()).

The rules and the model are in builds.py; the result (gear stats,
attributes, infusion sets) is the Inspecter's own character view, computed
on the build turned into a profile."""
from __future__ import annotations

import copy

import builds as B
from common import _pretty_id, class_key
from gamedata import (_fr_names, _skill_label, build_data, faction_label,
                      infusion_data, item_icon, item_label, item_type_label,
                      rarity_label)
from gearstats import gear_stats
from views import _augment_view, _talent_tree, character_view

CLASS_FR = {"Warrior": "Guerrier", "Mage": "Mage", "Priest": "Prêtre",
            "Rogue": "Voleur"}
SLOT_LABELS = {"Weapon1": "Main principale", "OffhandWeapon": "Main secondaire",
               "Weapon2": "Arme d'arsenal", "Head": "Tête", "Neck": "Cou",
               "Shoulders": "Épaules", "Chest": "Torse", "Back": "Dos",
               "Hands": "Mains", "Waist": "Taille", "Legs": "Jambes",
               "Feet": "Pieds", "FingerLeft": "Anneau", "Trinket": "Babiole",
               "FingerRight": "Anneau"}
AUG_LABELS = {"AugmentDemon": "Cadeau corrompu",
              "AugmentEnchantWeapon": "Formule magique",
              "AugmentDemonSigil": "Sceau démoniaque",
              "AugmentBlacksmith": "Renfort de forgeron",
              "AugmentJeweller": "Gemme",
              "AugmentOutfitter": "Broderie",
              "AugmentEnchantFeet": "Enchantement de bottes",
              "AugmentEnchantHands": "Enchantement de gants"}
HANDS_FR = {"1h": "une main", "2h": "deux mains", "dual": "deux armes",
            "long": "arme longue", "off": "main secondaire"}
SKILL_GROUPS = (("class", "Compétences de classe"),
                ("weapon", "Compétences de l'arme"),
                ("arsenal", "Compétences de l'arsenal"))


class BuildTab:
    def __init__(self, invalidate, toast):
        self._invalidate = invalidate
        self._toast = toast
        self.file = None            # the build being edited (its file name)
        self.build = None
        self.slot = None            # the slot whose editor is open
        self.confirm_delete = False

    # ---- actions ---------------------------------------------------------
    def actions(self):
        return {
            "build_new": self._new,
            "build_open": lambda p: self._open(p.get("file")),
            "build_close": self._close,
            "build_dup": self._duplicate,
            "build_delete": self._delete,
            "build_delete_cancel": lambda: setattr(self, "confirm_delete",
                                                   False),
            "build_rename": lambda p: self._edit(
                lambda b: b.update(name=str(p.get("value") or "").strip()[:60]
                                   or b["name"])),
            "build_class": lambda p: self._edit(
                lambda b: b.update(cls=p.get("value"))),
            "build_level": lambda p: self._edit(
                lambda b: b.update(lvl=int(p.get("value") or 1))),
            "build_slot": lambda p: setattr(self, "slot", p.get("slot")
                                            if p.get("slot") in B.SLOT_KIND
                                            else None),
            "build_slot_close": lambda: setattr(self, "slot", None),
            "build_pick": lambda p: self._pick(p.get("id")),
            "build_unequip": self._unequip,
            "build_piece": lambda p: self._piece(p.get("field"),
                                                 p.get("value")),
            "build_talent": lambda p: self._talent(p.get("id"),
                                                   p.get("delta")),
            "build_talents_reset": lambda: self._edit(
                lambda b: b.update(talents={})),
            "build_skill": lambda p: self._skill(p.get("group"),
                                                 p.get("index"),
                                                 p.get("value")),
        }

    def import_profile(self, prof):
        """Inspecter's "Créer un build" from an analysed player."""
        b = B.from_profile(prof, f"Build de {prof.get('n') or '?'}")
        self.file, self.build = B.save_build(b), b
        self.slot, self.confirm_delete = None, False
        self._toast(f"Build créé à partir de {prof.get('n')}.")

    def _new(self):
        n = len(B.list_builds()) + 1
        b = B.normalize(B.new_build(f"Build {n}"))
        self.file, self.build = B.save_build(b), b
        self.slot, self.confirm_delete = None, False

    def _open(self, file):
        for f, b in B.list_builds():
            if f == file:
                self.file, self.build = f, B.normalize(b)
                self.slot, self.confirm_delete = None, False
                return

    def _close(self):
        self.file = self.build = self.slot = None
        self.confirm_delete = False

    def _duplicate(self):
        if not self.build:
            return
        b = dict(self.build, name=f"{self.build['name']} (copie)")
        b = B.normalize(copy.deepcopy(b))
        self.file, self.build = B.save_build(b), b
        self._toast("Build dupliqué.")

    def _delete(self):
        if not self.build:
            return
        if not self.confirm_delete:
            self.confirm_delete = True
            return
        B.delete_build(self.file)
        self._toast(f"Build « {self.build['name']} » supprimé.")
        self._close()

    def _edit(self, change):
        if not self.build:
            return
        change(self.build)
        B.normalize(self.build)
        self.confirm_delete = False
        B.save_build(self.build, self.file)

    def _pick(self, kind):
        if not self.build or not self.slot:
            return
        e = B.item(kind)
        if not e or kind not in B.options(self.build, self.slot):
            return
        old = self.build["gear"].get(self.slot) or {}
        self._edit(lambda b: b["gear"].__setitem__(self.slot, {
            "id": kind, "rar": old.get("rar") or e.get("rar") or "Rare",
            "lvl": old.get("lvl") or b["lvl"], "up": old.get("up") or 0,
            "augs": {}, "inf": old.get("inf"), "istat": old.get("istat")}))

    def _unequip(self):
        if self.build and self.slot:
            self._edit(lambda b: b["gear"].pop(self.slot, None))

    def _piece(self, field, value):
        if not self.build or not self.slot:
            return
        p = self.build["gear"].get(self.slot)
        if not p:
            return

        def change(_b):
            if field in ("rar", "inf", "istat"):
                p[field] = value or None
            elif field == "prism":
                p["prism"] = bool(value)
            elif field in ("lvl", "up"):
                p[field] = int(value or 0)
            elif field.startswith("aug:"):
                kind = field[4:]
                augs = dict(p.get("augs") or {})
                if value:
                    augs[kind] = value
                else:
                    augs.pop(kind, None)
                p["augs"] = augs
        self._edit(change)

    def _talent(self, sid, delta):
        if self.build and B.set_talent(self.build, sid, int(delta or 0)):
            B.save_build(self.build, self.file)

    def _skill(self, group, index, value):
        if not self.build or group not in ("class", "weapon", "arsenal"):
            return

        def change(b):
            cur = list((b.get("skills") or {}).get(group) or [])
            i = int(index or 0)
            while len(cur) <= i:
                cur.append(None)
            if value and value in cur:          # a skill sits in one slot
                cur[cur.index(value)] = None
            cur[i] = value or None
            b["skills"][group] = [s for s in cur if s]
        self._edit(change)

    # ---- the page --------------------------------------------------------
    def page(self):
        d = build_data()
        if not d:
            return [{"k": "section", "t": "Build"},
                    {"k": "note", "warn": True,
                     "t": "Les données de build manquent : utilise Réparer "
                          "dans l'Aide."}]
        saved = [{"file": f, "name": b.get("name"), "lvl": b.get("lvl"),
                  "cls": CLASS_FR.get(b.get("cls"), b.get("cls")),
                  "ck": class_key(b.get("cls")), "on": f == self.file}
                 for f, b in B.list_builds()]
        node = {"k": "build", "id": "build", "list": saved, "open": None}
        if self.build:
            node["open"] = self._open_view()
        return [node]

    def _open_view(self):
        b = self.build
        d = build_data()
        prof = B.to_profile(b)
        view = character_view([], {b["name"]: prof}, b["name"], None, False)
        o = view["open"] or {}
        talents = B.talent_view(b)
        tree = _talent_tree(b["cls"], {k: v["rank"] for k, v in
                                       talents.items()})
        if tree:
            def mark(c):
                t = talents.get(c["id"]) or {}
                c["add"], c["remove"] = t.get("add"), t.get("remove")
            if tree.get("root"):
                mark(tree["root"])
            for tier in tree.get("tiers") or ():
                for cells in tier:
                    for c in cells:
                        mark(c)
        opts, slots = B.skill_options(b)
        skills = []
        for g, label in SKILL_GROUPS:
            chosen = list((b.get("skills") or {}).get(g) or [])
            skills.append({
                "g": g, "t": label, "slots": slots[g],
                "chosen": chosen + [None] * max(0, slots[g] - len(chosen)),
                "options": [{"id": s, "name": _skill_label(s)}
                            for s in opts[g]],
                "empty": ("Choisis d'abord l'arme." if g != "class"
                          and not opts[g] and slots[g] else
                          "Débloqué plus tard." if not slots[g] else "")})
        return {
            "file": self.file, "name": b["name"], "cls": b["cls"],
            "clsFr": CLASS_FR.get(b["cls"]), "ck": class_key(b["cls"]),
            "lvl": b["lvl"], "maxLvl": d.get("maxLevel") or 25,
            "classes": [{"v": c, "t": CLASS_FR[c]} for c in B.CLASSES],
            "confirmDelete": self.confirm_delete,
            "sheet": o.get("sheet"), "atbs": o.get("atbs"),
            "infusions": o.get("infusions"), "gear": o.get("gear"),
            "points": {"used": sum(t["rank"] for t in talents.values()),
                       "total": B.talent_points(b),
                       "from": d.get("talentsFrom") or 10},
            "tree": tree, "skills": skills,
            "editor": self._editor_view(o) if self.slot else None}

    def _editor_view(self, o):
        b, slot, d = self.build, self.slot, build_data()
        p = b["gear"].get(slot)
        opts = []
        names = _fr_names("attribute")
        stat_names = {}
        for k in B.options(b, slot):
            e = B.item(k)
            st = gear_stats(k, e.get("rar") or "Epic", b["lvl"], 0, [], 0)
            keys = [s[0] for s in (st[1] if st else ()) if s[0] != "Armor"]
            for s in keys:
                stat_names.setdefault(s, names.get(s) or _pretty_id(s))
            opts.append({"id": k, "name": item_label(k), "img": item_icon(k),
                         "stats": keys,
                         "fac": faction_label(e.get("fac")) if e.get("fac")
                         else "",
                         "rk": (e.get("rar") or "").lower(),
                         "type": " · ".join(x for x in (
                             item_type_label(e["type"]),
                             HANDS_FR.get(e.get("hands"), ""),
                             faction_label(e.get("fac")) if e.get("fac")
                             else "") if x)})
        opts.sort(key=lambda x: x["name"])
        piece = None
        if p:
            # the piece's computed entry, from the sheet
            entry = None
            for cells in ((o.get("sheet") or {}).get("left") or [],
                          (o.get("sheet") or {}).get("right") or []):
                for c in cells:
                    if c["slot"] == slot:
                        entry = c.get("g")
            sh = o.get("sheet") or {}
            if slot == "Weapon1":
                entry = ((sh.get("weapons") or [{}])[0] or {}).get("g")
            elif slot == "OffhandWeapon":
                ws = sh.get("weapons") or []
                entry = (ws[1] if len(ws) > 1 else {}).get("g")
            elif slot == "Weapon2":
                entry = (sh.get("arsenal") or {}).get("g")
            augs_data = d.get("augments") or {}
            augs = []
            for kind in B.aug_kinds(p["id"]):
                items = augs_data.get(kind, {}).get("items") or []
                augs.append({"kind": kind,
                             "t": AUG_LABELS.get(kind, _pretty_id(kind)),
                             "v": (p.get("augs") or {}).get(kind) or "",
                             "options": [{"v": "", "t": "Aucun"}] + sorted(
                                 ({"v": a, "t": _aug_name(a)} for a in items),
                                 key=lambda x: x["t"])})
            attr = _fr_names("attribute")
            piece = {
                "id": p["id"], "name": item_label(p["id"]),
                "rar": p.get("rar"), "lvl": p.get("lvl"),
                "up": p.get("up") or 0,
                "maxUp": B.max_upgrades(p.get("rar"), p["id"]),
                "prism": bool(p.get("prism")),
                "augs": augs, "infusable": B.infusable(p),
                "inf": p.get("inf") or "", "istat": p.get("istat") or "",
                "infOptions": [{"v": "", "t": "Aucune"}] + _infusion_options(),
                "statOptions": [{"v": s, "t": attr.get(s) or _pretty_id(s)}
                                for s in B.INFUSION_STATS],
                "g": entry}
        order = {k: i for i, k in enumerate(FILTER_STAT_ORDER)}
        return {"slot": slot, "label": SLOT_LABELS.get(slot, slot),
                "options": opts, "piece": piece,
                "stats": sorted(({"k": k, "t": t} for k, t in
                                 stat_names.items()),
                                key=lambda x: (order.get(x["k"], 99), x["t"])),
                "factions": sorted({o["fac"] for o in opts if o["fac"]}),
                "rarities": [{"v": r, "t": rarity_label(r)}
                             for r in d.get("rarities") or ()],
                "maxLvl": d.get("maxLevel") or 25,
                "infusionMin": rarity_label(d.get("infusionMinRarity"))
                if d.get("infusionMinRarity") else ""}


# the stat filters, in the character sheet's order
FILTER_STAT_ORDER = ("Vitality", "Strength", "Dexterity", "Faith", "Intellect",
                     "CritChanceRating", "FervorRating",
                     "ArmorPenetrationRating", "SpellPenetrationRating")
ROLE_FR = {"Tank": "Tank", "Support": "Soutien", "DPS": "Dégâts"}


def _infusion_options():
    """The infusions, by faction then role, each named with both: the
    bonus only applies on a piece of the same faction."""
    infs = infusion_data().get("infusions") or {}
    out = []
    for sid in build_data().get("infusions") or ():
        e = infs.get(sid) or {}
        out.append({"v": sid, "t": f"{e.get('name') or _skill_label(sid)} "
                                    f"({faction_label(e.get('f'))} · "
                                    f"{ROLE_FR.get(e.get('role'), e.get('role') or '?')})",
                    "k": (faction_label(e.get("f")), e.get("role") or "")})
    out.sort(key=lambda x: x.pop("k"))
    return out


def _aug_name(aid):
    """An augment's name with what it does (corrupted gifts share one)."""
    a = _augment_view(aid)
    return a["name"] + (f" — {a['fx']}" if a.get("fx") else "")

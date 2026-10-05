"""The Build tab: saved builds, the one being edited, and its page
(BuildTab.actions(), BuildTab.page()).

Rules and model are in builds.py; the sheet is the Inspecter's
character_view on the build turned into a profile."""
from __future__ import annotations

import copy

import builds as B
from common import dec_sep, pct_sp, _n, _pretty_id, class_key, element_label
from gamedata import (_fr_names, _skill_label, build_data, faction_label,
                      skill_tip,
                      infusion_data, item_icon, item_label, item_type_label,
                      rarity_label)
from gearstats import HERO_SLOTS, gear_stats, infusion_tiers
from i18n import tr
from simulate import simulate
from views import _augment_view, _talent_tree, character_view, class_keys

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


def _cls_fr(c):
    """A class's name, in the interface's language."""
    return tr(CLASS_FR[c]) if c in CLASS_FR else c


def _hands(h):
    return tr(HANDS_FR[h]) if h in HANDS_FR else ""


class BuildTab:
    def __init__(self, invalidate, toast):
        self._invalidate = invalidate
        self._toast = toast
        self.file = None            # the build being edited (its file name)
        self.build = None
        self.slot = None            # the slot whose editor is open
        self.confirm_delete = False
        self.cmp = None                 # {a, b}: two builds compared
        self.imaging = False            # the build's picture being made

    # ---- actions ---------------------------------------------------------
    def actions(self):
        return {
            "build_new": self._new,
            "build_open": lambda p: self._open(p.get("file")),
            "build_close": self._close,
            "build_dup": self._duplicate,
            "build_guide_options": self._guide_options,
            "build_guided": self._guided,
            "build_cmp_open": self._cmp_open,
            "build_cmp_set": lambda p: self._cmp_set(p.get("side"),
                                                     p.get("file")),
            "build_cmp_close": lambda: setattr(self, "cmp", None),
            "build_cmp_armor": lambda p: self._cmp_armor(p.get("value")),
            "build_share": self._share,
            "build_image": self._image,
            "build_import": lambda p: self._import_code(p.get("code")),
            "build_delete": self._delete,
            "build_delete_many": lambda p: self._delete_many(p.get("files")),
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
            "build_sim": lambda p: self._sim(p.get("field"), p.get("value")),
            "build_rune": lambda p: self._rune(p.get("skill"), p.get("rune")),
            "build_skill": lambda p: self._skill(p.get("group"),
                                                 p.get("index"),
                                                 p.get("value")),
        }

    def import_profile(self, prof, name=None):
        """Inspecter's "Créer un build" from an analysed player (or the
        Build tab's, from one's own character: named after it)."""
        b = B.from_profile(prof, name or tr("Build de {name}",
                                          name=prof.get('n') or '?'))
        self.file, self.build = B.save_build(b), b
        self.slot, self.confirm_delete = None, False
        self._toast(tr("Build créé à partir de {name}.", name=prof.get('n')))

    def _new(self):
        n = len(B.list_builds()) + 1
        b = B.normalize(B.new_build(tr("Build {n}", n=n)))
        self.file, self.build = B.save_build(b), b
        self.slot, self.confirm_delete = None, False

    def _open(self, file):
        for f, b in B.list_builds():
            if f == file:
                self.file, self.build = f, B.normalize(b)
                self.slot, self.confirm_delete = None, False
                return

    def close(self):
        self._close()

    def _close(self):
        self.cmp = None
        self.file = self.build = self.slot = None
        self.confirm_delete = False

    def _duplicate(self):
        if not self.build:
            return
        b = dict(self.build, name=tr("{name} (copie)", name=self.build['name']))
        b = B.normalize(copy.deepcopy(b))
        self.file, self.build = B.save_build(b), b
        self._toast(tr("Build dupliqué."))

    def _share(self):
        """The open build's share code, onto the clipboard."""
        if not self.build:
            return
        from winsys import copy_text_to_clipboard
        code = B.share_code(self.build)
        if copy_text_to_clipboard(code):
            self._toast(tr("Code du build copié ({n} caractères) : "
                           "colle-le à qui tu veux.", n=len(code)))
        else:
            self._toast(tr("Copie impossible : le presse-papiers est occupé."))

    def _image(self):
        """The open build as a picture (buildcard.py), in a thread (the 3D
        render takes seconds): saved in Images\\Farever Book and copied."""
        if not self.build or self.imaging:
            return
        import re
        import threading
        import sys
        import buildcard
        o = self._open_view()
        name = self.build["name"]
        self.imaging = True

        def work():
            try:
                folder = buildcard.pictures_dir()
                folder.mkdir(parents=True, exist_ok=True)
                stem = re.sub(r'[\\/:*?"<>|]+', " ", name).strip() or "Build"
                png = folder / f"{stem}.png"
                buildcard.render(o, png)
                copied = False
                try:
                    from PIL import Image
                    from winsys import copy_image_to_clipboard
                    with Image.open(png) as im:
                        copy_image_to_clipboard(im)
                    copied = True
                except Exception as e:
                    print(f"[meter] build image copy failed: {e}",
                          file=sys.stderr)
                self._toast(tr("Image du build copiée : colle-la où tu veux. "
                               "Elle est aussi enregistrée dans {path}.",
                               path=png) if copied else
                            tr("Image du build créée. Elle est aussi "
                               "enregistrée dans {path}.", path=png))
            except Exception as e:
                print(f"[meter] build image failed: {e!r}", file=sys.stderr)
                self._toast(tr("L'image n'a pas pu être créée : {e}", e=e))
            finally:
                self.imaging = False
                self._invalidate()
        threading.Thread(target=work, daemon=True, name="build-image").start()

    def _import_code(self, code):
        try:
            b = B.from_code(code)
        except ValueError as e:
            self._toast(str(e))
            return
        self.file, self.build = B.save_build(b), b
        self.slot, self.confirm_delete = None, False
        self._toast(tr("Build « {name} » importé.", name=b['name']))

    def _delete(self):
        if not self.build:
            return
        if not self.confirm_delete:
            self.confirm_delete = True
            return
        B.delete_build(self.file)
        self._toast(tr("Build « {name} » supprimé.", name=self.build['name']))
        self._close()

    def _delete_many(self, files):
        """The builds the player picked in the list, once confirmed."""
        names = {f: b.get("name") for f, b in B.list_builds()}
        gone = [names[f] for f in files or () if f in names
                and B.delete_build(f)]
        if self.file and self.file in (files or ()):
            self._close()
        if gone:
            names = ", ".join(tr("« {name} »", name=n) for n in gone)
            self._toast(tr("{n} builds supprimés : {names}.", n=len(gone),
                           names=names) if len(gone) > 1 else
                        tr("{n} build supprimé : {names}.", n=len(gone),
                           names=names))

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

    def _sim(self, field, value):
        if field not in ("armor", "enemy", "hit"):
            return

        def change(b):
            sim = dict(b.get("sim") or {})
            try:
                sim[field] = max(0, float(value))
            except (TypeError, ValueError):
                return
            b["sim"] = sim
        self._edit(change)

    def _rune(self, skill, rune):
        """One rune per skill: choosing the chosen one takes it off."""
        def change(b):
            runes = dict(b.get("runes") or {})
            if runes.get(skill) == rune or not rune:
                runes.pop(skill, None)
            else:
                runes[skill] = rune
            b["runes"] = runes
        self._edit(change)

    def _talent(self, sid, delta):
        if self.build and B.set_talent(self.build, sid, int(delta or 0)):
            B.save_build(self.build, self.file)

    def _skill(self, group, index, value):
        if not self.build or group not in ("class", "arsenal"):
            return

        def change(b):
            cur = list((b.get("skills") or {}).get(group) or [])
            i = int(index or 0)
            while len(cur) <= i:
                cur.append(None)
            if value and value in cur:          # a skill sits in one slot
                cur[cur.index(value)] = None
            cur[i] = value or None
            while cur and cur[-1] is None:      # slots keep their place
                cur.pop()
            b["skills"][group] = cur
        self._edit(change)

    # ---- the page --------------------------------------------------------
    def page(self):
        d = build_data()
        if not d:
            return [{"k": "section", "t": tr("Build")},
                    {"k": "note", "warn": True,
                     "t": tr("Les données de build manquent : utilise "
                             "Réparer dans l'Aide.")}]
        saved = [{"file": f, "name": b.get("name"), "lvl": b.get("lvl"),
                  "cls": _cls_fr(b.get("cls")),
                  "ck": class_key(b.get("cls")), "on": f == self.file}
                 for f, b in B.list_builds()]
        node = {"k": "build", "id": "build", "list": saved, "open": None}
        if self.cmp:
            node["cmp"] = self._cmp_view()
            if node["cmp"]:
                return [node]
            self.cmp = None
        if self.build:
            node["open"] = self._open_view()
        return [node]

    # -- comparing two builds of one class -------------------------------
    def _cmp_open(self):
        """The comparison opens on the open build (or the first one that
        has a twin of its class) against the next of its class."""
        builds = B.list_builds()
        by_cls = {}
        for f, b in builds:
            by_cls.setdefault(b.get("cls"), []).append(f)
        pairs = [fs for fs in by_cls.values() if len(fs) > 1]
        if not pairs:
            self._toast(tr("Il faut au moins deux builds de la même classe."))
            return
        first = self.file if self.file and any(
            self.file in fs for fs in pairs) else pairs[0][0]
        same = next(fs for fs in pairs if first in fs)
        self.cmp = {"a": first, "b": next(f for f in same if f != first)}

    def _cmp_set(self, side, file):
        if not self.cmp or side not in ("a", "b"):
            return
        builds = dict(B.list_builds())
        if file not in builds:
            return
        self.cmp[side] = file
        other = "b" if side == "a" else "a"
        cls = builds[file].get("cls")
        # the other side follows: same class, never the same build
        if (builds.get(self.cmp[other], {}).get("cls") != cls
                or self.cmp[other] == file):
            self.cmp[other] = next((f for f, b in builds.items()
                                    if b.get("cls") == cls and f != file),
                                   None)

    # -- the guided build -------------------------------------------------
    def _guide_options(self):
        """What the guide offers, asked when it opens: per class its
        weapons (with their pictures), the attributes and the stats."""
        d = build_data()
        names = _fr_names("attribute")
        out = {"classes": [{"v": c, "t": _cls_fr(c), "ck": class_key(c)}
                           for c in B.CLASSES],
               "maxLvl": d.get("maxLevel") or 25,
               "atbs": [{"v": a, "t": names.get(a) or _pretty_id(a)}
                        for a in B.GUIDE_ATTRIBUTES],
               "stats": [{"v": a, "t": names.get(a) or _pretty_id(a)}
                         for a in B.GUIDE_STATS],
               "weapons": {}, "offhands": {}}
        for c in B.CLASSES:
            for slot, key in (("Weapon", "weapons"), ("Offhand", "offhands")):
                out[key][c] = sorted((
                    {"v": k, "t": item_label(k), "img": item_icon(k),
                     "type": item_type_label(e.get("type")),
                     "hands": _hands(e.get("hands")),
                     "shield": bool(e.get("shield")),
                     "rk": (e.get("rar") or "").lower()}
                    for k, e in (d.get("items") or {}).items()
                    if e.get("slot") == slot and c in e.get("cls", ())),
                    key=lambda x: (x["type"], x["t"]))
        return out

    @staticmethod
    def _guide_raw(b):
        """The build's attributes, as its character sheet totals them."""
        prof = B.to_profile(b)
        v = character_view([], {b["name"]: prof}, b["name"], None, False)
        return ((v.get("open") or {}).get("atbs") or {}).get("raw")

    def _guide_survival(self, b):
        """Effective health: health / (1 - r) against a monster of its level,
        r = armour / (armour + 385 + 100·level)."""
        raw = self._guide_raw(b) or {}
        arm = max(0.0, raw.get("Armor") or 0)
        r = arm / (arm + 385 + 100 * b["lvl"])
        return (raw.get("MaxHealth") or 0) / max(0.05, 1 - r)

    def _guide_score(self, b, mode, heals):
        """What the guide maximises: the sum of all the build's skills'
        average hits (crits included) on a common target; `mode` keeps only
        magic or physical damage, `heals` adds heals and shields."""
        from simulate import is_magic
        raw = self._guide_raw(b)
        sim = self._sim_view(dict(b, sim={"armor": 30, "enemy": None,
                                          "hit": 300}), raw)
        total = 0.0
        for g in (sim or {}).get("groups") or ():
            for s in g.get("skills") or ():
                for ln in s.get("lines") or ():
                    if ln.get("k") == "Damage":
                        mg = is_magic(ln.get("affk"))
                        if mode == "magic" and not mg:
                            continue
                        if mode == "phys" and mg:
                            continue
                        total += ln.get("avgv") or 0
                    elif heals and ln.get("k") in ("Heal", "Shield"):
                        total += ln.get("avgv") or 0
        return total

    def _guide_optimise(self, b, p):
        """Greedy pass over the guide's draft: per slot the piece, then the
        augments, then the infusions that score best (two passes over
        pieces: a change can open another). The wishes break ties."""
        from gearstats import gear_stats
        stats = [s for s in p.get("stats") or () if s]
        mode = ("magic" if "SpellPenetrationRating" in stats
                and "ArmorPenetrationRating" not in stats else
                "phys" if "ArmorPenetrationRating" in stats
                and "SpellPenetrationRating" not in stats else "all")
        heals = B.guide_role(p) == "Support"
        weights = B.guide_weights(list(p.get("atbs") or ())[:2], stats[:3])
        # a few % bonus per wished stat on the piece, so the player's order
        # wins over the simulation's small gaps
        prio = dict(zip(stats[:3], (0.6, 0.3, 0.1)))
        draft = next((pc.get("inf") for pc in b["gear"].values()
                      if pc.get("inf")), "")

        def wish(kind):
            got = gear_stats(kind, (B.item(kind) or {}).get("rar"), b["lvl"],
                             0, [], 0)
            vals = got[1] if got else ()
            return (sum(prio.get(a, 0) * v for a, _n, v in vals) / 1000.0
                    + sum(weights.get(a, 0) * v for a, _n, v in vals) / 1e5)

        def score(bd):
            return self._guide_score(bd, mode, heals)

        best = score(b)
        for _pass in range(2):
            changed = False
            for slot in B.SLOT_KIND:
                if slot.startswith(("Weapon", "Offhand")):
                    continue
                cur = b["gear"].get(slot)
                taken = {pc["id"] for s, pc in b["gear"].items() if s != slot}
                pick, pick_v = cur, best * (1 + (wish(cur["id"]) if cur
                                                 else 0))
                for kind in B.options(b, slot):
                    if kind in taken or (cur and kind == cur["id"]):
                        continue
                    e = B.item(kind) or {}
                    pc = {"id": kind, "rar": e.get("rar") or "Rare",
                          "lvl": b["lvl"], "up": 0, "prism": False,
                          "augs": {}, "inf": "", "istat": ""}
                    if B.infusable(pc):
                        # epic: prismatic, infused, its bonus on a wish
                        pc.update(prism=True, inf=draft,
                                  istat=B.guide_istat(pc, stats))
                    b["gear"][slot] = pc
                    val = score(b) * (1 + wish(kind))
                    if val > pick_v + 1e-9:
                        pick, pick_v = pc, val
                if pick is None:
                    b["gear"].pop(slot, None)
                else:
                    b["gear"][slot] = pick
                if pick is not cur:
                    changed = True
                best = score(b)
            if not changed and _pass:
                break
        # the augments, slot by slot
        augs = build_data().get("augments") or {}
        for slot, pc in b["gear"].items():
            for kind in B.aug_kinds(pc["id"]):
                cands = [a for a in (augs.get(kind) or {}).get("items") or ()
                         if kind != "AugmentDemonSigil" or B.sigil_ok(b, a)]
                keep, keep_v = pc["augs"].get(kind), best
                for aid in cands:
                    pc["augs"][kind] = aid
                    val = score(b)
                    if val > keep_v + 1e-9:
                        keep, keep_v = aid, val
                if keep:
                    pc["augs"][kind] = keep
                else:
                    pc["augs"].pop(kind, None)
                best = keep_v
        plan = self._guide_infusions(b, p, stats, score)
        best = score(b)
        return B.normalize(b), best, mode, plan

    def _guide_infusions(self, b, p, stats, score):
        """The infusions builds.GUIDE_GOALS asks for, each group the one of
        its role that scores best (effective health for a tank). Infused
        pieces are made prismatic so any infusion's bonus holds."""
        infs = infusion_data().get("infusions") or {}
        pieces = [pc for pc in b["gear"].values() if B.infusable(pc)]
        for pc in pieces:
            pc["prism"] = True
            pc["istat"] = B.guide_istat(pc, stats)
        main = B.guide_role(p)
        other = "DPS" if main == "Tank" else "Tank"
        goal = p.get("goal") if p.get("goal") in B.GUIDE_GOALS else "max"
        plan = B.GUIDE_GOALS[goal]
        groups = []                         # [[sid, pieces]], in order

        def wear():
            left = list(pieces)
            for sid, n in groups:
                for pc in left[:n]:
                    pc["inf"] = sid
                left = left[n:]
            for pc in left:
                pc["inf"] = ""

        pref = {s: i for i, s in enumerate(B.GUIDE_INFUSION_PREF)}
        for who, n in plan:
            role = main if who == "main" else other
            taken = {sid for sid, _n in groups}
            cands = sorted((sid for sid, e in infs.items()
                            if sid.endswith("_" + role) and sid not in taken),
                           key=lambda s: pref.get(s, 99))
            pick, pick_v = None, None
            # survival goals: skip infusions that cost health (e.g. demons'
            # 4 pieces: Vitality −4 %)
            wear()
            floor = (self._guide_survival(b) * 0.999
                     if goal != "max" and role != "Tank" else None)
            for sid in cands:
                groups.append([sid, n])
                wear()
                if floor and self._guide_survival(b) < floor:
                    groups.pop()
                    continue
                val = (self._guide_survival(b) if role == "Tank"
                       else score(b))
                groups.pop()
                # a real gain only: else the preferred one stays
                if pick_v is None or val > pick_v * 1.001:
                    pick, pick_v = sid, val
            if pick:
                groups.append([pick, n])
        wear()
        out, done = [], 0
        for sid, n in groups:
            k = max(0, min(n, len(pieces) - done))
            done += k
            if k:
                out.append({"name": (infs.get(sid) or {}).get("name") or sid,
                            "n": k})
        return out

    def _guided(self, p):
        b = B.guided_build(p or {})
        b, score, mode, plan = self._guide_optimise(b, p or {})
        cls = _cls_fr(b["cls"])
        n = sum(1 for _ in B.list_builds() if True) + 1
        b["name"] = str((p or {}).get("name") or "").strip()[:60] or \
            tr("{cls} guidé {n}", cls=cls, n=n)
        self.cmp = None
        self.file, self.build = B.save_build(b), b
        self.slot, self.confirm_delete = None, False
        what = tr({"magic": "dégâts magiques", "phys": "dégâts physiques"}.get(
            mode, "dégâts"))
        worn = ", ".join(f"{x['name']} ×{x['n']}" for x in plan)
        if worn:
            self._toast(tr("Build « {name} » composé pour le maximum de "
                           "{what} de ses compétences ({n} pièces, "
                           "imprégnations {worn}). Les talents et les runes "
                           "restent à choisir.", name=b['name'], what=what,
                           n=len(b['gear']), worn=worn))
        else:
            self._toast(tr("Build « {name} » composé pour le maximum de "
                           "{what} de ses compétences ({n} pièces). Les "
                           "talents et les runes restent à choisir.",
                           name=b['name'], what=what, n=len(b['gear'])))

    def _cmp_armor(self, value):
        """The target's damage reduction for the spells, both builds."""
        if self.cmp is not None:
            try:
                self.cmp["armor"] = max(0.0, min(90.0, float(value)))
            except (TypeError, ValueError):
                pass

    def _cmp_view(self):
        builds = dict(B.list_builds())
        a, b = builds.get(self.cmp.get("a")), builds.get(self.cmp.get("b"))
        if not a or not b:
            return None

        def sheet(bd):
            prof = B.to_profile(bd)
            v = character_view([], {bd["name"]: prof}, bd["name"], None,
                               False)
            return (v.get("open") or {}).get("atbs") or {}

        sa, sb = sheet(a), sheet(b)
        # the spells, both against the same target: the left build's
        target = dict(a.get("sim") or {})
        if self.cmp.get("armor") is not None:
            target["armor"] = self.cmp["armor"]
        ma = self._sim_view(dict(a, sim=target), sa.get("raw"))
        mb = self._sim_view(dict(b, sim=target), sb.get("raw"))
        ra, rb = sa.get("raw") or {}, sb.get("raw") or {}
        groups = []
        for key, title in (("primary", tr("Attributs")),
                           ("secondary", tr("Plus de stats"))):
            rows = []
            texts_b = {r["k"]: r for r in sb.get(key) or ()}
            for r in sa.get(key) or ():
                k = r["k"]
                pct = r["v"].endswith("%")
                va, vb = ra.get(k) or 0, rb.get(k) or 0
                d = vb - va
                small = 0.05 if pct or k == "HealthRegen" else 0.5
                if abs(d) < small:
                    better, delta = "", ""
                else:
                    better = "b" if d > 0 else "a"
                    amount = abs(d)
                    if pct or k == "HealthRegen":
                        txt = f"{amount:.1f}".rstrip("0").rstrip(".")
                        txt = txt.replace(".", dec_sep()) + (pct_sp() if pct else "")
                    else:
                        txt = _n(round(amount))
                    delta = "+" + txt
                rows.append({"k": k, "t": r["t"], "a": r["v"],
                             "b": (texts_b.get(k) or {}).get("v", "—"),
                             "better": better, "delta": delta})
            groups.append({"t": title, "rows": rows})

        def card(f, bd):
            return {"file": f, "name": bd.get("name"), "lvl": bd.get("lvl"),
                    "cls": _cls_fr(bd.get("cls")),
                    "ck": class_key(bd.get("cls"))}
        cls_a = a.get("cls")
        return {
            "a": card(self.cmp["a"], a), "b": card(self.cmp["b"], b),
            # the left one picks among all builds that have a twin of their
            # class; the right one among the left one's class
            "pickA": [{"v": f, "t": bd.get("name")} for f, bd in builds.items()
                      if sum(1 for x in builds.values()
                             if x.get("cls") == bd.get("cls")) > 1],
            "pickB": [{"v": f, "t": bd.get("name")} for f, bd in builds.items()
                      if bd.get("cls") == cls_a and f != self.cmp["a"]],
            "groups": groups,
            "spells": _cmp_spells(ma, mb),
            "armor": float((ma or {}).get("armor", 30)),
            "target": tr("Cible de niveau {lvl}, la même pour les deux "
                         "builds. Dégâts normaux et critiques comparés "
                         "séparément : les chances de critique diffèrent "
                         "d'un build à l'autre.",
                         lvl=(ma or {}).get('enemy') or a.get('lvl'))}

    def _open_view(self):
        b = self.build
        d = build_data()
        prof = B.to_profile(b)
        view = character_view([], {b["name"]: prof}, b["name"], None, False)
        o = view["open"] or {}
        talents = B.talent_view(b)
        tree = _talent_tree(b["cls"], {k: v["rank"] for k, v in
                                       talents.items() if not v.get("gift")},
                            B.granted_talents(b))
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
        bar = self._skill_bar(b)
        passives = [{"id": s, "name": _skill_label(s), "tip": skill_tip(s)}
                    for s in B.passives(b)]
        return {
            "file": self.file, "name": b["name"], "cls": b["cls"],
            "clsFr": _cls_fr(b["cls"]) if b["cls"] in CLASS_FR else None,
            "ck": class_key(b["cls"]),
            "lvl": b["lvl"], "maxLvl": d.get("maxLevel") or 25,
            "classes": [{"v": c, "t": _cls_fr(c)} for c in B.CLASSES],
            "confirmDelete": self.confirm_delete,
            "imaging": self.imaging,
            "sheet": o.get("sheet"), "atbs": o.get("atbs"),
            "infusions": o.get("infusions"), "gear": o.get("gear"),
            "points": {"used": sum(t["rank"] for t in talents.values()
                                   if not t.get("gift")),
                       "total": B.talent_points(b),
                       "from": d.get("talentsFrom") or 10},
            "tree": tree, "bar": bar, "passives": passives,
            # the hero in 3D, wearing the build's armour
            "model": "hero:" + ".".join(
                f"{s}={(b['gear'].get(s) or {}).get('id')}" for s in HERO_SLOTS
                if (b["gear"].get(s) or {}).get("id")),
            "sim": self._sim_view(b, (o.get("atbs") or {}).get("raw")),
            "editor": self._editor_view(o) if self.slot else None}

    def _sim_view(self, b, raw):
        """The simulation: the bar's skills and the main weapon's attacks
        against the target set in the build, and the incoming hit."""
        if not raw:
            return None
        sim = b.get("sim") or {}
        armor = float(sim.get("armor") if sim.get("armor") is not None
                      else 30)
        enemy = int(sim.get("enemy") or b["lvl"])
        hit = float(sim.get("hit") if sim.get("hit") is not None else 300)
        gear = b.get("gear") or {}

        def weapon(slot):
            p = gear.get(slot)
            return (p["id"], p.get("lvl") or b["lvl"]) if p else None
        main, off, ars = (weapon("Weapon1"), weapon("OffhandWeapon"),
                          weapon("Weapon2"))
        runes = (b.get("runes") or {}).values()
        d = build_data()
        base_ids = set()                # the basic attacks' skills

        def attacks(w):
            """A weapon's basic attacks and combo."""
            out, n = [], 0
            for s in (B.item(w[0]) or {}).get("skills") or ():
                if s["type"] in ("Attack", "Attack2", "Attack3", "Attack4"):
                    n += 1
                    base_ids.add(s["id"])
                    out.append((s["id"], tr("Attaque de base {n}", n=n), w))
                elif s["type"] == "AttackCombo":
                    out.append((s["id"], tr("Combo : {name}",
                                            name=_skill_label(s['id'])), w))
            return out

        def skills_of(w, types, scaled=True):
            return [(s["id"], None, w if scaled else None)
                    for s in (B.item(w[0]) or {}).get("skills") or ()
                    if s["type"] in types]

        # every source the hero can use, the bar aside: the weapons worn,
        # the arsenal's, and all the class's skills (runes as set)
        groups = []
        if main:
            groups.append((tr("Arme principale"), item_label(main[0]),
                           attacks(main)
                           + skills_of(main, B.WEAPON_SKILL_TYPES)))
        if off:
            groups.append((tr("Main secondaire"), item_label(off[0]),
                           skills_of(off, B.WEAPON_SKILL_TYPES, False)))
        if ars:
            groups.append((tr("Arsenal"), item_label(ars[0]),
                           skills_of(ars, B.WEAPON_SKILL_TYPES
                                     + B.PASSIVE_TYPES)))
        cls = (d.get("classes") or {}).get(b["cls"]) or {}
        groups.append((tr("Compétences de classe"),
                       _cls_fr(b["cls"]) if b["cls"] in CLASS_FR else "",
                       [(s["id"], None, None) for s in cls.get("skills") or ()]))
        rows = [r for _t, _s, rs in groups for r in rs]
        # the hero alone: critical chances and what an incoming hit leaves
        out = simulate(raw, b["lvl"], [], armor, enemy, hit, runes)
        sims = [(t, sub, rs, simulate(raw, b["lvl"], rs, armor, enemy, hit,
                                      runes)) for t, sub, rs in groups if rs]
        fmt = lambda v: f"{v:.0f}"
        pc = lambda v: f"{v * 100:.1f}".replace(".", dec_sep()) + pct_sp()
        kinds = {"Damage": tr("Dégâts"), "Heal": tr("Soin"),
                 "Shield": tr("Bouclier")}
        view = {
            "armor": armor, "enemy": enemy, "hit": hit,
            "maxLvl": build_data().get("maxLevel") or 25,
            "crit": pc(out["critChance"]), "critMult": pc(out["critMult"]),
            "groups": [{
                "t": t, "sub": sub,
                "skills": _merge_base_attacks(
                    [self._card(r, fmt, pc, kinds) for r in g["rows"]],
                    [sid for sid, label, _w in rs
                     if label and sid in base_ids])}
                for t, sub, rs, g in sims],
            "runes": self._runes_view(b, raw, rows, armor, enemy, hit),
            "defense": {
                "hit": fmt(hit),
                "phys": fmt(out["defense"]["phys"]["after"]),
                "physMit": pc(out["defense"]["phys"]["mit"]),
                "magic": fmt(out["defense"]["magic"]["after"]),
                "magicMit": pc(out["defense"]["magic"]["mit"]),
                "taken": pc(out["defense"]["taken"]),
                "hp": fmt(out["defense"]["hp"]),
                "hitsPhys": (f"{out['defense']['hp'] / out['defense']['phys']['after']:.1f}".replace(".", dec_sep())
                             if out["defense"]["phys"]["after"] > 0 else "—")}}
        return view

    @staticmethod
    def _card(r, fmt, pc, kinds):
        """One simulated skill, as its card shows it."""
        return {
            "id": r["id"], "name": r["name"],
            "cd": f"{r['cd']:g} s".replace(".", dec_sep()) if r.get("cd") else "",
            "range": f"{r['range']:g} m".replace(".", dec_sep())
            if r.get("range") else "",
            "lines": [{"kind": kinds.get(x["kind"], x["kind"]), "k": x["kind"],
                       "aff": element_label(x["aff"]) if x["aff"] else "",
                       "normal": fmt(x["normal"]), "crit": fmt(x["crit"]),
                       "normalv": round(x["normal"], 1),
                       "critv": round(x["crit"], 1),
                       "avg": fmt(x["avg"]), "avgv": round(x["avg"], 1),
                       "affk": x["aff"] or "",
                       "mit": pc(x["mit"]) if "mit" in x else ""}
                      for x in r["lines"]]}

    def _runes_view(self, b, raw, rows, armor, enemy, hit):
        """Each class skill with runes, on the bar or not: its runes, the
        chosen one, each described with numbers simulated with that rune."""
        info = build_data().get("skillInfo") or {}
        chosen = b.get("runes") or {}
        out = []
        weapon_of = {sid: w for sid, _l, w in rows}
        on_bar = set(B.bar_skills(b))
        for sid in B.rune_skills(b):
            rs = (info.get(sid) or {}).get("runes") or ()
            if not rs:
                continue
            cards = []
            for r in rs:
                sim = simulate(raw, b["lvl"], [(sid, None,
                                                weapon_of.get(sid))],
                               armor, enemy, hit, [r["id"]])
                lines = [x for sk in sim["rows"] for x in sk["lines"]
                         if x.get("rune") == r["id"]]
                vals = {"dmg": next((x for x in lines
                                     if x["kind"] == "Damage"), None),
                        "heal": next((x for x in lines
                                      if x["kind"] in ("Heal", "Shield")),
                                     None)}
                cards.append({"id": r["id"], "name": _skill_label(r["id"]),
                              "on": chosen.get(sid) == r["id"],
                              "desc": _rune_text(r, _skill_label(sid), vals)})
            out.append({"skill": sid, "name": _skill_label(sid),
                        "bar": sid in on_bar, "runes": cards})
        return out

    def _skill_bar(self, b):
        """The action bar as the game shows it: 1-2 the weapon's skills
        (set by the weapons), 3-4 the arsenal's (picked), A E R G the
        class's (picked). A slot not open yet says the level it opens at."""
        d = build_data()
        opts, slots = B.skill_options(b)
        sk = b.get("skills") or {}

        def cell(key, group, i, levels, choice):
            chosen = list(sk.get(group) or [])
            sid = chosen[i] if i < len(chosen) else None
            open_ = i < slots[group]
            c = {"key": key, "group": group, "index": i,
                 "id": sid, "name": _skill_label(sid) if sid else "",
                 "tip": skill_tip(sid) if sid else None,
                 "open": open_}
            if not open_ and i < len(levels):
                c["lock"] = tr("niv. {n}", n=levels[i])
            if choice and open_:
                c["options"] = [{"id": s, "name": _skill_label(s),
                                 "tip": skill_tip(s)}
                                for s in opts[group]]
            return c
        wl = d.get("weaponSkillLevels") or [1, 2]
        al = d.get("arsenalLevels") or [7, 20]
        out = [cell(str(i + 1), "weapon", i, wl, False) for i in range(2)]
        out += [cell(str(i + 3), "arsenal", i, al, True) for i in range(2)]
        out += [dict(cell(k, "class", i, (), True), sep=(i == 0))
                for i, k in enumerate(class_keys())]
        return out

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
                             _hands(e.get("hands")),
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
                if kind == "AugmentDemonSigil":
                    items = [a for a in items if B.sigil_ok(b, a)]
                augs.append({"kind": kind,
                             "t": tr(AUG_LABELS[kind]) if kind in AUG_LABELS
                             else _pretty_id(kind),
                             "v": (p.get("augs") or {}).get(kind) or "",
                             "options": [{"v": "", "t": tr("Aucun")}] + sorted(
                                 (_aug_option(a) for a in items),
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
                "infOptions": [{"v": "", "t": tr("Aucune")}]
                + _infusion_options(),
                # only what the piece can roll: not a stat it already has
                "statOptions": [{"v": "", "t": tr("Aucun")}] + [
                    {"v": s, "t": attr.get(s) or _pretty_id(s)}
                    for s in B.istat_options(p)],
                "g": entry}
        order = {k: i for i, k in enumerate(FILTER_STAT_ORDER)}
        return {"slot": slot, "label": tr(SLOT_LABELS[slot])
                if slot in SLOT_LABELS else slot,
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


# the pieces the hero model wears (its weapons holstered, both sets)

def _infusion_options():
    """The infusions, sorted by faction then role."""
    infs = infusion_data().get("infusions") or {}
    out = []
    for sid in build_data().get("infusions") or ():
        e = infs.get(sid) or {}
        role = e.get("role")
        out.append({"v": sid, "t": e.get("name") or _skill_label(sid),
                    "sub": f"{faction_label(e.get('f'))} · "
                           + (tr(ROLE_FR[role]) if role in ROLE_FR
                              else role or "?"),
                    "img": item_icon(e.get("pattern")) if e.get("pattern")
                    else "",
                    "tiers": [t["txt"] for t in infusion_tiers(sid)],
                    "k": (faction_label(e.get("f")), e.get("role") or "")})
    out.sort(key=lambda x: x.pop("k"))
    return out


def _cmp_spells(ma, mb):
    """Two simulations' spells side by side, grouped by source; "better"
    marks the higher value where both sides have the line."""
    if not ma and not mb:
        return []
    ga = {g["t"]: g for g in (ma or {}).get("groups") or ()}
    gb = {g["t"]: g for g in (mb or {}).get("groups") or ()}
    order = list(ga) + [t for t in gb if t not in ga]
    out = []
    base_name = tr("Attaques de base")
    combo = tr("Combo : {name}", name="")         # the combos' name prefix
    for t in order:
        a, b = ga.get(t) or {}, gb.get(t) or {}
        # a weapon's basic attacks and its combo face the other weapon's,
        # whatever their ids; every other spell is itself
        def slot(s):
            n = s.get("name") or ""
            return ("base" if n.startswith(base_name)
                    else "combo" if n.startswith(combo) else s["id"])
        sa = {slot(s): s for s in a.get("skills") or ()}
        sb = {slot(s): s for s in b.get("skills") or ()}
        ids = list(sa) + [i for i in sb if i not in sa]
        rows = []
        for i in ids:
            x, y = sa.get(i), sb.get(i)
            # lines matched by what they are (damage / heal, element), not
            # by position: a rune can add a line on one side only
            def keyed(lines):
                seen, out = {}, []
                for ln in lines:
                    k = (ln.get("k"), ln.get("aff"))
                    seen[k] = seen.get(k, 0) + 1
                    out.append(((k, seen[k]), ln))
                return out
            ka = keyed((x or {}).get("lines") or [])
            kb = keyed((y or {}).get("lines") or [])
            da, db = dict(ka), dict(kb)
            keys = [k for k, _ in ka] + [k for k, _ in kb if k not in da]
            la = [da.get(k) for k in keys]
            lb = [db.get(k) for k in keys]
            def win(p, q, k):
                if not p or not q or abs(p[k] - q[k]) < 0.5:
                    return ""
                return "a" if p[k] > q[k] else "b"
            better = [{"n": win(p, q, "normalv"), "c": win(p, q, "critv")}
                      for p, q in zip(la, lb)]
            names = [s["name"] for s in (x, y) if s]
            if len(set(names)) == 1:
                name = names[0]
            elif all(n.startswith(combo) for n in names):
                name = tr("Combo : {name}", name=" / ".join(
                    n[len(combo):] for n in names))
            else:
                name = " / ".join(names)
            rows.append({"id": (x or y)["id"], "name": name,
                         "a": x and la, "b": y and lb, "better": better})
        out.append({"t": t, "subA": a.get("sub") or "",
                    "subB": b.get("sub") or "", "rows": rows})
    return out


def _merge_base_attacks(skills, base_ids):
    """The weapon's basic attacks 1, 2, 3 in one card, a line per hit."""
    base = [s for s in skills if s["id"] in base_ids]
    if len(base) < 2:
        return skills
    lines = []
    for n, s in enumerate(base, 1):
        for x in s["lines"]:
            lines.append(dict(x, kind=tr("Coup {n}", n=n)))
    merged = {"id": base[0]["id"], "name": tr("Attaques de base"), "cd": "",
              "range": base[0]["range"], "lines": lines}
    out, done = [], False
    for s in skills:
        if s["id"] in base_ids:
            if not done:
                out.append(merged)
                done = True
            continue
        out.append(s)
    return out


def _rune_text(rune, skill_name, vals):
    """A rune's French description with its numbers filled in."""
    import re
    txt = rune.get("desc") or ""

    def sub(m):
        key = m.group(1).split("%")[0]
        if key == "name":
            return skill_name
        if key == "cooldown" and rune.get("cd") is not None:
            return f"{rune['cd']:g} s".replace(".", dec_sep())
        if key in ("dmg", "damage") and vals.get("dmg"):
            return tr("{n} dégâts", n=f"{vals['dmg']['normal']:.0f}")
        if key in ("heal", "shield") and vals.get("heal"):
            return tr("{n} PV", n=f"{vals['heal']['normal']:.0f}")
        return "X"
    return re.sub(r"::([^:]+)::", sub, txt)


def _aug_option(aid):
    """One augment to choose: its icon, name and what it does."""
    a = _augment_view(aid)
    return {"v": aid, "t": a["name"], "fx": a.get("fx") or "",
            "img": item_icon(aid)}

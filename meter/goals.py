"""The goals list: what the player wants to gather, and how far.

Two kinds of goal:
  item    "5 Minerai de cuivre": the count OWNED, wherever it is — bag,
          equipment, bank tabs and currencies (the hook's "stock" message);
          a mount or a glider learnt counts once in the collection;
  rarity  "1 objet légendaire": the pieces of that rarity picked up since
          the goal was set (the hook's "pickup" messages).
Saved in .meter_goals.json."""
from __future__ import annotations

import base64
import json
import unicodedata
import sys
import time

from common import ANALYSIS, _WRITABLE
from gamedata import (
    _fr_names, build_data, faction_label, item_label, item_rarity,
    item_type_label, rarity_label)
from i18n import tr

GOALS_FILE = _WRITABLE / ".meter_goals.json"
RARITIES = ("Uncommon", "Rare", "Epic", "Legendary")


class Goals:
    def __init__(self):
        self.items = []
        self.stock = {}             # item kind -> owned (last "stock")
        # gear piece by piece: "kind|rarity|infusion bonus" -> owned (a
        # weapon's own rarity, "" for armour: its sheet's)
        self.gear = {}
        self.banks = -1             # bank tabs read (-1: unknown)
        self.learnt = set()         # mounts and gliders in the collection
        self._next = 1
        try:
            data = json.loads(GOALS_FILE.read_text(encoding="utf-8"))
            self.items = [g for g in data.get("goals") or ()
                          if isinstance(g, dict) and g.get("kind")]
            self._next = 1 + max((int(g.get("id") or 0)
                                  for g in self.items), default=0)
        except Exception:
            pass

    def save(self):
        try:
            GOALS_FILE.write_text(json.dumps({"goals": self.items}, indent=1),
                                  encoding="utf-8")
        except OSError as e:
            print(f"[meter] couldn't save the goals: {e}", file=sys.stderr)

    # -- editing -----------------------------------------------------------
    def add(self, kind, ref, n, rar=None, istat=None):
        n = max(1, min(int(n or 1), 99999))
        if kind == "item" and ref:
            g = {"kind": "item", "item": str(ref), "n": n}
            # gear: a weapon's rarity (its own), an armour's infusion bonus
            if rar in RARITIES and _is_weapon(ref):
                g["rar"] = rar
            if istat:
                g["istat"] = str(istat)
        elif kind == "rarity" and ref in RARITIES:
            g = {"kind": "rarity", "rarity": ref, "n": n, "got": 0}
        else:
            return None
        g.update(id=self._next, at=time.time())
        self._next += 1
        self.items.append(g)
        self.save()
        return g["id"]

    def remove(self, gid):
        self.items = [g for g in self.items if g.get("id") != gid]
        self.save()

    # -- what the game says ------------------------------------------------
    def on_stock(self, items, banks, gear=None):
        """The owned counts changed. Returns the goals this completed."""
        before = {g["id"]: self.have(g) >= g["n"] for g in self.items}
        self.stock = {str(k): int(v) for k, v in (items or {}).items()}
        self.gear = {str(k): int(v) for k, v in (gear or {}).items()}
        self.banks = int(banks if banks is not None else -1)
        return [g for g in self.items
                if not before.get(g["id"]) and self.have(g) >= g["n"]]

    def on_pickup(self, rarity, count):
        """A piece came in: the rarity goals count it. Returns the goals
        this completed."""
        done = []
        for g in self.items:
            if g["kind"] == "rarity" and g["rarity"] == rarity:
                was = g.get("got", 0) >= g["n"]
                g["got"] = g.get("got", 0) + max(1, int(count or 1))
                if not was and g["got"] >= g["n"]:
                    done.append(g)
        if done or any(g["kind"] == "rarity" and g["rarity"] == rarity
                       for g in self.items):
            self.save()
        return done

    def have(self, g):
        if g["kind"] == "item" and (g.get("rar") or g.get("istat")):
            # the pieces of that rarity, with that bonus
            n = 0
            for key, c in self.gear.items():
                kind, _s, rest = key.partition("|")
                rar, _s, bonus = rest.partition("|")
                if kind == g["item"] and (not g.get("rar") or rar == g["rar"]) \
                        and (not g.get("istat") or bonus == g["istat"]):
                    n += c
            return n
        if g["kind"] == "item":
            # a mount or glider learnt is no longer in the bag: the
            # collection has it
            return max(self.stock.get(g["item"], 0),
                       1 if g["item"] in self.learnt else 0)
        return g.get("got", 0)

    # -- the view ----------------------------------------------------------
    def label(self, g):
        if g["kind"] == "item":
            # its rarity is its picture's frame (view)
            bits = []
            if g.get("istat"):
                bits.append(_fr_names("attribute").get(g["istat"]) or g["istat"])
            return item_label(g["item"]) + (f" ({', '.join(bits)})" if bits else "")
        return tr("objet {rarity}", rarity=rarity_label(g["rarity"]).lower())

    def view(self):
        rows = []
        for g in self.items:
            have = self.have(g)
            rows.append({
                "id": g["id"], "t": self.label(g), "n": g["n"],
                "have": have, "done": have >= g["n"],
                "img": picture(g["item"]) if g["kind"] == "item" else "",
                # a rarity goal's, or the item's: its picture framed
                "rk": (g.get("rarity") or g.get("rar")
                       or (item_rarity(g["item"]) if g["kind"] == "item"
                           else "") or "").lower(),
                "how": (tr("possédés (sac, équipement, banque)")
                        if g["kind"] == "item"
                        else tr("obtenus depuis l'ajout"))})
        return {"rows": rows, "banks": self.banks,
                "rarities": [{"v": r, "t": rarity_label(r)}
                             for r in RARITIES]}


# The goal's chooser (overlay.js renderPicker): a kind of thing, then, for
# some, a narrower one (as the Build tab's piece choice: a slot, a weapon
# type), then the thing itself. (key, label, its things' item types or the
# Build catalogue's slots, the item that pictures it)
PICK_CATS = (
    ("resource", "Ressource", None, "SoftFur"),
    ("gear", "Équipement", None, "Chest_Starter_Fig"),
    ("weapon", "Arme", None, ""),
    ("mount", "Monture", ("Mount",), "Mount_Wolf_01"),
    ("glider", "Planeur", ("GearGlider",), "Glider_FlyingFish_Red"),
    ("currency", "Monnaie", ("Currency",), "Gold"),
)
# the resources' kinds, as the game's item types (Prospecting is out: a
# deposit's survey, not a thing to gather)
RESOURCE_TYPES = ("CraftingComponent", "Ore", "Leather", "Cloth",
                  "UpgradeComponent", "Soulstone", "Misc",
                  "Food", "Potion", "HealthPotion", "Elixir", "Consumable",
                  "AugmentDemon", "AugmentDemonSigil", "AugmentJeweller",
                  "AugmentBlacksmith", "AugmentOutfitter",
                  "AugmentEnchantWeapon", "AugmentEnchantHands",
                  "AugmentEnchantFeet", "InfusionPattern")
# the gear's slots, in the Build tab's order (its catalogue's "slot")
GEAR_SLOTS = (("Head", "Tête"), ("Shoulders", "Épaules"), ("Chest", "Torse"),
              ("Back", "Dos"), ("Hands", "Mains"), ("Waist", "Taille"),
              ("Legs", "Jambes"), ("Feet", "Pieds"), ("Neck", "Cou"),
              ("Finger", "Anneau"), ("Trinket", "Babiole"))
WEAPON_SLOTS = ("Weapon", "Offhand")
# item types grouped under the one they inherit (data.cdb itemType.inherit)
TYPE_PARENT = {"Ore": "CraftingComponent", "Leather": "CraftingComponent",
               "Cloth": "CraftingComponent"}
# a narrower kind's picture, when its first item's is not the one
SUB_PICS = {"CraftingComponent": "SoftFur"}
# item types the game leaves unnamed
TYPE_NAMES = {"UpgradeComponent": "Composant d'amélioration"}
# not things to gather: the game's placeholders, the experience (a currency
# st.Loadout.addCurrency keeps apart)
NOT_GOALS = {"Experience"}
PLACEHOLDER_NAMES = {"À FAIRE", "A FAIRE", "TODO"}


def catalog(cat=None, sub=None, learnt=()):
    """One step of the goal's chooser: the kinds of things, a kind's
    narrower ones (with how many each holds), or the things themselves
    (name, picture, what they are, rarity, learnt for a mount)."""
    names = _fr_names("item")
    types = _item_types()
    gear = (build_data().get("items") or {})
    learnt = set(learnt or ())

    def first_pic(ids):
        for i in ids:
            pic = picture(i)
            if pic:
                return pic
        return ""

    def pool(c):
        """The ids a kind (and its narrower one) holds."""
        if c == "gear":
            return [i for i, v in gear.items()
                    if v.get("slot") in dict(GEAR_SLOTS)]
        if c == "weapon":
            return [i for i, v in gear.items()
                    if v.get("slot") in WEAPON_SLOTS]
        if c == "resource":
            return [i for i, t in types.items() if t in RESOURCE_TYPES]
        spec = next((x[2] for x in PICK_CATS if x[0] == c), None) or ()
        return [i for i, t in types.items() if t in spec]

    def named(ids):
        return [i for i in ids if names.get(i) and i not in NOT_GOALS
                and names[i].strip().upper() not in PLACEHOLDER_NAMES]

    if not cat:
        return {"step": "cat", "opts": [
            {"v": k, "t": tr(t), "img": picture(pic) if pic
             else first_pic(sorted(named(pool(k))))}
            for k, t, _types, pic in PICK_CATS]}
    ids = named(pool(cat))
    if cat in ("gear", "weapon", "resource") and not sub:
        if cat == "gear":
            groups = [(s, tr(t), [i for i in ids if gear[i].get("slot") == s])
                      for s, t in GEAR_SLOTS]
        else:
            key = (lambda i: gear[i].get("type")) if cat == "weapon" \
                else (lambda i: _kind_of(types.get(i)))
            found = {}
            for i in ids:
                found.setdefault(key(i), []).append(i)
            groups = sorted(((k, tr(TYPE_NAMES[k]) if k in TYPE_NAMES
                              else item_type_label(k), v)
                             for k, v in found.items() if k),
                            key=lambda g: g[1])
        return {"step": "sub", "opts": [
            {"v": k, "t": t, "n": len(v),
             "img": picture(SUB_PICS[k]) if k in SUB_PICS else first_pic(sorted(v))}
            for k, t, v in groups if v]}
    if sub:
        if cat == "gear":
            ids = [i for i in ids if gear[i].get("slot") == sub]
        elif cat == "weapon":
            ids = [i for i in ids if gear[i].get("type") == sub]
        else:
            ids = [i for i in ids if _kind_of(types.get(i)) == sub]
    if cat in ("gear", "weapon"):
        return {"step": "items", **_gear_items(ids)}
    out = []
    for i in ids:
        g = gear.get(i) or {}
        rar = g.get("rar") or item_rarity(i) or ""
        bits = []
        if g:
            if g.get("fac"):
                bits.append(faction_label(g["fac"]))
            if g.get("lvl"):
                bits.append(tr("niv. {n}", n=g["lvl"]))
        if rar:
            bits.append(rarity_label(rar))
        if cat in ("mount", "glider") and i in learnt:
            bits.append(tr("déjà dans ta collection"))
        out.append({"id": i, "t": names[i], "img": picture(i),
                    "sub": " · ".join(bits), "rk": rar.lower(),
                    "lvl": g.get("lvl") or 0, "one": cat in ("mount", "glider")})
    out.sort(key=lambda r: (r["lvl"], _fold(r["t"])) if cat in ("gear", "weapon")
             else _fold(r["t"]))
    return {"step": "items", "items": out}


def _kind_of(t):
    """An item type as the chooser groups it: its parent's when it has one."""
    return TYPE_PARENT.get(t, t)


def _is_weapon(iid):
    return ((build_data().get("items") or {}).get(iid) or {}).get("slot") \
        in WEAPON_SLOTS


def _gear_items(ids):
    """The gear pieces as the Build tab lists them: one line for each
    rarity a piece exists at, its stats (the filters'), its family, its card
    at the highest level, and the infusion bonuses an infusable one can roll
    as it drops (ent.Hero.makeLootItem: $HInfusion.pickInfusionBonusStat)."""
    import builds as B
    from gearstats import gear_stats
    from views import item_rarities
    d = build_data()
    gear = d.get("items") or {}
    top = int(d.get("maxLevel") or 25)
    attrs = _fr_names("attribute")
    items, stats, facs = [], {}, set()
    for i in ids:
        e = gear.get(i) or {}
        fac = e.get("fac") if e.get("fac") != "World" else None
        typ = item_type_label(e.get("type"))
        for rar in item_rarities(i):
            st = gear_stats(i, rar, top, 0, [], 0)
            keys = [s[0] for s in (st[1] if st else ()) if s[0] != "Armor"]
            for k in keys:
                stats.setdefault(k, attrs.get(k) or k)
            if fac:
                facs.add(faction_label(fac))
            piece = {"id": i, "rar": rar, "lvl": top}
            istats = (B.istat_options(piece) if e.get("slot") in B.ARMOUR
                      and B.infusable(piece) else [])
            items.append({
                "id": i, "rar": rar, "t": item_label(i), "img": picture(i),
                "rk": rar.lower(), "stats": keys,
                "fac": faction_label(fac) if fac else "",
                "sub": " · ".join(x for x in (
                    typ, faction_label(fac) if fac else "", rarity_label(rar)) if x),
                "istats": [{"v": s, "t": attrs.get(s) or s} for s in istats],
                "tip": {"name": item_label(i), "img": picture(i), "rk": rar.lower(),
                        "type": typ, "rar": rarity_label(rar), "lvl": top,
                        "il": st[0] if st else None,
                        "stats": [{"t": t, "v": v} for _k, t, v in (st[1] if st else ())]}})
    rank = {r: n for n, r in enumerate(("Common",) + RARITIES)}
    items.sort(key=lambda x: (_fold(x["t"]), rank.get(x["rar"], 9)))
    found = {x["rar"] for x in items}
    return {"items": items,
            "rarities": [{"v": r, "t": rarity_label(r)} for r in RARITIES
                         if r in found],
            "stats": [{"k": k, "t": t} for k, t in stats.items()],
            "factions": sorted(facs)}


def _fold(text):
    """Lower case, accents off: "minerai" finds "Minerai", "eclat" "Éclat"."""
    return "".join(c for c in unicodedata.normalize("NFD", text.lower())
                   if unicodedata.category(c) != "Mn")


_TYPES = None


def _item_types():
    global _TYPES
    if _TYPES is None:
        try:
            _TYPES = json.loads((ANALYSIS / "item_types.json")
                                .read_text(encoding="utf-8"))
        except Exception:
            _TYPES = {}
    return _TYPES


_PICS = {}


def picture(iid):
    """An item's picture as a data URI: the gear icons, else the collection's
    (materials, mounts...), else ""."""
    iid = str(iid or "")
    if iid in _PICS:
        return _PICS[iid]
    uri = ""
    for folder, ext, mime in (("item_icons", "png", "png"),
                              ("collection_img", "webp", "webp")):
        path = ANALYSIS / folder / f"{iid}.{ext}"
        if iid.replace("_", "").isalnum() and path.is_file():
            try:
                uri = (f"data:image/{mime};base64,"
                       + base64.b64encode(path.read_bytes()).decode())
                break
            except OSError:
                pass
    _PICS[iid] = uri
    return uri

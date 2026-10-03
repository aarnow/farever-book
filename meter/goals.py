"""The goals overlay's list: what the player wants to gather, and how far.

Two kinds of goal:
  item    "5 Minerai de cuivre": the count OWNED, wherever it is — bag,
          equipment and bank tabs (the hook's "stock" message);
  rarity  "1 objet légendaire": the pieces of that rarity picked up since
          the goal was set (the hook's "pickup" messages).
Saved in .meter_goals.json; set from the overlay itself."""
from __future__ import annotations

import base64
import json
import unicodedata
import sys
import time

from common import ANALYSIS, _WRITABLE
from gamedata import _fr_names, item_label, item_type_label, rarity_label

GOALS_FILE = _WRITABLE / ".meter_goals.json"
RARITIES = ("Uncommon", "Rare", "Epic", "Legendary")
SEARCH_MAX = 12


class Goals:
    def __init__(self):
        self.items = []
        self.stock = {}             # item kind -> owned (last "stock")
        self.banks = -1             # bank tabs read (-1: unknown)
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

    # -- editing (the overlay's buttons) -----------------------------------
    def add(self, kind, ref, n):
        n = max(1, min(int(n or 1), 99999))
        if kind == "item" and ref:
            g = {"kind": "item", "item": str(ref), "n": n}
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

    def set_n(self, gid, n):
        for g in self.items:
            if g.get("id") == gid:
                g["n"] = max(1, min(int(n or 1), 99999))
        self.save()

    def clear_done(self):
        self.items = [g for g in self.items if self.have(g) < g["n"]]
        self.save()

    # -- what the game says ------------------------------------------------
    def on_stock(self, items, banks):
        """The owned counts changed. Returns the goals this completed."""
        before = {g["id"]: self.have(g) >= g["n"] for g in self.items}
        self.stock = {str(k): int(v) for k, v in (items or {}).items()}
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
        if g["kind"] == "item":
            return self.stock.get(g["item"], 0)
        return g.get("got", 0)

    # -- the overlay's view ------------------------------------------------
    def label(self, g):
        if g["kind"] == "item":
            return item_label(g["item"])
        return "objet " + rarity_label(g["rarity"]).lower()

    def view(self):
        rows = []
        for g in self.items:
            have = self.have(g)
            rows.append({
                "id": g["id"], "t": self.label(g), "n": g["n"],
                "have": have, "done": have >= g["n"],
                "img": picture(g["item"]) if g["kind"] == "item" else "",
                "rk": (g.get("rarity") or "").lower(),
                "how": ("possédés (sac, équipement, banque)"
                        if g["kind"] == "item" else "obtenus depuis l'ajout")})
        return {"rows": rows, "banks": self.banks,
                "rarities": [{"v": r, "t": rarity_label(r)}
                             for r in RARITIES]}


def search(q):
    """Items whose French name holds `q`, names starting with it first."""
    q = _fold(" ".join(str(q or "").split()))
    if len(q) < 2:
        return []
    names = _fr_names("item")
    hits = []
    for iid, nm in names.items():
        low = _fold(nm or "")
        if q in low:
            hits.append((0 if low.startswith(q) else 1, len(low), nm, iid))
    hits.sort()
    return [{"id": iid, "t": nm, "img": picture(iid),
             "type": item_type_label(_item_types().get(iid)) or ""}
            for _a, _b, nm, iid in hits[:SEARCH_MAX]]


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

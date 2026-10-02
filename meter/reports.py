"""Rift reports: the report page and the shareable image."""
from __future__ import annotations

import math
import os
import time
from pathlib import Path

from common import (
    CLASS_ICON_DIR, OLD_CLASS_TAGS, _mmss, _n, _pct1, _pretty_id, class_key,
    date_fr, element_color, element_label, phase_label)
from gamedata import (
    RARITY_ORDER, item_icon, item_label, item_rarity, rarity_label)
from combat import _overheal_note, _rate, _rate_text


def _parse_font(name, size):
    """Load a Windows font by filename, falling back to PIL's built-in bitmap
    font so a missing/odd font install degrades the image instead of losing it."""
    from PIL import ImageFont
    for path in (Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / name,
                 Path(name)):
        try:
            return ImageFont.truetype(str(path), size)
        except OSError:
            continue
    return ImageFont.load_default()


def report_view(data):
    """A saved rift report, as display-ready data for the page."""
    def cls(p):
        # Reports saved before the translation carry English tags.
        c = p.get("cls") or ""
        return OLD_CLASS_TAGS.get(c, c)

    def rank(players, key, dur, total):
        out = []
        for i, p in enumerate(players, 1):
            amt = float(p.get(key) or 0)
            rate = _rate(amt, dur)
            out.append({"rank": i, "name": p.get("name") or "?",
                        "cls": cls(p), "ck": class_key(p.get("cls")),
                        "rate": _n(rate) if rate else "—",
                        "total": _n(amt),
                        "pct": f"{(amt / total * 100) if total else 0:.0f}%"})
        return out

    phases = []
    for ph in data.get("phases") or []:
        dur = float(ph.get("duration") or 0)
        total, heal = float(ph.get("total") or 0), float(ph.get("heal") or 0)
        players = ph.get("players") or []
        healers = sorted((p for p in players if (p.get("heal") or 0) > 0.5),
                         key=lambda p: -p["heal"])
        mvp = players[0] if players else None
        phases.append({
            "label": phase_label(ph.get("label") or "Phase"),
            "dur": _mmss(dur),
            "dps": _rate_text(total, dur, "DPS") or "— DPS",
            "hps": _rate_text(heal, dur, "HPS") or "— HPS",
            "totals": f"{_n(total)} dégâts · {_n(heal)} soins"
                      + _overheal_note(ph),
            "mvp": ({"name": mvp.get("name") or "?",
                     "cls": cls(mvp), "ck": class_key(mvp.get("cls")),
                     "v": _rate_text(mvp.get("total", 0), dur, "DPS")
                     or f"{_n(mvp.get('total', 0))} dégâts"}
                    if mvp else None),
            "healer": ({"name": healers[0].get("name") or "?",
                        "cls": cls(healers[0]),
                        "ck": class_key(healers[0].get("cls")),
                        "v": _rate_text(healers[0]["heal"], dur, "HPS")
                        or f"{_n(healers[0]['heal'])} soins"}
                       if healers else None),
            "dmg": rank(players, "total", dur, total),
            # Everyone in the phase: those who healed nothing at the
            # bottom, greyed, so the table always lists the whole group.
            "heal": rank(healers, "heal", dur, heal) + [
                dict(r, rank=len(healers) + i, zero=True)
                for i, r in enumerate(rank(
                    [p for p in players
                     if (p.get("heal") or 0) <= 0.5], "heal", dur, heal),
                    1)],
            "types": [{"t": element_label(el),
                       "pct": _pct1(amt / total * 100 if total else 0),
                       "f": round(amt / (ph["elements"][0][1] or 1), 4),
                       "c": element_color(el)}
                      for el, amt in (ph.get("elements") or [])[:8]],
        })
    when = date_fr(time.localtime(data.get("at") or 0))
    out = {"k": "report", "id": "report",
           "detail": _report_players(data),
           "title": data.get("title") or "Rapport de faille",
           "sub": data.get("sub") or "",
           "when": when, "phases": phases}
    if isinstance(data.get("loot"), list):
        out["loot"] = loot_view(data["loot"])
    return out


def _report_players(data):
    """Each player of a report, phase by phase: what their damage and
    healing were made of — per skill (share, hits, crit rate, average hit)
    and per element. Everything a saved report already holds."""
    names = data.get("skill_names") or {}

    def label(sid):
        return names.get(sid) or _pretty_id(str(sid).split(":")[-1])

    out = {}
    for ph in data.get("phases") or []:
        dur = float(ph.get("duration") or 0)
        for p in ph.get("players") or []:
            who = p.get("name") or "?"
            total = float(p.get("total") or 0)
            heal = float(p.get("heal") or 0)
            # One row per NAME: the game names every step of a basic-attack
            # combo "Attaque", and three "Attaque" rows read as a bug.
            merged = {}
            for sid, v in (p.get("skills") or {}).items():
                hits, amt, crits = (list(v) + [0, 0, 0])[:3]
                m = merged.setdefault(label(sid), [0, 0.0, 0])
                m[0] += hits
                m[1] += amt
                m[2] += crits
            skills = []
            for name, (hits, amt, crits) in merged.items():
                skills.append({"n": name, "t": _n(amt),
                               "f": round(amt / total, 4) if total else 0,
                               "pct": _pct1(amt / total * 100 if total else 0),
                               "hits": _n(hits),
                               "crit": f"{crits / hits * 100:.0f} %"
                                       if hits else "—",
                               "avg": _n(amt / hits) if hits else "—",
                               "_a": amt})
            skills.sort(key=lambda s: -s.pop("_a"))
            hmerged = {}
            for sid, v in (p.get("heals") or {}).items():
                hits, amt = (list(v) + [0, 0])[:2]
                m = hmerged.setdefault(label(sid), [0, 0.0])
                m[0] += hits
                m[1] += amt
            heals = []
            for name, (hits, amt) in hmerged.items():
                heals.append({"n": name, "t": _n(amt),
                              "f": round(amt / heal, 4) if heal else 0,
                              "pct": _pct1(amt / heal * 100 if heal else 0),
                              "hits": _n(hits), "_a": amt})
            heals.sort(key=lambda s: -s.pop("_a"))
            els = sorted((p.get("elements") or {}).items(),
                         key=lambda kv: -(kv[1][1] if isinstance(kv[1], list)
                                          else kv[1]))
            elements = []
            for el_, v in els:
                amt = v[1] if isinstance(v, list) else v
                elements.append({"t": element_label(el_),
                                 "pct": _pct1(amt / total * 100 if total
                                              else 0),
                                 "f": round(amt / total, 4) if total else 0,
                                 "c": element_color(el_)})
            hits = int(p.get("hits") or 0)
            entry = out.setdefault(who, {"name": who,
                                         "ck": class_key(p.get("cls")),
                                         "cls": OLD_CLASS_TAGS.get(
                                             p.get("cls") or "",
                                             p.get("cls") or ""),
                                         "phases": []})
            entry["phases"].append({
                "label": phase_label(ph.get("label") or "Phase"),
                "facts": [
                    ["Dégâts", _n(total)],
                    ["DPS", _n(_rate(total, dur)) if _rate(total, dur)
                     else "—"],
                    ["Coups", _n(hits)],
                    ["Critiques", f"{int(p.get('crits') or 0) / hits * 100:.0f} %"
                     if hits else "—"],
                    ["Kills", _n(int(p.get("kills") or 0))],
                    ["Soins", _n(heal)]],
                "skills": skills, "heals": heals, "elements": elements})
    return out


LOOT_PHASES = (("coffre", "Coffre de fin"), ("boss", "Phase du boss"),
               ("exploration", "Exploration"))


def loot_view(loot):
    """A dungeon run's loot, grouped by phase (the chest first), identical
    items merged."""
    groups = []
    for key, label in LOOT_PHASES:
        merged = {}
        for it in loot:
            if it.get("phase") != key:
                continue
            k = (it.get("item"),
                 it.get("rarity") or item_rarity(it.get("item")),
                 it.get("level"))
            merged[k] = merged.get(k, 0) + int(it.get("count") or 1)
        if merged:
            groups.append({"t": label, "items": [
                {"name": item_label(item), "qty": qty,
                 "img": item_icon(item),
                 "rarity": rarity_label(rar) if rar else "",
                 "rk": (rar or "").lower(),
                 "level": f"niv. {lvl}" if isinstance(lvl, int) and lvl > 0
                 else ""}
                for (item, rar, lvl), qty in sorted(
                    merged.items(),
                    key=lambda kv: -RARITY_ORDER.get(kv[0][1], -1))]})
    return groups


# The rift report as an image. Drawn from the same display data as the Failles
# page (report_view), in the same palette and layout — cards per phase, framed
# tables, every player — so a pasted image looks like the window it came from.
# Drawn rather than screenshotted: pixel-clean, and it works with the window
# closed (the .png is written the moment a rift ends).
IMG_BG, IMG_PANEL, IMG_PANEL2 = "#15161C", "#1D1F28", "#242733"


IMG_LINE, IMG_TEXT, IMG_DIM, IMG_FAINT = "#2E3240", "#E7E4DC", "#9C988F", "#6E6B65"


IMG_ACCENT, IMG_HEAL, IMG_RIFT, IMG_PHASE = "#E2B65B", "#57C08A", "#D65DB1", "#F3C9E5"


IMG_COL_W = 560                 # one phase card


IMG_PAD = 24


def render_rift_report_image(data, path=None):
    """Draw a rift report as a PIL image; also writes a PNG when `path` is
    given."""
    from PIL import Image, ImageDraw

    view = report_view(data)
    reg = lambda size: _parse_font("segoeui.ttf", size)     # noqa: E731
    bold = lambda size: _parse_font("segoeuib.ttf", size)   # noqa: E731
    f_title, f_phase, f_mvp = bold(22), bold(17), bold(18)
    f_fact_v, f_body_b, f_body = bold(19), bold(14), reg(14)
    f_small, f_small_b, f_tiny = reg(12), bold(12), reg(11)

    W = IMG_PAD * 3 + IMG_COL_W * 2
    img = Image.new("RGBA", (W, 4000), IMG_BG)
    icons = {}

    def icon(key, size, faded=False):
        """A class icon at `size` px, or None if there is none."""
        if not key:
            return None
        k = (key, size, faded)
        if k not in icons:
            try:
                im = Image.open(CLASS_ICON_DIR / f"{key}.png").convert("RGBA")
                im = im.resize((size, size), Image.LANCZOS)
                if faded:
                    a = im.getchannel("A").point(lambda v: v * 45 // 100)
                    im.putalpha(a)
                icons[k] = im
            except OSError:
                icons[k] = None
        return icons[k]
    d = ImageDraw.Draw(img)

    def text(x, y, s, font, fill, anchor="la"):
        d.text((x, y), str(s), font=font, fill=fill, anchor=anchor)

    def fit(s, font, width):
        """Elide `s` to fit `width` pixels."""
        s = str(s)
        if d.textlength(s, font=font) <= width:
            return s
        while s and d.textlength(s + "…", font=font) > width:
            s = s[:-1]
        return s + "…"

    def star(x, y, r, fill):
        pts = []
        for i in range(10):
            a = -math.pi / 2 + i * math.pi / 5
            rad = r if i % 2 == 0 else r * 0.42
            pts.append((x + rad * math.cos(a), y + rad * math.sin(a)))
        d.polygon(pts, fill=fill)

    def plus(x, y, r, fill):
        t = max(2, int(r * 0.55))
        d.rectangle((x - t // 2, y - r, x + t // 2, y + r), fill=fill)
        d.rectangle((x - r, y - t // 2, x + r, y + t // 2), fill=fill)

    def table(x, y, w, rows, rate_label):
        """A framed ranking: header band, a rule between rows. Returns the
        y below it."""
        row_h, head_h = 26, 24
        cols = (("", 26, "la"), ("JOUEUR", None, "la"), (rate_label, 70, "ra"),
                ("TOTAL", 86, "ra"), ("PART", 50, "ra"))
        fixed = sum(c[1] for c in cols if c[1])
        name_w = w - fixed - 20
        h = head_h + row_h * len(rows)
        d.rounded_rectangle((x, y, x + w, y + h), 6, fill=IMG_BG,
                            outline=IMG_LINE)
        d.rounded_rectangle((x + 1, y + 1, x + w - 1, y + head_h), 5,
                            fill=IMG_PANEL2)

        def cells(yy, values, fonts, fills):
            cx = x + 10
            for (label, cw, anchor), v, f, fl in zip(cols, values, fonts, fills):
                cw = cw or name_w
                if anchor == "ra":
                    text(cx + cw, yy, v, f, fl, "ra")
                else:
                    text(cx, yy, v, f, fl)
                cx += cw

        cells(y + 6, [c[0] for c in cols], [f_tiny] * 5, [IMG_DIM] * 5)
        yy = y + head_h
        for i, r in enumerate(rows):
            if i % 2:
                d.rectangle((x + 1, yy, x + w - 1, yy + row_h),
                            fill="#191B22")
            d.line((x + 1, yy, x + w - 1, yy), fill=IMG_LINE)
            zero, top = r.get("zero"), r["rank"] <= 3 and not r.get("zero")
            ink = IMG_FAINT if zero else IMG_TEXT
            nfont = f_body_b if top else f_body
            name = fit(r["name"], nfont, name_w - 50)
            cells(yy + 5, [r["rank"], name, r["rate"], r["total"], r["pct"]],
                  [f_body, nfont, f_body, f_body, f_body],
                  [IMG_FAINT if zero else IMG_DIM, ink, ink, ink, ink])
            nx = int(x + 10 + 26 + d.textlength(name, font=nfont) + 6)
            ic = icon(r.get("ck"), 16, zero)
            if ic is not None:
                img.alpha_composite(ic, (nx, yy + 5))
            elif r.get("cls"):
                text(nx, yy + 7, r["cls"], f_small, IMG_FAINT if zero else IMG_DIM)
            yy += row_h
        # The frame last, so the row fills cannot paint over its edge.
        d.rounded_rectangle((x, y, x + w, y + h), 6, outline=IMG_LINE)
        return y + h

    def phase_card(x, y, ph):
        w = IMG_COL_W
        pad = 18
        ix, iw = x + pad, w - pad * 2
        cy = y + pad
        text(ix, cy, ph["label"].upper(), f_phase, IMG_PHASE)
        cy += 32
        fx = ix
        for label, value in (("DURÉE", ph["dur"]),
                             ("DPS", ph["dps"].replace(" DPS", "")),
                             ("HPS", ph["hps"].replace(" HPS", ""))):
            fw = max(110, int(d.textlength(value, font=f_fact_v)) + 26)
            d.rounded_rectangle((fx, cy, fx + fw, cy + 52), 6,
                                fill=IMG_PANEL2, outline=IMG_LINE)
            text(fx + 12, cy + 7, label, f_tiny, IMG_DIM)
            text(fx + 12, cy + 22, value, f_fact_v, IMG_TEXT)
            fx += fw + 8
        cy += 62
        text(ix, cy, ph["totals"], f_small, IMG_DIM)
        cy += 26
        d.line((ix, cy, ix + iw, cy), fill=IMG_LINE)
        cy += 14
        if ph.get("mvp"):
            boxes = [("MVP DÉGÂTS", ph["mvp"], IMG_ACCENT, star)]
            if ph.get("healer"):
                boxes.append(("MVP SOINS", ph["healer"], IMG_HEAL, plus))
            bw = (iw - 10 * (len(boxes) - 1)) // len(boxes)
            for i, (label, who, colour, mark) in enumerate(boxes):
                bx = ix + i * (bw + 10)
                d.rounded_rectangle((bx, cy, bx + bw, cy + 78), 6,
                                    fill=IMG_PANEL2, outline=IMG_LINE)
                d.rectangle((bx, cy + 1, bx + 3, cy + 77), fill=colour)
                text(bx + 14, cy + 8, label, f_tiny, IMG_DIM)
                mark(bx + 24, cy + 38, 9 if mark is star else 7, colour)
                name = fit(who["name"], f_mvp, bw - 90)
                text(bx + 40, cy + 26, name, f_mvp, colour)
                ix2 = int(bx + 46 + d.textlength(name, font=f_mvp))
                ic = icon(who.get("ck"), 20)
                if ic is not None:
                    img.alpha_composite(ic, (ix2, cy + 30))
                elif who.get("cls"):
                    text(ix2, cy + 32, who["cls"], f_small, IMG_DIM)
                text(bx + 14, cy + 54, who["v"], f_body, IMG_TEXT)
            cy += 92
        else:
            text(ix, cy, "rien n'a été enregistré pour cette phase", f_body,
                 IMG_FAINT)
            cy += 30

        def heading(s):
            nonlocal cy
            cy += 8
            text(ix, cy, s.upper(), f_small_b, IMG_RIFT)
            cy += 22

        if ph.get("dmg"):
            n = len(ph["dmg"])
            heading(f"Dégâts — {n} joueur{'s' if n > 1 else ''}")
            cy = table(ix, cy, iw, ph["dmg"], "DPS") + 10
        heals = ph.get("heal") or []
        n = len(heals)
        heading("Soins" + (f" — {n} joueur{'s' if n > 1 else ''}" if n else ""))
        if heals:
            cy = table(ix, cy, iw, heals, "HPS") + 10
        else:
            text(ix, cy, "aucun soin enregistré", f_body, IMG_FAINT)
            cy += 28
        if ph.get("types"):
            heading("Dégâts par type")
            th = 14 + 24 * len(ph["types"])
            d.rounded_rectangle((ix, cy, ix + iw, cy + th), 6, fill=IMG_BG,
                                outline=IMG_LINE)
            ty = cy + 9
            for t in ph["types"]:
                text(ix + 12, ty, t["t"], f_small, t["c"])
                bx0, bx1 = ix + 110, ix + iw - 80
                bw = max(4, int((bx1 - bx0) * max(0.02, t["f"])))
                d.rounded_rectangle((bx0, ty + 4, bx0 + bw, ty + 11), 3,
                                    fill=t["c"])
                text(ix + iw - 12, ty, t["pct"], f_small, IMG_TEXT, "ra")
                ty += 24
            cy += th
        return cy + pad

    # Measure both cards on a scratch pass, so they can share one height.
    y0 = IMG_PAD + 64
    bottoms = []
    for i, ph in enumerate(view["phases"][:2]):
        bottoms.append(phase_card(IMG_PAD + i * (IMG_COL_W + IMG_PAD), y0, ph))
    card_bottom = max(bottoms or [y0 + 40])

    # Draw for real: background panel, title, then the cards over it.
    d.rectangle((0, 0, W, 4000), fill=IMG_BG)
    H = card_bottom + IMG_PAD
    d.rounded_rectangle((8, 8, W - 8, H - 8), 10, fill=IMG_PANEL,
                        outline=IMG_LINE)
    text(IMG_PAD, IMG_PAD, view["title"], f_title, IMG_RIFT)
    after = view["when"] + (f"   ·   {view['sub']}" if view.get("sub") else "")
    text(IMG_PAD + d.textlength(view["title"], font=f_title) + 14,
         IMG_PAD + 6, after, f_body, IMG_DIM)
    d.line((IMG_PAD, IMG_PAD + 44, W - IMG_PAD, IMG_PAD + 44), fill=IMG_LINE)
    for i, ph in enumerate(view["phases"][:2]):
        x = IMG_PAD + i * (IMG_COL_W + IMG_PAD)
        d.rounded_rectangle((x, y0, x + IMG_COL_W, card_bottom), 10,
                            fill=IMG_BG, outline=IMG_LINE)
        phase_card(x, y0, ph)
    img = img.crop((0, 0, W, H)).convert("RGB")
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        img.save(path)
    return img


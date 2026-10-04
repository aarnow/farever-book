"""Rift reports: the report page and the shareable image."""
from __future__ import annotations

import os
import time
from pathlib import Path

from common import (
    CLASS_ICON_DIR, _mmss, _n, _pct1, _pretty_id, class_key, date_fr,
    element_color, element_label)
from gamedata import (
    RARITY_ORDER, item_icon, item_label, item_rarity, rarity_label)
from combat import _overheal_pct

# A window shorter than this has no rate worth showing: a boss phase a few
# milliseconds long would give a seven-figure DPS.
RATE_MIN_SECS = 0.5


def _rate(amount, duration):
    """`amount` per second, or None when the window is too short: "no rate"
    and "a rate of zero" show differently."""
    if not duration or duration < RATE_MIN_SECS:
        return None
    return (amount or 0.0) / duration


def _rate_text(amount, duration, unit):
    """"12 345 DPS", or None when there is no rate to state."""
    r = _rate(amount, duration)
    return None if r is None else f"{_n(r)} {unit}"


def _report_name(p):
    """"Aarnow (Prê)": the class's tag when it is known."""
    name = p.get("name") or "?"
    return f"{name} ({p['cls']})" if p.get("cls") else name


def _overheal_note(d, fmt=" ({:.0f}% en excès)"):
    """The share of the healing that restored nothing, or "" without
    healing."""
    heal = d.get("heal") or 0.0
    if heal <= 0.5:
        return ""
    return fmt.format(_overheal_pct(heal, d.get("heal_landed", heal)))


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
    """A saved rift report, as display-ready data for the page. A dungeon
    run keeps only its boss phase (its exploration too, if it never got
    there)."""
    if data.get("kind"):
        boss = [ph for ph in data.get("phases") or ()
                if "boss" in str(ph.get("label") or "").lower()]
        if boss:
            data = dict(data, phases=boss)
    def cls(p):
        return p.get("cls") or ""

    def rank(players, key, dur, total):
        out = []
        top = max((float(p.get(key) or 0) for p in players), default=0)
        for i, p in enumerate(players, 1):
            amt = float(p.get(key) or 0)
            rate = _rate(amt, dur)
            out.append({"rank": i, "name": p.get("name") or "?",
                        "cls": cls(p), "ck": class_key(p.get("cls")),
                        "rate": _n(rate) if rate else "—",
                        "total": _n(amt),
                        "pct": f"{(amt / total * 100) if total else 0:.0f}%",
                        # the bar behind the row: against the best of them
                        "f": round(amt / top, 4) if top else 0})
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
            "label": (ph.get("label") or "Phase"),
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
                       # its share of the whole, for the pie
                       "s": round(amt / total, 4) if total else 0,
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
            merged, ids, els_of = {}, {}, {}
            skill_el = p.get("skillEl") or {}
            for sid, v in (p.get("skills") or {}).items():
                hits, amt, crits = (list(v) + [0, 0, 0])[:3]
                m = merged.setdefault(label(sid), [0, 0.0, 0])
                m[0] += hits
                m[1] += amt
                m[2] += crits
                # the skills behind the name, for its icon (skill_img/)
                ids.setdefault(label(sid), []).append(str(sid).split(":")[-1])
                if skill_el.get(sid):
                    w = els_of.setdefault(label(sid), {})
                    w[skill_el[sid]] = w.get(skill_el[sid], 0) + amt
            skills = []
            for name, (hits, amt, crits) in merged.items():
                el_ = (max(els_of[name], key=els_of[name].get)
                       if els_of.get(name) else None)
                skills.append({"n": name, "t": _n(amt), "ids": ids[name],
                               # its bar in its element's colour (reports
                               # from 1.12 on; older ones keep the class's)
                               "c": element_color(el_) if el_ else "",
                               "f": round(amt / total, 4) if total else 0,
                               "pct": _pct1(amt / total * 100 if total else 0),
                               "hits": _n(hits),
                               "crit": f"{crits / hits * 100:.0f} %"
                                       if hits else "—",
                               "avg": _n(amt / hits) if hits else "—",
                               "_a": amt})
            skills.sort(key=lambda s: -s.pop("_a"))
            hmerged, hids = {}, {}
            for sid, v in (p.get("heals") or {}).items():
                hits, amt = (list(v) + [0, 0])[:2]
                m = hmerged.setdefault(label(sid), [0, 0.0])
                m[0] += hits
                m[1] += amt
                hids.setdefault(label(sid), []).append(str(sid).split(":")[-1])
            heals = []
            for name, (hits, amt) in hmerged.items():
                heals.append({"n": name, "t": _n(amt), "ids": hids[name],
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
                                         "cls": p.get("cls") or "",
                                         "phases": []})
            entry["phases"].append({
                "label": (ph.get("label") or "Phase"),
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
# page (report_view), in the window's own palette and layout — the title with
# its gold diamond, a card per phase, every player a bar in their class's
# colour, the damage by type as a ring — so a pasted image looks like the
# window it came from. Drawn rather than screenshotted: pixel-clean, and it
# works with the window closed (the .png is written the moment a rift ends).
IMG_BG, IMG_CARD, IMG_RAISED = "#211F3A", "#211F3A", "#36335C"
IMG_LINE, IMG_LINE2 = "#47447A", "#5B5893"
IMG_TEXT, IMG_DIM, IMG_FAINT = "#EEEBFF", "#ADA9D6", "#7F7BAA"
IMG_GOLD, IMG_GOLD2 = "#FBE08A", "#D69E2E"
IMG_TABLE, IMG_HEAD, IMG_ZEBRA = "#171529", "#100F1D", "#1F1D33"
IMG_CLASS = {"warrior": "#B8513C", "mage": "#3F78D6", "priest": "#C9A93E",
             "rogue": "#3E9E66"}
IMG_COL_W = 580                 # one phase card
IMG_PAD = 24


def _hex(c):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def _mix(fg, bg, a):
    """`fg` laid over `bg` at opacity `a`, as an RGB tuple."""
    f, g = _hex(fg), _hex(bg)
    return tuple(round(f[i] * a + g[i] * (1 - a)) for i in range(3))


def render_rift_report_image(data, path=None):
    """Draw a rift (or dungeon) report as a PIL image; also writes a PNG
    when `path` is given."""
    from PIL import Image, ImageDraw

    view = report_view(data)
    reg = lambda size: _parse_font("segoeui.ttf", size)     # noqa: E731
    bold = lambda size: _parse_font("segoeuib.ttf", size)   # noqa: E731

    def head(size):
        f = _parse_font("bahnschrift.ttf", size)
        try:
            f.set_variation_by_name("Bold")
        except Exception:
            f = bold(size)
        return f
    f_title, f_phase, f_sub = head(21), head(16), head(13)
    f_fact_v, f_fact_l = head(19), reg(11)
    f_row, f_row_b, f_num, f_tiny = reg(13), bold(13), reg(13), reg(11)
    f_when, f_leg, f_leg_b = reg(13), reg(13), bold(13)

    ncol = max(1, min(2, len(view["phases"])))
    W = IMG_PAD * (ncol + 1) + IMG_COL_W * ncol
    img = Image.new("RGBA", (W, 5000), IMG_BG)
    d = ImageDraw.Draw(img)
    icons = {}

    def icon(key, size):
        """A class icon at `size` px, or None if there is none."""
        if not key:
            return None
        if (key, size) not in icons:
            try:
                im = Image.open(CLASS_ICON_DIR / f"{key}.png").convert("RGBA")
                icons[(key, size)] = im.resize((size, size), Image.LANCZOS)
            except OSError:
                icons[(key, size)] = None
        return icons[(key, size)]

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

    def gradient(w, h, c0, c1):
        """A horizontal gradient strip, c0 on the left to c1 on the right."""
        strip = Image.new("RGB", (max(2, w), 1))
        px = strip.load()
        for i in range(strip.width):
            t = i / (strip.width - 1)
            px[i, 0] = tuple(round(c0[k] * (1 - t) + c1[k] * t) for k in range(3))
        return strip.resize((max(1, w), max(1, h)))

    def table(x, y, w, rows, rate_label):
        """A ranking as in the window: a bar per player, filled from the left
        in their class's colour as far as their share against the best, led
        by the class badge in a dark rounded square. Returns the y below."""
        row_h, head_h, ic_w = 30, 26, 32
        num_w = (64, 80, 50)
        h = head_h + row_h * len(rows)
        d.rounded_rectangle((x, y, x + w, y + h), 7, fill=IMG_TABLE)
        d.rounded_rectangle((x, y, x + w, y + head_h), 7, fill=IMG_HEAD)
        d.rectangle((x, y + head_h - 7, x + w, y + head_h), fill=IMG_HEAD)

        def numbers(yy, vals, font, fill):
            rx = x + w - 10
            for v, cw in zip(reversed(vals), reversed(num_w)):
                text(rx, yy, v, font, fill, "ra")
                rx -= cw
        text(x + ic_w + 4, y + 7, "JOUEUR", f_tiny, IMG_DIM)
        numbers(y + 7, [rate_label, "TOTAL", "PART"], f_tiny, IMG_DIM)
        yy = y + head_h
        for i, r in enumerate(rows):
            zero = bool(r.get("zero"))
            base = IMG_ZEBRA if i % 2 else IMG_TABLE
            d.rectangle((x, yy, x + w, yy + row_h), fill=base)
            f = 0 if zero else max(0.0, min(1.0, float(r.get("f") or 0)))
            cc = IMG_CLASS.get(r.get("ck") or "", "#7A7698")
            fw = int(w * f)
            if fw > 1:
                img.paste(gradient(fw, row_h, _mix(cc, base, .8),
                                   _mix(cc, base, .55)), (x, yy))
            if i:
                d.line((x, yy, x + w, yy), fill=(0, 0, 0))
            # the badge: a dark rounded square, the class icon in it
            sq = 22
            sx, sy = x + (ic_w - sq) // 2 + 2, yy + (row_h - sq) // 2
            under = _mix(cc, base, .8) if fw > sx - x + sq else _hex(base)
            d.rounded_rectangle((sx, sy, sx + sq, sy + sq), 6,
                                fill=tuple(round(c * .45) for c in under))
            ic = icon(r.get("ck"), 18)
            if ic is not None:
                img.alpha_composite(ic, (sx + 2, sy + 2))
            top = r["rank"] <= 3 and not zero
            font = f_row_b if top else f_row
            name_w = w - ic_w - sum(num_w) - 20
            label = fit(f"{r['rank']}. {r['name']}", font, name_w)
            # a soft shadow under the words, as in the window
            text(x + ic_w + 5, yy + 8, label, font, (0, 0, 0))
            text(x + ic_w + 4, yy + 7, label, font,
                 IMG_FAINT if zero else "#FFFFFF")
            numbers(yy + 7, [r["rate"], r["total"], r["pct"]], f_num,
                    IMG_FAINT if zero else IMG_TEXT)
            yy += row_h
        # the corners rounded again over the rows
        mask = Image.new("L", (w + 1, h + 1), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, w, h), 7, fill=255)
        corner = img.crop((x, y, x + w + 1, y + h + 1))
        bg = Image.new("RGBA", corner.size, IMG_CARD)
        img.paste(Image.composite(corner, bg, mask), (x, y))
        return y + h

    def ring(cx, cy, types):
        """The damage by type: a ring, each type its arc in its colour."""
        S, R, T = 4, 70, 22                         # supersampled
        size = (R + T) * 2 * S
        layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        total = sum(float(t.get("s") or 0) for t in types) or 1
        at = -90.0
        box = (T * S // 2, T * S // 2, size - T * S // 2, size - T * S // 2)
        for t in types:
            part = float(t.get("s") or 0) / total
            if part <= 0:
                continue
            sweep = part * 360
            gap = min(1.0, sweep / 3)
            ld.arc(box, at, at + sweep - gap, fill=t["c"], width=T * S)
            at += sweep
        layer = layer.resize((size // S, size // S), Image.LANCZOS)
        img.alpha_composite(layer, (int(cx - size // S / 2),
                                    int(cy - size // S / 2)))

    def phase_card(x, y, ph):
        w, pad = IMG_COL_W, 18
        ix, iw = x + pad, w - pad * 2
        cy = y + pad
        text(ix, cy, ph["label"].upper(), f_phase, "#FFFFFF")
        cy += 30
        # the three counts across the whole width
        gap = 8
        fw = (iw - gap * 2) // 3
        for i, (label, value) in enumerate(
                (("DURÉE", ph["dur"]), ("DPS", ph["dps"].replace(" DPS", "")),
                 ("HPS", ph["hps"].replace(" HPS", "")))):
            fx = ix + i * (fw + gap)
            d.rounded_rectangle((fx, cy, fx + fw, cy + 54), 6, fill=IMG_RAISED,
                                outline=IMG_LINE)
            text(fx + 12, cy + 8, label, f_fact_l, IMG_DIM)
            text(fx + 12, cy + 24, value, f_fact_v, IMG_TEXT)
        cy += 68
        d.line((ix, cy, ix + iw, cy), fill=IMG_LINE)
        cy += 6
        if not ph.get("mvp"):
            text(ix, cy + 10, "rien n'a été enregistré pour cette phase",
                 f_row, IMG_FAINT)
            cy += 34

        def heading(s):
            nonlocal cy
            cy += 14
            text(ix, cy, s.upper(), f_sub, "#FFFFFF")
            cy += 22

        if ph.get("dmg"):
            heading("Dégâts")
            cy = table(ix, cy, iw, ph["dmg"], "DPS")
        heading("Soins")
        if ph.get("heal"):
            cy = table(ix, cy, iw, ph["heal"], "HPS")
        else:
            text(ix, cy, "aucun soin enregistré", f_row, IMG_FAINT)
            cy += 24
        if ph.get("types"):
            heading("Dégâts par type")
            types = ph["types"]
            lh = 22
            leg_h = lh * len(types)
            block_h = max(184, leg_h)
            leg_w, ring_w = 190, 184
            bx = ix + (iw - (ring_w + 26 + leg_w)) // 2
            ring(bx + ring_w / 2, cy + block_h / 2, types)
            ly = cy + (block_h - leg_h) // 2
            lx = bx + ring_w + 26
            for t in types:
                d.rounded_rectangle((lx, ly + 5, lx + 12, ly + 17), 3, fill=t["c"])
                text(lx + 20, ly + 2, t["t"], f_leg, IMG_TEXT)
                text(lx + leg_w, ly + 2, t["pct"], f_leg_b, "#FFFFFF", "ra")
                ly += lh
            cy += block_h + 4
        return cy + pad

    # Measure both cards on a scratch pass, so they can share one height.
    y0 = IMG_PAD + 46
    bottoms = [phase_card(IMG_PAD + i * (IMG_COL_W + IMG_PAD), y0, ph)
               for i, ph in enumerate(view["phases"][:2])]
    card_bottom = max(bottoms or [y0 + 40])

    # Draw for real: the title, then the cards.
    d.rectangle((0, 0, W, 5000), fill=IMG_BG)
    H = card_bottom + IMG_PAD
    # the gold diamond, the title, the date, and the line running off
    dx, dy = IMG_PAD + 6, IMG_PAD + 13
    d.polygon([(dx, dy - 7), (dx + 7, dy), (dx, dy + 7), (dx - 7, dy)],
              fill=IMG_GOLD2)
    d.polygon([(dx, dy - 5), (dx + 4, dy - 1), (dx, dy + 3), (dx - 4, dy - 1)],
              fill=IMG_GOLD)
    tx = IMG_PAD + 20
    text(tx, IMG_PAD + 1, view["title"], f_title, "#FFFFFF")
    tx += d.textlength(view["title"], font=f_title) + 12
    after = view["when"] + (f"   ·   {view['sub']}" if view.get("sub") else "")
    text(tx, IMG_PAD + 6, after, f_when, IMG_DIM)
    lx0 = int(tx + d.textlength(after, font=f_when) + 14)
    if lx0 < W - IMG_PAD:
        img.paste(gradient(W - IMG_PAD - lx0, 1, _hex(IMG_LINE2), _hex(IMG_BG)),
                  (lx0, IMG_PAD + 13))
    for i, ph in enumerate(view["phases"][:2]):
        x = IMG_PAD + i * (IMG_COL_W + IMG_PAD)
        d.rounded_rectangle((x, y0, x + IMG_COL_W, card_bottom), 10,
                            fill=IMG_CARD, outline=IMG_LINE)
        phase_card(x, y0, ph)
    img = img.crop((0, 0, W, H)).convert("RGB")
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        img.save(path)
    return img


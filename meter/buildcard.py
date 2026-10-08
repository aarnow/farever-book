"""A build as one shareable picture: hero in 3D, attributes, gear, spells,
infusions, talents and runes.

An HTML page (from buildtab's open-build view) screenshotted by Edge
headless, shipped with every Windows 11; the hero is web/js/model3d.js
rendered first on a transparent background."""
import base64
import hashlib
import html
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from common import ANALYSIS, DATA_HOME, FROZEN, ROOT, SHIPPED_ANALYSIS
from i18n import tr

WIDTH = 1200
# the page's scripts: beside this file, or under res/ in the installed build
WEB = (ROOT / "web") if FROZEN else Path(__file__).resolve().parent / "web"
ASSETS = ROOT / "assets"
WORK = DATA_HOME / "cards"              # the pictures' scratch: pages, heroes
RARITY = {"common": "#C8C4BA", "uncommon": "#7BD88F", "rare": "#5B8DEF",
          "epic": "#B57BEF", "legendary": "#F29A4A"}


def edge():
    """msedge.exe, where Windows puts it."""
    for base in (os.environ.get("PROGRAMFILES(X86)"),
                 os.environ.get("PROGRAMFILES"),
                 os.environ.get("LOCALAPPDATA")):
        if base:
            p = Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            if p.is_file():
                return p
    return None


def _edge(page, w, h, *extra, budget=6000):
    exe = edge()
    if not exe:
        raise RuntimeError(tr("Microsoft Edge est introuvable."))
    args = [str(exe), "--headless=new", "--disable-gpu", "--hide-scrollbars",
            "--no-first-run", "--disable-extensions",
            f"--user-data-dir={WORK / 'profile'}",
            "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
            f"--window-size={w},{h}", f"--virtual-time-budget={budget}",
            *extra, page.as_uri()]
    return subprocess.run(args, capture_output=True, timeout=90,
                          creationflags=getattr(subprocess,
                                                "CREATE_NO_WINDOW", 0))


def _shoot(page, png, w, h, transparent=False):
    """Edge headless photographs the page (w × h) into png."""
    png.unlink(missing_ok=True)
    _edge(page, w, h, f"--screenshot={png}",
          *(["--default-background-color=00000000"] if transparent else []))
    if not png.is_file():
        raise RuntimeError(tr("Edge n'a pas produit l'image."))


def _height(page, w):
    """The page's height once laid out (Edge writes it into the DOM)."""
    r = _edge(page, w, 800, "--dump-dom")
    m = re.search(rb'data-h="([0-9]+)"', r.stdout or b"")
    return int(m.group(1)) if m else 2400


def _uri(path):
    p = Path(path)
    mime = {".png": "image/png", ".webp": "image/webp"}.get(p.suffix)
    try:
        return f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode()
    except OSError:
        return ""


def _img(folder, name):
    for base in (ANALYSIS, SHIPPED_ANALYSIS):
        for ext in (".webp", ".png"):
            p = base / folder / f"{name}{ext}"
            if p.is_file():
                return _uri(p)
    return ""


def hero_png(model_id):
    """The hero wearing the build, standing, on a transparent background
    (cached by the pieces worn). None when there is no model."""
    from gamedata import item_model_json
    js = item_model_json(model_id)
    if not js:
        return None
    WORK.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(model_id.encode()).hexdigest()[:16]
    png = WORK / f"hero_{key}.png"
    if png.is_file():
        return png
    m3d = (WEB / "js" / "model3d.js").read_text(encoding="utf-8")
    page = WORK / f"hero_{key}.html"
    page.write_text(
        "<!doctype html><html><head><meta charset=utf-8><style>"
        "html,body{margin:0;background:transparent}"
        ".m3d{width:520px;height:720px;display:block}</style></head><body>"
        "<script>function notify(){}</script>"
        f"<script>{m3d}</script><script>const ID={json.dumps(model_id)};"
        f"addModel(ID,{json.dumps(js)});document.body.appendChild("
        "m3dCanvas(ID,()=>{},{pitch:0.08,dist:0.86,lift:0,spin:false,"
        "yaw:1.75}));</script></body></html>", encoding="utf-8")
    try:
        _shoot(page, png, 520, 720, transparent=True)
    finally:
        page.unlink(missing_ok=True)
    return png


def e(s):
    return html.escape(str(s if s is not None else ""))


# ---- the card ------------------------------------------------------------
ATB_COLOR = {"Vitality": "#F26D85", "Strength": "#F29A4A", "Dexterity": "#7BD88F",
             "Faith": "#F2C94C", "Intellect": "#9D8CF7"}
ATB_ICON = {"Vitality": "stat_Vitality", "Strength": "stat_Strength",
            "Dexterity": "stat_Dexterity", "Faith": "stat_Faith",
            "Intellect": "stat_Intelligence"}
# the stats a build is built around: the three highest lead
KEY_STATS = ("Fervor", "SpellPenetration", "ArmorPenetration", "CritChance")
MINOR_STATS = ("MaxHealth", "Armor", "CritDamage", "Fervor", "SpellPenetration",
               "ArmorPenetration", "CritChance", "DodgeChance", "HealthRegen",
               "MagicMastery", "PhysicalMastery")


def _fx(text):
    """'Perforation d'armure −40 · Perforation magique +40', each bonus in
    green, each malus in red."""
    out = []
    for part in str(text or "").split(" · "):
        k = "neg" if "−" in part or re.search(r"\s-\d", part) else \
            "pos" if "+" in part else ""
        out.append(f'<span class="{k}">{e(part)}</span>')
    return '<span class="dot">·</span>'.join(out)


def _extras(g):
    """A line per add-on of a piece (sigil, gem, augment...), then its
    infusion with the bonus stat it rolled."""
    icons = {c.get("name"): c.get("img") for c in g.get("chips") or ()
             if c.get("img")}
    rows = []
    for x in g.get("extras") or ():
        img = icons.get(x.get("name"))
        fx = (f'<span class="off">{e(x.get("fx"))}</span>' if x.get("off")
              else _fx(x.get("fx")))
        rows.append(f'<div class="ex">{f"<img src={img!r}>" if img else "<i></i>"}'
                    f'<b>{e(x.get("name"))}</b>{fx}</div>')
    inf = g.get("inf") or {}
    if inf:
        icon = _img("skill_img", inf.get("id"))
        bonus = ""
        if inf.get("bonus"):
            v = f'+{inf["val"]} ' if inf.get("val") else ""
            bonus = (f'<span class="pos">{e(v)}{e(inf["bonus"])}</span>' if inf.get("on")
                     else f'<span class="off">{e(v)}{e(inf["bonus"])} '
                          + e(tr("(inactif, pièce non prismatique d’une autre "
                                 "faction)")) + '</span>')
        rows.append(f'<div class="ex inf">{f"<img src={icon!r}>" if icon else "<i></i>"}'
                    f'<b>{e(inf.get("name"))}</b>{bonus}</div>')
    elif g.get("plan"):
        rows.append('<div class="ex inf"><i></i><span class="off">'
                    + e(tr("Bonus visé : {plan}", plan=g["plan"]))
                    + '</span></div>')
    return "".join(rows)


def _piece(g, slot_label, weapon=False):
    if not g:
        return (f'<div class="pc empty"><span class="pic"></span>'
                f'<div class="pt"><b>{e(slot_label)}</b><span>{e(tr("Vide"))}</span></div></div>')
    rk = g.get("rk") or "common"
    if weapon:
        sub = e(slot_label)
    else:
        sub = e(" · ".join(x for x in (slot_label, g.get("rar")) if x))
    prism = (f'<em class="prism">✦ {e(tr("Prismatique"))}</em>'
             if g.get("prism") else "")
    return (f'<div class="pc r-{rk}{" wp" if weapon else ""}">'
            f'<span class="pic"><img src="{g.get("img") or ""}"></span>'
            f'<div class="pt"><b style="color:{RARITY.get(rk, "#fff")}">{e(g.get("name"))}</b>'
            f'<span class="ps">{sub}{prism}</span>{_extras(g)}</div></div>')


def _bar(o):
    """The spell bar as the game shows it: the weapons' four, then the
    class's four."""
    cells = []
    for b in o.get("bar") or ():
        if b.get("group") == "class" and b.get("index") == 0:
            cells.append('<span class="bsep"></span>')
        icon = _img("skill_img", b.get("id")) if b.get("id") else ""
        cells.append(f'<div class="bc"><span class="bic">'
                     f'{f"<img src={icon!r}>" if icon else ""}'
                     f'<i>{e(b.get("key"))}</i></span>'
                     f'<b>{e(b.get("name") or "—")}</b></div>')
    return "".join(cells)


def card_html(o, hero_uri=""):
    sheet = o.get("sheet") or {}
    atbs = o.get("atbs") or {}
    tiles = ""
    for a in atbs.get("primary") or ():
        icon = _uri(ASSETS / "charsheet" / (ATB_ICON.get(a["k"], "") + ".png"))
        tiles += (f'<div class="at" style="--c:{ATB_COLOR.get(a["k"], "#fff")}">'
                  f'<i class="aico" style="-webkit-mask-image:url({icon});mask-image:url({icon})"></i>'
                  f'<b>{e(a["v"])}</b><span>{e(a["t"])}</span></div>')
    sec = {x["k"]: x for x in atbs.get("secondary") or ()}
    raw = atbs.get("raw") or {}
    lead = sorted((k for k in KEY_STATS if k in sec),
                  key=lambda k: -(raw.get(k) or 0))[:3]
    keys = "".join(f'<div class="ks"><span>{e(sec[k]["t"])}</span><b>{e(sec[k]["v"])}</b></div>'
                   for k in lead)
    minor = "".join(
        f'<span class="mn">{e(sec[k]["t"])} <b>{e(sec[k]["v"])}</b>'
        + (f' <small>{e(sec[k]["sub"])}</small>' if sec[k].get("sub") else "")
        + "</span>"
        for k in MINOR_STATS if k in sec and k not in lead
        and not (k.endswith("Mastery") and sec[k]["v"].startswith("0")))
    weapons = "".join(_piece(w.get("g"), w.get("label"), True)
                      for w in sheet.get("weapons") or ())
    ars = sheet.get("arsenal") or {}
    if ars:
        weapons += _piece(ars.get("g"), tr("Arsenal"), True)
    left = "".join(_piece(x.get("g"), x.get("label")) for x in sheet.get("left") or ())
    right = "".join(_piece(x.get("g"), x.get("label")) for x in sheet.get("right") or ())
    infs = ""
    for s in o.get("infusions") or ():
        tiers = "".join(f'<div class="tier{" on" if t["on"] else ""}"><i>{t["n"]}</i>'
                        f'<span>{e(t["txt"])}</span></div>' for t in s.get("tiers") or ())
        infs += (f'<div class="set"><div class="seth"><b>{e(s["name"])}</b>'
                 f'<span>{e(s.get("fac"))} · {e(s.get("role"))}</span>'
                 f'<em>{e(tr("{n} pièces", n=s["n"]) if s["n"] > 1 else tr("{n} pièce", n=s["n"]))}</em>'
                 f'</div>{tiers}</div>')
    pts = o.get("points") or {}
    talents = []
    for tier in (o.get("tree") or {}).get("tiers") or ():
        for row in tier:
            for t in row if isinstance(row, list) else [row]:
                if isinstance(t, dict) and t.get("pts"):
                    talents.append(f'{e(t.get("name"))}'
                                   + (f' <small>{t["pts"]}/{t["max"]}</small>'
                                      if (t.get("max") or 1) > 1 else ""))
    runes = [f'{e(r["name"])} <small>({e(s["name"])})</small>'
             for s in (o.get("sim") or {}).get("runes") or ()
             for r in s.get("runes") or () if r.get("on")]
    tal_txt = ("".join(f'<span class="tg">{t}</span>' for t in talents)
               or f'<span class="none">{e(tr("Aucun talent choisi"))}</span>')
    rune_txt = ("".join(f'<span class="tg">{r}</span>' for r in runes)
                or f'<span class="none">{e(tr("Aucune rune choisie"))}</span>')
    bg = _img("dungeon_bg", "loading_screen4_hd")
    cls_icon = _uri(ASSETS / "classes" / f"{o.get('ck')}.png")
    hero = (f'<img class="hero3d" src="{hero_uri}">' if hero_uri
            else f'<img class="heroico" src="{cls_icon}">')
    return f'''<!doctype html><html lang="fr"><head><meta charset="utf-8"><style>
:root {{ --bg:#211F3A; --bg2:#1A1930; --panel:#2B2949; --panel2:#36335C; --line:#47447A; --line2:#5B5893;
  --text:#EEEBFF; --dim:#ADA9D6; --faint:#7F7BAA; --gold:#F2C94C; --green:#A6D23B; --hot:#F29A4A; --dmg:#6C9CF5; --heal:#57C08A;
  --fh:"Bahnschrift","Segoe UI Variable Display","Segoe UI",sans-serif; }}
* {{ box-sizing:border-box; }}
html,body {{ margin:0; background:var(--bg); }}
body {{ width:{WIDTH}px; font:14px/1.4 "Segoe UI",sans-serif; color:var(--text); }}
.top {{ position:relative; min-height:470px; overflow:hidden; background:url({bg}) center 60%/cover; }}
.top::after {{ content:""; position:absolute; inset:0;
  background:linear-gradient(90deg,rgba(26,25,48,.97) 0%,rgba(26,25,48,.85) 48%,rgba(26,25,48,.2) 100%),
             linear-gradient(0deg,var(--bg) 0%,rgba(33,31,58,0) 40%); }}
.hero3d {{ position:absolute; right:150px; bottom:-30px; height:520px; z-index:1;
  filter:drop-shadow(0 20px 30px rgba(0,0,0,.55)); }}
.heroico {{ position:absolute; right:120px; top:110px; width:240px; z-index:1; opacity:.9; }}
.brand {{ position:absolute; top:22px; right:30px; z-index:2; font-family:var(--fh); font-weight:700;
  letter-spacing:.08em; font-size:16px; }}
.brand span {{ color:var(--gold); }}
.tin {{ position:relative; z-index:2; padding:64px 0 34px 48px; width:700px; }}
.kick {{ display:flex; align-items:center; gap:10px; font-family:var(--fh); letter-spacing:.14em;
  text-transform:uppercase; font-size:14px; color:var(--gold); font-weight:700; }}
.kick img {{ width:34px; height:34px; }}
h1 {{ margin:10px 0 6px; font-family:var(--fh); font-size:58px; line-height:1.02; letter-spacing:.01em; }}
.sub {{ color:var(--dim); font-size:16px; }}
.sub b {{ color:var(--text); }}
.ats {{ display:flex; gap:10px; margin-top:26px; }}
.at {{ width:118px; display:flex; flex-direction:column; align-items:center; gap:2px; padding:10px 6px 8px;
  background:rgba(26,25,48,.78); border:1px solid color-mix(in srgb,var(--c) 45%,var(--line)); border-radius:12px;
  box-shadow:inset 0 2px 0 color-mix(in srgb,var(--c) 70%,transparent); }}
.aico {{ width:34px; height:34px; background:var(--c); -webkit-mask-size:contain; mask-size:contain;
  -webkit-mask-repeat:no-repeat; mask-repeat:no-repeat; -webkit-mask-position:center; mask-position:center;
  filter:drop-shadow(0 0 6px color-mix(in srgb,var(--c) 60%,transparent)); }}
.kss {{ display:grid; grid-template-columns:repeat(3,1fr); gap:10px; margin-top:12px; width:630px; }}
.ks {{ background:rgba(26,25,48,.78); border:1px solid var(--line); border-radius:12px; padding:8px 14px;
  display:flex; flex-direction:column; }}
.ks span {{ font-family:var(--fh); letter-spacing:.08em; text-transform:uppercase; font-size:11px; color:var(--dim); font-weight:700; }}
.ks b {{ font-family:var(--fh); font-size:28px; line-height:1.15; }}
.mns {{ display:flex; flex-wrap:wrap; gap:4px 16px; margin-top:10px; width:640px; font-size:12px; color:var(--dim); }}
.mn b {{ color:var(--text); font-family:var(--fh); font-size:13px; }}
.mn small {{ color:var(--faint); }}
.at b {{ font-family:var(--fh); font-size:26px; line-height:1.1; }}
.at span {{ color:var(--dim); font-size:12px; }}
.body {{ padding:4px 48px 28px; }}
.sec {{ display:flex; align-items:center; gap:10px; margin:22px 0 12px; font-family:var(--fh); font-size:19px; font-weight:700; }}
.sec::before {{ content:""; width:12px; height:12px; transform:rotate(45deg); background:var(--green); border-radius:2px;
  box-shadow:0 0 10px rgba(166,210,59,.5); }}
.sec::after {{ content:""; flex:1; height:1px; background:linear-gradient(90deg,var(--line),transparent); }}
.sts {{ display:grid; grid-template-columns:repeat(5,1fr); gap:10px; }}
.ss {{ background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:10px 14px; display:flex; flex-direction:column; }}
.ss span {{ font-family:var(--fh); letter-spacing:.08em; text-transform:uppercase; font-size:11px; color:var(--dim); font-weight:700; }}
.ss b {{ font-family:var(--fh); font-size:24px; }}
.ss small {{ color:var(--faint); font-size:12px; }}
.wps {{ display:grid; grid-template-columns:repeat(3,1fr); gap:10px; margin-bottom:10px; }}
.gear {{ display:grid; grid-template-columns:1fr 1fr; gap:10px; }}
.gcol {{ display:flex; flex-direction:column; gap:8px; }}
.pc {{ display:flex; align-items:flex-start; gap:12px; background:var(--panel); border:1px solid var(--line);
  border-radius:12px; padding:8px 12px 8px 8px; min-height:66px; }}
.pc .pic {{ width:50px; height:50px; flex:none; border-radius:10px; background:var(--bg2); border:2px solid var(--line2);
  display:grid; place-items:center; overflow:hidden; }}
.pc .pic img {{ width:44px; height:44px; }}
.pc.r-uncommon .pic {{ border-color:#3E7A4C; }} .pc.r-rare .pic {{ border-color:#34508A; }}
.pc.r-epic .pic {{ border-color:#6A4692; }} .pc.r-legendary .pic {{ border-color:#8F5A2C; box-shadow:0 0 12px rgba(242,154,74,.25); }}
.pc .pt {{ flex:1; min-width:0; display:flex; flex-direction:column; }}
.pc .pt b {{ font-family:var(--fh); font-size:15px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
.pc .pt > .ps {{ color:var(--dim); font-size:12px; }}
.pc.wp .pt b {{ font-size:16px; }}
.ex {{ display:flex; align-items:center; flex-wrap:wrap; gap:2px 6px; font-size:12px; color:var(--dim); margin-top:3px; }}
.ex img {{ width:20px; height:20px; border-radius:5px; background:var(--bg2); }}
.ex > i {{ width:6px; height:6px; margin:0 7px; transform:rotate(45deg); background:var(--line2); }}
.ex b {{ color:var(--text); font-weight:600; }}
.ex .dot {{ color:var(--faint); }}
.ex.inf b {{ color:var(--gold); }}
.pos {{ color:#8BD86A; }} .neg {{ color:#F2665E; }} .off {{ color:var(--faint); }}
.prism {{ font-style:normal; margin-left:8px; padding:0 7px; border-radius:6px; font-size:11px; font-weight:600;
  color:#8FE3F0; border:1px solid #4FB6C8; background:rgba(79,182,200,.12); text-shadow:0 0 4px rgba(42,184,224,.6); }}
.inf {{ font-size:12px; color:var(--text); margin-top:1px; display:flex; align-items:center; gap:6px; }}
.inf i {{ width:7px; height:7px; transform:rotate(45deg); background:var(--green); flex:none; }}
.inf b {{ color:var(--gold); font-weight:600; }}
.inf.off {{ color:var(--faint); }}
.chips {{ display:flex; gap:4px; flex:none; }}
.chips img {{ width:28px; height:28px; border-radius:7px; background:var(--bg2); border:1px solid var(--line); padding:2px; }}
.pc.empty {{ opacity:.5; }}
.pc.empty .pt span, .pc.empty div span {{ color:var(--faint); font-size:12px; display:block; }}
.bar {{ display:flex; align-items:flex-start; justify-content:center; gap:12px; background:var(--panel);
  border:1px solid var(--line); border-radius:14px; padding:16px 14px 12px; }}
.bc {{ width:118px; display:flex; flex-direction:column; align-items:center; gap:6px; text-align:center; }}
.bic {{ position:relative; width:72px; height:72px; border-radius:12px; overflow:hidden; border:2px solid var(--line2);
  background:var(--bg2); box-shadow:0 6px 14px rgba(0,0,0,.35); }}
.bic img {{ width:100%; height:100%; object-fit:cover; }}
.bic i {{ position:absolute; left:3px; top:3px; min-width:20px; height:20px; padding:0 4px; border-radius:5px; font-style:normal;
  background:rgba(26,25,48,.88); border:1px solid var(--line2); font-family:var(--fh); font-weight:700; font-size:12px;
  color:var(--gold); display:grid; place-items:center; }}
.bc b {{ font-family:var(--fh); font-size:13px; font-weight:600; line-height:1.2; }}
.bsep {{ width:2px; align-self:stretch; margin:4px 6px; background:linear-gradient(180deg,transparent,var(--gold),transparent); opacity:.6; }}
.sp {{ display:flex; align-items:center; gap:12px; background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:8px 12px 8px 8px; }}
.sp .key {{ width:24px; height:24px; flex:none; display:grid; place-items:center; border-radius:6px; background:var(--bg2);
  border:1px solid var(--line); font-family:var(--fh); font-weight:700; font-size:13px; color:var(--gold); }}
.sp .sic {{ width:50px; height:50px; flex:none; border-radius:10px; overflow:hidden; border:2px solid var(--line2); background:var(--bg2); }}
.sp .sic img {{ width:100%; height:100%; object-fit:cover; }}
.sp .st2 {{ flex:1; min-width:0; display:flex; flex-direction:column; }}
.sp .st2 b {{ font-family:var(--fh); font-size:15px; }}
.sp .st2 span {{ color:var(--faint); font-size:12px; }}
.svs {{ display:flex; gap:8px; flex:none; }}
.sv {{ display:flex; flex-direction:column; align-items:flex-end; min-width:74px; }}
.sv b {{ font-family:var(--fh); font-size:22px; line-height:1.05; }}
.sv.dmg b {{ color:#fff; }} .sv.heal b {{ color:var(--heal); }}
.sv span {{ font-size:11px; color:var(--dim); }}
.sv small {{ font-size:10px; color:var(--faint); white-space:nowrap; }}
.pass {{ display:flex; flex-wrap:wrap; gap:8px; margin-top:10px; }}
.pas {{ display:flex; align-items:center; gap:8px; padding:4px 12px 4px 4px; border-radius:20px; background:var(--panel);
  border:1px solid var(--line); font-size:13px; }}
.pas img {{ width:26px; height:26px; border-radius:50%; }}
.row {{ display:grid; grid-template-columns:1.25fr 1fr; gap:14px; }}
.set {{ background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:10px 14px; margin-bottom:8px; }}
.seth {{ display:flex; align-items:baseline; gap:10px; margin-bottom:6px; }}
.seth b {{ font-family:var(--fh); font-size:16px; }}
.seth span {{ color:var(--dim); font-size:12px; flex:1; }}
.seth em {{ font-style:normal; font-family:var(--fh); color:var(--gold); font-weight:700; }}
.tier {{ display:flex; gap:8px; align-items:flex-start; font-size:12px; color:var(--faint); padding:2px 0; }}
.tier i {{ font-style:normal; flex:none; width:20px; height:20px; border-radius:50%; display:grid; place-items:center;
  border:1px solid var(--line); font-family:var(--fh); font-weight:700; font-size:11px; }}
.tier.on {{ color:var(--text); }}
.tier.on i {{ background:var(--gold); border-color:var(--gold); color:#2A2410; }}
.box {{ background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:12px 14px; margin-bottom:8px; }}
.box h4 {{ margin:0 0 8px; font-family:var(--fh); font-size:13px; letter-spacing:.08em; text-transform:uppercase; color:var(--dim); }}
.box h4 em {{ font-style:normal; color:var(--gold); margin-left:6px; }}
.tgs {{ display:flex; flex-wrap:wrap; gap:6px; }}
.tg {{ font-size:12px; background:var(--bg2); border:1px solid var(--line); border-radius:6px; padding:2px 8px; }}
.tg small {{ color:var(--gold); }}
.none {{ color:var(--faint); font-size:13px; }}
.foot {{ margin-top:20px; display:flex; justify-content:space-between; color:var(--faint); font-size:12px;
  border-top:1px solid var(--line); padding-top:12px; }}
</style></head><body>
<div class="top">{hero}<div class="brand">Farever <span>Book</span></div>
 <div class="tin"><div class="kick"><img src="{cls_icon}">{tr("{cls} · niveau {lvl}", cls=e(o.get("clsFr")), lvl=e(o.get("lvl")))}</div>
  <h1>{e(o.get("name"))}</h1>
  <div class="ats">{tiles}</div>
  <div class="kss">{keys}</div>
  <div class="mns">{minor}</div></div></div>
<div class="body">
 <div class="sec">{e(tr("Équipement"))}</div>
 <div class="wps">{weapons}</div>
 <div class="gear"><div class="gcol">{left}</div><div class="gcol">{right}</div></div>
 <div class="sec">{e(tr("Sorts"))}</div>
 <div class="bar">{_bar(o)}</div>
 <div class="row">
  <div><div class="sec">{e(tr("Imprégnations"))}</div>{infs or '<div class="box"><span class="none">' + e(tr("Aucune imprégnation")) + '</span></div>'}</div>
  <div><div class="sec">{e(tr("Talents et runes"))}</div>
   <div class="box"><h4>{e(tr("Talents"))}<em>{pts.get("used", 0)} / {pts.get("total", 0)}</em></h4><div class="tgs">{tal_txt}</div></div>
   <div class="box"><h4>{e(tr("Runes"))}</h4><div class="tgs">{rune_txt}</div></div></div>
 </div>
 <div class="foot"><span>{e(tr("Fiche de build générée par Farever Book"))}</span></div>
</div><script>document.body.dataset.h=Math.ceil(document.body.getBoundingClientRect().height)</script>
</body></html>'''


def pictures_dir():
    """Windows' Pictures folder (wherever the user moved it), then ours in
    it: Images\\Farever Book."""
    base = None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion"
                            r"\Explorer\User Shell Folders") as k:
            base = Path(os.path.expandvars(
                winreg.QueryValueEx(k, "My Pictures")[0]))
    except OSError:
        pass
    if not base or not base.is_dir():
        base = Path.home() / "Pictures"
    return base / "Farever Book"


def render(o, out_png):
    """The card of the open build view `o` into out_png (a Path)."""
    WORK.mkdir(parents=True, exist_ok=True)
    hero = ""
    if o.get("model"):
        try:
            p = hero_png(o["model"])
            hero = _uri(p) if p else ""
        except (OSError, RuntimeError, subprocess.SubprocessError):
            hero = ""
    fd, name = tempfile.mkstemp(suffix=".html", dir=WORK)
    os.close(fd)
    page = Path(name)
    try:
        page.write_text(card_html(o, hero), encoding="utf-8")
        # measured first, then shot at that height: no empty bottom
        _shoot(page, Path(out_png), WIDTH, _height(page, WIDTH))
    finally:
        page.unlink(missing_ok=True)
    return out_png

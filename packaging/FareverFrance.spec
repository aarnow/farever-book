# PyInstaller spec for Farever France.
#
#   py -m PyInstaller --clean --noconfirm packaging/FareverFrance.spec
#
# Produces dist/FareverFrance/, a windowed build carrying its own Python, frida
# and Pillow. Run from the project root.
import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent

# What the app reads at runtime, under res/ (see ROOT in farever_france.py) so
# the "frida" folder of agent JS can't be confused with the frida package.
datas = [
    (str(ROOT / "frida" / "meter_hook.js"), "res/frida"),
    (str(ROOT / "analysis_out" / "resolver_data.json"), "res/analysis_out"),
    (str(ROOT / "analysis_out" / "meter_offsets.json"), "res/analysis_out"),
    (str(ROOT / "analysis_out" / "unit_names.json"), "res/analysis_out"),
    # sizes heals that restored nothing (overheal)
    (str(ROOT / "analysis_out" / "heal_specs.json"), "res/analysis_out"),
    (str(ROOT / "assets" / "fareverfrance.ico"), "res/assets"),
    # the header's wordmark before the game's data is read (welcome screen)
    (str(ROOT / "assets" / "ui_logo.png"), "res/assets"),
    # the class icons, cut from the game's art
    *[(str(f), "res/assets/classes")
      for f in sorted((ROOT / "assets" / "classes").glob("*.png"))],
    *[(str(f), "res/assets/charsheet")
      for f in sorted((ROOT / "assets" / "charsheet").glob("*.*"))],
    # the window's markup, inlined by menu_host.py (required: blank without it)
    *[(str(f), "res/web")
      for f in sorted((ROOT / "meter" / "web").glob("*.*"))],
    *[(str(f), "res/web/js")
      for f in sorted((ROOT / "meter" / "web" / "js").glob("*.js"))],
]

# the bundled packages' licences (packaging/third_party.py)
sys.path.insert(0, str(ROOT / "packaging"))
import third_party  # noqa: E402
(ROOT / "build").mkdir(exist_ok=True)
_licences = third_party.write(ROOT / "build" / "THIRD_PARTY_LICENSES.txt")
datas.append((str(_licences), "."))

# the Help tab's articles
_help = sorted((ROOT / "meter" / "web" / "help").glob("*.md"))
if not _help:
    raise SystemExit("[!] meter/web/help holds no articles — the Help tab "
                     "would ship empty")
for f in _help:
    datas.append((str(f), "res/web/help"))

# The generators ship so the installed app can rebuild its data after a game
# patch. All of them: emit_offsets skips a table whose generator is missing.
_tools = sorted((ROOT / "hltools").glob("*.py"))
for tool in _tools:
    datas.append((str(tool), "res/hltools"))

# Pillow is imported inside functions: named so it surely ships.
hiddenimports = ["PIL.Image", "PIL.ImageDraw", "PIL.ImageFont",
                 # the window's process: the exe re-enters itself with
                 # --menu-host, nothing in the import graph points at it
                 "menu_host",
                 # pywebview imports its backend at runtime; clr is pythonnet's
                 # entry into .NET
                 "webview.platforms.winforms", "clr",
                 # the tools run from source: named so their own imports
                 # (xml, zlib...) ship
                 *[t.stem for t in _tools]]

a = Analysis(
    [str(ROOT / "meter" / "farever_france.py")],
    # meter/ so the "menu_host" hidden import resolves
    pathex=[str(ROOT), str(ROOT / "meter"), str(ROOT / "hltools")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # pulled in by Pillow, unused, tens of megabytes
    excludes=["numpy", "scipy", "matplotlib", "pytest", "setuptools", "pydoc",
              "doctest", "unittest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FareverFrance",
    debug=False,
    strip=False,
    upx=False,          # UPX-packed binaries are a reliable antivirus trigger,
    console=False,      # and this one already injects into a game process
    icon=str(ROOT / "assets" / "fareverfrance.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="FareverFrance",
)

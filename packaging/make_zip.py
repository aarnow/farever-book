"""A zip to hand the app to a friend: unzip, double-click "Farever France".

    py packaging\\make_zip.py            ready to run (Python inside)
    py packaging\\make_zip.py --source   the project as it is here: the
                                         friend installs Python, then runs
                                         "Installer Farever France.cmd"

Writes dist\\Farever-France-<version>.zip holding one folder:

    Farever France\\
        Farever France.exe    the launcher (packaging\\lanceur.cs)
        LISEZ-MOI.txt
        app\\                  meter, frida, hltools, assets, analysis_out
        python\\               this machine's Python, trimmed, with the
                              modules the app needs (frida, pywebview...)

Nothing personal goes in: only the files git tracks, plus analysis_out (the
data extracted from the game) — no builds, rift or dungeon reports, no
.meter_* state, no exports. The friend's own data is written into his app\\
folder as he plays."""
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "meter"))
from common import VERSION      # noqa: E402

NAME = "Farever France"
DIST = ROOT / "dist"
STAGE = DIST / "stage" / NAME
CSC = Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe")

APP_DIRS = ("meter", "frida", "hltools", "assets")
# not needed to run: docs, headers, the test suite, Tk, pip and dev tools
PY_SKIP = {"Doc", "include", "libs", "Scripts", "tcl", "NEWS.txt"}
LIB_SKIP = {"test", "idlelib", "tkinter", "turtledemo", "ensurepip",
            "__pycache__"}
SITE_SKIP = ("pip", "pyflakes", "__pycache__")

README = """Farever France {v}
================

1. Décompresse tout le dossier « Farever France » où tu veux (par exemple
   dans Documents). Ne lance rien depuis l'intérieur du zip.
2. Double-clique sur « Farever France » (l'icône au blason).
3. Si Windows affiche « Windows a protégé votre ordinateur » : clique sur
   « Informations complémentaires » puis « Exécuter quand même ». C'est le
   cas de tout programme qui n'est pas signé.

L'application s'ouvre dans sa propre fenêtre (pratique sur un second écran)
et se connecte à Farever dès que le jeu est lancé. Rien n'est à installer :
Python et ses modules sont dans le dossier « python ».

Si rien ne s'ouvre : le journal est dans %LOCALAPPDATA%\FareverMeter\meter.log
(et « erreur-demarrage.txt » à côté du lanceur si le démarrage a échoué).

Après une mise à jour du jeu, si la connexion échoue : Aide > Réparer.
Tes données (failles, donjons, builds, réglages) sont rangées dans le
dossier « app ». Le journal est dans %LOCALAPPDATA%\\FareverMeter.

Usage personnel : l'application lit les données du jeu, elle ne modifie
rien dans Farever.
"""


def tracked(sub):
    out = subprocess.run(["git", "ls-files", "-z", sub], cwd=ROOT,
                         capture_output=True, check=True).stdout
    return [ROOT / p for p in out.decode("utf-8").split("\0") if p]


def copy_app(dst):
    for sub in APP_DIRS:
        for src in tracked(sub):
            if src.suffix == ".pyc":
                continue
            to = dst / src.relative_to(ROOT)
            to.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, to)
    # the game's data and pictures, tracked or not (images are generated)
    shutil.copytree(ROOT / "analysis_out", dst / "analysis_out",
                    ignore=shutil.ignore_patterns(
                        "__pycache__", "codex_progress_dump.json"))
    shutil.copy2(ROOT / "README.md", dst / "README.md")


def copy_python(dst):
    base = Path(sys.base_prefix)
    site = base / "Lib" / "site-packages"
    for item in base.iterdir():
        if item.name in PY_SKIP:
            continue
        if item.is_file():
            shutil.copy2(item, dst / item.name)
        elif item.name != "Lib":
            shutil.copytree(item, dst / item.name)
    lib = dst / "Lib"
    lib.mkdir()
    for item in (base / "Lib").iterdir():
        if item.name in LIB_SKIP or item.name == "site-packages":
            continue
        if item.is_dir():
            shutil.copytree(item, lib / item.name,
                            ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(item, lib / item.name)
    (lib / "site-packages").mkdir()
    for item in site.iterdir():
        if item.name.startswith(SITE_SKIP):
            continue
        if item.is_dir():
            shutil.copytree(item, lib / "site-packages" / item.name,
                            ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(item, lib / "site-packages" / item.name)


def build_launcher(dst):
    src = ROOT / "packaging" / "lanceur.cs"
    icon = ROOT / "assets" / "farevermeter.ico"
    r = subprocess.run([str(CSC), "/nologo", "/target:winexe",
                        f"/win32icon:{icon}", f"/out:{dst}", str(src)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("La compilation du lanceur a échoué :\n" + r.stdout
                 + r.stderr)


SOURCE_README = """Farever France {v} — le projet
==============================

1. Installe Python 3.13 (64 bits) depuis https://www.python.org/downloads/
   (laisse cochée l'option « py launcher »).
2. Décompresse tout le dossier « Farever France » où tu veux.
3. Double-clique sur « Installer Farever France.cmd » : il installe les
   modules (frida 17.18.0, pillow, pywebview) et crée le raccourci
   « Farever France » sur le Bureau et dans le menu Démarrer.
4. Lance avec le raccourci, ou dans un terminal ouvert dans le dossier :
       py meter\\farever_meter.py

Si Windows affiche un avertissement sur le .cmd (fichier venu d'Internet) :
« Informations complémentaires » puis « Exécuter quand même ».
Après une mise à jour du jeu, si la connexion échoue : Aide > Réparer.
Le journal est dans %LOCALAPPDATA%\\FareverMeter\\meter.log.
"""


def source_zip():
    """The project as git has it — this folder minus everything personal —
    plus the game pictures git ignores (they would otherwise only come back
    when the game data is regenerated)."""
    out = DIST / f"Farever-France-{VERSION}-projet.zip"
    files = tracked(".")
    for sub in ("collection_img", "bestiary_img", "map_tiles", "skill_img",
                "item_icons", "boss_portraits"):
        files += sorted(p for p in (ROOT / "analysis_out" / sub).rglob("*")
                        if p.is_file())
    DIST.mkdir(exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED,
                         compresslevel=9) as z:
        for f in files:
            if f.is_file() and f.suffix != ".pyc":
                z.write(f, f"{NAME}/{f.relative_to(ROOT).as_posix()}")
        z.writestr(f"{NAME}/LISEZ-MOI.txt", SOURCE_README.format(v=VERSION))
    print(f"{out}  ({out.stat().st_size / 1e6:.0f} Mo)")


def main():
    if "--source" in sys.argv:
        source_zip()
        return
    if not CSC.exists():
        sys.exit(f"Compilateur C# introuvable : {CSC}")
    shutil.rmtree(DIST / "stage", ignore_errors=True)
    STAGE.mkdir(parents=True)
    print("== application")
    copy_app(STAGE / "app")
    print("== Python")
    (STAGE / "python").mkdir()
    copy_python(STAGE / "python")
    print("== lanceur")
    build_launcher(STAGE / f"{NAME}.exe")
    (STAGE / "LISEZ-MOI.txt").write_text(README.format(v=VERSION),
                                         encoding="utf-8")
    out = DIST / f"Farever-France-{VERSION}.zip"
    print(f"== {out.name}")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED,
                         compresslevel=9) as z:
        for f in sorted(STAGE.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(STAGE.parent))
    shutil.rmtree(DIST / "stage", ignore_errors=True)
    print(f"{out}  ({out.stat().st_size / 1e6:.0f} Mo)")


if __name__ == "__main__":
    main()

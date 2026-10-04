"""The third-party components Farever France ships, and their licences: one
text file, THIRD_PARTY_LICENSES.txt, written at build time by
farevermeter.spec and installed with the app (Réglages › Configuration ›
Licences des composants).

Each licence's text is read from the package itself (its dist-info), so a
new version of a component brings its own text. Python's licence (the
Windows build's LICENSE.txt) also covers what Python bundles: OpenSSL,
libffi, bzip2, xz, SQLite and the Microsoft C runtime.

    py packaging\\third_party.py [out.txt]      to look at it"""
import importlib.metadata as md
import sys
from pathlib import Path

# (distribution, what it does here), in the order the file lists them
PACKAGES = (
    ("frida", "lecture des données de combat du jeu"),
    ("pywebview", "la fenêtre de l'application"),
    ("pythonnet", "le pont vers .NET de la fenêtre"),
    ("clr_loader", "le chargement de .NET"),
    ("pillow", "les images (rapports, fiches, icônes)"),
    ("cffi", "dépendance de pythonnet"),
    ("pycparser", "dépendance de cffi"),
    ("bottle", "dépendance de pywebview"),
    ("proxy_tools", "dépendance de pywebview"),
    ("typing_extensions", "dépendance de pywebview"),
)
# what comes without a licence file of its own: (name, licence, where)
OTHERS = (
    ("Microsoft Edge WebView2 SDK (Microsoft.Web.WebView2.*.dll, "
     "WebView2Loader.dll)", "© Microsoft Corporation, licence du SDK WebView2",
     "https://www.nuget.org/packages/Microsoft.Web.WebView2"),
)
RULE = "=" * 78


def _licence_texts(dist):
    out = []
    for f in dist.files or ():
        name = Path(str(f)).name.upper()
        if any(k in name for k in ("LICEN", "COPYING", "NOTICE")):
            try:
                out.append((Path(str(f)).name, f.read_text(encoding="utf-8")))
            except (OSError, UnicodeDecodeError):
                continue
    return out


def text():
    parts = [
        "Farever France : licences des composants tiers",
        "",
        "Farever France est gratuit. Son propre code n'est pas distribué et",
        "reste la propriété de son auteur, tous droits réservés. Il s'appuie",
        "sur les composants ci-dessous, chacun sous sa propre licence, dont le",
        "texte suit.",
        "",
        "Farever est un jeu d'Ankama. Farever France n'est ni affilié à Ankama",
        "ni approuvé par Ankama.",
        "",
    ]
    py = Path(sys.base_prefix) / "LICENSE.txt"
    parts += [RULE, f"Python {sys.version.split()[0]} (l'interpréteur de "
                    "l'application), avec OpenSSL, libffi, bzip2, xz, SQLite",
              "et le runtime C de Microsoft qu'il embarque", RULE, ""]
    try:
        parts.append(py.read_text(encoding="utf-8", errors="replace").strip())
    except OSError:
        parts.append("Python Software Foundation License : "
                     "https://docs.python.org/3/license.html")
    parts.append("")
    for name, role in PACKAGES:
        try:
            dist = md.distribution(name)
        except md.PackageNotFoundError:
            continue
        meta = dist.metadata
        lic = (meta.get("License-Expression") or meta.get("License") or "")
        lic = lic.splitlines()[0].strip() if lic else ""
        home = meta.get("Home-page") or next(
            (u.split(",", 1)[-1].strip()
             for u in meta.get_all("Project-URL") or ()), "")
        parts += [RULE, f"{meta.get('Name') or name} {dist.version} : {role}",
                  *([f"Licence : {lic}"] if lic else []),
                  *([home] if home else []), RULE, ""]
        texts = _licence_texts(dist)
        for fname, body in texts:
            if len(texts) > 1:
                parts += [f"--- {fname} ---", ""]
            parts += [body.strip(), ""]
    for name, lic, where in OTHERS:
        parts += [RULE, name, f"Licence : {lic}", where, RULE, ""]
    return "\n".join(parts).rstrip() + "\n"


def write(path):
    Path(path).write_text(text(), encoding="utf-8")
    return Path(path)


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "THIRD_PARTY_LICENSES.txt"
    print(write(out))

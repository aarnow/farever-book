"""The interface's languages: English (the default) and French.

The texts are written in French in the code; tr() gives them in the chosen
language from web/i18n/<lang>.json (French text -> its translation), the
catalogue the page uses too (core.js tr()). A text missing from it shows in
French. Placeholders are {name}, filled from tr()'s keywords."""
import json
import sys

from common import ROOT, FROZEN

LANGS = {"en": "English", "fr": "Français"}
DEFAULT = "en"
SOURCE = "fr"               # the language the texts are written in

I18N_DIR = (ROOT / "web" / "i18n") if FROZEN else (ROOT / "meter" / "web" / "i18n")

_lang = [DEFAULT]
_catalogs = {}


def set_lang(lang):
    if lang in LANGS:
        _lang[0] = lang


def lang():
    return _lang[0]


def catalog(lang_id):
    """French text -> its translation, for one language ({} for French)."""
    if lang_id == SOURCE:
        return {}
    if lang_id not in _catalogs:
        try:
            _catalogs[lang_id] = json.loads(
                (I18N_DIR / f"{lang_id}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"[i18n] no {lang_id} catalogue: {e}", file=sys.stderr)
            _catalogs[lang_id] = {}
    return _catalogs[lang_id]


def tr(text, **kw):
    """`text` (French) in the current language, its {placeholders} filled."""
    out = catalog(_lang[0]).get(text, text) if text else text
    return out.format(**kw) if kw else out

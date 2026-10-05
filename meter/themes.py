"""The window's colour themes. The default is the stylesheet as written
(indigo); another theme recolours its indigo family (backgrounds, panels,
borders, the lavender texts) to its own hue, and leaves alone what is
saturated: the colours that mean something (rarities, classes, damage,
healing, gold). The high-contrast theme also spreads the family's
lightness: backgrounds darker, borders and secondary texts lighter."""
import colorsys
import re

# id -> (name, hue in degrees or None for the stylesheet as is, saturation
# factor, high contrast)
THEMES = {
    "default": ("Indigo", None, 1.0, False),
    "emerald": ("Émeraude", 168, 0.9, False),
    "contrast": ("Contraste élevé", 230, 0.45, True),
}
DEFAULT = "default"

# the stylesheet's own hue (its --bg), which a theme's hue replaces
_BASE_HUE = 245 / 360
_COLOR = re.compile(r"#([0-9a-fA-F]{6})\b|rgba?\(\s*(\d+),\s*(\d+),\s*(\d+)")


def _is_ui(r, g, b):
    """Of the indigo family: its hue, and either muted or pastel (a vivid
    purple is a rarity's or a class's, and stays)."""
    h, light, sat = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    return 0.62 <= h <= 0.78 and sat > 0.08 and (sat <= 0.6 or light >= 0.85)


def _contrast(light):
    """Dark tones darker, the others toward white: what lies on a background
    stands out from it."""
    if light < 0.45:
        return light * 0.5
    return min(1.0, 0.78 + (light - 0.45) * 0.45)


def _recolor(rgb, hue, sat_k, contrast=False):
    h, light, sat = colorsys.rgb_to_hls(*(c / 255 for c in rgb))
    h = (hue / 360 + (h - _BASE_HUE)) % 1.0
    if contrast:
        light = _contrast(light)
    r, g, b = colorsys.hls_to_rgb(h, light, min(1.0, sat * sat_k))
    return round(r * 255), round(g * 255), round(b * 255)


def color(hex_color, theme):
    """One "#RRGGBB" in a theme's colours."""
    _name, hue, k, hc = THEMES.get(theme) or THEMES[DEFAULT]
    rgb = tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    if hue is None or not _is_ui(*rgb):
        return hex_color
    return "#%02X%02X%02X" % _recolor(rgb, hue, k, hc)


def preview(theme):
    """A theme's main colours, for its picture in the settings."""
    base = {"bg": "#211F3A", "panel": "#2B2949", "band1": "#45417A",
            "band2": "#3A3768", "line": "#5A5694", "dim": "#ADA9D6",
            "accent": "#F2C94C"}
    return {k: color(v, theme) for k, v in base.items()}


def themed(css, theme):
    """The stylesheet in a theme's colours."""
    _name, hue, k, hc = THEMES.get(theme) or THEMES[DEFAULT]
    if hue is None:
        return css

    def sub(m):
        if m.group(1):
            rgb = tuple(int(m.group(1)[i:i + 2], 16) for i in (0, 2, 4))
            if not _is_ui(*rgb):
                return m.group(0)
            return "#%02X%02X%02X" % _recolor(rgb, hue, k, hc)
        rgb = tuple(int(x) for x in m.group(2, 3, 4))
        if not _is_ui(*rgb):
            return m.group(0)
        head = m.group(0)[:m.group(0).index("(") + 1]
        return head + "%d, %d, %d" % _recolor(rgb, hue, k, hc)
    return _COLOR.sub(sub, css)

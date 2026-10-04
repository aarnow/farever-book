"""Skip re-encoding the pictures a game patch did not change.

The generators cut ~2,200 pictures out of the game (collection, bestiary,
skills, map) and encoding them as WebP is nearly all of a regenerate's time:
6.5 minutes measured 2026-10-01 (WebP method 6: now 4, 80 times faster
for the same size), which looked like a hang. A patch almost
never touches them, so each folder keeps a hash of every picture's pixels
(.pixels.json) and a picture is only encoded again when its pixels changed
or its file is gone.
"""
from __future__ import annotations

import atexit
import hashlib
import json
from pathlib import Path

INDEX = ".pixels.json"
_folders: dict = {}


def _index(folder: Path) -> dict:
    key = str(folder)
    if key not in _folders:
        try:
            _folders[key] = json.loads((folder / INDEX).read_text("utf-8"))
        except (OSError, ValueError):
            _folders[key] = {}
    return _folders[key]


def save(img, path, **kw):
    """img.save(path, **kw), unless the same pixels are already there."""
    path = Path(path)
    h = hashlib.blake2b(img.tobytes(), digest_size=16)
    h.update(f"{img.mode}{img.size}{sorted(kw.items())}".encode())
    digest = h.hexdigest()
    idx = _index(path.parent)
    if idx.get(path.name) == digest and path.exists():
        return
    img.save(path, **kw)
    idx[path.name] = digest


@atexit.register
def _flush():
    for folder, idx in _folders.items():
        try:
            (Path(folder) / INDEX).write_text(json.dumps(idx), "utf-8")
        except OSError:
            pass

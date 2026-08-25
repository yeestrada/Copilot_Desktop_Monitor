from __future__ import annotations

import tkinter as tk

from PIL import Image, ImageTk

from app_paths import bundle_dir

ICON_DIR = bundle_dir() / "assets" / "icons"
ICON_FILES = {
    "cursor": "cursor.png",
    "github_copilot": "github_copilot.png",
    "openai": "openai.png",
}

_photo_cache: dict[tuple[int, str, int], tk.PhotoImage] = {}


def get_provider_icon(widget: tk.Misc, provider: str, size: int = 22) -> tk.PhotoImage | None:
    root_id = id(widget.winfo_toplevel())
    cache_key = (root_id, provider, size)
    cached = _photo_cache.get(cache_key)
    if cached is not None:
        return cached

    filename = ICON_FILES.get(provider)
    if not filename:
        return None

    path = ICON_DIR / filename
    if not path.is_file():
        return None

    try:
        image = Image.open(path).convert("RGBA")
        image = image.resize((size, size), Image.Resampling.LANCZOS)
        photo = ImageTk.PhotoImage(image, master=widget)
    except OSError:
        return None

    _photo_cache[cache_key] = photo
    return photo

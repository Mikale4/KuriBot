"""Font discovery and the shared font sizes."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import List

from PIL import ImageFont

from kuribot.config import BASE_DIR


FONT_DIR_CANDIDATES = [
    BASE_DIR / "fonts",
]


def _font_candidates(*names: str) -> List[str]:
    candidates: List[str] = []
    seen: set[str] = set()

    def add(candidate: Path | str):
        value = str(candidate)
        if value not in seen:
            seen.add(value)
            candidates.append(value)

    env_font_dir = os.getenv("KURIBOT_FONT_DIR", "").strip()
    if env_font_dir:
        font_dir = Path(env_font_dir).expanduser()
        for name in names:
            add(font_dir / name)

    for font_dir in FONT_DIR_CANDIDATES:
        for name in names:
            add(font_dir / name)

    # Keep rendering deterministic across platforms by preferring packaged fonts,
    # then fall back to common Linux and Windows locations.
    system_candidates = {
        "Arimo-Regular.ttf": [
            "/usr/share/fonts/truetype/croscore/Arimo-Regular.ttf",
        ],
        "Arimo-Bold.ttf": [
            "/usr/share/fonts/truetype/croscore/Arimo-Bold.ttf",
        ],
        "LiberationSans-Regular.ttf": [
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        ],
        "LiberationSans-Bold.ttf": [
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        ],
        "NotoSans-Regular.ttf": [
            "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        ],
        "NotoSans-Bold.ttf": [
            "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        ],
        "DejaVuSans.ttf": [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ],
        "DejaVuSans-Bold.ttf": [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ],
        "arial.ttf": [
            "C:/Windows/Fonts/arial.ttf",
        ],
        "arialbd.ttf": [
            "C:/Windows/Fonts/arialbd.ttf",
        ],
    }

    for name in names:
        for candidate in system_candidates.get(name, []):
            add(candidate)

    return candidates


@lru_cache(maxsize=512)
def _load_font(size: int, *names: str) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    # Cached: renderers ask for fonts by size on every frame (100+ lookups per battle
    # image), and each uncached lookup re-checked the disk and re-parsed the font file.
    for font_path in _font_candidates(*names):
        try:
            if Path(font_path).exists():
                return ImageFont.truetype(font_path, size=size)
        except Exception:
            continue
    return ImageFont.load_default()


def get_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return _load_font(size, "Arimo-Regular.ttf", "LiberationSans-Regular.ttf", "NotoSans-Regular.ttf", "DejaVuSans.ttf", "arial.ttf")


FONT_XL = get_font(40)
FONT_L = get_font(28)
FONT_M = get_font(22)
FONT_S = get_font(18)
FONT_XS = get_font(14)


def get_bold_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    bold_font = _load_font(size, "Arimo-Bold.ttf", "LiberationSans-Bold.ttf", "NotoSans-Bold.ttf", "DejaVuSans-Bold.ttf", "arialbd.ttf")
    if getattr(bold_font, "path", None):
        return bold_font
    return get_font(size)


FONT_XL_BOLD = get_bold_font(40)
FONT_L_BOLD = get_bold_font(28)
FONT_M_BOLD = get_bold_font(22)
FONT_S_BOLD = get_bold_font(18)
FONT_XS_BOLD = get_bold_font(14)
